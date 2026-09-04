"""The live candidate detector.

Separate from ``ai_trader.benchmark.strategies`` on purpose. Those four are
frozen yardsticks — the moment one of them is tuned so the live desk trades
more, it stops being an honest baseline to measure the live desk against.
"""

from ai_trader.strategy.signal import (
    REJECTIONS,
    Signal,
    SignalConfig,
    TrendPullbackStrategy,
    evaluate,
)

__all__ = [
    "REJECTIONS",
    "Signal",
    "SignalConfig",
    "TrendPullbackStrategy",
    "evaluate",
]
