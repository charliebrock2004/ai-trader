export type DeskEvent = {
  id: number;
  createdAt: string;
  level: string;
  eventType: string;
  message: string;
};

export type PipelineStage = {
  id: string;
  title: string;
  status: string;
  detail: string;
};

export type ModuleRow = {
  id: string;
  title: string;
  status: string;
  detail: string;
};

const KILL_KEY = "ai-trader.killSwitch.engaged";
const EVENTS_KEY = "ai-trader.events";

export const PIPELINE: PipelineStage[] = [
  {
    id: "market_data",
    title: "Market Data",
    status: "active",
    detail: "Deterministic simulated OHLCV. No external market data.",
  },
  {
    id: "analysis",
    title: "Market / News Analysis",
    status: "active",
    detail: "Read-only SMAs, returns, trend, and volume. Not a trade signal.",
  },
  {
    id: "ai",
    title: "Grok AI",
    status: "fixture",
    detail: "Fixture HOLD by default. Real Grok is paper-analysis only when deliberately enabled.",
  },
  {
    id: "decision",
    title: "BUY / SELL / HOLD",
    status: "active",
    detail: "Fixture returns HOLD only. Proposals, not orders.",
  },
  {
    id: "paper_account",
    title: "Paper Account",
    status: "active",
    detail: "Offline £100 simulated cash. No fills. Read-only.",
  },
  {
    id: "risk",
    title: "Risk Engine",
    status: "active",
    detail: "Hard gate. Currently rejects every order.",
  },
  {
    id: "execution",
    title: "Paper Execution",
    status: "paper-only",
    detail: "Internal simulated fills. Broker adapters stay stubs. Live host blocked.",
  },
  {
    id: "database",
    title: "Trade & Event Log",
    status: "active",
    detail: "SQLite is initialised and recording system events.",
  },
];

export const MODULES: ModuleRow[] = [
  {
    id: "market_data",
    title: "Market data",
    status: "ready",
    detail: "Deterministic simulated OHLCV. Swap this provider later.",
  },
  {
    id: "analysis",
    title: "Analysis",
    status: "ready",
    detail: "Read-only technical summary. Swap later without touching execution.",
  },
  {
    id: "ai",
    title: "Grok AI",
    status: "fixture",
    detail: "Fixture HOLD by default. Real Grok is paper-analysis only when deliberately enabled.",
  },
  {
    id: "paper_account",
    title: "Paper account",
    status: "ready",
    detail: "Offline £100 simulated cash. No fills. Not live.",
  },
  {
    id: "risk",
    title: "Risk engine",
    status: "ready",
    detail: "Hard gate. AI cannot bypass this module.",
  },
  {
    id: "broker_simulated",
    title: "Simulated broker",
    status: "ready",
    detail: "Local stub. No orders are sent anywhere.",
  },
  {
    id: "broker_alpaca",
    title: "Alpaca paper",
    status: "held",
    detail: "Future adapter will authenticate against the Alpaca paper API only. The live API host is rejected by the safety module.",
  },
];

export const LEDGER = {
  decisions: 0,
  trades: 0,
  positions: 0,
  snapshots: 0,
};

function canUseStorage() {
  return typeof window !== "undefined";
}

export function readKillSwitch(): boolean {
  if (!canUseStorage()) return true;
  const raw = window.localStorage.getItem(KILL_KEY);
  if (raw === null) return true;
  return raw === "true";
}

export function writeKillSwitch(engaged: boolean) {
  if (!canUseStorage()) return;
  window.localStorage.setItem(KILL_KEY, engaged ? "true" : "false");
}

export function readEvents(): DeskEvent[] {
  if (!canUseStorage()) return [];
  try {
    const raw = window.localStorage.getItem(EVENTS_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as DeskEvent[];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

export function writeEvents(events: DeskEvent[]) {
  if (!canUseStorage()) return;
  window.localStorage.setItem(EVENTS_KEY, JSON.stringify(events.slice(0, 40)));
}

export function makeEvent(
  partial: Omit<DeskEvent, "id" | "createdAt"> & { id?: number; createdAt?: string },
): DeskEvent {
  return {
    id: partial.id ?? Date.now(),
    createdAt: partial.createdAt ?? new Date().toISOString(),
    level: partial.level,
    eventType: partial.eventType,
    message: partial.message,
  };
}
