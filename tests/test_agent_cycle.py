"""One event family, end to end, offline and deterministic.

calendar -> release -> verification -> probability -> edge -> ranking ->
analyst -> guardian -> risk -> paper fill -> ledger -> settlement -> outcome.

The point of these tests is that each stage can only refuse more firmly than
the one before it, and that the whole run is reconstructable from the database.
"""

from __future__ import annotations

import pytest

from ai_trader.agent.cycle import AgentCycle
from ai_trader.clock import FrozenClock
from ai_trader.contracts.ledger import ContractLedger
from ai_trader.contracts.risk import ContractRiskEngine, ContractRiskLimits
from ai_trader.costs.ledger import CostLedger
from ai_trader.db.records import RecordStore
from ai_trader.db.schema import initialise_database
from ai_trader.edge.opportunity import OpportunityEngine, OpportunityFilters
from ai_trader.events.base import ReleaseObservation, ReleaseStatus, ScheduledRelease
from ai_trader.events.bls import FixtureEventSource
from ai_trader.markets.base import BookLevel, Contract, OrderBook
from ai_trader.markets.fees import ZeroFeeModel
from ai_trader.markets.paper import PaperPredictionMarket
from ai_trader.survival.config import SurvivalConfig, SurvivalState
from ai_trader.survival.engine import SurvivalEngine
from ai_trader.survival.latch import TerminalLatch
from ai_trader.survival.policy import PolicyGuardian

NOW = "2026-04-14T14:00:00+00:00"
RESOLUTION = "2026-04-13T00:00:00+00:00"


class StubAnalyst:
    """Deterministic stand-in for Grok. Records what it was asked."""

    def __init__(self, *, proceed: bool = True, model: str = "stub") -> None:
        self._proceed = proceed
        self.model = model
        self.calls: list[str] = []

    def health(self):
        return {"name": "stub-analyst", "ready": True, "notes": "Deterministic test double."}

    def review(self, *, contract, observation, estimate, edge, book, survival_state):
        self.calls.append(contract.ticker)
        from ai_trader.ai.skeptic import SkepticReview

        return SkepticReview(
            recommendation="PROCEED" if self._proceed else "PASS",
            confidence=0.7,
            bull_case="The published number clears the strike.",
            bear_case="A revision could move it back below.",
            invalidators=["revision"],
            data_concerns="",
            ok=True,
            model=self.model,
        )


def _contract(ticker="CPI-ABOVE-30", strike=3.0, **kwargs) -> Contract:
    defaults = dict(
        ticker=ticker,
        question=f"Will CPI YoY exceed {strike}%?",
        event_key="CPI:2026-03",
        resolution_source="BLS",
        resolution_time=RESOLUTION,
        settlement_rules=f"Resolves YES if CPI YoY exceeds {strike}.",
        strike=strike,
        comparison="above",
        fee_model=ZeroFeeModel(),
        quote_currency="USD",
    )
    defaults.update(kwargs)
    return Contract(**defaults)


def _book(ticker="CPI-ABOVE-30", ask=0.70, depth=500) -> OrderBook:
    return OrderBook(
        ticker=ticker,
        observed_at=NOW,
        bids=(BookLevel(round(ask - 0.01, 2), depth),),
        asks=(BookLevel(ask, depth),),
    )


def _observation(status=ReleaseStatus.VERIFIED, yoy=3.4) -> ReleaseObservation:
    return ReleaseObservation(
        release_key="CPI:2026-03",
        series_key="BLS:CUUR0000SA0",
        source="BLS",
        status=status,
        observed_at="2026-04-12T13:35:00+00:00",
        value=320.0,
        previous_value=309.5,
        yoy_change=yoy,
        verification_method="two independent reads agreed",
        published_at="2026-04-12T13:30:00+00:00",
    )


def _release() -> ScheduledRelease:
    return ScheduledRelease(
        release_key="CPI:2026-03",
        series_key="BLS:CUUR0000SA0",
        source="BLS",
        label="US CPI 2026-03",
        scheduled_at="2026-04-12T13:30:00+00:00",
        period="2026-03",
    )


