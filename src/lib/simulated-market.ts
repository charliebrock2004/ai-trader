export type Candle = {
  timestamp: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};

export type CandleSeries = {
  symbol: string;
  timeframe: string;
  scenario: string;
  seed: number;
  source: string;
  barCount: number;
  candles: Candle[];
};

type ScenarioSpec = {
  drift: number;
  vol: number;
  meanReversion: number;
  shockAt: number;
  shockMove: number;
  volumeBase: number;
};

const MOD = 2147483647;
const MUL = 48271;
const SERIES_START_MS = Date.UTC(2024, 0, 2, 14, 30, 0);

export const TIMEFRAME_SECONDS: Record<string, number> = {
  "1m": 60,
  "5m": 300,
  "15m": 900,
  "1h": 3600,
  "4h": 14400,
  "1d": 86400,
};

const SCENARIOS: Record<string, ScenarioSpec> = {
  uptrend: { drift: 0.00115, vol: 0.0055, meanReversion: 0, shockAt: -1, shockMove: 0, volumeBase: 1_100_000 },
  downtrend: { drift: -0.00115, vol: 0.0055, meanReversion: 0, shockAt: -1, shockMove: 0, volumeBase: 1_100_000 },
  sideways: { drift: 0, vol: 0.0032, meanReversion: 0.22, shockAt: -1, shockMove: 0, volumeBase: 750_000 },
  high_volatility: { drift: 0.00015, vol: 0.018, meanReversion: 0, shockAt: -1, shockMove: 0, volumeBase: 2_400_000 },
  shock: { drift: 0.00012, vol: 0.004, meanReversion: 0.04, shockAt: 0.62, shockMove: -0.085, volumeBase: 1_350_000 },
};

export const SYMBOL_SCENARIOS: Record<string, string> = {
  "SIM-UP": "uptrend",
  "SIM-DOWN": "downtrend",
  "SIM-FLAT": "sideways",
  "SIM-VOL": "high_volatility",
  "SIM-SHOCK": "shock",
};

export const DEFAULT_SYMBOLS = ["SIM-UP", "SIM-DOWN", "SIM-FLAT", "SIM-VOL", "SIM-SHOCK"] as const;

const MARKET_KEY = "ai-trader.market";

function round2(value: number) {
  return Math.floor(value * 100 + 0.5) / 100;
}

class LCG {
  state: number;
  constructor(seed: number) {
    let state = seed % MOD;
    if (state <= 0) state += MOD - 1;
    this.state = state;
  }
  next() {
    this.state = (this.state * MUL) % MOD;
    return (this.state - 1) / (MOD - 1);
  }
  gauss() {
    let u = this.next();
    const v = this.next();
    if (u < 1e-12) u = 1e-12;
    return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
  }
}

function mixSeed(base: number, symbol: string) {
  let h = base;
  for (const char of symbol.toUpperCase()) {
    h = (h * 33 + char.charCodeAt(0)) % MOD;
  }
  return h || 1;
}

function startPrice(symbol: string) {
  let h = 0;
  for (const char of symbol.toUpperCase()) {
    h = (h * 33 + char.charCodeAt(0)) % 100000;
  }
  return round2(48 + (h % 320) + (h % 90) / 100);
}

function isoUtc(ms: number) {
  return new Date(ms).toISOString().replace(".000Z", "+00:00");
}

export function generateSeries(
  symbol: string,
  options?: { timeframe?: string; limit?: number; seed?: number },
): CandleSeries {
  const ticker = symbol.toUpperCase();
  const timeframe = options?.timeframe ?? "5m";
  const seed = options?.seed ?? 42;
  const scenario = SYMBOL_SCENARIOS[ticker] ?? "uptrend";
  const spec = SCENARIOS[scenario];
  let count = options?.limit ?? 48;
  if (count < 2) count = 2;
  if (count > 500) count = 500;
  const stepMs = (TIMEFRAME_SECONDS[timeframe] ?? 300) * 1000;
  const rng = new LCG(mixSeed(seed, ticker));
  let price = startPrice(ticker);
  const origin = price;
  const candles: Candle[] = [];
  const shockIndex = spec.shockAt >= 0 ? Math.trunc(count * spec.shockAt) : -1;

  for (let index = 0; index < count; index += 1) {
    const deviation = (price - origin) / origin;
    let ret = spec.drift + spec.vol * rng.gauss() - spec.meanReversion * deviation;
    if (index === shockIndex) ret += spec.shockMove;
    const close = Math.max(0.5, price * (1 + ret));
    const gap = 0.12 * spec.vol * rng.gauss();
    const open = Math.max(0.5, price * (1 + gap));
    const wing = Math.abs(close - open) + spec.vol * price * (0.35 + 0.8 * rng.next());
    let high = Math.max(open, close) + wing * 0.55;
    let low = Math.min(open, close) - wing * 0.45;
    low = Math.max(0.25, low);
    if (high < Math.max(open, close, low)) high = Math.max(open, close, low);
    if (low > Math.min(open, close, high)) low = Math.min(open, close, high);
    let volume = spec.volumeBase * (0.65 + 0.7 * rng.next()) * (1 + 10 * Math.abs(ret));
    if (index === shockIndex) volume *= 3.4;
    candles.push({
      timestamp: isoUtc(SERIES_START_MS + index * stepMs),
      open: round2(open),
      high: round2(high),
      low: round2(low),
      close: round2(close),
      volume: Math.floor(Math.max(0, volume) + 0.5),
    });
    price = candles[candles.length - 1].close;
  }

  return {
    symbol: ticker,
    timeframe,
    scenario,
    seed,
    source: "simulated",
    barCount: candles.length,
    candles,
  };
}

export function generateDefaultTape(): CandleSeries[] {
  return DEFAULT_SYMBOLS.map((symbol) =>
    generateSeries(symbol, { timeframe: "5m", limit: 60, seed: 42 }),
  );
}

export function readMarket(): CandleSeries[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(MARKET_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as CandleSeries[];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

export function writeMarket(series: CandleSeries[]) {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(MARKET_KEY, JSON.stringify(series));
}
