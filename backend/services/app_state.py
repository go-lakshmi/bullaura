import time
from datetime import datetime
from threading import Lock

from backend.config.logger import logger as log


class AppState:
    def __init__(self):
        self.engine = None
        self.lock = Lock()
        self.latest = {"updated_at": None, "stocks": []}
        self._last_published_at = 0.0
        self._publish_interval = 60.0
        self._publish_count = 0

        # UI audit collection. A symbol is stored only once per hourly window,
        # while its dictionary value always contains the latest UI details.
        # This prevents duplicate stock log lines even though the browser UI
        # receives snapshots frequently.
        self.ui_sent_stocks = set()
        self.ui_sent_stock_details = {}
        self._ui_log_started_at = time.monotonic()
        self._ui_log_started_wall = datetime.now()
        self._ui_log_interval = 600.0

    def set_engine(self, engine):
        with self.lock:
            self.engine = engine

    @staticmethod
    def _ui_log_details(stock):
        """Extract the important fields that are actually visible to the UI."""
        metrics = stock.get("metrics") or {}
        symbol = str(stock.get("symbol", "") or "").strip().replace(".NS", "").replace(".BO", "")
        return {
            "symbol": symbol,
            "token": stock.get("token", ""),
            "category": stock.get("category", ""),
            "signal_channel": stock.get("signal_channel", ""),
            "recommendation": stock.get("recommendation", ""),
            "status": stock.get("status", ""),
            "score": stock.get("rating", stock.get("score", 0)),
            "ltp": stock.get("ltp", 0),
            "gain_pct": stock.get("gain", 0),
            "rvol": stock.get("rvol", 0),
            "buying_pressure": stock.get("buying_pressure", 0),
            "current_volume": metrics.get("current_volume", 0),
            "avg_volume_5": metrics.get("avg_volume_5", 0),
            "avg_volume_20": metrics.get("avg_volume_20", 0),
            "buying_volume": metrics.get("buying_volume", 0),
            "selling_volume": metrics.get("selling_volume", 0),
            "buy_orders": metrics.get("buy_orders", 0),
            "sell_orders": metrics.get("sell_orders", 0),
            "close_position": metrics.get("close_position", 0),
            "day_high": metrics.get("day_high", 0),
            "day_low": metrics.get("day_low", 0),
            "resistance": metrics.get("resistance", 0),
            "distance_to_resistance": metrics.get("distance_to_resistance", 999),
            "price_stable": metrics.get("price_stable", False),
            "price_rising_60s": metrics.get("price_rising_60s", False),
            "price_rising_180s": metrics.get("price_rising_180s", False),
            "higher_low": metrics.get("higher_low", False),
            "recovery": metrics.get("recovery", False),
            "compression": metrics.get("compression", False),
            "pressure_rising": metrics.get("pressure_rising", False),
            "pattern": metrics.get("pattern", ""),
            "reason": metrics.get("reason", ""),
            "btst_signal": stock.get("btst_signal", ""),
            "swing_signal": stock.get("swing_signal", ""),
            "btst_score": stock.get("btst_score", 0),
            "swing_score": stock.get("swing_score", 0),
        }

    def _collect_ui_stocks_locked(self, snapshot):
        for stock in snapshot.get("stocks", []) or []:
            symbol = str(stock.get("symbol", "") or "").strip().replace(".NS", "")
            if not symbol:
                continue
            self.ui_sent_stocks.add(symbol)
            self.ui_sent_stock_details[symbol] = self._ui_log_details(stock)

    def _take_hourly_ui_summary_locked(self):
        now = time.monotonic()
        if now - self._ui_log_started_at < self._ui_log_interval:
            return None

        summary = {
            "window_start": self._ui_log_started_at,
            "window_end": now,
            "window_start_wall": self._ui_log_started_wall,
            "window_end_wall": datetime.now(),
            "stocks": [
                self.ui_sent_stock_details[symbol]
                for symbol in sorted(self.ui_sent_stocks)
                if symbol in self.ui_sent_stock_details
            ],
        }

        # Clear immediately while holding the same lock that protects the
        # collection. New snapshots therefore start a completely fresh hour.
        self.ui_sent_stocks.clear()
        self.ui_sent_stock_details.clear()
        self._ui_log_started_at = now
        self._ui_log_started_wall = summary["window_end_wall"]
        return summary

    @staticmethod
    def _log_hourly_ui_summary(summary):
        if summary is None:
            return

        stocks = summary["stocks"]
        log.info(
            "UI HOURLY SEND SUMMARY | unique_stocks=%d | window=%s -> %s",
            len(stocks),
            summary["window_start_wall"].strftime("%Y-%m-%d %H:%M:%S"),
            summary["window_end_wall"].strftime("%Y-%m-%d %H:%M:%S"),
        )

        if not stocks:
            log.info("UI HOURLY SEND SUMMARY | no qualified stocks were sent during this hour.")
            return

        for rank, stock in enumerate(stocks, start=1):
            log.info(
                "UI SEND STOCK #%d | symbol=%s | token=%s | category=%s | channel=%s | "
                "recommendation=%s | status=%s | score=%s | LTP=%.2f | gain=%.2f%% | "
                "RVOL=%.2fx | buy_pressure=%.2f%% | current_volume=%s | avg5=%s | avg20=%s | "
                "buy_qty=%s | sell_qty=%s | buy_orders=%s | sell_orders=%s | close_pos=%.2f%% | "
                "high=%.2f | low=%.2f | resistance=%.2f | dist_resistance=%.2f%% | "
                "stable=%s | rise60=%s | rise180=%s | higher_low=%s | recovery=%s | "
                "compression=%s | pressure_rising=%s | pattern=%s | reason=%s | "
                "BTST=%s/%s | SWING=%s/%s",
                rank,
                stock.get("symbol", ""), stock.get("token", ""),
                stock.get("category", ""), stock.get("signal_channel", ""),
                stock.get("recommendation", ""), stock.get("status", ""),
                stock.get("score", 0), float(stock.get("ltp", 0) or 0),
                float(stock.get("gain_pct", 0) or 0), float(stock.get("rvol", 0) or 0),
                float(stock.get("buying_pressure", 0) or 0) * 100.0,
                stock.get("current_volume", 0), stock.get("avg_volume_5", 0), stock.get("avg_volume_20", 0),
                stock.get("buying_volume", 0), stock.get("selling_volume", 0),
                stock.get("buy_orders", 0), stock.get("sell_orders", 0),
                float(stock.get("close_position", 0) or 0),
                float(stock.get("day_high", 0) or 0), float(stock.get("day_low", 0) or 0),
                float(stock.get("resistance", 0) or 0), float(stock.get("distance_to_resistance", 999) or 999),
                stock.get("price_stable", False), stock.get("price_rising_60s", False),
                stock.get("price_rising_180s", False), stock.get("higher_low", False),
                stock.get("recovery", False), stock.get("compression", False),
                stock.get("pressure_rising", False), stock.get("pattern", ""),
                stock.get("reason", ""), stock.get("btst_signal", ""), stock.get("btst_score", 0),
                stock.get("swing_signal", ""), stock.get("swing_score", 0),
            )

        log.info("UI HOURLY SEND SUMMARY END | unique_stocks=%d | collection cleared", len(stocks))

    def set_snapshot(self, snapshot, force=False):
        """Publish one completed snapshot and audit UI stocks once per hour."""
        now = time.monotonic()
        hourly_summary = None

        with self.lock:
            if force or (now - self._last_published_at) >= self._publish_interval:
                # Record exactly what is about to be published to the UI.
                self._collect_ui_stocks_locked(snapshot)
                hourly_summary = self._take_hourly_ui_summary_locked()

                self.latest = snapshot
                self._last_published_at = now
                self._publish_count += 1
                published = True
            else:
                published = False

        # Log outside the state lock so file I/O never blocks API/tick state.
        self._log_hourly_ui_summary(hourly_summary)
        return published

    def get_snapshot(self):
        with self.lock:
            return dict(self.latest)


# Shared application state used by FastAPI routes and the engine runner.
state = AppState()
