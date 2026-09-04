import { Link } from "@tanstack/react-router";
import { useCallback, useEffect, useState } from "react";
import { Empty, Failure, Loading, Shell } from "@/components/shell";
import { SurvivalMeter } from "@/components/survival-meter";
import {
  ago,
  api,
  money,
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
  const grokUsage = status.grok_usage;
  const grokConnected = Boolean(grokUsage?.connected);
  const grokCalls = grokUsage?.calls_today ?? 0;
  const grokBudget = grokUsage?.daily_budget ?? 8;
  const grokCost = grokUsage?.estimated_cost ?? status.costs?.costs_by_category?.llm ?? 0;
  const grokModel = grokUsage?.model ?? "grok-4.3";
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
            {status.terminated
              ? "Agent status"
              : grokConnected
                ? `Grok connected · ${grokModel}`
                : "Grok disconnected"}
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
              <dd>
                {status.engine === "python-worker"
                  ? "worker"
                  : status.engine === "sleeping"
                    ? "asleep"
                    : status.engine ?? "—"}
              </dd>
            </div>
          </dl>
        </aside>
      </section>

      {status.survival ? <SurvivalMeter survival={status.survival} /> : null}

      <dl className="desk-counts">
        <Count label="Trades" value={String(status.trades ?? status.decisions?.EXECUTED ?? 0)} />
        <Count
          label="P&L"
          value={signedMoney(netPnl, currency)}
        />
        <Count label="Equity" value={money(equity, currency)} />
        <Count
          label="Survival"
          value={status.survival?.state ?? "—"}
        />
      </dl>

      <section className="desk-panel">
        <header className="desk-panel-head">
          <h2 className="desk-panel-title">Grok</h2>
          <span className="font-mono text-[10px] text-faint">
            {grokConnected ? "connected" : "disconnected"}
          </span>
        </header>
        <dl className="desk-reason-grid">
          <Reason label="Model" value={grokModel} />
          <Reason label="Calls today" value={`${grokCalls} / ${grokBudget}`} />
          <Reason label="API cost" value={money(grokCost, currency)} />
          <Reason
            label="Filter"
            value={grokUsage?.filter ?? "trend pullback"}
          />
        </dl>
        <p className="mt-3 mb-0 text-sm leading-normal text-muted">
          {grokConnected
            ? `Grok is only called when the deterministic detector finds a candidate, at most ${grokBudget} times per day, at least ${Math.round((grokUsage?.min_interval_seconds ?? 1800) / 60)} minutes apart. Exhausting the budget does not stop paper trading.`
            : "No xAI key on the worker. The paper desk still runs on the deterministic detector and risk engine. Grok is not inventing decisions."}
        </p>
      </section>

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
        <SignalEvidence signal={status.signal} />
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
  if (status.engine === "sleeping" || status.engine === "unreachable" || status.engine === "unavailable") {
    return (
      status.hold_reason ||
      "The paper worker is asleep or not reachable (free host). Press Start to wake it. That can take up to a minute. Sleep does not reset the £100 book."
    );
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
    return `${symbol} ${timeframe} paper session${price}. Stop blocks new trades. Worker keeps running if you close this page, until the free host sleeps.`;
  }
  const executed = status.decisions?.EXECUTED ?? 0;
  if (executed === 0) {
    return "Paper account is idle. Press Start to run the worker on BTC-USD. It holds unless a real edge clears every gate.";
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

/**
 * Why the desk did not trade, counted by reason.
 *
 * The desk once sat at zero trades for days and the only way to find out why
 * was to read the strategy code. Every hold now names itself, so silence is
 * something you can read off the screen: a spread of reasons means the market
 * offered nothing, and one reason accounting for everything means a gate is
 * mis-set.
 */
function SignalEvidence({ signal }: { signal?: AgentStatus["signal"] }) {
  if (!signal || !signal.bars_evaluated) return null;
  const rejections = Object.entries(signal.rejections ?? {}).sort((a, b) => b[1] - a[1]);
  const meanings = signal.rejection_meanings ?? {};
  const total = rejections.reduce((sum, [, count]) => sum + count, 0);

  return (
    <div className="desk-signal">
      <p className="m-0 text-sm leading-normal text-muted">
        Looked at <strong>{signal.bars_evaluated}</strong>{" "}
        {signal.bars_evaluated === 1 ? "candle" : "candles"} and found{" "}
        <strong>{signal.candidates ?? 0}</strong>{" "}
        {(signal.candidates ?? 0) === 1 ? "candidate" : "candidates"}
        {typeof signal.grok_calls === "number"
          ? `, asking the analyst ${signal.grok_calls} ${signal.grok_calls === 1 ? "time" : "times"}`
          : ""}
        .
      </p>
      {rejections.length ? (
        <ul className="desk-rejections">
          {rejections.map(([key, count]) => (
            <li key={key}>
              <span className="desk-rejection-count">{count}</span>
              <span title={meanings[key] ?? key}>{meanings[key] ?? key}</span>
              <span className="desk-rejection-share">
                {total ? `${Math.round((count / total) * 100)}%` : ""}
              </span>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
