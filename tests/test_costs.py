"""Cost accounting and runway. Costs are reported, never acted on."""

from __future__ import annotations

import pytest

from ai_trader.clock import FrozenClock
from ai_trader.costs.ledger import XAI_PRICES, CostLedger
from ai_trader.db.records import RecordStore
from ai_trader.db.schema import initialise_database


@pytest.fixture()
def ledger(tmp_path) -> CostLedger:
    clock = FrozenClock("2026-03-08T12:00:00+00:00")
    store = RecordStore(initialise_database(tmp_path / "a.db"), clock=clock)
    return CostLedger(store, clock=clock, hosting_per_day=0.05, fx_usd_to_base=0.80)


def test_llm_cost_uses_the_published_token_price(ledger: CostLedger) -> None:
    price = XAI_PRICES["grok-4.6"]
    amount = ledger.record_llm_call(model="grok-4.6", input_tokens=10_000, output_tokens=2_000)
    expected_usd = 10_000 / 1e6 * price.input_per_million_usd + 2_000 / 1e6 * price.output_per_million_usd
    assert amount == pytest.approx(expected_usd * 0.80, rel=1e-9)
    row = ledger.store.list_costs()[0]
    assert row["category"] == "llm"
    assert row["units"] == 12_000
    # The rate used is recorded, so an old cost is never silently restated.
    assert "per Mtok" in row["description"]


def test_costs_roll_up_by_category(ledger: CostLedger) -> None:
    ledger.record_llm_call(model="grok-4.6", input_tokens=1_000_000, output_tokens=0)
    ledger.record_hosting(amount_base=1.50)
    ledger.record_fee(amount_base=0.25, description="contract fee")
    ledger.record_data(amount_base=0.10, description="release feed")
    by_category = ledger.by_category()
    assert set(by_category) == {"llm", "hosting", "fees", "data"}
    assert ledger.total() == pytest.approx(
        round(3.00 * 0.80, 2) + 1.50 + 0.25 + 0.10, abs=0.01
    )


def test_daily_burn_falls_back_to_the_hosting_rate_when_there_is_no_history(
    ledger: CostLedger,
) -> None:
    assert ledger.daily_burn() == 0.05


def test_daily_burn_uses_observed_spend_when_it_exceeds_hosting(ledger: CostLedger) -> None:
    for _ in range(7):
        ledger.record_hosting(amount_base=1.00)
    assert ledger.daily_burn(lookback_days=7) == 1.00


def test_runway_counts_only_capital_above_the_terminal_threshold(ledger: CostLedger) -> None:
    """Money below the terminal line cannot be spent — reaching it ends the agent."""
    for _ in range(7):
        ledger.record_hosting(amount_base=1.00)
    runway = ledger.runway_days(equity=100.0, terminal_threshold=40.0)
    assert runway == 60.0, "60 spendable pounds at £1/day"


def test_runway_is_zero_at_the_terminal_threshold(ledger: CostLedger) -> None:
    for _ in range(7):
        ledger.record_hosting(amount_base=1.00)
    assert ledger.runway_days(equity=40.0, terminal_threshold=40.0) == 0.0


def test_summary_reports_net_of_cost_profit(ledger: CostLedger) -> None:
    ledger.record_hosting(amount_base=2.00)
    summary = ledger.summary(
        equity=112.0,
        starting_equity=100.0,
        terminal_threshold=40.0,
        realised_pnl=10.0,
        unrealised_pnl=4.0,
    )
    assert summary["gross_trading_pnl"] == 14.0
    assert summary["operating_costs"] == 2.0
    assert summary["net_pnl"] == 12.0
    assert summary["self_sustaining"] is True
    assert summary["spendable_capital"] == 72.0


def test_an_agent_losing_money_is_not_self_sustaining(ledger: CostLedger) -> None:
    ledger.record_hosting(amount_base=5.00)
    summary = ledger.summary(
        equity=90.0, starting_equity=100.0, terminal_threshold=40.0, realised_pnl=-5.0
    )
    assert summary["net_pnl"] == -10.0
    assert summary["self_sustaining"] is False
