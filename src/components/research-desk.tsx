import { useEffect, useMemo, useState } from "react";
import { Link } from "@tanstack/react-router";
import { AnalysisPanel } from "@/components/analysis-panel";
import { AccountPanel } from "@/components/account-panel";
import { PaperBoard } from "@/components/paper-board";
import { DecisionPanel } from "@/components/decision-panel";
import { MarketTape } from "@/components/market-tape";
import {
  LEDGER,
  MODULES,
  PIPELINE,
  makeEvent,
  readEvents,
  readKillSwitch,
  writeEvents,
  writeKillSwitch,
  type DeskEvent,
} from "@/lib/desk";
import {
  analyseTape,
  readAnalysis,
  writeAnalysis,
  type MarketAnalysis,
} from "@/lib/analysis";
import {
  generateDefaultTape,
  readMarket,
  writeMarket,
  type CandleSeries,
} from "@/lib/simulated-market";
import {
  fixtureHolds,
  readDecisions,
  writeDecisions,
  type FixtureDecision,
} from "@/lib/fixture-ai";
import {
  initialPaperAccount,
  readPaperAccount,
  writePaperAccount,
  type PaperAccountState,
} from "@/lib/paper-account";
import { readPaperSim, runPaperDemo, writePaperSim, type PaperReport } from "@/lib/paper-sim";

const BOOT_EVENT: DeskEvent = {
  id: 1,
  createdAt: "foundation",
  level: "INFO",
  eventType: "boot",
  message: "AI-Trader foundation started. Order placement is disabled.",
};

function formatTime(value: string) {
  if (!value || value === "foundation") return "session";
  try {
    return new Date(value).toLocaleString();
  } catch {
    return value;
  }
}

