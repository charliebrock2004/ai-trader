import { useCallback, useEffect, useState } from "react";
import { Failure, Loading, Shell } from "@/components/shell";
import { api, type Tournament, type TournamentRow } from "@/lib/agent-api";

const GATE_LABELS: Record<string, string> = {
  paper_days: "under 60 days",
  paper_trades: "under 20 trades",
  paper_net_positive: "no net gain",
  paper_max_drawdown: "drawdown over 15%",
  paper_execution_errors: "execution errors",
};

/**
 * Forward paper tournament.
 *
 * Every strategy was frozen before the first scored candle, so nothing here was
 * tuned on the figures shown. All of it is simulated.
 */
export function TournamentPage() {
  const [data, setData] = useState<Tournament | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const next = await api.tournament();
      // A reply without rows is not a leaderboard (e.g. no engine is reachable).
      if (!next.ok || !Array.isArray(next.rows)) {
        throw new Error(next.error ?? next.hold_reason ?? "The leaderboard could not be computed.");
      }
      setData(next);
      setError(null);
    } catch (err) {
      setData(null);
      setError(err instanceof Error ? err.message : "Could not reach the paper engine.");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  if (error) {
    return (
      <Shell active="tournament">
        <Failure message={error} onRetry={() => void load()} />
      </Shell>
    );
  }
  if (!data) {
    return (
      <Shell active="tournament">
        <Loading label="Scoring frozen strategies on forward candles…" />
      </Shell>
    );
  }

  const bars = Math.max(0, ...Object.values(data.data ?? {}).map((d) => d.forward_bars));
  const strategies = data.rows.filter((r) => !r.benchmark);
  const passing = strategies.filter((r) => r.paper_gate.passed).length;

  return (
    <Shell active="tournament">
      <section className="desk-section-head">
        <div>
          <h2 className="desk-heading">Forward paper tournament</h2>
          <p className="mt-2 mb-0 max-w-[64ch] text-sm leading-normal text-muted">{data.note}</p>
        </div>
      </section>

      <dl className="desk-counts">
        <Stat label="Registered" value={day(data.registered_at)} />
        <Stat label="Forward days scored" value={String(bars)} />
        <Stat label="Passing paper gate" value={`${passing} of ${strategies.length}`} />
        <Stat label="Last refresh" value={clock(data.heartbeat?.last_success)} />
      </dl>

      {data.provisional ? (
        <p className="mt-4 mb-0 max-w-[64ch] text-sm text-muted" role="status">
          Provisional: too few forward days to rank anything. The paper gate needs 60 days and 20
          trades.
        </p>
      ) : null}

      <section className="desk-panel">
        <h3 className="desk-panel-title">Leaderboard</h3>
        <div className="desk-scroll">
          <table className="desk-table">
            <thead>
              <tr>
                <th scope="col">#</th>
                <th scope="col">Strategy</th>
                <th scope="col">Asset</th>
                <th scope="col">Net return</th>
                <th scope="col">Max drawdown</th>
                <th scope="col">Trades</th>
                <th scope="col">Position</th>
                <th scope="col">Paper gate</th>
              </tr>
            </thead>
            <tbody>
              {data.rows.map((row) => (
                <tr key={row.id}>
                  <td>{row.rank}</td>
                  <td>{row.benchmark ? `${row.candidate} (benchmark)` : row.candidate}</td>
                  <td>{row.symbol}</td>
                  <td className={tone(row.net_return)}>{signedPct(row.net_return)}</td>
                  <td>{pct(row.max_drawdown)}</td>
                  <td>{row.trades}</td>
                  <td>{pct(row.target_weight, 0)}</td>
                  <td>{gate(row)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </Shell>
  );
}

function gate(row: TournamentRow): string {
  if (row.benchmark) return "—";
  if (row.paper_gate.passed) return "Passed";
  return row.paper_gate.failed.map((f) => GATE_LABELS[f] ?? f).join(", ");
}

function pct(value: number, digits = 1): string {
  return `${(Math.abs(value) * 100).toFixed(digits)}%`;
}

function signedPct(value: number): string {
  const sign = value > 0 ? "+" : value < 0 ? "−" : "";
  return `${sign}${pct(value, 2)}`;
}

function tone(value: number): string {
  return value > 0 ? "text-ok" : value < 0 ? "text-halt" : "";
}

function day(iso: string | undefined): string {
  return iso ? iso.slice(0, 10) : "—";
}

function clock(epoch: number | null | undefined): string {
  if (!epoch) return "—";
  return `${new Date(epoch * 1000).toISOString().slice(11, 16)} UTC`;
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="desk-count">
      <dt className="font-mono text-[10px] tracking-[0.12em] text-faint uppercase">{label}</dt>
      <dd className="mt-1.5 mb-0 font-mono text-xl tabular-nums">{value}</dd>
    </div>
  );
}
