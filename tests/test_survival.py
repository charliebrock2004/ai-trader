"""Survival: monotone tightening, an irreversible latch, and no gambling path.

The single most important property in this repository is here: **losing money
must never increase permitted risk.** If any test in this file can be made to
pass by loosening a limit, the fix is wrong.
"""

from __future__ import annotations

import itertools
import random

import pytest

from ai_trader.clock import FrozenClock
from ai_trader.db.records import RecordStore
from ai_trader.db.schema import initialise_database
from ai_trader.survival.config import (
    DEFAULT_POLICIES,
    ORDERED_STATES,
    StatePolicy,
    SurvivalConfig,
    SurvivalConfigError,
    SurvivalState,
)
from ai_trader.survival.engine import SurvivalEngine
from ai_trader.survival.latch import AgentTerminatedError, TerminalLatch
from ai_trader.survival.policy import (
    PolicyGuardian,
    PolicyViolationError,
    is_downgrade,
)


def _store(tmp_path, clock=None) -> RecordStore:
    conn = initialise_database(tmp_path / "agent.db")
    return RecordStore(conn, clock=clock or FrozenClock("2026-03-02T09:00:00+00:00"))


def _engine(tmp_path, config=None, *, clock=None):
    clock = clock or FrozenClock("2026-03-02T09:00:00+00:00")
    store = _store(tmp_path, clock)
    latch = TerminalLatch(tmp_path / "TERMINAL", store=store, clock=clock)
    return SurvivalEngine(config or SurvivalConfig(), latch=latch, store=store, clock=clock), store, latch


# ==========================================================================
# Monotonicity — the anti-gambling invariant
# ==========================================================================
def test_permitted_risk_never_increases_as_capital_falls() -> None:
    """For any healthier S1 and less healthy S2: size(S1) >= size(S2)."""
    config = SurvivalConfig()
    for s1, s2 in itertools.combinations(ORDERED_STATES, 2):
        assert s1.rank < s2.rank
        p1, p2 = config.policy(s1), config.policy(s2)
        assert p1.risk_multiplier >= p2.risk_multiplier, (s1, s2)
        assert p1.max_exposure_pct >= p2.max_exposure_pct, (s1, s2)
        assert p1.max_premium_pct >= p2.max_premium_pct, (s1, s2)
        assert p1.max_new_positions_per_day >= p2.max_new_positions_per_day, (s1, s2)


def test_required_edge_never_decreases_as_capital_falls() -> None:
    """For any healthier S1 and less healthy S2: min_edge(S1) <= min_edge(S2)."""
    config = SurvivalConfig()
    for s1, s2 in itertools.combinations(ORDERED_STATES, 2):
        assert config.policy(s1).min_edge <= config.policy(s2).min_edge, (s1, s2)


def test_a_configuration_that_would_reward_losses_is_refused() -> None:
    """A config where a worse state allows more risk must not construct."""
    bad = dict(DEFAULT_POLICIES)
    bad[SurvivalState.CRITICAL] = StatePolicy(
        risk_multiplier=1.0,  # more than DEFENSIVE above it
        min_edge=0.20,
        max_exposure_pct=0.05,
        max_premium_pct=0.02,
        max_new_positions_per_day=1,
        description="gambler",
    )
    with pytest.raises(SurvivalConfigError, match="more risk"):
        SurvivalConfig(policies=bad)


def test_a_configuration_that_lowers_the_edge_bar_when_losing_is_refused() -> None:
    bad = dict(DEFAULT_POLICIES)
    bad[SurvivalState.DEFENSIVE] = StatePolicy(
        risk_multiplier=0.30,
        min_edge=0.01,  # lower than CAUTION above it
        max_exposure_pct=0.12,
        max_premium_pct=0.04,
        max_new_positions_per_day=2,
        description="bad",
    )
    with pytest.raises(SurvivalConfigError, match="smaller edge"):
        SurvivalConfig(policies=bad)