def build(
    tmp_path,
    *,
    contracts=None,
    observation=None,
    analyst=None,
    starting_cash=100.0,
    filters=None,
    survival_config=None,
    now=NOW,
):
    clock = FrozenClock(now)
    store = RecordStore(initialise_database(tmp_path / "agent.db"), clock=clock)
    latch = TerminalLatch(tmp_path / "TERMINAL", store=store, clock=clock)
    survival = SurvivalEngine(
        survival_config or SurvivalConfig(starting_equity=starting_cash),
        latch=latch, store=store, clock=clock,
    )
    guardian = PolicyGuardian(survival)
    ledger = ContractLedger(starting_cash=starting_cash, base_currency="GBP")
    market = PaperPredictionMarket(clock=clock)
    for contract in contracts or [_contract()]:
        market.register(contract)
        market.set_book(_book(contract.ticker))
    source = FixtureEventSource(clock=clock)
    source.add(_release(), observation if observation is not None else _observation())
    cost_ledger = CostLedger(store, clock=clock, base_currency="GBP")
    cycle = AgentCycle(
        event_source=source,
        market=market,
        ledger=ledger,
        risk=ContractRiskEngine(ContractRiskLimits()),
        guardian=guardian,
        survival=survival,
        store=store,
        opportunities=OpportunityEngine(filters=filters or OpportunityFilters()),
        analyst=analyst,
        cost_ledger=cost_ledger,
        clock=clock,
        fx_rate=0.80,
        quote_currency="USD",
    )
    return cycle, store, ledger, survival, market, clock


# ==========================================================================
# Happy path
# ==========================================================================
def test_a_verified_release_produces_a_trade(tmp_path) -> None:
    analyst = StubAnalyst(proceed=True)
    cycle, store, ledger, _survival, _market, _clock = build(tmp_path, analyst=analyst)
    report = cycle.run()

    assert report.traded == 1
    assert analyst.calls == ["CPI-ABOVE-30"]
    assert ledger.open_positions()
    position = ledger.open_positions()[0]
    assert position.contracts > 0
    # The whole premium is the risk, and it stays inside the 10% cap.
    assert position.max_loss_base <= 10.01
    assert ledger.cash < 100.0


def test_the_whole_decision_is_reconstructable_from_the_database(tmp_path) -> None:
    cycle, store, _ledger, _survival, _market, _clock = build(
        tmp_path, analyst=StubAnalyst(proceed=True)
    )
    report = cycle.run()
    executed = [d for d in report.decisions if d["executed"]]
    assert executed

    row = store.decision(executed[0]["id"])
    assert row["final_action"] == "BUY"
    assert row["model_probability"] == pytest.approx(0.98, abs=0.05)
    assert row["market_probability"] == pytest.approx(0.70)
    assert row["net_edge"] > 0
    assert row["ai_action"] == "PROCEED"
    assert "revision" in row["ai_bear"]
    assert row["policy_action"] == "BUY"
    assert row["risk_approved"] == 1
    assert row["survival_state"] == "HEALTHY"
    names = {i["name"] for i in row["inputs"]}
    assert {"official_data", "orderbook", "probability_estimate", "edge", "account"} <= names


def test_holds_and_rejections_are_recorded_too(tmp_path) -> None:
    """A cycle that considers several contracts and trades none leaves reasons."""
    contracts = [
        _contract("A", strike=3.0),
        _contract("B", strike=9.0),   # published 3.4 misses -> low probability
        _contract("C", strike=3.0),
    ]
    cycle, store, _ledger, _survival, market, _clock = build(
        tmp_path, contracts=contracts, analyst=StubAnalyst(proceed=True)
    )
    # Price C out of contention.
    market.set_book(_book("C", ask=0.97))
    report = cycle.run()

    assert report.contracts_considered == 3
    opportunities = store.list_opportunities()
    assert len(opportunities) == 3
    rejected = [o for o in opportunities if not o["selected"]]
    assert rejected, "at least one candidate must be filtered out with a reason"
    assert all(o["reject_reason"] for o in rejected)

    decisions = store.list_decisions(limit=50)
    holds = [d for d in decisions if d["final_action"] == "HOLD"]
    assert holds
    assert all(d["notes"] for d in holds)


# ==========================================================================
# Every gate can stop the trade
# ==========================================================================
def test_unverified_official_data_stops_everything(tmp_path) -> None:
    for status in (ReleaseStatus.PENDING, ReleaseStatus.CONFLICT, ReleaseStatus.UNVERIFIED):
        analyst = StubAnalyst(proceed=True)
        cycle, store, ledger, _s, _m, _c = build(
            tmp_path / status.value, observation=_observation(status=status), analyst=analyst
        )
        report = cycle.run()
        assert report.traded == 0, status
        assert analyst.calls == [], "the analyst is never asked about unverified data"
        assert ledger.open_positions() == []


