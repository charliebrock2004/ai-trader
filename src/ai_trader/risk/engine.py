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

from ai_trader.instruments import InstrumentSpec, instrument_for
from ai_trader.money import BASE_CURRENCY, money_float
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
    """Verdict on one proposed paper order.

    Prices (``stop_price``, ``take_profit_price``, ``*_distance``) are in the
    instrument's quote currency. Money figures (``max_risk``,
    ``proposed_notional``, ``max_loss_at_stop``, ``worst_case_loss``) are in the
    account's base currency, so they can be compared against equity directly.
    """

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
    worst_case_loss: float = 0.0
    base_currency: str = BASE_CURRENCY
    quote_currency: str = BASE_CURRENCY
    fx_rate: float = 1.0
    risk_multiplier: float = 1.0
    binding_constraint: str = ""

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
            "worst_case_loss": self.worst_case_loss,
            "risk_reward": self.risk_reward,
            "limits": self.limits,
            "base_currency": self.base_currency,
            "quote_currency": self.quote_currency,
            "fx_rate": self.fx_rate,
            "risk_multiplier": self.risk_multiplier,
            "binding_constraint": self.binding_constraint,
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

    def size_long(
        self,
        *,
        price: float,
        equity: float,
        cash: float,
        instrument: Optional[InstrumentSpec] = None,
        fx_rate: float = 1.0,
        risk_multiplier: float = 1.0,
        base_currency: str = BASE_CURRENCY,
        stop_pct_hint: Optional[float] = None,
    ) -> RiskAssessment:
        """Size a long entry.

        ``stop_pct_hint`` is a stop distance the strategy derived from current
        volatility. It is advisory: the engine clamps it into the limits' band
        and remains the only thing that sets the stop. A hint can therefore make
        a position smaller or larger within the existing risk budget, but it can
        never raise the amount at risk — that is still ``max_risk_amount``.

        ``price`` is in the instrument's quote currency. ``equity`` and ``cash``
        are in the account's base currency. ``fx_rate`` is base units per quote
        unit — 1.0 when the instrument is quoted in the base currency.

        Three independent caps apply and the smallest wins:

        1. **Risk budget** — worst-case loss (stop distance widened by the gap
           buffer) must stay inside ``max_risk_amount``.
        2. **Concentration** — notional must stay inside
           ``max_position_notional_pct`` of equity.
        3. **Cash** — notional must stay inside available cash. No leverage.

        ``risk_multiplier`` (<= 1.0) is how the survival policy tightens
        sizing. Values above 1.0 are clamped: nothing may size the account up.
        """
        limits = self.limits
        spec = instrument or instrument_for("")
        if price <= 0 or equity <= 0:
            return self._reject("Invalid price or equity.", "BUY")
        if fx_rate <= 0:
            return self._reject("Invalid FX rate. Refusing to size a foreign position.", "BUY")
        if limits.leverage != 0:
            return self._reject("Leverage is forbidden.", "BUY")

        # Survival can only ever tighten. Clamp defensively so a bug upstream
        # cannot enlarge a position.
        multiplier = min(1.0, max(0.0, float(risk_multiplier)))
        if multiplier <= 0:
            return self._reject("Survival policy allows no new risk.", "BUY")

        budget = money_float(limits.risk_budget(equity) * multiplier)
        if budget <= 0:
            return self._reject("Risk budget is zero.", "BUY")

        stop_pct, stop_source = limits.clamp_stop_pct(stop_pct_hint)
        stop_distance = round(price * stop_pct, 8)
        if stop_distance <= 0:
            return self._reject("Stop distance is zero.", "BUY")

        # Worst case is the stop distance widened by the gap buffer, in base
        # currency per unit held.
        worst_per_unit_base = limits.worst_case_stop_distance(stop_distance) * fx_rate
        if worst_per_unit_base <= 0:
            return self._reject("Worst-case loss per unit is zero.", "BUY")

        price_base = price * fx_rate
        qty_by_risk = budget / worst_per_unit_base
        qty_by_concentration = (
            equity * limits.max_position_notional_pct * multiplier
        ) / price_base
        qty_by_cash = (cash * limits.max_notional_pct) / price_base

        caps = {
            "risk_budget": qty_by_risk,
            "concentration": qty_by_concentration,
            "cash": qty_by_cash,
        }
        binding = min(caps, key=lambda key: caps[key])
        qty = spec.floor_qty(min(caps.values()))

        if qty <= 0 or qty < spec.min_qty:
            return self._reject(
                "Position size rounds to zero at this price and risk budget.", "BUY"
            )

        notional = money_float(qty * price_base)
        max_loss = money_float(qty * stop_distance * fx_rate)
        worst_case = money_float(qty * worst_per_unit_base)
        if notional <= 0:
            return self._reject("Position size is zero.", "BUY")
        # Belt and braces: the floor step must never push us over a cap.
        if worst_case - budget > 0.01:
            return self._reject("Worst-case loss exceeds the risk budget.", "BUY")
        if notional - money_float(cash * limits.max_notional_pct) > 0.01:
            return self._reject("Position notional exceeds available cash.", "BUY")

        stop_price = round(price - stop_distance, 8)
        tp_distance = round(stop_distance * limits.take_profit_rr, 8)
        take_profit = round(price + tp_distance, 8)
        return RiskAssessment(
            approved=True,
            reason=(
                f"Sized within paper risk limits (bound by {binding}, "
                f"stop {stop_pct:.2%} from {stop_source})."
            ),
            action="BUY",
            max_risk=budget,
            proposed_qty=qty,
            proposed_notional=notional,
            stop_distance=stop_distance,
            take_profit_distance=tp_distance,
            stop_price=stop_price,
            take_profit_price=take_profit,
            max_loss_at_stop=max_loss,
            worst_case_loss=worst_case,
            risk_reward=limits.take_profit_rr,
            limits=self._limits_dict(),
            base_currency=base_currency,
            quote_currency=spec.quote_currency,
            fx_rate=fx_rate,
            risk_multiplier=multiplier,
            binding_constraint=binding,
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
        instrument: Optional[InstrumentSpec] = None,
        fx_rate: float = 1.0,
        risk_multiplier: float = 1.0,
        terminated: bool = False,
        stop_pct_hint: Optional[float] = None,
    ) -> RiskAssessment:
        """Approve or reject an INTERNAL simulated paper order or an Alpaca
        PAPER order. Never a live broker order. AI cannot change limits.
        """
        act = action.upper()
        if terminated:
            return self._reject("Agent is TERMINATED. No new orders.", act)
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
        return self.size_long(
            price=price,
            equity=equity,
            cash=cash,
            instrument=instrument,
            fx_rate=fx_rate,
            risk_multiplier=risk_multiplier,
            stop_pct_hint=stop_pct_hint,
        )

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
            "max_position_notional_pct": limits.max_position_notional_pct,
            "stop_gap_buffer": limits.stop_gap_buffer,
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
