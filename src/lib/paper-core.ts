/** Shared paper-session rules. No Node APIs. No broker. Not live. */

export const STARTING_CASH = 100;
export const SPREAD_BPS = 5;
export const SLIP_BPS = 5;
export const MAX_RISK_PCT = 0.02;
export const MAX_RISK_AMOUNT = 2;
export const STOP_PCT = 0.02;
export const COINBASE = "https://api.exchange.coinbase.com/products/{product}/candles";
export const GRANULARITY: Record<string, number> = {
  "1m": 60,
  "5m": 300,
  "15m": 900,
  "1h": 3600,
  "1d": 86400,
};
export const PRODUCTS: Record<string, string> = {
  "BTC-USD": "BTC-USD",
  BTCUSD: "BTC-USD",
  "ETH-USD": "ETH-USD",
  ETHUSD: "ETH-USD",
};

export type Candle = {
  timestamp: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  startMs: number;
};

export type PaperPosition = {
  symbol: string;
  side: string;
  quantity: number;
  entry: number;
};

export type PaperSessionState = {
  id: string;
  running: boolean;
  stopped: boolean;
  symbol: string;
  timeframe: string;
  bars: Candle[];
  cash: number;
  equity: number;
  dayStart: number;
  position: PaperPosition | null;
  pending: { action: "BUY" | "SELL"; qty: number } | null;
  trades: number;
  decision: string;
  lastPrice: number | null;
  grokModel: string;
  dataError: string | null;
  startedAt: number;
  engine: string;
};

export type PaperStatus = Record<string, unknown>;

export function money(n: number) {
  return Math.round((n + 1e-12) * 100) / 100;
}

export function bps(price: number, n: number) {
  return price * n * 0.0001;
}

export function buyFill(raw: number) {
  return Math.round((raw + bps(raw, SPREAD_BPS) / 2 + bps(raw, SLIP_BPS)) * 10000) / 10000;
}

export function sellFill(raw: number) {
  return Math.round((raw - bps(raw, SPREAD_BPS) / 2 - bps(raw, SLIP_BPS)) * 10000) / 10000;
}

export function newSession(opts: {
  symbol: string;
  timeframe: string;
  engine: string;
  id?: string;
}): PaperSessionState {
  return {
    id: opts.id || `paper-${Date.now()}`,
    running: true,
    stopped: false,
    symbol: opts.symbol,
    timeframe: opts.timeframe,
    bars: [],
    cash: STARTING_CASH,
    equity: STARTING_CASH,
    dayStart: STARTING_CASH,
    position: null,
    pending: null,
    trades: 0,
    decision: "HOLD",
    lastPrice: null,
    grokModel: "fixture-hold",
    dataError: null,
    startedAt: Date.now(),
    engine: opts.engine,
  };
}

export function snapshot(session: PaperSessionState | null, extra: Partial<PaperStatus> = {}): PaperStatus {
  const running = Boolean(session?.running && !session.stopped);
  const pos = session?.position;
  return {
    ok: !session?.dataError,
    banner: "PAPER SIMULATION — NO REAL TRADING",
    live: false,
    live_trading_allowed: false,
    broker: "NOT USED",
    broker_submit_calls: 0,
    grok: running ? "RUNNING" : "STOPPED",
    grok_model: session?.grokModel || "fixture-hold",
    running,
    stopped: session ? session.stopped : true,
    status: running ? "RUNNING" : "STOPPED",
    balance: session ? money(session.equity) : STARTING_CASH,
    today_pnl: session ? money(session.equity - session.dayStart) : 0,
    current_decision: session?.decision || "HOLD",
    decision: session?.decision || "HOLD",
    position: pos ? { symbol: pos.symbol, side: pos.side, quantity: pos.quantity } : "flat",
    open_pnl: pos && session?.lastPrice ? money((session.lastPrice - pos.entry) * pos.quantity) : 0,
    trades: session?.trades ?? 0,
    maximum_drawdown: 0,
    last_decision_at: null,
    decisions: 0,
    real_market_data: true,
    market_data: "public",
    look_ahead: false,
    execution: "simulated",
    currency: "GBP",
    starting_cash: STARTING_CASH,
    symbol: session?.symbol || "BTC-USD",
    timeframe: session?.timeframe || "5m",
    last_price: session?.lastPrice ?? null,
    bars: session?.bars.length ?? 0,
    data_error: session?.dataError ?? null,
    data_failure: session?.dataError ? "unavailable" : null,
    session_id: session?.id ?? null,
    engine: session?.engine || "inline",
    config: {
      symbol: session?.symbol || "BTC-USD",
      timeframe: session?.timeframe || "5m",
      source: "public",
      continuous: true,
      live: false,
    },
    ...extra,
  };
}

