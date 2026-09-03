"""Calibration, performance from persisted data, replay determinism, benchmarks."""

from __future__ import annotations

import pytest

from ai_trader.agent.cycle import AgentCycle
from ai_trader.analytics.calibration import brier_score, build_report
from ai_trader.analytics.performance import compute_performance
from ai_trader.benchmark.event_benchmark import (
    BenchmarkCase,
    agent_strategy,
    deterministic_edge,
    no_trade,
    run_benchmark,
    run_strategy,
    split_by_time,
)
from ai_trader.clock import FrozenClock
from ai_trader.contracts.ledger import ContractLedger
from ai_trader.contracts.risk import ContractRiskEngine
from ai_trader.costs.ledger import CostLedger
from ai_trader.db.records import RecordStore
from ai_trader.db.schema import initialise_database
from ai_trader.edge.opportunity import OpportunityEngine
from ai_trader.markets.fees import STANDARD_FEES, ZeroFeeModel
from ai_trader.markets.paper import PaperPredictionMarket
from ai_trader.replay.recorder import (
    ReplayBookSource,
    ReplayError,
    ReplayEventSource,
    Tape,
    TapeRecorder,
    replay_clock,
)
from ai_trader.survival.config import SurvivalConfig
from ai_trader.survival.engine import SurvivalEngine
from ai_trader.survival.latch import TerminalLatch
from ai_trader.survival.policy import PolicyGuardian

from tests.test_agent_cycle import StubAnalyst, _book, _contract, _observation, _release


# ==========================================================================
# Calibration
# ==========================================================================
def test_brier_score_rewards_confident_correctness() -> None:
    assert brier_score([(1.0, 1)]) == 0.0
    assert brier_score([(0.0, 1)]) == 1.0
    assert brier_score([(0.5, 1)]) == 0.25
    assert brier_score([]) is None


def test_a_perfectly_calibrated_forecaster_shows_small_gaps() -> None:
    outcomes = []
    for bucket in range(10):
        p = bucket / 10.0 + 0.05
        hits = round(p * 100)
        for i in range(100):
            outcomes.append(
                {"predicted_probability": p, "resolved_outcome": 1 if i < hits else 0}
            )
    report = build_report(outcomes)
    assert report.count == 1000
    assert report.expected_calibration_error < 0.02
    assert report.skill_score > 0
    assert "Calibrated with positive skill" in report.verdict


def test_an_overconfident_forecaster_is_called_out() -> None:
    """Always saying 95% while being right half the time must not look good."""
    outcomes = [
        {"predicted_probability": 0.95, "resolved_outcome": i % 2} for i in range(200)
    ]
    report = build_report(outcomes)
    assert report.brier > 0.4
    assert report.skill_score < 0
    assert "No skill demonstrated" in report.verdict


def test_a_small_sample_refuses_to_draw_a_conclusion() -> None:
    outcomes = [{"predicted_probability": 0.9, "resolved_outcome": 1} for _ in range(5)]
    report = build_report(outcomes)
    assert "too few to judge" in report.verdict


def test_no_outcomes_means_calibration_is_unknown_not_perfect() -> None:
    report = build_report([])
    assert report.count == 0
    assert report.brier is None
    assert "unknown" in report.verdict


def test_buckets_capture_where_the_model_is_wrong() -> None:
    outcomes = (
        [{"predicted_probability": 0.9, "resolved_outcome": 0} for _ in range(40)]
        + [{"predicted_probability": 0.2, "resolved_outcome": 0} for _ in range(40)]
    )
    report = build_report(outcomes)
    high = [b for b in report.buckets if b.lower == 0.9][0]
    low = [b for b in report.buckets if b.lower == 0.2][0]
    assert high.gap < -0.8, "predicted 90%, happened never"
    assert abs(low.gap) < 0.25


# ==========================================================================
# Performance from persisted data
# ==========================================================================
def _store(tmp_path) -> RecordStore:
    clock = FrozenClock("2026-05-01T00:00:00+00:00")
    return RecordStore(initialise_database(tmp_path / "p.db"), clock=clock)


