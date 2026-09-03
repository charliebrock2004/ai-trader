"""The survival state machine.

Equity in, state out. The transition is deterministic and the LLM contributes
nothing to it: the input is a number produced by the ledger, and the output is
a ceiling on what the agent may do next.

Hysteresis
----------
A state only *improves* once equity clears the boundary by ``hysteresis``.
Worsening is immediate. That asymmetry is deliberate — being slow to relax and
quick to tighten is the conservative direction.

Termination
-----------
Reaching the terminal threshold engages :class:`TerminalLatch` and is the end
of the agent. There is no path back through this class.
"""

from __future__ import annotations

from typing import Any, Optional

from ai_trader.clock import Clock, default_clock
from ai_trader.survival.config import (
    ORDERED_STATES,
    StatePolicy,
    SurvivalConfig,
    SurvivalState,
)
from ai_trader.survival.latch import TerminalLatch

#: Informational only. Reaching a milestone never changes what is permitted.
MILESTONES: tuple[tuple[float, str, str], ...] = (
    (100.0, "equity_100", "Born — £100 of starting capital"),
    (200.0, "equity_200", "First growth — doubled the stake"),
    (500.0, "equity_500", "Established"),
    (1_000.0, "equity_1000", "Significant capital"),
    (5_000.0, "equity_5000", "Advanced"),
    (10_000.0, "equity_10000", "Major milestone"),
)


