import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


def _num(value, default=0.0):
    try:
        return float(value or default)
    except (TypeError, ValueError):
        return float(default)


def _today_support_resistance(candles, ltp):
    """Derive simple intraday swing levels from completed 15-minute bars."""
    completed = [candle for candle in candles if candle.get("completed")]
    if len(completed) < 3 or ltp <= 0:
        return {
            "support": None,
            "resistance": None,
            "status": "BUILDING LEVELS",
            "breakout_volume_ratio": None,
            "setup_label": "WAIT",
            "setup_reason": "Collecting at least 3 completed 15-minute candles before judging levels.",
            "invalidation_level": None,
            "invalidation_label": "",
        }

    # Use earlier completed bars to establish levels; the latest closed bar
    # is evaluated against those levels for a possible confirmed breakout.
    prior = completed[:-1]
    latest_closed = completed[-1]
    swing_highs = []
    swing_lows = []
    for index in range(1, len(prior) - 1):
        left, point, right = prior[index - 1:index + 2]
        if point["high"] >= left["high"] and point["high"] >= right["high"]:
            swing_highs.append(point["high"])
        if point["low"] <= left["low"] and point["low"] <= right["low"]:
            swing_lows.append(point["low"])

    lookback = prior[-8:]
    if not lookback:
        lookback = completed[-3:]
    if not swing_highs:
        swing_highs = [max(candle["high"] for candle in lookback)]
    if not swing_lows:
        swing_lows = [min(candle["low"] for candle in lookback)]

    supports_below = [level for level in swing_lows if level <= ltp]
    resistances_above = [level for level in swing_highs if level >= ltp]
    support = max(supports_below) if supports_below else min(swing_lows)
    resistance = min(resistances_above) if resistances_above else None

    recent_prior = prior[-8:]
    prior_resistance = max(
        (candle["high"] for candle in recent_prior),
        default=None,
    )
    prior_volumes = [
        candle["volume"] for candle in prior[-5:]
        if candle["volume"] > 0
    ]
    average_prior_volume = (
        sum(prior_volumes) / len(prior_volumes) if prior_volumes else 0.0
    )
    breakout_volume_ratio = (
        latest_closed["volume"] / average_prior_volume
        if average_prior_volume > 0
        else None
    )
    breakout = bool(
        prior_resistance
        and latest_closed["close"] > prior_resistance
        and ltp >= prior_resistance
        and breakout_volume_ratio is not None
        and breakout_volume_ratio >= 1.2
    )

    average_prior_volume_for_bounce = average_prior_volume
    bounce_volume_ratio = (
        latest_closed["volume"] / average_prior_volume_for_bounce
        if average_prior_volume_for_bounce > 0
        else None
    )
    previous_candle = completed[-2]
    support_bounce = bool(
        support
        and latest_closed["low"] <= support * 1.004
        and latest_closed["close"] > support
        and latest_closed["close"] > previous_candle["high"]
        and bounce_volume_ratio is not None
        and bounce_volume_ratio >= 1.2
        and ltp >= latest_closed["close"]
    )

    support_distance_pct = (
        (ltp - support) / support * 100 if support and ltp >= support else None
    )
    resistance_distance_pct = (
        (resistance - ltp) / resistance * 100
        if resistance and ltp <= resistance
        else None
    )
    if breakout:
        status = "BREAKOUT · 15M CLOSE + VOLUME"
        resistance = prior_resistance
        setup_label = "BREAKOUT CONFIRMED"
        setup_reason = (
            f"A completed 15-minute candle closed above resistance with "
            f"{breakout_volume_ratio:.2f}× recent volume."
        )
        invalidation_level = prior_resistance
        invalidation_label = "Breakout weakens below"
    elif support_bounce:
        status = "SUPPORT BOUNCE · 15M CLOSE + VOLUME"
        setup_label = "BOUNCE CONFIRMED"
        setup_reason = (
            f"Price held support, closed above the previous candle high, "
            f"and volume was {bounce_volume_ratio:.2f}× recent average."
        )
        invalidation_level = support
        invalidation_label = "Bounce weakens below"
    elif support is not None and support_distance_pct is not None and support_distance_pct <= 0.4:
        status = "TESTING SUPPORT"
        setup_label = "SUPPORT TEST"
        setup_reason = "Price is near support; wait for a completed candle to hold and turn upward."
        invalidation_level = support
        invalidation_label = "Support breaks below"
    elif resistance_distance_pct is not None and resistance_distance_pct <= 0.4:
        status = "TESTING RESISTANCE"
        setup_label = "RESISTANCE TEST"
        setup_reason = "Price is near resistance; wait for a 15-minute close above it with stronger volume."
        invalidation_level = support
        invalidation_label = "Support reference"
    elif resistance is None and prior_resistance and ltp > prior_resistance:
        status = "ABOVE RESISTANCE · WAIT FOR CLOSE"
        setup_label = "WAIT FOR CLOSE"
        setup_reason = "Price is above resistance intrabar, but a volume-confirmed 15-minute close is not confirmed yet."
        invalidation_level = prior_resistance
        invalidation_label = "Breakout line"
    elif support is not None and ltp < support:
        status = "BELOW SUPPORT"
        setup_label = "BELOW SUPPORT"
        setup_reason = "Price is below the support level; the support-based setup is invalid for now."
        invalidation_level = support
        invalidation_label = "Support was lost below"
    else:
        status = "IN RANGE"
        setup_label = "WAIT"
        setup_reason = "Price is between support and resistance with no confirmed bounce or breakout."
        invalidation_level = support
        invalidation_label = "Support reference"

    return {
        "support": round(float(support), 2) if support is not None else None,
        "resistance": round(float(resistance), 2) if resistance is not None else None,
        "status": status,
        "setup_label": setup_label,
        "setup_reason": setup_reason,
        "invalidation_label": invalidation_label,
        "invalidation_level": (
            round(float(invalidation_level), 2)
            if invalidation_level is not None else None
        ),
        "breakout_volume_ratio": (
            round(float(breakout_volume_ratio), 2)
            if breakout_volume_ratio is not None else None
        ),
    }