def test_performance_is_computed_from_the_database_not_from_memory(tmp_path) -> None:
    store = _store(tmp_path)
    for i, pnl in enumerate([5.0, -2.0, 3.0, -1.0]):
        decision_id = store.record_decision(
            {"cycle_id": "c", "final_action": "BUY", "executed": True, "net_edge": 0.1}
        )
        store.upsert_contract_position(
            {
                "position_id": f"POS-{i}", "decision_id": decision_id, "ticker": f"T{i}",
                "event_key": "E", "contracts": 10, "average_price": 0.5,
                "premium_base": 5.0, "fees_base": 0.1, "max_loss_base": 5.1,
                "max_gain_base": 4.9, "open": False, "resolved_outcome": 1 if pnl > 0 else 0,
                "settlement_base": 10.0 if pnl > 0 else 0.0, "realised_pnl_base": pnl,
                "closed_at": f"2026-05-0{i + 1}T00:00:00+00:00",
            }
        )
        store.record_outcome(
            decision_id=decision_id, predicted_probability=0.7,
            resolved_outcome=1 if pnl > 0 else 0,
            resolved_at=f"2026-05-0{i + 1}T00:00:00+00:00",
            realised_pnl_base=pnl, predicted_edge=0.1,
        )
    summary = compute_performance(store, starting_equity=100.0, equity=105.0)
    assert summary["trades"] == 4
    assert summary["wins"] == 2
    assert summary["losses"] == 2
    assert summary["win_rate"] == 0.5
    assert summary["gross_pnl"] == 5.0
    assert summary["expectancy"] == pytest.approx(1.25)
    assert summary["profit_factor"] == pytest.approx(8 / 3)
    assert summary["max_drawdown_pct"] > 0
    assert summary["calibration"]["count"] == 4


def test_performance_states_plainly_when_there_is_no_evidence(tmp_path) -> None:
    store = _store(tmp_path)
    summary = compute_performance(store, starting_equity=100.0, equity=100.0)
    assert summary["trades"] == 0
    assert "Nothing here supports any claim about edge" in summary["evidence_note"]
    assert summary["brier"] is None


def test_performance_counts_rejected_opportunities(tmp_path) -> None:
    store = _store(tmp_path)
    for _ in range(7):
        store.record_decision({"cycle_id": "c", "final_action": "HOLD", "executed": False})
    store.record_decision({"cycle_id": "c", "final_action": "BUY", "executed": True})
    summary = compute_performance(store, starting_equity=100.0, equity=100.0)
    assert summary["opportunities_considered"] == 8
    assert summary["opportunities_executed"] == 1
    assert summary["opportunities_rejected"] == 7
    assert summary["conversion_rate"] == pytest.approx(0.125)


def test_performance_reports_net_of_operating_cost(tmp_path) -> None:
    store = _store(tmp_path)
    clock = FrozenClock("2026-05-01T00:00:00+00:00")
    costs = CostLedger(store, clock=clock)
    costs.record_hosting(amount_base=3.0)
    decision_id = store.record_decision({"cycle_id": "c", "final_action": "BUY", "executed": True})
    store.upsert_contract_position(
        {
            "position_id": "P", "decision_id": decision_id, "ticker": "T", "event_key": "E",
            "contracts": 10, "average_price": 0.5, "premium_base": 5.0, "fees_base": 0.0,
            "max_loss_base": 5.0, "max_gain_base": 5.0, "open": False,
            "resolved_outcome": 1, "settlement_base": 10.0, "realised_pnl_base": 5.0,
            "closed_at": "2026-05-02T00:00:00+00:00",
        }
    )
    summary = compute_performance(
        store, starting_equity=100.0, equity=105.0, cost_ledger=costs
    )
    assert summary["gross_pnl"] == 5.0
    assert summary["operating_costs"] == 3.0
    assert summary["net_pnl"] == 2.0
    assert summary["self_sustaining"] is True


# ==========================================================================
# Replay
# ==========================================================================
def _build_cycle(tmp_path, *, event_source, market, clock, starting_cash=100.0):
    store = RecordStore(initialise_database(tmp_path / "r.db"), clock=clock)
    latch = TerminalLatch(tmp_path / "TERMINAL", store=store, clock=clock)
    survival = SurvivalEngine(
        SurvivalConfig(starting_equity=starting_cash), latch=latch, store=store, clock=clock
    )
    ledger = ContractLedger(starting_cash=starting_cash, base_currency="GBP")
    cycle = AgentCycle(
        event_source=event_source,
        market=market,
        ledger=ledger,
        risk=ContractRiskEngine(),
        guardian=PolicyGuardian(survival),
        survival=survival,
        store=store,
        opportunities=OpportunityEngine(),
        analyst=StubAnalyst(proceed=True),
        clock=clock,
        fx_rate=0.80,
        quote_currency="USD",
    )
    return cycle, store, ledger


