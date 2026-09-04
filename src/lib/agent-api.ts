/**
 * Typed access to the engine's real state.
 *
 * There is no fallback snapshot and no localStorage anywhere in this file. If
 * the engine cannot be reached the UI shows that it cannot be reached — a
 * dashboard that invents a number is worse than one that admits it is blind.
 */

export type SurvivalStateName = "HEALTHY" | "CAUTION" | "DEFENSIVE" | "CRITICAL" | "TERMINAL";

export type SurvivalSnapshot = {
  state: SurvivalStateName;
  terminated: boolean;
  equity: number;
  starting_equity: number;
  highest_equity: number;
  terminal_threshold: number;
  base_currency: string;
  distance_to_terminal: number;
  life_remaining_pct: number;
  drawdown_from_peak_pct: number;
  policy: {
    risk_multiplier: number;
    min_edge: number;
    max_exposure_pct: number;
    max_premium_pct: number;
    max_new_positions_per_day: number;
    description: string;
  };
  thresholds: Record<string, number>;
  latch: { terminated: boolean; reason?: string; at?: string | null };
};

export type AgentStatus = {
  ok: boolean;
  live: false;
  alive: boolean;
  terminated: boolean;
  banner: string;
  survival: SurvivalSnapshot;
  born_at: string | null;
  age_days: number | null;
  account: {
    base_currency: string;
    starting_cash: number;
    cash: number;
    equity: number;
    premium_at_risk: number;
    total_exposure: number;
    realised_pnl: number;
    fees_paid: number;
    drawdown: number;
    daily_pnl: number;
    open_positions: OpenPosition[];
  };
  costs: {
    operating_costs: number;
    net_pnl: number;
    gross_trading_pnl: number;
    daily_burn: number;
    runway_days: number | null;
    spendable_capital: number;
    self_sustaining: boolean;
    costs_by_category: Record<string, number>;
  };
  milestones: { key: string; label: string; equity: number }[];
  next_milestone: { key: string; label: string; equity: number } | null;
  decisions: Record<string, number>;
  last_decision: DecisionRow | null;
  last_cycle: CycleSummary | null;
  last_error: string | null;
  open_positions: OpenPosition[];
  config: {
    starting_equity: number;
    base_currency: string;
    terminal_threshold: number;
  };
  running?: boolean;
  stopped?: boolean;
  worker_alive?: boolean;
  session_ready?: boolean;
  status?: string;
  grok?: string;
  balance?: number;
  today_pnl?: number;
  current_decision?: string;
  decision?: string;
  position?: string | { symbol?: string; side?: string; quantity?: number };
  open_pnl?: number;
  trades?: number;
  symbol?: string;
  timeframe?: string;
  last_price?: number | null;
  hold_reason?: string | null;
  data_error?: string | null;
  engine?: string;
  currency?: string;
  persistence?: {
    kind?: string;
    durable?: boolean;
    restored?: boolean;
    warning?: string | null;
    updated_at?: string | null;
  };
};

export type OpenPosition = {
  position_id: string;
  ticker: string;
  event_key: string;
  side: string;
  contracts: number;
  average_price: number;
  premium_base: number;
  max_loss_base: number;
  max_gain_base: number;
  opened_at: string;
};

export type CycleSummary = {
  cycle_id: string;
  started_at: string;
  finished_at: string | null;
  survival_state: string;
  contracts_considered: number;
  analyst_calls: number;
  traded: number;
  rejected: number;
  errors: string[];
  shortlisted: string[];
};

export type DecisionRow = {
  id: number;
  created_at: string;
  cycle_id: string;
  ticker: string | null;
  event_key: string | null;
  model_probability: number | null;
  market_probability: number | null;
  net_edge: number | null;
  gross_edge: number | null;
  fees: number | null;
  spread: number | null;
  liquidity: number | null;
  ai_model: string | null;
  ai_action: string | null;
  ai_confidence: number | null;
  ai_bull: string | null;
  ai_bear: string | null;
  ai_invalidators: string | null;
  ai_failure: string | null;
  proposed_action: string | null;
  policy_action: string | null;
  policy_reason: string | null;
  survival_state: string | null;
  risk_approved: number;
  risk_reason: string | null;
  risk_json: string | null;
  final_action: string;
  executed: number;
  order_ref: string | null;
  stage: string | null;
  equity_before: number | null;
  base_currency: string | null;
  notes: string | null;
};

export type DecisionDetail = DecisionRow & {
  inputs: { name: string; kind: string; value_json: string; source: string | null }[];
  outcome: {
    predicted_probability: number;
    resolved_outcome: number;
    resolved_at: string;
    brier: number;
    correct: number;
    realised_pnl_base: number | null;
    predicted_edge: number | null;
    realised_edge: number | null;
  } | null;
  positions: Record<string, unknown>[];
};

export type CalibrationBucket = {
  label: string;
  count: number;
  mean_predicted: number;
  observed_rate: number;
  gap: number;
};

export type Performance = {
  starting_equity: number;
  equity: number;
  total_return_pct: number | null;
  gross_pnl: number;
  operating_costs: number;
  net_pnl: number;
  fees: number;
  self_sustaining: boolean;
  trades: number;
  wins: number;
  losses: number;
  win_rate: number | null;
  average_win: number | null;
  average_loss: number | null;
  expectancy: number | null;
  profit_factor: number | null;
  max_drawdown_pct: number;
  sharpe_like: number | null;
  opportunities_considered: number;
  opportunities_executed: number;
  opportunities_rejected: number;
  conversion_rate: number | null;
  average_predicted_edge: number | null;
  average_realised_edge: number | null;
  brier: number | null;
  evidence_note: string;
  calibration: {
    count: number;
    brier: number | null;
    skill_score: number | null;
    accuracy: number | null;
    expected_calibration_error: number | null;
    buckets: CalibrationBucket[];
    verdict: string;
  };
};

