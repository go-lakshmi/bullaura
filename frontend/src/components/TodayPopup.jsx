import React, { useMemo } from "react";
import {
  ResponsiveContainer,
  ComposedChart,
  LineChart,
  Line,
  BarChart,
  Bar,
  Cell,
  Tooltip,
  YAxis,
  CartesianGrid,
  XAxis,
  ReferenceLine,
  Customized,
} from "recharts";

const MARKET_OPEN = 9 * 60 + 15;
const MARKET_CLOSE = 15 * 60 + 30;

function timeToMinutes(value) {
  const match = String(value || "").match(/(?:T|\s)?(\d{1,2}):(\d{2})/);
  if (!match) return null;
  const total = Number(match[1]) * 60 + Number(match[2]);
  return Number.isFinite(total) && total >= MARKET_OPEN && total <= MARKET_CLOSE
    ? total
    : null;
}

function withTimeAxis(data) {
  return data.map((point) => {
    const x = timeToMinutes(point?.date);
    return x == null ? null : { ...point, x, timeLabel: String(point?.minute || point?.date || "") };
  }).filter(Boolean).sort((a, b) => a.x - b.x);
}

function PriceTooltip({ active, payload }) {
  if (!active || !payload?.length) return null;
  const p = payload[0]?.payload || {};
  if (p.open == null) return <div className="todayTooltip">{p.timeLabel || ""}<br />₹{Number(p.value || 0).toFixed(2)}</div>;
  return (
    <div className="todayTooltip">
      <div className="todayTooltipTime">{p.start}–{p.end}</div>
      <div>O ₹{Number(p.open).toFixed(2)}　H ₹{Number(p.high).toFixed(2)}</div>
      <div>L ₹{Number(p.low).toFixed(2)}　C ₹{Number(p.close).toFixed(2)}</div>
      <div>Vol {Number(p.volume || 0).toLocaleString("en-IN")}</div>
    </div>
  );
}

function VolumeTooltip({ active, payload }) {
  if (!active || !payload?.length) return null;
  const p = payload[0]?.payload || {};
  return <div className="todayTooltip"><div className="todayTooltipTime">{p.start || p.timeLabel || ""}</div><strong>{Number(p.value || p.volume || 0).toLocaleString("en-IN")}</strong></div>;
}

function findAxisScale(axisMap) {
  const axis = Object.values(axisMap || {})[0];
  return axis?.scale;
}

