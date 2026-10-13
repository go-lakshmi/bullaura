import React, {useId, useMemo} from "react";
import {
  ResponsiveContainer,
  AreaChart,
  Area,
  Line,
  Tooltip,
  YAxis,
  XAxis,
  CartesianGrid,
} from "recharts";

function buildVisualData(data) {
  const rows = (Array.isArray(data) ? data : [])
    .map((p) => ({
      ...p,
      actualValue: Number(p?.value),
    }))
    .filter((p) => Number.isFinite(p.actualValue) && p.actualValue > 0);

  if (!rows.length) return [];

  const min = Math.min(...rows.map((p) => p.actualValue));
  const max = Math.max(...rows.map((p) => p.actualValue));
  const range = max - min;

  // Normalize only the DRAWING value. The tooltip always shows the real NSE /
  // Angel One price. This makes even small price movements visually obvious.
  const toVisual = (value) => (
    value == null
      ? null
      : range > 0
        ? ((value - min) / range) * 100
        : 50
  );

  return rows.map((p, index) => {
    const average = (period) => {
      if (index + 1 < period) return null;
      const window = rows.slice(index - period + 1, index + 1);
      return window.reduce((sum, point) => sum + point.actualValue, 0) / period;
    };
    const average5 = average(5);
    const average10 = average(10);

    return {
      ...p,
      visualValue: toVisual(p.actualValue),
      average5,
      average10,
      visualAverage5: toVisual(average5),
      visualAverage10: toVisual(average10),
      changeFromFirst: ((p.actualValue - rows[0].actualValue) / rows[0].actualValue) * 100,
    };
  });
}

function PriceTooltip({active, payload}) {
  if (!active || !payload?.length) return null;
  const p = payload[0]?.payload || {};
  return (
    <div className="chartTooltip">
      <div className="chartTooltipDate">{p.date || ""}</div>
      <strong>Price: ₹{Number(p.actualValue || 0).toFixed(2)}</strong>
      {p.average5 != null && <div style={{color:"#f59e0b"}}>5-session avg: ₹{p.average5.toFixed(2)}</div>}
      {p.average10 != null && <div style={{color:"#8b5cf6"}}>10-session avg: ₹{p.average10.toFixed(2)}</div>}
      <div className={Number(p.changeFromFirst || 0) >= 0 ? "tooltipUp" : "tooltipDown"}>
        {Number(p.changeFromFirst || 0) >= 0 ? "+" : ""}{Number(p.changeFromFirst || 0).toFixed(2)}% from first day
      </div>
    </div>
  );
}

export default function PriceChart({data=[], expanded=false}) {
  const gradientId = useId().replace(/:/g, "");
  const chartData = useMemo(() => buildVisualData(data), [data]);

  if (!chartData.length) return <div className="chartEmpty">No price data</div>;

  const priceKey = expanded ? "actualValue" : "visualValue";
  const average5Key = expanded ? "average5" : "visualAverage5";
  const average10Key = expanded ? "average10" : "visualAverage10";

  return (
    <ResponsiveContainer width="100%" height="100%" minWidth={0}>
      <AreaChart data={chartData} margin={expanded ? {top:12,right:18,left:8,bottom:4} : {top:8,right:2,left:2,bottom:2}}>
        <defs>
          <linearGradient id={`${gradientId}-priceFill`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#2f80ed" stopOpacity={0.24}/>
            <stop offset="100%" stopColor="#2f80ed" stopOpacity={0.02}/>
          </linearGradient>
        </defs>
        <CartesianGrid vertical={false} stroke="#edf1f5" strokeDasharray="3 3" />
        <YAxis
          domain={expanded ? ["auto", "auto"] : [0,100]}
          hide={!expanded}
          width={expanded ? 58 : 0}
          tick={{fontSize:11, fill:"#687385"}}
          tickFormatter={(value) => `₹${Number(value).toFixed(0)}`}
        />
        {expanded && (
          <XAxis
            dataKey="date"
            tick={{fontSize:10, fill:"#687385"}}
            tickFormatter={(value) => String(value || "").slice(5)}
            minTickGap={28}
            tickLine={false}
            axisLine={{stroke:"#d8dde5"}}
          />
        )}
        <Tooltip content={<PriceTooltip />} cursor={{stroke:"#aab4c2",strokeDasharray:"3 3"}} />
        <Area
          type="monotoneX"
          dataKey={priceKey}
          stroke="#2f80ed"
          strokeWidth={2.4}
          fill={`url(#${gradientId}-priceFill)`}
          baseValue={expanded ? "dataMin" : 0}
          dot={false}
          activeDot={{r:4, strokeWidth:2, fill:"#fff"}}
          isAnimationActive={false}
        />
        <Line
          type="monotoneX"
          dataKey={average5Key}
          stroke="#f59e0b"
          strokeWidth={1.6}
          dot={false}
          activeDot={false}
          connectNulls={false}
          isAnimationActive={false}
        />
        <Line
          type="monotoneX"
          dataKey={average10Key}
          stroke="#8b5cf6"
          strokeWidth={1.6}
          dot={false}
          activeDot={false}
          connectNulls={false}
          isAnimationActive={false}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}
