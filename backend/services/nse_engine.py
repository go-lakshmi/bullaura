# engine/scanner.py
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List, Optional
from zoneinfo import ZoneInfo
import io
import json
import queue
import re
import threading
import time

import numpy as np
import pandas as pd
import requests
from nselib import capital_market
from SmartApi.smartWebSocketV2 import SmartWebSocketV2

from backend.config.logger import logger as log
from backend.services.fundamentals_filter import add_debt_to_equity_and_filter
from backend.services.sector_strength import (
    normalize_index_symbol,
    sector_index_for,
    sector_rating_adjustment,
)


def _build_daily_indicator_seed(closes_oldest_first, min_history=50):
    """Build the previous-session EMA and Wilder RSI state for live updates."""
    closes = []
    for value in closes_oldest_first or []:
        try:
            close = float(value)
        except (TypeError, ValueError):
            continue
        if np.isfinite(close) and close > 0:
            closes.append(close)

    if len(closes) < min_history:
        return None

    alpha12 = 2.0 / 13.0
    alpha26 = 2.0 / 27.0
    alpha9 = 2.0 / 10.0
    ema12 = ema26 = closes[0]
    signal = 0.0
    for index, close in enumerate(closes):
        if index:
            ema12 += alpha12 * (close - ema12)
            ema26 += alpha26 * (close - ema26)
        macd = ema12 - ema26
        signal = macd if index == 0 else signal + alpha9 * (macd - signal)

    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    if len(deltas) < 14:
        return None
    average_gain = sum(max(delta, 0.0) for delta in deltas[:14]) / 14.0
    average_loss = sum(max(-delta, 0.0) for delta in deltas[:14]) / 14.0
    for delta in deltas[14:]:
        average_gain = (average_gain * 13.0 + max(delta, 0.0)) / 14.0
        average_loss = (average_loss * 13.0 + max(-delta, 0.0)) / 14.0

    return {
        "previous_close": closes[-1],
        "ema12": ema12,
        "ema26": ema26,
        "macd_signal": signal,
        "average_gain_14": average_gain,
        "average_loss_14": average_loss,
    }


def _provisional_daily_indicators(seed, live_ltp):
    """Calculate today's provisional daily MACD/RSI without compounding ticks."""
    if not seed or live_ltp <= 0:
        return {}

    change = live_ltp - seed["previous_close"]
    ema12 = seed["ema12"] + (2.0 / 13.0) * (live_ltp - seed["ema12"])
    ema26 = seed["ema26"] + (2.0 / 27.0) * (live_ltp - seed["ema26"])
    macd = ema12 - ema26
    signal = seed["macd_signal"] + (2.0 / 10.0) * (macd - seed["macd_signal"])

    average_gain = (seed["average_gain_14"] * 13.0 + max(change, 0.0)) / 14.0
    average_loss = (seed["average_loss_14"] * 13.0 + max(-change, 0.0)) / 14.0
    rsi = (
        50.0
        if average_gain == 0 and average_loss == 0
        else 100.0
        if average_loss == 0
        else 100.0 - 100.0 / (1.0 + average_gain / average_loss)
    )

    return {
        "daily_macd": round(macd, 4),
        "daily_macd_signal": round(signal, 4),
        "daily_macd_histogram": round(macd - signal, 4),
        "daily_rsi": round(rsi, 2),
    }


