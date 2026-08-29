import { Link } from "@tanstack/react-router";
import { useMemo, useState } from "react";
import snapshot from "@/lib/benchmark-snapshot.json";
import {
  formatDrawdown,
  formatPct,
  formatPf,
  formatWinRate,
  strategyLabel,
  type BenchmarkReport,
} from "@/lib/benchmark";

const FALLBACK = snapshot as BenchmarkReport;

type MarketRow = {
  return_pct?: number;
  trades?: number;
  maximum_drawdown?: number;
};

function asReport(value: unknown): BenchmarkReport | null {
  if (!value || typeof value !== "object") return null;
  const row = value as Partial<BenchmarkReport>;
  if (!row.headline || !Array.isArray(row.comparison)) return null;
  return row as BenchmarkReport;
}

export function PerformancePage() {
  const [report, setReport] = useState<BenchmarkReport>(FALLBACK);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");

  const headline = report.headline;
  const verdict = useMemo(() => {
    if (headline.trades === 0 || report.verdict?.grok_traded === false) {
      return "Grok did not take a trade. Sitting in cash is not a measured edge.";
    }
    if (report.verdict?.beats_all) return "Grok beat every baseline on out-of-sample data.";
    if (report.verdict?.beats_buy_and_hold) {
      return "Grok beat buy-and-hold. It did not beat every baseline.";
    }
    return "Grok did not beat buy-and-hold on out-of-sample data.";
  }, [headline.trades, report.verdict]);

  const tiles = [
    ["Grok return", formatPct(headline.grok_return_pct)],
    ["Benchmark return", formatPct(headline.benchmark_return_pct)],
    ["Max drawdown", formatDrawdown(headline.maximum_drawdown)],
    ["Trades", String(headline.trades)],
    ["Win rate", formatWinRate(headline.win_rate)],
    ["Profit factor", formatPf(headline.profit_factor)],
  ];

  const oosMarkets = (
    report.splits as
      | Record<string, { markets?: Record<string, Record<string, MarketRow>> }>
      | undefined
  )?.out_of_sample?.markets;

  async function runBenchmark() {
    setBusy(true);
    setNote("");
    try {
      const res = await fetch("/api/benchmark", { method: "POST" });
      if (!res.ok) throw new Error("Benchmark unavailable");
      const body = asReport(await res.json());
      if (!body) throw new Error("Unexpected response");
      setReport(body);
    } catch {
      setNote("Showing the last paper snapshot. The live engine is not on this desk.");
      setReport(FALLBACK);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="desk-frame">
      <p className="desk-banner" role="status">
        Paper simulation — no real trading
      </p>
      <header className="desk-top">
        <div className="desk-brand">
          <span className="desk-mark" aria-hidden="true" />
          <div>
            <p className="m-0 font-mono text-[10px] tracking-[0.16em] text-faint uppercase">
              Paper trading
            </p>
            <h1 className="font-display m-0 text-[1.35rem] leading-tight font-medium tracking-[-0.03em]">
              Performance
            </h1>
          </div>
        </div>
        <div className="flex flex-wrap items-center justify-end gap-2">
          <Link to="/" className="desk-chip min-h-11">
            Trade
          </Link>
          <Link to="/system" className="desk-chip min-h-11">
            System
          </Link>
          <span className="desk-chip desk-chip-warn">Paper only</span>
        </div>
      </header>

      <section className="desk-hero">
        <div>
          <p className="mb-2.5 font-mono text-[11px] tracking-[0.18em] text-faint uppercase">
            Out of sample · {headline.grok_model}
          </p>
          <h2 className="font-display m-0 max-w-[16ch] text-[clamp(2rem,6vw,3.15rem)] leading-[1.12] font-medium tracking-[-0.03em]">
            Grok versus the baselines.
          </h2>
          <p className="mt-4 max-w-[46ch] text-sm leading-normal text-muted">{verdict}</p>
        </div>
        <aside className="desk-card">
          <p className="m-0 text-sm text-muted">
            Same £100, same tape, same costs, same risk limits. Buy-and-hold is the
            benchmark. Nothing here is live.
          </p>
          <button
            type="button"
            className="desk-btn desk-btn-primary mt-4 min-h-11 w-full"
            onClick={runBenchmark}
            disabled={busy}
          >
            {busy ? "Running paper benchmark…" : "Run paper benchmark"}
          </button>
          {note ? <p className="mt-3 mb-0 font-mono text-[11px] text-faint">{note}</p> : null}
        </aside>
      </section>

      <dl className="mb-10 grid grid-cols-2 gap-2.5 md:grid-cols-3">
        {tiles.map(([label, value]) => (
          <div key={label} className="desk-count">
            <dt className="font-mono text-[10px] tracking-[0.12em] text-faint uppercase">{label}</dt>
            <dd className="mt-1.5 mb-0 font-mono text-xl tabular-nums">{value}</dd>
          </div>
        ))}
      </dl>

      <section className="desk-card mb-10">
        <h2 className="font-display m-0 text-[clamp(1.6rem,4vw,2.25rem)] leading-tight font-medium tracking-[-0.03em]">
          Comparison
        </h2>
        <p className="mt-2 mb-5 text-sm text-muted">
          Out-of-sample average across the five simulated markets.
        </p>
        <div className="desk-table-wrap">
          <table className="desk-table">
            <thead>
              <tr>
                <th>Strategy</th>
                <th>Return</th>
                <th>Drawdown</th>
                <th>Trades</th>
                <th>Win rate</th>
                <th>Profit factor</th>
              </tr>
            </thead>
            <tbody>
              {report.comparison.map((row) => (
                <tr key={row.strategy}>
                  <td>{strategyLabel(row.strategy)}</td>
                  <td>{formatPct(row.return_pct)}</td>
                  <td>{formatDrawdown(row.maximum_drawdown)}</td>
                  <td>{row.trades}</td>
                  <td>{formatWinRate(row.win_rate)}</td>
                  <td>{formatPf(row.profit_factor)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <details className="desk-card mb-10">
        <summary className="text-sm text-muted">Detailed splits and markets</summary>
        <p className="mt-3 mb-4 font-mono text-xs leading-relaxed text-faint">
          Training seed 101 · validation seed 202 · out-of-sample seed 303. Strategies were not
          fitted to these paths. Historical periods are reserved and not connected.
        </p>
        {oosMarkets
          ? Object.entries(oosMarkets).map(([symbol, byStrategy]) => (
              <p key={symbol} className="mb-2 font-mono text-xs text-muted">
                {symbol}: Grok {formatPct(byStrategy.GROK?.return_pct ?? 0)} · Hold{" "}
                {formatPct(byStrategy.BUY_AND_HOLD?.return_pct ?? 0)} · Tech{" "}
                {formatPct(byStrategy.SIMPLE_TECHNICAL?.return_pct ?? 0)} · Random{" "}
                {formatPct(byStrategy.RANDOM_BASELINE?.return_pct ?? 0)}
              </p>
            ))
          : null}
        {report.notes ? <p className="mt-3 mb-0 text-sm text-muted">{report.notes}</p> : null}
      </details>

      <footer className="mt-8 flex flex-col gap-1.5 border-t border-fg/12 pt-4 font-mono text-xs text-faint">
        <p>Paper and simulate only. Live trading is disabled in code.</p>
        <p>Broker not used · live trading allowed: false</p>
      </footer>
    </div>
  );
}