def test_terminal_must_permit_nothing() -> None:
    bad = dict(DEFAULT_POLICIES)
    bad[SurvivalState.TERMINAL] = StatePolicy(
        risk_multiplier=0.01, min_edge=1.0, max_exposure_pct=0.0,
        max_premium_pct=0.0, max_new_positions_per_day=0, description="undead",
    )
    with pytest.raises(SurvivalConfigError, match="no risk at all"):
        SurvivalConfig(policies=bad)


def test_thresholds_must_decrease_strictly() -> None:
    with pytest.raises(SurvivalConfigError, match="decrease strictly"):
        SurvivalConfig(caution_at=0.5, defensive_at=0.7, critical_at=0.6, terminal_at=0.4)


# ==========================================================================
# State machine
# ==========================================================================
def test_states_map_to_equity_bands(tmp_path) -> None:
    engine, _store, _latch = _engine(tmp_path)
    assert engine.observe(100.0) is SurvivalState.HEALTHY
    assert engine.observe(84.0) is SurvivalState.CAUTION
    assert engine.observe(69.0) is SurvivalState.DEFENSIVE
    assert engine.observe(54.0) is SurvivalState.CRITICAL


def test_worsening_is_immediate_but_recovery_needs_hysteresis(tmp_path) -> None:
    engine, _store, _latch = _engine(tmp_path)
    engine.observe(100.0)
    assert engine.observe(84.0) is SurvivalState.CAUTION
    # Just back over the 85 boundary is not enough to relax.
    assert engine.observe(85.5) is SurvivalState.CAUTION
    # Clearing it by the 3% margin is.
    assert engine.observe(88.5) is SurvivalState.HEALTHY


def test_hysteresis_prevents_flapping_across_a_boundary(tmp_path) -> None:
    engine, store, _latch = _engine(tmp_path)
    engine.observe(100.0)
    for equity in [84.9, 85.1, 84.9, 85.1, 84.9, 85.1]:
        engine.observe(equity)
    transitions = store.list_survival_transitions()
    # One move down, and no oscillation back and forth.
    assert len([t for t in transitions if t["to_state"] == "CAUTION"]) == 1
    assert not any(t["to_state"] == "HEALTHY" for t in transitions)


def test_transitions_are_recorded_with_the_equity_that_caused_them(tmp_path) -> None:
    engine, store, _latch = _engine(tmp_path)
    engine.observe(100.0)
    engine.observe(60.0)
    row = store.list_survival_transitions()[0]
    assert row["from_state"] == "HEALTHY"
    assert row["to_state"] == "DEFENSIVE"
    assert row["equity"] == 60.0


def test_a_random_equity_walk_never_relaxes_below_a_boundary(tmp_path) -> None:
    """Fuzz: the state implied by equity is never healthier than the one held."""
    engine, _store, _latch = _engine(tmp_path)
    config = engine.config
    rng = random.Random(7)
    equity = 100.0
    for _ in range(400):
        equity = max(45.0, min(160.0, equity + rng.uniform(-6.0, 6.0)))
        state = engine.observe(equity)
        if state is SurvivalState.TERMINAL:
            break
        implied = config.state_for_equity(equity)
        # Held state may lag behind an improvement, never ahead of a decline.
        assert state.rank >= implied.rank, (equity, state, implied)


# ==========================================================================
# Terminal latch
# ==========================================================================
def test_reaching_the_threshold_terminates(tmp_path) -> None:
    engine, store, latch = _engine(tmp_path)
    engine.observe(100.0)
    assert engine.observe(39.0) is SurvivalState.TERMINAL
    assert engine.is_terminated() is True
    assert latch.is_terminated() is True
    life = store.agent_life()
    assert life["survival_state"] == "TERMINAL"
    assert life["terminated_at"]


