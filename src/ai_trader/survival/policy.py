"""The Policy Guardian.

Sits between the analyst and the risk engine. Its single structural guarantee:

    **It can only make a proposal more conservative.**

Every check returns either the proposal unchanged or something strictly safer.
There is no branch that turns HOLD into BUY, and no branch that raises a size,
loosens a threshold or extends an exposure limit. :meth:`PolicyGuardian.review`
asserts this on the way out, so a future edit that breaks the rule fails
immediately rather than silently creating a gambling agent.

The guardian is deterministic. It never asks the analyst anything, it never
reads model text as an instruction, and cost pressure is not one of its inputs
— running low on runway must not change what the agent is allowed to trade.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from ai_trader.survival.config import SurvivalState
from ai_trader.survival.engine import SurvivalEngine

#: Ranked most to least aggressive. A downgrade may only move rightwards.
ACTION_ORDER = ("BUY", "SELL", "CLOSE", "HOLD")

#: Actions that open new risk. Only these are gated by edge and exposure.
OPENING_ACTIONS = frozenset({"BUY"})


def _rank(action: str) -> int:
    try:
        return ACTION_ORDER.index(action.upper())
    except ValueError:
        return ACTION_ORDER.index("HOLD")


def is_downgrade(before: str, after: str) -> bool:
    """True when ``after`` is no more aggressive than ``before``."""
    return _rank(after) >= _rank(before)


@dataclass(frozen=True)
class PolicyOutcome:
    action: str
    reason: str
    approved: bool
    survival_state: str
    risk_multiplier: float
    min_edge: float
    max_premium: float
    max_exposure: float
    checks: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "reason": self.reason,
            "approved": self.approved,
            "survival_state": self.survival_state,
            "risk_multiplier": self.risk_multiplier,
            "min_edge": self.min_edge,
            "max_premium": self.max_premium,
            "max_exposure": self.max_exposure,
            "checks": self.checks,
        }


class PolicyViolationError(RuntimeError):
    """The guardian tried to return something more aggressive than it received."""


class PolicyGuardian:
    """Deterministic gate. Downgrade-only, by construction and by assertion."""

    def __init__(
        self,
        survival: SurvivalEngine,
        *,
        approved_venues: frozenset[str] = frozenset({"paper"}),
        trusted_sources: frozenset[str] = frozenset({"BLS", "BEA", "NOAA", "FIXTURE"}),
    ) -> None:
        self.survival = survival
        self.approved_venues = approved_venues
        self.trusted_sources = trusted_sources

    # -- helpers used by the spot simulator --------------------------------
    def risk_multiplier(self) -> float:
        return min(1.0, max(0.0, self.survival.policy.risk_multiplier))

    def is_terminated(self) -> bool:
        return self.survival.is_terminated()

    def review_spot(self, *, action: str, account: dict[str, Any], bar: int = 0) -> PolicyOutcome:
        """Minimal review for the spot paper simulator.

        The spot path has no edge estimate, so only the survival state, the
        terminal latch and the daily position budget apply.
        """
        equity = float(account.get("account_equity") or account.get("equity") or 0.0)
        self.survival.observe(equity, reason=f"spot bar {bar}")
        state = self.survival.state
        policy = self.survival.policy
        checks: list[dict[str, Any]] = []
        proposed = action.upper()

        if self.survival.is_terminated():
            return self._outcome(proposed, "HOLD", "Agent is TERMINATED.", checks, approved=False)
        if proposed in OPENING_ACTIONS and policy.risk_multiplier <= 0:
            return self._outcome(
                proposed, "HOLD", f"{state.value} permits no new risk.", checks, approved=False
            )
        checks.append({"name": "survival_state", "passed": True, "detail": state.value})
        return self._outcome(
            proposed, proposed, f"Permitted under {state.value}.", checks, approved=True
        )

    # -- the full review ---------------------------------------------------
    def review(
        self,
        *,
        proposed_action: str,
        net_edge: Optional[float] = None,
        equity: float = 0.0,
        venue: str = "paper",
        data_source: Optional[str] = None,
        event_verified: bool = True,
        resolution_known: bool = True,
        liquidity: Optional[float] = None,
        min_liquidity: float = 0.0,
        premium_at_risk: Optional[float] = None,
        current_exposure: float = 0.0,
        event_exposure: float = 0.0,
        positions_opened_today: int = 0,
        daily_loss: float = 0.0,
        daily_loss_limit: Optional[float] = None,
        duplicate_event: bool = False,
        systems_disagree: bool = False,
    ) -> PolicyOutcome:
        """Run every deterministic gate. Any failure yields HOLD.

        ``premium_at_risk`` is the worst case for this position in base
        currency — for a binary contract, the entire premium.
        """
        proposed = (proposed_action or "HOLD").upper()
        self.survival.observe(equity, reason="policy review")
        state = self.survival.state
        policy = self.survival.policy
        checks: list[dict[str, Any]] = []

        def fail(name: str, detail: str) -> PolicyOutcome:
            checks.append({"name": name, "passed": False, "detail": detail})
            return self._outcome(proposed, "HOLD", detail, checks, approved=False)

        def ok(name: str, detail: str = "") -> None:
            checks.append({"name": name, "passed": True, "detail": detail})

        # 1. Terminal is absolute and comes first.
        if self.survival.is_terminated():
            return fail("terminal", "Agent is TERMINATED. No new orders.")
        ok("terminal", state.value)

        # 2. Anything that does not open risk skips the opening gates. Closing
        #    a position is always at least as safe as holding it.
        if proposed not in OPENING_ACTIONS:
            ok("non_opening_action", f"{proposed} does not open new risk.")
            return self._outcome(
                proposed, proposed, f"{proposed} permitted under {state.value}.", checks, approved=True
            )

        # 3. Venue and data provenance.
        if venue not in self.approved_venues:
            return fail("venue", f"Venue '{venue}' is not on the approved list.")
        ok("venue", venue)
        if data_source is not None and data_source.upper() not in self.trusted_sources:
            return fail("source", f"Data source '{data_source}' is not trusted.")
        ok("source", data_source or "n/a")
        if not event_verified:
            return fail("event_verified", "Official data is not verified. Uncertainty means HOLD.")
        ok("event_verified")
        if not resolution_known:
            return fail("resolution", "Contract resolution rules are not understood. HOLD.")
        ok("resolution")
        if systems_disagree:
            return fail("consistency", "Systems disagree about this opportunity. HOLD.")
        ok("consistency")
        if duplicate_event:
            return fail("duplicate", "This event already has an executed position.")
        ok("duplicate")

        # 4. Edge must clear the bar the survival state sets.
        if net_edge is None:
            return fail("edge", "No edge was computed. Uncertainty means HOLD.")
        if net_edge < policy.min_edge:
            return fail(
                "edge",
                f"Net edge {net_edge:.4f} is below the {state.value} minimum "
                f"{policy.min_edge:.4f}.",
            )
        ok("edge", f"{net_edge:.4f} >= {policy.min_edge:.4f}")

        # 5. Liquidity.
        if min_liquidity > 0 and (liquidity is None or liquidity < min_liquidity):
            return fail(
                "liquidity",
                f"Liquidity {liquidity} is below the {min_liquidity} minimum.",
            )
        ok("liquidity", str(liquidity))

        # 6. Exposure ceilings, in base currency.
        if equity <= 0:
            return fail("equity", "Equity is not positive.")
        max_premium = round(equity * policy.max_premium_pct, 4)
        max_exposure = round(equity * policy.max_exposure_pct, 4)
        if premium_at_risk is not None and premium_at_risk > max_premium + 1e-9:
            return fail(
                "premium",
                f"Premium at risk {premium_at_risk:.2f} exceeds the {state.value} cap "
                f"{max_premium:.2f}.",
            )
        ok("premium", f"cap {max_premium:.2f}")
        projected = current_exposure + (premium_at_risk or 0.0)
        if projected > max_exposure + 1e-9:
            return fail(
                "exposure",
                f"Total exposure {projected:.2f} would exceed the {state.value} cap "
                f"{max_exposure:.2f}.",
            )
        ok("exposure", f"cap {max_exposure:.2f}")
        event_projected = event_exposure + (premium_at_risk or 0.0)
        if event_projected > max_premium + 1e-9:
            return fail(
                "correlated_exposure",
                f"Exposure to this single event {event_projected:.2f} would exceed "
                f"{max_premium:.2f}. Contracts resolving on one release are correlated.",
            )
        ok("correlated_exposure")

        # 7. Daily budgets.
        if positions_opened_today >= policy.max_new_positions_per_day:
            return fail(
                "daily_positions",
                f"{state.value} allows {policy.max_new_positions_per_day} new positions "
                "per day and that is spent.",
            )
        ok("daily_positions")
        if daily_loss_limit is not None and daily_loss <= -abs(daily_loss_limit):
            return fail("daily_loss", "Daily loss limit reached.")
        ok("daily_loss")

        return self._outcome(
            proposed,
            proposed,
            f"Cleared every guardian check under {state.value}.",
            checks,
            approved=True,
        )

    # -- construction with the safety assertion ---------------------------
    def _outcome(
        self,
        proposed: str,
        action: str,
        reason: str,
        checks: list[dict[str, Any]],
        *,
        approved: bool,
    ) -> PolicyOutcome:
        if not is_downgrade(proposed, action):
            raise PolicyViolationError(
                f"Guardian tried to upgrade {proposed} to {action}. "
                "The guardian may only ever be more conservative."
            )
        policy = self.survival.policy
        state = self.survival.state
        equity_based_premium = 0.0
        equity_based_exposure = 0.0
        return PolicyOutcome(
            action=action,
            reason=reason,
            approved=approved and action == proposed,
            survival_state=state.value,
            risk_multiplier=min(1.0, max(0.0, policy.risk_multiplier)),
            min_edge=policy.min_edge,
            max_premium=policy.max_premium_pct,
            max_exposure=policy.max_exposure_pct,
            checks=checks,
        )
