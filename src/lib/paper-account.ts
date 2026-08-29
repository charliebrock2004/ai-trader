export type PaperAccountState = {
  currency: "GBP";
  starting_cash: number;
  cash: number;
  buying_power: number;
  account_equity: number;
  invested_value: number;
  realised_pnl: number;
  unrealised_pnl: number;
  total_pnl: number;
  positions: [];
  fill_count: number;
  source: "simulated-paper";
  as_of: string;
  live: false;
  drawdown?: number;
  daily_pnl?: number;
  peak_equity?: number;
  halted?: boolean;
};

const ACCOUNT_KEY = "ai-trader.paperAccount";

export const STARTING_CASH = 100;

export function initialPaperAccount(asOf = new Date().toISOString()): PaperAccountState {
  return {
    currency: "GBP",
    starting_cash: STARTING_CASH,
    cash: STARTING_CASH,
    buying_power: STARTING_CASH,
    account_equity: STARTING_CASH,
    invested_value: 0,
    realised_pnl: 0,
    unrealised_pnl: 0,
    total_pnl: 0,
    positions: [],
    fill_count: 0,
    source: "simulated-paper",
    as_of: asOf,
    live: false,
    drawdown: 0,
    daily_pnl: 0,
    peak_equity: STARTING_CASH,
    halted: false,
  };
}

export function readPaperAccount(): PaperAccountState | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(ACCOUNT_KEY);
    if (!raw) return null;
    return JSON.parse(raw) as PaperAccountState;
  } catch {
    return null;
  }
}

export function writePaperAccount(state: PaperAccountState) {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(ACCOUNT_KEY, JSON.stringify(state));
}
