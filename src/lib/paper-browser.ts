/**
 * In-app paper engine for the iPhone Home Screen PWA.
 *
 * Preview Start used a 20s Python+Grok server POST. iOS standalone Web.app
 * leaves that fetch pending, so the button stuck on Starting…
 *
 * This path loads Coinbase from the device (public CORS), walks candles,
 * then asks the server only for a short Grok paper opinion. Start becomes
 * RUNNING from real market data, not from a fake timer.
 */

import {
  applyPendingFill,
  coinbaseUrl,
  lastEligibleIndex,
  markToMarket,
  newSession,
  parseCoinbaseRows,
  queueDecision,
  raceTimeout,
  snapshot,
  type Candle,
  type PaperSessionState,
  type PaperStatus,
  walkHold,
} from "@/lib/paper-core";
import type { PaperSessionStatus } from "@/lib/paper-session";

const g = globalThis as typeof globalThis & {
  __aiTraderBrowser?: PaperSessionState | null;
  __aiTraderBrowserTimer?: number;
};

function store(next: PaperSessionState | null) {
  g.__aiTraderBrowser = next;
}

function current(): PaperSessionState | null {
  return g.__aiTraderBrowser ?? null;
}

function asUi(status: PaperStatus): PaperSessionStatus {
  return status as unknown as PaperSessionStatus;
}

async function loadCandles(symbol: string, timeframe: string, limit: number): Promise<Candle[]> {
  const { url, product, granularity } = coinbaseUrl(symbol, timeframe);
  const response = await raceTimeout(
    fetch(url, {
      headers: { Accept: "application/json" },
      cache: "no-store",
    }),
    8000,
    "Public market data timed out.",
  );
  if (!response.ok) throw new Error(`Public market data unavailable (${response.status}).`);
  const payload = await response.json();
  return parseCoinbaseRows(payload, { product, granularity, limit });
}

function grokUrl() {
  return new URL("/api/paper-session/grok", window.location.href).href;
}

async function askGrok(session: PaperSessionState, candle: Candle): Promise<{ action: string; model: string }> {
  const response = await raceTimeout(
    fetch(grokUrl(), {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      cache: "no-store",
      body: JSON.stringify({
        symbol: session.symbol,
        equity: session.equity,
        candle,
        live: false,
      }),
    }),
    8000,
    "Grok paper analysis timed out.",
  );
  if (!response.ok) return { action: "HOLD", model: "fixture-hold" };
  const json = (await response.json()) as { action?: string; model?: string };
  const action = String(json.action || "HOLD").toUpperCase();
  return {
    action: action === "BUY" || action === "SELL" || action === "HOLD" ? action : "HOLD",
    model: String(json.model || "fixture-hold"),
  };
}

async function grokInBackground(sessionId: string, candle: Candle) {
  const session = current();
  if (!session || session.id !== sessionId || session.stopped) return;
  try {
    const grok = await askGrok(session, candle);
    const live = current();
    if (!live || live.id !== sessionId || live.stopped) return;
    live.grokModel = grok.model === "fixture-hold" ? "fixture-hold" : "real Grok";
    queueDecision(live, grok.action);
    store(live);
  } catch {
    const live = current();
    if (!live || live.id !== sessionId || live.stopped) return;
    live.grokModel = "fixture-hold";
    if (!live.decision) live.decision = "HOLD";
    store(live);
  }
}

export async function startBrowserPaper(): Promise<PaperSessionStatus> {
  const symbol = "BTC-USD";
  const timeframe = "5m";
  const candles = await loadCandles(symbol, timeframe, 24);
  const session = newSession({ symbol, timeframe, engine: "browser" });
  walkHold(session, candles);
  store(session);
  const last = candles[candles.length - 1];
  const eligible = lastEligibleIndex(candles.length, 8, 8);
  if (last && eligible >= 0) {
    void grokInBackground(session.id, candles[eligible] || last);
  }
  return asUi(snapshot(session));
}

export async function stopBrowserPaper(): Promise<PaperSessionStatus> {
  const session = current();
  if (session) {
    session.running = false;
    session.stopped = true;
    store(session);
  }
  if (typeof g.__aiTraderBrowserTimer === "number") {
    window.clearInterval(g.__aiTraderBrowserTimer);
    g.__aiTraderBrowserTimer = undefined;
  }
  return asUi(
    snapshot(
      session ?? {
        ...newSession({ symbol: "BTC-USD", timeframe: "5m", engine: "browser", id: "stopped" }),
        running: false,
        stopped: true,
      },
    ),
  );
}

export function browserPaperStatus(): PaperSessionStatus | null {
  const session = current();
  return session ? asUi(snapshot(session)) : null;
}

export async function tickBrowserPaper(): Promise<PaperSessionStatus | null> {
  const session = current();
  if (!session || !session.running || session.stopped) return session ? asUi(snapshot(session)) : null;
  try {
    const candles = await loadCandles(session.symbol, session.timeframe, 24);
    const prevLast = session.bars[session.bars.length - 1]?.startMs;
    const nextLast = candles[candles.length - 1];
    if (nextLast && nextLast.startMs !== prevLast) {
      applyPendingFill(session, nextLast);
      markToMarket(session, nextLast.close);
      session.bars = candles;
      store(session);
      void grokInBackground(session.id, nextLast);
    } else if (nextLast) {
      markToMarket(session, nextLast.close);
      session.bars = candles;
      store(session);
    }
    return asUi(snapshot(session));
  } catch {
    return asUi(snapshot(session));
  }
}