def test_terminal_is_absorbing_even_if_equity_recovers(tmp_path) -> None:
    """Money coming back does not resurrect the agent."""
    engine, _store, _latch = _engine(tmp_path)
    engine.observe(39.0)
    assert engine.observe(500.0) is SurvivalState.TERMINAL
    assert engine.observe(10_000.0) is SurvivalState.TERMINAL


def test_terminal_survives_a_process_restart(tmp_path) -> None:
    clock = FrozenClock("2026-03-02T09:00:00+00:00")
    engine, store, _latch = _engine(tmp_path, clock=clock)
    engine.observe(30.0)
    assert engine.is_terminated()

    # A brand new process, same disk.
    latch2 = TerminalLatch(tmp_path / "TERMINAL", store=store, clock=clock)
    engine2 = SurvivalEngine(SurvivalConfig(), latch=latch2, store=store, clock=clock)
    assert engine2.state is SurvivalState.TERMINAL
    assert engine2.is_terminated() is True
    with pytest.raises(AgentTerminatedError):
        engine2.assert_alive()


def test_deleting_the_latch_file_does_not_revive_the_agent(tmp_path) -> None:
    """The database is the second witness. Losing one is not enough."""
    clock = FrozenClock("2026-03-02T09:00:00+00:00")
    engine, store, latch = _engine(tmp_path, clock=clock)
    engine.observe(20.0)
    assert latch.is_terminated()

    (tmp_path / "TERMINAL").unlink()
    revived = TerminalLatch(tmp_path / "TERMINAL", store=store, clock=clock)
    assert revived.is_terminated() is True
    engine2 = SurvivalEngine(SurvivalConfig(), latch=revived, store=store, clock=clock)
    assert engine2.state is SurvivalState.TERMINAL


def test_an_unreadable_latch_file_still_means_dead(tmp_path) -> None:
    """A corrupt latch must fail closed, not open."""
    path = tmp_path / "TERMINAL"
    path.write_text("{ this is not json", encoding="utf-8")
    latch = TerminalLatch(path)
    assert latch.is_terminated() is True
    with pytest.raises(AgentTerminatedError):
        latch.assert_alive()


def test_the_latch_has_no_public_or_private_release_method() -> None:
    """There must be no way, from code, to un-terminate an agent."""
    forbidden = {"clear", "release", "reset", "revive", "disengage", "resume", "untrip"}
    attributes = {name.lstrip("_").lower() for name in dir(TerminalLatch)}
    assert forbidden.isdisjoint(attributes), forbidden & attributes


def test_engaging_the_latch_twice_keeps_the_first_reason(tmp_path) -> None:
    latch = TerminalLatch(tmp_path / "TERMINAL")
    first = latch.engage(reason="first", equity=39.0, threshold=40.0)
    second = latch.engage(reason="second", equity=1.0, threshold=40.0)
    assert first["reason"] == "first"
    assert second["reason"] == "first"


# ==========================================================================
# Policy Guardian
# ==========================================================================
def _guardian(tmp_path, config=None):
    engine, store, latch = _engine(tmp_path, config)
    return PolicyGuardian(engine), engine, store


def test_guardian_passes_a_clean_opportunity(tmp_path) -> None:
    guardian, _engine, _store = _guardian(tmp_path)
    outcome = guardian.review(
        proposed_action="BUY",
        net_edge=0.20,
        equity=100.0,
        data_source="BLS",
        liquidity=500.0,
        min_liquidity=50.0,
        premium_at_risk=5.0,
    )
    assert outcome.action == "BUY"
    assert outcome.approved is True


