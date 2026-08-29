import { generateSeries, type Candle, type CandleSeries } from "@/lib/simulated-market";
import { initialPaperAccount, type PaperAccountState } from "@/lib/paper-account";

export type PaperOrder = {
  order_id: string;
  symbol: string;
  side: string;
  quantity: number;
  requested_price: number;
  filled_price: number | null;
  stop_loss: number | null;
  take_profit: number | null;
  timestamp: string;
  status: string;
  reason: string;
};

export type PaperFill = {
  fill_id: string;
  order_id: string;
  symbol: string;
  side: string;
  quantity: number;
  price: number;
  timestamp: string;
  reason: string;
};

export type PaperPosition = {
  symbol: string;
  side: string;
  quantity: number;
  average_entry: number;
  current_price: number;
  stop_loss: number | null;
  take_profit: number | null;
  unrealised_pnl: number;
  realised_pnl: number;
  position_value: number;
  entry_timestamp: string;
  exit_timestamp: string | null;
  open: boolean;
};

export type Performance = {
  total_trades: number;
  winning_trades: number;
  losing_trades: number;
  win_rate: number;
  profit_factor: number;
  average_win: number;
  average_loss: number;
  maximum_drawdown: number;
  return_pct: number;
};

export type PaperReport = {
  account: PaperAccountState;
  orders: PaperOrder[];
  fills: PaperFill[];
  positions: PaperPosition[];
  closed: PaperPosition[];
  performance: Performance;
  risk: {
    max_risk_pct: number;
    max_risk_amount: number;
    max_daily_loss_pct: number;
    max_open_positions: number;
    max_trades_per_day: number;
    leverage: number;
    kill_switch: boolean;
    trading_mode: string;
  };
};

const KEY = "ai-trader.paperSim";
const SPREAD_BPS = 5;
const SLIP_BPS = 5;
const BPS = 0.0001;

function money(value: number) {
  return Math.round((value + 1e-12) * 100) / 100;
}
function floorQty(value: number) {
  return Math.floor(value * 10000) / 10000;
}
function buyPx(raw: number) {
  return Math.round((raw + (raw * SPREAD_BPS * BPS) / 2 + raw * SLIP_BPS * BPS) * 10000) / 10000;
}
function sellPx(raw: number) {
  return Math.round((raw - (raw * SPREAD_BPS * BPS) / 2 - raw * SLIP_BPS * BPS) * 10000) / 10000;
}

function resolveHit(sl: number | null, tp: number | null, candle: Candle) {
  const stop = sl != null && candle.low <= sl;
  const target = tp != null && candle.high >= tp;
  if (stop && target) return "stop" as const;
  if (stop) return "stop" as const;
  if (target) return "target" as const;
  return null;
}

