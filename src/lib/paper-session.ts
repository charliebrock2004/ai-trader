export type PaperSessionStatus = {
  ok: boolean;
  banner: string;
  live: boolean;
  broker: string;
  grok: "RUNNING" | "ACTIVE" | "STOPPED" | string;
  grok_model?: string;
  running?: boolean;
  stopped?: boolean;
  balance: number;
  today_pnl: number;
  decision?: string;
  current_decision?: string;
  position: "flat" | { symbol?: string; side?: string; quantity?: number };
  open_pnl: number;
  trades: number;
  maximum_drawdown: number;
  last_decision_at?: string | null;
  decisions?: number;
  real_market_data?: boolean;
  data_error?: string | null;
  data_failure?: string | null;
  status?: string;
  execution?: string;
  currency?: string;
};

export const EMPTY_SESSION: PaperSessionStatus = {
  ok: true,
  banner: "PAPER SIMULATION — NO REAL TRADING",
  live: false,
  broker: "NOT USED",
  grok: "STOPPED",
  grok_model: "fixture-hold",
  running: false,
  stopped: true,
  balance: 100,
  today_pnl: 0,
  current_decision: "HOLD",
  position: "flat",
  open_pnl: 0,
  trades: 0,
  maximum_drawdown: 0,
  last_decision_at: null,
  decisions: 0,
  real_market_data: false,
  status: "STOPPED",
  execution: "simulated",
  currency: "GBP",
};

export function moneyGbp(value: number) {
  return moneyAmount(value, "GBP");
}

export function moneyAmount(value: number, currency = "GBP") {
  const n = Number(value) || 0;
  const sign = n < 0 ? "−" : "";
  const symbol = currency === "USD" ? "$" : "£";
  return `${sign}${symbol}${Math.abs(n).toFixed(2)}`;
}

export function formatDrawdownPct(value: number) {
  return `${((Number(value) || 0) * 100).toFixed(2)}%`;
}

export function positionLabel(pos: PaperSessionStatus["position"]) {
  if (!pos || pos === "flat") return "flat";
  return `${pos.symbol ?? ""} ${pos.side ?? "LONG"} ${pos.quantity ?? ""}`.trim();
}