class SurvivalEngine:
    """Owns survival state, the terminal latch, and the life record."""

    def __init__(
        self,
        config: Optional[SurvivalConfig] = None,
        *,
        latch: Optional[TerminalLatch] = None,
        store: Any = None,
        clock: Optional[Clock] = None,
    ) -> None:
        self.config = config or SurvivalConfig()
        self.store = store
        self.clock = clock or default_clock()
        self.latch = latch
        self._state = SurvivalState.HEALTHY
        self._last_equity = self.config.starting_equity
        self._highest_equity = self.config.starting_equity
        self._bootstrap()

    # -- lifecycle --------------------------------------------------------
    def _bootstrap(self) -> None:
        if self.latch is not None and self.latch.is_terminated():
            self._state = SurvivalState.TERMINAL
        if self.store is None:
            return
        life = self.store.agent_life()
        if life is None:
            life = self.store.init_agent_life(
                born_at=self.clock.now_iso(),
                base_currency=self.config.base_currency,
                starting_equity=self.config.starting_equity,
                terminal_threshold=self.config.terminal_equity,
                survival_state=self._state.value,
            )
            self.store.record_milestone(
                key="equity_100", label=MILESTONES[0][2], equity=self.config.starting_equity
            )
        recorded = str(life.get("survival_state") or SurvivalState.HEALTHY.value)
        try:
            restored = SurvivalState(recorded)
        except ValueError:
            restored = SurvivalState.HEALTHY
        # A recorded TERMINAL always wins, even if the latch file was lost.
        if restored is SurvivalState.TERMINAL or life.get("terminated_at"):
            self._state = SurvivalState.TERMINAL
        elif self._state is not SurvivalState.TERMINAL:
            self._state = restored
        self._highest_equity = float(life.get("highest_equity") or self.config.starting_equity)

    # -- reads ------------------------------------------------------------
    @property
    def state(self) -> SurvivalState:
        return self._state

    @property
    def policy(self) -> StatePolicy:
        return self.config.policy(self._state)

    def is_terminated(self) -> bool:
        if self._state is SurvivalState.TERMINAL:
            return True
        if self.latch is not None and self.latch.is_terminated():
            self._state = SurvivalState.TERMINAL
            return True
        return False

    def assert_alive(self) -> None:
        if self.latch is not None:
            self.latch.assert_alive()
        if self._state is SurvivalState.TERMINAL:
            from ai_trader.survival.latch import AgentTerminatedError

            raise AgentTerminatedError({"reason": "Survival state is TERMINAL."})

    # -- the transition ---------------------------------------------------
    def observe(self, equity: float, *, reason: str = "equity update") -> SurvivalState:
        """Feed equity in; get the (possibly new) state out.

        Worsening applies immediately. Improving requires clearing the boundary
        by the hysteresis margin. Terminal is absorbing.
        """
        equity = float(equity)
        self._last_equity = equity
        if self.is_terminated():
            return SurvivalState.TERMINAL

        if equity > self._highest_equity:
            self._highest_equity = equity
            if self.store is not None:
                self.store.update_agent_life(highest_equity=round(equity, 2))
        self._check_milestones(equity)

        implied = self.config.state_for_equity(equity)
        previous = self._state

        if implied is SurvivalState.TERMINAL:
            return self._terminate(equity, reason=reason)

        if implied.rank > previous.rank:
            # Worse. Apply immediately.
            return self._transition(previous, implied, equity, reason)

        if implied.rank < previous.rank:
            # Better. Only relax once the boundary is cleared by the margin.
            if not self._clears_hysteresis(equity, previous):
                return previous
            return self._transition(previous, implied, equity, f"{reason} (recovery)")

        return previous

    def _clears_hysteresis(self, equity: float, current: SurvivalState) -> bool:
        """Has equity risen far enough above the current state's floor to relax?"""
        boundary = self.config.threshold_equity(current)
        margin = self.config.starting_equity * self.config.hysteresis
        return equity > boundary + margin

    def _transition(
        self,
        previous: SurvivalState,
        nxt: SurvivalState,
        equity: float,
        reason: str,
    ) -> SurvivalState:
        self._state = nxt
        if self.store is not None:
            self.store.record_survival_transition(
                from_state=previous.value,
                to_state=nxt.value,
                equity=round(equity, 2),
                threshold=self.config.threshold_equity(nxt),
                reason=reason,
                irreversible=nxt is SurvivalState.TERMINAL,
            )
            self.store.update_agent_life(survival_state=nxt.value)
        return nxt

    def _terminate(self, equity: float, *, reason: str) -> SurvivalState:
        detail = (
            f"Equity {equity:.2f} {self.config.base_currency} reached the terminal "
            f"threshold {self.config.terminal_equity:.2f}. {reason}"
        )
        previous = self._state
        self._state = SurvivalState.TERMINAL
        if self.store is not None:
            self.store.record_survival_transition(
                from_state=previous.value,
                to_state=SurvivalState.TERMINAL.value,
                equity=round(equity, 2),
                threshold=self.config.terminal_equity,
                reason=detail,
                irreversible=True,
            )
        if self.latch is not None:
            self.latch.engage(
                reason=detail, equity=equity, threshold=self.config.terminal_equity
            )
        elif self.store is not None:
            self.store.update_agent_life(
                survival_state=SurvivalState.TERMINAL.value,
                terminated_at=self.clock.now_iso(),
                terminal_reason=detail,
            )
        return SurvivalState.TERMINAL

    def _check_milestones(self, equity: float) -> None:
        if self.store is None:
            return
        for threshold, key, label in MILESTONES:
            if equity >= threshold:
                self.store.record_milestone(key=key, label=label, equity=round(equity, 2))

    # -- presentation -----------------------------------------------------
    def snapshot(self, *, equity: Optional[float] = None) -> dict[str, Any]:
        eq = self._last_equity if equity is None else float(equity)
        policy = self.policy
        start = self.config.starting_equity
        terminal = self.config.terminal_equity
        span = max(start - terminal, 1e-9)
        remaining = max(0.0, min(1.0, (eq - terminal) / span))
        return {
            "state": self._state.value,
            "terminated": self._state is SurvivalState.TERMINAL,
            "equity": round(eq, 2),
            "starting_equity": start,
            "highest_equity": round(self._highest_equity, 2),
            "terminal_threshold": terminal,
            "base_currency": self.config.base_currency,
            "distance_to_terminal": round(eq - terminal, 2),
            "life_remaining_pct": round(remaining * 100.0, 1),
            "drawdown_from_peak_pct": (
                round(max(0.0, (self._highest_equity - eq) / self._highest_equity) * 100.0, 2)
                if self._highest_equity > 0
                else 0.0
            ),
            "policy": policy.to_dict(),
            "thresholds": {s.value: self.config.threshold_equity(s) for s in ORDERED_STATES},
            "latch": self.latch.snapshot() if self.latch is not None else {"terminated": False},
        }
