import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { existsSync } from "node:fs";
import { join } from "node:path";
import { createInterface } from "node:readline";

type EngineResult = Record<string, unknown>;

type Pending = {
  resolve: (value: EngineResult) => void;
  reject: (error: Error) => void;
  timer: ReturnType<typeof setTimeout>;
};

type WorkerState = {
  child: ChildProcessWithoutNullStreams | null;
  seq: number;
  pending: Map<string, Pending>;
  starting: Promise<boolean> | null;
};

const FAILED_START =
  "Paper engine could not start. Paper trading is unavailable right now.";

const g = globalThis as typeof globalThis & { __aiTraderPaper?: WorkerState };

function state(): WorkerState {
  if (!g.__aiTraderPaper) {
    g.__aiTraderPaper = {
      child: null,
      seq: 0,
      pending: new Map(),
      starting: null,
    };
  }
  return g.__aiTraderPaper;
}

function grokPaperFlag() {
  if (process.env.GROK_PAPER_ANALYSIS) return process.env.GROK_PAPER_ANALYSIS;
  return process.env.XAI_API_KEY ? "true" : "false";
}

function workspaceRoot() {
  if (existsSync(join(process.cwd(), "src/ai_trader/rpc.py"))) return process.cwd();
  return process.cwd();
}

export function pythonEngineAvailable() {
  if (process.env.PAPER_ENGINE === "inline") return false;
  if (process.env.VERCEL) return false;
  if (process.env.AWS_LAMBDA_FUNCTION_NAME) return false;
  // Production / PWA (Nitro vercel, vite preview) has no Python sidecar.
  // Preview `npm run dev` stays on the stdio worker.
  if (process.env.NODE_ENV === "production") return false;
  const root = workspaceRoot();
  if (!existsSync(join(root, "src/ai_trader/rpc.py"))) return false;
  return ["/usr/bin/python3", "/usr/local/bin/python3", "/bin/python3"].some((bin) =>
    existsSync(bin),
  );
}

export function publicError(raw: string) {
  const text = raw.toLowerCase();
  if (
    text.includes("hds-") ||
    /port .+ is not found/.test(text) ||
    text.includes("econnrefused") ||
    text.includes("8090") ||
    text.includes("eaddrinuse") ||
    text.includes("fetch failed")
  ) {
    return FAILED_START;
  }
  return raw || FAILED_START;
}

function failSession(message: string): EngineResult {
  return {
    ok: false,
    live: false,
    broker: "NOT USED",
    grok: "STOPPED",
    running: false,
    stopped: true,
    balance: 100,
    today_pnl: 0,
    current_decision: "HOLD",
    position: "flat",
    open_pnl: 0,
    trades: 0,
    data_error: publicError(message),
  };
}

function settleLine(line: string) {
  let parsed: { id?: unknown; ok?: unknown; result?: EngineResult; error?: unknown };
  try {
    parsed = JSON.parse(line) as typeof parsed;
  } catch {
    return;
  }
  const id = typeof parsed.id === "string" ? parsed.id : "";
  const waiter = state().pending.get(id);
  if (!waiter) return;
  state().pending.delete(id);
  clearTimeout(waiter.timer);
  if (parsed.ok === false && parsed.result) {
    waiter.resolve(parsed.result);
    return;
  }
  if (parsed.ok === false) {
    waiter.resolve(failSession(String(parsed.error || "Paper engine request failed.")));
    return;
  }
  waiter.resolve(
    parsed.result && typeof parsed.result === "object"
      ? parsed.result
      : failSession("Empty paper-engine response."),
  );
}

function attach(proc: ChildProcessWithoutNullStreams) {
  const rl = createInterface({ input: proc.stdout });
  rl.on("line", settleLine);
  proc.stderr?.on("data", () => {
    /* worker logs stay out of the UI */
  });
  proc.on("exit", () => {
    const current = state();
    if (current.child === proc) current.child = null;
    for (const [id, waiter] of current.pending) {
      current.pending.delete(id);
      clearTimeout(waiter.timer);
      waiter.resolve(failSession(FAILED_START));
    }
  });
  proc.on("error", () => {
    const current = state();
    if (current.child === proc) current.child = null;
    for (const [id, waiter] of current.pending) {
      current.pending.delete(id);
      clearTimeout(waiter.timer);
      waiter.resolve(failSession(FAILED_START));
    }
  });
}

function spawnWorker() {
  const root = workspaceRoot();
  const proc = spawn("python3", ["-u", "-m", "ai_trader", "rpc"], {
    cwd: root,
    env: {
      ...process.env,
      PYTHONPATH: `${root}/src`,
      PYTHONUNBUFFERED: "1",
      GROK_PAPER_ANALYSIS: grokPaperFlag(),
      TRADING_MODE: process.env.TRADING_MODE || "simulate",
      KILL_SWITCH_ENGAGED: process.env.KILL_SWITCH_ENGAGED || "true",
    },
    stdio: ["pipe", "pipe", "pipe"],
  });
  attach(proc);
  return proc;
}

export async function ensurePaperWorker() {
  if (!pythonEngineAvailable()) return false;
  const current = state();
  if (current.child && !current.child.killed && current.child.exitCode === null) return true;
  if (current.starting) return current.starting;
  current.starting = new Promise<boolean>((resolve) => {
    try {
      const next = spawnWorker();
      current.child = next;
      if (next.exitCode !== null) {
        resolve(false);
        return;
      }
      const pingId = `health-${Date.now()}`;
      const timer = setTimeout(() => {
        current.pending.delete(pingId);
        resolve(Boolean(current.child && current.child.exitCode === null));
      }, 8000);
      current.pending.set(pingId, {
        resolve: () => {
          clearTimeout(timer);
          resolve(true);
        },
        reject: () => {
          clearTimeout(timer);
          resolve(false);
        },
        timer,
      });
      next.stdin.write(`${JSON.stringify({ id: pingId, cmd: "health" })}\n`);
    } catch {
      resolve(false);
    }
  }).finally(() => {
    current.starting = null;
  });
  return current.starting;
}

