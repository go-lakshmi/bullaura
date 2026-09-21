import React from "react";
import { useEffect, useMemo, useState } from "react";
import StockCard from "./components/StockCard";
import StockTable from "./components/StockTable";

const API = "http://127.0.0.1:8000";

export default function App() {
  const [data, setData] = useState({
    stocks: [],
    updated_at: null,
  });

  const [q, setQ] = useState("");
  const [mode, setMode] = useState("cards");

  useEffect(() => {
    let disposed = false;

    fetch(API + "/api/stocks")
      .then((r) => r.json())
      .then((d) => {
        if (!disposed) setData(d);
      })
      .catch(() => {});

    const ws = new WebSocket("ws://127.0.0.1:8000/ws/stocks");

    ws.onmessage = (e) => {
      if (!disposed) setData(JSON.parse(e.data));
    };

    return () => {
      disposed = true;
      if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
        ws.close();
      }
    };
  }, []);

  const stocks = useMemo(
    () =>
      data.stocks.filter(
        (s) =>
          !q ||
          String(s.symbol || "").toLowerCase().includes(q.toLowerCase())
      ),
    [data.stocks, q]
  );

  return (
    <div className="app">
      <header>
        <div>
          <h1>Stock Shocker</h1>
          <p>Live decision dashboard</p>
        </div>
        <div className="status">
          {data.updated_at ? "LIVE" : "WAITING FOR ENGINE"}
        </div>
      </header>

      <div className="toolbar">
        <input
          placeholder="Search stock..."
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <button onClick={() => setMode("cards")} className={mode === "cards" ? "active" : ""}>
          Cards
        </button>
        <button onClick={() => setMode("table")} className={mode === "table" ? "active" : ""}>
          Table
        </button>
      </div>

      {mode === "cards" ? (
        <main className="grid">
          {stocks.map((s) => <StockCard key={s.symbol} stock={s} />)}
          {!stocks.length && (
            <div className="empty">
              The UI is ready. Start the configured Python engine to populate live stocks.
            </div>
          )}
        </main>
      ) : (
        <StockTable stocks={stocks} />
      )}
    </div>
  );
}