def test_guardian_can_only_ever_downgrade(tmp_path) -> None:
    """Fuzz every input combination; the action must never get more aggressive."""
    guardian, _engine, _store = _guardian(tmp_path)
    rng = random.Random(11)
    for _ in range(500):
        proposed = rng.choice(["BUY", "SELL", "CLOSE", "HOLD"])
        outcome = guardian.review(
            proposed_action=proposed,
            net_edge=rng.choice([None, -0.5, 0.0, 0.01, 0.06, 0.5]),
            equity=rng.choice([0.0, 41.0, 60.0, 100.0, 250.0]),
            venue=rng.choice(["paper", "rogue-venue"]),
            data_source=rng.choice([None, "BLS", "some-blog"]),
            event_verified=rng.random() > 0.3,
            resolution_known=rng.random() > 0.2,
            liquidity=rng.choice([None, 0.0, 10.0, 900.0]),
            min_liquidity=rng.choice([0.0, 50.0]),
            premium_at_risk=rng.choice([None, 0.5, 5.0, 90.0]),
            current_exposure=rng.uniform(0, 40),
            event_exposure=rng.uniform(0, 20),
            positions_opened_today=rng.randint(0, 6),
            duplicate_event=rng.random() > 0.8,
            systems_disagree=rng.random() > 0.9,
        )
        assert is_downgrade(proposed, outcome.action), (proposed, outcome.action)


def test_guardian_raises_if_it_would_ever_upgrade(tmp_path) -> None:
    guardian, _engine, _store = _guardian(tmp_path)
    with pytest.raises(PolicyViolationError):
        guardian._outcome("HOLD", "BUY", "should be impossible", [], approved=True)


def test_uncertainty_means_hold(tmp_path) -> None:
    guardian, _engine, _store = _guardian(tmp_path)
    base = dict(proposed_action="BUY", net_edge=0.3, equity=100.0, data_source="BLS")
    for override, expected in [
        ({"net_edge": None}, "No edge"),
        ({"event_verified": False}, "not verified"),
        ({"resolution_known": False}, "resolution rules"),
        ({"systems_disagree": True}, "disagree"),
        ({"duplicate_event": True}, "already has an executed position"),
        ({"venue": "somewhere-else"}, "not on the approved list"),
        ({"data_source": "a-random-blog"}, "not trusted"),
    ]:
        outcome = guardian.review(**{**base, **override})
        assert outcome.action == "HOLD", override
        assert expected in outcome.reason, (override, outcome.reason)


def test_a_thin_edge_is_refused_and_the_bar_rises_as_capital_falls(tmp_path) -> None:
    guardian, engine, _store = _guardian(tmp_path)
    # HEALTHY: a 6-point edge clears the 5-point bar.
    healthy = guardian.review(proposed_action="BUY", net_edge=0.06, equity=100.0,
                              data_source="BLS", premium_at_risk=1.0)
    assert healthy.action == "BUY"
    # DEFENSIVE at £69: the bar is 12 points, so the same edge is refused.
    engine.observe(69.0)
    defensive = guardian.review(proposed_action="BUY", net_edge=0.06, equity=69.0,
                                data_source="BLS", premium_at_risk=1.0)
    assert defensive.action == "HOLD"
    assert "below the DEFENSIVE minimum" in defensive.reason


def test_losing_money_shrinks_the_allowed_premium(tmp_path) -> None:
    guardian, engine, _store = _guardian(tmp_path)
    healthy = guardian.review(proposed_action="BUY", net_edge=0.5, equity=100.0,
                              data_source="BLS", premium_at_risk=9.0)
    assert healthy.action == "BUY", "10% of £100 is allowed while HEALTHY"
    engine.observe(54.0)  # CRITICAL: at or below 55% of starting equity
    critical = guardian.review(proposed_action="BUY", net_edge=0.5, equity=54.0,
                               data_source="BLS", premium_at_risk=9.0)
    assert critical.action == "HOLD"
    assert "exceeds the CRITICAL cap" in critical.reason


def test_correlated_exposure_on_one_event_is_capped(tmp_path) -> None:
    guardian, _engine, _store = _guardian(tmp_path)
    outcome = guardian.review(
        proposed_action="BUY", net_edge=0.5, equity=100.0, data_source="BLS",
        premium_at_risk=6.0, event_exposure=6.0,
    )
    assert outcome.action == "HOLD"
    assert "single event" in outcome.reason