export type SystemStatus = {
  ok: boolean;
  live: false;
  paper_only: boolean;
  control_enabled: boolean;
  /** True when this frontend is proxying to a persistent worker over HTTPS. */
  worker_connected?: boolean;
  /** True when page access is the only thing gating the Start button. */
  frontend_open?: boolean;
  components: { id: string; title: string; ok: boolean; detail: string }[];
  last_error: string | null;
  last_cycle_at: string | null;
  survival: SurvivalSnapshot;
  strategy: string;
  event_family: string;
};

export class ApiError extends Error {
  // A plain field rather than a constructor parameter property: Node's
  // strip-only TypeScript mode (used by `npm test`) cannot compile the latter.
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function get<T>(path: string): Promise<T> {
  let lastError: Error | null = null;
  for (let attempt = 1; attempt <= 4; attempt += 1) {
    try {
      const response = await fetch(path, {
        headers: { accept: "application/json" },
        cache: "no-store",
        signal: AbortSignal.timeout(12_000),
      });
      const body = await readBody<T>(response);
      const row = body as { engine?: unknown };
      if (
        attempt < 4 &&
        (row.engine === "sleeping" || row.engine === "unreachable" || row.engine === "unavailable")
      ) {
        await new Promise((resolve) => setTimeout(resolve, 4000));
        continue;
      }
      return body;
    } catch (error) {
      lastError = error instanceof Error ? error : new Error(String(error));
      const text = lastError.message.toLowerCase();
      const retryable =
        text.includes("timeout") ||
        text.includes("timed out") ||
        text.includes("abort") ||
        text.includes("asleep") ||
        text.includes("not responding") ||
        text.includes("failed (503)") ||
        text.includes("failed (502)");
      if (!retryable || attempt === 4) throw lastError;
      await new Promise((resolve) => setTimeout(resolve, 4000));
    }
  }
  throw lastError ?? new Error("Could not reach the paper engine.");
}

async function mutate<T>(path: string, body?: unknown, timeoutMs = 25000): Promise<T> {
  let lastError: Error | null = null;
  for (let attempt = 1; attempt <= 4; attempt += 1) {
    try {
      const response = await fetch(path, {
        method: "POST",
        headers: { accept: "application/json", "content-type": "application/json" },
        cache: "no-store",
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: AbortSignal.timeout(timeoutMs),
      });
      return await readBody<T>(response);
    } catch (error) {
      lastError = error instanceof Error ? error : new Error(String(error));
      const text = lastError.message.toLowerCase();
      const retryable =
        text.includes("timeout") ||
        text.includes("timed out") ||
        text.includes("abort") ||
        text.includes("asleep") ||
        text.includes("not responding") ||
        text.includes("failed (503)") ||
        text.includes("failed (502)");
      if (!retryable || attempt === 4) throw lastError;
      await new Promise((resolve) => setTimeout(resolve, 4000));
    }
  }
  throw lastError ?? new Error("Start failed.");
}

async function readBody<T>(response: Response): Promise<T> {
  const text = await response.text();
  let body: unknown = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    throw new ApiError("The engine returned a response that was not JSON.", response.status);
  }
  if (!response.ok) {
    const row = body as { error?: string; data_error?: string } | null;
    throw new ApiError(
      row?.error ?? row?.data_error ?? `Request failed (${response.status}).`,
      response.status,
    );
  }
  return body as T;
}

export const api = {
  agent: () => get<AgentStatus>("/api/agent"),
  start: () =>
    mutate<AgentStatus>("/api/paper-session/start", {
      symbol: "BTC-USD",
      source: "public",
      bars: 24,
      timeframe: "5m",
      grok_frequency: 8,
      warmup: 8,
      continuous: true,
    }),
  stop: () => mutate<AgentStatus>("/api/paper-session/stop", undefined, 8000),
  performance: () => get<Performance>("/api/performance"),
  system: () => get<SystemStatus>("/api/system"),
  decisions: (limit = 50) => get<{ decisions: DecisionRow[] }>(`/api/decisions?limit=${limit}`),
  decision: (id: number) => get<{ ok: boolean; decision: DecisionDetail }>(`/api/decisions/${id}`),
};

// ---------------------------------------------------------------------------
// Formatting
// ---------------------------------------------------------------------------
export function money(value: number | null | undefined, currency = "GBP"): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  const symbol = currency === "GBP" ? "£" : currency === "USD" ? "$" : "";
  const sign = value < 0 ? "−" : "";
  return `${sign}${symbol}${Math.abs(value).toFixed(2)}`;
}

export function signedMoney(value: number | null | undefined, currency = "GBP"): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  const base = money(Math.abs(value), currency);
  if (value > 0) return `+${base}`;
  if (value < 0) return `−${base}`;
  return base;
}

export function percent(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return `${value.toFixed(digits)}%`;
}

export function probability(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return `${(value * 100).toFixed(1)}%`;
}

export function points(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  const sign = value > 0 ? "+" : value < 0 ? "−" : "";
  return `${sign}${(Math.abs(value) * 100).toFixed(2)}pp`;
}

export function ago(iso: string | null | undefined): string {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  if (!Number.isFinite(then)) return "—";
  const seconds = Math.max(0, Math.round((Date.now() - then) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

export function shortDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString(undefined, {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}
