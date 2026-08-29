"""Risk management engine.

Every AI decision must pass through here before any execution is considered.
Broker orders remain disabled (allow_orders=False).
Paper simulation uses review_paper() which can size and approve INTERNAL
paper orders only. That path never calls a broker.
The AI cannot change limits.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from ai_trader.risk.limits import RiskLimits
from ai_trader.types import Action, Decision, RiskVerdict

FOUNDATION_REJECT_REASON = (
    "Foundation mode: the risk engine rejects all orders. "
    "Execution is not implemented."
)


def _floor_qty(value: float) -> float:
    if value <= 0:
        return 0.0
    return int(value * 10000) / 10000.0


@dataclass(frozen=True)
class RiskAssessment:
    approved: bool
    reason: str
    action: str
    max_risk: float
    proposed_qty: float
    proposed_notional: float
    stop_distance: float
    take_profit_distance: float
    stop_price: Optional[float]
    take_profit_price: Optional[float]
    max_loss_at_stop: float
    risk_reward: float
    limits: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "approved": self.approved,
            "reason": self.reason,
            "action": self.action,
            "max_risk": self.max_risk,
            "proposed_qty": self.proposed_qty,
            "proposed_notional": self.proposed_notional,
            "stop_distance": self.stop_distance,
            "take_profit_distance": self.take_profit_distance,
            "stop_price": self.stop_price,
            "take_profit_price": self.take_profit_price,
            "max_loss_at_stop": self.max_loss_at_stop,
            "risk_reward": self.risk_reward,
            "limits": self.limits,
        }


class RiskEngine:
    name = "risk"

    def __init__(
        self,
        *,
        max_position_pct: float = 0.02,
        allow_orders: bool = False,
        limits: Optional[RiskLimits] = None,
    ) -> None:
        self.max_position_pct = max_position_pct
        self.allow_orders = allow_orders  # stays False in this build
        self.limits = limits or RiskLimits()

    def size_long(self, *, price: float, equity: float, cash: float) -> RiskAssessment:
        limits = self.limits
        if price <= 0 or equity <= 0:
            return self._reject("Invalid price or equity.", "BUY")
        if limits.leverage != 0:
            return self._reject("Leverage is forbidden.", "BUY")
        budget = limits.risk_budget(equity)
        stop_distance = round(price * limits.default_stop_pct, 4)
        if stop_distance <= 0:
            return self._reject("Stop distance is zero.", "BUY")
        qty = _floor_qty(budget / stop_distance)
        notional = round(qty * price, 2)
        cash_cap = round(cash * limits.max_notional_pct, 2)
        if notional > cash_cap and price > 0:
            qty = _floor_qty(cash_cap / price)
            notional = round(qty * price, 2)
        max_loss = round(qty * stop_distance, 2)
        if max_loss - budget > 0.01:
            qty = _floor_qty(budget / stop_distance)
            notional = round(qty * price, 2)
            max_loss = round(qty * stop_distance, 2)
        if qty <= 0 or notional <= 0:
            return self._reject("Position size is zero.", "BUY")
        stop_price = round(price - stop_distance, 4)
        tp_distance = round(stop_distance * limits.take_profit_rr, 4)
        take_profit = round(price + tp_distance, 4)
        return RiskAssessment(
            approved=True,
            reason="Sized within paper risk limits.",
            action="BUY",
            max_risk=budget,
            proposed_qty=qty,
            proposed_notional=notional,
            stop_distance=stop_distance,
            take_profit_distance=tp_distance,
            stop_price=stop_price,
            take_profit_price=take_profit,
            max_loss_at_stop=max_loss,
            risk_reward=limits.take_profit_rr,
            limits=self._limits_dict(),
        )

    def review(
        self,
        decision: Decision,
        *,
        account: Optional[dict[str, Any]] = None,
        positions: Optional[list[dict[str, Any]]] = None,
    ) -> RiskVerdict:
        if decision.action == Action.HOLD:
            return RiskVerdict(
                approved=False,
                reason="HOLD does not produce an order.",
                max_qty=0,
                decision=decision,
            )
        if not self.allow_orders:
            return RiskVerdict(
                approved=False,
                reason=FOUNDATION_REJECT_REASON,
                max_qty=0,
                decision=decision,
            )
        return RiskVerdict(
            approved=False,
            reason="Risk engine has no live policy yet; default is reject.",
            max_qty=0,
            decision=decision,
        )

    def review_paper(
        self,
        action: str,
        *,
        price: float,
        account: dict[str, Any],
        open_positions: int,
        trades_today: int,
        daily_pnl: float,
        has_position: bool,
        halted: bool,
        kill_switch: bool,
    ) -> RiskAssessment:
        """Approve or reject an INTERNAL simulated paper order or an Alpaca
        PAPER order. Never a live broker order. AI cannot change limits.
        """
        act = action.upper()
        if kill_switch:
            return self._reject("Kill switch engaged.", act)
        if halted:
            return self._reject("Account halted after daily loss limit.", act)
        if act == "HOLD":
            return self._reject("HOLD does not produce an order.", act)
        equity = float(account.get("account_equity") or account.get("equity") or 0)
        cash = float(account.get("cash") or 0)
        day_start = float(account.get("day_start_equity") or account.get("starting_cash") or equity)
        if daily_pnl <= -self.limits.daily_loss_amount(day_start) and act in {"BUY", "SELL"}:
            return self._reject("Daily loss limit reached.", act)
        if trades_today >= self.limits.max_trades_per_day and act in {"BUY", "SELL"}:
            return self._reject("Maximum trades per day reached.", act)
        if act == "CLOSE":
            if not has_position:
                return self._reject("No position to close.", act)
            return RiskAssessment(
                approved=True,
                reason="Close existing paper position.",
                action="CLOSE",
                max_risk=0,
                proposed_qty=0,
                proposed_notional=0,
                stop_distance=0,
                take_profit_distance=0,
                stop_price=None,
                take_profit_price=None,
                max_loss_at_stop=0,
                risk_reward=self.limits.take_profit_rr,
                limits=self._limits_dict(),
            )
        if act == "SELL":
            if not has_position:
                return self._reject("Shorts are disabled. SELL rejected.", act)
            return RiskAssessment(
                approved=True,
                reason="Reduce/close long paper position.",
                action="SELL",
                max_risk=0,
                proposed_qty=0,
                proposed_notional=0,
                stop_distance=0,
                take_profit_distance=0,
                stop_price=None,
                take_profit_price=None,
                max_loss_at_stop=0,
                risk_reward=self.limits.take_profit_rr,
                limits=self._limits_dict(),
            )
        if act != "BUY":
            return self._reject(f"Unsupported action {act}.", act)
        if open_positions >= self.limits.max_open_positions:
            return self._reject("Maximum open positions reached.", act)
        if has_position:
            return self._reject("Already in this symbol. Adds are disabled.", act)
        return self.size_long(price=price, equity=equity, cash=cash)

    def health(self) -> dict:
        return {
            "name": self.name,
            "ready": True,
            "allow_orders": self.allow_orders,
            "max_position_pct": self.max_position_pct,
            "limits": self._limits_dict(),
            "notes": "Hard gate. AI cannot bypass this module. Paper sizing 2%/£2. Broker orders still disabled.",
        }

    def _limits_dict(self) -> dict[str, Any]:
        limits = self.limits
        return {
            "starting_cash": limits.starting_cash,
            "max_risk_pct": limits.max_risk_pct,
            "max_risk_amount": limits.max_risk_amount,
            "max_open_positions": limits.max_open_positions,
            "max_daily_loss_pct": limits.max_daily_loss_pct,
            "max_trades_per_day": limits.max_trades_per_day,
            "leverage": limits.leverage,
            "default_stop_pct": limits.default_stop_pct,
            "take_profit_rr": limits.take_profit_rr,
        }

    def _reject(self, reason: str, action: str) -> RiskAssessment:
        return RiskAssessment(
            approved=False,
            reason=reason,
            action=action,
            max_risk=self.limits.max_risk_amount,
            proposed_qty=0,
            proposed_notional=0,
            stop_distance=0,
            take_profit_distance=0,
            stop_price=None,
            take_profit_price=None,
            max_loss_at_stop=0,
            risk_reward=self.limits.take_profit_rr,
            limits=self._limits_dict(),
        )
