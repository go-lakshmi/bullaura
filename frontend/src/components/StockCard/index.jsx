import React from "react";
import PriceChart from "../PriceChart"; import VolumeChart from "../VolumeChart"; import Metrics from "../Metrics";
export default function StockCard({stock}) {
  const gain=Number(stock.gain||0);
  return <article className="card">
    <div className="cardTop"><div><strong>{stock.symbol}</strong><span>{stock.signal_channel ? `${stock.signal_channel} ${stock.recommendation || "WATCH"}` : (stock.recommendation||stock.btst_signal||stock.swing_signal||"WATCH")}</span></div><div><b>₹{Number(stock.ltp||0).toFixed(2)}</b><em className={gain>=0?"up":"down"}>{gain.toFixed(2)}%</em></div></div>
    <label>PRICE</label><div className="chart"><PriceChart data={stock.price_history||[]}/></div>
    <label>VOLUME</label><div className="chart volume"><VolumeChart data={stock.volume_history||[]}/></div>
    <Metrics stock={stock}/>
  </article>;
}
