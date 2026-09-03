"""Edge calculation.

    net_edge = model_probability - market_probability - fee_cost - spread_cost

All four terms are in probability points, which is what makes them
subtractable: a contract priced at 0.71 that pays 1 has its cost expressed as
the fraction of a contract's payout consumed, so a 1.75-cent fee on a $1
contract is 0.0175 of edge.

Both directions matter. If the market is priced *above* our probability, the
edge is on the NO side; this build only trades YES, so a negative edge is
simply not an opportunity rather than an inverted one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass(frozen=True)
class EdgeResult:
    model_probability: float
    market_probability: float
    gross_edge: float
    fee_cost: float
    spread_cost: float
    net_edge: float
    price: float
    side: str = "YES"
    inputs: dict[str, Any] = field(default_factory=dict)

    @property
    def is_opportunity(self) -> bool:
        return self.net_edge > 0

    def expected_value_per_contract(self) -> float:
        """Expected profit per contract in quote currency, after costs.

        Buying YES at ``price`` pays 1 with probability ``model_probability``:

            EV = p * (1 - price) - (1 - p) * price - fees
               = p - price - fees
        """
        return round(
            self.model_probability - self.price - self.fee_cost - self.spread_cost, 8
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_probability": self.model_probability,
            "market_probability": self.market_probability,
            "gross_edge": self.gross_edge,
            "fee_cost": self.fee_cost,
            "spread_cost": self.spread_cost,
            "net_edge": self.net_edge,
            "price": self.price,
            "side": self.side,
            "expected_value_per_contract": self.expected_value_per_contract(),
            "is_opportunity": self.is_opportunity,
            "inputs": self.inputs,
        }


def compute_edge(
    *,
    model_probability: float,
    ask_price: float,
    bid_price: Optional[float] = None,
    fee_model: Any = None,
    maker: bool = False,
) -> EdgeResult:
    """Net edge for buying YES at ``ask_price``.

    The market's implied probability is the **ask**, not the mid: the ask is
    what you actually pay. Using the mid would credit half the spread as edge
    that does not exist. The remaining half-spread is carried separately as
    ``spread_cost`` so the round trip is not understated either.
    """
    p = float(model_probability)
    ask = float(ask_price)
    if not 0.0 < ask < 1.0:
        raise ValueError(f"Ask price {ask} must be strictly inside (0, 1).")

    fee_cost = 0.0
    if fee_model is not None:
        if hasattr(fee_model, "fee_per_contract"):
            fee_cost = float(fee_model.fee_per_contract(price=ask, maker=maker))
        else:
            fee_cost = float(fee_model.trade_fee(contracts=1, price=ask, maker=maker))

    spread_cost = 0.0
    if bid_price is not None:
        spread = max(0.0, ask - float(bid_price))
        # Half the spread is the cost of getting back out at the mid.
        spread_cost = round(spread / 2.0, 8)

    gross = round(p - ask, 8)
    net = round(gross - fee_cost - spread_cost, 8)
    return EdgeResult(
        model_probability=round(p, 8),
        market_probability=round(ask, 8),
        gross_edge=gross,
        fee_cost=round(fee_cost, 8),
        spread_cost=spread_cost,
        net_edge=net,
        price=round(ask, 8),
        inputs={
            "ask_price": ask,
            "bid_price": bid_price,
            "maker": maker,
            "fee_model": fee_model.to_dict() if hasattr(fee_model, "to_dict") else None,
        },
    )