async function command(cmd: string, payload?: Record<string, unknown>, timeoutMs = 120000) {
  const ready = await ensurePaperWorker();
  const current = state();
  if (!ready || !current.child || !current.child.stdin.writable) {
    return failSession(FAILED_START);
  }
  const id = String(++current.seq);
  const line = JSON.stringify({ id, cmd, payload: payload || {} });
  return new Promise<EngineResult>((resolve) => {
    const timer = setTimeout(() => {
      current.pending.delete(id);
      resolve(failSession("Paper session timed out."));
    }, timeoutMs);
    current.pending.set(id, {
      resolve,
      reject: () => resolve(failSession(FAILED_START)),
      timer,
    });
    try {
      current.child?.stdin.write(`${line}\n`);
    } catch (error) {
      current.pending.delete(id);
      clearTimeout(timer);
      resolve(failSession(error instanceof Error ? error.message : FAILED_START));
    }
  });
}

function unavailableDesk(cmd: string): EngineResult {
  const message =
    "No trading worker is reachable. This host cannot run Python itself, so the " +
    "engine must run as a persistent service and PAPER_WORKER_URL must point at it.";
  const stopped: EngineResult = {
    ok: cmd !== "start" && cmd !== "stop" && cmd !== "cycle",
    live: false,
    live_trading_allowed: false,
    broker: "NOT USED",
    banner: "PAPER SIMULATION — NO REAL TRADING",
    alive: true,
    terminated: false,
    running: false,
    stopped: true,
    worker_alive: false,
    session_ready: false,
    grok: "STOPPED",
    status: "STOPPED",
    balance: 100,
    today_pnl: 0,
    current_decision: "HOLD",
    decision: "HOLD",
    position: "flat",
    open_pnl: 0,
    trades: 0,
    currency: "GBP",
    engine: "unavailable",
    hold_reason: message,
    data_error: cmd === "start" || cmd === "stop" || cmd === "cycle" ? message : null,
    account: {
      base_currency: "GBP",
      starting_cash: 100,
      cash: 100,
      equity: 100,
      premium_at_risk: 0,
      total_exposure: 0,
      realised_pnl: 0,
      fees_paid: 0,
      drawdown: 0,
      daily_pnl: 0,
      open_positions: [],
    },
    survival: {
      state: "HEALTHY",
      terminated: false,
      equity: 100,
      starting_equity: 100,
      highest_equity: 100,
      terminal_threshold: 40,
      base_currency: "GBP",
      distance_to_terminal: 60,
      life_remaining_pct: 100,
      drawdown_from_peak_pct: 0,
      policy: {
        risk_multiplier: 1,
        min_edge: 0,
        max_exposure_pct: 0,
        max_premium_pct: 0,
        max_new_positions_per_day: 0,
        description: "Worker not on this host.",
      },
      thresholds: {},
      latch: { terminated: false },
    },
    costs: {
      operating_costs: 0,
      net_pnl: 0,
      gross_trading_pnl: 0,
      daily_burn: 0,
      runway_days: null,
      spendable_capital: 100,
      self_sustaining: false,
      costs_by_category: {},
    },
    milestones: [],
    next_milestone: null,
    decisions: { TOTAL: 0 },
    last_decision: null,
    last_cycle: null,
    last_error: message,
    open_positions: [],
    config: { starting_equity: 100, base_currency: "GBP", terminal_threshold: 40 },
  };
  return stopped;
}

/**
 * Run one engine command, whichever transport is available.
 *
 * The order matters and is deliberate:
 *
 * 1. **A configured remote worker wins.** It is the deployed engine, and when
 *    an operator points a local UI at it they mean it — silently preferring a
 *    local sidecar would give them two desks and two ledgers.
 * 2. **Otherwise spawn the local stdio worker.** This is `npm run dev` on a
 *    machine that has Python.
 * 3. **Otherwise say so.** No third transport, and nothing invented to fill
 *    the gap.
 */
export async function paperEngineCommand(cmd: string, payload?: Record<string, unknown>) {
  const { remoteWorkerConfigured, callWorker } = await import("@/lib/worker-remote.server");
  if (remoteWorkerConfigured()) {
    const reply = await callWorker(cmd, payload ?? {});
    return Response.json(reply.body, {
      status: reply.status,
      headers: { "cache-control": "no-store", "content-type": "application/json; charset=utf-8" },
    });
  }
  if (!pythonEngineAvailable()) {
    const result = unavailableDesk(cmd);
    const mutating = cmd === "start" || cmd === "stop" || cmd === "cycle";
    return Response.json(result, {
      status: mutating ? 503 : 200,
      headers: { "cache-control": "no-store", "content-type": "application/json; charset=utf-8" },
    });
  }
  const result = await command(cmd, payload, cmd === "start" ? 15000 : 15000);
  const error = typeof result.data_error === "string" ? publicError(String(result.data_error)) : result.data_error;
  if (typeof error === "string") result.data_error = error;
  const failed = result.ok === false && result.data_error;
  return Response.json(result, {
    status: failed ? 503 : 200,
    headers: { "cache-control": "no-store", "content-type": "application/json; charset=utf-8" },
  });
}
