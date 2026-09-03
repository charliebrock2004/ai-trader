"""Durability: the agent is its database, not its process.

A worker restart must change nothing, and a TERMINATED agent must stay dead
across restarts even if the latch file is lost.
"""

from __future__ import annotations

import pytest

from ai_trader.agent.runtime import AgentRuntime
from ai_trader.clock import FrozenClock
from ai_trader.db.records import RecordStore
from ai_trader.db.schema import initialise_database
from ai_trader.events.bls import FixtureEventSource
from ai_trader.markets.paper import PaperPredictionMarket
from ai_trader.survival.config import SurvivalConfig
from ai_trader.survival.latch import AgentTerminatedError

from tests.test_agent_cycle import StubAnalyst, _book, _contract, _observation, _release


_DEFAULT = object()


def _runtime(tmp_path, *, clock=None, analyst=_DEFAULT, db="agent.db"):
    clock = clock or FrozenClock("2026-04-14T14:00:00+00:00")
    store = RecordStore(initialise_database(tmp_path / db), clock=clock)
    market = PaperPredictionMarket(clock=clock)
    contract = _contract()
    market.register(contract)
    market.set_book(_book())
    source = FixtureEventSource(clock=clock)
    source.add(_release(), _observation())
    runtime = AgentRuntime(
        store=store,
        data_dir=tmp_path,
        survival_config=SurvivalConfig(starting_equity=100.0),
        clock=clock,
        event_source=source,
        market=market,
        analyst=StubAnalyst(proceed=True) if analyst is _DEFAULT else analyst,
        fx_rate=0.80,
        quote_currency="USD",
    )
    return runtime, store, clock


# ==========================================================================
# Restart
# ==========================================================================
def test_a_fresh_agent_starts_at_the_configured_stake(tmp_path) -> None:
    runtime, _store, _clock = _runtime(tmp_path)
    status = runtime.status()
    assert status["alive"] is True
    assert status["account"]["equity"] == 100.0
    assert status["survival"]["state"] == "HEALTHY"
    assert status["survival"]["life_remaining_pct"] == 100.0
    assert status["born_at"]


def test_an_open_position_survives_a_restart(tmp_path) -> None:
    runtime, _store, clock = _runtime(tmp_path)
    report = runtime.run_cycle()
    assert report["traded"] == 1
    cash_before = runtime.ledger.cash
    equity_before = runtime.ledger.equity()
    position = runtime.ledger.open_positions()[0]

    # A brand new process against the same database and disk.
    restarted, _store2, _clock2 = _runtime(tmp_path, clock=clock)
    assert len(restarted.ledger.open_positions()) == 1
    restored = restarted.ledger.open_positions()[0]
    assert restored.ticker == position.ticker
    assert restored.contracts == position.contracts
    assert restarted.ledger.cash == pytest.approx(cash_before, abs=0.01)
    assert restarted.ledger.equity() == pytest.approx(equity_before, abs=0.01)


def test_realised_pnl_survives_a_restart(tmp_path) -> None:
    runtime, _store, clock = _runtime(tmp_path)
    runtime.run_cycle()
    clock.set("2026-04-20T00:00:00+00:00")
    runtime.run_cycle()
    assert not runtime.ledger.open_positions()
    equity = runtime.ledger.equity()
    assert equity > 100.0

    restarted, _store2, _c = _runtime(tmp_path, clock=clock)
    assert restarted.ledger.equity() == pytest.approx(equity, abs=0.01)
    assert restarted.ledger.realised_pnl == pytest.approx(runtime.ledger.realised_pnl, abs=0.01)


def test_decision_history_is_never_lost_on_restart(tmp_path) -> None:
    runtime, store, clock = _runtime(tmp_path)
    runtime.run_cycle()
    before = store.decision_counts()["TOTAL"]
    assert before > 0
    restarted, store2, _c = _runtime(tmp_path, clock=clock)
    assert store2.decision_counts()["TOTAL"] == before


# ==========================================================================
# Termination survives everything
# ==========================================================================
def test_a_terminated_agent_refuses_to_start(tmp_path) -> None:
    runtime, _store, clock = _runtime(tmp_path)
    runtime.survival.observe(10.0)
    assert runtime.survival.is_terminated()

    restarted, _store2, _c = _runtime(tmp_path, clock=clock)
    assert restarted.status()["terminated"] is True
    with pytest.raises(AgentTerminatedError):
        restarted.assert_alive()


def test_a_terminated_agent_runs_no_cycle_and_says_why(tmp_path) -> None:
    runtime, _store, _clock = _runtime(tmp_path)
    runtime.survival.observe(5.0)
    result = runtime.run_cycle()
    assert result["ok"] is False
    assert result["terminated"] is True
    assert "TERMINATED" in result["error"]


def test_losing_the_latch_file_does_not_revive_the_agent(tmp_path) -> None:
    runtime, _store, clock = _runtime(tmp_path)
    runtime.survival.observe(10.0)
    (tmp_path / "TERMINAL").unlink()

    restarted, _store2, _c = _runtime(tmp_path, clock=clock)
    assert restarted.status()["terminated"] is True, "the database is the second witness"


# ==========================================================================
# Status surfaces only real state
# ==========================================================================
def test_status_reports_costs_and_runway(tmp_path) -> None:
    runtime, _store, _clock = _runtime(tmp_path)
    runtime.costs.record_hosting(amount_base=1.0)
    status = runtime.status()
    assert status["costs"]["operating_costs"] == 1.0
    assert status["costs"]["spendable_capital"] == 60.0
    assert status["costs"]["runway_days"] is not None


def test_status_counts_holds_as_well_as_trades(tmp_path) -> None:
    runtime, _store, _clock = _runtime(tmp_path)
    runtime.run_cycle()
    counts = runtime.status()["decisions"]
    assert counts["TOTAL"] >= 1
    assert "EXECUTED" in counts and "NOT_EXECUTED" in counts


def test_the_next_milestone_is_reported_but_unlocks_nothing(tmp_path) -> None:
    runtime, _store, _clock = _runtime(tmp_path)
    status = runtime.status()
    assert status["next_milestone"]["equity"] == 200.0
    assert status["config"]["risk_limits"]["max_premium_pct"] == 0.10


def test_system_page_reports_a_missing_component_as_broken(tmp_path) -> None:
    runtime, _store, _clock = _runtime(tmp_path, analyst=None)
    system = runtime.system()
    analyst = [c for c in system["components"] if c["id"] == "analyst"][0]
    assert analyst["ok"] is False
    assert system["ok"] is False, "a dashboard must not look healthy when it is not"
    assert system["paper_only"] is True


def test_system_page_shows_termination(tmp_path) -> None:
    runtime, _store, _clock = _runtime(tmp_path)
    runtime.survival.observe(5.0)
    system = runtime.system()
    agent = [c for c in system["components"] if c["id"] == "agent"][0]
    assert agent["ok"] is False
    assert "TERMINATED" in agent["detail"]


def test_performance_comes_from_persisted_data(tmp_path) -> None:
    runtime, _store, clock = _runtime(tmp_path)
    runtime.run_cycle()
    clock.set("2026-04-20T00:00:00+00:00")
    runtime.run_cycle()
    performance = runtime.performance()
    assert performance["trades"] == 1
    assert performance["calibration"]["count"] == 1
    assert "too few" in performance["calibration"]["verdict"]
    assert "provisional" in performance["evidence_note"]
