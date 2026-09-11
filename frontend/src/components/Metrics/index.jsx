import React from "react";
export default function Metrics({stock}) {
  const m=stock.metrics||{};
  return <div className="metrics">
    <div><small>RVOL</small><b>{Number(stock.rvol||m.rvol||0).toFixed(2)}x</b></div>
    <div><small>BUY PRESSURE</small><b>{(Number(stock.buying_pressure||0)*100).toFixed(0)}%</b></div>
    <div><small>RATING</small><b>{Number(stock.rating||0).toFixed(1)}</b></div>
    <div><small>5D AVG VOL</small><b>{Number(m.avg_volume_5 || 0).toLocaleString("en-IN")}</b></div>
  </div>;
}
