
from __future__ import annotations

import csv
import json
import logging
import random
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import requests


# ---------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | NSEPreprocessor | %(message)s",
)

log = logging.getLogger("NSEPreprocessor")


# ---------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[2]

DATA_ROOT = PROJECT_ROOT / "data"

RAW_ROOT = DATA_ROOT / "raw" / "nse"

STOCKS_DIR = RAW_ROOT / "stocks"
PRICES_DIR = RAW_ROOT / "prices"
COMPANY_DIR = RAW_ROOT / "company"

METADATA_DIR = DATA_ROOT / "metadata"

STOCKS_FILE = STOCKS_DIR / "nse_equity_stocks.csv"
STATUS_FILE = METADATA_DIR / "nse_download_status.json"


# ---------------------------------------------------------------------
# NSE configuration
# ---------------------------------------------------------------------

NSE_BASE_URL = "https://www.nseindia.com"

NSE_API_BASE = f"{NSE_BASE_URL}/api"

NSE_QUOTE_URL = f"{NSE_API_BASE}/quote-equity"

NSE_HISTORICAL_URL = f"{NSE_API_BASE}/historical/cm/equity"

# NSE website pages used to establish a valid session.
NSE_HOME = NSE_BASE_URL
NSE_MARKET_DATA_PAGE = f"{NSE_BASE_URL}/market-data/live-equity-market"
NSE_HISTORICAL_PAGE = (
    f"{NSE_BASE_URL}/historical/price-and-volume-data"
)


# ---------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------

def ensure_directories() -> None:
    """Create all required directories."""

    for directory in (
        STOCKS_DIR,
        PRICES_DIR,
        COMPANY_DIR,
        METADATA_DIR,
    ):
        directory.mkdir(parents=True, exist_ok=True)


def safe_symbol(symbol: str) -> str:
    """Make symbol safe for a Windows filename."""

    return "".join(
        c if c.isalnum() or c in ("-", "_") else "_"
        for c in symbol.upper().strip()
    )


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default

    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        log.warning("Unable to read %s: %s", path, exc)
        return default


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    temp_path = path.with_suffix(path.suffix + ".tmp")

    with temp_path.open("w", encoding="utf-8") as f:
        json.dump(
            data,
            f,
            indent=2,
            ensure_ascii=False,
            default=str,
        )

    temp_path.replace(path)


def looks_like_html(response: requests.Response) -> bool:
    """
    NSE sometimes returns an HTML page instead of JSON/CSV.

    Never pass such a response directly into pandas/json.
    """

    content_type = (
        response.headers.get("Content-Type", "")
        .lower()
    )

    first_bytes = response.content[:1000].lower()

    return (
        "text/html" in content_type
        or b"<html" in first_bytes
        or b"<!doctype html" in first_bytes
    )


# ---------------------------------------------------------------------
# NSE Session
# ---------------------------------------------------------------------

