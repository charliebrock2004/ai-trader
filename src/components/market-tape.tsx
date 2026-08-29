import { useMemo, useState } from "react";
import { DEFAULT_SYMBOLS, type CandleSeries } from "@/lib/simulated-market";

function sparkPoints(closes: number[], width = 220, height = 56) {
  if (closes.length < 2) return "";
  const min = Math.min(...closes);
  const max = Math.max(...closes);
  const span = max - min || 1;
  return closes
    .map((value, index) => {
      const x = (index / (closes.length - 1)) * width;
      const y = height - ((value - min) / span) * height;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
}

function formatTime(timestamp: string) {
  return timestamp.slice(11, 16);
}

export function MarketTape({ series }: { series: CandleSeries[] }) {
  const [symbol, setSymbol] = useState<string>(DEFAULT_SYMBOLS[0]);
  const active = useMemo(
    () => series.find((item) => item.symbol === symbol) ?? series[0],
    [series, symbol],
  );

  if (!series.length || !active) {
    return (
      <section className="desk-card mb-12">
        <div className="mb-2">
          <h2 className="font-display m-0 text-[clamp(1.6rem,4vw,2.25rem)] leading-tight font-medium tracking-[-0.03em]">
            Simulated tape
          </h2>
          <p className="mt-2 text-sm text-muted">
            No bars yet. Disengage the kill switch and run a dry cycle.
          </p>
        </div>
      </section>
    );
  }

  const last = active.candles[active.candles.length - 1];
  const first = active.candles[0];
  const change = ((last.close - first.close) / first.close) * 100;
  const closes = active.candles.map((c) => c.close);
  const recent = active.candles.slice(-6);

  return (
    <section className="desk-card mb-12" aria-labelledby="tape-title">
      <div className="mb-4 flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
        <div>
          <h2
            id="tape-title"
            className="font-display m-0 text-[clamp(1.6rem,4vw,2.25rem)] leading-tight font-medium tracking-[-0.03em]"
          >
            Simulated tape
          </h2>
          <p className="mt-2 text-sm text-muted">
            Seed 42 · 5m bars · local generator only. Nothing is live.
          </p>
        </div>
        <p className="m-0 font-mono text-sm tabular-nums text-muted">
          {active.scenario} · {last.close.toFixed(2)} · {change >= 0 ? "+" : ""}
          {change.toFixed(2)}%
        </p>
      </div>
      <div className="mb-4 flex flex-wrap gap-2">
        {series.map((item) => {
          const selected = item.symbol === active.symbol;
          return (
            <button
              key={item.symbol}
              type="button"
              className={selected ? "desk-chip min-h-11 bg-fg text-bg" : "desk-chip min-h-11"}
              onClick={() => setSymbol(item.symbol)}
            >
              {item.symbol}
            </button>
          );
        })}
      </div>
      <svg viewBox="0 0 220 56" className="mb-4 h-16 w-full text-fg" role="img" aria-label={`${active.symbol} close path`}>
        <polyline
          fill="none"
          stroke="currentColor"
          strokeWidth="1.6"
          points={sparkPoints(closes)}
        />
      </svg>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[28rem] border-collapse font-mono text-xs tabular-nums">
          <thead>
            <tr className="text-left text-faint">
              <th className="pb-2 font-medium">Time</th>
              <th className="pb-2 font-medium">Open</th>
              <th className="pb-2 font-medium">High</th>
              <th className="pb-2 font-medium">Low</th>
              <th className="pb-2 font-medium">Close</th>
              <th className="pb-2 font-medium">Vol</th>
            </tr>
          </thead>
          <tbody>
            {recent.map((candle) => (
              <tr key={candle.timestamp} className="border-t border-fg/12">
                <td className="py-2">{formatTime(candle.timestamp)}</td>
                <td>{candle.open.toFixed(2)}</td>
                <td>{candle.high.toFixed(2)}</td>
                <td>{candle.low.toFixed(2)}</td>
                <td>{candle.close.toFixed(2)}</td>
                <td>{Math.round(candle.volume).toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
