import { useCallback, useEffect, useState } from "react";
import { Failure, Loading, Shell } from "@/components/shell";
import { api, type Tournament, type TournamentRow } from "@/lib/agent-api";
import { rankMove, type RankHistory } from "@/lib/rank-history";

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
  const [asset, setAsset] = useState<string>("all");
  const [history, setHistory] = useState<RankHistory | null>(null);

  const load = useCallback(async () => {
    try {
      const next = await api.tournament();
      // A reply without rows is not a leaderboard (e.g. no engine is reachable).
      if (!next.ok || !Array.isArray(next.rows)) {
        throw new Error(
          next.error ??
            "No trading worker is reachable, so the leaderboard cannot be computed. A free worker may be asleep; retry in a moment.",
        );
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

  useEffect(() => {
    // Optional: the leaderboard is complete without the daily record.
    api
      .tournamentHistory()
      .then((h) => setHistory(h.ok ? { days: h.days, ranks: h.ranks } : null))
      .catch(() => setHistory(null));
  }, []);

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
  const assets = [...new Set(data.rows.map((r) => r.symbol))].sort();
  const shown = asset === "all" ? data.rows : data.rows.filter((r) => r.symbol === asset);
  // An engine that predates the comparison sends no excess_return: show "—", not "0 of N".
  const compared = strategies.filter((r) => r.forward_bars > 0 && r.excess_return != null);
  const beating = compared.filter((r) => (r.excess_return ?? 0) > 0).length;
  const factor = data.stress_cost_factor ?? 2;
  const recorded = history?.days ?? [];
  const moves = recorded.length >= 2;

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
        <Stat
          label="Ahead of holding"
          value={compared.length ? `${beating} of ${compared.length}` : "—"}
        />
        <Stat label="Last refresh" value={clock(data.heartbeat?.last_success)} />
      </dl>

      {data.provisional ? (
        <p className="mt-4 mb-0 max-w-[64ch] text-sm text-muted" role="status">
          Provisional: too few forward days to rank anything. The paper gate needs 60 days and 20
          trades.
        </p>
      ) : null}

      <section className="desk-panel">
        <header className="desk-panel-head">
          <h3 className="desk-panel-title">Leaderboard</h3>
          <div className="flex flex-wrap gap-2" role="group" aria-label="Filter by asset">
            {["all", ...assets].map((a) => (
              <button
                key={a}
                type="button"
                className={`desk-chip min-h-11 ${asset === a ? "desk-chip-active" : ""}`}
                aria-pressed={asset === a}
                onClick={() => setAsset(a)}
              >
                {a === "all" ? "All assets" : a}
              </button>
            ))}
          </div>
        </header>
        <p className="mt-0 mb-3 max-w-[70ch] text-sm leading-normal text-muted">
          "Vs holding" is the net return minus simply holding the same coin over the same days,
          after the same costs. "At {factor}× cost" re-runs the same rule with fee and slippage
          multiplied by {factor}; it does not affect the rank or the gate.{" "}
          {moves
            ? `"Moved" is the change in rank between the daily records of ${recorded[recorded.length - 2]} and ${recorded[recorded.length - 1]}.`
            : history
              ? `Rank movement appears once two daily records exist (${recorded.length} so far).`
              : "The daily rank record could not be read."}
        </p>
        <div className="desk-scroll">
          <table className="desk-table">
            <thead>
              <tr>
                <th scope="col">#</th>
                {moves ? <th scope="col">Moved</th> : null}
                <th scope="col">Strategy</th>
                <th scope="col">Asset</th>
                <th scope="col">Equity</th>
                <th scope="col">Net return</th>
                <th scope="col">Vs holding</th>
                <th scope="col">At {factor}× cost</th>
                <th scope="col">Max drawdown</th>
                <th scope="col">Trades</th>
                <th scope="col">Target position</th>
                <th scope="col">Paper gate</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((row) => (
                <tr key={row.id}>
                  <td>{row.rank}</td>
                  {moves && history ? <Move places={rankMove(history, row.id)} /> : null}
                  <td>{row.benchmark ? `${row.candidate} (benchmark)` : row.candidate}</td>
                  <td>{row.symbol}</td>
                  <td>
                    <Spark curve={row.curve} label={`${row.candidate} ${row.symbol}`} />
                  </td>
                  <td className={tone(row.net_return)}>{signedPct(row.net_return)}</td>
                  <td className={tone(row.excess_return ?? 0)}>
                    {row.excess_return == null ? "—" : signedPct(row.excess_return)}
                  </td>
                  <td className={tone(row.stress_net_return ?? 0)}>
                    {row.stress_net_return == null ? "—" : signedPct(row.stress_net_return)}
                  </td>
                  <td>{pct(row.max_drawdown)}</td>
                  <td>{row.trades}</td>
                  <td>{pct(row.target_weight, 0)}</td>
                  <td className="whitespace-nowrap">{gate(row)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </Shell>
  );
}

function Move({ places }: { places: number | null }) {
  if (places == null) return <td className="text-faint">—</td>;
  if (places === 0) return <td className="text-muted">0</td>;
  const up = places > 0;
  return (
    <td className={up ? "text-ok" : "text-halt"}>
      <span aria-hidden="true">{up ? "▲" : "▼"}</span>
      <span className="sr-only">{up ? "up" : "down"}</span> {Math.abs(places)}
    </td>
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

/** Equity curve from its 1.0 start. Drawn only once there is something to draw. */
function Spark({ curve, label }: { curve: number[] | undefined; label: string }) {
  if (!curve || curve.length < 2) return <span className="text-faint">—</span>;
  const w = 96;
  const h = 28;
  const lo = Math.min(...curve, 1);
  const hi = Math.max(...curve, 1);
  const span = hi - lo || 1;
  const y = (v: number) => h - 2 - ((v - lo) / span) * (h - 4);
  const points = curve
    .map((v, i) => `${((i / (curve.length - 1)) * w).toFixed(1)},${y(v).toFixed(1)}`)
    .join(" ");
  const end = curve[curve.length - 1];
  const change = signedPct(end - 1);
  return (
    <svg
      width={w}
      height={h}
      viewBox={`0 0 ${w} ${h}`}
      role="img"
      aria-label={`${label} simulated equity, ${change} since registration`}
    >
      <line x1="0" x2={w} y1={y(1)} y2={y(1)} stroke="currentColor" strokeOpacity="0.25" />
      <polyline
        points={points}
        fill="none"
        strokeWidth="1.5"
        className={end > 1 ? "stroke-ok" : end < 1 ? "stroke-halt" : "stroke-muted"}
      />
    </svg>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="desk-count">
      <dt className="font-mono text-[10px] tracking-[0.12em] text-faint uppercase">{label}</dt>
      <dd className="mt-1.5 mb-0 font-mono text-xl tabular-nums">{value}</dd>
    </div>
  );
}
