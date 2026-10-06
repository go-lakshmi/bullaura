import React, { useMemo } from "react";
import {
  ResponsiveContainer,
  LineChart,
  Line,
  BarChart,
  Bar,
  Cell,
  Tooltip,
  YAxis,
  CartesianGrid,
  XAxis,
} from "recharts";

const MARKET_OPEN = 9 * 60 + 15;   // 09:15
const MARKET_CLOSE = 15 * 60 + 30; // 15:30

function timeToMinutes(value) {
  const text = String(value || "");
  const match = text.match(/(?:T|\s)?(\d{1,2}):(\d{2})/);
  if (!match) return null;
  const hours = Number(match[1]);
  const minutes = Number(match[2]);
  if (!Number.isFinite(hours) || !Number.isFinite(minutes)) return null;
  const total = hours * 60 + minutes;
  return total >= MARKET_OPEN && total <= MARKET_CLOSE ? total : null;
}

function withTimeAxis(data) {
  return data
    .map((point) => {
      const x = timeToMinutes(point?.date);
      if (x == null) return null;
      return { ...point, x, timeLabel: String(point?.minute || point?.date || "") };
    })
    .filter(Boolean)
    .sort((a, b) => a.x - b.x);
}

function PriceTooltip({ active, payload }) {
  if (!active || !payload?.length) return null;
  const p = payload[0]?.payload || {};
  return (
    <div className="todayTooltip">
      <div className="todayTooltipTime">{p.timeLabel || p.date || ""}</div>
      <strong>₹{Number(p.value || 0).toFixed(2)}</strong>
    </div>
  );
}

function VolumeTooltip({ active, payload }) {
  if (!active || !payload?.length) return null;
  const p = payload[0]?.payload || {};
  return (
    <div className="todayTooltip">
      <div className="todayTooltipTime">{p.timeLabel || p.date || ""}</div>
      <strong>{Number(p.value || 0).toLocaleString("en-IN")}</strong>
    </div>
  );
}

export default function TodayPopup({ stock }) {
  const rawPriceData = Array.isArray(stock.today_price_history)
    ? stock.today_price_history
    : [];
  const rawVolumeData = Array.isArray(stock.today_volume_history)
    ? stock.today_volume_history
    : [];

  // Use a fixed NSE session timeline. Data is plotted only where observations
  // exist; the remaining 09:15-15:30 space stays empty until that time arrives.
  const priceData = useMemo(() => withTimeAxis(rawPriceData), [rawPriceData]);
  const volumeData = useMemo(() => withTimeAxis(rawVolumeData), [rawVolumeData]);
  // Always keep the full 09:15-15:30 timeline. This is what makes the
  // intraday line expand naturally as the market moves through the session.
  const sessionDomain = [MARKET_OPEN, MARKET_CLOSE];

  const metrics = stock.metrics || {};
  const hasData = priceData.length > 0 || volumeData.length > 0;

  return (
    <div className="todayPopup" aria-hidden="true">
      <div className="todayPopupTitle">{stock.symbol} · TODAY</div>

      {!hasData ? (
        <div className="todayPopupEmpty">Waiting for today's live data</div>
      ) : (
        <>
          <div className="todaySectionLabel">PRICE</div>
          <div className="todayChart todayPriceChart">
            {priceData.length > 0 ? (
              <ResponsiveContainer width="100%" height="100%" minWidth={0}>
                <LineChart data={priceData} margin={{ top: 3, right: 0, left: 0, bottom: 0 }}>
                  <CartesianGrid vertical={false} stroke="#edf1f5" strokeDasharray="3 3" />
                  <YAxis domain={["auto", "auto"]} hide />
                  <XAxis
                    dataKey="x"
                    type="number"
                    domain={sessionDomain}
                    hide
                    allowDataOverflow
                  />
                  <Tooltip content={<PriceTooltip />} cursor={{ stroke: "#aab4c2", strokeDasharray: "3 3" }} />
                  <Line
                    type="monotoneX"
                    dataKey="value"
                    stroke="#2f80ed"
                    strokeWidth={2}
                    dot={false}
                    activeDot={{ r: 3, strokeWidth: 1, fill: "#fff" }}
                    isAnimationActive={false}
                    connectNulls={false}
                  />
                </LineChart>
              </ResponsiveContainer>
            ) : (
              <div className="todayPopupEmpty">No price data</div>
            )}
          </div>

          <div className="todaySectionLabel">VOLUME</div>
          <div className="todayChart todayVolumeChart">
            {volumeData.length > 0 ? (
              <ResponsiveContainer width="100%" height="100%" minWidth={0}>
                <BarChart data={volumeData} margin={{ top: 2, right: 0, left: 0, bottom: 0 }}>
                  <CartesianGrid vertical={false} stroke="#f0f2f5" strokeDasharray="3 3" />
                  <XAxis
                    dataKey="x"
                    type="number"
                    domain={sessionDomain}
                    hide
                    allowDataOverflow
                  />
                  <YAxis domain={[0, "auto"]} hide />
                  <Tooltip content={<VolumeTooltip />} cursor={{ fill: "rgba(23,32,51,0.04)" }} />
                  <Bar dataKey="value" radius={[2, 2, 0, 0]} isAnimationActive={false} barSize={2}>
                    {volumeData.map((entry, index) => (
                      <Cell
                        key={`today-volume-${index}`}
                        fill={entry.value > 0 ? "#16a34a" : "#94a3b8"}
                        fillOpacity={0.82}
                      />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            ) : (
              <div className="todayPopupEmpty">No volume data</div>
            )}
          </div>

          <div className="todayStats">
            <span>Open <b>₹{Number(metrics.day_open || 0).toFixed(2)}</b></span>
            <span>High <b>₹{Number(metrics.day_high || 0).toFixed(2)}</b></span>
            <span>Low <b>₹{Number(metrics.day_low || 0).toFixed(2)}</b></span>
            <span>Close <b>₹{Number(stock.ltp || 0).toFixed(2)}</b></span>
          </div>
        </>
      )}
    </div>
  );
}
