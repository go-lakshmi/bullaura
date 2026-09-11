import React, {useMemo} from "react";
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  Cell,
  Tooltip,
  YAxis,
  CartesianGrid,
  XAxis,
} from "recharts";

function buildData(data) {
  const rows = (Array.isArray(data) ? data : [])
    .map((p) => ({
      ...p,
      actualValue: Number(p?.value),
    }))
    .filter((p) => Number.isFinite(p.actualValue) && p.actualValue >= 0);

  return rows.map((p, i) => {
    const previous = i > 0 ? rows[i - 1].actualValue : null;
    return {
      ...p,
      direction: previous === null ? "first" : p.actualValue > previous ? "up" : "down",
      changeFromPrevious: previous && previous > 0
        ? ((p.actualValue - previous) / previous) * 100
        : 0,
    };
  });
}

function VolumeTooltip({active, payload}) {
  if (!active || !payload?.length) return null;
  const p = payload[0]?.payload || {};
  const up = p.direction === "up";
  return (
    <div className="chartTooltip">
      <div className="chartTooltipDate">{p.date || ""}</div>
      <strong>Volume: {Number(p.actualValue || 0).toLocaleString("en-IN")}</strong>
      {p.direction !== "first" && (
        <div className={up ? "tooltipUp" : "tooltipDown"}>
          {up ? "↑" : "↓"} {Math.abs(Number(p.changeFromPrevious || 0)).toFixed(2)}% vs previous day
        </div>
      )}
    </div>
  );
}

export default function VolumeChart({data=[]}) {
  const chartData = useMemo(() => buildData(data), [data]);

  if (!chartData.length) return <div className="chartEmpty">No volume data</div>;

  const values = chartData.map((p) => p.actualValue);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const pad = max > min ? (max - min) * 0.08 : Math.max(max * 0.08, 1);
  const domainMin = Math.max(0, min - pad);
  const domainMax = max + pad;

  return (
    <ResponsiveContainer width="100%" height="100%" minWidth={0}>
      <BarChart data={chartData} margin={{top:3,right:1,left:1,bottom:0}}>
        <CartesianGrid vertical={false} stroke="#f0f2f5" strokeDasharray="3 3" />
        <XAxis dataKey="date" hide />
        <YAxis domain={[domainMin, domainMax]} hide />
        <Tooltip content={<VolumeTooltip />} cursor={{fill:"rgba(23,32,51,0.04)"}} />
        <Bar dataKey="actualValue" radius={[2,2,0,0]} isAnimationActive={false} maxBarSize={12}>
          {chartData.map((entry, index) => (
            <Cell
              key={`volume-cell-${index}`}
              fill={entry.direction === "up" ? "#16a34a" : entry.direction === "down" ? "#dc2626" : "#94a3b8"}
              fillOpacity={0.82}
            />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
