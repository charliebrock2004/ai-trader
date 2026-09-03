"""Survival configuration. Frozen, and the LLM has no route to any of it.

Thresholds are expressed as fractions of starting equity so the same
configuration works for a £100 experiment and a £1,000 one.

The monotonicity requirement
----------------------------
As capital falls, the agent must become *more* conservative, never less. That
is the whole anti-gambling design: "I will die if I lose money" must not become
"so I should bet bigger to win it back". It is enforced structurally rather
than by prompt:

* ``risk_multiplier`` is non-increasing as the state worsens.
* ``min_edge`` is non-decreasing as the state worsens.
* ``max_exposure_pct`` and ``max_premium_pct`` are non-increasing.

:func:`SurvivalConfig.validate` refuses to construct a configuration that
violates any of those, so a typo cannot quietly create a gambling agent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SurvivalState(str, Enum):
    """Ordered best to worst. ``rank`` is what monotonicity is checked against."""

    HEALTHY = "HEALTHY"
    CAUTION = "CAUTION"
    DEFENSIVE = "DEFENSIVE"
    CRITICAL = "CRITICAL"
    TERMINAL = "TERMINAL"

    @property
    def rank(self) -> int:
        return _ORDER.index(self)

    def is_worse_than(self, other: "SurvivalState") -> bool:
        return self.rank > other.rank


_ORDER = [
    SurvivalState.HEALTHY,
    SurvivalState.CAUTION,
    SurvivalState.DEFENSIVE,
    SurvivalState.CRITICAL,
    SurvivalState.TERMINAL,
]

ORDERED_STATES = tuple(_ORDER)


@dataclass(frozen=True)
class StatePolicy:
    """What one survival state permits. Every field is a ceiling, never a floor."""

    #: Multiplies the risk budget. <= 1.0 always.
    risk_multiplier: float
    #: Minimum net edge (in probability points, 0-1) required to trade at all.
    min_edge: float
    #: Maximum total open exposure as a fraction of equity.
    max_exposure_pct: float
    #: Maximum premium at risk on a single position, as a fraction of equity.
    max_premium_pct: float
    #: Maximum new positions opened per day.
    max_new_positions_per_day: int
    #: Human-readable description shown in the UI.
    description: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "risk_multiplier": self.risk_multiplier,
            "min_edge": self.min_edge,
            "max_exposure_pct": self.max_exposure_pct,
            "max_premium_pct": self.max_premium_pct,
            "max_new_positions_per_day": self.max_new_positions_per_day,
            "description": self.description,
        }


DEFAULT_POLICIES: dict[SurvivalState, StatePolicy] = {
    SurvivalState.HEALTHY: StatePolicy(
        risk_multiplier=1.0,
        min_edge=0.05,
        max_exposure_pct=0.30,
        max_premium_pct=0.10,
        max_new_positions_per_day=4,
        description="Full permitted risk. Still requires a 5-point edge.",
    ),
    SurvivalState.CAUTION: StatePolicy(
        risk_multiplier=0.60,
        min_edge=0.08,
        max_exposure_pct=0.20,
        max_premium_pct=0.06,
        max_new_positions_per_day=3,
        description="Capital has slipped. Smaller size, higher bar.",
    ),
    SurvivalState.DEFENSIVE: StatePolicy(
        risk_multiplier=0.30,
        min_edge=0.12,
        max_exposure_pct=0.12,
        max_premium_pct=0.04,
        max_new_positions_per_day=2,
        description="Preserving capital. Only clear, large edges.",
    ),
    SurvivalState.CRITICAL: StatePolicy(
        risk_multiplier=0.10,
        min_edge=0.20,
        max_exposure_pct=0.05,
        max_premium_pct=0.02,
        max_new_positions_per_day=1,
        description="Near the terminal threshold. Almost everything is a HOLD.",
    ),
    SurvivalState.TERMINAL: StatePolicy(
        risk_multiplier=0.0,
        min_edge=1.0,
        max_exposure_pct=0.0,
        max_premium_pct=0.0,
        max_new_positions_per_day=0,
        description="Dead. No new orders, ever.",
    ),
}


class SurvivalConfigError(ValueError):
    """A configuration that would let losses increase risk. Refused at construction."""


@dataclass(frozen=True)
class SurvivalConfig:
    """Thresholds and per-state policy. Nothing here is reachable by an LLM."""

    starting_equity: float = 100.00
    base_currency: str = "GBP"

    #: Equity fractions of starting equity at which each state begins.
    #: Read as "at or below this fraction, you are in this state".
    caution_at: float = 0.85
    defensive_at: float = 0.70
    critical_at: float = 0.55
    terminal_at: float = 0.40

    #: Equity must recover this much above a boundary before the state
    #: improves, so the agent does not flap across a threshold.
    hysteresis: float = 0.03

    policies: dict[SurvivalState, StatePolicy] = field(
        default_factory=lambda: dict(DEFAULT_POLICIES)
    )

    def __post_init__(self) -> None:
        self.validate()

    # -- validation -------------------------------------------------------
    def validate(self) -> "SurvivalConfig":
        if self.starting_equity <= 0:
            raise SurvivalConfigError("Starting equity must be positive.")
        bounds = [self.caution_at, self.defensive_at, self.critical_at, self.terminal_at]
        if not all(0.0 < b < 1.0 for b in bounds):
            raise SurvivalConfigError("Every threshold must be a fraction strictly between 0 and 1.")
        if not (self.caution_at > self.defensive_at > self.critical_at > self.terminal_at):
            raise SurvivalConfigError(
                "Thresholds must decrease strictly: caution > defensive > critical > terminal."
            )
        if self.hysteresis < 0:
            raise SurvivalConfigError("Hysteresis cannot be negative.")
        missing = [s for s in ORDERED_STATES if s not in self.policies]
        if missing:
            raise SurvivalConfigError(f"Missing policy for {[s.value for s in missing]}.")
        self._assert_monotone()
        return self

    def _assert_monotone(self) -> None:
        """Worse states may only tighten. This is the anti-gambling invariant."""
        previous: StatePolicy | None = None
        for state in ORDERED_STATES:
            policy = self.policies[state]
            if not 0.0 <= policy.risk_multiplier <= 1.0:
                raise SurvivalConfigError(
                    f"{state.value}: risk_multiplier must be within [0, 1]."
                )
            if previous is not None:
                if policy.risk_multiplier > previous.risk_multiplier:
                    raise SurvivalConfigError(
                        f"{state.value} allows more risk than the healthier state above it. "
                        "Losses must never increase permitted size."
                    )
                if policy.min_edge < previous.min_edge:
                    raise SurvivalConfigError(
                        f"{state.value} requires a smaller edge than the healthier state above it."
                    )
                if policy.max_exposure_pct > previous.max_exposure_pct:
                    raise SurvivalConfigError(
                        f"{state.value} allows more exposure than the healthier state above it."
                    )
                if policy.max_premium_pct > previous.max_premium_pct:
                    raise SurvivalConfigError(
                        f"{state.value} allows more premium at risk than the state above it."
                    )
                if policy.max_new_positions_per_day > previous.max_new_positions_per_day:
                    raise SurvivalConfigError(
                        f"{state.value} allows more new positions than the state above it."
                    )
            previous = policy
        terminal = self.policies[SurvivalState.TERMINAL]
        if terminal.risk_multiplier != 0.0 or terminal.max_exposure_pct != 0.0:
            raise SurvivalConfigError("TERMINAL must permit no risk at all.")

    # -- derived numbers --------------------------------------------------
    @property
    def terminal_equity(self) -> float:
        return round(self.starting_equity * self.terminal_at, 2)

    def threshold_equity(self, state: SurvivalState) -> float:
        fractions = {
            SurvivalState.HEALTHY: 1.0,
            SurvivalState.CAUTION: self.caution_at,
            SurvivalState.DEFENSIVE: self.defensive_at,
            SurvivalState.CRITICAL: self.critical_at,
            SurvivalState.TERMINAL: self.terminal_at,
        }
        return round(self.starting_equity * fractions[state], 2)

    def policy(self, state: SurvivalState) -> StatePolicy:
        return self.policies[state]

    def state_for_equity(self, equity: float) -> SurvivalState:
        """State implied by equity alone, ignoring hysteresis and any latch."""
        ratio = float(equity) / self.starting_equity
        if ratio <= self.terminal_at:
            return SurvivalState.TERMINAL
        if ratio <= self.critical_at:
            return SurvivalState.CRITICAL
        if ratio <= self.defensive_at:
            return SurvivalState.DEFENSIVE
        if ratio <= self.caution_at:
            return SurvivalState.CAUTION
        return SurvivalState.HEALTHY

    def to_dict(self) -> dict[str, Any]:
        return {
            "starting_equity": self.starting_equity,
            "base_currency": self.base_currency,
            "terminal_equity": self.terminal_equity,
            "hysteresis": self.hysteresis,
            "thresholds": {
                state.value: self.threshold_equity(state) for state in ORDERED_STATES
            },
            "policies": {state.value: self.policies[state].to_dict() for state in ORDERED_STATES},
        }