export function ResearchDesk() {
  const [killEngaged, setKillEngaged] = useState(true);
  const [events, setEvents] = useState<DeskEvent[]>([BOOT_EVENT]);
  const [market, setMarket] = useState<CandleSeries[]>([]);
  const [analysis, setAnalysis] = useState<MarketAnalysis[]>([]);
  const [decisions, setDecisions] = useState<FixtureDecision[]>([]);
  const [account, setAccount] = useState<PaperAccountState | null>(null);
  const [paper, setPaper] = useState<PaperReport | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const existing = readEvents();
    const engaged = readKillSwitch();
    const next = existing.length ? existing : [BOOT_EVENT];
    setKillEngaged(engaged);
    setEvents(next);
    writeEvents(next);
    setMarket(readMarket());
    setAnalysis(readAnalysis());
    setDecisions(readDecisions());
    setAccount(readPaperAccount());
    setPaper(readPaperSim());
    setReady(true);
  }, []);

  function persist(nextEngaged: boolean, nextEvents: DeskEvent[]) {
    setKillEngaged(nextEngaged);
    setEvents(nextEvents);
    writeKillSwitch(nextEngaged);
    writeEvents(nextEvents);
  }

  function toggleKill() {
    const engaged = !killEngaged;
    const event = makeEvent({
      level: engaged ? "WARNING" : "INFO",
      eventType: engaged ? "kill_switch_engaged" : "kill_switch_disengaged",
      message: engaged
        ? "Kill switch engaged."
        : "Kill switch disengaged. Order placement remains disabled.",
    });
    persist(engaged, [event, ...events]);
  }

  function dryRun() {
    if (killEngaged) return;
    const tape = generateDefaultTape();
    const rows = analyseTape(tape);
    const holds = fixtureHolds(rows);
    setMarket(tape);
    writeMarket(tape);
    setAnalysis(rows);
    writeAnalysis(rows);
    setDecisions(holds);
    writeDecisions(holds);
    const paper = initialPaperAccount();
    setAccount(paper);
    writePaperAccount(paper);
    const event = makeEvent({
      level: "INFO",
      eventType: "dry_run",
      message: "Dry run completed. Paper account unchanged. No orders placed.",
    });
    persist(false, [event, ...events]);
  }

  async function grokCycle() {
    if (killEngaged) return;
    let model = "fixture-hold";
    let action: FixtureDecision["action"] = "HOLD";
    let confidence = 1;
    let reasoning = "Fixture default. Real Grok is off unless GROK_PAPER_ANALYSIS is enabled in the Python engine.";
    let execution = "none";
    let risk = "rejected";
    try {
      const res = await fetch("/api/grok-paper-cycle", { method: "POST" });
      if (res.ok) {
        const body = await res.json();
        model = body.ai_model === "real Grok" ? "real Grok" : "fixture-hold";
        action = body.ai_decision?.action ?? "HOLD";
        confidence = body.confidence ?? 0;
        reasoning = body.reasoning ?? reasoning;
        execution = body.paper_execution ?? "none";
        risk = body.risk?.approved ? "approved" : "rejected";
        if (body.paper?.account) {
          setAccount(body.paper.account);
          writePaperAccount(body.paper.account);
        }
      }
    } catch {
      // Published desk has no xAI key. Stay on fixture HOLD.
    }
    const row: FixtureDecision = {
      symbol: "SIM-UP",
      action,
      confidence,
      reasoning,
      timestamp: new Date().toISOString(),
      analysisRef: "grok-paper-cycle",
      model,
      risk,
      execution,
      broker: "NOT USED",
    };
    setDecisions([row, ...decisions]);
    writeDecisions([row, ...decisions]);
    const event = makeEvent({
      level: "INFO",
      eventType: "grok_paper_cycle",
      message: `${model} ${action}. Broker not used.`,
    });
    persist(false, [event, ...events]);
  }

  function paperSim() {
    if (killEngaged) return;
    const report = runPaperDemo("SIM-UP");
    setPaper(report);
    writePaperSim(report);
    setAccount(report.account);
    writePaperAccount(report.account);
    const event = makeEvent({
      level: "INFO",
      eventType: "paper_sim",
      message: "Paper simulation completed. No broker called.",
    });
    persist(false, [event, ...events]);
  }

  const lede = killEngaged
    ? "Market data, Grok, and Alpaca paper trading are wired as modules — not connected, not live. The risk engine sits between any future AI decision and execution. The kill switch is on."
    : "Dry cycle still HOLDs. Paper simulation can fill internally against the £100 book. Real Grok and Alpaca stay gated.";

  const counts = useMemo(
    () => [
      ["Decisions", decisions.length],
      ["Trades", paper?.performance.total_trades ?? 0],
      ["Positions", paper?.closed.length ?? 0],
      ["Events", events.length],
      ["Snapshots", account ? 1 : 0],
    ],
    [events.length, decisions.length, account, paper],
  );

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
              Advanced
            </p>
            <h1 className="font-display m-0 text-[1.35rem] leading-tight font-medium tracking-[-0.03em]">
              System
            </h1>
          </div>
        </div>
        <div className="flex flex-wrap justify-end gap-2">
          <Link to="/" className="desk-chip min-h-11">
            Trade
          </Link>
          <Link to="/performance" className="desk-chip min-h-11">
            Performance
          </Link>
          <span className="desk-chip">Simulate</span>
          <span className="desk-chip desk-chip-warn">Orders off</span>
          <span className="desk-chip desk-chip-warn">Broker not used</span>
        </div>
      </header>

      <section className="desk-hero" aria-labelledby="hero-title">
        <div>
          <p className="mb-2.5 font-mono text-[11px] tracking-[0.18em] text-faint uppercase">
            Advanced / System
          </p>
          <h2
            id="hero-title"
            className="font-display m-0 max-w-[14ch] text-[clamp(2rem,6vw,3.15rem)] leading-[1.12] font-medium tracking-[-0.03em]"
          >
            The desk can paper-trade. Nothing can go live.
          </h2>
          <p className="mt-4 max-w-[46ch] text-sm leading-normal text-muted">{lede}</p>
        </div>
        <aside className={`desk-card ${killEngaged ? "" : "desk-card-clear"}`}>
          <div className="flex items-center gap-2.5">
            <span className="desk-dot" />
            <h3 className="font-display m-0 text-[1.35rem] leading-tight font-medium tracking-[-0.03em]">
              {killEngaged ? "Kill switch engaged" : "Kill switch clear"}
            </h3>
          </div>
          <p className="mt-2.5 mb-4 text-sm text-muted">
            {killEngaged
              ? "Pipeline halted. Dry runs and order paths are blocked until you disengage. Disengaging does not enable orders."
              : "Pipeline may run dry cycles. Order placement is still disabled in the broker and risk layers."}
          </p>
          <div className="flex flex-col gap-2 md:flex-row">
            <button type="button" className="desk-btn desk-btn-primary flex-1" onClick={toggleKill}>
              {killEngaged ? "Disengage kill switch" : "Engage kill switch"}
            </button>
            <button
              type="button"
              className="desk-btn desk-btn-ghost flex-1"
              onClick={dryRun}
              disabled={killEngaged || !ready}
            >
              Run dry cycle
            </button>
            <button
              type="button"
              className="desk-btn desk-btn-ghost flex-1"
              onClick={paperSim}
              disabled={killEngaged || !ready}
            >
              Run paper sim
            </button>
            <button
              type="button"
              className="desk-btn desk-btn-ghost flex-1"
              onClick={grokCycle}
              disabled={killEngaged || !ready}
            >
              Run Grok paper cycle
            </button>
          </div>
          <p className="mt-3.5 font-mono text-[11px] text-faint">
            {killEngaged
              ? "Initialised in the engaged (safe) state."
              : "Kill switch is not engaged."}
          </p>
        </aside>
      </section>

      <section className="mb-12" aria-labelledby="pipe-title">
        <div className="mb-4">
          <h2
            id="pipe-title"
            className="font-display m-0 text-[clamp(1.6rem,4vw,2.25rem)] leading-tight font-medium tracking-[-0.03em]"
          >
            Pipeline
          </h2>
          <p className="mt-2 text-sm text-muted">Signals move top to bottom. Execution is last, and locked.</p>
        </div>
        <ol className="m-0 grid list-none gap-2.5 p-0">
          {PIPELINE.map((stage, index) => (
            <li key={stage.id} className="desk-stage">
              <span className="pt-1 font-mono text-[11px] text-faint">
                {String(index + 1).padStart(2, "0")}
              </span>
              <div>
                <h3 className="m-0 text-base font-medium">{stage.title}</h3>
                <p className="mt-1 mb-0 text-sm text-muted">{stage.detail}</p>
              </div>
              <span className="desk-chip">{stage.status}</span>
            </li>
          ))}
        </ol>
      </section>

      <MarketTape series={market} />
      <AnalysisPanel rows={analysis} />
      <DecisionPanel rows={decisions} />
      <AccountPanel account={account} />
      <PaperBoard report={paper} />

      <section className="mb-12 grid gap-6 md:grid-cols-2">
        <article className="desk-card">
          <div className="mb-4">
            <h2 className="font-display m-0 text-[clamp(1.6rem,4vw,2.25rem)] leading-tight font-medium tracking-[-0.03em]">
              Modules
            </h2>
            <p className="mt-2 text-sm text-muted">Swap later without rewriting the desk.</p>
          </div>
          <ul className="m-0 grid list-none gap-3 p-0">
            {MODULES.map((mod, index) => (
              <li
                key={mod.id}
                className={`flex justify-between gap-3 pb-3 ${
                  index === MODULES.length - 1
                    ? "border-0 pb-0"
                    : "border-b border-fg/12"
                }`}
              >
                <div>
                  <h3 className="m-0 text-sm font-medium">{mod.title}</h3>
                  <p className="mt-1 mb-0 text-xs text-muted">{mod.detail}</p>
                </div>
                <span className="desk-chip h-fit">{mod.status}</span>
              </li>
            ))}
          </ul>
        </article>
        <article className="desk-card">
          <div className="mb-4">
            <h2 className="font-display m-0 text-[clamp(1.6rem,4vw,2.25rem)] leading-tight font-medium tracking-[-0.03em]">
              Ledger
            </h2>
            <p className="mt-2 text-sm text-muted">
              Trades and positions stay empty. Fixture HOLD is a proposal only.
            </p>
          </div>
          <dl className="mb-5 grid grid-cols-2 gap-2.5">
            {counts.map(([label, value]) => (
              <div key={label} className="desk-count">
                <dt className="font-mono text-[10px] tracking-[0.12em] text-faint uppercase">{label}</dt>
                <dd className="mt-1.5 mb-0 font-mono text-xl tabular-nums">{value}</dd>
              </div>
            ))}
          </dl>
          <div className="grid gap-4">
            <div>
              <h3 className="font-display m-0 text-[1.1rem] font-medium">Decisions</h3>
              <p className="mt-1 mb-0 text-sm text-faint">
                {decisions.length ? `${decisions.length} fixture HOLD proposal(s).` : "No AI decisions recorded."}
              </p>
            </div>
            <div>
              <h3 className="font-display m-0 text-[1.1rem] font-medium">Trades</h3>
              <p className="mt-1 mb-0 text-sm text-faint">No trades recorded.</p>
            </div>
            <div>
              <h3 className="font-display m-0 text-[1.1rem] font-medium">Positions</h3>
              <p className="mt-1 mb-0 text-sm text-faint">No open positions.</p>
            </div>
          </div>
        </article>
      </section>

      <section className="desk-card">
        <div className="mb-4">
          <h2 className="font-display m-0 text-[clamp(1.6rem,4vw,2.25rem)] leading-tight font-medium tracking-[-0.03em]">
            Event log
          </h2>
          <p className="mt-2 text-sm text-muted">System events only. Secrets are never written here.</p>
        </div>
        <div className="grid max-h-[360px] gap-2.5 overflow-auto font-mono text-xs">
          {events.length === 0 ? (
            <p className="text-faint">Waiting for events.</p>
          ) : (
            events.map((event) => (
              <article
                key={event.id}
                className="grid gap-0.5 border-b border-fg/12 pb-2.5"
              >
                <time className="text-faint">
                  {formatTime(event.createdAt)} · {event.level} · {event.eventType}
                </time>
                <div>{event.message}</div>
              </article>
            ))
          )}
        </div>
      </section>

      <footer className="mt-8 flex flex-col gap-1.5 border-t border-fg/12 pt-4 font-mono text-xs text-faint">
        <p>Paper and simulate only. Live trading is disabled in code.</p>
        <p>v0.1.0 · live trading allowed: false</p>
      </footer>
    </div>
  );
}
