"""Baseline strategies evaluated under one execution model."""

from btc_research.strategies.baselines import (
    CANDIDATE_NAMES,
    SESSION_HOURS,
    BuyAndHold,
    MeanReversion,
    Momentum,
    TimeWindow,
    VolFilteredMomentum,
    default_strategies,
)

__all__ = [
    "CANDIDATE_NAMES",
    "SESSION_HOURS",
    "BuyAndHold",
    "MeanReversion",
    "Momentum",
    "TimeWindow",
    "VolFilteredMomentum",
    "default_strategies",
]
