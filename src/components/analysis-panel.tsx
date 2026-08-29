import { useMemo, useState } from "react";
import type { MarketAnalysis } from "@/lib/analysis";

function fmt(value: number | null, digits = 2) {
  if (value === null || Number.isNaN(value)) return "—";
  return value.toFixed(digits);
}

function pct(value: number | null) {
  if (value === null || Number.isNaN(value)) return "—";
  const signed = value >= 0 ? "+" : "";
  return `${signed}${(value * 100).toFixed(2)}%`;
}

export function AnalysisPanel({ rows }: { rows: MarketAnalysis[] }) {
  const [symbol, setSymbol] = useState(rows[0]?.symbol ?? "");
  const active = useMemo(
    () => rows.find((row) => row.symbol === symbol) ?? rows[0],
    [rows, symbol],
  );

  if (!rows.length || !active) {
    return (
      <section className="desk-card mb-12">
        <h2 className="font-display m-0 text-[clamp(1.6rem,4vw,2.25rem)] leading-tight font-medium tracking-[-0.03em]">
          Analysis
        </h2>
        <p className="mt-2 text-sm text-muted">
          No analysis yet. Disengage the kill switch and run a dry cycle.
        </p>
      </section>
    );
  }

  const stats = [
    ["Price", fmt(active.currentPrice)],
    ["Trend", active.trend],
    ["Return 1", pct(active.lastPct)],
    ["Return 5", pct(active.lookbacks["5"])],
    ["SMA 5", fmt(active.sma5)],
    ["SMA 10", fmt(active.sma10)],
    ["SMA 20", fmt(active.sma20)],
    ["SMA 50", fmt(active.sma50)],
    ["Range", fmt(active.lastRange)],
    ["Avg range", fmt(active.averageRange)],
    ["Vol (stdev)", fmt(active.rollingVol, 4)],
    ["Vol vs avg", fmt(active.volumeVsAverage, 2) + (active.volumeVsAverage === null ? "" : "×")],
  ];

  return (
    <section className="desk-card mb-12" aria-labelledby="analysis-title">
      <div className="mb-4 flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
        <div>
          <h2
            id="analysis-title"
            className="font-display m-0 text-[clamp(1.6rem,4vw,2.25rem)] leading-tight font-medium tracking-[-0.03em]"
          >
            Analysis
          </h2>
          <p className="mt-2 text-sm text-muted">
            Read-only. Not a trade signal. Grok is still gated.
          </p>
        </div>
        <p className="m-0 font-mono text-sm text-muted">
          {active.scenario} · {active.barCount} bars
        </p>
      </div>
      <div className="mb-4 flex flex-wrap gap-2">
        {rows.map((row) => (
          <button
            key={row.symbol}
            type="button"
            className={row.symbol === active.symbol ? "desk-chip min-h-11 bg-fg text-bg" : "desk-chip min-h-11"}
            onClick={() => setSymbol(row.symbol)}
          >
            {row.symbol}
          </button>
        ))}
      </div>
      <dl className="grid grid-cols-2 gap-2.5 md:grid-cols-4">
        {stats.map(([label, value]) => (
          <div key={label} className="desk-count">
            <dt className="font-mono text-[10px] tracking-[0.12em] text-faint uppercase">{label}</dt>
            <dd className="mt-1.5 mb-0 font-mono text-sm tabular-nums">{value}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
