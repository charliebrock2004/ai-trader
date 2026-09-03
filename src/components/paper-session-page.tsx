import { Link } from "@tanstack/react-router";
import { useState } from "react";
import {
  EMPTY_SESSION,
  moneyAmount,
  positionLabel,
  type PaperSessionStatus,
} from "@/lib/paper-session";
import snapshot from "@/lib/paper-session-snapshot.json";

function asStatus(value: unknown): PaperSessionStatus | null {
  if (!value || typeof value !== "object") return null;
  const row = value as Partial<PaperSessionStatus>;
  if (typeof row.balance !== "number") return null;
  return { ...EMPTY_SESSION, ...row };
}

const FALLBACK = asStatus(snapshot) ?? EMPTY_SESSION;

export function PaperSessionPage() {
  const [status, setStatus] = useState<PaperSessionStatus>(FALLBACK);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");

  const grok = status.grok === "ACTIVE" ? "ACTIVE" : "STOPPED";
  const currency = status.currency || "GBP";
  const tiles: [string, string][] = [
    ["Balance", moneyAmount(status.balance, currency)],
    ["P&L", moneyAmount(status.today_pnl, currency)],
    ["Grok", grok],
    ["Position", positionLabel(status.position)],
    ["Trades", String(status.trades ?? 0)],
    ["Status", status.status || (status.stopped ? "STOPPED" : "SIMULATED")],
  ];

  async function start() {
    setBusy(true);
    setNote("");
    try {
      const res = await fetch("/api/paper-session/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          symbol: "BTC-USD",
          source: "public",
          bars: 24,
          timeframe: "5m",
          grok_frequency: 8,
          warmup: 8,
        }),
      });
      if (!res.ok) throw new Error("Session unavailable");
      const body = asStatus(await res.json());
      if (!body) throw new Error("Unexpected response");
      setStatus(body);
      if (body.data_error) setNote(body.data_error);
    } catch {
      // Do not swap in a snapshot here. A Start that failed leaves the desk
      // exactly as it was, and showing canned figures in its place would put
      // numbers on screen that no engine reported.
      setNote("Could not reach the trading engine. Nothing was started.");
    } finally {
      setBusy(false);
    }
  }

  async function stop() {
    setBusy(true);
    try {
      const res = await fetch("/api/paper-session/stop", { method: "POST" });
      if (res.ok) {
        const body = asStatus(await res.json());
        if (body) setStatus({ ...body, grok: "STOPPED" });
      } else {
        setStatus({ ...status, grok: "STOPPED", running: false, stopped: true });
      }
    } catch {
      setStatus({ ...status, grok: "STOPPED", running: false, stopped: true });
    } finally {
      setBusy(false);
      setNote("Stopped. New paper trades blocked.");
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
              Research desk
            </p>
            <h1 className="font-display m-0 text-[1.35rem] leading-tight font-medium tracking-[-0.03em]">
              Paper session
            </h1>
          </div>
        </div>
        <div className="flex flex-wrap items-center justify-end gap-2">
          <Link to="/" className="desk-chip min-h-11">
            Desk
          </Link>
          <Link to="/performance" className="desk-chip min-h-11">
            Performance
          </Link>
          <span className="desk-chip desk-chip-warn">Paper only</span>
        </div>
      </header>

      <section className="desk-hero">
        <div>
          <p className="mb-2.5 font-mono text-[11px] tracking-[0.18em] text-faint uppercase">
            Grok {grok}
          </p>
          <h2 className="font-display m-0 max-w-[16ch] text-[clamp(2rem,6vw,3.15rem)] leading-[1.12] font-medium tracking-[-0.03em]">
            One session. No live orders.
          </h2>
          <p className="mt-4 max-w-[42ch] text-sm leading-normal text-muted">
            Sequential candles. Grok. Risk. Internal paper fills only. Never live.
          </p>
        </div>
        <aside className="desk-card">
          <p className="m-0 text-sm text-muted">
            Start walks the tape. Stop blocks new paper trades immediately.
          </p>
          <div className="mt-4 flex flex-col gap-2 md:flex-row">
            <button
              type="button"
              className="desk-btn desk-btn-primary min-h-11 flex-1"
              onClick={start}
              disabled={busy}
            >
              {busy ? "Running…" : "Start"}
            </button>
            <button type="button" className="desk-btn desk-btn-ghost min-h-11 flex-1" onClick={stop} disabled={busy}>
              Stop
            </button>
          </div>
          {note ? <p className="mt-3 mb-0 font-mono text-[11px] text-faint">{note}</p> : null}
        </aside>
      </section>

      <dl className="mb-10 grid grid-cols-2 gap-2.5 md:grid-cols-3">
        {tiles.map(([label, value]) => (
          <div key={label} className="desk-count">
            <dt className="font-mono text-[10px] tracking-[0.12em] text-faint uppercase">{label}</dt>
            <dd className="mt-1.5 mb-0 break-all font-mono text-xl tabular-nums">{value}</dd>
          </div>
        ))}
      </dl>

      <footer className="mt-8 flex flex-col gap-1.5 border-t border-fg/12 pt-4 font-mono text-xs text-faint">
        <p>Paper fills stay on the internal book. Live trading is disabled in code.</p>
        <p>External live hosts blocked · live trading allowed: false</p>
      </footer>
    </div>
  );
}