export default function TodayPopup({ stock }) {
  const candles = Array.isArray(stock.today_candles_15m) ? stock.today_candles_15m : [];
  const rawPriceData = Array.isArray(stock.today_price_history) ? stock.today_price_history : [];
  const rawVolumeData = Array.isArray(stock.today_volume_history) ? stock.today_volume_history : [];
  const priceData = useMemo(() => withTimeAxis(rawPriceData), [rawPriceData]);
  const volumeData = useMemo(() => withTimeAxis(rawVolumeData), [rawVolumeData]);
  const candleVolumeData = useMemo(() => candles.map((candle) => ({
    ...candle,
    value: Number(candle.volume || 0),
  })), [candles]);
  const levels = stock.today_support_resistance || {};
  const setupLabel = levels.setup_label || levels.status || "WAIT";
  const setupTone = setupLabel.includes("CONFIRMED")
    ? "confirmed"
    : setupLabel.includes("SUPPORT TEST") || setupLabel.includes("RESISTANCE TEST") || setupLabel.includes("WAIT FOR CLOSE")
      ? "watch"
      : setupLabel.includes("BELOW SUPPORT")
        ? "invalid"
        : "wait";
  const sessionDomain = [MARKET_OPEN, MARKET_CLOSE];
  const metrics = stock.metrics || {};
  const hasData = candles.length > 0 || priceData.length > 0 || volumeData.length > 0;
  const candlePrices = candles.flatMap((candle) => [Number(candle.low), Number(candle.high)]).filter((value) => value > 0);
  const priceDomain = candlePrices.length
    ? [Math.min(...candlePrices, Number(levels.support) || Infinity), Math.max(...candlePrices, Number(levels.resistance) || -Infinity)]
    : ["auto", "auto"];

  const drawCandles = ({ xAxisMap, yAxisMap }) => {
    const xScale = findAxisScale(xAxisMap);
    const yScale = findAxisScale(yAxisMap);
    if (!xScale || !yScale) return null;
    return (
      <g className="todayCandles">
        {candles.map((candle, index) => {
          const x = xScale(Number(candle.x));
          const high = yScale(Number(candle.high));
          const low = yScale(Number(candle.low));
          const open = yScale(Number(candle.open));
          const close = yScale(Number(candle.close));
          const color = Number(candle.close) >= Number(candle.open) ? "#16a34a" : "#ef4444";
          const bodyWidth = Math.max(2, Math.abs(xScale(Number(candle.x) + 7.5) - xScale(Number(candle.x) - 7.5)) * 0.55);
          const bodyTop = Math.min(open, close);
          const bodyHeight = Math.max(1.5, Math.abs(close - open));
          return (
            <g key={`${candle.start}-${index}`}>
              <line x1={x} x2={x} y1={high} y2={low} stroke={color} strokeWidth={1.2} />
              <rect x={x - bodyWidth / 2} y={bodyTop} width={bodyWidth} height={bodyHeight} fill={color} stroke={color} strokeWidth={0.8} />
            </g>
          );
        })}
      </g>
    );
  };

  return (
    <div className="todayPopup" aria-hidden="true">
      <div className="todayPopupTitle">{stock.symbol} · TODAY · 15M</div>
      {!hasData ? (
        <div className="todayPopupEmpty">Waiting for today's live data</div>
      ) : (
        <>
          <div className="todaySectionLabel">{candles.length ? "OHLC CANDLES" : "LIVE PRICE · CANDLES BUILDING"}</div>
          <div className="todayChart todayPriceChart">
            {candles.length ? (
              <ResponsiveContainer width="100%" height="100%" minWidth={0}>
                <ComposedChart data={candles} margin={{ top: 5, right: 2, left: 2, bottom: 0 }}>
                  <CartesianGrid vertical={false} stroke="#edf1f5" strokeDasharray="3 3" />
                  <XAxis dataKey="x" type="number" domain={sessionDomain} hide allowDataOverflow />
                  <YAxis domain={priceDomain} hide allowDataOverflow />
                  <Tooltip content={<PriceTooltip />} cursor={{ stroke: "#aab4c2", strokeDasharray: "3 3" }} />
                  {levels.support != null && <ReferenceLine y={Number(levels.support)} stroke="#16a34a" strokeDasharray="4 3" label={{ value: "S", fill: "#16a34a", fontSize: 8, position: "insideBottomRight" }} />}
                  {levels.resistance != null && <ReferenceLine y={Number(levels.resistance)} stroke="#ef4444" strokeDasharray="4 3" label={{ value: "R", fill: "#ef4444", fontSize: 8, position: "insideTopRight" }} />}
                  <Line dataKey="close" stroke="transparent" activeDot={{ r: 3, fill: "#fff", stroke: "#64748b" }} isAnimationActive={false} />
                  <Customized component={drawCandles} />
                </ComposedChart>
              </ResponsiveContainer>
            ) : (
              <ResponsiveContainer width="100%" height="100%" minWidth={0}>
                <LineChart data={priceData} margin={{ top: 3, right: 0, left: 0, bottom: 0 }}>
                  <CartesianGrid vertical={false} stroke="#edf1f5" strokeDasharray="3 3" />
                  <YAxis domain={["auto", "auto"]} hide />
                  <XAxis dataKey="x" type="number" domain={sessionDomain} hide allowDataOverflow />
                  <Tooltip content={<PriceTooltip />} />
                  <Line type="monotoneX" dataKey="value" stroke="#2f80ed" strokeWidth={2} dot={false} isAnimationActive={false} />
                </LineChart>
              </ResponsiveContainer>
            )}
          </div>

          <div className={`todaySetupSummary todaySetup-${setupTone}`}>
            <div className="todaySetupHeading">
              <span>PRICE + VOLUME READ</span>
              <strong>{setupLabel}</strong>
            </div>
            <p>{levels.setup_reason || "Waiting for enough completed candles to assess the setup."}</p>
            <div className="todayLevelSummary">
              <span>Support <strong>{levels.support != null ? `₹${Number(levels.support).toFixed(2)}` : "—"}</strong></span>
              <span>Resistance <strong>{levels.resistance != null ? `₹${Number(levels.resistance).toFixed(2)}` : "—"}</strong></span>
            </div>
            {levels.invalidation_level != null && (
              <small className="todayInvalidation">
                {levels.invalidation_label || "Level to watch"} ₹{Number(levels.invalidation_level).toFixed(2)}
              </small>
            )}
            <small className="todaySetupNote">Price and volume context only; not a guaranteed entry.</small>
          </div>

          <div className="todaySectionLabel">15M VOLUME</div>
          <div className="todayChart todayVolumeChart">
            {(candleVolumeData.length ? candleVolumeData : volumeData).length ? (
              <ResponsiveContainer width="100%" height="100%" minWidth={0}>
                <BarChart data={candleVolumeData.length ? candleVolumeData : volumeData} margin={{ top: 2, right: 0, left: 0, bottom: 0 }}>
                  <CartesianGrid vertical={false} stroke="#f0f2f5" strokeDasharray="3 3" />
                  <XAxis dataKey="x" type="number" domain={sessionDomain} hide allowDataOverflow />
                  <YAxis domain={[0, "auto"]} hide />
                  <Tooltip content={<VolumeTooltip />} cursor={{ fill: "rgba(23,32,51,0.04)" }} />
                  <Bar dataKey="value" radius={[2, 2, 0, 0]} isAnimationActive={false} barSize={5}>
                    {(candleVolumeData.length ? candleVolumeData : volumeData).map((entry, index) => <Cell key={`today-volume-${index}`} fill={entry.close == null || entry.close >= entry.open ? "#16a34a" : "#ef4444"} fillOpacity={0.82} />)}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            ) : <div className="todayPopupEmpty">No volume data</div>}
          </div>

          <div className="todayStats">
            <span>Open <b>₹{Number(metrics.day_open || 0).toFixed(2)}</b></span>
            <span>High <b>₹{Number(metrics.day_high || 0).toFixed(2)}</b></span>
            <span>Low <b>₹{Number(metrics.day_low || 0).toFixed(2)}</b></span>
            <span>LTP <b>₹{Number(stock.ltp || 0).toFixed(2)}</b></span>
          </div>
        </>
      )}
    </div>
  );
}
