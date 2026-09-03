"""The audit trail must answer six questions from the database alone."""

from __future__ import annotations

import json

import pytest

from ai_trader.clock import FrozenClock
from ai_trader.db.records import RecordStore
from ai_trader.db.schema import initialise_database


@pytest.fixture()
def store(tmp_path) -> RecordStore:
    conn = initialise_database(tmp_path / "agent.db")
    return RecordStore(conn, clock=FrozenClock("2026-03-02T09:00:00+00:00"))


def test_agent_schema_tables_exist(store: RecordStore) -> None:
    names = {
        row["name"]
        for row in store.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    for required in (
        "agent_life",
        "survival_transitions",
        "milestones",
        "markets",
        "market_snapshots",
        "official_data",
        "opportunities",
        "decisions",
        "decision_inputs",
        "contract_orders",
        "contract_fills",
        "contract_positions",
        "outcomes",
        "costs",
        "strategy_performance",
    ):
        assert required in names, required


def test_a_hold_is_recorded_as_a_decision(store: RecordStore) -> None:
    decision_id = store.record_decision(
        {
            "cycle_id": "c1",
            "kind": "binary",
            "ticker": "CPI-26MAR",
            "final_action": "HOLD",
            "proposed_action": "BUY",
            "policy_action": "HOLD",
            "policy_reason": "Edge below the survival-state threshold.",
            "risk_approved": False,
            "risk_reason": "Not reached: policy downgraded first.",
            "executed": False,
            "stage": "policy",
        }
    )
    row = store.decision(decision_id)
    assert row is not None
    assert row["final_action"] == "HOLD"
    assert row["executed"] == 0
    assert "survival-state threshold" in row["policy_reason"]


def test_rejections_are_recorded_with_their_reason(store: RecordStore) -> None:
    store.record_opportunity(
        {
            "cycle_id": "c1",
            "ticker": "CPI-26MAR-Y",
            "model_probability": 0.62,
            "market_probability": 0.60,
            "net_edge": 0.004,
            "selected": False,
            "reject_reason": "Net edge 0.004 below the 0.05 minimum.",
        }
    )
    rows = store.list_opportunities()
    assert len(rows) == 1
    assert rows[0]["selected"] == 0
    assert "below the 0.05 minimum" in rows[0]["reject_reason"]


def test_a_full_decision_answers_all_six_questions(store: RecordStore) -> None:
    market_id = store.upsert_market(
        venue="paper",
        ticker="CPI-26MAR-ABOVE",
        kind="binary",
        question="Will March 2026 CPI YoY be above 3.0%?",
        event_key="CPI:2026-03",
        resolution_source="BLS",
        settlement_rules="Resolves YES if the BLS headline CPI YoY exceeds 3.0%.",
    )
    official_id = store.record_official_data(
        series_key="BLS:CUUR0000SA0",
        release_key="CPI:2026-03",
        source="BLS",
        observed_at="2026-03-02T13:30:00+00:00",
        status="verified",
        value=3.4,
        unit="percent_yoy",
        verified=True,
        verification_method="two independent reads agreed",
        second_read=3.4,
    )
    opportunity_id = store.record_opportunity(
        {
            "cycle_id": "c9",
            "market_id": market_id,
            "ticker": "CPI-26MAR-ABOVE",
            "event_key": "CPI:2026-03",
            "official_data_id": official_id,
            "model_probability": 0.94,
            "market_probability": 0.71,
            "net_edge": 0.19,
            "selected": True,
        }
    )
    decision_id = store.record_decision(
        {
            "cycle_id": "c9",
            "kind": "binary",
            "ticker": "CPI-26MAR-ABOVE",
            "market_id": market_id,
            "event_key": "CPI:2026-03",
            "opportunity_id": opportunity_id,
            "model_probability": 0.94,
            "market_probability": 0.71,
            "gross_edge": 0.23,
            "net_edge": 0.19,
            "fees": 0.02,
            "spread": 0.02,
            "liquidity": 240.0,
            "ai_model": "grok-4.6",
            "ai_action": "PROCEED",
            "ai_confidence": 0.7,
            "ai_bull": "The released number is unambiguous and above the strike.",
            "ai_bear": "Resolution uses the seasonally adjusted series, not the headline.",
            "ai_invalidators": ["revision", "series mismatch"],
            "ai_validated": True,
            "proposed_action": "BUY",
            "policy_action": "BUY",
            "policy_reason": "Edge clears the HEALTHY threshold.",
            "survival_state": "HEALTHY",
            "risk_multiplier": 1.0,
            "risk_approved": True,
            "risk_reason": "Sized within the survival budget.",
            "final_action": "BUY",
            "executed": True,
            "order_ref": "CON-0001",
            "equity_before": 100.0,
        },
        inputs=[
            {"name": "official_value", "kind": "official", "value": 3.4, "source": "BLS"},
            {"name": "orderbook_mid", "kind": "market", "value": 0.71, "source": "paper"},
            {"name": "equity", "kind": "account", "value": 100.0},
        ],
    )
    store.record_outcome(
        decision_id=decision_id,
        predicted_probability=0.94,
        resolved_outcome=1,
        resolved_at="2026-03-15T00:00:00+00:00",
        realised_pnl_base=4.10,
        predicted_edge=0.19,
        realised_edge=0.29,
    )

    row = store.decision(decision_id)
    assert row is not None
    # 1. What did the agent know?
    names = {i["name"] for i in row["inputs"]}
    assert names == {"official_value", "orderbook_mid", "equity"}
    # 2. What did it believe?
    assert row["model_probability"] == 0.94
    # 3. What did the deterministic layer compute?
    assert row["net_edge"] == 0.19
    assert row["fees"] == 0.02
    # 4. What did the analyst recommend, and what was the bear case?
    assert row["ai_action"] == "PROCEED"
    assert "seasonally adjusted" in row["ai_bear"]
    assert json.loads(row["ai_invalidators"]) == ["revision", "series mismatch"]
    # 5. Why was it allowed?
    assert row["policy_action"] == "BUY"
    assert row["risk_approved"] == 1
    # 6. What happened next?
    assert row["outcome"]["resolved_outcome"] == 1
    assert row["outcome"]["correct"] == 1
    assert row["outcome"]["brier"] == pytest.approx(0.0036)


def test_brier_score_and_correctness_are_computed_not_supplied(store: RecordStore) -> None:
    confident_and_wrong = store.record_decision({"cycle_id": "c", "final_action": "BUY"})
    store.record_outcome(
        decision_id=confident_and_wrong,
        predicted_probability=0.95,
        resolved_outcome=0,
        resolved_at="2026-03-15T00:00:00+00:00",
    )
    row = store.list_outcomes()[0]
    assert row["brier"] == pytest.approx(0.9025)
    assert row["correct"] == 0


def test_duplicate_event_protection_sees_executed_decisions_only(store: RecordStore) -> None:
    store.record_decision(
        {"cycle_id": "c", "event_key": "CPI:2026-03", "final_action": "HOLD", "executed": False}
    )
    assert store.release_already_traded("CPI:2026-03") is False
    store.record_decision(
        {"cycle_id": "c", "event_key": "CPI:2026-03", "final_action": "BUY", "executed": True}
    )
    assert store.release_already_traded("CPI:2026-03") is True


def test_official_data_upsert_is_idempotent(store: RecordStore) -> None:
    kwargs = dict(
        series_key="BLS:X",
        release_key="R1",
        source="BLS",
        observed_at="2026-03-02T13:30:00+00:00",
        status="pending",
    )
    first = store.record_official_data(**kwargs)
    second = store.record_official_data(**{**kwargs, "status": "verified", "value": 3.4})
    assert first == second
    row = store.latest_official("BLS:X", "R1")
    assert row["status"] == "verified"
    assert row["value"] == 3.4


def test_costs_aggregate_by_category_and_window(store: RecordStore) -> None:
    store.record_cost(category="llm", description="grok call", amount_base=0.012,
                      incurred_at="2026-03-01T10:00:00+00:00")
    store.record_cost(category="llm", description="grok call", amount_base=0.008,
                      incurred_at="2026-03-02T10:00:00+00:00")
    store.record_cost(category="hosting", description="daily", amount_base=0.10,
                      incurred_at="2026-03-02T00:00:00+00:00")
    assert store.total_costs() == pytest.approx(0.12)
    assert store.costs_by_category() == {"llm": pytest.approx(0.02), "hosting": pytest.approx(0.10)}
    assert store.costs_since("2026-03-02T00:00:00+00:00") == pytest.approx(0.108)


def test_order_idempotency_key_prevents_duplicates(store: RecordStore) -> None:
    payload = {
        "order_id": "CON-1",
        "idempotency_key": "cycle1:CPI-26MAR:YES",
        "ticker": "CPI-26MAR",
        "contracts": 3,
        "status": "FILLED",
    }
    store.record_contract_order(payload)
    assert store.order_exists("cycle1:CPI-26MAR:YES") is True
    with pytest.raises(Exception):
        store.record_contract_order({**payload, "order_id": "CON-2"})


def test_exposure_rolls_up_by_event_and_ticker(store: RecordStore) -> None:
    for i, (ticker, event, loss) in enumerate(
        [("A-YES", "CPI:2026-03", 3.0), ("B-YES", "CPI:2026-03", 2.0), ("C-YES", "NFP:2026-03", 4.0)]
    ):
        store.upsert_contract_position(
            {
                "position_id": f"POS-{i}",
                "ticker": ticker,
                "event_key": event,
                "contracts": 5,
                "average_price": 0.6,
                "premium_base": loss,
                "max_loss_base": loss,
                "max_gain_base": 2.0,
            }
        )
    assert store.exposure_by_event("CPI:2026-03") == 5.0
    assert store.exposure_by_ticker("A-YES") == 3.0
    assert store.total_open_exposure() == 9.0


def test_milestones_are_recorded_once(store: RecordStore) -> None:
    assert store.record_milestone(key="equity_200", label="First growth", equity=200.0) is True
    assert store.record_milestone(key="equity_200", label="First growth", equity=210.0) is False
    assert len(store.list_milestones()) == 1
