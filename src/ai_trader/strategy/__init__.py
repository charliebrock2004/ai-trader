"""The live candidate detector.

Separate from ``ai_trader.benchmark.strategies`` on purpose. Those four are
frozen yardsticks — the moment one of them is tuned so the live desk trades
more, it stops being an honest baseline to measure the live desk against.
"""

from ai_trader.strategy.signal import (
    REGIME_DOWN,
    REGIME_RANGE,
    REGIME_UNKNOWN,
    REGIME_UP,
    REJECTIONS,
    SETUP_BREAKOUT,
    SETUP_MOMENTUM,
    SETUP_PULLBACK,
    SETUP_RANGE_BOUNCE,
    Signal,
    SignalConfig,
    TrendPullbackStrategy,
    evaluate,
    reference_volatility,
)

__all__ = [
    "REGIME_DOWN",
    "REGIME_RANGE",
    "REGIME_UNKNOWN",
    "REGIME_UP",
    "REJECTIONS",
    "SETUP_BREAKOUT",
    "SETUP_MOMENTUM",
    "SETUP_PULLBACK",
    "SETUP_RANGE_BOUNCE",
    "Signal",
    "SignalConfig",
    "TrendPullbackStrategy",
    "evaluate",
    "reference_volatility",
]
