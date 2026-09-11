import time
from datetime import datetime
from zoneinfo import ZoneInfo


def _num(value, default=0.0):
    try:
        return float(value or default)
    except (TypeError, ValueError):
        return float(default)


def build_snapshot(engine):
    """
    Build the local-UI snapshot.

    Chart data is one month of completed NSE sessions plus ONE current-day
    point from Angel One.  The current NSE row is deliberately excluded from
    the historical series so the live LTP/current volume come only from
    Angel One during the session.
    """
    stocks_by_symbol = {}
    today = datetime.now(ZoneInfo("Asia/Kolkata")).date()

    for token, cache in getattr(engine, "live_cache", {}).items():
        symbol = str(cache.get("symbol", "") or "").strip()
        if not symbol:
            continue

        cs = cache.get("ui_last_cs") or {}
        live_signal = cache.get("ui_live_signal") or {}
        btst_signal = cache.get("ui_btst_signal") or {}
        past = getattr(engine, "volume_shockers", {}).get(symbol, {}) or {}

        # ---------------------------------------------------------------
        # ONE-MONTH CHART DATA
        # ---------------------------------------------------------------
        # NSE stores the latest row first.  Use completed sessions only;
        # if NSE already contains today's row, exclude it because today's
        # LTP and cumulative volume must come from Angel One.
        nse_records = past.get("nse_data") or []
        historical_price = []
        historical_volume = []

        # nse_data is newest-first. Build completed-session rows once so
        # averages and chart history use the same D-1.. historical baseline.
        completed_rows = []
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

        for row_date, row in reversed(completed_rows):
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

        price_history = historical_price[-31:]
        volume_history = historical_volume[-31:]

        # Local UI receives only the same display-qualified candidates intended
        # for the mobile dashboard: buy pressure > 40% and gain < 3%.
        # Keep BTST and Swing as separate channel states so the UI never makes
        # one channel look like the other. Then choose the strongest channel
        # only for the primary card badge/ranking.
        def display_qualified(candidate):
            if not candidate:
                return False
            return (
                _num(candidate.get("buy")) > 40.0
                and _num(candidate.get("gain")) < 3.0
            )

        btst_display = btst_signal if display_qualified(btst_signal) else {}
        swing_display = live_signal if display_qualified(live_signal) else {}
        qualifying = [c for c in (btst_display, swing_display) if c]

        if not qualifying:
            continue

        active = max(qualifying, key=lambda x: _num(x.get("score")))
        recommendation = active.get("recommendation", "")
        status = active.get("status", "")
        active_channel = (
            "BTST" if active is btst_display else "SWING"
        )
        if btst_display and swing_display:
            active_channel = "BTST+SWING"

        day5 = past.get("day_5", {}) or {}
        day10 = past.get("day_10", {}) or {}
        day20 = past.get("day_20", {}) or {}

        stock_payload = {
            "symbol": symbol,
            "token": str(token),
            "ltp": current_ltp,
            "gain": _num(cs.get("gain_pct")),
            "rvol": _num(active.get("vol")),
            "buying_pressure": _num(cs.get("buying_pressure"), 0.5),
            "rating": _num(active.get("score"), _num(past.get("preparation_score"))),
            "btst_signal": btst_display.get("recommendation", ""),
            "swing_signal": swing_display.get("recommendation", ""),
            "btst_score": _num(btst_display.get("score")),
            "swing_score": _num(swing_display.get("score")),
            "signal_channel": active_channel,
            "recommendation": recommendation,
            "status": status,
            "news_status": active.get("news_status", "none"),
            "news_title": active.get("news_title", ""),
            "news_source": active.get("news_source", ""),
            "price_history": price_history,
            "volume_history": volume_history,
            "metrics": {
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
                "resistance": _num(active.get("resistance")),
                "distance_to_resistance": _num(active.get("distance"), 999),
                "price_stable": bool(active.get("price_stable", False)),
                "price_rising_60s": bool(active.get("price_rising_60s", False)),
                "price_rising_180s": bool(active.get("price_rising_180s", False)),
                "higher_low": bool(active.get("higher_low", False)),
                "recovery": bool(active.get("recovery", False)),
                "compression": bool(active.get("compression", False)),
                "pressure_rising": bool(active.get("pressure_rising", False)),
                "pattern": active.get("pattern", past.get("setup", "")),
                "reason": active.get("reason", ""),
            },
            "historical": past,
            "updated_at": _num(cache.get("ui_updated_at")),
        }

        # Defensive symbol-level deduplication. If Angel One exposes more than
        # one cache/token for a symbol, the strongest qualified row wins.
        existing = stocks_by_symbol.get(symbol)
        if existing is None or stock_payload["rating"] > existing["rating"]:
            stocks_by_symbol[symbol] = stock_payload

    stocks = sorted(
        stocks_by_symbol.values(),
        key=lambda x: x.get("rating", 0),
        reverse=True,
    )[:30]
    return {"updated_at": time.time(), "stocks": stocks}
