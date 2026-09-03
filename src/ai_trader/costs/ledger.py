"""Cost ledger and runway.

Tracks what it costs to keep the agent alive: model calls, hosting, data and
trading fees. Produces net-of-cost P&L and a runway estimate.

The one rule that matters
-------------------------
None of this may influence a trading decision. An agent that widens its risk
because the hosting bill is due is exactly the failure mode this project is
built to avoid, so the cost ledger is deliberately a *reporting* component: the
risk engine and the policy guardian never receive a cost figure, and a test
asserts the guardian's verdict is unchanged by arbitrary accrued cost.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from enum import Enum
from typing import Any, Optional

from ai_trader.clock import Clock, default_clock
from ai_trader.money import money_float


class CostCategory(str, Enum):
    LLM = "llm"
    HOSTING = "hosting"
    DATA = "data"
    FEES = "fees"
    OTHER = "other"


@dataclass(frozen=True)
class TokenPrice:
    """Price per million tokens, in USD, as published by the provider.

    These are configuration, not measurements. If the provider changes its
    pricing this must be updated; the ledger records the rate it used so an old
    cost is never silently restated.
    """

    model: str
    input_per_million_usd: float
    output_per_million_usd: float

    def cost_usd(self, *, input_tokens: int, output_tokens: int) -> float:
        return (
            input_tokens / 1_000_000.0 * self.input_per_million_usd
            + output_tokens / 1_000_000.0 * self.output_per_million_usd
        )


#: Configured xAI pricing. Verify against the provider's current published
#: rates before relying on the absolute numbers; the mechanism does not depend
#: on them being exact, but the runway figure does.
XAI_PRICES: dict[str, TokenPrice] = {
    "grok-4.6": TokenPrice("grok-4.6", input_per_million_usd=3.00, output_per_million_usd=15.00),
}
DEFAULT_TOKEN_PRICE = TokenPrice("unknown", input_per_million_usd=3.00, output_per_million_usd=15.00)


class CostLedger:
    """Records operating cost and derives runway. Never consulted by risk."""

    def __init__(
        self,
        store: Any,
        *,
        clock: Optional[Clock] = None,
        base_currency: str = "GBP",
        hosting_per_day: float = 0.0,
        fx_usd_to_base: float = 1.0,
    ) -> None:
        self.store = store
        self.clock = clock or default_clock()
        self.base_currency = base_currency
        self.hosting_per_day = float(hosting_per_day)
        self.fx_usd_to_base = float(fx_usd_to_base)

    # -- recording --------------------------------------------------------
    def record_llm_call(
        self,
        *,
        model: str,
        input_tokens: int,
        output_tokens: int,
        reference: Optional[str] = None,
    ) -> float:
        price = XAI_PRICES.get(model, DEFAULT_TOKEN_PRICE)
        usd = price.cost_usd(input_tokens=input_tokens, output_tokens=output_tokens)
        amount = usd * self.fx_usd_to_base
        self.store.record_cost(
            category=CostCategory.LLM.value,
            description=(
                f"{model}: {input_tokens} in / {output_tokens} out "
                f"@ ${price.input_per_million_usd}/${price.output_per_million_usd} per Mtok"
            ),
            amount_base=amount,
            currency=self.base_currency,
            units=float(input_tokens + output_tokens),
            unit_name="tokens",
            reference=reference,
        )
        return round(amount, 6)

    def record_fee(self, *, amount_base: float, description: str, reference: Optional[str] = None) -> float:
        self.store.record_cost(
            category=CostCategory.FEES.value,
            description=description,
            amount_base=float(amount_base),
            currency=self.base_currency,
            reference=reference,
        )
        return round(float(amount_base), 6)

    def record_hosting(self, *, amount_base: float, description: str = "hosting") -> float:
        self.store.record_cost(
            category=CostCategory.HOSTING.value,
            description=description,
            amount_base=float(amount_base),
            currency=self.base_currency,
        )
        return round(float(amount_base), 6)

    def record_data(self, *, amount_base: float, description: str) -> float:
        self.store.record_cost(
            category=CostCategory.DATA.value,
            description=description,
            amount_base=float(amount_base),
            currency=self.base_currency,
        )
        return round(float(amount_base), 6)

    # -- reporting --------------------------------------------------------
    def total(self) -> float:
        return money_float(self.store.total_costs())

    def by_category(self) -> dict[str, float]:
        return self.store.costs_by_category()

    def daily_burn(self, *, lookback_days: int = 7) -> float:
        """Average cost per day over the lookback window.

        Falls back to the configured hosting rate when there is no history yet,
        so a brand-new agent still reports a finite runway rather than infinity.
        """
        since = self.clock.now() - timedelta(days=lookback_days)
        spent = self.store.costs_since(since.isoformat())
        observed = spent / max(1, lookback_days)
        return money_float(max(observed, self.hosting_per_day))

    def runway_days(self, *, equity: float, terminal_threshold: float, lookback_days: int = 7) -> Optional[float]:
        """Days of operating cost the spendable capital covers.

        Spendable capital is equity *above the terminal threshold*: money below
        it cannot be spent, because reaching it ends the agent. ``None`` means
        no measurable burn rate.
        """
        burn = self.daily_burn(lookback_days=lookback_days)
        if burn <= 0:
            return None
        spendable = max(0.0, float(equity) - float(terminal_threshold))
        return round(spendable / burn, 2)

    def summary(
        self,
        *,
        equity: float,
        starting_equity: float,
        terminal_threshold: float,
        realised_pnl: float = 0.0,
        unrealised_pnl: float = 0.0,
        lookback_days: int = 7,
    ) -> dict[str, Any]:
        total = self.total()
        gross = money_float(realised_pnl + unrealised_pnl)
        return {
            "base_currency": self.base_currency,
            "starting_equity": money_float(starting_equity),
            "equity": money_float(equity),
            "gross_trading_pnl": gross,
            "realised_pnl": money_float(realised_pnl),
            "unrealised_pnl": money_float(unrealised_pnl),
            "operating_costs": total,
            "costs_by_category": self.by_category(),
            "net_pnl": money_float(gross - total),
            "self_sustaining": gross >= total and total > 0,
            "daily_burn": self.daily_burn(lookback_days=lookback_days),
            "runway_days": self.runway_days(
                equity=equity, terminal_threshold=terminal_threshold, lookback_days=lookback_days
            ),
            "spendable_capital": money_float(max(0.0, equity - terminal_threshold)),
        }
