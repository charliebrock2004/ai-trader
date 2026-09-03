"""Fee models. Fees are data, not an assumption in a comment.

A binary contract's fee is not a flat percentage — on Kalshi-style venues it is
proportional to ``p(1-p)``, so it is *worst exactly where most trading happens*
(near 50c) and falls away towards both ends. A strategy evaluated without this
in the edge calculation is fiction, so :class:`FeeModel` is a required input to
the edge engine rather than an afterthought.

Fee schedule provenance
-----------------------
The default multiplier below reflects Kalshi's published trading-fee formula:

    fee = roundup_to_cent(multiplier * contracts * price * (1 - price))

rounded up to the next whole cent **per order**, not per contract. The
multiplier is 0.07 for most categories and higher for some (crypto). Verify the
venue's current published schedule before relying on the absolute numbers — the
schedule is configuration here precisely so that updating it is a one-line
change and every recorded cost keeps the rate it was computed with.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

CENT = 0.01


def round_up_to_cent(value: float) -> float:
    if value <= 0:
        return 0.0
    return math.ceil(round(value / CENT, 9)) * CENT


@runtime_checkable
class FeeModel(Protocol):
    name: str

    def trade_fee(self, *, contracts: int, price: float, maker: bool = False) -> float:
        """Fee in quote currency for one order."""

    def settlement_fee(self, *, contracts: int) -> float:
        """Fee charged when a position settles."""

    def to_dict(self) -> dict[str, Any]: ...


@dataclass(frozen=True)
class ZeroFeeModel:
    """No fees. Only for isolating other effects in tests — never a venue."""

    name: str = "zero"

    def trade_fee(self, *, contracts: int, price: float, maker: bool = False) -> float:
        return 0.0

    def settlement_fee(self, *, contracts: int) -> float:
        return 0.0

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "multiplier": 0.0}


@dataclass(frozen=True)
class BinaryTradeFeeModel:
    """``roundup_to_cent(multiplier * C * P * (1-P))`` per order.

    ``maker_multiplier`` is applied instead when the order rests. Set it to 0.0
    for venues that do not charge makers.
    """

    multiplier: float = 0.07
    maker_multiplier: float = 0.0025
    settlement_per_contract: float = 0.0
    name: str = "binary-pxq"
    source: str = "Kalshi published trading-fee formula"

    def __post_init__(self) -> None:
        if self.multiplier < 0 or self.maker_multiplier < 0:
            raise ValueError("Fee multipliers cannot be negative.")

    def trade_fee(self, *, contracts: int, price: float, maker: bool = False) -> float:
        n = int(contracts)
        p = float(price)
        if n <= 0 or p <= 0.0 or p >= 1.0:
            return 0.0
        multiplier = self.maker_multiplier if maker else self.multiplier
        return round(round_up_to_cent(multiplier * n * p * (1.0 - p)), 4)

    def settlement_fee(self, *, contracts: int) -> float:
        if self.settlement_per_contract <= 0 or contracts <= 0:
            return 0.0
        return round(round_up_to_cent(self.settlement_per_contract * int(contracts)), 4)

    def fee_per_contract(self, *, price: float, maker: bool = False) -> float:
        """Unrounded per-contract rate. Used by the edge engine.

        The edge calculation needs a smooth per-contract cost; the rounding is
        a per-order effect and applying it here would misprice small sizes in
        both directions.
        """
        p = float(price)
        if p <= 0.0 or p >= 1.0:
            return 0.0
        multiplier = self.maker_multiplier if maker else self.multiplier
        return round(multiplier * p * (1.0 - p), 8)

    def round_trip_cost_per_contract(self, *, price: float) -> float:
        """Entry fee plus the settlement fee. Exiting early would add another leg."""
        return round(
            self.fee_per_contract(price=price) + self.settlement_per_contract, 8
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "multiplier": self.multiplier,
            "maker_multiplier": self.maker_multiplier,
            "settlement_per_contract": self.settlement_per_contract,
            "source": self.source,
            "formula": "roundup_to_cent(multiplier * contracts * price * (1 - price))",
        }


#: Most categories.
STANDARD_FEES = BinaryTradeFeeModel(multiplier=0.07)
#: Some venues charge more on selected categories (crypto, for example).
PREMIUM_FEES = BinaryTradeFeeModel(multiplier=0.10, name="binary-pxq-premium")


def break_even_edge(fee_model: FeeModel, *, price: float, spread: float) -> float:
    """Minimum probability edge that just covers costs at this price.

    Useful as a sanity check on whether a small account can clear its own
    transaction costs at all: at 50c with the standard multiplier the entry fee
    alone is 1.75 points of probability, before any spread.
    """
    per_contract = (
        fee_model.fee_per_contract(price=price)
        if hasattr(fee_model, "fee_per_contract")
        else fee_model.trade_fee(contracts=1, price=price)
    )
    return round(per_contract + max(0.0, float(spread)) / 2.0, 6)
