"""Mutable paper ledger. Offline only. No broker, no withdrawals, no leverage."""

from __future__ import annotations

from typing import Optional

from ai_trader.account.simulated import CURRENCY, SOURCE, STARTING_CASH
from ai_trader.paper.models import PaperFill, PaperOrder, PaperPosition
from ai_trader.types import PaperAccountState, utc_now_iso


def money(value: float) -> float:
    return round(float(value) + 1e-12, 2)


class PaperLedger:
    def __init__(self, *, starting_cash: float = STARTING_CASH) -> None:
        cash = money(starting_cash)
        self.starting_cash = cash
        self.cash = cash
        self.realised_pnl = 0.0
        self.peak_equity = cash
        self.day_start_equity = cash
        self.day_key = ""
        self.trades_today = 0
        self.halted = False
        self.positions: dict[str, PaperPosition] = {}
        self.closed_positions: list[PaperPosition] = []
        self.orders: list[PaperOrder] = []
        self.fills: list[PaperFill] = []
        self._order_seq = 0
        self._fill_seq = 0

    def next_order_id(self) -> str:
        self._order_seq += 1
        return f"PAP-{self._order_seq:04d}"

    def next_fill_id(self) -> str:
        self._fill_seq += 1
        return f"FIL-{self._fill_seq:04d}"

    def open_positions(self) -> list[PaperPosition]:
        return [p for p in self.positions.values() if p.open]

    def invested_value(self) -> float:
        return money(sum(p.position_value for p in self.open_positions()))

    def unrealised_pnl(self) -> float:
        return money(sum(p.unrealised_pnl for p in self.open_positions()))

    def equity(self) -> float:
        return money(self.cash + self.invested_value())

    def daily_pnl(self) -> float:
        return money(self.equity() - self.day_start_equity)

    def drawdown(self) -> float:
        if self.peak_equity <= 0:
            return 0.0
        return round(max(0.0, (self.peak_equity - self.equity()) / self.peak_equity), 6)

    def roll_day(self, timestamp: str) -> None:
        key = timestamp[:10]
        if not self.day_key:
            self.day_key = key
            self.day_start_equity = self.equity()
            return
        if key != self.day_key:
            self.day_key = key
            self.day_start_equity = self.equity()
            self.trades_today = 0
            self.halted = False

    def mark(self, symbol: str, price: float) -> None:
        pos = self.positions.get(symbol)
        if pos and pos.open:
            pos.current_price = price
        eq = self.equity()
        if eq > self.peak_equity:
            self.peak_equity = eq

    def apply_buy(self, order: PaperOrder, fill: PaperFill) -> None:
        cost = money(fill.quantity * fill.price)
        if cost > self.cash + 0.001:
            raise ValueError("Insufficient cash for paper fill.")
        self.cash = money(self.cash - cost)
        self.positions[fill.symbol] = PaperPosition(
            symbol=fill.symbol,
            quantity=fill.quantity,
            average_entry=fill.price,
            current_price=fill.price,
            stop_loss=order.stop_loss,
            take_profit=order.take_profit,
            entry_timestamp=fill.timestamp,
            order_id=order.order_id,
        )
        self.fills.append(fill)
        self.trades_today += 1
        order.status = "FILLED"
        order.filled_price = fill.price
        order.filled_at = fill.timestamp

    def close_position(
        self,
        symbol: str,
        fill: PaperFill,
        *,
        reason: str,
    ) -> PaperPosition:
        pos = self.positions.get(symbol)
        if not pos or not pos.open:
            raise ValueError("No open position to close.")
        proceeds = money(fill.quantity * fill.price)
        pnl = money((fill.price - pos.average_entry) * fill.quantity)
        self.cash = money(self.cash + proceeds)
        self.realised_pnl = money(self.realised_pnl + pnl)
        pos.open = False
        pos.current_price = fill.price
        pos.exit_timestamp = fill.timestamp
        pos.realised_pnl = pnl
        pos.quantity = fill.quantity
        self.closed_positions.append(pos)
        del self.positions[symbol]
        self.fills.append(fill)
        self.trades_today += 1
        return pos

    def snapshot(self, as_of: Optional[str] = None) -> PaperAccountState:
        as_of = as_of or utc_now_iso()
        invested = self.invested_value()
        unreal = self.unrealised_pnl()
        total = money(self.realised_pnl + unreal)
        eq = money(self.cash + invested)
        return PaperAccountState(
            currency=CURRENCY,
            starting_cash=self.starting_cash,
            cash=self.cash,
            buying_power=self.cash,
            account_equity=eq,
            invested_value=invested,
            realised_pnl=self.realised_pnl,
            unrealised_pnl=unreal,
            total_pnl=total,
            positions=tuple(p.to_dict() for p in self.open_positions()),
            fill_count=len(self.fills),
            source=SOURCE,
            as_of=as_of,
            drawdown=self.drawdown(),
            daily_pnl=self.daily_pnl(),
            peak_equity=self.peak_equity,
            halted=self.halted,
        )
