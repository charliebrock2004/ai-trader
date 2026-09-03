/**
 * Server-side client for the persistent paper worker.
 *
 * Vercel has no Python and no process that outlives a request, so the engine
 * lives on a host that does (Render) and this module reaches it over HTTPS.
 * It is the *same* engine the local stdio path talks to — the command names
 * below are the ones in `ai_trader/rpc.py`, so there is one command surface and
 * one ledger, not a second implementation.
 *
 * Two rules this file exists to enforce:
 *
 * 1. **The control token never reaches the browser.** It is read from the
 *    server environment here and attached to the outbound request. Nothing in
 *    `src/components` can see it, and it is never named with a `VITE_` prefix,
 *    which would inline it into the client bundle.
 * 2. **A worker that cannot be reached is reported, not papered over.** Every
 *    failure path returns a payload that says STOPPED with a reason. None of
 *    them invents a price, a fill, a balance or a RUNNING state.
 */

type Json = Record<string, unknown>;

/** Reads a command from the worker. */
const READ_PATHS: Record<string, (payload: Json) => string> = {
  health: () => "/health",
  status: () => "/api/status",
  agent: () => "/api/agent",
  performance: () => "/api/performance",
  system: () => "/api/system",
  decisions: (payload) => {
    const params = new URLSearchParams();
    const limit = Number(payload.limit);
    if (Number.isFinite(limit)) params.set("limit", String(Math.max(1, Math.min(200, limit))));
    if (payload.only_executed) params.set("only_executed", "true");
    const query = params.toString();
    return query ? `/api/decisions?${query}` : "/api/decisions";
  },
  decision: (payload) => `/api/decisions/${encodeURIComponent(String(payload.id ?? ""))}`,
  opportunities: (payload) => {
    const limit = Number(payload.limit);
    return Number.isFinite(limit)
      ? `/api/opportunities?limit=${Math.max(1, Math.min(200, limit))}`
      : "/api/opportunities";
  },
};

/** Changes worker state. These carry the token; the reads above do not. */
const WRITE_PATHS: Record<string, string> = {
  start: "/api/start",
  stop: "/api/stop",
  cycle: "/api/cycle",
};

/** A status read should fail fast so the dashboard stays responsive. */
const READ_TIMEOUT_MS = 10_000;
/**
 * Start loads and validates a candle series before it will admit to RUNNING,
 * which is slower than a read and must not be cut off halfway — a client
 * timeout mid-Start would leave the UI unsure whether a session exists.
 */
const WRITE_TIMEOUT_MS = 60_000;

/**
 * The worker's base URL, or "" when none is configured.
 *
 * Server environment only. A trailing slash is trimmed so path joining cannot
 * produce a double slash that some hosts answer with a redirect.
 */
export function workerBaseUrl(): string {
  const raw = (process.env.PAPER_WORKER_URL ?? process.env.WORKER_URL ?? "").trim();
  if (!raw) return "";
  let parsed: URL;
  try {
    parsed = new URL(raw);
  } catch {
    return "";
  }
  // Only HTTP(S). A file: or data: URL here would be a configuration mistake
  // worth failing closed on rather than attempting.
  if (parsed.protocol !== "https:" && parsed.protocol !== "http:") return "";
  return parsed.origin + parsed.pathname.replace(/\/+$/, "");
}

export function remoteWorkerConfigured(): boolean {
  return workerBaseUrl().length > 0;
}

function controlToken(): string {
  return (process.env.AI_TRADER_API_TOKEN ?? "").trim();
}

/**
 * The shape the dashboard can always render.
 *
 * Deliberately minimal: enough for the UI to show an honest STOPPED desk with
 * the reason, and not one field more that could be mistaken for market state.
 */
export function workerUnreachable(message: string): Json {
  return {
    ok: false,
    live: false,
    live_trading_allowed: false,
    broker: "NOT USED",
    banner: "PAPER SIMULATION — NO REAL TRADING",
    running: false,
    stopped: true,
    worker_alive: false,
    session_ready: false,
    grok: "STOPPED",
    status: "STOPPED",
    // Not "python-worker": there is no engine answering. A dashboard that
    // prints this field must not be able to name a running engine when none
    // responded.
    engine: "unreachable",
    real_market_data: false,
    market_data: "unavailable",
    balance: 100,
    today_pnl: 0,
    open_pnl: 0,
    trades: 0,
    current_decision: "HOLD",
    decision: "HOLD",
    position: "flat",
    currency: "GBP",
    data_error: message,
    hold_reason: message,
  };
}

/** Strips anything that could leak deployment shape into the browser. */
function publicMessage(raw: string): string {
  const text = raw.toLowerCase();
  if (
    text.includes("econnrefused") ||
    text.includes("enotfound") ||
    text.includes("eai_again") ||
    text.includes("fetch failed") ||
    text.includes("socket hang up")
  ) {
    return "The trading worker is not responding. Paper trading is unavailable right now.";
  }
  if (text.includes("aborted") || text.includes("timeout") || text.includes("timed out")) {
    return "The trading worker did not answer in time.";
  }
  return raw;
}

export type WorkerReply = { status: number; body: Json };

/**
 * Run one command against the remote worker.
 *
 * Never throws: a transport failure becomes a 503 with an honest STOPPED body,
 * because a dashboard that renders an exception page tells the operator less
 * than one that says the worker is down.
 */
export async function callWorker(cmd: string, payload: Json = {}): Promise<WorkerReply> {
  const base = workerBaseUrl();
  if (!base) {
    return {
      status: 503,
      body: workerUnreachable(
        "No trading worker is configured for this deployment. Set PAPER_WORKER_URL " +
          "to the worker's URL to connect it.",
      ),
    };
  }

  const writePath = WRITE_PATHS[cmd];
  const readPath = READ_PATHS[cmd];
  if (!writePath && !readPath) {
    return { status: 400, body: workerUnreachable(`Unknown paper-engine command '${cmd}'.`) };
  }

  const headers: Record<string, string> = { accept: "application/json" };
  let method = "GET";
  let body: string | undefined;

  if (writePath) {
    const token = controlToken();
    if (!token) {
      // Fail closed and say which side is unconfigured. Sending the request
      // without a token would produce a 401 that looks like a worker fault.
      return {
        status: 503,
        body: workerUnreachable(
          "This deployment has no AI_TRADER_API_TOKEN configured, so Start and Stop " +
            "are disabled. Set the same token on the frontend and the worker.",
        ),
      };
    }
    method = "POST";
    headers["x-ai-trader-token"] = token;
    headers["content-type"] = "application/json";
    body = JSON.stringify(payload ?? {});
  }

  const path = writePath ?? readPath!(payload ?? {});
  const timeout = writePath ? WRITE_TIMEOUT_MS : READ_TIMEOUT_MS;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);

  try {
    const response = await fetch(`${base}${path}`, {
      method,
      headers,
      body,
      signal: controller.signal,
      cache: "no-store",
      redirect: "error",
    });
    const text = await response.text();
    let parsed: unknown = null;
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = null;
    }
    if (!parsed || typeof parsed !== "object") {
      return {
        status: 502,
        body: workerUnreachable(
          `The trading worker answered ${response.status} without a usable payload.`,
        ),
      };
    }
    const result = parsed as Json;
    if (typeof result.data_error === "string") {
      result.data_error = publicMessage(result.data_error);
    }
    return { status: response.status, body: result };
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return { status: 503, body: workerUnreachable(publicMessage(message)) };
  } finally {
    clearTimeout(timer);
  }
}
