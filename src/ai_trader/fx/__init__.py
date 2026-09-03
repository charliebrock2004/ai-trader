"""FX rate providers. Fail closed: no rate means no trade."""

from ai_trader.fx.provider import (
    FxProvider,
    FxRateUnavailableError,
    PinnedFxProvider,
    PublicFxFeed,
)

__all__ = [
    "FxProvider",
    "FxRateUnavailableError",
    "PinnedFxProvider",
    "PublicFxFeed",
]
