# data/instrument_master.py
import json
import requests
from pathlib import Path
from backend.config.logger import logger as log


class InstrumentMaster:

    INSTRUMENT_URL = (
        "https://margincalculator.angelbroking.com/"
        "OpenAPI_File/files/OpenAPIScripMaster.json"
    )

    CACHE_FILE = Path("data/instruments.json")

    def __init__(self):
        self.instruments = []
        self.load()

    def load(self):
        try:
            if self.CACHE_FILE.exists() and self.CACHE_FILE.stat().st_size == 0:
                log.warning("Empty instruments cache file detected. Deleting to force fresh download.")
                self.CACHE_FILE.unlink()

            if not self.CACHE_FILE.exists():
                self.refresh()
                return

            with open(self.CACHE_FILE, "r", encoding="utf-8") as fp:
                self.instruments = json.load(fp)

            log.info(f"Loaded {len(self.instruments)} instruments successfully.")

        except json.JSONDecodeError as je:
            log.error(f"Cache file corrupted. Forcing clean reload. Error: {je}")
            if self.CACHE_FILE.exists():
                self.CACHE_FILE.unlink()
            self.refresh()
            
        except Exception as ex:
            log.exception(f"Instrument master load failed critical failure: {ex}")
            raise

    def refresh(self):
        try:
            log.info("Downloading fresh instrument master from Angel One servers...")
            response = requests.get(self.INSTRUMENT_URL, timeout=30)
            response.raise_for_status()

            raw_text = response.text.strip()
            if not (raw_text.startswith("[") or raw_text.startswith("{")):
                raise ValueError("Angel One returned text/HTML instead of structural token JSON data.")

            self.CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            self.CACHE_FILE.write_text(raw_text, encoding="utf-8")

            self.instruments = json.loads(raw_text)
            log.info(f"Refreshed and loaded {len(self.instruments)} instruments successfully.")

        except Exception as ex:
            log.exception(f"Instrument download/refresh failed: {ex}")
            self.instruments = []

    # --- RE-IMPLEMENTING YOUR ORIGINAL LOOKUP METHODS ---[cite: 3]
    def get_token(self, symbol, exchange="NSE"):
        symbol = symbol.upper()
        for item in self.instruments:
            if (
                item.get("exch_seg") == exchange
                and item.get("symbol", "").startswith(symbol)
            ):
                return item.get("token")
        return None

    def get_instrument(self, symbol, exchange="NSE"):
        symbol = symbol.upper()
        for item in self.instruments:
            # Match segment (e.g., 'NSE') and check if the trading symbol starts with our string[cite: 3]
            if (
                item.get("exch_seg") == exchange
                and item.get("symbol", "").startswith(symbol)
            ):
                return item
        return None

    def search(self, keyword):
        keyword = keyword.upper()
        results = []
        for item in self.instruments:
            symbol = item.get("symbol", "").upper()
            if keyword in symbol:
                results.append(item)
        return results[:20]