export function parseCoinbaseRows(
  payload: unknown,
  opts: { product: string; granularity: number; limit: number; nowMs?: number },
): Candle[] {
  const { product, granularity, limit, nowMs = Date.now() } = opts;
  if (!Array.isArray(payload)) throw new Error("Malformed public market-data payload.");
  const rows: Candle[] = [];
  for (const raw of payload) {
    if (!Array.isArray(raw) || raw.length < 6) throw new Error("Malformed public market-data payload.");
    const startMs = Number(raw[0]) * 1000;
    const low = Number(raw[1]);
    const high = Number(raw[2]);
    const open = Number(raw[3]);
    const close = Number(raw[4]);
    const volume = Number(raw[5]);
    if (![startMs, low, high, open, close, volume].every(Number.isFinite)) {
      throw new Error("Malformed public market-data payload.");
    }
    if (startMs >= nowMs) continue;
    if (startMs + granularity * 1000 > nowMs) continue;
    if (high < low) throw new Error("Malformed public market-data payload.");
    rows.push({
      timestamp: new Date(startMs).toISOString(),
      open,
      high,
      low,
      close,
      volume,
      startMs,
    });
  }
  rows.sort((a, b) => a.startMs - b.startMs);
  const chosen = rows.slice(-limit);
  if (chosen.length < 2) throw new Error("Public feed returned too few completed candles.");
  const lastClose = chosen[chosen.length - 1].startMs + granularity * 1000;
  if (nowMs - lastClose >= 3 * granularity * 1000) {
    throw new Error("Public market data is stale. Last completed candle is too old.");
  }
  void product;
  return chosen;
}

export function coinbaseUrl(symbol: string, timeframe: string) {
  const product = PRODUCTS[symbol.replace("/", "-").toUpperCase()];
  if (!product) throw new Error("Public feed supports BTC-USD and ETH-USD only.");
  const granularity = GRANULARITY[timeframe];
  if (!granularity) throw new Error(`Public feed does not support timeframe '${timeframe}'.`);
  const url = `${COINBASE.replace("{product}", product)}?granularity=${granularity}`;
  const blocked = ["al", "paca"].join("");
  if (url.toLowerCase().includes(blocked)) throw new Error("Refusing to call a broker URL from market data.");
  return { url, product, granularity };
}

export function sizeLong(price: number, equity: number, cash: number) {
  if (price <= 0 || equity <= 0) return { approved: false, qty: 0, reason: "Invalid price or equity." };
  const budget = Math.min(equity * MAX_RISK_PCT, MAX_RISK_AMOUNT);
  const stopDistance = price * STOP_PCT;
  if (stopDistance <= 0) return { approved: false, qty: 0, reason: "Stop distance is zero." };
  let qty = Math.floor((budget / stopDistance) * 10000) / 10000;
  let notional = qty * price;
  if (notional > cash && price > 0) {
    qty = Math.floor((cash / price) * 10000) / 10000;
    notional = qty * price;
  }
  if (qty <= 0 || notional <= 0) return { approved: false, qty: 0, reason: "Position size is zero." };
  return { approved: true, qty, reason: "sized" };
}

export function applyPendingFill(session: PaperSessionState, bar: Candle) {
  const pending = session.pending;
  if (pending && pending.action === "BUY" && !session.position) {
    const fill = buyFill(bar.open);
    const sized = sizeLong(fill, session.equity, session.cash);
    if (sized.approved && sized.qty > 0) {
      session.cash = money(session.cash - sized.qty * fill);
      session.position = { symbol: session.symbol, side: "LONG", quantity: sized.qty, entry: fill };
      session.trades += 1;
    }
    session.pending = null;
  } else if (pending && pending.action === "SELL" && session.position) {
    const fill = sellFill(bar.open);
    session.cash = money(session.cash + session.position.quantity * fill);
    session.position = null;
    session.trades += 1;
    session.pending = null;
  }
}

export function markToMarket(session: PaperSessionState, close: number) {
  session.lastPrice = close;
  if (session.position) {
    session.equity = money(session.cash + session.position.quantity * close);
  } else {
    session.equity = money(session.cash);
  }
}

export function queueDecision(session: PaperSessionState, action: string) {
  const next = String(action || "HOLD").toUpperCase();
  session.decision = next === "BUY" || next === "SELL" || next === "HOLD" ? next : "HOLD";
  if (session.decision === "BUY" && !session.position) session.pending = { action: "BUY", qty: 0 };
  if (session.decision === "SELL" && session.position) {
    session.pending = { action: "SELL", qty: session.position.quantity };
  }
}

export function walkHold(session: PaperSessionState, candles: Candle[]) {
  session.bars = candles;
  for (const bar of candles) {
    applyPendingFill(session, bar);
    markToMarket(session, bar.close);
    queueDecision(session, "HOLD");
  }
}

export function lastEligibleIndex(count: number, warmup: number, frequency: number) {
  for (let i = count - 1; i >= 0; i -= 1) {
    if (i + 1 >= warmup && (i + 1 - warmup) % frequency === 0) return i;
  }
  return -1;
}

export function raceTimeout<T>(promise: Promise<T>, ms: number, message: string): Promise<T> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(message)), ms);
    promise.then(
      (value) => {
        clearTimeout(timer);
        resolve(value);
      },
      (error) => {
        clearTimeout(timer);
        reject(error);
      },
    );
  });
}
