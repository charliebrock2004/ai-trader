import type { MarketAnalysis } from "@/lib/analysis";

export type FixtureDecision = {
  symbol: string;
  action: "HOLD" | "BUY" | "SELL";
  confidence: number;
  reasoning: string;
  timestamp: string;
  analysisRef: string;
  model: string;
  risk?: string;
  execution?: string;
  broker?: string;
};

const DECISIONS_KEY = "ai-trader.decisions";

export function fixtureHold(analysis: MarketAnalysis, timestamp = new Date().toISOString()): FixtureDecision {
  const analysisRef = `${analysis.symbol}:${analysis.asOf}`;
  return {
    symbol: analysis.symbol,
    action: "HOLD",
    confidence: 1,
    reasoning:
      `Fixture Grok adapter — offline, no network. Always HOLD. This is not a forecast and not a trade. Analysis ${analysisRef}: trend ${analysis.trend}, price ${analysis.currentPrice}.`,
    timestamp,
    analysisRef,
    model: "fixture-hold",
  };
}

export function fixtureHolds(rows: MarketAnalysis[]) {
  const timestamp = new Date().toISOString();
  return rows.map((row) => fixtureHold(row, timestamp));
}

export function readDecisions(): FixtureDecision[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(DECISIONS_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as FixtureDecision[];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

export function writeDecisions(rows: FixtureDecision[]) {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(DECISIONS_KEY, JSON.stringify(rows));
}
