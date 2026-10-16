import React, { useState } from "react";
import PriceChart from "../PriceChart";
import VolumeChart from "../VolumeChart";
import Metrics from "../Metrics";
import TodayPopup from "../TodayPopup";

function getStatus(category) {
  const value = String(category || "").toUpperCase().trim();

  if (value === "BUY") return { label: "BUY", icon: "🟢", className: "status-buy" };
  if (value === "BREAK") return { label: "BREAK", icon: "🔵", className: "status-break" };
  if (value === "WATCH") return { label: "WATCH", icon: "🟡", className: "status-watch" };
  if (value === "WAIT") return { label: "WAIT", icon: "🔴", className: "status-wait" };

  return { label: value || "WAIT", icon: value ? "⚪" : "🔴", className: "status-default" };
}

export default function StockCard({ stock }) {
  const gain = Number(stock.gain || 0);
  const last5AverageMove = stock.last_5_avg_daily_move;
  const showCalmMoveMarker = (
    last5AverageMove !== null
    && last5AverageMove !== undefined
    && Number.isFinite(Number(last5AverageMove))
    && Number(last5AverageMove) > -0.5
    && Number(last5AverageMove) < 0.5
  );
  const dailyMacd = stock.daily_macd;
  const dailyRsi = stock.daily_rsi;
  const showMacdMarker = (
    dailyMacd !== null
    && dailyMacd !== undefined
    && Number.isFinite(Number(dailyMacd))
    && Number(dailyMacd) < -10
  );
  const showRsiMarker = (
    dailyRsi !== null
    && dailyRsi !== undefined
    && Number.isFinite(Number(dailyRsi))
    && Number(dailyRsi) > 40
  );
  const status = getStatus(stock.category);
  const [showToday, setShowToday] = useState(false);
  const [showPriceDetails, setShowPriceDetails] = useState(false);

  return (
    <article className="card">
      <div className="cardTop">
        <div><strong>{stock.symbol}</strong></div>
        <div>
          <b>
            ₹{Number(stock.ltp || 0).toFixed(2)}
            {showCalmMoveMarker && (
              <span
                className="ltpIndicatorBadge ltpBadge5d"
                title={`5-session average daily move: ${Number(last5AverageMove).toFixed(2)}%`}
                aria-label="Five-session average daily move is between minus 0.5 and plus 0.5 percent"
              >5D</span>
            )}
            {showMacdMarker && (
              <span
                className="ltpIndicatorBadge ltpBadgeMacd"
                title={`Daily MACD (12, 26, 9): ${Number(dailyMacd).toFixed(2)} (< -10)`}
                aria-label={`Daily MACD is ${Number(dailyMacd).toFixed(2)}, below minus 10`}
              >M</span>
            )}
            {showRsiMarker && (
              <span
                className="ltpIndicatorBadge ltpBadgeRsi"
                title={`Daily RSI (14): ${Number(dailyRsi).toFixed(2)} (> 40)`}
                aria-label={`Daily RSI is ${Number(dailyRsi).toFixed(2)}, above 40`}
              >R</span>
            )}
          </b>
          <em className={gain >= 0 ? "up" : "down"}>{gain.toFixed(2)}%</em>
        </div>
      </div>

      <div
        className={`common-status ${status.className}`}
        onMouseEnter={() => setShowToday(true)}
        onMouseLeave={() => setShowToday(false)}
        aria-label={`${status.label}. Hover for today's intraday price and volume.`}
      >
        <span>{status.icon}</span>
        <span>{status.label}</span>
        {showToday && <TodayPopup stock={stock} />}
      </div>

      {stock.company_sector && (
        <div className="sectorSummary" title={`${stock.company_industry || stock.company_sector}${stock.sector_index ? ` · ${stock.sector_index}` : ""}${stock.sector_vs_nifty_5d_pct == null ? "" : ` · five-session sector performance versus Nifty ${Number(stock.sector_vs_nifty_5d_pct) > 0 ? "+" : ""}${Number(stock.sector_vs_nifty_5d_pct).toFixed(2)}%`}${Number(stock.sector_rating_adjustment || 0) ? ` · sector rating adjustment ${Number(stock.sector_rating_adjustment) > 0 ? "+" : ""}${Number(stock.sector_rating_adjustment)}` : ""}`}>
          <span>{stock.company_sector}</span>
          {stock.sector_index_daily_change_pct !== null && stock.sector_index_daily_change_pct !== undefined && (
            <b className={Number(stock.sector_index_daily_change_pct) >= 0 ? "up" : "down"}>
              {Number(stock.sector_index_daily_change_pct) > 0 ? "+" : ""}{Number(stock.sector_index_daily_change_pct).toFixed(2)}% T
            </b>
          )}
          {stock.sector_index_5d_change_pct !== null && stock.sector_index_5d_change_pct !== undefined && (
            <small className={Number(stock.sector_index_5d_change_pct) >= 0 ? "up" : "down"}>
              {Number(stock.sector_index_5d_change_pct) > 0 ? "+" : ""}{Number(stock.sector_index_5d_change_pct).toFixed(2)}% 5D
            </small>
          )}
          {Number(stock.sector_rating_adjustment || 0) !== 0 && (
            <small className={Number(stock.sector_rating_adjustment) > 0 ? "up" : "down"}>
              {Number(stock.sector_rating_adjustment) > 0 ? "+" : ""}{Number(stock.sector_rating_adjustment)}
            </small>
          )}
        </div>
      )}

      <div className="priceChartHeading">
        <label>PRICE</label>
        <div className="priceChartLegend" aria-label="Price graph lines">
          <span><i className="legendAverage5"/>5D</span>
          <span><i className="legendAverage10"/>10D</span>
          <button
            className="priceChartExpand"
            type="button"
            onClick={() => setShowPriceDetails(true)}
            aria-label={`Expand ${stock.symbol} one-month price graph`}
            title="Expand one-month price graph"
          >↗</button>
        </div>
      </div>
      <div className="chart"><PriceChart data={stock.price_history || []} /></div>

      <label>VOLUME</label>
      <div className="chart volume"><VolumeChart data={stock.volume_history || []} /></div>

      <Metrics stock={stock} />

      {showPriceDetails && (
        <div className="priceDetailBackdrop" onClick={() => setShowPriceDetails(false)}>
          <section
            className="priceDetailDialog"
            role="dialog"
            aria-modal="true"
            aria-label={`${stock.symbol} one-month price graph`}
            onClick={(event) => event.stopPropagation()}
          >
            <div className="priceDetailHeader">
              <div>
                <strong>{stock.symbol}</strong>
                <span> · 1 MONTH PRICE</span>
              </div>
              <button type="button" onClick={() => setShowPriceDetails(false)} aria-label="Close graph">×</button>
            </div>
            <div className="priceDetailChart">
              <PriceChart data={stock.price_history || []} expanded />
            </div>
          </section>
        </div>
      )}
    </article>
  );
}
