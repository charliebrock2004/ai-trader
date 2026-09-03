"""Prediction-market risk.

Spot risk sizes against a stop distance. A binary contract has no stop: if it
resolves against you, the premium is gone in full. So sizing here works
backwards from the **whole premium** rather than from a stop, and the position
is only permitted if losing all of it keeps the agent inside its survival
budget.

Every limit is a ceiling. Nothing in this module can increase a size.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Optional

from ai_trader.money import money_float


@dataclass(frozen=True)
class ContractRiskLimits:
    """Ceilings for binary positions, in the account's base currency."""

    #: Fraction of equity that may be at risk on a single position.
    max_premium_pct: float = 0.10
    #: Fraction of equity that may be at risk across all open positions.
    max_total_exposure_pct: float = 0.30
    #: Fraction of equity that may be at risk across one event's contracts.
    #: Contracts resolving on the same release are perfectly correlated.
    max_event_exposure_pct: float = 0.10
    #: Hard ceiling on contracts in one order, before any other cap.
    max_contracts: int = 500
    #: New positions per day.
    max_new_positions_per_day: int = 4
    #: Daily loss halt, as a fraction of the day's opening equity.
    max_daily_loss_pct: float = 0.05
    #: Refuse prices where the payoff is too skewed to size sensibly.
    min_price: float = 0.02
    max_price: float = 0.98

    def daily_loss_amount(self, day_start_equity: float) -> float:
        return round(day_start_equity * self.max_daily_loss_pct, 2)

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_premium_pct": self.max_premium_pct,
            "max_total_exposure_pct": self.max_total_exposure_pct,
            "max_event_exposure_pct": self.max_event_exposure_pct,
            "max_contracts": self.max_contracts,
            "max_new_positions_per_day": self.max_new_positions_per_day,
            "max_daily_loss_pct": self.max_daily_loss_pct,
            "min_price": self.min_price,
            "max_price": self.max_price,
        }


@dataclass(frozen=True)
class ContractSizing:
    approved: bool
    reason: str
    contracts: int
    price: float
    premium_base: float
    fee_base: float
    max_loss_base: float
    max_gain_base: float
    binding_constraint: str
    limits: dict[str, Any] = field(default_factory=dict)
    survival_state: str = "HEALTHY"
    risk_multiplier: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "approved": self.approved,
            "reason": self.reason,
            "contracts": self.contracts,
            "price": self.price,
            "premium_base": self.premium_base,
            "fee_base": self.fee_base,
            "max_loss_base": self.max_loss_base,
            "max_gain_base": self.max_gain_base,
            "binding_constraint": self.binding_constraint,
            "limits": self.limits,
            "survival_state": self.survival_state,
            "risk_multiplier": self.risk_multiplier,
        }


