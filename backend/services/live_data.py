import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


def _num(value, default=0.0):
    try:
        return float(value or default)
    except (TypeError, ValueError):
        return float(default)


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
    today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
    chart_start_date = today - timedelta(days=30)

    for token, cache in getattr(engine, "live_cache", {}).items():
        symbol = str(cache.get("symbol", "") or "").strip()
        if not symbol:
            continue

        cs = cache.get("ui_last_cs") or {}
        signal = cache.get("ui_unified_signal") or {}
        is_new_listing = symbol in new_listing_symbols
        past = (
            getattr(engine, "new_listing_candidates", {}).get(symbol, {})
            if is_new_listing
            else getattr(engine, "volume_shockers", {}).get(symbol, {})
        ) or {}

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

        if not signal:
            continue

        recommendation = str(signal.get("recommendation", "WAIT") or "WAIT")
        rating = _num(signal.get("rating", signal.get("score")))
        gain_pct = _num(cs.get("gain_pct"))

        live_ltp = _num(cs.get("ltp"))
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
