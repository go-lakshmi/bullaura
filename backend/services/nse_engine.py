# engine/scanner.py
from nselib import capital_market
import pandas as pd
import time
import queue
import threading
import pandas as pd
from pathlib import Path
from backend.config.logger import logger as log
from SmartApi.smartWebSocketV2 import SmartWebSocketV2
from backend.services.mobile_dashboard import MobileDashboard
import os
import io
import json
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from nselib import capital_market
import pandas as pd
import logging
import math
import os
import re
import threading
import time
from datetime import date, datetime, timezone,timedelta
from typing import Any, Dict, List, Optional, Tuple
import numpy as np
import pandas as pd
import requests
import yfinance as yf
import traceback
from collections import deque

class FreeNewsManager:
    """
    Free news layer for the top technical candidates.

    Sources:
      1) NSE corporate-announcement endpoint (primary).
      2) yfinance/Yahoo news (secondary).

    News is cached locally for NEWS_TTL_SECONDS so the live tick loop never
    makes a news request on every tick or every 60-second dispatch.
    """

    NEWS_TTL_SECONDS = 10 * 60
    NEWS_WINDOW_SECONDS = 24 * 60 * 60

    POSITIVE_TERMS = (
        "order", "contract", "bagging", "award", "won", "wins",
        "acquisition", "acquire", "approval", "approved", "partnership",
        "strategic", "capacity expansion", "expansion", "commission",
        "launch", "record", "strong results", "profit", "revenue growth",
        "fund raise", "fundraising", "investment", "joint venture",
    )
    NEGATIVE_TERMS = (
        "penalty", "fine", "investigation", "fraud", "default", "downgrade",
        "resignation", "arrest", "regulatory action", "show cause",
        "order cancellation", "cancelled", "canceled", "shutdown",
        "loss", "weak results", "decline", "debt", "insolvency",
        "rating downgrade", "sebi action", "ed action",
    )
    MIXED_TERMS = (
        "clarification", "media report", "rumour", "rumor", "litigation",
        "court", "dispute", "settlement", "restructuring",
    )

    def __init__(self):
        self.lock = threading.Lock()
        self.cache = {}
        self.last_refresh = 0.0
        self.refresh_in_progress = False

    @staticmethod
    def _clean_symbol(symbol):
        return str(symbol or "").upper().replace(".NS", "").replace(".BO", "").strip()

    @staticmethod
    def _text(value):
        if value is None:
            return ""
        return " ".join(str(value).replace("\n", " ").split()).strip()

    def _classify(self, title, summary=""):
        text = f"{title} {summary}".lower()
        positive = sum(1 for term in self.POSITIVE_TERMS if term in text)
        negative = sum(1 for term in self.NEGATIVE_TERMS if term in text)
        mixed = sum(1 for term in self.MIXED_TERMS if term in text)

        if positive and negative:
            return "mixed"
        if negative >= 1 and negative >= positive:
            return "negative"
        if positive >= 1 and positive > negative:
            return "positive"
        if mixed:
            return "mixed"
        return "none"

    def _merge_status(self, current, new_status):
        if current == "none":
            return new_status
        if new_status == "none" or new_status == current:
            return current
        return "mixed"

    def _store_article(
        self,
        symbol,
        title,
        summary="",
        source="",
        published_at=None,
        require_timestamp=False,
    ):
        symbol = self._clean_symbol(symbol)
        if not symbol:
            return

        # Keep only fresh news. yfinance normally supplies
        # providerPublishTime; NSE supplies broadcast/an_dt style timestamps.
        if published_at is not None:
            try:
                published_ts = float(published_at)
                if published_ts > 10_000_000_000:
                    published_ts /= 1000.0
                if time.time() - published_ts > self.NEWS_WINDOW_SECONDS:
                    return
                if published_ts > time.time() + 300:
                    return
            except (TypeError, ValueError):
                if require_timestamp:
                    return
        elif require_timestamp:
            return

        status = self._classify(title, summary)
        if status == "none":
            return

        item = {
            "symbol": symbol,
            "status": status,
            "title": self._text(title)[:240],
            "source": self._text(source)[:80],
            "updated_at": time.time(),
        }
        with self.lock:
            old = self.cache.get(symbol, {
                "status": "none",
                "title": "",
                "source": "",
                "updated_at": 0.0,
            })
            old["status"] = self._merge_status(old.get("status", "none"), status)
            old["title"] = item["title"]
            old["source"] = item["source"]
            old["updated_at"] = item["updated_at"]
            self.cache[symbol] = old

    def _fetch_nse(self, symbols):
        """
        Fetch NSE corporate announcements once for the whole candidate set.

        NSE's public corporate-filings page exposes Symbol / Subject /
        Broadcast Date-Time. The endpoint is intentionally treated
        defensively because NSE can change its response schema.
        """
        if not symbols:
            return

        url = "https://www.nseindia.com/api/corporate-announcements"
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json,text/plain,*/*",
            "Referer": "https://www.nseindia.com/companies-listing/"
                       "corporate-filings-application?id=allAnnouncements",
        }

        session = requests.Session()
        try:
            session.get(
                "https://www.nseindia.com/",
                headers=headers,
                timeout=8,
            )
            response = session.get(
                url,
                headers=headers,
                params={"index": "equities"},
                timeout=10,
            )
            response.raise_for_status()
            payload = response.json()

            rows = payload if isinstance(payload, list) else (
                payload.get("data", []) if isinstance(payload, dict) else []
            )
            symbol_set = {self._clean_symbol(s) for s in symbols}

            for row in rows:
                if not isinstance(row, dict):
                    continue
                symbol = self._clean_symbol(
                    row.get("symbol") or row.get("sym") or row.get("symbolName")
                )
                if symbol not in symbol_set:
                    continue

                title = (
                    row.get("subject")
                    or row.get("desc")
                    or row.get("description")
                    or row.get("sm_name")
                    or ""
                )
                summary = row.get("details") or row.get("desc") or ""
                published_at = (
                    row.get("broadcastDateTime")
                    or row.get("broadcast_date_time")
                    or row.get("an_dt")
                    or row.get("sort_date")
                    or row.get("date")
                )

                # NSE commonly returns an ISO/date string rather than Unix time.
                if published_at and not isinstance(published_at, (int, float)):
                    parsed = None
                    for fmt in (
                        "%d-%b-%Y %H:%M:%S",
                        "%d-%m-%Y %H:%M:%S",
                        "%d-%b-%Y",
                        "%d-%m-%Y",
                        "%Y-%m-%d %H:%M:%S",
                        "%Y-%m-%d",
                    ):
                        try:
                            parsed = datetime.strptime(
                                str(published_at).strip(), fmt
                            ).replace(tzinfo=timezone.utc).timestamp()
                            break
                        except ValueError:
                            pass
                    published_at = parsed

                self._store_article(
                    symbol,
                    title,
                    summary,
                    "NSE",
                    published_at=published_at,
                    require_timestamp=False,
                )

            log.info("NSE news refresh completed for %d candidates.", len(symbols))
        except Exception as exc:
            log.warning("NSE news refresh failed: %s", exc)

    def _fetch_yfinance(self, symbols):
        """
        Secondary broad-news source. yfinance exposes Tickers.news(), which
        lets us submit the candidate universe as one batch abstraction rather
        than manually creating 30 individual news calls.
        """
        if not symbols:
            return

        try:
            tickers = " ".join(f"{self._clean_symbol(s)}.NS" for s in symbols)
            yf_tickers = yf.Tickers(tickers)
            batch_news = yf_tickers.news()

            if not isinstance(batch_news, dict):
                return

            symbol_set = {self._clean_symbol(s) for s in symbols}

            for ticker_key, articles in batch_news.items():
                symbol = self._clean_symbol(ticker_key)
                if symbol not in symbol_set:
                    continue

                if isinstance(articles, dict):
                    articles = articles.get("news", [])
                if not isinstance(articles, list):
                    continue

                for article in articles:
                    if not isinstance(article, dict):
                        continue

                    title = article.get("title") or ""
                    summary = article.get("summary") or ""
                    publisher = article.get("publisher") or "Yahoo"

                    self._store_article(
                        symbol,
                        title,
                        summary,
                        publisher,
                        published_at=article.get("providerPublishTime"),
                        require_timestamp=True,
                    )

            log.info(
                "yfinance news refresh completed for %d candidates.",
                len(symbols),
            )
        except Exception as exc:
            log.warning("yfinance news refresh failed: %s", exc)

    def refresh(self, symbols, force=False):
        symbols = list(dict.fromkeys(self._clean_symbol(s) for s in symbols if s))
        if not symbols:
            return

        now = time.time()
        with self.lock:
            if self.refresh_in_progress:
                return
            if not force and now - self.last_refresh < self.NEWS_TTL_SECONDS:
                return
            self.refresh_in_progress = True
            self.last_refresh = now

        try:
            # Clear only statuses for this candidate universe. This prevents
            # an old positive headline from surviving forever.
            with self.lock:
                for symbol in symbols:
                    self.cache[symbol] = {
                        "status": "none",
                        "title": "",
                        "source": "",
                        "updated_at": now,
                    }

            self._fetch_nse(symbols)
            self._fetch_yfinance(symbols)
        finally:
            with self.lock:
                self.refresh_in_progress = False

    def refresh_async(self, symbols, force=False):
        worker = threading.Thread(
            target=self.refresh,
            args=(list(symbols), force),
            daemon=True,
            name="free-news-refresh",
        )
        worker.start()

    def get(self, symbol):
        symbol = self._clean_symbol(symbol)
        with self.lock:
            item = dict(self.cache.get(symbol, {}))

        if not item:
            return {
                "news_status": "none",
                "news_title": "",
                "news_source": "",
            }

        # If the cache is stale, don't show an old directional signal.
        if time.time() - float(item.get("updated_at", 0)) > self.NEWS_WINDOW_SECONDS:
            return {
                "news_status": "none",
                "news_title": "",
                "news_source": "",
            }

        return {
            "news_status": item.get("status", "none"),
            "news_title": item.get("title", ""),
            "news_source": item.get("source", ""),
        }

    def enrich(self, signals):
        for signal in signals:
            signal.update(self.get(signal.get("symbol", "")))
        return signals

dashboard = MobileDashboard(session_name="Mainboard IPO Momentum Matrix")

class NSEHighPerformanceTradingPipeline:

    def __init__(self, broker, instrument_master, config_path="data/trades_config.json", strategy_conf_path="data/conf.json", portfolio_tracker=None, mobile_dashboard=None):
        self.broker = broker
        self.master = instrument_master
        self.config_path = Path(config_path)
        self.strategy_conf_path = Path(strategy_conf_path)
        self.bar_aggregators = {}
        self.portfolio_tracker = portfolio_tracker
        self.mobile_dashboard = MobileDashboard(session_name="Live Production Stream")
        # Local UI only for now. Telegram dispatch remains disabled until explicitly re-enabled.
        self.local_ui_only = True
        self.live_cache = {}
        self.tick_queue = queue.Queue()
        self.sws = None
        self.active_stream_tokens = set()  
        self.settings = {}
        self.scrip_master_data = None
        self.volume_shockers = {}
        # UI bridge: populated from the same live calculations used by the scanner.
        # This is presentation state only; it does not alter qualification logic.
        self.ui_state = {}
        self.swing_stocks_token = os.getenv("SWING_STOCKS_TOKEN", "")
        self.btst_stocks_token = os.getenv("BTST_STOCKS_TOKEN", "")
        self.chat_ids = os.getenv("TELEGRAM_CHAT_IDS", "").split(",") if os.getenv("TELEGRAM_CHAT_IDS") else []
        # Free news layer: refreshed only for the current top candidates.
        self.news_manager = FreeNewsManager()
        # 1. LOAD THE SCRIP MASTER FIRST SO TOKENS EXIST IN MEMORY
        self.load_raw_scrip_master()

        # --- FISHY FILTERING MEMORY SETS & CACHE PATHS ---
        self.clean_symbols_set = set()
        self.fishy_symbols_set = set()
        eq_stocks_sym = self.download_all_stocks_from_nse()
        self.download_stockdata_from_nse(eq_stocks_sym)
        

    def download_all_stocks_from_nse(self) -> List[str]:
        url = "https://archives.nseindia.com/content/equities/EQUITY_L.csv"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        }
        response = requests.get(url, headers=headers)
        df_all = pd.read_csv(io.StringIO(response.text))
        df_all.columns = df_all.columns.str.strip()

        eq_stocks = df_all[df_all["SERIES"].str.strip() == "EQ"][
            "SYMBOL"
        ].str.strip()
        return [f"{sym}" for sym in eq_stocks.dropna().unique()]   


    def download_stockdata_from_nse(self, symbols):
        """
        Download NSE 1-month data and apply ONLY these filters:

        1. Older prices D18-D21 must be greater than the recent D0-D10
           range OR the recent D0-D15 range.
        2. DailyMovePct = (ClosePrice - PrevClose) / PrevClose * 100.
        3. Average daily move of the latest 5 sessions must be < 1%.

        Only the original NSE eligibility filters are applied here.
        Additional historical breakout metrics are calculated and stored
        after those filters so the live Angel One stage can compare against
        the historical baseline.

        For passing stocks, the NSE rows plus DailyMovePct are stored in
        self.volume_shockers.
        """

        cache_file = Path("data/volume_shockers_cache.json")
        today_str = time.strftime("%Y-%m-%d")
        cache_version = "nse_live_volume_shocker_v3"

        if cache_file.exists():
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    cached_data = json.load(f)

                if (
                    cached_data.get("date") == today_str
                    and cached_data.get("version") == cache_version
                ):
                    self.volume_shockers = cached_data.get("data", {})
                    log.info(
                        f"Loaded {len(self.volume_shockers)} filtered stocks from cache."
                    )
                    return
            except Exception as e:
                log.info(f"Cache read error: {e}. Re-downloading...")

        log.info(
            f"Downloading 1-month historical data for {len(symbols)} NSE stocks..."
        )

        def fetch_single_stock(symbol):
            try:
                time.sleep(0.3)

                df = capital_market.price_volume_data(
                    symbol=symbol,
                    period="1M"
                )

                if df is None or df.empty:
                    return symbol, None

                df = df.copy()

                # ============================================================
                # SORT BY DATE - NEWEST FIRST
                #
                # D0  = latest NSE session
                # D1  = previous NSE session
                # ...
                # D21 = oldest session
                # ============================================================

                if "Date" in df.columns:
                    df["Date"] = pd.to_datetime(
                        df["Date"],
                        errors="coerce",
                        dayfirst=True
                    )

                    df = df.dropna(subset=["Date"])

                    df = df.sort_values(
                        "Date",
                        ascending=False
                    )

                # ============================================================
                # ONLY EQ SERIES
                # ============================================================

                if "Series" in df.columns:
                    df["Series"] = (
                        df["Series"]
                        .astype(str)
                        .str.strip()
                        .str.upper()
                    )

                    df = df[df["Series"] == "EQ"].copy()

                # Need at least 21 sessions
                if len(df) < 21:
                    return symbol, None

                # Keep latest 21 sessions
                df = df.head(21).copy()

                # ============================================================
                # REQUIRED COLUMNS
                # ============================================================

                required_cols = [
                    "PrevClose",
                    "ClosePrice",
                    "No.ofTrades"
                ]

                if any(col not in df.columns for col in required_cols):
                    return symbol, None

                # ============================================================
                # CONVERT NUMERIC COLUMNS
                # ============================================================

                for col in [
                    "PrevClose",
                    "OpenPrice",
                    "HighPrice",
                    "LowPrice",
                    "LastPrice",
                    "ClosePrice",
                    "AveragePrice",
                    "TotalTradedQuantity",
                    "Turnover₹",
                    "No.ofTrades"
                ]:
                    if col in df.columns:
                        df[col] = (
                            df[col]
                            .astype(str)
                            .str.replace(",", "", regex=False)
                            .str.replace("₹", "", regex=False)
                            .str.strip()
                        )

                        df[col] = pd.to_numeric(
                            df[col],
                            errors="coerce"
                        )

                # ============================================================
                # NEED VALID PRICE DATA
                # ============================================================

                df = df.dropna(
                    subset=[
                        "PrevClose",
                        "ClosePrice"
                    ]
                ).copy()

                if len(df) < 21:
                    return symbol, None

                # ============================================================
                # FILTER 1:
                # NORMAL TRADING ACTIVITY
                #
                # D0 = latest NSE record
                #
                # Use latest 20 records:
                #
                # D0-D19
                #
                # IMPORTANT:
                # D0 IS INCLUDED.
                # ============================================================

                trades_20d = (
                    df["No.ofTrades"]
                    .head(20)
                    .dropna()
                )

                if len(trades_20d) < 20:
                    return symbol, None

                avg_trades_20d = float(
                    trades_20d.mean()
                )

                # Skip stocks that normally trade too slowly.
                if avg_trades_20d < 2000:
                    return symbol, None

                close = (
                    df["ClosePrice"]
                    .astype(float)
                    .tolist()
                )

                # Available oldest four sessions:
                # D18, D19, D20, D21
                older_4 = close[17:22]

                # Recent ranges
                recent_11 = close[0:11]   # D0-D10
                recent_16 = close[0:16]   # D0-D15

                # Flexible price-structure filter:
                # Compare the MAX older price (D18-D21) with the MAX
                # recent price (D0-D10 / D0-D15).  If they are within
                # 1%, consider the historical price structure close enough.
                # This avoids rejecting stocks where the older and recent
                # price levels are essentially the same.
                older_max = max(older_4) if older_4 else 0.0
                recent_max_11 = max(recent_11) if recent_11 else 0.0
                recent_max_16 = max(recent_16) if recent_16 else 0.0

                price_difference_11_pct = (
                    abs(older_max - recent_max_11) / recent_max_11 * 100.0
                    if recent_max_11 > 0 else 999.0
                )

                price_difference_16_pct = (
                    abs(older_max - recent_max_16) / recent_max_16 * 100.0
                    if recent_max_16 > 0 else 999.0
                )

                # ============================================================
                # KEEP BOTH HISTORICAL PRICE PATTERNS
                # ============================================================
                # 1) STRICT pattern: preserve the original filter so we do not
                #    lose stocks where ALL D18-D21 prices stayed above the
                #    recent D0-D10 / D0-D15 range.
                strict_filter_11 = all(
                    old_price > recent_max_11
                    for old_price in older_4
                )
                strict_filter_16 = all(
                    old_price > recent_max_16
                    for old_price in older_4
                )

                # 2) FLEXIBLE pattern: also keep stocks where the highest old
                #    price is within 1% of the recent maximum.
                close_filter_11 = price_difference_11_pct <= 1.0
                close_filter_16 = price_difference_16_pct <= 1.0

                # Keep a stock if it matches EITHER the old strict pattern OR
                # the new max-within-1% pattern. This preserves old candidates
                # while adding the newer candidates.
                price_filter_11 = strict_filter_11 or close_filter_11
                price_filter_16 = strict_filter_16 or close_filter_16

                if not (price_filter_11 or price_filter_16):
                    return symbol, None

                # ============================================================
                # FILTER 3:
                # DAILY MOVE
                #
                # DailyMovePct =
                #
                # (ClosePrice - PrevClose)
                # ------------------------ × 100
                #       PrevClose
                # ============================================================

                daily_moves = (
                    (
                        df["ClosePrice"]
                        - df["PrevClose"]
                    )
                    / df["PrevClose"].replace(
                        0,
                        np.nan
                    )
                    * 100.0
                )

                df["DailyMovePct"] = (
                    daily_moves.round(4)
                )

                # Latest 5 NSE sessions
                #
                # D0, D1, D2, D3, D4
                #
                # D0 is included.
                last_5_moves = (
                    daily_moves
                    .head(5)
                    .dropna()
                )

                if len(last_5_moves) < 5:
                    return symbol, None

                last_5_avg_move = float(
                    last_5_moves.mean()
                )

                # Skip stocks whose recent average daily
                # movement is >= 1%.
                if last_5_avg_move >= 1.0:
                    return symbol, None

                # ============================================================
                # HISTORICAL BREAKOUT BASELINE
                #
                # These metrics are calculated from the 21 NSE sessions and
                # stored with the stock.  They are used later by the live
                # Angel One confirmation stage.
                # ============================================================

                # Latest 20 sessions, including D0.
                hist20 = df.head(20).copy()
                hist5 = df.head(5).copy()
                hist10 = df.head(10).copy()

                # Average volume / turnover / trades.
                avg_volume_20d = float(hist20["TotalTradedQuantity"].mean())                     if "TotalTradedQuantity" in hist20.columns else 0.0

                avg_turnover_20d = float(hist20["Turnover₹"].mean())                     if "Turnover₹" in hist20.columns else 0.0

                avg_trades_20d = float(hist20["No.ofTrades"].mean())

                # Historical daily range as % of previous close.
                historical_range_pct = (
                    (df["HighPrice"] - df["LowPrice"])
                    / df["PrevClose"].replace(0, np.nan)
                    * 100.0
                )

                avg_range_20d = float(
                    historical_range_pct.head(20).dropna().mean()
                ) if not historical_range_pct.head(20).dropna().empty else 0.0

                avg_range_5d = float(
                    historical_range_pct.head(5).dropna().mean()
                ) if not historical_range_pct.head(5).dropna().empty else 0.0

                # Recent highs / lows.
                recent_5_high = float(hist5["HighPrice"].max())
                recent_10_high = float(hist10["HighPrice"].max())
                recent_20_high = float(hist20["HighPrice"].max())

                recent_5_low = float(hist5["LowPrice"].min())
                recent_10_low = float(hist10["LowPrice"].min())
                recent_20_low = float(hist20["LowPrice"].min())

                latest_close = float(df.iloc[0]["ClosePrice"])

                # Close compression over the latest 5 sessions.
                close5 = hist5["ClosePrice"].dropna()
                close_compression_pct = (
                    ((float(close5.max()) - float(close5.min()))
                     / float(close5.min()) * 100.0)
                    if len(close5) >= 2 and float(close5.min()) > 0
                    else 0.0
                )

                # Higher-low structure: newest half's low should be >= older half's low.
                low_series = hist5["LowPrice"].dropna().tolist()
                higher_low_structure = False
                if len(low_series) >= 4:
                    recent_half_low = float(min(low_series[:2]))
                    older_half_low = float(min(low_series[2:]))
                    higher_low_structure = recent_half_low >= older_half_low

                # Distance of latest close from the 10D/20D high.
                distance_from_10d_high_pct = (
                    (recent_10_high - latest_close) / recent_10_high * 100.0
                    if recent_10_high > 0 else 0.0
                )
                distance_from_20d_high_pct = (
                    (recent_20_high - latest_close) / recent_20_high * 100.0
                    if recent_20_high > 0 else 0.0
                )

                # ============================================================
                # CONVERT DATAFRAME TO RECORDS
                #
                # ALL 21 NSE RECORDS ARE RETAINED.
                # ============================================================

                records = df.to_dict(
                    orient="records"
                )

                for record in records:

                    for key, value in list(
                        record.items()
                    ):

                        if isinstance(
                            value,
                            pd.Timestamp
                        ):
                            record[key] = value.strftime(
                                "%Y-%m-%d"
                            )

                        elif isinstance(
                            value,
                            np.integer
                        ):
                            record[key] = int(value)

                        elif isinstance(
                            value,
                            np.floating
                        ):
                            record[key] = (
                                None
                                if np.isnan(value)
                                else float(value)
                            )

                # ============================================================
                # RETURN FILTERED STOCK
                # ============================================================
                log.info(f" {symbol} : {records} ")
                return symbol, {
                    "symbol": symbol,

                    # Historical baseline used by live confirmation.
                    "avg_trades_20d": round(avg_trades_20d, 2),
                    "avg_volume_20d": round(avg_volume_20d, 2),
                    "avg_turnover_20d": round(avg_turnover_20d, 2),
                    "avg_range_20d_pct": round(avg_range_20d, 4),
                    "avg_range_5d_pct": round(avg_range_5d, 4),

                    # Historical structure.
                    "recent_5_high": round(recent_5_high, 2),
                    "recent_10_high": round(recent_10_high, 2),
                    "recent_20_high": round(recent_20_high, 2),
                    "recent_5_low": round(recent_5_low, 2),
                    "recent_10_low": round(recent_10_low, 2),
                    "recent_20_low": round(recent_20_low, 2),
                    "close_compression_5d_pct": round(close_compression_pct, 4),
                    "higher_low_structure": higher_low_structure,
                    "distance_from_10d_high_pct": round(
                        distance_from_10d_high_pct, 4
                    ),
                    "distance_from_20d_high_pct": round(
                        distance_from_20d_high_pct, 4
                    ),

                    # Existing filters.
                    "last_5_avg_daily_move": round(
                        last_5_avg_move,
                        4
                    ),
                    "price_structure_filter": (
                        "D0-D10"
                        if price_filter_11
                        else "D0-D15"
                    ),

                    # ALL 21 NSE records.
                    "nse_data": records,
                }

            except Exception as ex:

                log.warning(
                    f"NSE filtering failed for {symbol}: {ex}"
                )

                return symbol, None

        results = {}

        with ThreadPoolExecutor(max_workers=3) as executor:
            future_to_symbol = {
                executor.submit(fetch_single_stock, sym): sym
                for sym in symbols
            }

            completed = 0

            for future in as_completed(future_to_symbol):
                try:
                    symbol, result = future.result(timeout=15)
                    if result:
                        results[symbol] = result
                except Exception as thread_err:
                    log.warning(f"Stock processing error: {thread_err}")

                completed += 1

                if completed % 50 == 0:
                    log.info(
                        f"Progress: {completed}/{len(symbols)} stocks processed..."
                    )

                time.sleep(0.1)

        self.volume_shockers = results

        log.info(
            f"\nNSE filters passed: {len(results)} / {len(symbols)} stocks."
        )

        try:
            cache_file.parent.mkdir(parents=True, exist_ok=True)

            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "date": today_str,
                        "version": cache_version,
                        "data": self.volume_shockers,
                    },
                    f,
                    indent=2,
                    default=str,
                )

            log.info("NSE filtered data cached successfully.")

        except Exception as e:
            log.info(f"Failed to save cache: {e}")
    

    def load_raw_scrip_master(self):
            """
            Pre-loads structural token dictionary mappings from Angel One.
            Follows a strict date-cached workflow:
            1. If local file exists and was modified today -> Load instantly.
            2. If file is older or missing -> Try downloading fresh chunks.
            3. If download fails -> Fallback to previous local copy.
            """
            if self.scrip_master_data is not None:
                return
    
            local_master_path = Path("data/local_scrip_master.json")
            temp_master_path = Path("data/local_scrip_master.tmp")
            today_date = date.today()
    
            if local_master_path.exists():
                try:
                    file_timestamp = local_master_path.stat().st_mtime
                    file_modify_date = datetime.fromtimestamp(file_timestamp).date()
    
                    if file_modify_date == today_date:
                        log.info("Local scrip master is up to date. Loading from disk...")
                        with open(local_master_path, "r", encoding="utf-8") as f:
                            self.scrip_master_data = json.load(f)
                        log.info(f"Loaded {len(self.scrip_master_data)} tokens from today's local file cache.")
                        return
                    else:
                        log.info("Local scrip master is old. Attempting fresh morning sync...")
                except Exception as e:
                    log.warning(f"Error checking local file stats: {e}. Proceeding to download...")
    
            log.info("Attempting to stream fresh Angel One token master registry mapping schemas...")
            import requests
            from requests.adapters import HTTPAdapter
            from urllib3.util import Retry
            
            url = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
            
            session = requests.Session()
            retries = Retry(total=4, backoff_factor=2, status_forcelist=[500, 502, 503, 504], raise_on_status=False)
            session.mount("https://", HTTPAdapter(max_retries=retries))
            
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "application/json",
                "Connection": "keep-alive"
            }
            
            try:
                local_master_path.parent.mkdir(parents=True, exist_ok=True)
                
                with session.get(url, headers=headers, timeout=45, stream=True) as response:
                    response.raise_for_status()
                    
                    with open(temp_master_path, "wb") as f:
                        for chunk in response.iter_content(chunk_size=512 * 1024):
                            if chunk:
                                f.write(chunk)
                
                if temp_master_path.exists():
                    if local_master_path.exists():
                        local_master_path.unlink()
                    temp_master_path.rename(local_master_path)
                                
                with open(local_master_path, "r", encoding="utf-8") as f:
                    self.scrip_master_data = json.load(f)
                    
                log.info(f"Successfully downloaded and loaded fresh master records: {len(self.scrip_master_data)} tokens.")
                return
    
            except Exception as e:
                log.warning(f"Network download failed: {e}")
                
                if temp_master_path.exists():
                    try: temp_master_path.unlink()
                    except Exception: pass
                
                if local_master_path.exists():
                    log.info("Network dropped! Reverting to local copy to keep the pipeline alive...")
                    try:
                        with open(local_master_path, "r", encoding="utf-8") as f:
                            self.scrip_master_data = json.load(f)
                        log.info(f"Successfully recovered pipeline using fallback cache data ({len(self.scrip_master_data)} tokens loaded).")
                        return
                    except Exception as file_err:
                        log.error(f"Fallback copy was also unreadable or corrupted: {file_err}")
                
                log.error("Critically missed both online streaming paths and local file indicators.")
                raise

    def start_websocket_stream(self):
        """Initializes the Angel One WebSocket connection and binds stream event handlers."""
        self.load_raw_scrip_master()
        feed_token = self.broker.smart_api.getfeedToken()
        self.sws = SmartWebSocketV2(self.broker.jwt_token, self.broker.api_key, self.broker.client_id, feed_token)

        def on_open(wsapp):
            log.info("WebSocket Core Link connected successfully. Subscribing to filtered stocks...")
            self.subscribe_filtered_stocks()

        def on_data(wsapp, message):
            self.tick_queue.put(message)

        def on_error(wsapp, error):
            log.error(f"WebSocket Connection Error: {error}")

        def on_close(wsapp, close_status_code, close_msg):
            log.warning("WebSocket Link disconnected from exchange.")

        self.sws.on_open = on_open
        self.sws.on_data = on_data
        self.sws.on_error = on_error
        self.sws.on_close = on_close
        
        threading.Thread(target=self.sws.connect, daemon=True).start()
        # threading.Thread(target=self.run_continuous_funnel_loop, daemon=True).start()

    def subscribe_filtered_stocks(self):
        """Extracts stock symbols from self.volume_shockers, maps them to tokens, and subscribes via WebSocket."""
        target_tokens = []
        
        # 1) Extract clean stock symbols from self.volume_shockers data (stripping suffix like '.NS')
        volume_shocker_symbols = {
            sym.replace(".NS", "").strip() for sym in self.volume_shockers.keys()
        }
        
        if not volume_shocker_symbols:
            log.warning("No symbols found in self.volume_shockers to subscribe.")
            return

        for item in self.scrip_master_data:
            inst = item.get("raw_instrument", item) if isinstance(item, dict) else item
            if isinstance(inst, dict) and inst.get("exch_seg") == "NSE":
                symbol_root = inst.get("symbol", "").split("-")[0].strip()
                if symbol_root in volume_shocker_symbols:
                    token = str(inst.get("token")).strip()
                    target_tokens.append(token)
                    if token not in self.live_cache:
                        self.live_cache[token] = {
                            "symbol": symbol_root,
                            "ticks": [],
                            "open_price": 0.0,
                            "day_high": 0.0,
                            "day_low": 0.0,
                            "cum_vol_price": 0.0,
                            "cum_vol": 0,
                            "prev_ltp": 0.0,
                            "prev_volume": 0,
                            "prev_buy_qty": 0.0,
                            "prev_sell_qty": 0.0,
                            "prev_buy_orders": 0,
                            "prev_sell_orders": 0,
                            "consecutive_price_ups": 0,
                            "breakout_hold_count": 0,
                            "last_breakout_level": 0.0,
                            "last_signal": None,
                            "live_samples": deque(maxlen=1800),
                            "last_stabilization_check": 0.0,
                            "breakout_first_seen": 0.0,
                            "breakout_last_seen": 0.0,
                            "breakout_max_pullback_pct": 0.0
                        }

        # 2) Connect and subscribe to WebSocket channels for each stock data
        if target_tokens and self.sws and self.sws.wsapp and self.sws.wsapp.sock and self.sws.wsapp.sock.connected:
            self.sws.subscribe(correlation_id="volume_shockers_sync", mode=3, token_list=[{"exchangeType": 1, "tokens": target_tokens}])
            self.active_stream_tokens.update(target_tokens)
            log.info(f"Successfully subscribed to {len(target_tokens)} tokens from self.volume_shockers.")


    def run_continuous_funnel_loop(self):
        """
        Compares NSE historical preparation data with live Angel One data.

        Historical NSE:
            - liquidity baseline
            - recent highs/lows
            - compression
            - range baseline
            - historical price structure

        Live Angel One:
            - LTP vs resistance
            - intraday range / close-position strength
            - volume pace (RVOL)
            - trade pace
            - turnover
            - buying pressure
            - buy quantity / buy orders
            - breakout persistence

        Telegram receives only WATCH / BUY CANDIDATE / HOLD style signals.
        """

        log.info(
            "Continuous live breakout confirmation loop online."
        )

        accumulated_signals = []
        accumulated_btst_signals = []
        last_dispatch_time = time.time()

        while True:
            try:
                # Process the first available tick, then immediately drain all
                # ticks already waiting in the queue.  This keeps every subscribed
                # stock moving through the existing Angel One -> analysis pipeline
                # without introducing an artificial 1-minute processing wait.
                msg = self.tick_queue.get(timeout=1)

                pending_messages = [msg]
                while True:
                    try:
                        pending_messages.append(self.tick_queue.get_nowait())
                    except queue.Empty:
                        break

                for msg in pending_messages:
                    token = str(msg.get("token"))
                    cache = self.live_cache.get(token)

                    if not cache:
                        continue

                    symbol = cache.get("symbol", "")
                    cs = self.analyze_tick_metrics(token, msg)

                    if cs.get("ltp", 0) <= 0:
                        continue

                    past_data = self.volume_shockers.get(symbol)
                    if not past_data:
                        continue

                    signal = self.evaluate_live_breakout(
                        symbol,
                        past_data,
                        cs
                    )

                    btst_signal = self.evaluate_btst_candidate(
                        symbol,
                        past_data,
                        cs
                    )

                    # Mirror the already-computed live state to the web UI on
                    # every processed Angel One tick.  No qualification condition
                    # is changed here.
                    self._update_ui_state(
                        symbol, token, cs, signal, btst_signal, past_data
                    )

                    if btst_signal:
                        existing_btst = next(
                            (
                                item for item in accumulated_btst_signals
                                if item["symbol"] == symbol
                            ),
                            None
                        )
                        # Mobile/Telegram BTST display filter:
                        # keep only stocks with buy pressure > 40% and
                        # current gain < 3%.  Do this before the top-30
                        # collection so non-matching stocks are skipped.
                        btst_buy_pressure = float(btst_signal.get("buy", 0) or 0)
                        btst_gain = float(btst_signal.get("gain", 0) or 0)

                        if btst_buy_pressure > 30.0 and btst_gain < 5.0:
                            if (
                                existing_btst is None
                                or btst_signal["score"] > existing_btst["score"]
                            ):
                                if existing_btst is not None:
                                    accumulated_btst_signals.remove(existing_btst)
                                accumulated_btst_signals.append(btst_signal)

                    if signal:
                        # Keep the strongest/current signal for the
                        # symbol during this 20-second batch.
                        existing = next(
                            (
                                item for item in accumulated_signals
                                if item["symbol"] == symbol
                            ),
                            None
                        )

                        # Mobile/Telegram LIVE display filter:
                        # keep only stocks with buy pressure > 40% and
                        # current gain < 3%. Apply before top-30 collection.
                        live_buy_pressure = float(signal.get("buy", 0) or 0)
                        live_gain = float(signal.get("gain", 0) or 0)

                        if live_buy_pressure > 30.0 and live_gain < 5.0:
                            if (
                                existing is None
                                or signal["score"] > existing["score"]
                            ):
                                if existing is not None:
                                    accumulated_signals.remove(existing)

                                accumulated_signals.append(signal)

                # ========================================================
                # DISPATCH EVERY 20 SECONDS
                # ========================================================

                current_time = time.time()

                if current_time - last_dispatch_time >= 20.0:

                    if accumulated_signals:
                        # News is deliberately outside the technical scoring engine.
                        # Refresh asynchronously so the live tick loop is never
                        # blocked by NSE/Yahoo network calls.
                        news_symbols = [s.get("symbol") for s in accumulated_signals[:30]]
                        self.news_manager.refresh_async(news_symbols)
                        self.news_manager.enrich(accumulated_signals[:30])

                    if (not self.local_ui_only) and accumulated_signals and self.mobile_dashboard:
                        accumulated_signals.sort(
                            key=lambda x: (
                                x["score"],
                                x["gain"]
                            ),
                            reverse=True
                        )

                        log.info(
                            f"Dispatching {len(accumulated_signals)} "
                            "live breakout signals to Telegram..."
                        )

                        threading.Thread(
                            target=self.mobile_dashboard.render_and_stream_tables,
                            args=(
                                self.swing_stocks_token,
                                self.chat_ids
                            ),
                            kwargs={
                                "final_top_10": accumulated_signals[:30]
                            },
                            daemon=True
                        ).start()

                    if (not self.local_ui_only) and accumulated_btst_signals and self.mobile_dashboard and self.btst_stocks_token:
                        accumulated_btst_signals.sort(
                            key=lambda x: x["score"],
                            reverse=True
                        )
                        log.info(
                            f"Dispatching {len(accumulated_btst_signals)} BTST candidates to Telegram..."
                        )
                        threading.Thread(
                            target=self.mobile_dashboard.render_and_stream_tables,
                            args=(
                                self.btst_stocks_token,
                                self.chat_ids
                            ),
                            kwargs={
                                "final_top_10": accumulated_btst_signals[:30],
                                "mode": "btst",
                            },
                            daemon=True
                        ).start()

                    accumulated_signals.clear()
                    accumulated_btst_signals.clear()
                    last_dispatch_time = current_time

            except queue.Empty:
                # No tick arrived within one second. Still maintain the
                # 60-second dispatch clock.
                log.debug("No ticker received within the 1-second queue wait.")
                current_time = time.time()

                if current_time - last_dispatch_time >= 20.0:
                    if accumulated_signals:
                        news_symbols = [s.get("symbol") for s in accumulated_signals[:30]]
                        self.news_manager.refresh_async(news_symbols)
                        self.news_manager.enrich(accumulated_signals[:30])

                    if (not self.local_ui_only) and accumulated_signals and self.mobile_dashboard:
                        accumulated_signals.sort(
                            key=lambda x: (
                                x["score"],
                                x["gain"]
                            ),
                            reverse=True
                        )

                        threading.Thread(
                            target=self.mobile_dashboard.render_and_stream_tables,
                            args=(
                                self.swing_stocks_token,
                                self.chat_ids
                            ),
                            kwargs={
                                "final_top_10": accumulated_signals[:30]
                            },
                            daemon=True
                        ).start()

                    if (not self.local_ui_only) and accumulated_btst_signals and self.mobile_dashboard and self.btst_stocks_token:
                        accumulated_btst_signals.sort(
                            key=lambda x: x["score"],
                            reverse=True
                        )
                        log.info(
                            f"Dispatching {len(accumulated_btst_signals)} BTST candidates to Telegram..."
                        )
                        threading.Thread(
                            target=self.mobile_dashboard.render_and_stream_tables,
                            args=(
                                self.btst_stocks_token,
                                self.chat_ids
                            ),
                            kwargs={
                                "final_top_10": accumulated_btst_signals[:30],
                                "mode": "btst",
                            },
                            daemon=True
                        ).start()

                    accumulated_signals.clear()
                    accumulated_btst_signals.clear()
                    last_dispatch_time = current_time

                continue

            except Exception as e:
                error_trace = traceback.format_exc()
                log.error(
                    "Error inside run_continuous_funnel_loop: "
                    f"{e} {error_trace}"
                )

    def _update_ui_state(self, symbol, token, cs, signal, btst_signal, past_data):
        """Publish scanner state for the web dashboard without changing strategy logic."""
        cache = self.live_cache.get(str(token), {})
        cache["ui_live_signal"] = dict(signal) if signal else {}
        cache["ui_btst_signal"] = dict(btst_signal) if btst_signal else {}
        cache["ui_last_cs"] = dict(cs)
        cache["ui_updated_at"] = time.time()

        # Keep a compact per-symbol reference as well.
        self.ui_state[str(symbol)] = {
            "token": str(token),
            "symbol": symbol,
            "live_signal": dict(cache.get("ui_live_signal") or {}),
            "btst_signal": dict(cache.get("ui_btst_signal") or {}),
            "cs": dict(cs),
            "past_data": past_data,
            "updated_at": cache["ui_updated_at"],
        }

    def _market_elapsed_fraction(self):
        """
        NSE regular session is approximately 09:15-15:30.
        Returns elapsed fraction capped to [0.05, 1.0].

        Used only to normalize intraday volume/trade pace; it prevents
        early-session volume from being unfairly compared with a full-day
        historical total.
        """
        now = datetime.now()

        market_open = now.replace(
            hour=9, minute=15, second=0, microsecond=0
        )
        market_close = now.replace(
            hour=15, minute=30, second=0, microsecond=0
        )

        total_seconds = (
            market_close - market_open
        ).total_seconds()

        elapsed_seconds = (
            now - market_open
        ).total_seconds()

        if total_seconds <= 0:
            return 1.0

        return max(
            0.05,
            min(1.0, elapsed_seconds / total_seconds)
        )

    @staticmethod
    def _close_position(ltp, day_high, day_low):
        if day_high > day_low:
            return (ltp - day_low) / (day_high - day_low) * 100.0
        return 50.0

    def evaluate_live_breakout(
        self,
        symbol: str,
        past_data: dict,
        cs: dict
    ) -> Optional[dict]:
        """
        Volume Shocker decision engine.

        IMPORTANT:
        - Historical NSE data is the completed session baseline (D-1 and older).
        - Today's decision uses ONLY live Angel One observations.
        - A BUY requires agreement between price, volume pace, buying pressure,
          range strength and a sustained breakout.
        - Tick count is never treated as elapsed time. Breakout persistence is
          measured with wall-clock seconds.
        """
        now = time.time()
        ltp = float(cs.get("ltp", 0) or 0)
        previous_close = float(cs.get("last_closed_price", 0) or 0)
        if ltp <= 0 or previous_close <= 0:
            return None

        position_52w_pct = cs.get("position_52w_pct")
        if position_52w_pct is None:
            return None
        
        try:
            position_52w_pct = float(position_52w_pct)
        except (TypeError, ValueError):
            return None
        
        if position_52w_pct < 30.0:
            return None

        

        avg_volume = float(past_data.get("avg_volume_20d", 0) or 0)
        avg_trades = float(past_data.get("avg_trades_20d", 0) or 0)
        avg_turnover = float(past_data.get("avg_turnover_20d", 0) or 0)
        recent_5_high = float(past_data.get("recent_5_high", 0) or 0)
        recent_10_high = float(past_data.get("recent_10_high", 0) or 0)
        recent_20_high = float(past_data.get("recent_20_high", 0) or 0)

        if avg_volume <= 0 or recent_5_high <= 0:
            return None

        current_volume = float(cs.get("current_volume", 0) or 0)
        current_turnover = float(cs.get("current_turnover", 0) or 0)
        buying_pressure = float(cs.get("buying_pressure", 0.5) or 0.5)
        day_high = float(cs.get("day_high", ltp) or ltp)
        day_low = float(cs.get("day_low", ltp) or ltp)

        # ------------------------------------------------------------------
        # Store timestamped live observations. This is what makes the price
        # stabilization and breakout persistence time-based rather than tick-
        # based. One tick every millisecond does NOT count as one minute.
        # ------------------------------------------------------------------
        token = str(cs.get("token", ""))
        cache = self.live_cache.get(token)
        if cache is None:
            return None
        samples = cache.setdefault("live_samples", deque(maxlen=1800))
        samples.append({
            "ts": now,
            "ltp": ltp,
            "volume": current_volume,
            "buy_pressure": buying_pressure,
            "day_high": day_high,
            "day_low": day_low,
        })

        def window(seconds):
            cutoff = now - seconds
            return [x for x in samples if x["ts"] >= cutoff]

        w60 = window(60)
        w180 = window(180)
        w300 = window(300)

        # ------------------------------------------------------------------
        # Intraday pace. Linear elapsed-time normalization is the best
        # available baseline when only daily NSE totals are stored. It avoids
        # comparing 10:00 AM volume directly with a full-day historical total.
        # ------------------------------------------------------------------
        elapsed_fraction = self._market_elapsed_fraction()
        expected_volume_to_now = avg_volume * elapsed_fraction
        rvol_pace = (
            current_volume / expected_volume_to_now
            if expected_volume_to_now > 0 else 0.0
        )
        turnover_pace = (
            current_turnover / (avg_turnover * elapsed_fraction)
            if avg_turnover > 0 and current_turnover > 0 else 0.0
        )

        # Actual executed trade count is not normally present in Angel One
        # FULL packets. If a broker payload supplies it, use it; otherwise do
        # NOT pretend best-5 order counts are executed trades.
        live_trade_count = 0.0
        for key in (
            "trade_count", "number_of_trades", "no_of_trades",
            "no.oftrades", "no_of_trade", "trades"
        ):
            if msg_value := cs.get(key):
                try:
                    live_trade_count = float(msg_value)
                    break
                except (TypeError, ValueError):
                    pass
        trade_pace = (
            live_trade_count / (avg_trades * elapsed_fraction)
            if live_trade_count > 0 and avg_trades > 0 else 0.0
        )

        # ------------------------------------------------------------------
        # Price stabilization / exhaustion.
        # We want the recent fall to stop making new lows before calling it
        # absorption. This is deliberately tolerant of normal noise.
        # ------------------------------------------------------------------
        stabilization = False
        no_new_low_90s = False
        short_range_pct = 999.0
        if len(w180) >= 3:
            recent_prices = [x["ltp"] for x in w180]
            low_180 = min(recent_prices)
            high_180 = max(recent_prices)
            short_range_pct = (
                (high_180 - low_180) / previous_close * 100.0
                if previous_close > 0 else 999.0
            )
            low_90 = min(x["ltp"] for x in window(90))
            no_new_low_90s = ltp >= low_90 * 0.999
            price_change_180 = (ltp - recent_prices[0]) / recent_prices[0] * 100.0
            stabilization = (
                (short_range_pct <= 0.60 and price_change_180 >= -0.20)
                or (
                    no_new_low_90s
                    and ltp >= low_180 * 1.001
                    and price_change_180 >= -0.20
                )
            )

        # Current price direction over a real time window, not one tick.
        price_rising_60s = False
        price_rising_180s = False
        if len(w60) >= 2:
            first = w60[0]["ltp"]
            price_rising_60s = ltp > first * 1.0005
        if len(w180) >= 2:
            first = w180[0]["ltp"]
            price_rising_180s = ltp > first * 1.001

        # ------------------------------------------------------------------
        # Breakout levels. Use the highest recent resistance that is actually
        # relevant. We do not call a 5D breakout a 10D breakout accidentally.
        # ------------------------------------------------------------------
        resistance_levels = [
            (recent_5_high, "5D"),
            (recent_10_high, "10D"),
            (recent_20_high, "20D"),
        ]
        resistance_levels = [(p, n) for p, n in resistance_levels if p > 0]

        # Highest historical resistance is the strongest breakout. For the
        # watch state, use the nearest resistance above price.
        above_levels = [(p, n) for p, n in resistance_levels if ltp >= p]
        if above_levels:
            breakout_level, breakout_type = max(above_levels, key=lambda x: x[0])
        else:
            breakout_level, breakout_type = min(
                resistance_levels,
                key=lambda x: max(0.0, (x[0] - ltp) / x[0])
            )

        above_breakout = ltp >= breakout_level
        distance_to_breakout_pct = (
            (breakout_level - ltp) / breakout_level * 100.0
            if breakout_level > 0 else 999.0
        )

        # ------------------------------------------------------------------
        # Time-based breakout persistence.
        # Require at least 180 seconds above the breakout level for BUY.
        # A brief dip below the level is tolerated only if it is <0.25% and
        # the stock quickly recovers.
        # ------------------------------------------------------------------
        if above_breakout:
            if cache.get("last_breakout_level", 0) != breakout_level:
                cache["last_breakout_level"] = breakout_level
                cache["breakout_first_seen"] = now
                cache["breakout_last_seen"] = now
                cache["breakout_max_pullback_pct"] = 0.0
            else:
                cache["breakout_last_seen"] = now

            elapsed_breakout_seconds = max(
                0.0, now - float(cache.get("breakout_first_seen", now))
            )
            cache["breakout_max_pullback_pct"] = max(
                float(cache.get("breakout_max_pullback_pct", 0.0)),
                max(0.0, (breakout_level - ltp) / breakout_level * 100.0)
            )
        else:
            elapsed_breakout_seconds = 0.0
            # Reset only when price is materially below resistance.
            if distance_to_breakout_pct > 0.75:
                cache["breakout_first_seen"] = 0.0
                cache["breakout_last_seen"] = 0.0
                cache["last_breakout_level"] = 0.0
                cache["breakout_max_pullback_pct"] = 0.0

        breakout_persistent = (
            above_breakout
            and elapsed_breakout_seconds >= 180.0
            and float(cache.get("breakout_max_pullback_pct", 0.0)) <= 0.25
        )

        # ------------------------------------------------------------------
        # Price/volume relationship.
        # ------------------------------------------------------------------
        gain_pct = (ltp - previous_close) / previous_close * 100.0
        volume_strong = rvol_pace >= 1.5
        volume_very_strong = rvol_pace >= 2.0
        price_volume_bullish = (
            current_volume > 0
            and volume_strong
            and (price_rising_60s or gain_pct > 0)
        )
        absorption = (
            volume_strong
            and abs(gain_pct) <= 0.50
            and stabilization
            and buying_pressure >= 0.55
        )
        distribution_warning = (
            volume_strong
            and gain_pct < -0.50
            and self._close_position(ltp, day_high, day_low) < 40.0
            and buying_pressure < 0.50
        )

        # ------------------------------------------------------------------
        # Score: historical readiness 30 + live confirmation 70.
        # Live confirmation is intentionally dominant for a Volume Shocker.
        # ------------------------------------------------------------------
        historical_score = 0
        historical_reasons = []
        historical_score += 8  # already passed NSE eligibility
        historical_reasons.append("NSE volume-shocker universe passed")

        if abs(float(past_data.get("last_5_avg_daily_move", 0) or 0)) < 1.0:
            historical_score += 5
            historical_reasons.append("recent price movement controlled")

        compression = float(past_data.get("close_compression_5d_pct", 999) or 999)
        if compression <= 3.0:
            historical_score += 7
            historical_reasons.append("5D compression")

        if past_data.get("higher_low_structure", False):
            historical_score += 5
            historical_reasons.append("higher-low structure")

        distance_10d = float(past_data.get("distance_from_10d_high_pct", 999) or 999)
        if distance_10d <= 5.0:
            historical_score += 5
            historical_reasons.append("near 10D high")

        live_score = 0
        live_reasons = []

        if volume_very_strong:
            live_score += 15
            live_reasons.append(f"RVOL {rvol_pace:.2f}x")
        elif volume_strong:
            live_score += 11
            live_reasons.append(f"RVOL {rvol_pace:.2f}x")
        elif rvol_pace >= 1.2:
            live_score += 6
            live_reasons.append(f"RVOL {rvol_pace:.2f}x")

        if price_volume_bullish:
            live_score += 12
            live_reasons.append("price + volume aligned bullish")
        elif absorption:
            live_score += 8
            live_reasons.append("high-volume absorption / stabilization")
        elif volume_strong and gain_pct < 0:
            live_reasons.append("volume rising while price falls")

        if buying_pressure >= 0.65:
            live_score += 14
            live_reasons.append(f"buy pressure {buying_pressure * 100:.1f}%")
        elif buying_pressure >= 0.60:
            live_score += 10
            live_reasons.append(f"buy pressure {buying_pressure * 100:.1f}%")
        elif buying_pressure >= 0.55:
            live_score += 5
            live_reasons.append(f"buy pressure {buying_pressure * 100:.1f}%")

        if stabilization:
            live_score += 7
            live_reasons.append("price stabilized")
        if price_rising_60s:
            live_score += 5
            live_reasons.append("price rising over 60s")
        if price_rising_180s:
            live_score += 4
            live_reasons.append("price rising over 3m")

        close_position = self._close_position(ltp, day_high, day_low)
        if close_position >= 85:
            live_score += 7
            live_reasons.append(f"near day high ({close_position:.0f}%)")
        elif close_position >= 70:
            live_score += 4
            live_reasons.append(f"strong range position ({close_position:.0f}%)")

        if above_breakout:
            live_score += 10
            live_reasons.append(f"above {breakout_type} resistance")
        elif distance_to_breakout_pct <= 1.0:
            live_score += 5
            live_reasons.append(f"within {distance_to_breakout_pct:.2f}% of resistance")

        if turnover_pace >= 1.5:
            live_score += 3
            live_reasons.append(f"turnover pace {turnover_pace:.2f}x")

        if trade_pace > 0:
            if trade_pace >= 1.5:
                live_score += 3
                live_reasons.append(f"executed-trade pace {trade_pace:.2f}x")
            elif trade_pace >= 1.2:
                live_score += 2
                live_reasons.append(f"executed-trade pace {trade_pace:.2f}x")
        else:
            order_activity = (
                float(cs.get("total_buy_orders", 0) or 0)
                + float(cs.get("total_sell_orders", 0) or 0)
            )
            prev_order_activity = (
                float(cs.get("prev_total_orders", 0) or 0)
            )
            if order_activity > 0 and prev_order_activity > 0 and order_activity > prev_order_activity:
                live_reasons.append("order-book participation increasing")

        # ------------------------------------------------------------------
        # Final decision.
        # ------------------------------------------------------------------
        total_score = min(100, historical_score + live_score)

        # Distribution is an explicit veto.
        if distribution_warning:
            recommendation = "WAIT"
            status = "🔴 DISTRIBUTION / WAIT"
        elif (
            total_score >= 75
            and above_breakout
            and breakout_persistent
            and buying_pressure >= 0.60
            and close_position >= 70
            and (price_volume_bullish or price_rising_180s)
        ):
            recommendation = "BUY"
            status = "🚀 VOLUME SHOCKER BUY"
        elif (
            total_score >= 60
            and (
                above_breakout
                or distance_to_breakout_pct <= 1.0
                or absorption
            )
        ):
            recommendation = "WATCH"
            status = "🟡 VOLUME SHOCKER WATCH"
        elif (
            absorption
            and rvol_pace >= 1.2
            and buying_pressure >= 0.55
        ):
            recommendation = "WAIT"
            status = "🟡 ABSORPTION / WAIT FOR BREAKOUT"
        else:
            return None

        if recommendation == "BUY":
            priority = 3
        elif recommendation == "WATCH":
            priority = 2
        else:
            priority = 1

        return {
            "symbol": symbol,
            "score": int(total_score),
            "recommendation": recommendation,
            "status": status,
            "ltp": round(ltp, 2),
            "gain": round(gain_pct, 2),
                        "buy": round(buying_pressure * 100, 2),
            "sell": round((1.0 - buying_pressure) * 100, 2),
            "vol": round(rvol_pace, 2),
            "trades": round(trade_pace, 2) if trade_pace > 0 else None,
            "turnover": round(turnover_pace, 2),
            "close_pos": round(close_position, 1),
            "day_range": round((day_high - day_low) / previous_close * 100.0, 2),
            "resistance": round(breakout_level, 2),
            "distance": round(distance_to_breakout_pct, 2),
            "breakout_hold_seconds": int(elapsed_breakout_seconds),
            "breakout_persistent": breakout_persistent,
            "price_stable": stabilization,
            "price_rising_60s": price_rising_60s,
            "price_rising_180s": price_rising_180s,
            "price_volume_bullish": price_volume_bullish,
            "absorption": absorption,
            "historical_score": int(historical_score),
            "live_score": int(live_score),
            "reason": "; ".join(historical_reasons + live_reasons)[:700],
            "priority": priority,
        }

    def evaluate_btst_candidate(
        self,
        symbol: str,
        past_data: dict,
        cs: dict
    ) -> Optional[dict]:
        """
        End-of-day BTST preparation engine.

        This is intentionally separate from the live-breakout BUY engine.
        It looks for stocks that may be worth analyzing near the close for a
        next-session move: absorption, compression, recovery, or controlled
        breakout preparation.

        Important: this is a CANDIDATE ranker, not an automatic BUY signal.
        Price is allowed to be falling, flat, or rising as long as the
        behaviour is healthy (stabilizing, holding, recovering, or controlled).
        Persistent new lows / distribution are strong negative evidence.
        """
        now = time.time()
        ltp = float(cs.get("ltp", 0) or 0)
        previous_close = float(cs.get("last_closed_price", 0) or 0)
        if ltp <= 0 or previous_close <= 0:
            return None

        # ---------------------------------------------------------------
        # BTST 52-WEEK POSITION FILTER
        #
        # Angel One FULL-mode data supplies the current 52W high/low.
        # Keep only stocks trading at/above the midpoint of that range.
        #
        # 50% = exact 52W midpoint.
        # ---------------------------------------------------------------
        position_52w_pct = cs.get("position_52w_pct")
        if position_52w_pct is None:
            return None

        try:
            position_52w_pct = float(position_52w_pct)
        except (TypeError, ValueError):
            return None

        if position_52w_pct < 30.0:
            return None

        avg_volume = float(past_data.get("avg_volume_20d", 0) or 0)
        avg_turnover = float(past_data.get("avg_turnover_20d", 0) or 0)
        resistance_5 = float(past_data.get("recent_5_high", 0) or 0)
        resistance_10 = float(past_data.get("recent_10_high", 0) or 0)
        resistance_20 = float(past_data.get("recent_20_high", 0) or 0)
        if avg_volume <= 0:
            return None

        token = str(cs.get("token", ""))
        cache = self.live_cache.get(token)
        if cache is None:
            return None

        samples = cache.setdefault("live_samples", deque(maxlen=1800))
        # evaluate_live_breakout normally appends first; this fallback keeps
        # the BTST engine safe if it is called independently later.
        if not samples or samples[-1].get("ts") != now:
            samples.append({
                "ts": now,
                "ltp": ltp,
                "volume": float(cs.get("current_volume", 0) or 0),
                "buy_pressure": float(cs.get("buying_pressure", 0.5) or 0.5),
                "day_high": float(cs.get("day_high", ltp) or ltp),
                "day_low": float(cs.get("day_low", ltp) or ltp),
            })

        def window(seconds):
            cutoff = now - seconds
            return [x for x in samples if x["ts"] >= cutoff]

        w60 = window(60)
        w180 = window(180)
        w300 = window(300)
        w600 = window(600)
        if len(w180) < 3:
            return None

        elapsed_fraction = self._market_elapsed_fraction()
        current_volume = float(cs.get("current_volume", 0) or 0)
        current_turnover = float(cs.get("current_turnover", 0) or 0)
        buying_pressure = float(cs.get("buying_pressure", 0.5) or 0.5)

        expected_volume = avg_volume * elapsed_fraction
        rvol_pace = current_volume / expected_volume if expected_volume > 0 else 0.0
        turnover_pace = (
            current_turnover / (avg_turnover * elapsed_fraction)
            if avg_turnover > 0 and current_turnover > 0 else 0.0
        )

        prices_180 = [x["ltp"] for x in w180]
        prices_300 = [x["ltp"] for x in w300] or prices_180
        low_180 = min(prices_180)
        high_180 = max(prices_180)
        range_180_pct = (high_180 - low_180) / previous_close * 100.0

        # Recent-low tolerance for BTST:
        # A tiny dip below the recent low is normal market noise. Treat
        # 0% to -0.50% as a SOFT_NEW_LOW rather than rejecting the setup.
        # A move below -0.50% is treated as a genuine NEW_LOW.
        low_90_samples = window(90)
        low_90 = min(x["ltp"] for x in low_90_samples) if low_90_samples else ltp
        distance_from_low_pct = (
            (ltp - low_90) / low_90 * 100.0
            if low_90 > 0 else 0.0
        )
        no_new_low = distance_from_low_pct >= -0.50
        soft_new_low = -0.50 <= distance_from_low_pct < 0.0
        hard_new_low = distance_from_low_pct < -0.50

        first_180 = prices_180[0]
        change_180 = (ltp - first_180) / first_180 * 100.0 if first_180 > 0 else 0.0
        first_300 = prices_300[0]
        change_300 = (ltp - first_300) / first_300 * 100.0 if first_300 > 0 else 0.0

        # Higher-low structure: compare the recent low with the older part of
        # the 5-minute window. It is intentionally tolerant of tiny noise.
        higher_low = False
        if len(w300) >= 6:
            mid = len(w300) // 2
            older_low = min(x["ltp"] for x in w300[:mid])
            recent_low = min(x["ltp"] for x in w300[mid:])
            higher_low = recent_low >= older_low * 0.9995

        # Pressure trend is more useful than one snapshot.
        pressure_rising = False
        pressure_weakening = False
        if len(w180) >= 4:
            mid = len(w180) // 2
            p_old = sum(x["buy_pressure"] for x in w180[:mid]) / max(1, mid)
            p_new = sum(x["buy_pressure"] for x in w180[mid:]) / max(1, len(w180) - mid)
            pressure_rising = p_new >= p_old + 0.025
            pressure_weakening = p_new <= p_old - 0.025

        price_rising_60 = len(w60) >= 2 and ltp > w60[0]["ltp"] * 1.0005
        price_rising_180 = len(w180) >= 2 and ltp > w180[0]["ltp"] * 1.001
        price_falling_180 = change_180 < -0.20

        stabilization = (
            (range_180_pct <= 0.75 and change_180 >= -0.35)
            or (no_new_low and higher_low and change_180 >= -0.35)
        )
        recovery = (
            no_new_low
            and (price_rising_60 or price_rising_180)
            and (change_180 >= -0.20 or higher_low)
        )
        compression = (
            range_180_pct <= 0.60
            and no_new_low
            and not pressure_weakening
        )

        # Volume should be meaningful, but a late-day BTST candidate does not
        # need a full breakout today. Persistent participation is preferred.
        volume_strong = rvol_pace >= 1.20
        volume_very_strong = rvol_pace >= 1.50
        volume_fading = False
        if len(w300) >= 6:
            mid = len(w300) // 2
            v_old = w300[mid - 1]["volume"]
            v_new = w300[-1]["volume"]
            volume_fading = v_new < v_old * 0.85

        gain_pct = (ltp - previous_close) / previous_close * 100.0
        day_high = float(cs.get("day_high", ltp) or ltp)
        day_low = float(cs.get("day_low", ltp) or ltp)
        close_position = self._close_position(ltp, day_high, day_low)

        # Do not chase a stock that has already made an outsized move today.
        extended = gain_pct >= 7.0
        overextended = gain_pct >= 10.0

        resistances = [p for p in (resistance_5, resistance_10, resistance_20) if p > 0]
        nearest_resistance = min((p for p in resistances if p >= ltp), default=max(resistances) if resistances else 0.0)
        distance_to_resistance = (
            (nearest_resistance - ltp) / nearest_resistance * 100.0
            if nearest_resistance > 0 else 999.0
        )
        near_resistance = 0.0 <= distance_to_resistance <= 3.0

        # Strong negative condition: high participation + falling pressure +
        # persistent lows. This is distribution, not BTST preparation.
        distribution = (
            volume_strong
            and pressure_weakening
            and not no_new_low
            and gain_pct < -0.50
        )
        if distribution:
            return None

        score = 0
        reasons = []
        pattern_points = {
            "ABSORPTION": 0,
            "COMPRESSION": 0,
            "RECOVERY": 0,
            "BREAKOUT PREPARATION": 0,
        }

        # Historical readiness.
        score += 10
        reasons.append("NSE historical filter passed")
        if float(past_data.get("close_compression_5d_pct", 999) or 999) <= 3.0:
            score += 6
            reasons.append("5D compression")
        if past_data.get("higher_low_structure", False):
            score += 5
            reasons.append("historical higher-low")

        if volume_very_strong:
            score += 15
            reasons.append(f"RVOL {rvol_pace:.2f}x")
        elif volume_strong:
            score += 11
            reasons.append(f"RVOL {rvol_pace:.2f}x")
        elif rvol_pace >= 1.05:
            score += 5
            reasons.append(f"RVOL {rvol_pace:.2f}x")

        if pressure_rising:
            score += 12
            reasons.append("buy pressure rising")
        if buying_pressure >= 0.62:
            score += 10
            reasons.append(f"buy pressure {buying_pressure * 100:.0f}%")
        elif buying_pressure >= 0.57:
            score += 6
            reasons.append(f"buy pressure {buying_pressure * 100:.0f}%")
        elif buying_pressure < 0.45:
            score -= 8
            reasons.append("weak buy pressure")

        if not hard_new_low:
            if soft_new_low:
                score += 3
                reasons.append(f"soft new low {distance_from_low_pct:.2f}% (within -0.50%)")
            else:
                score += 8
                reasons.append("no new low")
        else:
            score -= 10
            reasons.append(f"new low {distance_from_low_pct:.2f}%")

        if higher_low:
            score += 7
            reasons.append("higher low")

        if stabilization:
            score += 9
            reasons.append("stabilizing")
            pattern_points["ABSORPTION"] += 10

        if compression:
            score += 8
            reasons.append("tight compression")
            pattern_points["COMPRESSION"] += 12

        if recovery:
            score += 10
            reasons.append("recovery")
            pattern_points["RECOVERY"] += 14

        if near_resistance and not overextended:
            score += 6
            reasons.append(f"near resistance {nearest_resistance:.2f}")
            pattern_points["BREAKOUT PREPARATION"] += 10

        if price_rising_60 and volume_strong:
            score += 5
            reasons.append("controlled rise with participation")

        if turnover_pace >= 1.2:
            score += 3
            reasons.append(f"turnover pace {turnover_pace:.2f}x")

        # Negative evidence.
        if pressure_weakening:
            score -= 8
            reasons.append("buy pressure weakening")
        if volume_fading and not recovery:
            score -= 5
            reasons.append("volume fading")
        if gain_pct < -3.0 and not stabilization:
            score -= 10
            reasons.append("uncontrolled decline")
        if extended:
            score -= 8
            reasons.append("already extended")
        if overextended:
            score -= 12

        score = max(0, min(100, int(score)))
        pattern = max(pattern_points, key=pattern_points.get)
        if pattern_points[pattern] <= 0:
            return None

        # A candidate must show preparation, not simply volume.
        valid_setup = (
            volume_strong
            and no_new_low
            and (
                stabilization
                or compression
                or recovery
                or (near_resistance and pressure_rising)
            )
            and buying_pressure >= 0.50
            and not overextended
        )
        if not valid_setup or score < 55:
            return None

        if recovery and stabilization:
            pattern = "ABSORPTION → RECOVERY"
        elif compression and recovery:
            pattern = "COMPRESSION → RECOVERY"

        if score >= 80:
            recommendation = "BTST CANDIDATE"
            status = "🟢 BTST CANDIDATE"
        elif score >= 65:
            recommendation = "BTST WATCH"
            status = "🟡 BTST WATCH"
        else:
            recommendation = "BTST EARLY"
            status = "⚪ BTST EARLY"

        return {
            "symbol": symbol,
            "score": score,
            "recommendation": recommendation,
            "status": status,
            "ltp": round(ltp, 2),
            "gain": round(gain_pct, 2),
            "position_52w_pct": round(position_52w_pct, 2),
            "buy": round(buying_pressure * 100, 2),
            "sell": round((1.0 - buying_pressure) * 100, 2),
            "vol": round(rvol_pace, 2),
            "turnover": round(turnover_pace, 2),
            "close_pos": round(close_position, 1),
            "day_range": round((day_high - day_low) / previous_close * 100.0, 2),
            "resistance": round(nearest_resistance, 2),
            "distance": round(distance_to_resistance, 2),
            "price_stable": stabilization,
            "price_rising_60s": price_rising_60,
            "price_rising_180s": price_rising_180,
            "no_new_low": no_new_low,
            "soft_new_low": soft_new_low,
            "hard_new_low": hard_new_low,
            "distance_from_low_pct": round(distance_from_low_pct, 2),
            "higher_low": higher_low,
            "recovery": recovery,
            "compression": compression,
            "pressure_rising": pressure_rising,
            "pattern": pattern,
            "historical_score": min(21, 10 + (6 if float(past_data.get("close_compression_5d_pct", 999) or 999) <= 3.0 else 0) + (5 if past_data.get("higher_low_structure", False) else 0)),
            "reason": "; ".join(reasons)[:700],
            "priority": 3 if score >= 80 else (2 if score >= 65 else 1),
        }

    def analyze_tick_metrics(self, token: str, msg: dict) -> dict:
        """
        Parse an Angel One FULL-mode tick and calculate live metrics.

        Angel One prices are normally scaled by 100 when delivered as
        integer fields. The function also tolerates already-scaled floats.
        """

        if token not in self.live_cache:
            self.live_cache[token] = {
                "symbol": "",
                "ticks": [],
                "open_price": 0.0,
                "day_high": 0.0,
                "day_low": 0.0,
                "prev_ltp": 0.0,
                "prev_volume": 0,
                "prev_buy_qty": 0.0,
                "prev_sell_qty": 0.0,
                "prev_buy_orders": 0,
                "prev_sell_orders": 0,
                "consecutive_price_ups": 0,
                "breakout_hold_count": 0,
                "last_breakout_level": 0.0,
                "last_signal": None,
                "live_samples": deque(maxlen=1800),
                "last_stabilization_check": 0.0,
                "breakout_first_seen": 0.0,
                "breakout_last_seen": 0.0,
                "breakout_max_pullback_pct": 0.0
            }

        cache = self.live_cache[token]

        def _price(value):
            """
            Convert Angel One websocket price fields to rupees.

            SmartAPI FULL-mode price fields are normally integer paise:
                7876 -> 78.76

            Important:
            - Preserve a real float such as 78.76 as 78.76.
            - Convert integer/string-integer websocket values from paise.
            - Do not use a magnitude-based rule because legitimate NSE
              prices can themselves be above ₹1,000.
            """
            if value is None:
                return 0.0

            try:
                # JSON integer from Angel One: paise -> rupees.
                if isinstance(value, int) and not isinstance(value, bool):
                    return value / 100.0

                # Some wrappers may expose the integer as a string.
                if isinstance(value, str):
                    text = value.strip()
                    if not text:
                        return 0.0

                    if re.fullmatch(r"[+-]?\d+", text):
                        return int(text) / 100.0

                    return float(text)

                # A genuine float is already treated as rupees.
                return float(value)

            except (TypeError, ValueError):
                return 0.0

        def _number(value):
            try:
                return float(value or 0)
            except (TypeError, ValueError):
                return 0.0

        raw_ltp = msg.get("last_traded_price", 0)
        raw_close = msg.get("closed_price", 0)
        raw_open = msg.get("open_price_of_the_day", 0)
        raw_high = msg.get("high_price_of_the_day", 0)
        raw_low = msg.get("low_price_of_the_day", 0)

        # 52-week range supplied by Angel One FULL-mode market data.
        raw_52w_high = msg.get("52_week_high_price", 0)
        raw_52w_low = msg.get("52_week_low_price", 0)

        ltp = _price(raw_ltp)
        last_closed_price = _price(raw_close)
        open_price = _price(raw_open)
        day_high = _price(raw_high)
        day_low = _price(raw_low)
        week_52_high = _price(raw_52w_high)
        week_52_low = _price(raw_52w_low)

        current_volume = int(
            _number(
                msg.get(
                    "volume_trade_for_the_day",
                    msg.get("volume", 0)
                )
            )
        )

        # Angel One order-book fields.
        buying_volume = _number(
            msg.get("total_buy_quantity", 0)
        )
        selling_volume = _number(
            msg.get("total_sell_quantity", 0)
        )

        best_5_buy = msg.get("best_5_buy_data", []) or []
        best_5_sell = msg.get("best_5_sell_data", []) or []

        total_buy_orders = sum(
            int(_number(b.get("no of orders", 0)))
            for b in best_5_buy
            if isinstance(b, dict)
        )

        total_sell_orders = sum(
            int(_number(s.get("no of orders", 0)))
            for s in best_5_sell
            if isinstance(s, dict)
        )

        # Angel One FULL packets generally do not expose executed trade count.
        # Support it when present, but never substitute order-book counts.
        live_trade_count = 0.0
        for key in (
            "trade_count", "number_of_trades", "no_of_trades",
            "no.oftrades", "no_of_trade", "trades"
        ):
            raw_trade_count = msg.get(key)
            if raw_trade_count not in (None, ""):
                try:
                    live_trade_count = float(raw_trade_count)
                    break
                except (TypeError, ValueError):
                    pass

        # Use broker-reported values when available, otherwise retain
        # the values already observed for the day.
        if open_price > 0:
            cache["open_price"] = open_price

        if day_high > 0:
            cache["day_high"] = max(
                cache.get("day_high", 0.0),
                day_high
            )

        if day_low > 0:
            old_low = cache.get("day_low", 0.0)
            cache["day_low"] = (
                day_low
                if old_low <= 0
                else min(old_low, day_low)
            )

        if ltp > 0:
            cache["day_high"] = max(
                cache.get("day_high", 0.0),
                ltp
            )

            if cache.get("day_low", 0.0) <= 0:
                cache["day_low"] = ltp
            else:
                cache["day_low"] = min(
                    cache["day_low"],
                    ltp
                )

        day_high = cache.get("day_high", ltp)
        day_low = cache.get("day_low", ltp)

        # Previous tick state.
        prev_ltp = cache.get("prev_ltp", 0.0) or ltp
        prev_volume = cache.get(
            "prev_volume",
            current_volume
        )

        prev_buy_qty = cache.get(
            "prev_buy_qty",
            buying_volume
        )

        prev_sell_qty = cache.get(
            "prev_sell_qty",
            selling_volume
        )

        prev_buy_orders = cache.get(
            "prev_buy_orders",
            total_buy_orders
        )

        prev_sell_orders = cache.get(
            "prev_sell_orders",
            total_sell_orders
        )

        # Total order-book activity from the previous tick.
        # This is only used as order-book participation, NOT as executed trades.
        prev_total_orders = cache.get(
            "prev_total_orders",
            total_buy_orders + total_sell_orders
        )

        # Live comparisons.
        is_price_increasing = (
            ltp > prev_ltp
            if prev_ltp > 0
            else False
        )

        is_volume_growing = (
            current_volume > prev_volume
        )

        is_buy_qty_increasing = (
            buying_volume > prev_buy_qty
        )

        is_buy_orders_increasing = (
            total_buy_orders > prev_buy_orders
        )

        is_sell_orders_increasing = (
            total_sell_orders > prev_sell_orders
        )

        # Continuous upward price ticks.
        if is_price_increasing:
            cache["consecutive_price_ups"] = (
                cache.get("consecutive_price_ups", 0) + 1
            )
        else:
            cache["consecutive_price_ups"] = 0

        is_continuous_buy = (
            cache["consecutive_price_ups"] >= 2
        )

        total_order_qty = (
            buying_volume + selling_volume
        )

        buying_pressure = (
            buying_volume / total_order_qty
            if total_order_qty > 0
            else 0.5
        )

        # Current approximate traded value.
        # Angel One may provide average_traded_price_of_the_day.
        avg_traded_price = _price(
            msg.get(
                "average_traded_price",
                msg.get(
                    "average_traded_price_of_the_day",
                    0
                )
            )
        )

        if avg_traded_price <= 0:
            avg_traded_price = ltp

        current_turnover = (
            current_volume * avg_traded_price
        )

        # Intraday range position.
        close_position = (
            (ltp - day_low)
            / (day_high - day_low)
            * 100.0
            if day_high > day_low
            else 50.0
        )

        # Current gain from previous NSE close.
        gain_pct = (
            (ltp - last_closed_price)
            / last_closed_price
            * 100.0
            if last_closed_price > 0
            else 0.0
        )

        # Position inside the Angel One 52-week range.
        # 0% = 52W low, 100% = 52W high.
        # This is exposed to the BTST engine as context/filter data.
        position_52w_pct = (
            (ltp - week_52_low)
            / (week_52_high - week_52_low)
            * 100.0
            if week_52_high > week_52_low > 0
            else None
        )

        # Update state AFTER calculating comparisons.
        cache["prev_ltp"] = ltp
        cache["prev_volume"] = current_volume
        cache["prev_buy_qty"] = buying_volume
        cache["prev_sell_qty"] = selling_volume
        cache["prev_buy_orders"] = total_buy_orders
        cache["prev_sell_orders"] = total_sell_orders
        cache["prev_total_orders"] = total_buy_orders + total_sell_orders

        return {
            "token": token,
            "symbol": cache.get("symbol", ""),
            "last_closed_price": round(
                last_closed_price,
                2
            ),
            "ltp": round(ltp, 2),
            "open_price": round(open_price, 2),
            "day_high": round(day_high, 2),
            "day_low": round(day_low, 2),
            "52_week_high": round(week_52_high, 2),
            "52_week_low": round(week_52_low, 2),
            "position_52w_pct": (
                round(position_52w_pct, 2)
                if position_52w_pct is not None
                else None
            ),
            "current_volume": current_volume,
            "current_turnover": round(
                current_turnover,
                2
            ),
            "total_buy_orders": total_buy_orders,
            "total_sell_orders": total_sell_orders,
            "trade_count": live_trade_count,
            "prev_total_orders": prev_total_orders,
            "is_price_increasing": is_price_increasing,
            "is_volume_growing_up": is_volume_growing,
            "is_continuous_buy": is_continuous_buy,
            "buying_pressure": round(
                buying_pressure,
                4
            ),
            "selling_pressure": round(
                1.0 - buying_pressure,
                4
            ),
            "buying_volume": buying_volume,
            "selling_volume": selling_volume,
            "buying_quantity_increasing": is_buy_qty_increasing,
            "buying_orders_increasing": is_buy_orders_increasing,
            "selling_orders_increasing": is_sell_orders_increasing,
            "close_position": round(
                close_position,
                2
            ),
            "gain_pct": round(
                gain_pct,
                4
            ),
        }