def build_snapshot(engine):
    """
    Build the local-UI snapshot.

    Chart data covers the current day and prior 30 calendar days of completed
    NSE sessions plus one current-day point from Angel One. The current NSE
    row is excluded so live LTP/current volume come only from Angel One.
    """
    stocks_by_symbol = {}
    new_stocks_by_symbol = {}
    new_listing_symbols = set(
        getattr(engine, "new_listing_candidates", {}).keys()
    )
    ma_recovery_symbols = set(
        getattr(engine, "ma_recovery_candidates", {}).keys()
    )
    today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
    chart_start_date = today - timedelta(days=30)

    for token, cache in getattr(engine, "live_cache", {}).items():
        symbol = str(cache.get("symbol", "") or "").strip()
        if not symbol:
            continue

        cs = cache.get("ui_last_cs") or {}
        signal = cache.get("ui_unified_signal") or {}
        is_new_listing = symbol in new_listing_symbols
        is_ma_recovery = symbol in ma_recovery_symbols
        if is_new_listing:
            past = getattr(engine, "new_listing_candidates", {}).get(symbol, {})
        elif is_ma_recovery:
            past = getattr(engine, "ma_recovery_candidates", {}).get(symbol, {})
        else:
            past = getattr(engine, "volume_shockers", {}).get(symbol, {})
        past = past or {}

        # ---------------------------------------------------------------
        # ONE-MONTH CHART DATA
        # ---------------------------------------------------------------
        # NSE stores the latest row first.  Use completed sessions only;
        # if NSE already contains today's row, exclude it because today's
        # LTP and cumulative volume must come from Angel One. Keep only the
        # rolling 30-calendar-day window to align with a one-month chart.
        nse_records = past.get("nse_data") or []
        historical_price = []
        historical_volume = []

        # nse_data is newest-first. Keep all completed rows for rolling
        # volume averages, and a date-limited subset for the month charts.
        completed_rows = []
        chart_rows = []
        for row in nse_records:
            raw_date = row.get("Date")
            try:
                row_date = datetime.strptime(
                    str(raw_date)[:10], "%Y-%m-%d"
                ).date()
            except (TypeError, ValueError):
                continue
            if row_date < today:
                completed_rows.append((row_date, row))
                if row_date >= chart_start_date:
                    chart_rows.append((row_date, row))

        def avg_completed_volume(days):
            values = [
                _num(row.get("TotalTradedQuantity"))
                for _, row in completed_rows[:days]
                if _num(row.get("TotalTradedQuantity")) > 0
            ]
            return (sum(values) / len(values)) if values else 0.0

        avg_volume_5 = avg_completed_volume(5)
        avg_volume_10 = avg_completed_volume(10)
        avg_volume_15 = avg_completed_volume(15)
        avg_volume_20 = avg_completed_volume(20)

        for row_date, row in reversed(chart_rows):
            close = _num(row.get("ClosePrice"))
            volume = _num(row.get("TotalTradedQuantity"))
            if close > 0:
                historical_price.append({
                    "date": row_date.strftime("%Y-%m-%d"),
                    "ts": row_date.strftime("%Y-%m-%d"),
                    "value": close,
                })
            historical_volume.append({
                "date": row_date.strftime("%Y-%m-%d"),
                "ts": row_date.strftime("%Y-%m-%d"),
                "value": volume,
            })

        # Current-day point: ONLY Angel One.  One point is used instead of
        # every websocket tick so the UI graph remains a one-month daily
        # series rather than turning back into an intraday chart.
        current_ltp = _num(cs.get("ltp"))
        current_volume = _num(cs.get("current_volume"))
        today_label = today.strftime("%Y-%m-%d")

        if current_ltp > 0:
            historical_price.append({
                "date": today_label,
                "ts": today_label,
                "value": current_ltp,
            })

        historical_volume.append({
            "date": today_label,
            "ts": today_label,
            "value": current_volume,
        })

        price_history = historical_price
        volume_history = historical_volume

        # ---------------------------------------------------------------
        # TODAY'S INTRADAY DATA FOR THE STATUS-HOVER POPUP
        # ---------------------------------------------------------------
        # nse_engine records one Angel One observation per minute in
        # cache["intraday_samples"]. Convert that cache into exactly the
        # fields expected by frontend/components/TodayPopup.jsx.
        #
        # Angel One volume is cumulative for the session, so the popup
        # volume bars use the delta from the previous minute.
        raw_intraday = list(cache.get("intraday_samples") or [])
        today_price_history = []
        today_volume_history = []
        previous_cumulative_volume = None

        for sample in raw_intraday:
            sample_ltp = _num(sample.get("ltp"))
            cumulative_volume = int(_num(sample.get("volume")))
            minute = str(sample.get("minute", "") or "").strip()

            if sample_ltp <= 0 or not minute:
                continue

            if previous_cumulative_volume is None:
                minute_volume = 0
            else:
                minute_volume = max(
                    0,
                    cumulative_volume - previous_cumulative_volume,
                )

            previous_cumulative_volume = cumulative_volume

            # TodayPopup.jsx reads `date` for the X-axis and `minute` for
            # the tooltip label. Keep both as HH:MM.
            today_price_history.append({
                "date": minute,
                "minute": minute,
                "ts": _num(sample.get("ts"), time.time()),
                "value": sample_ltp,
            })

            today_volume_history.append({
                "date": minute,
                "minute": minute,
                "ts": _num(sample.get("ts"), time.time()),
                "value": minute_volume,
            })

        intraday_live = bool(today_price_history and current_ltp > 0)

        # Build the live 15-minute OHLCV series from every Angel One tick
        # processed by nse_engine. Only completed candles feed S/R levels;
        # the active candle is still drawn and updated in the popup.
        market_now = datetime.now(ZoneInfo("Asia/Kolkata"))
        market_minute = market_now.hour * 60 + market_now.minute
        today_candles_15m = []
        for raw_candle in list(cache.get("intraday_15m_candles") or []):
            if raw_candle.get("date") != today_label:
                continue
            try:
                start_minute = int(raw_candle.get("start_minute"))
                end_minute = int(raw_candle.get("end_minute"))
                candle = {
                    "date": today_label,
                    "start": str(raw_candle.get("start", "")),
                    "end": str(raw_candle.get("end", "")),
                    "start_minute": start_minute,
                    "end_minute": end_minute,
                    "x": start_minute + 7.5,
                    "open": _num(raw_candle.get("open")),
                    "high": _num(raw_candle.get("high")),
                    "low": _num(raw_candle.get("low")),
                    "close": _num(raw_candle.get("close")),
                    "volume": int(_num(raw_candle.get("volume"))),
                    "completed": market_minute >= end_minute,
                }
            except (TypeError, ValueError):
                continue
            if candle["open"] > 0 and candle["high"] > 0 and candle["low"] > 0 and candle["close"] > 0:
                today_candles_15m.append(candle)

        today_support_resistance = _today_support_resistance(
            today_candles_15m,
            current_ltp,
        )

        if not signal:
            continue

        # A stock that failed only the historical SMA gate is shown with the
        # regular candidates after its live price stays above the previous
        # close and at least one stored SMA for three minutes.
        if is_ma_recovery:
            last_qualifying_at = _num(cache.get("ma_recovery_last_qualifying_at"))
            if (
                not cache.get("ma_recovery_confirmed")
                or time.time() - last_qualifying_at > 60.0
            ):
                continue

        recommendation = str(signal.get("recommendation", "WAIT") or "WAIT")
        rating = _num(signal.get("rating", signal.get("score")))
        gain_pct = _num(cs.get("gain_pct"))

        live_ltp = _num(cs.get("ltp"))
        moving_averages = past.get("moving_averages") or {}
        available_moving_averages = [
            _num(value)
            for value in moving_averages.values()
            if _num(value) > 0
        ]

        # UI-only trend hide: keep monitoring and rating these stocks, but
        # don't publish them when LTP is below every moving average we have.
        # Stocks with no available averages pass through unchanged.
        if live_ltp > 0 and available_moving_averages and all(
            live_ltp < average for average in available_moving_averages
        ):
            continue

        day_low = _num(cs.get("day_low"))
        distance_from_day_low_pct = (
            (live_ltp - day_low) / day_low * 100.0
            if day_low > 0
            else None
        )

        # Hide stocks trading less than 0.5% above today's low. This affects
        # only the UI snapshot; tick processing and rating continue normally.
        if not is_new_listing and (
            distance_from_day_low_pct is not None
            and distance_from_day_low_pct < 0.5
        ):
            continue

        # The two regular-card presentation hides do not apply to short-
        # history candidates: show every evaluated short-history stock.
        if not is_new_listing and gain_pct > 3.0:
            continue

        signal_age = max(0.0, time.time() - _num(cache.get("ui_updated_at"), time.time()))
        data_stale = signal_age > 120.0
        if data_stale:
            rating = max(0.0, rating - 15.0)
            recommendation = "WAIT"
        status = "🔴 WAIT" if data_stale else str(signal.get("status", recommendation) or recommendation)

        stock_payload = {
            "symbol": symbol,
            "is_new_listing": is_new_listing,
            "is_ma_recovery": is_ma_recovery,
            "moving_averages": past.get("moving_averages", {}),
            "recovery_above_smas": cache.get("ma_recovery_above_smas", []),
            "historical_sessions": past.get("historical_sessions"),
            "token": str(token),
            "ltp": current_ltp,
            "last_5_avg_daily_move": past.get("last_5_avg_daily_move"),
            "daily_macd": signal.get("daily_macd"),
            "daily_rsi": signal.get("daily_rsi"),
            "company_sector": signal.get("company_sector"),
            "company_industry": signal.get("company_industry"),
            "sector_index": signal.get("sector_index"),
            "sector_index_daily_change_pct": signal.get("sector_index_daily_change_pct"),
            "sector_index_5d_change_pct": signal.get("sector_index_5d_change_pct"),
            "sector_vs_nifty_5d_pct": signal.get("sector_vs_nifty_5d_pct"),
            "sector_rating_adjustment": signal.get("sector_rating_adjustment", 0),
            "base_rating": signal.get("base_rating", rating),
            "gain": gain_pct,
            "rvol": _num(signal.get("vol")),
            "buying_pressure": _num(cs.get("buying_pressure"), 0.5),
            "rating": rating,
            "category": recommendation,
            "recommendation": recommendation,
            "status": status,
            "price_history": price_history,
            "volume_history": volume_history,
            "today_price_history": today_price_history,
            "today_volume_history": today_volume_history,
            "today_candles_15m": today_candles_15m,
            "today_support_resistance": today_support_resistance,
            "intraday_live": intraday_live,
            "metrics": {
                "intraday_points": len(today_price_history),
                "intraday_last_time": (
                    today_price_history[-1]["minute"]
                    if today_price_history else ""
                ),
                "current_volume": int(current_volume),
                "avg_volume_5": avg_volume_5,
                "avg_volume_10": avg_volume_10,
                "avg_volume_15": avg_volume_15,
                "avg_volume_20": avg_volume_20,
                "buying_volume": _num(cs.get("buying_volume")),
                "selling_volume": _num(cs.get("selling_volume")),
                "buy_orders": int(_num(cs.get("total_buy_orders"))),
                "sell_orders": int(_num(cs.get("total_sell_orders"))),
                "close_position": _num(cs.get("close_position")),
                "day_high": _num(cs.get("day_high")),
                "day_low": _num(cs.get("day_low")),
                "previous_close": _num(cs.get("last_closed_price")),
                "price_stable": bool(signal.get("price_stable", False)),
                "price_rising_60s": bool(signal.get("price_rising_60s", False)),
                "price_rising_180s": bool(signal.get("price_rising_180s", False)),
                "higher_low": bool(signal.get("higher_low", False)),
                "no_new_low": signal.get("no_new_low"),
                "pressure_rising": bool(signal.get("pressure_rising", False)),
                "historical_month_change": _num(signal.get("historical_month_change")),
                "historical_5d_change": _num(signal.get("historical_5d_change")),
                "settled_days": int(_num(signal.get("settled_days"))),
                "price_change_5m": _num(signal.get("price_change_5m")),
                "price_change_15m": _num(signal.get("price_change_15m")),
                "volume_ratio": _num(signal.get("vol")),
                "volume_baseline": signal.get("volume_baseline", "unavailable"),
                "volume_baseline_days": int(_num(signal.get("volume_baseline_days"))),
                "expected_volume": int(_num(signal.get("expected_volume"))),
                "data_stale": data_stale,
                "data_age_seconds": round(signal_age, 1),
            },
            "historical": past,
            "updated_at": _num(cache.get("ui_updated_at")),
        }

        # Defensive symbol-level deduplication. If Angel One exposes more than
        # one cache/token for a symbol, the strongest qualified row wins.
        destination = new_stocks_by_symbol if is_new_listing else stocks_by_symbol
        existing = destination.get(symbol)
        if existing is None or stock_payload["rating"] > existing["rating"]:
            destination[symbol] = stock_payload

    stocks = sorted(
        stocks_by_symbol.values(),
        key=lambda x: x.get("rating", 0),
        reverse=True,
    )[:300]
    new_stocks = sorted(
        new_stocks_by_symbol.values(),
        key=lambda x: x.get("rating", 0),
        reverse=True,
    )
    return {
        "updated_at": time.time(),
        "stocks": stocks,
        "new_stocks": new_stocks,
    }
