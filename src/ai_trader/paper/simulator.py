"""Sequential paper simulator. No look-ahead. No broker. No network."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable, Optional

from ai_trader.account.simulated import STARTING_CASH
from ai_trader.analysis.technical import analyse_series
from ai_trader.paper.execution import (
    ASSUMPTIONS,
    SPREAD_BPS,
    SLIPPAGE_BPS,
    buy_fill_price,
    resolve_intrabar,
    sell_fill_price,
)
from ai_trader.paper.ledger import PaperLedger
from ai_trader.paper.models import (
    FillReason,
    OrderStatus,
    PaperAction,
    PaperFill,
    PaperOrder,
)
from ai_trader.paper.performance import summarise
from ai_trader.paper.signals import FixtureHoldSource, SignalSource
from ai_trader.risk.engine import RiskEngine
from ai_trader.types import Candle, CandleSeries


class PaperSimulator:
    def __init__(
        self,
        *,
        starting_cash: float = STARTING_CASH,
        risk: Optional[RiskEngine] = None,
        spread_bps: float = SPREAD_BPS,
        slip_bps: float = SLIPPAGE_BPS,
        flatten_at_end: bool = True,
    ) -> None:
        self.ledger = PaperLedger(starting_cash=starting_cash)
        self.risk = risk or RiskEngine(allow_orders=False)
        self.spread_bps = spread_bps
        self.slip_bps = slip_bps
        self.flatten_at_end = flatten_at_end
        self.pending: Optional[PaperOrder] = None
        self.events: list[dict[str, Any]] = []
        self.kill_switch = False
        self.max_drawdown = 0.0
        self.seen_future = False  # invariant: must stay False
        self.equity_curve: list[dict[str, Any]] = []
        self.stopped_at: Optional[int] = None

    def _note(self, kind: str, **payload: Any) -> None:
        self.events.append({"kind": kind, **payload})

    def _account_dict(self) -> dict[str, Any]:
        snap = self.ledger.snapshot()
        data = snap.to_dict()
        data["day_start_equity"] = self.ledger.day_start_equity
        return data

    def _maybe_halt(self) -> None:
        limit = self.risk.limits.daily_loss_amount(self.ledger.day_start_equity)
        if self.ledger.daily_pnl() <= -limit:
            self.ledger.halted = True
            self._note("halt", reason="daily_loss_limit", daily_pnl=self.ledger.daily_pnl())

    def _fill_pending(self, candle: Candle, index: int) -> None:
        order = self.pending
        if order is None:
            return
        price = buy_fill_price(candle.open, spread_bps=self.spread_bps, slip_bps=self.slip_bps)
        cost = round(order.quantity * price, 2)
        if cost > self.ledger.cash + 0.001:
            order.status = OrderStatus.REJECTED.value
            order.reason = "Insufficient cash at fill."
            self._note("reject", order=order.to_dict())
            self.pending = None
            return
        fill = PaperFill(
            fill_id=self.ledger.next_fill_id(),
            order_id=order.order_id,
            symbol=order.symbol,
            side="BUY",
            quantity=order.quantity,
            price=price,
            timestamp=candle.timestamp,
            reason=FillReason.ENTRY.value,
            spread=self.spread_bps,
            slippage=self.slip_bps,
        )
        self.ledger.apply_buy(order, fill)
        self.pending = None
        self._note("fill", fill=fill.to_dict(), bar=index)

    def _manage_position(self, candle: Candle, index: int) -> None:
        for symbol, pos in list(self.ledger.positions.items()):
            if not pos.open:
                continue
            hit = resolve_intrabar(
                stop_loss=pos.stop_loss,
                take_profit=pos.take_profit,
                candle=candle,
            )
            if hit is None:
                self.ledger.mark(symbol, candle.close)
                continue
            raw = pos.stop_loss if hit == "stop" else pos.take_profit
            assert raw is not None
            price = sell_fill_price(raw, spread_bps=self.spread_bps, slip_bps=self.slip_bps)
            fill = PaperFill(
                fill_id=self.ledger.next_fill_id(),
                order_id=pos.order_id,
                symbol=symbol,
                side="SELL",
                quantity=pos.quantity,
                price=price,
                timestamp=candle.timestamp,
                reason=FillReason.STOP.value if hit == "stop" else FillReason.TARGET.value,
                spread=self.spread_bps,
                slippage=self.slip_bps,
            )
            closed = self.ledger.close_position(symbol, fill, reason=fill.reason)
            for order in self.ledger.orders:
                if order.order_id == pos.order_id:
                    order.status = OrderStatus.CLOSED.value
            self._note("exit", fill=fill.to_dict(), pnl=closed.realised_pnl, bar=index, why=hit)
        self._maybe_halt()

    def _signal(
        self,
        index: int,
        visible: CandleSeries,
        source: SignalSource,
        kill_switch: bool,
    ) -> None:
        if self.pending is not None:
            return
        candle = visible.candles[index]
        analysis = None
        if len(visible.candles) >= 5:
            analysis = analyse_series(visible)
        action = source.decide(index, visible, analysis)
        if action in {PaperAction.HOLD, "HOLD"}:
            return
        open_pos = self.ledger.open_positions()
        has = any(p.symbol == visible.symbol for p in open_pos)
        assessment = self.risk.review_paper(
            action.value if hasattr(action, "value") else str(action),
            price=candle.close,
            account=self._account_dict(),
            open_positions=len(open_pos),
            trades_today=self.ledger.trades_today,
            daily_pnl=self.ledger.daily_pnl(),
            has_position=has,
            halted=self.ledger.halted,
            kill_switch=kill_switch,
        )
        if action in {PaperAction.CLOSE, PaperAction.SELL} and assessment.approved and has:
            pos = self.ledger.positions[visible.symbol]
            price = sell_fill_price(candle.close, spread_bps=self.spread_bps, slip_bps=self.slip_bps)
            order = PaperOrder(
                order_id=self.ledger.next_order_id(),
                symbol=visible.symbol,
                side="SELL",
                quantity=pos.quantity,
                requested_price=candle.close,
                stop_loss=None,
                take_profit=None,
                timestamp=candle.timestamp,
                status=OrderStatus.FILLED.value,
                reason=assessment.reason,
                source=source.name,
                filled_price=price,
                filled_at=candle.timestamp,
                signal_bar=index,
            )
            fill = PaperFill(
                fill_id=self.ledger.next_fill_id(),
                order_id=order.order_id,
                symbol=visible.symbol,
                side="SELL",
                quantity=pos.quantity,
                price=price,
                timestamp=candle.timestamp,
                reason=FillReason.CLOSE.value,
                spread=self.spread_bps,
                slippage=self.slip_bps,
            )
            self.ledger.orders.append(order)
            self.ledger.close_position(visible.symbol, fill, reason="CLOSE")
            self._note("close", fill=fill.to_dict(), bar=index)
            return
        qty = assessment.proposed_qty
        if assessment.approved:
            est = buy_fill_price(candle.close, spread_bps=self.spread_bps, slip_bps=self.slip_bps)
            pad = 1.02 if (self.spread_bps or self.slip_bps) else 1.0
            if est > 0:
                affordable = int((self.ledger.cash / (est * pad)) * 10000) / 10000.0
                if affordable < qty:
                    qty = affordable
        if not assessment.approved:
            assessment_ok = False
            reject_reason = assessment.reason
        elif qty <= 0:
            assessment_ok = False
            reject_reason = "Insufficient cash after spread buffer."
        else:
            assessment_ok = True
            reject_reason = assessment.reason
        if not assessment_ok:
            order = PaperOrder(
                order_id=self.ledger.next_order_id(),
                symbol=visible.symbol,
                side=str(action.value if hasattr(action, "value") else action),
                quantity=0,
                requested_price=candle.close,
                stop_loss=None,
                take_profit=None,
                timestamp=candle.timestamp,
                status=OrderStatus.REJECTED.value,
                reason=reject_reason,
                source=source.name,
                signal_bar=index,
            )
            self.ledger.orders.append(order)
            self._note("reject", order=order.to_dict(), bar=index)
            return
        order = PaperOrder(
            order_id=self.ledger.next_order_id(),
            symbol=visible.symbol,
            side="BUY",
            quantity=qty,
            requested_price=candle.close,
            stop_loss=assessment.stop_price,
            take_profit=assessment.take_profit_price,
            timestamp=candle.timestamp,
            status=OrderStatus.PENDING.value,
            reason=assessment.reason,
            source=source.name,
            signal_bar=index,
        )
        self.ledger.orders.append(order)
        self.pending = order
        self._note("pending", order=order.to_dict(), bar=index, assessment=assessment.to_dict())

    def run(
        self,
        series: CandleSeries,
        *,
        source: Optional[SignalSource] = None,
        kill_switch: bool = False,
        stop_check: Optional[Callable[[int], bool]] = None,
        on_bar: Optional[Callable[[int, CandleSeries], None]] = None,
        finalize: bool = True,
    ) -> dict[str, Any]:
        source = source or FixtureHoldSource()
        self.kill_switch = kill_switch
        self.stopped_at = None
        last_visible = self._process_range(
            series,
            0,
            len(series.candles),
            source=source,
            stop_check=stop_check,
            on_bar=on_bar,
        )
        if finalize:
            self._finalize(last_visible)
        return self._report(series, source, kill_switch)

    def extend(
        self,
        series: CandleSeries,
        *,
        start_index: int,
        source: Optional[SignalSource] = None,
        kill_switch: bool = False,
        stop_check: Optional[Callable[[int], bool]] = None,
        on_bar: Optional[Callable[[int, CandleSeries], None]] = None,
        finalize: bool = False,
    ) -> dict[str, Any]:
        """Process only new bars. Does not replay earlier candles. No look-ahead."""
        source = source or FixtureHoldSource()
        last_visible = self._process_range(
            series,
            start_index,
            len(series.candles),
            source=source,
            stop_check=stop_check,
            on_bar=on_bar,
        )
        if finalize:
            self._finalize(last_visible)
        return self._report(series, source, kill_switch)

    def _process_range(
        self,
        series: CandleSeries,
        start_index: int,
        end_index: int,
        *,
        source: SignalSource,
        stop_check: Optional[Callable[[int], bool]],
        on_bar: Optional[Callable[[int, CandleSeries], None]],
    ) -> Optional[CandleSeries]:
        candles = series.candles
        last_visible: Optional[CandleSeries] = None
        start_index = max(0, int(start_index))
        end_index = min(len(candles), int(end_index))
        for i in range(start_index, end_index):
            candle = candles[i]
            visible = replace(series, candles=candles[: i + 1])
            last_visible = visible
            if len(visible.candles) != i + 1:
                self.seen_future = True
            if i + 1 < len(candles) and visible.candles[-1].timestamp == candles[i + 1].timestamp:
                self.seen_future = True
            if on_bar is not None:
                on_bar(i, visible)
            stopped = bool(stop_check and stop_check(i))
            if stopped and self.stopped_at is None:
                self.stopped_at = i
            self.ledger.roll_day(candle.timestamp)
            if stopped:
                if self.pending is not None:
                    self.pending.status = OrderStatus.CANCELLED.value
                    self.pending.reason = "Session stopped. New paper trades blocked."
                    self._note("stop_cancel", order=self.pending.to_dict(), bar=i)
                    self.pending = None
            elif self.pending is not None:
                self._fill_pending(candle, i)
            self._manage_position(candle, i)
            self.ledger.mark(series.symbol, candle.close)
            dd = self.ledger.drawdown()
            if dd > self.max_drawdown:
                self.max_drawdown = dd
            self.equity_curve.append(
                {
                    "bar": i,
                    "timestamp": candle.timestamp,
                    "equity": self.ledger.equity(),
                }
            )
            if self.kill_switch or stopped:
                continue
            self._signal(i, visible, source, kill_switch=self.kill_switch)
        return last_visible

    def _finalize(self, last_visible: Optional[CandleSeries]) -> None:
        if self.pending is not None:
            self.pending.status = OrderStatus.CANCELLED.value
            self.pending.reason = "No next bar to fill. Fill not guaranteed."
            self._note("cancel", order=self.pending.to_dict())
            self.pending = None
        if self.flatten_at_end and self.ledger.open_positions() and last_visible:
            last = last_visible.candles[-1]
            for pos in list(self.ledger.open_positions()):
                price = sell_fill_price(last.close, spread_bps=self.spread_bps, slip_bps=self.slip_bps)
                fill = PaperFill(
                    fill_id=self.ledger.next_fill_id(),
                    order_id=pos.order_id,
                    symbol=pos.symbol,
                    side="SELL",
                    quantity=pos.quantity,
                    price=price,
                    timestamp=last.timestamp,
                    reason=FillReason.DAY_END.value,
                    spread=self.spread_bps,
                    slippage=self.slip_bps,
                )
                closed = self.ledger.close_position(pos.symbol, fill, reason="DAY_END")
                self._note("flatten", fill=fill.to_dict(), pnl=closed.realised_pnl)

    def _report(self, series: CandleSeries, source: SignalSource, kill_switch: bool) -> dict[str, Any]:
        candles = series.candles
        snap = self.ledger.snapshot(as_of=candles[-1].timestamp if candles else None)
        performance = summarise(
            closed=self.ledger.closed_positions,
            starting_cash=self.ledger.starting_cash,
            equity=snap.account_equity,
            max_drawdown=self.max_drawdown,
        )
        return {
            "ok": True,
            "mode": "simulate",
            "live": False,
            "look_ahead": self.seen_future,
            "assumptions": ASSUMPTIONS,
            "signal_source": source.name,
            "symbol": series.symbol,
            "bars": len(candles),
            "account": snap.to_dict(),
            "orders": [o.to_dict() for o in self.ledger.orders],
            "fills": [f.to_dict() for f in self.ledger.fills],
            "positions": [p.to_dict() for p in self.ledger.open_positions()],
            "closed_positions": [p.to_dict() for p in self.ledger.closed_positions],
            "performance": performance,
            "equity_curve": self.equity_curve,
            "events": self.events,
            "broker_submit_calls": 0,
            "kill_switch": kill_switch,
            "stopped_at": self.stopped_at,
        }


