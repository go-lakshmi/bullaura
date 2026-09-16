import React from "react";
import { useEffect, useMemo, useState } from "react";
import StockCard from "./components/StockCard";
import StockTable from "./components/StockTable";

const API = "http://127.0.0.1:8000";

function getStatus(category) {
  const value = String(category || "").toUpperCase().trim();

  if (value === "BUY") {
    return {
      label: "BUY",
      icon: "🟢",
      className: "status-buy",
    };
  }

  if (value === "BREAK") {
    return {
      label: "BREAK",
      icon: "🔵",
      className: "status-break",
    };
  }

  if (value === "WATCH") {
    return {
      label: "WATCH",
      icon: "🟡",
      className: "status-watch",
    };
  }

  if (value === "WAIT") {
    return {
      label: "WAIT",
      icon: "🔴",
      className: "status-wait",
    };
  }

  return {
    label: value || "WAIT",
    icon: value ? "⚪" : "🔴",
    className: "status-default",
  };
}

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
        if (!disposed) {
          setData(d);
        }
      })
      .catch(() => {});

    const ws = new WebSocket("ws://127.0.0.1:8000/ws/stocks");

    ws.onmessage = (e) => {
      if (!disposed) {
        setData(JSON.parse(e.data));
      }
    };

    return () => {
      disposed = true;

      if (
        ws.readyState === WebSocket.OPEN ||
        ws.readyState === WebSocket.CONNECTING
      ) {
        ws.close();
      }
    };
  }, []);

  const stocks = useMemo(
    () =>
      data.stocks.filter(
        (s) =>
          !q ||
          String(s.symbol || "")
            .toLowerCase()
            .includes(q.toLowerCase())
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

        <button
          onClick={() => setMode("cards")}
          className={mode === "cards" ? "active" : ""}
        >
          Cards
        </button>

        <button
          onClick={() => setMode("table")}
          className={mode === "table" ? "active" : ""}
        >
          Table
        </button>
      </div>

      {mode === "cards" ? (
        <main className="grid">
          {stocks.map((s) => {
            const status = getStatus(s.category);

            return (
              <div
                key={s.symbol}
                className="stock-card-wrapper"
              >
                <div className={`common-status ${status.className}`}>
                  <span>{status.icon}</span>
                  <span>{status.label}</span>
                </div>

                <StockCard stock={s} />
              </div>
            );
          })}

          {!stocks.length && (
            <div className="empty">
              The UI is ready. Start the configured Python engine to
              populate live stocks.
            </div>
          )}
        </main>
      ) : (
        <StockTable stocks={stocks} />
      )}

      <style>{`
        .stock-card-wrapper {
          position: relative;
        }

        .common-status {
          position: absolute;
          top: 8px;
          right: 8px;
          z-index: 10;

          display: flex;
          align-items: center;
          gap: 4px;

          padding: 4px 8px;

          border-radius: 999px;

          font-size: 11px;
          font-weight: 700;

          background: white;
          border: 1px solid #e5e7eb;

          box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08);
        }

        .status-buy {
          color: #047857;
          border-color: #86efac;
          background: #f0fdf4;
        }

        .status-break {
          color: #1d4ed8;
          border-color: #93c5fd;
          background: #eff6ff;
        }

        .status-watch {
          color: #a16207;
          border-color: #fde68a;
          background: #fefce8;
        }

        .status-wait {
          color: #b91c1c;
          border-color: #fca5a5;
          background: #fef2f2;
        }

        .status-default {
          color: #475569;
          border-color: #cbd5e1;
          background: #f8fafc;
        }
      `}</style>
    </div>
  );
}