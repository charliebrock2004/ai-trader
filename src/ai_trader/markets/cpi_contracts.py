"""Contracts for the CPI event family.

A prediction-market venue publishes its own contracts. Until a venue adapter is
connected, this builds the equivalent ladder locally from the release calendar,
so the pipeline has something concrete to price and the machinery can be
exercised end to end.

What this does **not** do is invent an order book. A contract with no book is
reported as having no book and is refused — the agent holds. That is the
correct behaviour with no venue connected, and it is visible on the System page
rather than looking like an agent that simply chose not to trade.

Connecting a real venue means implementing ``PredictionMarketAdapter`` against
it and passing its ``book_source``; nothing else in the pipeline changes.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional

from ai_trader.events.base import ScheduledRelease
from ai_trader.markets.base import Contract
from ai_trader.markets.fees import STANDARD_FEES, FeeModel

#: Year-on-year CPI strikes, in percentage points. A ladder rather than a single
#: contract, because which strike is mispriced is exactly what we do not know
#: in advance.
DEFAULT_CPI_STRIKES: tuple[float, ...] = (2.0, 2.5, 3.0, 3.5, 4.0)


def cpi_contracts(
    release: ScheduledRelease,
    *,
    strikes: Iterable[float] = DEFAULT_CPI_STRIKES,
    venue: str = "paper",
    fee_model: Optional[FeeModel] = None,
    resolution_time: Optional[str] = None,
) -> list[Contract]:
    """Build the YES ladder for one CPI release.

    Each contract resolves against the year-on-year change, and says so in its
    settlement rules — the wording matters because the resolution rule is what
    the probability estimator applies.
    """
    fees = fee_model or STANDARD_FEES
    period = release.period
    rows: list[Contract] = []
    for strike in strikes:
        label = f"{strike:.1f}".rstrip("0").rstrip(".")
        rows.append(
            Contract(
                ticker=f"CPI-{period}-ABOVE-{label.replace('.', 'P')}",
                question=(
                    f"Will US CPI-U year-on-year for {period} come in above {label}%?"
                ),
                event_key=release.release_key,
                resolution_source=release.source,
                resolution_time=resolution_time or release.scheduled_at,
                settlement_rules=(
                    f"Resolves YES if the {release.source} CPI-U all-items year-on-year "
                    f"change for {period} exceeds {label}%. Resolves NO otherwise. "
                    "The YoY figure is computed from the published index against the "
                    "same month of the prior year."
                ),
                venue=venue,
                quote_currency="USD",
                tick_size=0.01,
                min_order=1,
                max_order=5_000,
                fee_model=fees,
                strike=float(strike),
                comparison="above",
            )
        )
    return rows


def register_cpi_ladder(
    market: Any,
    event_source: Any,
    *,
    limit: int = 6,
    strikes: Iterable[float] = DEFAULT_CPI_STRIKES,
) -> int:
    """Register a ladder for every release on the calendar. Returns the count."""
    registered = 0
    for release in event_source.calendar(limit=limit):
        for contract in cpi_contracts(release, strikes=strikes):
            market.register(contract)
            registered += 1
    return registered