export function runPaperDemo(symbol = "SIM-UP"): PaperReport {
  const series: CandleSeries = generateSeries(symbol, { timeframe: "5m", limit: 60, seed: 42 });
  const candles = series.candles;
  let cash = 100;
  const starting = 100;
  let realised = 0;
  let peak = 100;
  let maxDd = 0;
  let seq = 0;
  let fillSeq = 0;
  const orders: PaperOrder[] = [];
  const fills: PaperFill[] = [];
  const closed: PaperPosition[] = [];
  let position: PaperPosition | null = null;
  let pending: PaperOrder | null = null;

  function equity() {
    const invested = position ? money(position.quantity * position.current_price) : 0;
    return money(cash + invested);
  }

  for (let i = 0; i < candles.length; i += 1) {
    const candle = candles[i];
    if (pending) {
      const price = buyPx(candle.open);
      const cost = money(pending.quantity * price);
      if (cost <= cash + 0.001) {
        cash = money(cash - cost);
        pending.status = "FILLED";
        pending.filled_price = price;
        fills.push({
          fill_id: `FIL-${String((fillSeq += 1)).padStart(4, "0")}`,
          order_id: pending.order_id,
          symbol,
          side: "BUY",
          quantity: pending.quantity,
          price,
          timestamp: candle.timestamp,
          reason: "ENTRY",
        });
        position = {
          symbol,
          side: "LONG",
          quantity: pending.quantity,
          average_entry: price,
          current_price: price,
          stop_loss: pending.stop_loss,
          take_profit: pending.take_profit,
          unrealised_pnl: 0,
          realised_pnl: 0,
          position_value: money(pending.quantity * price),
          entry_timestamp: candle.timestamp,
          exit_timestamp: null,
          open: true,
        };
      } else {
        pending.status = "REJECTED";
        pending.reason = "Insufficient cash at fill.";
      }
      pending = null;
    }

    if (position) {
      const hit = resolveHit(position.stop_loss, position.take_profit, candle);
      if (hit) {
        const raw = hit === "stop" ? position.stop_loss! : position.take_profit!;
        const price = sellPx(raw);
        const pnl = money((price - position.average_entry) * position.quantity);
        cash = money(cash + money(position.quantity * price));
        realised = money(realised + pnl);
        fills.push({
          fill_id: `FIL-${String((fillSeq += 1)).padStart(4, "0")}`,
          order_id: position.symbol,
          symbol,
          side: "SELL",
          quantity: position.quantity,
          price,
          timestamp: candle.timestamp,
          reason: hit === "stop" ? "STOP" : "TARGET",
        });
        const done = { ...position, open: false, realised_pnl: pnl, current_price: price, exit_timestamp: candle.timestamp, unrealised_pnl: 0 };
        closed.push(done);
        const orig = orders.find((o) => o.status === "FILLED");
        if (orig) orig.status = "CLOSED";
        position = null;
      } else {
        position.current_price = candle.close;
        position.unrealised_pnl = money((candle.close - position.average_entry) * position.quantity);
        position.position_value = money(position.quantity * candle.close);
      }
    }

    const eq = equity();
    if (eq > peak) peak = eq;
    const dd = peak > 0 ? (peak - eq) / peak : 0;
    if (dd > maxDd) maxDd = dd;

    if (!position && !pending && i === 20) {
      const price = candle.close;
      const stopDistance = Math.round(price * 0.02 * 10000) / 10000;
      const budget = 2;
      let qty = floorQty(budget / stopDistance);
      const est = buyPx(price);
      const affordable = floorQty(cash / (est * 1.02));
      if (affordable < qty) qty = affordable;
      const order: PaperOrder = {
        order_id: `PAP-${String((seq += 1)).padStart(4, "0")}`,
        symbol,
        side: "BUY",
        quantity: qty,
        requested_price: price,
        filled_price: null,
        stop_loss: Math.round((price - stopDistance) * 10000) / 10000,
        take_profit: Math.round((price + stopDistance * 2) * 10000) / 10000,
        timestamp: candle.timestamp,
        status: "PENDING",
        reason: "Sized within paper risk limits.",
      };
      orders.push(order);
      pending = order;
    }
  }

  if (pending) {
    pending.status = "CANCELLED";
    pending.reason = "No next bar to fill. Fill not guaranteed.";
    pending = null;
  }
  if (position) {
    const last = candles[candles.length - 1];
    const price = sellPx(last.close);
    const pnl = money((price - position.average_entry) * position.quantity);
    cash = money(cash + money(position.quantity * price));
    realised = money(realised + pnl);
    closed.push({ ...position, open: false, realised_pnl: pnl, current_price: price, exit_timestamp: last.timestamp, unrealised_pnl: 0 });
    position = null;
  }

  const wins = closed.filter((p) => p.realised_pnl > 0);
  const losses = closed.filter((p) => p.realised_pnl < 0);
  const grossWin = wins.reduce((s, p) => s + p.realised_pnl, 0);
  const grossLoss = Math.abs(losses.reduce((s, p) => s + p.realised_pnl, 0));
  const eq = money(cash);
  const account: PaperAccountState = {
    ...initialPaperAccount(candles[candles.length - 1]?.timestamp),
    cash,
    buying_power: cash,
    account_equity: eq,
    invested_value: 0,
    realised_pnl: realised,
    unrealised_pnl: 0,
    total_pnl: realised,
    positions: [],
    fill_count: fills.length,
    drawdown: Math.round(maxDd * 1e6) / 1e6,
    daily_pnl: money(eq - starting),
    peak_equity: peak,
    halted: false,
  };

  return {
    account,
    orders,
    fills,
    positions: [],
    closed,
    performance: {
      total_trades: closed.length,
      winning_trades: wins.length,
      losing_trades: losses.length,
      win_rate: closed.length ? Math.round((wins.length / closed.length) * 10000) / 10000 : 0,
      profit_factor: grossLoss ? Math.round((grossWin / grossLoss) * 10000) / 10000 : grossWin ? 999 : 0,
      average_win: wins.length ? money(grossWin / wins.length) : 0,
      average_loss: losses.length ? money(grossLoss / losses.length) * -1 : 0,
      maximum_drawdown: Math.round(maxDd * 1e6) / 1e6,
      return_pct: money(((eq - starting) / starting) * 100),
    },
    risk: {
      max_risk_pct: 0.02,
      max_risk_amount: 2,
      max_daily_loss_pct: 0.05,
      max_open_positions: 2,
      max_trades_per_day: 10,
      leverage: 0,
      kill_switch: false,
      trading_mode: "simulate",
    },
  };
}

export function readPaperSim(): PaperReport | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(KEY);
    return raw ? (JSON.parse(raw) as PaperReport) : null;
  } catch {
    return null;
  }
}

export function writePaperSim(report: PaperReport) {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(KEY, JSON.stringify(report));
}
