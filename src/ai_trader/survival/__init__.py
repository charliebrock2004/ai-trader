"""Deterministic survival: state, latch, and the guardian.

Nothing in this package is reachable from an LLM response. The inputs are
equity and a set of frozen thresholds; the outputs are ceilings.
"""

from ai_trader.survival.config import (
    ORDERED_STATES,
    DEFAULT_POLICIES,
    StatePolicy,
    SurvivalConfig,
    SurvivalConfigError,
    SurvivalState,
)
from ai_trader.survival.engine import MILESTONES, SurvivalEngine
from ai_trader.survival.latch import AgentTerminatedError, TerminalLatch
from ai_trader.survival.policy import (
    PolicyGuardian,
    PolicyOutcome,
    PolicyViolationError,
    is_downgrade,
)

__all__ = [
    "AgentTerminatedError",
    "DEFAULT_POLICIES",
    "MILESTONES",
    "ORDERED_STATES",
    "PolicyGuardian",
    "PolicyOutcome",
    "PolicyViolationError",
    "StatePolicy",
    "SurvivalConfig",
    "SurvivalConfigError",
    "SurvivalEngine",
    "SurvivalState",
    "TerminalLatch",
    "is_downgrade",
]
