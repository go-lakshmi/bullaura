import React, {useId, useMemo} from "react";
import {
  ResponsiveContainer,
  AreaChart,
  Area,
  Tooltip,
  YAxis,
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
  return rows.map((p) => ({
    ...p,
    visualValue: range > 0 ? ((p.actualValue - min) / range) * 100 : 50,
    changeFromFirst: ((p.actualValue - rows[0].actualValue) / rows[0].actualValue) * 100,
  }));
}

function PriceTooltip({active, payload}) {
  if (!active || !payload?.length) return null;
  const p = payload[0]?.payload || {};
  return (
    <div className="chartTooltip">
      <div className="chartTooltipDate">{p.date || ""}</div>
      <strong>Price: ₹{Number(p.actualValue || 0).toFixed(2)}</strong>
      <div className={Number(p.changeFromFirst || 0) >= 0 ? "tooltipUp" : "tooltipDown"}>
        {Number(p.changeFromFirst || 0) >= 0 ? "+" : ""}{Number(p.changeFromFirst || 0).toFixed(2)}% from first day
      </div>
    </div>
  );
}

export default function PriceChart({data=[]}) {
  const gradientId = useId().replace(/:/g, "");
  const chartData = useMemo(() => buildVisualData(data), [data]);

  if (!chartData.length) return <div className="chartEmpty">No price data</div>;

  return (
    <ResponsiveContainer width="100%" height="100%" minWidth={0}>
      <AreaChart data={chartData} margin={{top:8,right:2,left:2,bottom:2}}>
        <defs>
          <linearGradient id={`${gradientId}-priceFill`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#2f80ed" stopOpacity={0.24}/>
            <stop offset="100%" stopColor="#2f80ed" stopOpacity={0.02}/>
          </linearGradient>
        </defs>
        <CartesianGrid vertical={false} stroke="#edf1f5" strokeDasharray="3 3" />
        <YAxis domain={[0,100]} hide />
        <Tooltip content={<PriceTooltip />} cursor={{stroke:"#aab4c2",strokeDasharray:"3 3"}} />
        <Area
          type="monotoneX"
          dataKey="visualValue"
          stroke="#2f80ed"
          strokeWidth={2.4}
          fill={`url(#${gradientId}-priceFill)`}
          dot={false}
          activeDot={{r:4, strokeWidth:2, fill:"#fff"}}
          isAnimationActive={false}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}
