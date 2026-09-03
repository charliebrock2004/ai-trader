import { Link } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { raceTimeout } from "@/lib/paper-core";
import {
  EMPTY_SESSION,
  moneyAmount,
  positionLabel,
  type PaperSessionStatus,
} from "@/lib/paper-session";
import snapshot from "@/lib/paper-session-snapshot.json";

function asStatus(value: unknown): PaperSessionStatus | null {
  if (!value || typeof value !== "object") return null;
  const row = value as Record<string, unknown>;
  const balance = Number(row.balance);
  if (!Number.isFinite(balance)) return null;
  return {
    ...EMPTY_SESSION,
    ...(row as Partial<PaperSessionStatus>),
    balance,
    today_pnl: Number(row.today_pnl) || 0,
    open_pnl: Number(row.open_pnl) || 0,
    trades: Number(row.trades) || 0,
  };
}

function errorMessage(status: number, body: unknown, raw: string) {
  if (body && typeof body === "object") {
    const row = body as { data_error?: unknown; detail?: unknown; message?: unknown };
    if (typeof row.data_error === "string" && row.data_error) return cleanError(row.data_error);
    if (typeof row.message === "string" && row.message) return cleanError(row.message);
    if (typeof row.detail === "string" && row.detail) return cleanError(row.detail);
  }
  if (raw && !raw.trim().startsWith("<")) return cleanError(raw.slice(0, 240));
  return `Start failed (${status}).`;
}

function cleanError(raw: string) {
  const text = raw.toLowerCase();
  if (
    text.includes("hds-") ||
    /port .+ is not found/.test(text) ||
    text.includes("econnrefused") ||
    text.includes("8090") ||
    text.includes("fetch failed")
  ) {
    return "Paper engine could not start. Paper trading is unavailable right now.";
  }
  return raw;
}

function grokLabel(status: PaperSessionStatus) {
  if (status.running && !status.stopped) return "RUNNING";
  if (status.grok === "RUNNING" || status.grok === "ACTIVE") return "RUNNING";
  return "STOPPED";
}

function readJson(raw: string) {
  try {
    return JSON.parse(raw) as unknown;
  } catch {
    return null;
  }
}

function apiUrl(path: string) {
  return new URL(path, window.location.href).href;
}

const FALLBACK = asStatus(snapshot) ?? EMPTY_SESSION;