def test_terminated_agent_is_refused_by_the_guardian(tmp_path) -> None:
    guardian, engine, _store = _guardian(tmp_path)
    engine.observe(10.0)
    outcome = guardian.review(proposed_action="BUY", net_edge=0.99, equity=10.0,
                              data_source="BLS", premium_at_risk=0.1)
    assert outcome.action == "HOLD"
    assert "TERMINATED" in outcome.reason


def test_closing_a_position_is_never_blocked_by_opening_gates(tmp_path) -> None:
    """Getting out of risk must not be gated on edge, liquidity or exposure."""
    guardian, engine, _store = _guardian(tmp_path)
    engine.observe(60.0)
    outcome = guardian.review(proposed_action="CLOSE", net_edge=None, equity=60.0)
    assert outcome.action == "CLOSE"
    assert outcome.approved is True


# ==========================================================================
# Cost independence
# ==========================================================================
def test_accrued_operating_cost_does_not_change_sizing_or_the_edge_bar(tmp_path) -> None:
    """Runway pressure must never make the agent trade bigger or looser.

    The guardian's inputs are equity and the survival state. Cost is not one of
    them, and this test asserts that by recording a large cost and checking the
    verdict is byte-identical.
    """
    guardian, _engine, store = _guardian(tmp_path)
    kwargs = dict(
        proposed_action="BUY", net_edge=0.06, equity=100.0,
        data_source="BLS", premium_at_risk=5.0, liquidity=500.0, min_liquidity=10.0,
    )
    before = guardian.review(**kwargs).to_dict()

    for _ in range(50):
        store.record_cost(category="llm", description="grok", amount_base=0.50)
    store.record_cost(category="hosting", description="month", amount_base=25.0)
    assert store.total_costs() == pytest.approx(50.0)

    after = guardian.review(**kwargs).to_dict()
    assert after == before


def test_guardian_never_reads_the_cost_ledger() -> None:
    """Structural check: no cost concept appears in the guardian's real code.

    Parsed rather than grepped, so the module docstring explaining *why* costs
    are excluded does not trip the check.
    """
    import ast
    import inspect

    from ai_trader.survival import policy

    tree = ast.parse(inspect.getsource(policy))
    identifiers: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            identifiers.add(node.id.lower())
        elif isinstance(node, ast.Attribute):
            identifiers.add(node.attr.lower())
        elif isinstance(node, ast.arg):
            identifiers.add(node.arg.lower())
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            identifiers.add((getattr(node, "module", "") or "").lower())
            for alias in node.names:
                identifiers.add(alias.name.lower())
    offenders = {
        name for name in identifiers
        if any(token in name for token in ("cost", "runway", "burn", "expense"))
    }
    assert not offenders, f"Guardian must not depend on operating cost: {offenders}"


# ==========================================================================
# Snapshot
# ==========================================================================
def test_snapshot_reports_life_remaining_between_start_and_terminal(tmp_path) -> None:
    engine, _store, _latch = _engine(tmp_path)
    engine.observe(100.0)
    assert engine.snapshot()["life_remaining_pct"] == 100.0
    engine.observe(70.0)
    # 70 sits half way between the 40 terminal threshold and 100 start.
    assert engine.snapshot()["life_remaining_pct"] == 50.0
    engine.observe(40.0)
    assert engine.snapshot()["life_remaining_pct"] == 0.0
    assert engine.snapshot()["terminated"] is True


def test_milestones_are_informational_and_do_not_change_limits(tmp_path) -> None:
    engine, store, _latch = _engine(tmp_path)
    before = engine.policy.risk_multiplier
    engine.observe(1_200.0)
    keys = {m["key"] for m in store.list_milestones()}
    assert {"equity_100", "equity_200", "equity_500", "equity_1000"} <= keys
    assert engine.policy.risk_multiplier == before, "a milestone must not unlock more risk"
