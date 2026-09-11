import os
import requests
import time


class MobileDashboard:
    """
    Telegram dashboard for the new NSE + Angel One Volume Shocker engine.

    Expected stock fields from nse(11).py include:
      symbol, score, recommendation, status, ltp, gain,
      buy, sell, vol, turnover, close_pos, day_range,
      resistance, distance, breakout_hold,
      historical_score, live_score, reason,
      price_stable, price_rising_60s, price_rising_180s,
      price_volume_bullish, absorption, breakout_persistent,
      breakout_hold_seconds, trade_pace / trades (if available).

    The dashboard is intentionally defensive: missing fields are displayed
    as N/A instead of raising an exception.
    """

    def __init__(self, session_name="Volume Shocker Live Dashboard"):
        self.session_name = session_name
        self.last_render_timestamp = None

    # ------------------------------------------------------------------
    # Formatting helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _num(value, default=0.0):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _text(value, default="N/A"):
        if value is None:
            return default
        text = str(value).strip()
        return text if text else default

    def _format_price(self, value):
        value = self._num(value)
        if value >= 1000:
            return f"{value:,.0f}"
        if value >= 100:
            return f"{value:.1f}"
        return f"{value:.2f}"

    def _format_pct(self, value, signed=False):
        value = self._num(value)
        return f"{value:+.2f}%" if signed else f"{value:.2f}%"

    def _format_x(self, value):
        value = self._num(value)
        if value <= 0:
            return "N/A"
        return f"{value:.2f}x"

    @staticmethod
    def _yes_no(value):
        return "YES" if bool(value) else "NO"

    @staticmethod
    def _trend(value):
        return "↗" if bool(value) else "→"

    @staticmethod
    def _recommendation_icon(recommendation):
        return {
            "BUY": "🚀",
            "WATCH": "🟡",
            "HOLD": "🟢",
            "WAIT": "🔴",
        }.get(str(recommendation).upper(), "⚪")

    @staticmethod
    def _safe_html(text):
        """Keep Telegram HTML safe for values such as reason/symbol."""
        text = str(text)
        return (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    # ------------------------------------------------------------------
    # Signal classification
    # ------------------------------------------------------------------

    def _signal_label(self, stock):
        recommendation = str(
            stock.get("recommendation", "")
        ).upper()

        if recommendation == "BUY":
            return "🚀 BUY NOW"
        if recommendation == "WATCH":
            return "🟡 BREAKOUT WATCH"
        if recommendation == "HOLD":
            return "🟢 DEVELOPING"
        if recommendation == "WAIT":
            return "🔴 WAIT / NO BUY"

        # Backward compatibility with older nse output.
        buy_tag = str(stock.get("buy_tag", "")).upper()
        if "BUY" in buy_tag:
            return "🚀 BUY NOW"

        return "⚪ MONITOR"

    def _signal_priority(self, stock):
        recommendation = str(
            stock.get("recommendation", "")
        ).upper()

        priority = {
            "BUY": 4,
            "WATCH": 3,
            "HOLD": 2,
            "WAIT": 1,
        }.get(recommendation, 0)

        # Stronger score first inside each recommendation.
        return (
            priority,
            self._num(stock.get("score")),
            self._num(stock.get("buy")),
            self._num(stock.get("vol")),
        )

    # ------------------------------------------------------------------
    # Individual stock card
    # ------------------------------------------------------------------

    def _format_stock_card(self, stock, rank=None):
        symbol = (
            self._text(stock.get("symbol"), "N/A")
            .replace(".NS", "")
            .replace(".BO", "")
        )

        recommendation = str(
            stock.get("recommendation", "")
        ).upper()

        icon = self._recommendation_icon(recommendation)
        score = int(round(self._num(stock.get("score"))))

        ltp = self._format_price(stock.get("ltp"))
        gain = self._format_pct(stock.get("gain"), signed=True)

        buy = self._num(stock.get("buy"), -1)
        sell = self._num(stock.get("sell"), -1)
        rvol = self._num(stock.get("vol"), -1)
        turnover = self._num(stock.get("turnover"), -1)
        close_pos = self._num(stock.get("close_pos"), -1)
        day_range = self._num(stock.get("day_range"), -1)

        resistance = self._num(stock.get("resistance"), 0)
        distance = self._num(stock.get("distance"), 999)

        historical_score = int(
            round(self._num(stock.get("historical_score")))
        )
        live_score = int(
            round(self._num(stock.get("live_score")))
        )

        price_stable = stock.get("price_stable")
        rising_60 = stock.get("price_rising_60s")
        rising_180 = stock.get("price_rising_180s")
        pv_bullish = stock.get("price_volume_bullish")
        absorption = stock.get("absorption")
        persistent = stock.get("breakout_persistent")

        hold_seconds = stock.get(
            "breakout_hold_seconds",
            stock.get("breakout_hold", 0),
        )

        # New engine may expose trade pace as "trade_pace" or "trades".
        trade_pace = stock.get(
            "trade_pace",
            stock.get("trades"),
        )

        reason = self._safe_html(
            self._text(stock.get("reason"), "No detailed reason")
        )

        lines = [
            f"<b>{icon} {self._safe_html(symbol)}</b>  "
            f"<b>{self._signal_label(stock)}</b>",
            f"₹{ltp}   {gain}   <b>Score {score}/100</b>",
            "━━━━━━━━━━━━━━━━━━━━",
            (
                f"📊 RVOL     {self._format_x(rvol)}"
                f"   Buy {buy:.0f}%"
                if buy >= 0
                else "📊 RVOL     N/A   Buy N/A"
            ),
            (
                f"💰 Turnover {self._format_x(turnover)}"
                f"   Sell {sell:.0f}%"
                if sell >= 0
                else "💰 Turnover N/A   Sell N/A"
            ),
            (
                f"📍 ClosePos  {close_pos:.0f}%"
                f"   Range {day_range:.2f}%"
                if close_pos >= 0 and day_range >= 0
                else "📍 ClosePos  N/A   Range N/A"
            ),
            (
                f"🧱 Resist    ₹{self._format_price(resistance)}"
                f"   Dist {distance:+.2f}%"
                if resistance > 0 and distance < 999
                else "🧱 Resist    N/A"
            ),
            (
                f"⏱ Trend     60s {self._trend(rising_60)}"
                f"   180s {self._trend(rising_180)}"
            ),
            (
                f"🛡 Stable    {self._yes_no(price_stable)}"
                f"   PV {'BULL' if pv_bullish else 'BEAR/NO'}"
            ),
            (
                f"🚀 Breakout  {self._yes_no(persistent)}"
                f"   Hold {int(self._num(hold_seconds))}s"
            ),
            (
                f"📈 Scores    HIST {historical_score}"
                f"   LIVE {live_score}"
            ),
        ]

        if trade_pace is not None:
            lines.append(
                f"🔄 Trade pace {self._format_x(trade_pace)}"
            )
        else:
            lines.append("🔄 Trade count N/A")

        if absorption:
            lines.append("🧲 Absorption: YES")

        lines.extend([
            f"💡 <i>{reason[:420]}</i>",
        ])

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Telegram message
    # ------------------------------------------------------------------

    def _build_telegram_message(self, final_top_10):
        if not final_top_10:
            return (
                "<b>📡 VOLUME SHOCKER SCANNER</b>\n"
                f"📅 <i>{self.last_render_timestamp}</i>\n\n"
                "⚪ No active candidates currently pass the gates."
            )

        # Sort independently of the old category logic.
        stocks = sorted(
            final_top_10,
            key=self._signal_priority,
            reverse=True,
        )

        buys = [
            s for s in stocks
            if str(s.get("recommendation", "")).upper() == "BUY"
        ]
        watches = [
            s for s in stocks
            if str(s.get("recommendation", "")).upper() == "WATCH"
        ]
        holds = [
            s for s in stocks
            if str(s.get("recommendation", "")).upper() == "HOLD"
        ]
        waits = [
            s for s in stocks
            if str(s.get("recommendation", "")).upper() == "WAIT"
        ]

        lines = [
            "<b>📡 VOLUME SHOCKER LIVE MATRIX</b>",
            f"📅 <i>{self.last_render_timestamp}</i>",
            "",
            (
                f"🚀 BUY {len(buys)}   "
                f"🟡 WATCH {len(watches)}   "
                f"🟢 DEV {len(holds)}   "
                f"🔴 WAIT {len(waits)}"
            ),
        ]

        # Put BUY candidates first. These are the stocks the user should
        # inspect immediately.
        sections = [
            ("🚀 BUY NOW", buys),
            ("🟡 BREAKOUT WATCH", watches),
            ("🟢 DEVELOPING", holds),
            ("🔴 WAIT / DISTRIBUTION", waits),
        ]

        rank = 0
        for title, section in sections:
            if not section:
                continue

            lines.extend(["", f"<b>{title}</b>"])

            for stock in section:
                rank += 1
                lines.extend([
                    "",
                    self._format_stock_card(stock, rank=rank),
                ])

        # Keep Telegram messages safely below its message-size limit.
        message = "\n".join(lines)

        if len(message) > 3800:
            message = message[:3750] + "\n\n<i>...more candidates omitted</i>"

        return message

    def _push_to_telegram(self, final_top_10, token, chat_ids):
        """Send the new decision-oriented Volume Shocker dashboard."""
        if not token or not chat_ids:
            return

        message = self._build_telegram_message(final_top_10)

        url = f"https://api.telegram.org/bot{token}/sendMessage"

        for chat_id in chat_ids:
            payload = {
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }

            try:
                response = requests.post(
                    url,
                    json=payload,
                    timeout=5,
                )

                if response.status_code != 200:
                    print(
                        "Telegram API alert transmission failed: "
                        f"{response.status_code} {response.text}"
                    )

            except Exception as e:
                print(
                    "Failed to transmit mobile dashboard data "
                    f"over Telegram: {e}"
                )

    # ------------------------------------------------------------------
    # Main render entry point
    # ------------------------------------------------------------------

    def render_and_stream_tables(
        self,
        token,
        chatids,
        portfolio_summary=None,
        final_top_10=None,
    ):
        """Render local status and send the decision-oriented Telegram alert."""
        try:
            portfolio_summary = portfolio_summary or {}
            final_top_10 = final_top_10 or []

            os.system(
                "cls" if os.name == "nt" else "clear"
            )

            self.last_render_timestamp = time.strftime(
                "%Y-%m-%d %H:%M:%S"
            )

            if not final_top_10:
                print(
                    "SYSTEM STATUS: Scanning stream... "
                    "No assets currently pass all gates."
                )
            else:
                sorted_stocks = sorted(
                    final_top_10,
                    key=self._signal_priority,
                    reverse=True,
                )

                print(
                    "\n"
                    + "=" * 78
                    + f"\n {self.session_name.upper()}"
                    + "\n"
                    + "=" * 78
                )

                for stock in sorted_stocks:
                    symbol = (
                        self._text(stock.get("symbol"), "N/A")
                        .replace(".NS", "")
                        .replace(".BO", "")
                    )

                    recommendation = str(
                        stock.get("recommendation", "N/A")
                    ).upper()

                    score = int(
                        round(self._num(stock.get("score")))
                    )

                    print(
                        f"{self._recommendation_icon(recommendation)} "
                        f"{symbol:<12} "
                        f"{recommendation:<6} "
                        f"Score={score:<3} "
                        f"LTP=₹{self._format_price(stock.get('ltp'))} "
                        f"RVOL={self._format_x(stock.get('vol'))} "
                        f"BUY={self._num(stock.get('buy')):.0f}%"
                    )

                print("=" * 78)

            self._push_to_telegram(
                final_top_10,
                token,
                chatids,
            )

        except Exception as e:
            print(
                f"Mobile Render System encountered an exception: {e}"
            )
