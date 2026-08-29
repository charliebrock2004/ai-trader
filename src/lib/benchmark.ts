export type StrategyRow = {
  strategy: string;
  return_pct: number;
  ending_balance: number;
  absolute_pnl: number;
  maximum_drawdown: number;
  trades: number;
  win_rate: number;
  profit_factor: number;
  volatility?: number;
  risk_adjusted_return?: number;
};

export type BenchmarkHeadline = {
  split: string;
  grok_return_pct: number;
  benchmark_return_pct: number;
  benchmark_name: string;
  maximum_drawdown: number;
  trades: number;
  win_rate: number;
  profit_factor: number;
  grok_model: string;
};

export type BenchmarkReport = {
  ok: boolean;
  banner: string;
  live: boolean;
  broker: string;
  grok_model: string;
  headline: BenchmarkHeadline;
  comparison: StrategyRow[];
  verdict: {
    beats_buy_and_hold: boolean;
    beats_simple_technical: boolean;
    beats_random: boolean;
    beats_all: boolean;
    grok_traded?: boolean;
  };
  splits?: Record<string, unknown>;
  notes?: string;
  available?: boolean;
};

export function formatPct(value: number, digits = 2) {
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(digits)}%`;
}

export function formatDrawdown(value: number) {
  return `${(value * 100).toFixed(2)}%`;
}

export function formatWinRate(value: number) {
  return `${(value * 100).toFixed(0)}%`;
}

export function formatPf(value: number) {
  if (value >= 100) return "—";
  return value.toFixed(2);
}

export function strategyLabel(name: string) {
  if (name === "BUY_AND_HOLD") return "Buy & hold";
  if (name === "SIMPLE_TECHNICAL") return "Simple technical";
  if (name === "RANDOM_BASELINE") return "Random";
  if (name === "GROK") return "Grok";
  return name;
}