def test_an_analyst_pass_stops_the_trade(tmp_path) -> None:
    analyst = StubAnalyst(proceed=False)
    cycle, store, ledger, _s, _m, _c = build(tmp_path, analyst=analyst)
    report = cycle.run()
    assert report.traded == 0
    assert analyst.calls == ["CPI-ABOVE-30"]
    holds = [d for d in report.decisions if d["stage"] == "analyst"]
    assert holds and holds[0]["final_action"] == "HOLD"


def test_a_missing_analyst_does_not_default_to_yes(tmp_path) -> None:
    """No analyst configured must not mean 'approved'.

    An adversarial review is part of the strategy. Losing it is a degraded
    system, not permission to trade unchallenged.
    """
    cycle, _store, ledger, _s, _m, _c = build(tmp_path, analyst=None)
    report = cycle.run()
    assert report.analyst_calls == 0
    assert report.traded == 0
    assert ledger.open_positions() == []
    holds = [d for d in report.decisions if d["stage"] == "analyst"]
    assert holds and "unchallenged" in holds[0]["notes"]


def test_the_analyst_requirement_can_be_lifted_deliberately(tmp_path) -> None:
    """Only so the benchmark can measure what the analyst actually contributes."""
    cycle, _store, ledger, _s, _m, _c = build(tmp_path, analyst=None)
    cycle.require_analyst = False
    report = cycle.run()
    assert report.traded == 1
    assert report.analyst_calls == 0


def test_a_thin_edge_never_reaches_the_analyst(tmp_path) -> None:
    analyst = StubAnalyst(proceed=True)
    cycle, _store, _ledger, _s, market, _c = build(tmp_path, analyst=analyst)
    market.set_book(_book(ask=0.96))
    report = cycle.run()
    assert report.traded == 0
    assert analyst.calls == [], "deterministic filtering runs before any model spend"


def test_a_terminated_agent_runs_no_cycle(tmp_path) -> None:
    cycle, store, ledger, survival, _m, _c = build(tmp_path, analyst=StubAnalyst(proceed=True))
    survival.observe(10.0)
    assert survival.state is SurvivalState.TERMINAL
    report = cycle.run()
    assert report.terminated is True
    assert report.traded == 0
    assert report.decisions[0]["stage"] == "terminal"
    assert ledger.open_positions() == []


def test_survival_state_tightens_what_gets_through(tmp_path) -> None:
    """The same opportunity is taken while healthy and refused while critical."""
    healthy_cycle, _s1, healthy_ledger, _sv1, _m1, _c1 = build(
        tmp_path / "healthy", analyst=StubAnalyst(proceed=True)
    )
    assert healthy_cycle.run().traded == 1

    critical_cycle, _s2, _l2, survival, _m2, _c2 = build(
        tmp_path / "critical", analyst=StubAnalyst(proceed=True), starting_cash=100.0
    )
    # Drop equity into CRITICAL, where the edge bar is 20 points.
    critical_cycle.ledger.cash = 54.0
    survival.observe(54.0)
    assert survival.state is SurvivalState.CRITICAL
    report = critical_cycle.run()
    assert report.traded == 0


def test_duplicate_event_protection_prevents_a_second_position(tmp_path) -> None:
    cycle, store, _ledger, _s, _m, _c = build(tmp_path, analyst=StubAnalyst(proceed=True))
    assert cycle.run().traded == 1
    # Second cycle on the same release must not stack another position.
    second = cycle.run()
    assert second.traded == 0