def _record_tape(tmp_path) -> Tape:
    recorder = TapeRecorder(started_at="2026-04-14T14:00:00+00:00", notes="test tape")
    contract = _contract()
    recorder.record_contract(contract)
    recorder.record_release(_release())
    recorder.record_observation(_observation())
    recorder.record_book(_book())
    recorder.record_fx("USD", 0.80)
    return recorder.tape


def test_a_tape_round_trips_through_disk(tmp_path) -> None:
    tape = _record_tape(tmp_path)
    path = tape.save(tmp_path / "tape.json")
    loaded = Tape.load(path)
    assert loaded.to_dict() == tape.to_dict()


def test_a_tape_from_a_different_version_is_refused(tmp_path) -> None:
    path = tmp_path / "old.json"
    path.write_text('{"version": 99}', encoding="utf-8")
    with pytest.raises(ReplayError, match="version"):
        Tape.load(path)


def test_replay_reproduces_the_live_run_exactly(tmp_path) -> None:
    """The point of a replay: identical inputs through identical code."""
    tape = _record_tape(tmp_path)
    clock = replay_clock(tape)

    def run(directory):
        source = ReplayEventSource(tape)
        books = ReplayBookSource(tape)
        market = PaperPredictionMarket(clock=clock, book_source=books)
        market.register(_contract())
        cycle, store, ledger = _build_cycle(
            directory, event_source=source, market=market, clock=replay_clock(tape)
        )
        report = cycle.run()
        return report, ledger, store

    first_report, first_ledger, first_store = run(tmp_path / "a")
    second_report, second_ledger, second_store = run(tmp_path / "b")

    assert first_report.traded == second_report.traded
    assert [p.to_dict() for p in first_ledger.open_positions()] == [
        p.to_dict() for p in second_ledger.open_positions()
    ]
    assert first_ledger.cash == second_ledger.cash

    def normalise(rows):
        return [
            {k: v for k, v in row.items() if k not in {"id", "created_at"}}
            for row in rows
        ]

    assert normalise(first_store.list_decisions(50)) == normalise(
        second_store.list_decisions(50)
    )


def test_replay_never_falls_back_to_a_live_source(tmp_path) -> None:
    tape = _record_tape(tmp_path)
    tape.observations.clear()
    source = ReplayEventSource(tape)
    with pytest.raises(ReplayError, match="will not fall back"):
        source.observe(_release())


def test_replay_sources_hold_no_http_client() -> None:
    """Structural: a replay cannot make a network call because it has no client."""
    tape = Tape(started_at="2026-01-01T00:00:00+00:00")
    source = ReplayEventSource(tape)
    for attribute in ("_http", "http_client", "session", "client"):
        assert not hasattr(source, attribute)
    assert source.health()["live"] is False


def test_replay_book_source_serves_in_recorded_order(tmp_path) -> None:
    tape = _record_tape(tmp_path)
    tape.books["CPI-ABOVE-30"].append(_book(ask=0.55).to_dict())
    books = ReplayBookSource(tape)
    first = books("CPI-ABOVE-30")
    second = books("CPI-ABOVE-30")
    assert first.best_ask == 0.70
    assert second.best_ask == 0.55
    books.reset()
    assert books("CPI-ABOVE-30").best_ask == 0.70


# ==========================================================================
# Benchmarks
# ==========================================================================
def _case(i, *, p_model, ask, outcome, resolved="2026-01-01", fee=ZeroFeeModel()) -> BenchmarkCase:
    return BenchmarkCase(
        case_id=f"C{i}",
        event_key=f"E{i}",
        ticker=f"T{i}",
        resolved_at=f"{resolved}T00:00:00+00:00",
        model_probability=p_model,
        market_ask=ask,
        market_bid=round(ask - 0.01, 2),
        outcome=outcome,
        depth=1000,
        fee_model=fee,
    )


