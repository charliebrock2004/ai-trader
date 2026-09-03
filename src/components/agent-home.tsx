import { Link } from "@tanstack/react-router";
import { useCallback, useEffect, useState } from "react";
import { Empty, Failure, Loading, Shell } from "@/components/shell";
import { SurvivalMeter } from "@/components/survival-meter";
import {
  ago,
  api,
  money,
  percent,
  points,
  probability,
  shortDate,
  signedMoney,
  type AgentStatus,
} from "@/lib/agent-api";

/**
 * The home screen answers the operational questions in one viewport:
 * is it RUNNING, is it paper, what is the balance, what did it decide,
 * why, when was the last cycle, and what is survival.
 *
 * Start/Stop talk to the Python worker. The browser is not the engine.
 */
export function AgentHome() {
  const [status, setStatus] = useState<AgentStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<"start" | "stop" | null>(null);

  const load = useCallback(async () => {
    try {
      const next = await api.agent();
      setStatus(next);
      if (next.data_error) setError(next.data_error);
      else setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not reach the paper engine.");
    } finally {
      setLoading(false);
    }
  }, []);

  const running = Boolean(status?.running && !status?.stopped);
  const starting = Boolean(running && !status?.session_ready);

  useEffect(() => {
    void load();
    const interval = running || starting || busy === "start" ? 2000 : 8000;
    const id = window.setInterval(() => void load(), interval);
    return () => window.clearInterval(id);
  }, [load, running, starting, busy]);

  async function start() {
    if (busy || running) return;
    setBusy("start");
    setError(null);
    try {
      const next = await api.start();
      setStatus(next);
      setError(next.data_error ?? null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Start failed.");
    } finally {
      setBusy(null);
    }
  }

  async function stop() {
    if (busy === "stop") return;
    setBusy("stop");
    try {
      const next = await api.stop();
      setStatus(next);
      setError(next.data_error ?? null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Stop failed.");
    } finally {
      setBusy(null);
    }
  }

  if (loading && !status) {
    return (
      <Shell active="home">
        <Loading />
      </Shell>
    );
  }
  if (!status) {
    return (
      <Shell active="home">
        <Failure message={error ?? "Unknown error."} onRetry={() => void load()} />
      </Shell>
    );
  }

  const currency = status.account?.base_currency ?? status.currency ?? "GBP";
  const last = status.last_decision;
  const cycle = status.last_cycle;
  const netPnl = status.costs?.net_pnl ?? 0;
  const equity = Number.isFinite(status.balance as number)
    ? (status.balance as number)
    : (status.account?.equity ?? 100);
  const deskStatus = status.status ?? (running ? (starting ? "STARTING" : "RUNNING") : "STOPPED");
  const grok = status.grok ?? (running ? "RUNNING" : "STOPPED");
  const decision = (status.current_decision || status.decision || last?.final_action || "HOLD").toUpperCase();
  const today = status.today_pnl ?? status.account?.daily_pnl ?? 0;
  const startLabel = busy === "start" || starting ? "Starting…" : running ? "Running" : "Start";

  return (
    <Shell active="home">
      {error ? (
        <p className="desk-inline-warning" role="alert">
          {error}
        </p>
      ) : null}

      <section className="desk-hero">
        <div>
          <p className="mb-2.5 font-mono text-[11px] tracking-[0.18em] text-faint uppercase">
            {status.terminated ? "Agent status" : `Grok ${grok}`}
          </p>
          <p className={`desk-decision ${status.terminated ? "text-halt" : ""}`}>
            {status.terminated ? "TERMINATED" : decision}
          </p>
          <p className="mt-3 max-w-[42ch] text-sm leading-normal text-muted">
            {describe(status, deskStatus)}
          </p>
        </div>
        <aside className="desk-card">
          <p className="m-0 font-mono text-[10px] tracking-[0.12em] text-faint uppercase">
            {deskStatus === "STARTING" ? "Starting" : deskStatus === "RUNNING" ? "Running" : "Stopped"}
          </p>
          <p className="desk-equity">{money(equity, currency)}</p>
          <p
            className={`m-0 font-mono text-sm tabular-nums ${
              netPnl > 0 ? "text-ok" : netPnl < 0 ? "text-halt" : "text-muted"
            }`}
          >
            {signedMoney(today, currency)} today
          </p>
          <div className="desk-controls">
            <button
              type="button"
              className="desk-btn desk-btn-primary min-h-11"
              onClick={() => void start()}
              disabled={Boolean(busy) || running || status.terminated}
            >
              {startLabel}
            </button>
            <button
              type="button"
              className="desk-btn desk-btn-ghost min-h-11"
              onClick={() => void stop()}
              disabled={busy === "stop" || status.terminated}
            >
              {busy === "stop" ? "Stopping…" : "Stop"}
            </button>
          </div>
          <dl className="desk-mini">
            <div>
              <dt>Position</dt>
              <dd>{positionLabel(status.position)}</dd>
            </div>
            <div>
              <dt>Trades</dt>
              <dd>{String(status.trades ?? status.decisions?.EXECUTED ?? 0)}</dd>
            </div>
            <div>
              <dt>Engine</dt>
              <dd>{status.engine === "python-worker" ? "worker" : status.engine ?? "—"}</dd>
            </div>
          </dl>
        </aside>
      </section>

      {status.survival ? <SurvivalMeter survival={status.survival} /> : null}

      <dl className="desk-counts">
        <Count label="Trades" value={String(status.trades ?? status.decisions?.EXECUTED ?? 0)} />
        <Count label="Decisions" value={String(status.decisions?.TOTAL ?? 0)} />
        <Count
          label="Drawdown"
          value={percent(status.survival?.drawdown_from_peak_pct, 2)}
        />
        <Count label="Costs" value={money(status.costs?.operating_costs, currency)} />
      </dl>

      <section className="desk-panel">
        <header className="desk-panel-head">
          <h2 className="desk-panel-title">What it is doing</h2>
          {cycle ? (
            <span className="font-mono text-[10px] text-faint">{ago(cycle.finished_at)}</span>
          ) : null}
        </header>
        {status.hold_reason ? (
          <p className="m-0 mb-3 text-sm leading-normal text-muted">{status.hold_reason}</p>
        ) : null}
        {cycle ? (
          <p className="m-0 text-sm leading-normal text-muted">
            Last cycle examined <strong>{cycle.contracts_considered}</strong>{" "}
            {cycle.contracts_considered === 1 ? "contract" : "contracts"}, shortlisted{" "}
            <strong>{cycle.shortlisted.length}</strong>, asked the analyst{" "}
            <strong>{cycle.analyst_calls}</strong>{" "}
            {cycle.analyst_calls === 1 ? "time" : "times"}, and traded{" "}
            <strong>{cycle.traded}</strong>.
            {cycle.errors.length ? ` ${cycle.errors.length} error(s) were recorded.` : ""}
          </p>
        ) : (
          <p className="m-0 text-sm text-muted">
            {running
              ? "Worker is up. Waiting for the first cycle."
              : "No cycle has run yet. Press Start to run the paper desk."}
          </p>
        )}
      </section>

      <section className="desk-panel">
        <header className="desk-panel-head">
          <h2 className="desk-panel-title">Why it decided that</h2>
          {last ? (
            <Link to="/decisions/$id" params={{ id: String(last.id) }} className="desk-link">
              Full record →
            </Link>
          ) : null}
        </header>
        {last ? (
          <>
            <div className="desk-reason-grid">
              <Reason label="Market" value={last.ticker ?? "—"} />
              <Reason label="Model" value={probability(last.model_probability)} />
              <Reason label="Market price" value={probability(last.market_probability)} />
              <Reason label="Net edge" value={points(last.net_edge)} />
            </div>
            <p className="mt-3 mb-0 max-w-[70ch] text-sm leading-normal text-muted">
              {last.notes ?? last.policy_reason ?? last.risk_reason ?? "No reason recorded."}
            </p>
            <p className="mt-1.5 mb-0 font-mono text-[11px] text-faint">
              {shortDate(last.created_at)} · stage {last.stage ?? "—"} ·{" "}
              {last.executed ? "executed" : "not executed"}
            </p>
          </>
        ) : (
          <Empty
            title="No decisions yet"
            detail="Once the worker is running, every decision — including the HOLDs — appears here."
          />
        )}
      </section>

      {status.open_positions?.length ? (
        <section className="desk-panel">
          <h2 className="desk-panel-title">Open positions</h2>
          <div className="desk-scroll">
            <table className="desk-table">
              <thead>
                <tr>
                  <th>Contract</th>
                  <th className="num">Contracts</th>
                  <th className="num">Price</th>
                  <th className="num">At risk</th>
                  <th className="num">Max gain</th>
                </tr>
              </thead>
              <tbody>
                {status.open_positions.map((position) => (
                  <tr key={position.position_id}>
                    <td>{position.ticker}</td>
                    <td className="num">{position.contracts}</td>
                    <td className="num">{probability(position.average_price)}</td>
                    <td className="num text-halt">{money(position.max_loss_base, currency)}</td>
                    <td className="num text-ok">{money(position.max_gain_base, currency)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-2 mb-0 text-xs text-faint">
            A binary contract has no stop loss. “At risk” is the whole premium.
          </p>
        </section>
      ) : null}
    </Shell>
  );
}

function describe(status: AgentStatus, deskStatus: string): string {
  if (status.terminated) {
    return "Permanently shut down. It cannot trade, and it cannot restart itself.";
  }
  if (deskStatus === "STARTING") {
    const symbol = status.symbol || "BTC-USD";
    const timeframe = status.timeframe || "5m";
    return `Starting the paper worker on ${symbol} ${timeframe}. The browser is not the engine.`;
  }
  if (deskStatus === "RUNNING") {
    const symbol = status.symbol || "BTC-USD";
    const timeframe = status.timeframe || "5m";
    const price = typeof status.last_price === "number" ? ` · last ${status.last_price}` : "";
    return `${symbol} ${timeframe} paper session${price}. Stop blocks new trades. Worker keeps running if you close this page.`;
  }
  const executed = status.decisions?.EXECUTED ?? 0;
  if (executed === 0) {
    return "Paper account is idle. Press Start to run the worker. It holds unless a real edge clears every gate.";
  }
  const next = status.next_milestone;
  return next
    ? `Stopped. Next milestone: ${next.label} at ${money(next.equity, status.account?.base_currency ?? "GBP")}.`
    : "Stopped. New paper trades are blocked.";
}

function positionLabel(pos: AgentStatus["position"]): string {
  if (!pos || pos === "flat") return "flat";
  if (typeof pos === "string") return pos;
  return `${pos.symbol ?? ""} ${pos.side ?? "LONG"} ${pos.quantity ?? ""}`.trim();
}

function Count({ label, value }: { label: string; value: string }) {
  return (
    <div className="desk-count">
      <dt className="font-mono text-[10px] tracking-[0.12em] text-faint uppercase">{label}</dt>
      <dd className="mt-1.5 mb-0 font-mono text-xl tabular-nums">{value}</dd>
    </div>
  );
}

function Reason({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="m-0 font-mono text-[10px] tracking-[0.12em] text-faint uppercase">{label}</p>
      <p className="mt-1 mb-0 font-mono text-base tabular-nums">{value}</p>
    </div>
  );
}
