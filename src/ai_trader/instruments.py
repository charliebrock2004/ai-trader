"""Instrument specifications.

An instrument knows the currency it is quoted in and how finely it can be
traded. The account's base accounting currency is separate (see
``ai_trader.money``); crossing between the two always needs an explicit FX rate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ai_trader.money import BASE_CURRENCY, normalise_currency


@dataclass(frozen=True)
class InstrumentSpec:
    symbol: str
    quote_currency: str
    qty_step: float = 0.0001
    min_qty: float = 0.0001
    kind: str = "spot"

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", (self.symbol or "").strip().upper())
        object.__setattr__(self, "quote_currency", normalise_currency(self.quote_currency))
        if self.qty_step <= 0:
            raise ValueError("qty_step must be positive.")
        if self.min_qty < 0:
            raise ValueError("min_qty must not be negative.")

    def floor_qty(self, quantity: float) -> float:
        """Round a quantity down to a tradeable step. Never rounds up."""
        if quantity <= 0:
            return 0.0
        steps = int(round(float(quantity) / self.qty_step + 1e-9))
        # int(round(...)) can round up on the boundary; walk back if it did.
        while steps * self.qty_step > quantity + 1e-12:
            steps -= 1
        if steps <= 0:
            return 0.0
        value = steps * self.qty_step
        return float(f"{value:.10f}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "quote_currency": self.quote_currency,
            "qty_step": self.qty_step,
            "min_qty": self.min_qty,
            "kind": self.kind,
        }


#: Instruments the paper engine knows about. Public crypto is USD-quoted; the
#: deterministic simulated symbols are quoted in the base currency so the
#: simulated path needs no FX rate at all.
_REGISTRY: dict[str, InstrumentSpec] = {
    "BTC-USD": InstrumentSpec("BTC-USD", "USD", qty_step=0.00001, min_qty=0.00001),
    "ETH-USD": InstrumentSpec("ETH-USD", "USD", qty_step=0.0001, min_qty=0.0001),
}


def instrument_for(symbol: str) -> InstrumentSpec:
    """Look up a symbol. Unknown symbols default to base-currency spot.

    Simulated symbols (``SIM-UP`` and friends) are deliberately base-currency:
    the simulated tape is a synthetic instrument denominated in the account's
    own currency, so no FX conversion is involved and none is invented.
    """
    key = (symbol or "").strip().upper()
    known = _REGISTRY.get(key)
    if known is not None:
        return known
    return InstrumentSpec(key or "UNKNOWN", BASE_CURRENCY, qty_step=0.0001, min_qty=0.0001)


def register_instrument(spec: InstrumentSpec) -> None:
    _REGISTRY[spec.symbol] = spec


def known_instruments() -> list[dict[str, Any]]:
    return [spec.to_dict() for spec in sorted(_REGISTRY.values(), key=lambda s: s.symbol)]