def test_the_no_trade_baseline_is_exactly_flat() -> None:
    cases = [_case(i, p_model=0.9, ask=0.5, outcome=i % 2) for i in range(50)]
    result = run_strategy(cases, no_trade, name="NO_TRADE", split="all")
    assert result.trades == 0
    assert result.net_pnl == 0.0
    assert result.return_pct == 0.0
    assert result.ending_equity == 100.0


def test_a_genuinely_predictive_model_beats_no_trade() -> None:
    # The model is right 90% of the time and the market is priced at 50/50.
    cases = [
        _case(i, p_model=0.9, ask=0.5, outcome=1 if i % 10 else 0) for i in range(100)
    ]
    result = run_strategy(cases, deterministic_edge(), name="DETERMINISTIC", split="all")
    assert result.trades > 0
    assert result.net_pnl > 0


def test_a_useless_model_loses_and_the_benchmark_says_so() -> None:
    """If the strategy loses, that must be reported, not massaged."""
    cases = [_case(i, p_model=0.9, ask=0.8, outcome=0) for i in range(60)]
    report = run_benchmark(cases, min_edge=0.0)
    agent = report["results"]["all"]["AGENT"]
    assert agent["net_pnl"] < 0
    assert report["beats_all"] is False
    assert "No edge is demonstrated" in report["verdict"]


def test_fees_can_turn_a_small_edge_into_a_loss() -> None:
    """The reason the fee model is not optional."""
    cases = [
        _case(i, p_model=0.54, ask=0.50, outcome=1 if i % 100 < 54 else 0, fee=STANDARD_FEES)
        for i in range(100)
    ]
    free = run_strategy(
        [_case(i, p_model=c.model_probability, ask=c.market_ask, outcome=c.outcome)
         for i, c in enumerate(cases)],
        deterministic_edge(min_edge=0.0), name="free", split="all",
    )
    charged = run_strategy(cases, deterministic_edge(min_edge=0.0), name="charged", split="all")
    assert charged.fees > 0
    assert charged.net_pnl < free.net_pnl


def test_out_of_sample_is_a_later_period_not_another_seed() -> None:
    cases = (
        [_case(i, p_model=0.9, ask=0.5, outcome=1, resolved="2026-01-05") for i in range(40)]
        + [_case(100 + i, p_model=0.9, ask=0.5, outcome=0, resolved="2026-06-05") for i in range(40)]
    )
    development, out_of_sample = split_by_time(cases, cutoff="2026-03-01T00:00:00+00:00")
    assert len(development) == 40 and len(out_of_sample) == 40
    assert all(c.resolved_at < "2026-03-01T00:00:00+00:00" for c in development)

    report = run_benchmark(cases, cutoff="2026-03-01T00:00:00+00:00", min_edge=0.0)
    assert report["headline_split"] == "out_of_sample"
    # Looked great in development, loses out of sample. That is the whole point.
    assert report["results"]["development"]["AGENT"]["net_pnl"] > 0
    assert report["results"]["out_of_sample"]["AGENT"]["net_pnl"] < 0
    assert report["beats_all"] is False


def test_a_small_sample_refuses_to_claim_anything() -> None:
    cases = [_case(i, p_model=0.9, ask=0.5, outcome=1) for i in range(10)]
    report = run_benchmark(cases, min_edge=0.0)
    assert "too small a sample" in report["verdict"]


def test_an_analyst_veto_reduces_trades_and_is_isolated() -> None:
    """Comparing DETERMINISTIC with AGENT is what measures the analyst's value."""
    cases = [_case(i, p_model=0.9, ask=0.5, outcome=1) for i in range(50)]
    without = run_strategy(cases, deterministic_edge(), name="d", split="all")
    with_veto = run_strategy(
        cases, agent_strategy(analyst=lambda case: case.case_id.endswith("0")),
        name="a", split="all",
    )
    assert with_veto.trades < without.trades


def test_every_strategy_faces_identical_conditions() -> None:
    cases = [_case(i, p_model=0.9, ask=0.5, outcome=i % 2) for i in range(60)]
    report = run_benchmark(cases, min_edge=0.0)
    rows = report["results"]["all"]
    assert {r["opportunities"] for r in rows.values()} == {60}
    assert {r["starting_equity"] for r in rows.values()} == {100.0}
    assert report["live"] is False
