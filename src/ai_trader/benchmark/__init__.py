"""Paper-only strategy validation. No broker. No live trading."""

from ai_trader.benchmark.runner import run_benchmark
from ai_trader.benchmark.splits import DEFAULT_PERIODS, HEADLINE_SPLIT
from ai_trader.benchmark.strategies import STRATEGY_NAMES

__all__ = [
    "run_benchmark",
    "DEFAULT_PERIODS",
    "HEADLINE_SPLIT",
    "STRATEGY_NAMES",
]