class NSEHighPerformanceTradingPipeline:

    def __init__(self, broker, instrument_master, config_path="data/trades_config.json", strategy_conf_path="data/conf.json", portfolio_tracker=None):
        self.broker = broker
        self.master = instrument_master
        self.config_path = Path(config_path)
        self.strategy_conf_path = Path(strategy_conf_path)
        self.bar_aggregators = {}
        self.portfolio_tracker = portfolio_tracker
        self.live_cache = {}
        self.tick_queue = queue.Queue()
        self.tick_stats_lock = threading.Lock()
        self.tick_stats = {
            "received": 0,
            "evaluated": 0,
            "skipped": 0,
            "failed": 0,
        }
        self.tick_stats_by_token = {}
        self.tick_unmapped_symbols = []
        self.sws = None
        self.active_stream_tokens = set()  
        self.settings = {}
        self.scrip_master_data = None
        self.volume_shockers = {}
        self.new_listing_candidates = {}
        self.sector_index_by_token = {}
        self.sector_index_live_data = {}
        self.sector_index_history_path = Path("data/sector_index_history.json")
        self.sector_index_history = self._load_sector_index_history()
        self._sector_history_refresh_started = False
        self.volume_profile_path = Path("data/intraday_volume_profiles.json")
        self.intraday_volume_profiles = self._load_intraday_volume_profiles()
        self._volume_profile_last_save = 0.0
        # UI bridge: populated from the same live calculations used by the scanner.
        # This is presentation state only; it does not alter qualification logic.
        self.ui_state = {}
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
        Download one year of NSE history and select candidates using the
        52-week position, liquidity, and recent 22-session price patterns.
        """

        cache_file = Path("data/volume_shockers_cache.json")
        today_str = time.strftime("%Y-%m-%d")

        # v11 adds separately streamed short-history candidates.
        cache_version = "nse_live_volume_shocker_v11"

        # ------------------------------------------------------------------
        # CACHE
        # ------------------------------------------------------------------

        if cache_file.exists():
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    cached_data = json.load(f)

                if (
                    cached_data.get("date") == today_str
                    and cached_data.get("version") == cache_version
                ):
                    self.volume_shockers = cached_data.get("data", {})
                    self.new_listing_candidates = cached_data.get("new_listings", {})

                    log.info(
                        "NSE CACHE LOADED | date=%s | version=%s | stocks=%d",
                        today_str,
                        cache_version,
                        len(self.volume_shockers),
                    )

                    return

                else:
                    log.info(
                        "NSE CACHE NOT USED | "
                        "cached_date=%s | cached_version=%s | "
                        "expected_date=%s | expected_version=%s",
                        cached_data.get("date"),
                        cached_data.get("version"),
                        today_str,
                        cache_version,
                    )

            except Exception as e:
                log.warning(
                    "NSE CACHE READ FAILED | error=%s | "
                    "will download fresh data",
                    e,
                )

        # ------------------------------------------------------------------
        # START
        # ------------------------------------------------------------------

        log.info(
            "NSE FILTER START | stocks_to_process=%d | cache_version=%s",
            len(symbols),
            cache_version,
        )

        print(
            f"Downloading 1-year historical data for "
            f"{len(symbols)} NSE stocks..."
        )

        # ------------------------------------------------------------------
        # FILTER COUNTERS
        # ------------------------------------------------------------------

        filter_counts = {
            "empty_data": 0,
            "less_than_22_rows": 0,
            "missing_required_columns": 0,
            "invalid_price_rows": 0,
            "less_than_20_trade_rows": 0,
            "low_average_trades": 0,
            "invalid_52week_range": 0,
            "low_52week_position": 0,
            "historical_pattern_failed": 0,
            "recent_cooling_failed": 0,
            "api_or_processing_error": 0,
            "passed": 0,
            "short_history_candidates": 0,
        }

        counter_lock = threading.Lock()
        new_listing_results = {}

        def increment_counter(name):
            with counter_lock:
                filter_counts[name] = filter_counts.get(name, 0) + 1

        # ------------------------------------------------------------------
        # SINGLE STOCK
        # ------------------------------------------------------------------

        def fetch_single_stock(symbol):

            try:
                time.sleep(0.3)

                # ----------------------------------------------------------
                # DOWNLOAD
                # ----------------------------------------------------------

                to_date = datetime.now()
                # Include a small calendar buffer for weekends and holidays.
                from_date = to_date - timedelta(days=370)

                df = capital_market.price_volume_data(
                    symbol=symbol,
                    from_date=from_date.strftime("%d-%m-%Y"),
                    to_date=to_date.strftime("%d-%m-%Y"),
                )

                if df is None or df.empty:

                    increment_counter("empty_data")

                    log.warning(
                        "NSE REJECT | %s | reason=EMPTY_NSE_DATA",
                        symbol,
                    )

                    return symbol, None

                df = df.copy()

                # ----------------------------------------------------------
                # DATE
                # ----------------------------------------------------------

                if "Date" in df.columns:

                    df["Date"] = pd.to_datetime(
                        df["Date"],
                        errors="coerce",
                        dayfirst=True,
                    )

                    df = df.dropna(
                        subset=["Date"]
                    )

                    df = df.sort_values(
                        "Date",
                        ascending=False,
                    )

                    # NSE may return today's still-forming session. Only
                    # completed sessions can participate in historical rules.
                    market_today = datetime.now(
                        ZoneInfo("Asia/Kolkata")
                    ).date()
                    year_ago = market_today - timedelta(days=365)
                    df = df[
                        (df["Date"].dt.date < market_today)
                        & (df["Date"].dt.date >= year_ago)
                    ].copy()

                # ----------------------------------------------------------
                # EQ SERIES
                # ----------------------------------------------------------

                if "Series" in df.columns:

                    df["Series"] = (
                        df["Series"]
                        .astype(str)
                        .str.strip()
                        .str.upper()
                    )

                    df = df[
                        df["Series"] == "EQ"
                    ].copy()

                # ----------------------------------------------------------
                # REQUIRED COLUMNS
                # ----------------------------------------------------------

                required_cols = [
                    "Date",
                    "PrevClose",
                    "HighPrice",
                    "LowPrice",
                    "ClosePrice",
                    "No.ofTrades",
                ]

                missing_cols = [
                    col
                    for col in required_cols
                    if col not in df.columns
                ]

                if missing_cols:

                    increment_counter(
                        "missing_required_columns"
                    )

                    log.warning(
                        "NSE REJECT | %s | "
                        "reason=MISSING_COLUMNS | columns=%s",
                        symbol,
                        missing_cols,
                    )

                    return symbol, None

                # ----------------------------------------------------------
                # NUMERIC CONVERSION
                # ----------------------------------------------------------

                numeric_columns = [
                    "PrevClose",
                    "OpenPrice",
                    "HighPrice",
                    "LowPrice",
                    "LastPrice",
                    "ClosePrice",
                    "AveragePrice",
                    "TotalTradedQuantity",
                    "Turnover₹",
                    "No.ofTrades",
                ]

                for col in numeric_columns:

                    if col in df.columns:

                        df[col] = (
                            df[col]
                            .astype(str)
                            .str.replace(
                                ",",
                                "",
                                regex=False,
                            )
                            .str.replace(
                                "₹",
                                "",
                                regex=False,
                            )
                            .str.strip()
                        )

                        df[col] = pd.to_numeric(
                            df[col],
                            errors="coerce",
                        )

                # ----------------------------------------------------------
                # VALID PRICE DATA
                # ----------------------------------------------------------

                df = df.dropna(
                    subset=[
                        "PrevClose",
                        "HighPrice",
                        "LowPrice",
                        "ClosePrice",
                    ]
                ).copy()

                if df.empty:

                    increment_counter(
                        "invalid_price_rows"
                    )

                    log.warning(
                        "NSE REJECT | %s | "
                        "reason=INVALID_PRICE_DATA_AFTER_CLEANUP | rows=%d",
                        symbol,
                        len(df),
                    )

                    return symbol, None

                # Keep valid histories with fewer than 22 completed EQ
                # sessions in a separate live-only collection. They bypass
                # the established-stock filters below, including liquidity,
                # 52-week position, historical pattern, cooling, and Yahoo
                # fundamentals. No-history or malformed rows are still rejected.
                if len(df) < 22:
                    increment_counter("less_than_22_rows")
                    short_history = df.copy()
                    valid_short_prices = (
                        (short_history["PrevClose"] > 0)
                        & (short_history["HighPrice"] > 0)
                        & (short_history["LowPrice"] > 0)
                        & (short_history["ClosePrice"] > 0)
                        & (short_history["HighPrice"] >= short_history["LowPrice"])
                    )
                    short_history = short_history.loc[valid_short_prices].copy()
                    if short_history.empty:
                        increment_counter("invalid_price_rows")
                        return symbol, None

                    short_ranges = (
                        (short_history["HighPrice"] - short_history["LowPrice"])
                        / short_history["LowPrice"].replace(0, np.nan)
                        * 100.0
                    ).dropna()
                    short_daily_moves = (
                        (short_history["ClosePrice"] - short_history["PrevClose"])
                        / short_history["PrevClose"].replace(0, np.nan)
                        * 100.0
                    ).dropna()
                    short_history["DailyMovePct"] = short_daily_moves.reindex(
                        short_history.index
                    ).round(4)
                    short_records = short_history.to_dict(orient="records")
                    for record in short_records:
                        for key, value in list(record.items()):
                            if isinstance(value, pd.Timestamp):
                                record[key] = value.strftime("%Y-%m-%d")
                            elif isinstance(value, (float, np.floating)) and not np.isfinite(value):
                                record[key] = None
                            elif isinstance(value, (np.integer,)):
                                record[key] = int(value)
                            elif isinstance(value, (np.floating,)):
                                record[key] = float(value)

                    with counter_lock:
                        new_listing_results[symbol] = {
                            "symbol": symbol,
                            "is_new_listing": True,
                            "historical_sessions": len(short_history),
                            "avg_trades_20d": round(
                                float(short_history["No.ofTrades"].dropna().mean()), 2
                            ) if short_history["No.ofTrades"].notna().any() else 0.0,
                            "avg_volume_20d": round(
                                float(short_history["TotalTradedQuantity"].dropna().mean()), 2
                            ) if "TotalTradedQuantity" in short_history and short_history["TotalTradedQuantity"].notna().any() else 0.0,
                            "avg_volume_5d": round(
                                float(short_history["TotalTradedQuantity"].head(5).dropna().mean()), 2
                            ) if "TotalTradedQuantity" in short_history and short_history["TotalTradedQuantity"].head(5).notna().any() else 0.0,
                            "avg_range_20d_pct": round(float(short_ranges.mean()), 4) if not short_ranges.empty else 0.0,
                            "avg_range_5d_pct": round(float(short_ranges.head(5).mean()), 4) if not short_ranges.empty else 0.0,
                            "last_5_avg_daily_move": round(float(short_daily_moves.head(5).mean()), 4) if not short_daily_moves.empty else 0.0,
                            "higher_low_structure": False,
                            "nse_data": short_records,
                            "indicator_closes_oldest_first": [],
                            "indicator_history_last_date": short_history.iloc[0]["Date"].strftime("%Y-%m-%d"),
                        }
                    increment_counter("short_history_candidates")
                    log.info(
                        "NSE SHORT HISTORY QUEUED | %s | sessions=%d | Angel One + New Stocks UI",
                        symbol,
                        len(short_history),
                    )
                    return symbol, None

                # Require a minimum average daily trade count so very
                # illiquid stocks are excluded from the candidate universe.
                trades_20d = df["No.ofTrades"].head(20).dropna()
                if len(trades_20d) < 20:
                    increment_counter("less_than_20_trade_rows")
                    log.info(
                        "NSE REJECT | %s | reason=LESS_THAN_20_TRADE_ROWS | rows=%d",
                        symbol,
                        len(trades_20d),
                    )
                    return symbol, None

                avg_trades_20d = float(trades_20d.mean())
                if avg_trades_20d < 2000:
                    increment_counter("low_average_trades")
                    log.info(
                        "NSE REJECT | %s | reason=LOW_AVERAGE_TRADES | "
                        "avg_trades_20d=%.2f | required>=2000",
                        symbol,
                        avg_trades_20d,
                    )
                    return symbol, None

                # ----------------------------------------------------------
                # 52-WEEK POSITION FILTER
                # ----------------------------------------------------------

                week_52_high = float(df["HighPrice"].max())
                week_52_low = float(df["LowPrice"].min())
                week_52_range = week_52_high - week_52_low
                latest_completed_close = float(df.iloc[0]["ClosePrice"])

                if week_52_range <= 0 or week_52_low <= 0:
                    increment_counter("invalid_52week_range")
                    log.info(
                        "NSE REJECT | %s | reason=INVALID_52_WEEK_RANGE | "
                        "high=%.2f | low=%.2f",
                        symbol,
                        week_52_high,
                        week_52_low,
                    )
                    return symbol, None

                position_52w_pct = (
                    (latest_completed_close - week_52_low)
                    / week_52_range
                    * 100.0
                )
                if position_52w_pct < 50.0:
                    increment_counter("low_52week_position")
                    log.info(
                        "NSE REJECT | %s | reason=52_WEEK_POSITION_BELOW_50%% | "
                        "position=%.2f%% | close=%.2f | low=%.2f | high=%.2f",
                        symbol,
                        position_52w_pct,
                        latest_completed_close,
                        week_52_low,
                        week_52_high,
                    )
                    return symbol, None

                # Preserve a longer close history for daily MACD/RSI while
                # keeping the existing 22-session filter payload unchanged.
                indicator_closes_oldest_first = [
                    float(value)
                    for value in df["ClosePrice"].head(100).tolist()
                    if pd.notna(value) and float(value) > 0
                ][::-1]
                indicator_history_last_date = df.iloc[0]["Date"].strftime("%Y-%m-%d")

                # Keep the existing short-term filters and NSE payload
                # limited to the latest 22 completed sessions.
                df = df.head(22).copy()

                daily_moves = (
                    (
                        df["ClosePrice"]
                        - df["PrevClose"]
                    )
                    / df["PrevClose"].replace(
                        0,
                        np.nan,
                    )
                    * 100.0
                )

                df["DailyMovePct"] = (
                    daily_moves.round(4)
                )

                last_5_moves = (
                    daily_moves
                    .head(5)
                    .dropna()
                )

                last_5_avg_move = float(
                    last_5_moves.mean()
                ) if not last_5_moves.empty else 0.0

                # ==========================================================
                # HISTORICAL BREAKOUT BASELINE
                # ==========================================================

                hist20 = df.head(20).copy()
                hist5 = df.head(5).copy()
                hist10 = df.head(10).copy()

                avg_volume_20d = (
                    float(
                        hist20[
                            "TotalTradedQuantity"
                        ].mean()
                    )
                    if "TotalTradedQuantity"
                    in hist20.columns
                    else 0.0
                )
                avg_volume_5d = (
                    float(hist5["TotalTradedQuantity"].mean())
                    if "TotalTradedQuantity" in hist5.columns
                    else 0.0
                )

                avg_turnover_20d = (
                    float(
                        hist20[
                            "Turnover₹"
                        ].mean()
                    )
                    if "Turnover₹"
                    in hist20.columns
                    else 0.0
                )

                historical_range_pct = (
                    (
                        df["HighPrice"]
                        - df["LowPrice"]
                    )
                    / df[
                        "LowPrice"
                    ].replace(
                        0,
                        np.nan,
                    )
                    * 100.0
                )

                avg_range_20d = (
                    float(
                        historical_range_pct
                        .head(20)
                        .dropna()
                        .mean()
                    )
                    if not historical_range_pct
                    .head(20)
                    .dropna()
                    .empty
                    else 0.0
                )

                # ----------------------------------------------------------
                # HISTORICAL PATTERN: falling from older highs or flat bands
                # M1=D0-D4, M2=D8-D12, M3=D13-D17, M4=D18-D21.
                # ----------------------------------------------------------

                window_ranges = {
                    "M1": df.iloc[0:5],
                    "M2": df.iloc[8:13],
                    "M3": df.iloc[13:18],
                    "M4": df.iloc[18:22],
                }
                window_highs = {
                    name: float(window["HighPrice"].max())
                    for name, window in window_ranges.items()
                }
                window_lows = {
                    name: float(window["LowPrice"].min())
                    for name, window in window_ranges.items()
                }

                m1_high = window_highs["M1"]
                m2_high = window_highs["M2"]
                m3_high = window_highs["M3"]
                m4_high = window_highs["M4"]
                highs = list(window_highs.values())
                lows = list(window_lows.values())
                high_spread_pct = (
                    (max(highs) - min(highs)) / min(highs) * 100.0
                    if min(highs) > 0 else float("inf")
                )
                low_spread_pct = (
                    (max(lows) - min(lows)) / min(lows) * 100.0
                    if min(lows) > 0 else float("inf")
                )
                fall_from_m4_pct = (
                    max(
                        (
                            (m4_high - float(df.iloc[day]["HighPrice"]))
                            / m4_high * 100.0
                            for day in range(4)
                        ),
                        default=0.0,
                    )
                    if m4_high > 0 else 0.0
                )
                fall_from_m3_pct = (
                    max(
                        (
                            (m3_high - float(df.iloc[day]["HighPrice"]))
                            / m3_high * 100.0
                            for day in range(4)
                        ),
                        default=0.0,
                    )
                    if m3_high > 0 else 0.0
                )
                fall_from_m4 = (
                    any(
                        (
                            (m4_high - float(df.iloc[day]["HighPrice"]))
                            / m4_high * 100.0
                        ) > 8.0
                        for day in range(4)
                    )
                    and m2_high < m4_high
                )
                fall_from_m3 = (
                    any(
                        (
                            (m3_high - float(df.iloc[day]["HighPrice"]))
                            / m3_high * 100.0
                        ) > 8.0
                        for day in range(4)
                    )
                    and m2_high < m3_high
                )
                high_flat = high_spread_pct <= 1.0
                low_flat = low_spread_pct <= 1.0
                historical_selected = (
                    fall_from_m4 or fall_from_m3 or high_flat or low_flat
                )
                historical_patterns = [
                    label for condition, label in (
                        (fall_from_m4, "FALL_FROM_M4"),
                        (fall_from_m3, "FALL_FROM_M3"),
                        (high_flat, "HIGH_SPREAD_FLAT"),
                        (low_flat, "LOW_SPREAD_FLAT"),
                    ) if condition
                ]

                cooling_threshold_pct = min(2.0, 0.8 * avg_range_20d)
                recent_day_ranges = (
                    historical_range_pct.head(3).tolist()
                )
                cooling_days = []
                for value in recent_day_ranges:
                    if pd.isna(value) or value > cooling_threshold_pct:
                        break
                    cooling_days.append(float(value))
                recent_cooling = bool(cooling_days)

                # TEMPORARY TEST: cooling is calculated and logged, but does
                # not reject a stock. Restore `or not recent_cooling` after
                # validating the NSE candidates.
                if not recent_cooling:
                    increment_counter("recent_cooling_failed")

                if not historical_selected:
                    increment_counter("historical_pattern_failed")
                    log.info(
                        "NSE REJECT | %s | reason=%s | patterns=%s | "
                        "fall_m4=%.2f%% | fall_m3=%.2f%% | "
                        "high_spread=%.2f%% | low_spread=%.2f%% | "
                        "cooling_threshold=%.2f%% | recent_ranges=%s",
                        symbol,
                        "HISTORICAL_PATTERN_FAILED",
                        historical_patterns,
                        fall_from_m4_pct,
                        fall_from_m3_pct,
                        high_spread_pct,
                        low_spread_pct,
                        cooling_threshold_pct,
                        [round(float(value), 3) for value in recent_day_ranges],
                    )
                    return symbol, None

                selected_pattern = "+".join(historical_patterns)

                avg_range_5d = (
                    float(
                        historical_range_pct
                        .head(5)
                        .dropna()
                        .mean()
                    )
                    if not historical_range_pct
                    .head(5)
                    .dropna()
                    .empty
                    else 0.0
                )

                # ----------------------------------------------------------
                # RECENT HIGHS / LOWS
                # ----------------------------------------------------------

                recent_5_high = float(
                    hist5["HighPrice"].max()
                )

                recent_10_high = float(
                    hist10["HighPrice"].max()
                )

                recent_20_high = float(
                    hist20["HighPrice"].max()
                )

                recent_5_low = float(
                    hist5["LowPrice"].min()
                )

                recent_10_low = float(
                    hist10["LowPrice"].min()
                )

                recent_20_low = float(
                    hist20["LowPrice"].min()
                )

                latest_close = float(
                    df.iloc[0]["ClosePrice"]
                )

                # ----------------------------------------------------------
                # CLOSE COMPRESSION
                # ----------------------------------------------------------

                close5 = (
                    hist5["ClosePrice"]
                    .dropna()
                )

                close_compression_pct = (
                    (
                        (
                            float(close5.max())
                            - float(close5.min())
                        )
                        / float(close5.min())
                        * 100.0
                    )
                    if (
                        len(close5) >= 2
                        and float(close5.min()) > 0
                    )
                    else 0.0
                )

                # ----------------------------------------------------------
                # HIGHER LOW
                # ----------------------------------------------------------

                low_series = (
                    hist5["LowPrice"]
                    .dropna()
                    .tolist()
                )

                higher_low_structure = False

                if len(low_series) >= 4:

                    recent_half_low = float(
                        min(low_series[:2])
                    )

                    older_half_low = float(
                        min(low_series[2:])
                    )

                    higher_low_structure = (
                        recent_half_low
                        >= older_half_low
                    )

                # ----------------------------------------------------------
                # DISTANCE FROM HIGH
                # ----------------------------------------------------------

                distance_from_10d_high_pct = (
                    (
                        recent_10_high
                        - latest_close
                    )
                    / recent_10_high
                    * 100.0
                    if recent_10_high > 0
                    else 0.0
                )

                distance_from_20d_high_pct = (
                    (
                        recent_20_high
                        - latest_close
                    )
                    / recent_20_high
                    * 100.0
                    if recent_20_high > 0
                    else 0.0
                )

                # ==========================================================
                # CONVERT RECORDS
                # ==========================================================

                records = df.to_dict(
                    orient="records"
                )

                for record in records:

                    for key, value in list(
                        record.items()
                    ):

                        if isinstance(
                            value,
                            pd.Timestamp,
                        ):
                            record[key] = (
                                value.strftime(
                                    "%Y-%m-%d"
                                )
                            )

                        elif isinstance(
                            value,
                            np.integer,
                        ):
                            record[key] = int(
                                value
                            )

                        elif isinstance(
                            value,
                            np.floating,
                        ):
                            record[key] = (
                                None
                                if np.isnan(value)
                                else float(value)
                            )

                # ==========================================================
                # PASSED
                # ==========================================================

                increment_counter("passed")

                log.info(
                    "NSE SELECTED | %s | "
                    "pattern=%s | "
                    "avg_trades_20d=%.2f | "
                    "last_5_avg_move=%.4f%% | "
                    "high_spread=%.2f%% | low_spread=%.2f%% | "
                    "fall_m4=%.2f%% | fall_m3=%.2f%% | "
                    "avg_range_20d=%.2f%% | cooling_threshold=%.2f%% | "
                    "recent_ranges=%s | 52w_position=%.2f%%",
                    symbol,
                    selected_pattern,
                    avg_trades_20d,
                    last_5_avg_move,
                    high_spread_pct,
                    low_spread_pct,
                    fall_from_m4_pct,
                    fall_from_m3_pct,
                    avg_range_20d,
                    cooling_threshold_pct,
                    [round(float(value), 3) for value in recent_day_ranges],
                    position_52w_pct,
                )

                return symbol, {

                    "symbol": symbol,

                    # Historical baseline.
                    "avg_trades_20d": round(
                        avg_trades_20d,
                        2,
                    ),

                    "52_week_high": round(week_52_high, 2),
                    "52_week_low": round(week_52_low, 2),
                    "position_52w_pct": round(position_52w_pct, 2),

                    "avg_volume_20d": round(
                        avg_volume_20d,
                        2,
                    ),

                    "avg_volume_5d": round(avg_volume_5d, 2),

                    "avg_turnover_20d": round(
                        avg_turnover_20d,
                        2,
                    ),

                    "avg_range_20d_pct": round(
                        avg_range_20d,
                        4,
                    ),

                    "avg_range_5d_pct": round(
                        avg_range_5d,
                        4,
                    ),

                    # Historical structure.
                    "recent_5_high": round(
                        recent_5_high,
                        2,
                    ),

                    "recent_10_high": round(
                        recent_10_high,
                        2,
                    ),

                    "recent_20_high": round(
                        recent_20_high,
                        2,
                    ),

                    "recent_5_low": round(
                        recent_5_low,
                        2,
                    ),

                    "recent_10_low": round(
                        recent_10_low,
                        2,
                    ),

                    "recent_20_low": round(
                        recent_20_low,
                        2,
                    ),

                    "close_compression_5d_pct": round(
                        close_compression_pct,
                        4,
                    ),

                    "higher_low_structure":
                        higher_low_structure,

                    "distance_from_10d_high_pct":
                        round(
                            distance_from_10d_high_pct,
                            4,
                        ),

                    "distance_from_20d_high_pct":
                        round(
                            distance_from_20d_high_pct,
                            4,
                        ),

                    # Existing filter result.
                    "last_5_avg_daily_move":
                        round(
                            last_5_avg_move,
                            4,
                        ),

                    "price_structure_filter":
                        selected_pattern,

                    "historical_selected": historical_selected,
                    "fall_from_m4": fall_from_m4,
                    "fall_from_m3": fall_from_m3,
                    "fall_from_m4_pct": round(fall_from_m4_pct, 4),
                    "fall_from_m3_pct": round(fall_from_m3_pct, 4),
                    "high_spread_pct": round(high_spread_pct, 4),
                    "low_spread_pct": round(low_spread_pct, 4),
                    "recent_cooling": recent_cooling,
                    "cooling_threshold_pct": round(
                        cooling_threshold_pct, 4
                    ),
                    "recent_day_ranges_pct": [
                        round(float(value), 4)
                        for value in recent_day_ranges
                    ],

                    # ALL 22 NSE records.
                    "nse_data": records,
                    "indicator_closes_oldest_first": indicator_closes_oldest_first,
                    "indicator_history_last_date": indicator_history_last_date,
                }

            except Exception as ex:

                increment_counter(
                    "api_or_processing_error"
                )

                log.exception(
                    "NSE ERROR | %s | "
                    "reason=API_OR_PROCESSING_ERROR",
                    symbol,
                )

                return symbol, None

        # ==============================================================
        # PROCESS ALL STOCKS
        # ==============================================================

        results = {}

        with ThreadPoolExecutor(
            max_workers=3
        ) as executor:

            future_to_symbol = {
                executor.submit(
                    fetch_single_stock,
                    sym,
                ): sym
                for sym in symbols
            }

            completed = 0

            for future in as_completed(
                future_to_symbol
            ):

                symbol = future_to_symbol[
                    future
                ]

                try:

                    returned_symbol, result = (
                        future.result(
                            timeout=15
                        )
                    )

                    if result:
                        results[
                            returned_symbol
                        ] = result

                except Exception as thread_err:

                    increment_counter(
                        "api_or_processing_error"
                    )

                    log.exception(
                        "NSE THREAD ERROR | %s | error=%s",
                        symbol,
                        thread_err,
                    )

                completed += 1

                if completed % 50 == 0:

                    log.info(
                        "NSE FILTER PROGRESS | "
                        "%d/%d processed | selected=%d",
                        completed,
                        len(symbols),
                        len(results),
                    )

                    print(
                        f"Progress: "
                        f"{completed}/"
                        f"{len(symbols)} "
                        f"stocks processed..."
                    )

                time.sleep(0.1)

        # ==============================================================
        # DAILY FUNDAMENTALS SCREEN
        # ==============================================================

        results, fundamentals_counts = add_debt_to_equity_and_filter(results)

        log.info(
            "FUNDAMENTALS FILTER COMPLETE | passed=%d | rejected=%d | unknown_kept=%d",
            fundamentals_counts["passed"],
            fundamentals_counts["rejected"],
            fundamentals_counts["unknown"],
        )

        # STORE RESULTS
        # ============================================================== 

        self.volume_shockers = results
        self.new_listing_candidates = new_listing_results

        # ==============================================================
        # FINAL FILTER SUMMARY
        # ==============================================================

        log.info(
            "============================================================"
        )

        log.info(
            "NSE FILTER SUMMARY"
        )

        log.info(
            "Total stocks processed       : %d",
            len(symbols),
        )

        log.info(
            "Empty NSE data               : %d",
            filter_counts["empty_data"],
        )

        log.info(
            "Less than 22 sessions        : %d",
            filter_counts["less_than_22_rows"],
        )
        log.info(
            "Short-history candidates     : %d",
            filter_counts["short_history_candidates"],
        )

        log.info(
            "Missing required columns     : %d",
            filter_counts["missing_required_columns"],
        )

        log.info(
            "Invalid price data           : %d",
            filter_counts["invalid_price_rows"],
        )

        log.info(
            "Less than 20 trade rows      : %d",
            filter_counts["less_than_20_trade_rows"],
        )

        log.info(
            "Average trades < 2000        : %d",
            filter_counts["low_average_trades"],
        )

        log.info(
            "Invalid 52-week range        : %d",
            filter_counts["invalid_52week_range"],
        )

        log.info(
            "52-week position < 50%%       : %d",
            filter_counts["low_52week_position"],
        )

        log.info(
            "Historical pattern failed   : %d",
            filter_counts["historical_pattern_failed"],
        )

        log.info(
            "Recent cooling failed       : %d",
            filter_counts["recent_cooling_failed"],
        )

        log.info(
            "API/processing errors        : %d",
            filter_counts["api_or_processing_error"],
        )

        log.info(
            "NSE FILTER PASSED             : %d",
            filter_counts["passed"],
        )

        log.info(
            "============================================================"
        )

        print(
            f"\nNSE filters passed: "
            f"{len(results)} / {len(symbols)} stocks."
        )

        # ==============================================================
        # SAVE CACHE
        # ==============================================================

        try:

            cache_file.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            with open(
                cache_file,
                "w",
                encoding="utf-8",
            ) as f:

                json.dump(
                    {
                        "date": today_str,
                        "version": cache_version,
                        "data": self.volume_shockers,
                        "new_listings": self.new_listing_candidates,
                    },
                    f,
                    indent=2,
                    default=str,
                )

            log.info(
                "NSE CACHE SAVED | "
                "date=%s | version=%s | stocks=%d | file=%s",
                today_str,
                cache_version,
                len(self.volume_shockers),
                cache_file,
            )

            print(
                "NSE filtered data cached successfully."
            )

        except Exception as e:

            log.exception(
                "NSE CACHE SAVE FAILED | error=%s",
                e,
            )

            print(
                f"Failed to save cache: {e}"
            )
    

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
            token = str(message.get("token", "")) if isinstance(message, dict) else ""
            received_at = time.time()
            with self.tick_stats_lock:
                self.tick_stats["received"] += 1
                stock_stats = self.tick_stats_by_token.setdefault(
                    token or "unknown",
                    {
                        "symbol": self.live_cache.get(token, {}).get("symbol", ""),
                        "received": 0,
                        "evaluated": 0,
                        "skipped": 0,
                        "failed": 0,
                        "last_received_at": None,
                        "last_processed_at": None,
                        "last_status": "awaiting_processing",
                        "last_skip_reason": "",
                    },
                )
                stock_stats["received"] += 1
                stock_stats["last_received_at"] = received_at
                stock_stats["symbol"] = self.live_cache.get(token, {}).get(
                    "symbol", stock_stats.get("symbol", "")
                )
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
        """Subscribe to selected NSE stocks and only their needed index feeds."""
        target_tokens = []
        self.tick_unmapped_symbols = []

        # Stream regular and short-history candidates through the same socket.
        all_candidates = dict(self.volume_shockers)
        all_candidates.update(self.new_listing_candidates)
        candidate_symbols = {
            sym.replace(".NS", "").strip() for sym in self.volume_shockers.keys()
        }
        candidate_symbols.update(
            sym.replace(".NS", "").strip() for sym in self.new_listing_candidates.keys()
        )

        if not candidate_symbols:
            log.warning("No regular or short-history NSE candidates found to subscribe.")
            return

        for item in self.scrip_master_data:
            inst = item.get("raw_instrument", item) if isinstance(item, dict) else item
            if isinstance(inst, dict) and inst.get("exch_seg") == "NSE":
                symbol_root = inst.get("symbol", "").split("-")[0].strip()
                if symbol_root in candidate_symbols:
                    token = str(inst.get("token")).strip()
                    if token not in target_tokens:
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
                    with self.tick_stats_lock:
                        self.tick_stats_by_token.setdefault(
                            token,
                            {
                                "symbol": symbol_root,
                                "received": 0,
                                "evaluated": 0,
                                "skipped": 0,
                                "failed": 0,
                                "last_received_at": None,
                                "last_processed_at": None,
                                "last_status": "awaiting_tick",
                                "last_skip_reason": "",
                                "subscription_requested": False,
                            },
                        )

        mapped_symbols = {
            self.live_cache[token].get("symbol", "")
            for token in target_tokens
            if token in self.live_cache
        }
        self.tick_unmapped_symbols = sorted(candidate_symbols - mapped_symbols)
        if self.tick_unmapped_symbols:
            log.warning(
                "NSE candidates without Angel One NSE tokens | count=%d | symbols=%s",
                len(self.tick_unmapped_symbols),
                ",".join(self.tick_unmapped_symbols),
            )

        required_index_names = {"Nifty 50"}
        for symbol in candidate_symbols:
            stock = all_candidates.get(symbol, {}) or {}
            sector_name = stock.get("sector_index_symbol") or sector_index_for(
                stock.get("company_sector"), stock.get("company_industry")
            )
            if sector_name:
                required_index_names.add(sector_name)

        found_indexes = {}
        for item in self.scrip_master_data or []:
            inst = item.get("raw_instrument", item) if isinstance(item, dict) else item
            if not isinstance(inst, dict):
                continue
            if inst.get("exch_seg") != "NSE" or inst.get("instrumenttype") != "AMXIDX":
                continue
            index_key = normalize_index_symbol(inst.get("symbol"))
            for wanted in required_index_names:
                if index_key == normalize_index_symbol(wanted):
                    token = str(inst.get("token", "")).strip()
                    if not token:
                        continue
                    found_indexes[wanted] = token
                    self.sector_index_by_token[token] = wanted
                    if token not in target_tokens:
                        target_tokens.append(token)
                    index_label = wanted
                    if token not in self.live_cache:
                        self.live_cache[token] = {
                            "symbol": index_label,
                            "ticks": [],
                            "live_samples": deque(maxlen=1800),
                        }
                    with self.tick_stats_lock:
                        self.tick_stats_by_token.setdefault(
                            token,
                            {
                                "symbol": index_label,
                                "received": 0,
                                "evaluated": 0,
                                "skipped": 0,
                                "failed": 0,
                                "last_received_at": None,
                                "last_processed_at": None,
                                "last_status": "awaiting_tick",
                                "last_skip_reason": "",
                                "subscription_requested": False,
                            },
                        )

        missing_indexes = sorted(required_index_names - set(found_indexes))
        if missing_indexes:
            log.warning(
                "SECTOR INDEX TOKEN MISSING | count=%d | indexes=%s",
                len(missing_indexes),
                ",".join(missing_indexes),
            )
        if not self._sector_history_refresh_started:
            self._sector_history_refresh_started = True
            threading.Thread(
                target=self._refresh_sector_index_history,
                name="sector-index-history-refresh",
                daemon=True,
            ).start()

        stock_tokens = [token for token in target_tokens if token not in self.sector_index_by_token]
        index_tokens = [token for token in target_tokens if token in self.sector_index_by_token]

        # 2) Stocks use FULL mode for their depth metrics; index feeds only
        # need quote mode for LTP and previous-close comparisons.
        if target_tokens and self.sws and self.sws.wsapp and self.sws.wsapp.sock and self.sws.wsapp.sock.connected:
            if stock_tokens:
                self.sws.subscribe(
                    correlation_id="volume_shockers_sync",
                    mode=3,
                    token_list=[{"exchangeType": 1, "tokens": stock_tokens}],
                )
            if index_tokens:
                self.sws.subscribe(
                    correlation_id="sector_indexes_sync",
                    mode=2,
                    token_list=[{"exchangeType": 1, "tokens": index_tokens}],
                )
            self.active_stream_tokens.update(target_tokens)
            with self.tick_stats_lock:
                for token in target_tokens:
                    if token in self.tick_stats_by_token:
                        self.tick_stats_by_token[token]["subscription_requested"] = True
            log.info(
                "Angel One subscriptions requested | stocks=%d | sector_indexes=%d | total_tokens=%d",
                len(mapped_symbols),
                len(found_indexes),
                len(target_tokens),
            )


    def run_continuous_funnel_loop(self):
        """Evaluate each subscribed tick and publish the current state to the web UI."""
        log.info("Unified live stock rating loop online.")

        while True:
            try:
                # Drain queued ticks in batches so every subscribed symbol is
                # evaluated promptly without waiting for a periodic dispatch.
                pending_messages = [self.tick_queue.get(timeout=1)]
                while True:
                    try:
                        pending_messages.append(self.tick_queue.get_nowait())
                    except queue.Empty:
                        break

                for msg in pending_messages:
                    try:
                        token = str(msg.get("token", ""))
                        if token in self.sector_index_by_token:
                            index_name = self.sector_index_by_token[token]
                            index_metrics = self.analyze_tick_metrics(token, msg)
                            self.live_cache[token]["symbol"] = index_name
                            index_previous_close = index_metrics.get("last_closed_price", 0)
                            self.sector_index_live_data[index_name] = {
                                "daily_change_pct": (
                                    index_metrics.get("gain_pct")
                                    if index_previous_close and index_previous_close > 0
                                    else None
                                ),
                                "ltp": index_metrics.get("ltp"),
                                "previous_close": index_previous_close,
                                "updated_at": time.time(),
                            }
                            self._record_tick_status("evaluated", token)
                            continue

                        cache = self.live_cache.get(token)
                        if not cache:
                            self._record_tick_status("skipped", token, "no_live_cache")
                            continue

                        symbol = cache.get("symbol", "")
                        cs = self.analyze_tick_metrics(token, msg)
                        if cs.get("ltp", 0) <= 0:
                            self._record_tick_status("skipped", token, "invalid_ltp")
                            continue

                        past_data = (
                            self.volume_shockers.get(symbol)
                            or self.new_listing_candidates.get(symbol)
                        )
                        if not past_data:
                            self._record_tick_status("skipped", token, "no_historical_data")
                            continue

                        signal = self.evaluate_unified_candidate(symbol, past_data, cs)
                        self._update_ui_state(
                            symbol, token, cs, signal, past_data
                        )
                        self._record_tick_status("evaluated", token)
                    except Exception:
                        self._record_tick_status("failed", token, "processing_exception")
                        log.exception(
                            "Failed to process one Angel One tick; continuing batch | token=%s",
                            (msg.get("token") if isinstance(msg, dict) else "unknown"),
                        )

            except queue.Empty:
                log.debug("No ticker received within the 1-second queue wait.")
            except Exception:
                log.exception("Error inside run_continuous_funnel_loop")

    def _record_tick_status(self, status, token, detail=""):
        """Count received-tick outcomes and periodically log processing coverage."""
        with self.tick_stats_lock:
            if status not in ("evaluated", "skipped", "failed"):
                return
            self.tick_stats[status] += 1
            stock_stats = self.tick_stats_by_token.setdefault(
                str(token or "unknown"),
                {
                    "symbol": self.live_cache.get(str(token), {}).get("symbol", ""),
                    "received": 0,
                    "evaluated": 0,
                    "skipped": 0,
                    "failed": 0,
                    "last_received_at": None,
                    "last_processed_at": None,
                    "last_status": "awaiting_tick",
                    "last_skip_reason": "",
                },
            )
            stock_stats[status] += 1
            stock_stats["last_processed_at"] = time.time()
            stock_stats["last_status"] = status
            stock_stats["last_skip_reason"] = detail if status == "skipped" else ""
            handled = (
                self.tick_stats["evaluated"]
                + self.tick_stats["skipped"]
                + self.tick_stats["failed"]
            )
            if handled % 1000 != 0:
                return
            stats = dict(self.tick_stats)

        log.info(
            "Angel One tick processing | received=%d | evaluated=%d | skipped=%d | failed=%d | queued=%d",
            stats["received"],
            stats["evaluated"],
            stats["skipped"],
            stats["failed"],
            self.tick_queue.qsize(),
        )

    def get_tick_processing_stats(self):
        """Return aggregate and per-token received/processed tick counts."""
        with self.tick_stats_lock:
            aggregate = dict(self.tick_stats)
            stocks = {
                token: {
                    **details,
                    "queued": max(
                        0,
                        details["received"]
                        - details["evaluated"]
                        - details["skipped"]
                        - details["failed"],
                    ),
                }
                for token, details in self.tick_stats_by_token.items()
            }
        subscribed_tokens = [
            token for token, details in stocks.items()
            if details.get("subscription_requested")
            and token not in self.sector_index_by_token
        ]
        subscribed_index_tokens = [
            token for token, details in stocks.items()
            if details.get("subscription_requested")
            and token in self.sector_index_by_token
        ]
        subscribed_symbols = sorted({
            stocks[token].get("symbol", "") for token in subscribed_tokens
            if stocks[token].get("symbol")
        })

        return {
            "aggregate": aggregate,
            "queue_depth": self.tick_queue.qsize(),
            "historical_candidate_count": len(self.volume_shockers),
            "subscription_requested_token_count": len(subscribed_tokens),
            "subscription_requested_symbol_count": len(subscribed_symbols),
            "sector_index_subscription_requested_count": len(subscribed_index_tokens),
            "symbols_without_token": list(self.tick_unmapped_symbols),
            "stocks": {
                token: details for token, details in stocks.items()
                if token not in self.sector_index_by_token
            },
            "sector_indexes": {
                token: details for token, details in stocks.items()
                if token in self.sector_index_by_token
            },
        }

    def _update_ui_state(self, symbol, token, cs, signal, past_data):
        """Publish the unified rating and status for the web dashboard."""
        cache = self.live_cache.get(str(token), {})
        cache["ui_unified_signal"] = dict(signal) if signal else {}
        cache["ui_last_cs"] = dict(cs)
        cache["ui_updated_at"] = time.time()

        # Keep a compact per-symbol reference as well.
        self.ui_state[str(symbol)] = {
            "token": str(token),
            "symbol": symbol,
            "unified_signal": dict(cache.get("ui_unified_signal") or {}),
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

    def _load_intraday_volume_profiles(self):
        """Load persisted per-minute cumulative volume profiles by symbol/date."""
        try:
            with self.volume_profile_path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
            return payload if isinstance(payload, dict) else {}
        except FileNotFoundError:
            return {}
        except Exception:
            log.exception("Could not load saved intraday volume profiles")
            return {}

    def _load_sector_index_history(self):
        """Load the latest once-daily sector-index candles used for scoring."""
        try:
            with self.sector_index_history_path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
            return payload if isinstance(payload, dict) else {}
        except FileNotFoundError:
            return {}
        except Exception:
            log.exception("Could not load cached sector-index history")
            return {}

    def _refresh_sector_index_history(self):
        """Fetch index candles once per session; a failure leaves scoring neutral."""
        today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
        today_text = today.isoformat()
        index_names = set(self.sector_index_by_token.values())
        index_names.add("Nifty 50")
        api = getattr(self.broker, "smart_api", None)
        if api is None or not hasattr(api, "getCandleData"):
            log.warning("SECTOR HISTORY UNAVAILABLE | Angel One candle API is missing")
            return

        index_instruments = {}
        for wrapper in self.scrip_master_data or []:
            instrument = wrapper.get("raw_instrument", wrapper) if isinstance(wrapper, dict) else wrapper
            if (
                isinstance(instrument, dict)
                and instrument.get("exch_seg") == "NSE"
                and instrument.get("instrumenttype") == "AMXIDX"
            ):
                index_instruments[normalize_index_symbol(instrument.get("symbol"))] = instrument

        changed = False
        for index_name in sorted(index_names):
            cached = self.sector_index_history.get(index_name) or {}
            if cached.get("fetched_on") == today_text and cached.get("five_day_change_pct") is not None:
                continue

            instrument = index_instruments.get(normalize_index_symbol(index_name))
            if not instrument:
                continue

            end = datetime.now(ZoneInfo("Asia/Kolkata"))
            start = end - timedelta(days=18)
            params = {
                "exchange": "NSE",
                "symboltoken": str(instrument.get("token", "")),
                "interval": "ONE_DAY",
                "fromdate": start.strftime("%Y-%m-%d %H:%M"),
                "todate": end.strftime("%Y-%m-%d %H:%M"),
            }
            try:
                response = api.getCandleData(params)
                candle_rows = response.get("data", []) if isinstance(response, dict) and response.get("status") else []
                completed = []
                for row in candle_rows or []:
                    if not isinstance(row, (list, tuple)) or len(row) < 5:
                        continue
                    candle_date = str(row[0])[:10]
                    close = float(row[4] or 0)
                    if candle_date < today_text and close > 0:
                        completed.append((candle_date, close))
                completed.sort(key=lambda entry: entry[0])
                completed = completed[-6:]
                five_day_change = None
                if len(completed) == 6 and completed[0][1] > 0:
                    five_day_change = (completed[-1][1] / completed[0][1] - 1.0) * 100.0
                self.sector_index_history[index_name] = {
                    "fetched_on": today_text,
                    "dates": [entry[0] for entry in completed],
                    "closes": [entry[1] for entry in completed],
                    "five_day_change_pct": round(five_day_change, 3) if five_day_change is not None else None,
                }
                changed = True
            except Exception as exc:
                error_text = str(exc).lower()
                if "rate" in error_text or "exceeding access" in error_text:
                    log.warning(
                        "SECTOR HISTORY RATE LIMITED | index=%s | "
                        "stopping remaining candle requests for this refresh",
                        index_name,
                    )
                    break
                log.exception("SECTOR HISTORY FETCH FAILED | index=%s", index_name)
            # Angel One's historical candle endpoint is rate limited. Keep
            # index-history requests well spaced; this runs only once/session.
            time.sleep(1.5)

        if changed:
            try:
                self.sector_index_history_path.parent.mkdir(parents=True, exist_ok=True)
                temporary_path = self.sector_index_history_path.with_suffix(".tmp")
                with temporary_path.open("w", encoding="utf-8") as handle:
                    json.dump(self.sector_index_history, handle, separators=(",", ":"))
                temporary_path.replace(self.sector_index_history_path)
            except Exception:
                log.exception("Could not save sector-index history cache")

    def _record_intraday_volume_profile(self, symbol, session_date, minute_index, volume):
        """Keep one cumulative-volume observation per regular-session minute."""
        if not symbol or minute_index < 0 or minute_index >= 375:
            return

        symbol_profiles = self.intraday_volume_profiles.setdefault(symbol, {})
        profile = symbol_profiles.setdefault(session_date, [0] * 375)
        if len(profile) < 375:
            profile.extend([profile[-1] if profile else 0] * (375 - len(profile)))

        previous_index = minute_index - 1
        while previous_index >= 0 and profile[previous_index] <= 0:
            previous_index -= 1
        prior_volume = profile[previous_index] if previous_index >= 0 else 0
        cumulative = max(int(volume or 0), int(prior_volume or 0))
        for index in range(previous_index + 1, minute_index):
            profile[index] = prior_volume
        profile[minute_index] = cumulative

        for old_date in sorted(symbol_profiles)[:-8]:
            symbol_profiles.pop(old_date, None)

        now = time.time()
        if now - self._volume_profile_last_save < 30:
            return

        try:
            self.volume_profile_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = self.volume_profile_path.with_suffix(".tmp")
            with temp_path.open("w", encoding="utf-8") as handle:
                json.dump(self.intraday_volume_profiles, handle, separators=(",", ":"))
            temp_path.replace(self.volume_profile_path)
            self._volume_profile_last_save = now
        except Exception:
            log.exception("Could not persist intraday volume profiles")

    def _volume_profile_ratio(self, symbol, session_date, minute_index, current_volume, avg_volume_5d):
        """Compare today's cumulative volume with prior sessions at this minute."""
        symbol_profiles = self.intraday_volume_profiles.get(symbol, {})
        prior_dates = [day for day in sorted(symbol_profiles) if day < session_date][-5:]
        baseline_values = [
            int(symbol_profiles[day][minute_index])
            for day in prior_dates
            if minute_index < len(symbol_profiles[day])
            and int(symbol_profiles[day][minute_index]) > 0
        ]

        if baseline_values:
            ordered = sorted(baseline_values)
            middle = len(ordered) // 2
            baseline = (
                ordered[middle]
                if len(ordered) % 2
                else (ordered[middle - 1] + ordered[middle]) / 2.0
            )
            ratio = current_volume / baseline if baseline > 0 else 0.0
            source = "same_time_profile" if len(baseline_values) >= 5 else "partial_time_profile"
            return ratio, baseline, len(baseline_values), source

        elapsed_fraction = max(1.0 / 375.0, min(1.0, (minute_index + 1) / 375.0))
        estimated_baseline = max(0.0, avg_volume_5d * elapsed_fraction)
        ratio = current_volume / estimated_baseline if estimated_baseline > 0 else 0.0
        return ratio, estimated_baseline, 0, "linear_daily_estimate"

    def evaluate_unified_candidate(self, symbol, past_data, cs):
        """Continuously score a live NSE candidate and assign its current state."""
        ltp = float(cs.get("ltp", 0) or 0)
        previous_close = float(cs.get("last_closed_price", 0) or 0)
        token = str(cs.get("token", ""))
        cache = self.live_cache.get(token)
        if ltp <= 0 or previous_close <= 0 or cache is None:
            return None

        now = time.time()
        symbol = str(symbol or "").strip()
        gain_pct = (ltp - previous_close) / previous_close * 100.0
        current_volume = int(float(cs.get("current_volume", 0) or 0))
        buying_pressure = max(0.0, min(1.0, float(cs.get("buying_pressure", 0.5) or 0.0)))
        day_high = float(cs.get("day_high", ltp) or ltp)
        day_low = float(cs.get("day_low", ltp) or ltp)

        samples = cache.setdefault("live_samples", deque(maxlen=1800))
        samples.append({
            "ts": now,
            "ltp": ltp,
            "volume": current_volume,
            "buy_pressure": buying_pressure,
        })

        def window(seconds):
            cutoff = now - seconds
            return [sample for sample in samples if sample.get("ts", 0) >= cutoff]

        def change_pct(window_samples):
            if len(window_samples) < 2 or float(window_samples[0].get("ltp", 0) or 0) <= 0:
                return 0.0
            first = float(window_samples[0]["ltp"])
            return (ltp - first) / first * 100.0

        w5 = window(300)
        w15 = window(900)
        w30 = window(1800)
        change_5m = change_pct(w5)
        change_15m = change_pct(w15)

        # Compare the most recent 15 minutes with the preceding 15 minutes to
        # detect fresh lows and higher lows without treating every tick as a bar.
        prior_15 = [sample for sample in w30 if sample.get("ts", 0) < now - 900]
        recent_15 = [sample for sample in w30 if sample.get("ts", 0) >= now - 900]
        prior_low = min((float(x.get("ltp", ltp)) for x in prior_15), default=0.0)
        recent_low = min((float(x.get("ltp", ltp)) for x in recent_15), default=0.0)
        enough_low_history = len(prior_15) >= 2 and len(recent_15) >= 2 and prior_low > 0
        no_new_low = (recent_low >= prior_low * 0.998) if enough_low_history else None
        higher_low = (recent_low > prior_low * 1.001) if enough_low_history else False

        avg_range_20 = max(0.0, float(past_data.get("avg_range_20d_pct", 0) or 0))
        recent_range_30 = 0.0
        if len(w30) >= 3:
            prices = [float(x.get("ltp", 0) or 0) for x in w30 if float(x.get("ltp", 0) or 0) > 0]
            if prices:
                recent_range_30 = (max(prices) - min(prices)) / previous_close * 100.0

        # Historical trend and settling are continuous score components.
        records = past_data.get("nse_data") or []
        rows = []
        for row in records:
            try:
                close = float(row.get("ClosePrice", 0) or 0)
                high = float(row.get("HighPrice", 0) or 0)
                low = float(row.get("LowPrice", 0) or 0)
                if close > 0:
                    rows.append({"close": close, "high": high, "low": low})
            except (TypeError, ValueError):
                continue

        indicator_closes = past_data.get("indicator_closes_oldest_first")
        if not indicator_closes:
            # Older on-disk caches may not have the extended indicator history.
            indicator_closes = [row["close"] for row in reversed(rows)]
        indicator_history_date = str(
            past_data.get("indicator_history_last_date")
            or ((records[0].get("Date") if records else "") or "")
        )[:10]
        indicator_seed_cache = cache.get("daily_indicator_seed") or {}
        if indicator_seed_cache.get("history_date") != indicator_history_date:
            indicator_seed_cache = {
                "history_date": indicator_history_date,
                "state": _build_daily_indicator_seed(indicator_closes),
            }
            cache["daily_indicator_seed"] = indicator_seed_cache
        daily_indicators = _provisional_daily_indicators(
            indicator_seed_cache.get("state"),
            ltp,
        )

        month_change = 0.0
        five_day_change = 0.0
        if len(rows) >= 2:
            month_change = (rows[0]["close"] / rows[min(20, len(rows) - 1)]["close"] - 1.0) * 100.0
            five_day_change = (rows[0]["close"] / rows[min(5, len(rows) - 1)]["close"] - 1.0) * 100.0

        trend_score = 10.0
        if month_change < -8.0:
            trend_score -= 6.0
        elif month_change < -3.0:
            trend_score -= 4.0
        elif month_change < 0:
            trend_score -= 2.0
        else:
            trend_score += 3.0
        if five_day_change < -3.0:
            trend_score -= 3.0
        elif five_day_change > 0:
            trend_score += 2.0
        if past_data.get("higher_low_structure"):
            trend_score += 2.0
        trend_score = max(0.0, min(15.0, trend_score))

        # Count consecutive completed sessions from D0, capped at D2. Each
        # cooled day contributes 2.4 historical settling points.
        stable_days = 0
        if avg_range_20 > 0:
            cooling_limit_pct = min(2.0, 0.8 * avg_range_20)
            for row in rows[:3]:
                if row["high"] <= 0 or row["low"] <= 0:
                    break
                daily_range = (row["high"] - row["low"]) / row["low"] * 100.0
                if daily_range <= cooling_limit_pct:
                    stable_days += 1
                else:
                    break
        historical_settling_score = min(15.0, stable_days * 2.4)
        avg_range_5 = max(0.0, float(past_data.get("avg_range_5d_pct", 0) or 0))
        if avg_range_20 > 0 and avg_range_5 > 0:
            compression_ratio = avg_range_5 / avg_range_20
            historical_settling_score += 3.0 if compression_ratio <= 0.7 else (1.5 if compression_ratio <= 0.9 else 0.0)
        if past_data.get("higher_low_structure"):
            historical_settling_score += 1.0
        historical_settling_score = min(15.0, historical_settling_score)

        live_settling_score = 0.0
        if len(w30) >= 3 and avg_range_20 > 0:
            expected_30m_range = avg_range_20 * (30.0 / 375.0) ** 0.5
            range_ratio = recent_range_30 / expected_30m_range if expected_30m_range > 0 else 99.0
            live_settling_score += 9.0 if range_ratio <= 0.65 else (6.0 if range_ratio <= 1.0 else (3.0 if range_ratio <= 1.4 else 0.0))
            if no_new_low is True:
                live_settling_score += 3.0
            if higher_low:
                live_settling_score += 3.0
        live_settling_score = min(15.0, live_settling_score)

        local_now = datetime.now(ZoneInfo("Asia/Kolkata"))
        session_date = local_now.strftime("%Y-%m-%d")
        minute_index = (local_now.hour * 60 + local_now.minute) - (9 * 60 + 15)
        if 0 <= minute_index < 375:
            self._record_intraday_volume_profile(
                symbol,
                session_date,
                minute_index,
                current_volume,
            )
        daily_volumes = []
        for row in past_data.get("nse_data") or []:
            try:
                row_date = str(row.get("Date", ""))[:10]
                value = float(row.get("TotalTradedQuantity", 0) or 0)
                if row_date and row_date < session_date and value > 0:
                    daily_volumes.append(value)
            except (TypeError, ValueError):
                continue
            if len(daily_volumes) >= 5:
                break
        avg_volume_5d = sum(daily_volumes) / len(daily_volumes) if daily_volumes else 0.0
        volume_ratio, expected_volume, profile_days, volume_baseline = self._volume_profile_ratio(
            symbol,
            session_date,
            max(0, min(374, minute_index)),
            current_volume,
            avg_volume_5d,
        )
        volume_score = 10.0 if volume_baseline == "linear_daily_estimate" else 0.0
        if volume_ratio >= 1.5:
            volume_score = 20.0
        elif volume_ratio >= 1.2:
            volume_score = 16.0
        elif volume_ratio >= 1.0:
            volume_score = 13.0
        elif volume_ratio >= 0.75:
            volume_score = 8.0
        elif volume_baseline != "linear_daily_estimate":
            volume_score = 4.0

        price_score = 0.0
        if change_15m > 0.5:
            price_score += 8.0
        elif change_15m > 0.1:
            price_score += 6.0
        elif change_15m > 0:
            price_score += 4.0
        elif change_15m < -0.5:
            price_score += 0.0
        else:
            price_score += 2.0
        price_score += 5.0 if gain_pct > 0 else (2.0 if gain_pct >= -0.5 else 0.0)
        if volume_ratio >= 1.2 and change_15m > 0:
            price_score += 5.0
        price_score = min(20.0, price_score)

        pressure_samples = w15
        earlier_pressure = float(pressure_samples[0].get("buy_pressure", buying_pressure)) if len(pressure_samples) >= 2 else buying_pressure
        pressure_delta = buying_pressure - earlier_pressure
        pressure_score = 3.0
        if buying_pressure >= 0.62:
            pressure_score = 8.0
        elif buying_pressure >= 0.57:
            pressure_score = 7.0
        elif buying_pressure >= 0.52:
            pressure_score = 6.0
        elif buying_pressure >= 0.45:
            pressure_score = 4.0
        if pressure_delta >= 0.05:
            pressure_score += 2.0
        elif pressure_delta <= -0.05:
            pressure_score -= 2.0
        pressure_score = max(0.0, min(10.0, pressure_score))

        recovery_score = 0.0
        if no_new_low is True:
            recovery_score += 2.0
        if higher_low:
            recovery_score += 1.5
        if change_15m > 0.1 and gain_pct < 0:
            recovery_score += 1.5

        base_rating = int(round(min(100.0, max(0.0,
            trend_score + historical_settling_score + live_settling_score
            + volume_score + price_score + pressure_score + recovery_score
        ))))

        company_sector = past_data.get("company_sector")
        company_industry = past_data.get("company_industry")
        sector_index = past_data.get("sector_index_symbol") or sector_index_for(
            company_sector, company_industry
        )
        sector_live = self.sector_index_live_data.get(sector_index, {}) if sector_index else {}
        sector_history = self.sector_index_history.get(sector_index, {}) if sector_index else {}
        nifty_live = self.sector_index_live_data.get("Nifty 50", {})
        nifty_history = self.sector_index_history.get("Nifty 50", {})
        if time.time() - float(sector_live.get("updated_at", 0) or 0) > 120:
            sector_live = {}
        if time.time() - float(nifty_live.get("updated_at", 0) or 0) > 120:
            nifty_live = {}
        sector_day_change = sector_live.get("daily_change_pct")
        sector_5d_change = sector_history.get("five_day_change_pct")
        nifty_day_change = nifty_live.get("daily_change_pct")
        nifty_5d_change = nifty_history.get("five_day_change_pct")
        sector_adjustment, sector_vs_nifty_5d = sector_rating_adjustment(
            sector_day_change,
            sector_5d_change,
            nifty_day_change,
            nifty_5d_change,
        )
        # Sector context helps ranking, while statuses continue to use the
        # stock's base score and existing price/volume/pressure requirements.
        rating = int(round(min(100.0, max(0.0, base_rating + sector_adjustment))))

        price_up = change_15m > 0.1 or gain_pct > 0
        volume_confirmed = volume_ratio >= 1.0
        pressure_supportive = buying_pressure >= 0.52
        break_move = (
            change_5m >= 1.0
            and volume_ratio >= 1.2
            and buying_pressure >= 0.55
            and len(w5) >= 3
        )
        if break_move:
            recommendation = "BREAK"
            status = "🔵 BREAK"
        elif (
            base_rating >= 70
            and price_up
            and volume_confirmed
            and pressure_supportive
            and no_new_low is True
            and live_settling_score >= 6.0
        ):
            recommendation = "BUY"
            status = "🟢 BUY"
        elif base_rating >= 40 or price_up or volume_ratio >= 1.2:
            recommendation = "WATCH"
            status = "🟡 WATCH"
        else:
            recommendation = "WAIT"
            status = "🔴 WAIT"

        return {
            "symbol": symbol,
            "score": rating,
            "rating": rating,
            "base_rating": base_rating,
            "company_sector": company_sector,
            "company_industry": company_industry,
            "sector_index": sector_index,
            "sector_index_daily_change_pct": (
                round(float(sector_day_change), 2)
                if sector_day_change is not None else None
            ),
            "sector_index_5d_change_pct": (
                round(float(sector_5d_change), 2)
                if sector_5d_change is not None else None
            ),
            "sector_vs_nifty_5d_pct": (
                round(float(sector_vs_nifty_5d), 2)
                if sector_vs_nifty_5d is not None else None
            ),
            "sector_rating_adjustment": sector_adjustment,
            "recommendation": recommendation,
            "status": status,
            "ltp": round(ltp, 2),
            "gain": round(gain_pct, 2),
            "vol": round(volume_ratio, 2),
            "buy": round(buying_pressure * 100.0, 2),
            "price_change_5m": round(change_5m, 2),
            "price_change_15m": round(change_15m, 2),
            "volume_baseline": volume_baseline,
            "volume_baseline_days": profile_days,
            "expected_volume": int(expected_volume),
            "price_stable": live_settling_score >= 9,
            "no_new_low": no_new_low,
            "higher_low": higher_low,
            "pressure_rising": pressure_delta >= 0.05,
            "price_rising_60s": change_pct(window(60)) > 0,
            "price_rising_180s": change_pct(window(180)) > 0,
            "historical_month_change": round(month_change, 2),
            "historical_5d_change": round(five_day_change, 2),
            **daily_indicators,
            "settled_days": stable_days,
            "intraday_range_30m_pct": round(recent_range_30, 3),
            "breakout_persistent": False,
        }

    def evaluate_live_breakout(self, symbol, past_data, cs):
        """Compatibility alias to the unified stock rating evaluator."""
        return self.evaluate_unified_candidate(symbol, past_data, cs)
    def evaluate_btst_candidate(self, symbol, past_data, cs):
        """Compatibility alias to the unified stock rating evaluator."""
        return self.evaluate_unified_candidate(symbol, past_data, cs)
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

        ltp = _price(raw_ltp)
        last_closed_price = _price(raw_close)
        open_price = _price(raw_open)
        day_high = _price(raw_high)
        day_low = _price(raw_low)

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

        # ---------------------------------------------------------------
        # TODAY INTRADAY HISTORY FOR LOCAL UI POPUP
        # ---------------------------------------------------------------
        # Angel One gives cumulative day volume. Keep one observation per
        # minute so the popup can draw the real intraday LTP and per-minute
        # volume without storing every websocket tick.
        #
        # IMPORTANT: this data is populated directly from analyze_tick_metrics
        # for every processed Angel One tick. It must happen BEFORE the live
        # signal qualification below, otherwise a stock that is displayed in
        # the UI could have no intraday chart data.
        intraday_date = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d")
        intraday_samples = cache.setdefault(
            "intraday_samples",
            deque(maxlen=390),
        )

        if cache.get("intraday_date") != intraday_date:
            intraday_samples.clear()
            cache["intraday_date"] = intraday_date

        minute_key = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%H:%M")

        if (
            intraday_samples
            and intraday_samples[-1].get("minute") == minute_key
        ):
            # Keep the latest tick for the current minute.
            intraday_samples[-1]["ts"] = time.time()
            intraday_samples[-1]["ltp"] = ltp
            intraday_samples[-1]["volume"] = current_volume
        else:
            intraday_samples.append({
                "minute": minute_key,
                "ts": time.time(),
                "ltp": ltp,
                "volume": current_volume,
            })

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