export function TradingHome() {
  const [status, setStatus] = useState<PaperSessionStatus>(FALLBACK);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");

  const grok = grokLabel(status);
  const decision = (status.current_decision || status.decision || "HOLD").toUpperCase();
  const currency = status.currency || "GBP";
  const running = Boolean(status.running && !status.stopped);
  const symbol = status.symbol || status.config?.symbol || "BTC-USD";
  const timeframe = status.timeframe || status.config?.timeframe || "5m";
  const lastPrice = typeof status.last_price === "number" ? status.last_price : null;
  const failed = Boolean(
    note &&
      (status.data_error ||
        note.toLowerCase().includes("fail") ||
        note.toLowerCase().includes("unavailable") ||
        note.toLowerCase().includes("timed out")),
  );

  useEffect(() => {
    if (!busy) return;
    const id = window.setTimeout(() => {
      setBusy(false);
      setNote((current) => current || "Start did not finish. No live orders were sent.");
    }, 18000);
    return () => window.clearTimeout(id);
  }, [busy]);

  useEffect(() => {
    if (!status.running) return;
    const id = window.setInterval(() => {
      void (async () => {
        try {
          const res = await raceTimeout(fetch(apiUrl("/api/paper-session"), { cache: "no-store" }), 8000, "Status timed out.");
          const raw = await res.text();
          const parsed = readJson(raw);
          const next = asStatus(parsed);
          if (!next) {
            setNote(`Status failed (${res.status}).`);
            return;
          }
          if (status.running && !next.running && !next.data_error && next.session_id !== status.session_id) {
            return;
          }
          setStatus(next);
          if (next.data_error) setNote(cleanError(next.data_error));
        } catch (error) {
          setNote(error instanceof Error ? cleanError(error.message) : "Status check failed.");
        }
      })();
    }, 2000);
    return () => window.clearInterval(id);
  }, [status.running, status.session_id]);

  async function startFromServer() {
    const res = await raceTimeout(
      fetch(apiUrl("/api/paper-session/start"), {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        cache: "no-store",
        body: JSON.stringify({
          symbol: "BTC-USD",
          source: "public",
          bars: 24,
          timeframe: "5m",
          grok_frequency: 8,
          warmup: 8,
          continuous: true,
        }),
      }),
      12000,
      "Start timed out. The paper engine did not answer. No live orders were sent.",
    );
    const raw = await res.text();
    const parsed = readJson(raw);
    if (!parsed) throw new Error(errorMessage(res.status, null, raw));
    if (!res.ok) throw new Error(errorMessage(res.status, parsed, raw));
    const body = asStatus(parsed);
    if (!body) throw new Error("Unexpected paper-session response.");
    return body;
  }

  /**
   * Start the desk.
   *
   * Only the worker starts a session. An earlier version tried an in-browser
   * paper engine first and only fell back to the server if that threw — which
   * meant the button usually drove a simulation that never touched the risk
   * engine, the policy guardian, the survival latch or the ledger, produced
   * P&L that existed in no audit trail, and vanished on refresh. There is one
   * engine, and it is not in the browser.
   */
  async function start() {
    setBusy(true);
    setNote("Asking the worker to start…");
    try {
      const body = await startFromServer();
      setStatus(body);
      if (body.data_error) {
        setNote(cleanError(body.data_error));
        return;
      }
      if (body.running && !body.stopped) {
        const nextSymbol = body.symbol || body.config?.symbol || "BTC-USD";
        const nextTimeframe = body.timeframe || body.config?.timeframe || "5m";
        const price = typeof body.last_price === "number" ? ` · last ${body.last_price}` : "";
        setNote(`Paper session running · ${nextSymbol} ${nextTimeframe}${price}`);
      } else {
        setNote("The worker did not start a session.");
      }
    } catch (error) {
      setNote(error instanceof Error ? cleanError(error.message) : "Start failed.");
    } finally {
      setBusy(false);
    }
  }

  async function stop() {
    setBusy(true);
    try {
      const res = await raceTimeout(
        fetch(apiUrl("/api/paper-session/stop"), { method: "POST", cache: "no-store" }),
        10000,
        "Stop timed out.",
      );
      const body = asStatus(readJson(await res.text()));
      if (!res.ok || !body) {
        // Do not paint the desk STOPPED on the strength of a failed request.
        // The worker decides whether it stopped; saying otherwise here would
        // leave a running session behind a screen that claims it is halted.
        setNote("Stop failed. The worker may still be running — check the System page.");
        return;
      }
      setStatus(body);
      setNote(
        body.running ? "The worker did not stop." : "Stopped. New paper trades blocked.",
      );
    } catch (error) {
      setNote(
        error instanceof Error
          ? `${cleanError(error.message)} The worker may still be running.`
          : "Stop failed.",
      );
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
              AI-Trader
            </h1>
          </div>
        </div>
        <nav className="flex flex-wrap items-center justify-end gap-2" aria-label="App">
          <Link to="/performance" className="desk-chip min-h-11">
            Performance
          </Link>
          <Link to="/system" className="desk-chip min-h-11">
            System
          </Link>
        </nav>
      </header>

      <section className="desk-hero">
        <div>
          <p className="mb-2.5 font-mono text-[11px] tracking-[0.18em] text-faint uppercase">
            Grok {grok}
          </p>
          <p className="desk-decision">{decision}</p>
          <p className="mt-3 max-w-[36ch] text-sm leading-normal text-muted">
            {running
              ? `${symbol} ${timeframe} paper session${lastPrice != null ? ` · last ${lastPrice}` : ""}. Stop blocks new trades.`
              : "Current BUY / SELL / HOLD decision. Start a paper session. Stop blocks new trades."}
          </p>
        </div>
        <aside className="desk-card">
          <div className="flex flex-col gap-2">
            <button
              type="button"
              className="desk-btn desk-btn-primary min-h-11"
              onClick={start}
              disabled={busy || running}
            >
              {busy ? "Starting…" : running ? "Running" : "Start"}
            </button>
            <button type="button" className="desk-btn desk-btn-ghost min-h-11" onClick={stop} disabled={busy}>
              Stop
            </button>
          </div>
          {note ? (
            <p className={`mt-3 mb-0 text-sm ${failed ? "text-halt" : "text-muted"}`} role="status">
              {note}
            </p>
          ) : null}
        </aside>
      </section>

      <dl className="mb-10 grid grid-cols-1 gap-2.5 sm:grid-cols-3">
        <div className="desk-count">
          <dt className="font-mono text-[10px] tracking-[0.12em] text-faint uppercase">
            Account balance
          </dt>
          <dd className="mt-1.5 mb-0 break-all font-mono text-xl tabular-nums">
            {moneyAmount(status.balance, currency)}
          </dd>
        </div>
        <div className="desk-count">
          <dt className="font-mono text-[10px] tracking-[0.12em] text-faint uppercase">
            Today’s profit/loss
          </dt>
          <dd className="mt-1.5 mb-0 break-all font-mono text-xl tabular-nums">
            {moneyAmount(status.today_pnl, currency)}
          </dd>
        </div>
        <div className="desk-count">
          <dt className="font-mono text-[10px] tracking-[0.12em] text-faint uppercase">
            Current position
          </dt>
          <dd className="mt-1.5 mb-0 break-all font-mono text-xl tabular-nums">
            {positionLabel(status.position)}
          </dd>
        </div>
      </dl>

      <footer className="mt-8 flex flex-col gap-1.5 border-t border-fg/12 pt-4 font-mono text-xs text-faint">
        <p>Paper only. Live trading is disabled.</p>
      </footer>
    </div>
  );
}
