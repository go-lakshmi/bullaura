import React from "react";

export default function StockTable({stocks=[]}) {
  return <div className="tableWrap"><table><thead><tr><th>Stock</th><th>Status</th><th>LTP</th><th>Gain</th><th>RVOL</th><th>BUY%</th><th>Rating</th><th>5D Avg Vol</th></tr></thead><tbody>{stocks.map(s=><tr key={s.symbol}><td>{s.symbol}</td><td>{s.category || "WAIT"}</td><td>₹{Number(s.ltp||0).toFixed(2)}</td><td>{Number(s.gain||0).toFixed(2)}%</td><td>{Number(s.rvol||0).toFixed(2)}x</td><td>{(Number(s.buying_pressure||0)*100).toFixed(0)}</td><td>{Number(s.rating||0).toFixed(1)}</td><td>{Number(s.metrics?.avg_volume_5||0).toLocaleString("en-IN")}</td></tr>)}</tbody></table></div>;
}