# ==========================================================================
# Settlement and calibration
# ==========================================================================
def test_a_resolved_position_settles_and_records_an_outcome(tmp_path) -> None:
    cycle, store, ledger, _s, _m, clock = build(
        tmp_path, analyst=StubAnalyst(proceed=True), now="2026-04-12T14:00:00+00:00"
    )
    # Make the contract resolvable in the future, trade, then move past it.
    report = cycle.run()
    assert report.traded == 1
    position = ledger.open_positions()[0]

    clock.set("2026-04-20T00:00:00+00:00")
    settled_report = cycle.run()
    assert settled_report.settlements, "the position should have resolved"
    assert not ledger.open_positions()

    outcomes = store.list_outcomes()
    assert len(outcomes) == 1
    outcome = outcomes[0]
    assert outcome["resolved_outcome"] == 1, "CPI 3.4 is above the 3.0 strike"
    assert outcome["correct"] == 1
    assert outcome["brier"] < 0.05
    assert outcome["realised_pnl_base"] is not None
    # 10 contracts settling at $1 each, converted at 0.80.
    assert ledger.equity() > 100.0


def test_a_losing_resolution_takes_the_premium_and_is_recorded(tmp_path) -> None:
    contract = _contract("CPI-ABOVE-30", strike=3.0)
    cycle, store, ledger, _s, market, clock = build(
        tmp_path, contracts=[contract], analyst=StubAnalyst(proceed=True),
        now="2026-04-12T14:00:00+00:00",
    )
    cycle.run()
    opened = ledger.open_positions()[0]
    premium = opened.max_loss_base

    # The number is revised below the strike before resolution.
    cycle.event_source._observations["CPI:2026-03"] = _observation(yoy=2.1)
    clock.set("2026-04-20T00:00:00+00:00")
    cycle.run()

    assert not ledger.open_positions()
    closed = ledger.closed[0]
    assert closed.resolved_outcome == 0
    assert closed.settlement_base == 0.0
    assert closed.realised_pnl_base == pytest.approx(-premium)
    outcome = store.list_outcomes()[0]
    assert outcome["correct"] == 0
    assert outcome["brier"] > 0.9, "confidently wrong scores badly, as it should"


def test_settlement_fees_are_recorded_as_operating_cost(tmp_path) -> None:
    from ai_trader.markets.fees import BinaryTradeFeeModel

    contract = _contract(fee_model=BinaryTradeFeeModel(multiplier=0.07))
    cycle, store, _ledger, _s, _m, clock = build(
        tmp_path, contracts=[contract], analyst=StubAnalyst(proceed=True),
        now="2026-04-12T14:00:00+00:00",
    )
    cycle.run()
    costs = store.costs_by_category()
    assert costs.get("fees", 0) > 0, "the entry fee is an operating cost"


# ==========================================================================
# Robustness
# ==========================================================================
def test_a_broken_book_does_not_stop_the_cycle(tmp_path) -> None:
    contracts = [_contract("GOOD"), _contract("BROKEN")]
    cycle, _store, ledger, _s, market, _c = build(
        tmp_path, contracts=contracts, analyst=StubAnalyst(proceed=True)
    )
    market._books.pop("BROKEN", None)
    report = cycle.run()
    assert report.contracts_considered == 2
    assert report.traded == 1
    holds = [d for d in report.decisions if d["ticker"] == "BROKEN"]
    assert holds and "No order book" in holds[0]["notes"]


def test_book_depth_limits_the_position(tmp_path) -> None:
    """A cheap contract could be sized far larger; the book is what stops it."""
    cycle, _store, ledger, _s, market, _c = build(tmp_path, analyst=StubAnalyst(proceed=True))
    # 8 contracts clears the 5-contract liquidity floor, and at 20c the risk
    # budget would otherwise allow around 60.
    market.set_book(_book(ask=0.20, depth=8))
    cycle.run()
    assert ledger.open_positions()[0].contracts == 8


def test_a_book_below_the_liquidity_floor_is_not_traded_at_all(tmp_path) -> None:
    cycle, _store, ledger, _s, market, _c = build(tmp_path, analyst=StubAnalyst(proceed=True))
    market.set_book(_book(ask=0.20, depth=3))
    report = cycle.run()
    assert report.traded == 0
    assert ledger.open_positions() == []
    holds = [d for d in report.decisions if d["final_action"] == "HOLD"]
    assert any("below the 5 minimum" in (d["notes"] or "") for d in holds)


def test_every_cycle_reports_equity_before_and_after(tmp_path) -> None:
    cycle, _store, _ledger, _s, _m, _c = build(tmp_path, analyst=StubAnalyst(proceed=True))
    report = cycle.run()
    assert report.equity_before == 100.0
    assert report.equity_after == pytest.approx(report.equity_before, abs=0.01)
    assert report.to_dict()["live"] is False
