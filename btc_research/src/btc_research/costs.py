"""Execution cost and exposure assumptions.

All research runs share one cost model so strategies are comparable.
Fees are charged on traded notional. Slippage and half-spread worsen the fill
versus the bar open. There is no leverage and no borrowed cash.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class CostModel:
    """Per-side trading costs in basis points of traded notional.

    Primary research assumption (Binance spot VIP 0, checked 2026-10):
    maker = taker = 10 bps. Paying fees in BNB is a 25% discount (7.5 bps)
    and is the sensitivity case, not the base case. BTCUSDT spread for a
    small spot order is well under 1 bp; 0.5 bp half-spread plus 1 bp
    slippage is a conservative small-order allowance, not a market-impact
    model for large size.
    """

    fee_bps: float = 10.0
    slippage_bps: float = 1.0
    half_spread_bps: float = 0.5

    def __post_init__(self) -> None:
        for name in ("fee_bps", "slippage_bps", "half_spread_bps"):
            value = getattr(self, name)
            if value < 0:
                raise ValueError(f"{name} cannot be negative")

    @property
    def slippage_rate(self) -> float:
        return (self.slippage_bps + self.half_spread_bps) / 10_000.0

    @property
    def fee_rate(self) -> float:
        return self.fee_bps / 10_000.0

    @property
    def buy_factor(self) -> float:
        """Cash spent per 1 unit of quote notional at the raw open."""
        return (1.0 + self.slippage_rate) * (1.0 + self.fee_rate)

    @property
    def sell_factor(self) -> float:
        """Cash received per 1 unit of quote notional at the raw open."""
        return (1.0 - self.slippage_rate) * (1.0 - self.fee_rate)

    def round_trip_drag(self, gross_return: float) -> float:
        """Net simple return of buying at this open and selling at a later price.

        gross_return is close/open - 1 (or next/open - 1) before costs.
        """
        gross_multiple = 1.0 + gross_return
        net_multiple = gross_multiple * self.sell_factor / self.buy_factor
        return net_multiple - 1.0

    def to_dict(self) -> dict[str, float]:
        data = asdict(self)
        data["buy_factor"] = self.buy_factor
        data["sell_factor"] = self.sell_factor
        data["approx_round_trip_return_drag"] = self.round_trip_drag(0.0)
        return data


@dataclass(frozen=True)
class ExecutionConfig:
    """Shared simulation settings. max_exposure is a hard cap in [0, 1]."""

    cost: CostModel = CostModel()
    initial_equity: float = 10_000.0
    max_exposure: float = 1.0
    min_trade_fraction: float = 1e-8

    def __post_init__(self) -> None:
        if self.initial_equity <= 0:
            raise ValueError("initial_equity must be positive")
        if not 0.0 <= self.max_exposure <= 1.0:
            raise ValueError("max_exposure must be in [0, 1]; leverage is not allowed")
        if self.min_trade_fraction < 0:
            raise ValueError("min_trade_fraction cannot be negative")


def sensitivity_cost() -> CostModel:
    """BNB-discount fee with a thinner slippage allowance. Not the base case."""
    return CostModel(fee_bps=7.5, slippage_bps=0.5, half_spread_bps=0.5)
