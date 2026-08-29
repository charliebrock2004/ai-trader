import type { CandleSeries } from "@/lib/simulated-market";

export type MarketAnalysis = {
  symbol: string;
  timeframe: string;
  scenario: string;
  asOf: string;
  barCount: number;
  currentPrice: number | null;
  recentHigh: number | null;
  recentLow: number | null;
  trend: "UP" | "DOWN" | "SIDEWAYS" | "UNKNOWN";
  lastAbs: number | null;
  lastPct: number | null;
  lookbacks: Record<string, number | null>;
  sma5: number | null;
  sma10: number | null;
  sma20: number | null;
  sma50: number | null;
  slope5: number | null;
  slope10: number | null;
  slope20: number | null;
  lastRange: number | null;
  lastRangePct: number | null;
  averageRange: number | null;
  rollingVol: number | null;
  lastVolume: number | null;
  averageVolume: number | null;
  volumeVsAverage: number | null;
  notes: string;
};

const ANALYSIS_KEY = "ai-trader.analysis";
const SMA_WINDOWS = [5, 10, 20, 50] as const;
const RETURN_LOOKBACKS = [1, 5, 10, 20] as const;
const VOL_WINDOW = 20;
const SLOPE_SHIFT = 5;
const SLOPE_DEADZONE = 0.0025;

function mean(values: number[]) {
  if (!values.length) return null;
  return values.reduce((sum, item) => sum + item, 0) / values.length;
}

function sampleStdev(values: number[]) {
  if (values.length < 2) return null;
  const centre = mean(values);
  if (centre === null) return null;
  const variance = values.reduce((sum, item) => sum + (item - centre) ** 2, 0) / (values.length - 1);
  return Math.sqrt(variance);
}

function sma(closes: number[], window: number) {
  if (window <= 0 || closes.length < window) return null;
  return mean(closes.slice(-window));
}

function smaSlope(closes: number[], window: number) {
  if (closes.length < window + SLOPE_SHIFT) return null;
  const now = sma(closes, window);
  const then = sma(closes.slice(0, -SLOPE_SHIFT), window);
  if (now === null || then === null || then === 0) return null;
  return (now - then) / then;
}

function pctChange(closes: number[], lookback: number) {
  if (lookback <= 0 || closes.length <= lookback) return null;
  const start = closes[closes.length - 1 - lookback];
  if (start === 0) return null;
  return (closes[closes.length - 1] - start) / start;
}

function absChange(closes: number[], lookback: number) {
  if (lookback <= 0 || closes.length <= lookback) return null;
  return closes[closes.length - 1] - closes[closes.length - 1 - lookback];
}

function roundTo(value: number | null, digits: number) {
  if (value === null || !Number.isFinite(value)) return null;
  const factor = 10 ** digits;
  return Math.round(value * factor) / factor;
}

function classifyTrend(price: number | null, anchor: number | null, slope: number | null) {
  if (price === null || anchor === null || slope === null) return "UNKNOWN" as const;
  if (slope > SLOPE_DEADZONE) return "UP" as const;
  if (slope < -SLOPE_DEADZONE) return "DOWN" as const;
  return "SIDEWAYS" as const;
}

export function analyseSeries(series: CandleSeries): MarketAnalysis {
  const closes = series.candles.map((c) => c.close);
  const last = series.candles[series.candles.length - 1] ?? null;
  const price = last ? last.close : null;
  const smaValues: Record<number, number | null> = {};
  for (const window of SMA_WINDOWS) smaValues[window] = sma(closes, window);
  const slope5 = smaSlope(closes, 5);
  const slope10 = smaSlope(closes, 10);
  const slope20 = smaSlope(closes, 20);
  const lookbacks: Record<string, number | null> = {};
  for (const period of RETURN_LOOKBACKS) lookbacks[String(period)] = roundTo(pctChange(closes, period), 6);

  const lastRange = last ? last.high - last.low : null;
  const lastRangePct = last && last.close ? lastRange! / last.close : null;
  const recent = series.candles.slice(-VOL_WINDOW);
  const ranges = recent.map((c) => c.high - c.low);
  const returns: number[] = [];
  for (let i = 1; i < closes.length; i += 1) {
    if (closes[i - 1] === 0) continue;
    returns.push((closes[i] - closes[i - 1]) / closes[i - 1]);
  }
  const volumes = series.candles.map((c) => c.volume);
  const avgVolume = mean(volumes.slice(-VOL_WINDOW));
  const lastVolume = last ? last.volume : null;
  const vsAverage = lastVolume !== null && avgVolume ? lastVolume / avgVolume : null;
  const anchor = smaValues[20] ?? smaValues[10] ?? smaValues[5];
  const slope = smaValues[20] !== null ? slope20 : smaValues[10] !== null ? slope10 : slope5;

  return {
    symbol: series.symbol,
    timeframe: series.timeframe,
    scenario: series.scenario,
    asOf: last ? last.timestamp : "",
    barCount: series.candles.length,
    currentPrice: roundTo(price, 4),
    recentHigh: roundTo(series.candles.length ? Math.max(...series.candles.map((c) => c.high)) : null, 4),
    recentLow: roundTo(series.candles.length ? Math.min(...series.candles.map((c) => c.low)) : null, 4),
    trend: classifyTrend(price, anchor, slope),
    lastAbs: roundTo(absChange(closes, 1), 4),
    lastPct: roundTo(pctChange(closes, 1), 6),
    lookbacks,
    sma5: roundTo(smaValues[5], 4),
    sma10: roundTo(smaValues[10], 4),
    sma20: roundTo(smaValues[20], 4),
    sma50: roundTo(smaValues[50], 4),
    slope5: roundTo(slope5, 6),
    slope10: roundTo(slope10, 6),
    slope20: roundTo(slope20, 6),
    lastRange: roundTo(lastRange, 4),
    lastRangePct: roundTo(lastRangePct, 6),
    averageRange: roundTo(mean(ranges), 4),
    rollingVol: roundTo(sampleStdev(returns.slice(-VOL_WINDOW)), 6),
    lastVolume: roundTo(lastVolume, 2),
    averageVolume: roundTo(avgVolume, 2),
    volumeVsAverage: roundTo(vsAverage, 6),
    notes: "Read-only technical summary. Not a trade signal. No order is implied.",
  };
}

export function analyseTape(series: CandleSeries[]) {
  return series.map(analyseSeries);
}

export function readAnalysis(): MarketAnalysis[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(ANALYSIS_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as MarketAnalysis[];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

export function writeAnalysis(rows: MarketAnalysis[]) {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(ANALYSIS_KEY, JSON.stringify(rows));
}
