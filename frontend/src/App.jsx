import React from "react";
import { useEffect, useMemo, useState } from "react";
import StockCard from "./components/StockCard";
import StockTable from "./components/StockTable";
import {
  getBreakoutRetestSignal,
  getDojiSignal,
  getEngulfingSignal,
  getHammerSignal,
  getMorningStarSignal,
  getVolumeSurgeSignal,
} from "./utils/candleSignals";

const API = "http://127.0.0.1:8000";

function getStockSortTier(stock) {
  if (getDojiSignal(stock)) return -6;
  if (getVolumeSurgeSignal(stock)) return -5;
  if (getBreakoutRetestSignal(stock)) return -4;
  const patternSignals = [
    getEngulfingSignal(stock),
    getMorningStarSignal(stock),
    getHammerSignal(stock),
  ].filter(Boolean);
  if (patternSignals.some((signal) => signal.supported)) return -3;
  if (patternSignals.length) return -2;

  const isBuyOrBreak = [stock.status, stock.category, stock.recommendation].some(
    (candidate) => {
      const value = String(candidate || "")
        .toUpperCase()
        .replace(/^[^A-Z]*/, "")
        .trim();
      return value === "BUY" || value === "BREAK";
    }
  );
  if (isBuyOrBreak) return 0;

  const setup = String(stock.today_support_resistance?.setup_label || "")
    .toUpperCase()
    .trim();
  if (setup === "BOUNCE CONFIRMED") return 1;
  if (setup === "SUPPORT TEST") return 2;
  return 3;
}

function getPatternContextRank(stock) {
  return Math.max(
    0,
    getEngulfingSignal(stock)?.contextRank || 0,
    getMorningStarSignal(stock)?.contextRank || 0,
    getHammerSignal(stock)?.contextRank || 0,
  );
}

export default function App() {
  const [data, setData] = useState({
    stocks: [],
    new_stocks: [],
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
      data.stocks
        .filter(
          (s) =>
            !q ||
            String(s.symbol || "").toLowerCase().includes(q.toLowerCase())
        )
        .map((stock, index) => ({ stock, index }))
        .sort((a, b) => {
          const tierDifference = getStockSortTier(a.stock) - getStockSortTier(b.stock);
          if (tierDifference !== 0) return tierDifference;

          const contextDifference = getPatternContextRank(b.stock) - getPatternContextRank(a.stock);
          if (contextDifference !== 0) return contextDifference;

          // Within each priority tier, keep the existing gain/sector ordering.
          const gainDifference =
            Number(b.stock.gain ?? 0) - Number(a.stock.gain ?? 0);
          if (gainDifference !== 0) return gainDifference;

          const sectorAdjustmentDifference =
            Number(b.stock.sector_rating_adjustment ?? 0) -
            Number(a.stock.sector_rating_adjustment ?? 0);
          if (sectorAdjustmentDifference !== 0) return sectorAdjustmentDifference;

          const aSectorFiveDay = Number(a.stock.sector_index_5d_change_pct);
          const bSectorFiveDay = Number(b.stock.sector_index_5d_change_pct);
          const aHasSectorFiveDay = Number.isFinite(aSectorFiveDay);
          const bHasSectorFiveDay = Number.isFinite(bSectorFiveDay);
          if (aHasSectorFiveDay !== bHasSectorFiveDay) {
            return aHasSectorFiveDay ? -1 : 1;
          }
          if (aHasSectorFiveDay && bSectorFiveDay !== aSectorFiveDay) {
            return bSectorFiveDay - aSectorFiveDay;
          }

          return a.index - b.index;
        })
        .map(({ stock }) => stock),
    [data.stocks, q]
  );

  const newStocks = useMemo(
    () =>
      (data.new_stocks || [])
        .filter(
          (s) =>
            !q ||
            String(s.symbol || "").toLowerCase().includes(q.toLowerCase())
        )
        .map((stock, index) => ({ stock, index }))
        .sort((a, b) => {
          const tierDifference = getStockSortTier(a.stock) - getStockSortTier(b.stock);
          if (tierDifference !== 0) return tierDifference;
          const gainDifference =
            Number(b.stock.gain ?? 0) - Number(a.stock.gain ?? 0);
          if (gainDifference !== 0) return gainDifference;
          const sectorAdjustmentDifference =
            Number(b.stock.sector_rating_adjustment ?? 0) -
            Number(a.stock.sector_rating_adjustment ?? 0);
          if (sectorAdjustmentDifference !== 0) return sectorAdjustmentDifference;
          const aSectorFiveDay = Number(a.stock.sector_index_5d_change_pct);
          const bSectorFiveDay = Number(b.stock.sector_index_5d_change_pct);
          const aHasSectorFiveDay = Number.isFinite(aSectorFiveDay);
          const bHasSectorFiveDay = Number.isFinite(bSectorFiveDay);
          if (aHasSectorFiveDay !== bHasSectorFiveDay) {
            return aHasSectorFiveDay ? -1 : 1;
          }
          if (aHasSectorFiveDay && bSectorFiveDay !== aSectorFiveDay) {
            return bSectorFiveDay - aSectorFiveDay;
          }
          return a.index - b.index;
        })
        .map(({ stock }) => stock),
    [data.new_stocks, q]
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
        <button onClick={() => setMode("cards2")} className={mode === "cards2" ? "active" : ""}>
          Cards 2 (101–300)
        </button>
        <button onClick={() => setMode("new-stocks")} className={mode === "new-stocks" ? "active" : ""}>
          New Stocks ({newStocks.length})
        </button>
        <button onClick={() => setMode("table")} className={mode === "table" ? "active" : ""}>
          Table
        </button>
      </div>

      {mode === "cards" || mode === "cards2" || mode === "new-stocks" ? (
        <main className="grid">
          {(mode === "new-stocks"
            ? newStocks
            : mode === "cards"
              ? stocks.slice(0, 100)
              : stocks.slice(100, 300)
          ).map((s) => <StockCard key={s.symbol} stock={s} />)}
          {!stocks.length && mode === "cards" && (
            <div className="empty">
              The UI is ready. Start the configured Python engine to populate live stocks.
            </div>
          )}
          {mode === "cards2" && stocks.length <= 100 && (
            <div className="empty">No stocks ranked 101–300 are available right now.</div>
          )}
          {mode === "new-stocks" && newStocks.length === 0 && (
            <div className="empty">No short-history stocks have live data right now.</div>
          )}
        </main>
      ) : (
        <StockTable stocks={stocks.slice(0, 100)} />
      )}
    </div>
  );
}
