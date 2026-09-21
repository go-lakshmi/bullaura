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
  const status = getStatus(stock.category);
  const [showToday, setShowToday] = useState(false);

  return (
    <article className="card">
      <div className="cardTop">
        <div><strong>{stock.symbol}</strong></div>
        <div>
          <b>₹{Number(stock.ltp || 0).toFixed(2)}</b>
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

      <label>PRICE</label>
      <div className="chart"><PriceChart data={stock.price_history || []} /></div>

      <label>VOLUME</label>
      <div className="chart volume"><VolumeChart data={stock.volume_history || []} /></div>

      <Metrics stock={stock} />
    </article>
  );
}
