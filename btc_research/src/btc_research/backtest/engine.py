"""Chronological spot simulator.

At the open of bar t the engine rebalances to `targets[t]`, using only that
open as the fill reference. High, low and close are not used to decide the
trade. Close is used afterwards to mark the position to market.

Cash cannot go negative. Exposure cannot exceed `max_exposure` and cannot
exceed 1. Buys that would require borrowing are cut to the cash available
after fees and slippage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from btc_research.costs import ExecutionConfig
from btc_research.data.candles import Candle


@dataclass(frozen=True)
class Fill:
    time_ms: int
    side: str
    qty: float
    raw_open: float
    fill_price: float
    fee: float
    cash_after: float
    qty_after: float


@dataclass(frozen=True)
class RoundTrip:
    """One campaign from flat, back to flat. Unrealised inventory is not a win."""

    entry_time_ms: int
    exit_time_ms: int
    pnl: float
    cost_basis: float

    @property
    def return_on_cost(self) -> float:
        if self.cost_basis <= 0:
            return 0.0
        return self.pnl / self.cost_basis


@dataclass
class BacktestResult:
    equity: list[float]
    position_qty: list[float]
    exposure: list[float]
    fills: list[Fill] = field(default_factory=list)
    round_trips: list[RoundTrip] = field(default_factory=list)
    final_cash: float = 0.0
    final_qty: float = 0.0


def run_backtest(
    candles: Sequence[Candle],
    targets: Sequence[float],
    execution: ExecutionConfig,
    *,
    start: int = 0,
    end: int | None = None,
) -> BacktestResult:
    """Simulate bars [start, end).

    The account starts flat with `initial_equity` at `start`, even if targets
    were built with earlier history. That is the out-of-sample fold contract:
    indicator state may use the past, the account may not.
    """
    if end is None:
        end = len(candles)
    if not 0 <= start < end <= len(candles):
        raise ValueError(f"bad slice {start}:{end} for {len(candles)} bars")
    if len(targets) != len(candles):
        raise ValueError("targets must align with candles")

    cash = float(execution.initial_equity)
    qty = 0.0
    buy_factor = execution.cost.buy_factor
    sell_factor = execution.cost.sell_factor
    slip = execution.cost.slippage_rate
    fee_rate = execution.cost.fee_rate
    max_exposure = execution.max_exposure
    min_fraction = execution.min_trade_fraction

    equity: list[float] = []
    position_qty: list[float] = []
    exposure_path: list[float] = []
    fills: list[Fill] = []
    trips: list[RoundTrip] = []

    in_campaign = False
    cash_at_flat = cash
    campaign_basis = 0.0
    campaign_entry = 0

    for index in range(start, end):
        candle = candles[index]
        raw = candle.open
        if raw <= 0:
            raise ValueError(f"non-positive open at {candle.open_time_ms}")
        target = targets[index]
        if target != target or target < -1e-12 or target > 1.0 + 1e-12:
            raise ValueError(f"target {target} at {index} is outside [0, 1]")
        desired = min(max(target, 0.0), max_exposure)
        marked = cash + qty * raw
        desired_qty = 0.0 if marked <= 0 else desired * marked / raw
        delta = desired_qty - qty
        if marked > 0 and abs(delta) * raw < marked * min_fraction:
            delta = 0.0

        if delta > 0:
            max_buy = cash / (raw * buy_factor) if buy_factor > 0 else 0.0
            buy_qty = min(delta, max_buy)
            if buy_qty > 0:
                if qty <= 1e-12:
                    in_campaign = True
                    cash_at_flat = cash
                    campaign_basis = 0.0
                    campaign_entry = candle.open_time_ms
                fill_price = raw * (1.0 + slip)
                fee = buy_qty * fill_price * fee_rate
                cash -= buy_qty * raw * buy_factor
                qty += buy_qty
                campaign_basis += buy_qty * fill_price + fee
                fills.append(
                    Fill(
                        time_ms=candle.open_time_ms,
                        side="buy",
                        qty=buy_qty,
                        raw_open=raw,
                        fill_price=fill_price,
                        fee=fee,
                        cash_after=cash,
                        qty_after=qty,
                    )
                )
        elif delta < 0 and qty > 0:
            sell_qty = min(-delta, qty)
            if sell_qty > 0:
                fill_price = raw * (1.0 - slip)
                fee = sell_qty * fill_price * fee_rate
                cash += sell_qty * raw * sell_factor
                qty -= sell_qty
                fills.append(
                    Fill(
                        time_ms=candle.open_time_ms,
                        side="sell",
                        qty=sell_qty,
                        raw_open=raw,
                        fill_price=fill_price,
                        fee=fee,
                        cash_after=cash,
                        qty_after=qty,
                    )
                )
                if qty <= 1e-8:
                    qty = 0.0
                    if in_campaign:
                        trips.append(
                            RoundTrip(
                                entry_time_ms=campaign_entry,
                                exit_time_ms=candle.open_time_ms,
                                pnl=cash - cash_at_flat,
                                cost_basis=campaign_basis,
                            )
                        )
                    in_campaign = False
                    campaign_basis = 0.0

        mark = cash + qty * candle.close
        equity.append(mark)
        position_qty.append(qty)
        exposure_path.append(0.0 if mark <= 0 else (qty * candle.close) / mark)

    return BacktestResult(
        equity=equity,
        position_qty=position_qty,
        exposure=exposure_path,
        fills=fills,
        round_trips=trips,
        final_cash=cash,
        final_qty=qty,
    )