class NSEClient:
    """
    Robust NSE HTTP client.

    NSE can reject requests or return HTML instead of the expected
    JSON response. This client establishes a browser-like session and
    retries those situations.
    """

    def __init__(
        self,
        timeout: int = 30,
        max_retries: int = 5,
    ):
        self.timeout = timeout
        self.max_retries = max_retries

        self.session = requests.Session()

        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 "
                    "(KHTML, like Gecko) "
                    "Chrome/142.0.0.0 Safari/537.36"
                ),
                "Accept": (
                    "application/json,text/plain,*/*"
                ),
                "Accept-Language": "en-US,en;q=0.9",
                "Connection": "keep-alive",
                "Referer": NSE_BASE_URL + "/",
                "DNT": "1",
            }
        )

        self.initialized = False

    # -------------------------------------------------------------
    # Session initialization
    # -------------------------------------------------------------

    def initialize(self) -> None:
        """
        Open NSE pages first to establish cookies/session.
        """

        if self.initialized:
            return

        log.info("Initializing NSE session...")

        urls = [
            NSE_HOME,
            NSE_MARKET_DATA_PAGE,
            NSE_HISTORICAL_PAGE,
        ]

        for url in urls:
            try:
                response = self.session.get(
                    url,
                    timeout=self.timeout,
                )

                log.info(
                    "NSE session GET %s -> %s",
                    url,
                    response.status_code,
                )

                time.sleep(random.uniform(1.0, 2.0))

            except requests.RequestException as exc:
                log.warning(
                    "NSE session initialization failed for %s: %s",
                    url,
                    exc,
                )

        self.initialized = True

    # -------------------------------------------------------------
    # Generic GET
    # -------------------------------------------------------------

    def get(
        self,
        url: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> requests.Response:

        self.initialize()

        last_error = None

        for attempt in range(1, self.max_retries + 1):

            try:
                response = self.session.get(
                    url,
                    params=params,
                    timeout=self.timeout,
                )

                if response.status_code == 200:

                    if looks_like_html(response):

                        last_error = RuntimeError(
                            "NSE returned HTML instead of expected data"
                        )

                        log.warning(
                            "NSE returned HTML "
                            "(attempt %s/%s): %s",
                            attempt,
                            self.max_retries,
                            response.url,
                        )

                    else:
                        return response

                else:

                    last_error = RuntimeError(
                        f"NSE HTTP {response.status_code}"
                    )

                    log.warning(
                        "NSE HTTP %s "
                        "(attempt %s/%s): %s",
                        response.status_code,
                        attempt,
                        self.max_retries,
                        response.url,
                    )

            except requests.RequestException as exc:

                last_error = exc

                log.warning(
                    "NSE request failed "
                    "(attempt %s/%s): %s",
                    attempt,
                    self.max_retries,
                    exc,
                )

            # Re-establish cookies/session before retry.
            self.initialized = False

            wait_seconds = min(
                2 ** attempt,
                20,
            ) + random.uniform(0.5, 1.5)

            time.sleep(wait_seconds)

        raise RuntimeError(
            f"NSE request failed after "
            f"{self.max_retries} attempts: {url}"
        ) from last_error

    # -------------------------------------------------------------
    # JSON
    # -------------------------------------------------------------

    def get_json(
        self,
        url: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Any:

        response = self.get(
            url,
            params=params,
        )

        try:
            return response.json()

        except ValueError as exc:

            sample = response.text[:300].replace(
                "\n",
                " ",
            )

            raise RuntimeError(
                "NSE response was not valid JSON. "
                f"Response sample: {sample}"
            ) from exc


# ---------------------------------------------------------------------
# NSE Historical Preprocessor
# ---------------------------------------------------------------------

class NSEHistoricalPreprocessor:

    def __init__(
        self,
        years: int = 3,
        request_delay: float = 1.0,
    ):
        self.years = years
        self.request_delay = request_delay

        self.nse = NSEClient()

        ensure_directories()

        self.status = load_json(
            STATUS_FILE,
            {
                "started_at": None,
                "updated_at": None,
                "stocks_total": 0,
                "completed": 0,
                "failed": 0,
                "stocks": {},
            },
        )

    # -----------------------------------------------------------------
    # Date range
    # -----------------------------------------------------------------

    def get_date_range(self):
        """
        Get exactly 3 years ending today.

        We do NOT remove the current day here.

        Whatever NSE provides is stored.
        Later analysis can decide whether a metric should use only
        completed sessions.
        """

        end_date = date.today()

        start_date = end_date.replace(
            year=end_date.year - self.years
        )

        return start_date, end_date

    # -----------------------------------------------------------------
    # Stock universe
    # -----------------------------------------------------------------

    def get_nse_stock_universe(self) -> List[Dict[str, Any]]:
        """
        Obtain NSE equity securities.

        Uses NSE equity-stock quote/index data as the initial universe.

        The service keeps only actual equity EQ series entries.
        """

        log.info("Loading NSE equity stock universe...")

        # NSE's equity-stockIndices endpoint provides the equity
        # market universe. We request NIFTY 500 only as a fallback
        # is NOT desired, therefore failure here is raised.
        #
        # For the complete universe, NSE's security master / equity
        # list should be used where available.
        #
        # The API endpoint below is intentionally kept separate so
        # that the universe source can be replaced by the official
        # NSE security-master download without touching the rest of
        # the service.

        url = f"{NSE_API_BASE}/equity-stockIndices"

        data = self.nse.get_json(
            url,
            params={
                "index": "NIFTY 500",
            },
        )

        records = data.get("data", [])

        if not records:
            raise RuntimeError(
                "NSE returned no stock records."
            )

        stocks = []

        for row in records:

            symbol = str(
                row.get("symbol", "")
            ).strip().upper()

            if not symbol:
                continue

            stocks.append(
                {
                    "symbol": symbol,
                    "company_name": row.get(
                        "meta",
                        {},
                    ).get(
                        "companyName"
                    )
                    if isinstance(
                        row.get("meta"),
                        dict,
                    )
                    else None,
                    "series": "EQ",
                }
            )

        # Remove duplicate symbols.
        unique = {}

        for stock in stocks:
            unique[stock["symbol"]] = stock

        stocks = list(unique.values())

        stocks.sort(
            key=lambda x: x["symbol"]
        )

        log.info(
            "NSE universe loaded: %s stocks",
            len(stocks),
        )

        return stocks

    # -----------------------------------------------------------------
    # Save stock universe
    # -----------------------------------------------------------------

    def save_stock_universe(
        self,
        stocks: List[Dict[str, Any]],
    ) -> None:

        df = pd.DataFrame(stocks)

        df.to_csv(
            STOCKS_FILE,
            index=False,
            encoding="utf-8",
        )

        log.info(
            "Saved NSE stock universe: %s",
            STOCKS_FILE,
        )

    # -----------------------------------------------------------------
    # Historical price data
    # -----------------------------------------------------------------

    def download_historical_prices(
        self,
        symbol: str,
    ) -> Path:

        symbol_safe = safe_symbol(symbol)

        output_dir = PRICES_DIR / symbol_safe
        output_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        output_file = (
            output_dir /
            f"{symbol_safe}_3y.csv"
        )

        # Resume if already downloaded.
        if output_file.exists():
            try:
                existing = pd.read_csv(
                    output_file,
                    nrows=2,
                )

                if not existing.empty:
                    log.info(
                        "[PRICE] %-15s already exists",
                        symbol,
                    )
                    return output_file

            except Exception:
                pass

        start_date, end_date = (
            self.get_date_range()
        )

        params = {
            "symbol": symbol,
            "series": '["EQ"]',
            "from": start_date.strftime(
                "%d-%m-%Y"
            ),
            "to": end_date.strftime(
                "%d-%m-%Y"
            ),
        }

        log.info(
            "[PRICE] %-15s downloading %s -> %s",
            symbol,
            start_date,
            end_date,
        )

        data = self.nse.get_json(
            NSE_HISTORICAL_URL,
            params=params,
        )

        records = data.get("data", [])

        if not records:
            raise RuntimeError(
                f"NSE returned no historical "
                f"price records for {symbol}"
            )

        # IMPORTANT:
        # Store every field NSE returned.
        df = pd.json_normalize(records)

        df.to_csv(
            output_file,
            index=False,
            encoding="utf-8",
        )

        log.info(
            "[PRICE] %-15s saved %s rows",
            symbol,
            len(df),
        )

        return output_file

    # -----------------------------------------------------------------
    # Company profile
    # -----------------------------------------------------------------

    def download_company_profile(
        self,
        symbol: str,
    ) -> Path:

        symbol_safe = safe_symbol(symbol)

        output_file = (
            COMPANY_DIR /
            f"{symbol_safe}.json"
        )

        # Resume if already available.
        if output_file.exists():
            existing = load_json(
                output_file,
                None,
            )

            if existing:
                log.info(
                    "[PROFILE] %-15s already exists",
                    symbol,
                )
                return output_file

        log.info(
            "[PROFILE] %-15s downloading",
            symbol,
        )

        data = self.nse.get_json(
            NSE_QUOTE_URL,
            params={
                "symbol": symbol,
            },
        )

        # Store the COMPLETE raw NSE response.
        save_json(
            output_file,
            data,
        )

        log.info(
            "[PROFILE] %-15s saved",
            symbol,
        )

        return output_file

    # -----------------------------------------------------------------
    # Process one stock
    # -----------------------------------------------------------------

    def process_stock(
        self,
        stock: Dict[str, Any],
    ) -> bool:

        symbol = stock["symbol"]

        stock_status = self.status[
            "stocks"
        ].setdefault(
            symbol,
            {
                "price": "pending",
                "company_profile": "pending",
                "error": None,
                "updated_at": None,
            },
        )

        try:

            # ---------------------------------------------------------
            # 1. Historical market data
            # ---------------------------------------------------------

            if stock_status["price"] != "completed":

                self.download_historical_prices(
                    symbol
                )

                stock_status["price"] = (
                    "completed"
                )

                time.sleep(
                    self.request_delay
                    + random.uniform(
                        0.1,
                        0.5,
                    )
                )

            # ---------------------------------------------------------
            # 2. Company profile
            # ---------------------------------------------------------

            if (
                stock_status["company_profile"]
                != "completed"
            ):

                self.download_company_profile(
                    symbol
                )

                stock_status[
                    "company_profile"
                ] = "completed"

                time.sleep(
                    self.request_delay
                    + random.uniform(
                        0.1,
                        0.5,
                    )
                )

            stock_status["error"] = None

            stock_status[
                "updated_at"
            ] = datetime.now().isoformat()

            return True

        except Exception as exc:

            stock_status["error"] = str(exc)

            stock_status[
                "updated_at"
            ] = datetime.now().isoformat()

            log.error(
                "[FAILED] %-15s | %s",
                symbol,
                exc,
            )

            return False

        finally:

            self.status[
                "updated_at"
            ] = datetime.now().isoformat()

            save_json(
                STATUS_FILE,
                self.status,
            )

    # -----------------------------------------------------------------
    # Main execution
    # -----------------------------------------------------------------

    def run(self) -> None:

        started_at = datetime.now()

        self.status[
            "started_at"
        ] = started_at.isoformat()

        log.info("=" * 70)
        log.info("NSE HISTORICAL PREPROCESSOR START")
        log.info("=" * 70)

        # -------------------------------------------------------------
        # Get stock universe
        # -------------------------------------------------------------

        stocks = self.get_nse_stock_universe()

        self.save_stock_universe(stocks)

        self.status[
            "stocks_total"
        ] = len(stocks)

        save_json(
            STATUS_FILE,
            self.status,
        )

        # -------------------------------------------------------------
        # Process every stock
        # -------------------------------------------------------------

        completed = 0
        failed = 0

        for index, stock in enumerate(
            stocks,
            start=1,
        ):

            symbol = stock["symbol"]

            log.info(
                "[%s/%s] Processing %s",
                index,
                len(stocks),
                symbol,
            )

            success = self.process_stock(
                stock
            )

            if success:
                completed += 1
            else:
                failed += 1

            self.status[
                "completed"
            ] = completed

            self.status[
                "failed"
            ] = failed

            save_json(
                STATUS_FILE,
                self.status,
            )

        # -------------------------------------------------------------
        # Finished
        # -------------------------------------------------------------

        finished_at = datetime.now()

        self.status[
            "finished_at"
        ] = finished_at.isoformat()

        self.status[
            "duration_seconds"
        ] = (
            finished_at - started_at
        ).total_seconds()

        save_json(
            STATUS_FILE,
            self.status,
        )

        log.info("=" * 70)
        log.info("NSE HISTORICAL PREPROCESSOR COMPLETE")
        log.info("=" * 70)

        log.info(
            "Total stocks : %s",
            len(stocks),
        )

        log.info(
            "Completed    : %s",
            completed,
        )

        log.info(
            "Failed       : %s",
            failed,
        )

        log.info(
            "Status file  : %s",
            STATUS_FILE,
        )

        log.info("=" * 70)


# ---------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------

if __name__ == "__main__":

    processor = NSEHistoricalPreprocessor(
        years=3,

        # Keep this reasonably slow initially.
        # NSE can throttle aggressive requests.
        request_delay=1.0,
    )

    processor.run()

