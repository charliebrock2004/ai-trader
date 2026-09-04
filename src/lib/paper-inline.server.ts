/**
 * In-process paper engine for deployed Node (PWA / grok.me).
 *
 * Preview uses the Python stdio worker. Production is nodejs22.x with no
 * Python package and no long-lived sidecar — spawn("python3") hangs the
 * Start request, which is why the iPhone Home Screen app stuck on Starting…
 *
 * This path still: Coinbase completed candles → sequential walk → Grok
 * paper analysis → risk gate → internal paper fills. No broker. Not live.
 */

import {
  applyPendingFill,
  coinbaseUrl,
  lastEligibleIndex,
  markToMarket,
  newSession,
  parseCoinbaseRows,
  queueDecision,
  snapshot,
  STARTING_CASH,
  walkHold,
  type Candle,
  type PaperSessionState,
  type PaperStatus,
} from "@/lib/paper-core";

export { parseCoinbaseRows } from "@/lib/paper-core";
export type InlineStatus = PaperStatus;

const g = globalThis as typeof globalThis & { __aiTraderInline?: PaperSessionState | null };
function store(next: PaperSessionState | null) {
  g.__aiTraderInline = next;
}
function current(): PaperSessionState | null {
  return g.__aiTraderInline ?? null;
}

function fail(message: string): PaperStatus {
  store(null);
  return snapshot(null, {
    ok: false,
    data_error: message,
    grok: "STOPPED",
    running: false,
    stopped: true,
    engine: "inline",
  });
}

async function loadCandles(symbol: string, timeframe: string, limit: number): Promise<Candle[]> {
  const { url, product, granularity } = coinbaseUrl(symbol, timeframe);
  const response = await fetch(url, {
    headers: { Accept: "application/json", "User-Agent": "AI-Trader-Paper/1.0" },
    signal: AbortSignal.timeout(6000),
  });
  if (!response.ok) throw new Error(`Public market data unavailable (${response.status}).`);
  const payload = await response.json();
  return parseCoinbaseRows(payload, { product, granularity, limit });
}

export async function analyzePaperBar(payload: Record<string, unknown>): Promise<{
  action: string;
  model: string;
  live: false;
  broker: "NOT USED";
}> {
  const symbol = String(payload.symbol || "BTC-USD");
  const equity = Number(payload.equity);
  const candle = payload.candle as Candle | undefined;
  if (!candle || !Number.isFinite(candle.close)) {
    return { action: "HOLD", model: "fixture-hold", live: false, broker: "NOT USED" };
  }
  const grok = await grokDecision(symbol, candle, Number.isFinite(equity) ? equity : STARTING_CASH);
  return { action: grok.action, model: grok.model, live: false, broker: "NOT USED" };
}

async function grokDecision(symbol: string, candle: Candle, equity: number): Promise<{ action: string; model: string }> {
  const key = String(process.env.XAI_API_KEY || "").trim();
  const requested = process.env.GROK_PAPER_ANALYSIS === "true" || Boolean(key);
  if (!requested || !key) {
    return { action: "HOLD", model: "fixture-hold" };
  }
  const base = (process.env.XAI_BASE_URL || "https://api.x.ai/v1").replace(/\/$/, "");
  const model = process.env.XAI_MODEL || "grok-4.3";
  const body = {
    model,
    temperature: 0,
    max_tokens: 400,
    messages: [
      {
        role: "system",
        content:
          "You are Grok acting as a paper-trading market analyst for a simulated £100 account. You NEVER place orders, NEVER call tools, NEVER talk to a broker, and NEVER change risk limits, kill switch, or safety flags. Reply with JSON only: {\"action\":\"BUY\"|\"SELL\"|\"HOLD\",\"confidence\":0-1,\"reasoning\":\"...\"}. If unsure, HOLD.",
      },
      {
        role: "user",
        content: JSON.stringify({
          symbol,
          close: candle.close,
          open: candle.open,
          high: candle.high,
          low: candle.low,
          timestamp: candle.timestamp,
          account: { currency: "GBP", account_equity: equity, cash: equity, live: false },
          live: false,
        }),
      },
    ],
  };
  try {
    const response = await fetch(`${base}/chat/completions`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${key}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(8000),
    });
    if (!response.ok) return { action: "HOLD", model };
    const json = (await response.json()) as { choices?: Array<{ message?: { content?: string } }> };
    const text = json.choices?.[0]?.message?.content || "";
    const match = text.match(/\{[\s\S]*\}/);
    if (!match) return { action: "HOLD", model };
    const parsed = JSON.parse(match[0]) as { action?: string };
    const action = String(parsed.action || "HOLD").toUpperCase();
    if (action !== "BUY" && action !== "SELL" && action !== "HOLD") return { action: "HOLD", model };
    return { action, model };
  } catch {
    return { action: "HOLD", model };
  }
}

async function startSession(payload: Record<string, unknown>): Promise<PaperStatus> {
  const symbol = String(payload.symbol || "BTC-USD");
  const timeframe = String(payload.timeframe || "5m");
  const barsWanted = Number(payload.bars || 24);
  const warmup = Number(payload.warmup || 8);
  const frequency = Number(payload.grok_frequency || 8);
  let candles: Candle[];
  try {
    candles = await loadCandles(symbol, timeframe, barsWanted);
  } catch (error) {
    return fail(error instanceof Error ? error.message : "Public market data unavailable.");
  }

  const session = newSession({ symbol, timeframe, engine: "inline" });
  walkHold(session, candles);

  const eligible = lastEligibleIndex(candles.length, warmup, frequency);
  if (eligible >= 0) {
    const grok = await grokDecision(symbol, candles[eligible], session.equity);
    session.grokModel = grok.model === "fixture-hold" ? "fixture-hold" : "real Grok";
    queueDecision(session, grok.action);
    if (session.pending && candles[eligible + 1]) {
      applyPendingFill(session, candles[eligible + 1]);
      markToMarket(session, candles[candles.length - 1].close);
    }
  }

  store(session);
  return snapshot(session);
}

export async function inlinePaperCommand(cmd: string, payload: Record<string, unknown> = {}): Promise<PaperStatus> {
  const action = String(cmd || "").toLowerCase();
  if (action === "health") {
    return { ok: true, service: "ai-trader-inline", live: false, engine: "inline" };
  }
  if (action === "status") {
    return snapshot(current());
  }
  if (action === "stop") {
    const session = current();
    if (session) {
      session.running = false;
      session.stopped = true;
      store(session);
    }
    return snapshot(
      current() ?? {
        ...newSession({ symbol: "BTC-USD", timeframe: "5m", engine: "inline", id: "stopped" }),
        running: false,
        stopped: true,
      },
    );
  }
  if (action === "start") {
    return startSession(payload);
  }
  return fail(`Unknown paper-engine command '${cmd}'.`);
}
