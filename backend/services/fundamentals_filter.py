"""Daily Yahoo Finance fundamentals screen for NSE historical candidates."""

from concurrent.futures import ThreadPoolExecutor, as_completed
import math

import yfinance as yf

from backend.config.logger import logger as log


DEBT_TO_EQUITY_LIMIT = 1.0
YAHOO_DEBT_TO_EQUITY_PERCENT_DIVISOR = 100.0


def add_debt_to_equity_and_filter(stocks, max_workers=4):
    """Add debt_to_equity (as a ratio) to each stock and reject known ratios > 1.

    Yahoo's ``Ticker.info['debtToEquity']`` is expressed as a percentage (e.g.
    695.283 means 6.95283x), so it is normalized to a ratio for storage.
    If Yahoo has no value or the request fails, retain the candidate with a
    null value so a data-provider failure does not silently remove it.
    """
    if not stocks:
        return {}, {"passed": 0, "rejected": 0, "unknown": 0}

    def fetch_ratio(symbol):
        ticker_symbol = f"{symbol}.NS"
        try:
            raw_value = yf.Ticker(ticker_symbol).info.get("debtToEquity")
            if raw_value is None:
                return symbol, None, "missing"

            percentage = float(raw_value)
            if not math.isfinite(percentage) or percentage < 0:
                return symbol, None, "invalid"

            return (
                symbol,
                percentage / YAHOO_DEBT_TO_EQUITY_PERCENT_DIVISOR,
                "ok",
            )
        except Exception as exc:
            return symbol, None, f"error: {exc}"

    screened = {}
    rejected = 0
    unknown = 0

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_symbol = {
            executor.submit(fetch_ratio, symbol): symbol for symbol in stocks
        }

        for future in as_completed(future_to_symbol):
            symbol = future_to_symbol[future]
            try:
                returned_symbol, ratio, status = future.result()
            except Exception:
                returned_symbol, ratio, status = symbol, None, "error: worker failed"

            if status != "ok":
                unknown += 1
                log.warning(
                    "FUNDAMENTALS UNKNOWN | %s | debt_to_equity unavailable | %s",
                    returned_symbol,
                    status,
                )

            if ratio is not None and ratio > DEBT_TO_EQUITY_LIMIT:
                rejected += 1
                log.info(
                    "FUNDAMENTALS REJECT | %s | debt_to_equity=%.4fx | limit=%.2fx",
                    returned_symbol,
                    ratio,
                    DEBT_TO_EQUITY_LIMIT,
                )
                continue

            stock = dict(stocks[returned_symbol])
            stock["debt_to_equity"] = round(ratio, 6) if ratio is not None else None
            screened[returned_symbol] = stock

            if ratio is not None:
                log.info(
                    "FUNDAMENTALS PASS | %s | debt_to_equity=%.4fx | limit=%.2fx",
                    returned_symbol,
                    ratio,
                    DEBT_TO_EQUITY_LIMIT,
                )

    log.info(
        "FUNDAMENTALS FILTER SUMMARY | candidates=%d | passed=%d | rejected=%d | unknown_kept=%d",
        len(stocks),
        len(screened),
        rejected,
        unknown,
    )
    return screened, {
        "passed": len(screened) - unknown,
        "rejected": rejected,
        "unknown": unknown,
    }