class ContractRiskEngine:
    """Deterministic gate and sizer for binary contracts."""

    name = "contract-risk"

    def __init__(self, limits: Optional[ContractRiskLimits] = None) -> None:
        self.limits = limits or ContractRiskLimits()

    def _reject(self, reason: str, price: float = 0.0) -> ContractSizing:
        return ContractSizing(
            approved=False,
            reason=reason,
            contracts=0,
            price=price,
            premium_base=0.0,
            fee_base=0.0,
            max_loss_base=0.0,
            max_gain_base=0.0,
            binding_constraint="rejected",
            limits=self.limits.to_dict(),
        )

    def size(
        self,
        *,
        price: float,
        equity: float,
        cash: float,
        fee_model: Any,
        fx_rate: float = 1.0,
        available_contracts: Optional[int] = None,
        current_exposure: float = 0.0,
        event_exposure: float = 0.0,
        positions_opened_today: int = 0,
        daily_pnl: float = 0.0,
        day_start_equity: Optional[float] = None,
        risk_multiplier: float = 1.0,
        survival_state: str = "HEALTHY",
        survival_max_premium_pct: Optional[float] = None,
        survival_max_exposure_pct: Optional[float] = None,
        terminated: bool = False,
        halted: bool = False,
    ) -> ContractSizing:
        """Size a YES entry, working backwards from total premium at risk.

        ``risk_multiplier`` and the two ``survival_*`` overrides come from the
        survival policy and may only tighten: each is intersected with the
        static limit, never substituted for it.
        """
        limits = self.limits
        if terminated:
            return self._reject("Agent is TERMINATED. No new positions.", price)
        if halted:
            return self._reject("Account halted after the daily loss limit.", price)
        if equity <= 0:
            return self._reject("Equity is not positive.", price)
        if fx_rate <= 0:
            return self._reject("Invalid FX rate. Refusing to size a foreign contract.", price)
        if not limits.min_price <= price <= limits.max_price:
            return self._reject(
                f"Price {price} is outside the tradeable band "
                f"[{limits.min_price}, {limits.max_price}].",
                price,
            )
        if positions_opened_today >= limits.max_new_positions_per_day:
            return self._reject("Daily new-position budget is spent.", price)

        start_equity = day_start_equity if day_start_equity is not None else equity
        if daily_pnl <= -limits.daily_loss_amount(start_equity):
            return self._reject("Daily loss limit reached.", price)

        multiplier = min(1.0, max(0.0, float(risk_multiplier)))
        if multiplier <= 0:
            return self._reject(f"{survival_state} permits no new risk.", price)

        # Intersect static limits with the survival policy. min() both ways so
        # neither source can ever loosen the other.
        premium_pct = min(
            limits.max_premium_pct,
            survival_max_premium_pct if survival_max_premium_pct is not None else 1.0,
        )
        exposure_pct = min(
            limits.max_total_exposure_pct,
            survival_max_exposure_pct if survival_max_exposure_pct is not None else 1.0,
        )
        event_pct = min(limits.max_event_exposure_pct, premium_pct)

        premium_cap = money_float(equity * premium_pct * multiplier)
        exposure_headroom = money_float(equity * exposure_pct - current_exposure)
        event_headroom = money_float(equity * event_pct - event_exposure)

        if exposure_headroom <= 0:
            return self._reject("Total exposure cap already reached.", price)
        if event_headroom <= 0:
            return self._reject(
                "Exposure to this event is already at its cap. Contracts resolving on "
                "one release are correlated.",
                price,
            )

        # Cost per contract in base currency, including the entry fee.
        fee_per_contract = (
            fee_model.fee_per_contract(price=price)
            if hasattr(fee_model, "fee_per_contract")
            else 0.0
        )
        cost_per_contract = (price + fee_per_contract) * fx_rate
        if cost_per_contract <= 0:
            return self._reject("Cost per contract is not positive.", price)

        caps: dict[str, float] = {
            "premium_cap": premium_cap / cost_per_contract,
            "total_exposure": exposure_headroom / cost_per_contract,
            "event_exposure": event_headroom / cost_per_contract,
            "cash": cash / cost_per_contract,
            "max_contracts": float(limits.max_contracts),
        }
        if available_contracts is not None:
            caps["book_depth"] = float(max(0, available_contracts))

        binding = min(caps, key=lambda key: caps[key])
        contracts = int(math.floor(min(caps.values())))
        if contracts <= 0:
            return self._reject(
                f"Position rounds to zero contracts (bound by {binding}).", price
            )

        fee = fee_model.trade_fee(contracts=contracts, price=price)
        premium_base = money_float(contracts * price * fx_rate)
        fee_base = money_float(fee * fx_rate)
        max_loss = money_float(premium_base + fee_base)
        max_gain = money_float(contracts * (1.0 - price) * fx_rate - fee_base)

        # The rounded per-order fee can nudge the position over a cap that the
        # smooth per-contract estimate cleared. Step down until it fits.
        while contracts > 0 and (
            max_loss > premium_cap + 0.001
            or max_loss > exposure_headroom + 0.001
            or max_loss > event_headroom + 0.001
            or max_loss > cash + 0.001
        ):
            contracts -= 1
            if contracts == 0:
                break
            fee = fee_model.trade_fee(contracts=contracts, price=price)
            premium_base = money_float(contracts * price * fx_rate)
            fee_base = money_float(fee * fx_rate)
            max_loss = money_float(premium_base + fee_base)
            max_gain = money_float(contracts * (1.0 - price) * fx_rate - fee_base)

        if contracts <= 0:
            return self._reject(
                "Position rounds to zero contracts once fees are included.", price
            )

        return ContractSizing(
            approved=True,
            reason=f"Sized within contract risk limits (bound by {binding}).",
            contracts=contracts,
            price=round(float(price), 6),
            premium_base=premium_base,
            fee_base=fee_base,
            max_loss_base=max_loss,
            max_gain_base=max_gain,
            binding_constraint=binding,
            limits=limits.to_dict(),
            survival_state=survival_state,
            risk_multiplier=multiplier,
        )
