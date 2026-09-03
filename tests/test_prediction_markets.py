"""Binary contracts: fees, depth, accounting and the no-stop-loss reality."""

from __future__ import annotations

import pytest

from ai_trader.clock import FrozenClock
from ai_trader.contracts.ledger import ContractLedger, ContractLedgerError
from ai_trader.contracts.risk import ContractRiskEngine, ContractRiskLimits
from ai_trader.markets.base import BookLevel, Contract, MarketDataError, OrderBook
from ai_trader.markets.fees import (
    STANDARD_FEES,
    BinaryTradeFeeModel,
    ZeroFeeModel,
    break_even_edge,
    round_up_to_cent,
)
from ai_trader.markets.paper import PaperPredictionMarket

CLOCK = FrozenClock("2026-03-02T14:00:00+00:00")


def _contract(**kwargs) -> Contract:
    defaults = dict(
        ticker="CPI-26MAR-ABOVE-30",
        question="Will March 2026 CPI YoY print above 3.0%?",
        event_key="CPI:2026-03",
        resolution_source="BLS",
        resolution_time="2026-03-15T00:00:00+00:00",
        settlement_rules="Resolves YES if headline CPI YoY exceeds 3.0%.",
        strike=3.0,
        comparison="above",
    )
    defaults.update(kwargs)
    return Contract(**defaults)


def _book(levels, bids=((0.60, 100),)) -> OrderBook:
    return OrderBook(
        ticker="CPI-26MAR-ABOVE-30",
        observed_at="2026-03-02T14:00:00+00:00",
        bids=tuple(BookLevel(p, c) for p, c in bids),
        asks=tuple(BookLevel(p, c) for p, c in levels),
    )


# ==========================================================================
# Fees
# ==========================================================================
def test_fee_follows_the_published_formula() -> None:
    """roundup_to_cent(0.07 * C * P * (1-P)), per order."""
    # 100 contracts at 50c: 0.07 * 100 * 0.25 = 1.75
    assert STANDARD_FEES.trade_fee(contracts=100, price=0.50) == pytest.approx(1.75)
    # 100 at 10c: 0.07 * 100 * 0.09 = 0.63
    assert STANDARD_FEES.trade_fee(contracts=100, price=0.10) == pytest.approx(0.63)
    # Symmetric about 50c.
    assert STANDARD_FEES.trade_fee(contracts=100, price=0.90) == pytest.approx(0.63)


def test_fee_is_worst_at_the_middle_of_the_price_range() -> None:
    """The cost peaks exactly where most trading happens."""
    fees = [STANDARD_FEES.trade_fee(contracts=100, price=p) for p in (0.05, 0.25, 0.5, 0.75, 0.95)]
    assert fees[2] == max(fees)
    assert fees[0] < fees[1] < fees[2]
    assert fees[4] < fees[3] < fees[2]


def test_fee_rounds_up_to_the_cent_never_down() -> None:
    assert round_up_to_cent(0.0001) == pytest.approx(0.01)
    assert round_up_to_cent(0.011) == pytest.approx(0.02)
    assert round_up_to_cent(0.0) == 0.0
    # One contract at 50c is 0.0175, which rounds up to a whole cent.
    assert STANDARD_FEES.trade_fee(contracts=1, price=0.50) == pytest.approx(0.02)


def test_prices_at_the_extremes_carry_no_trade_fee() -> None:
    assert STANDARD_FEES.trade_fee(contracts=100, price=0.0) == 0.0
    assert STANDARD_FEES.trade_fee(contracts=100, price=1.0) == 0.0


def test_maker_fee_is_cheaper_than_taker() -> None:
    model = BinaryTradeFeeModel(multiplier=0.07, maker_multiplier=0.0025)
    taker = model.trade_fee(contracts=1000, price=0.50)
    maker = model.trade_fee(contracts=1000, price=0.50, maker=True)
    assert maker < taker


def test_break_even_edge_shows_the_bar_a_small_account_must_clear() -> None:
    """At 50c the entry fee alone is 1.75 probability points, before spread."""
    edge = break_even_edge(STANDARD_FEES, price=0.50, spread=0.0)
    assert edge == pytest.approx(0.0175)
    with_spread = break_even_edge(STANDARD_FEES, price=0.50, spread=0.02)
    assert with_spread == pytest.approx(0.0275)


def test_negative_fee_multipliers_are_refused() -> None:
    with pytest.raises(ValueError):
        BinaryTradeFeeModel(multiplier=-0.01)


# ==========================================================================
# Contract semantics
# ==========================================================================
def test_contract_resolves_against_its_own_comparison() -> None:
    above = _contract(comparison="above", strike=3.0)
    assert above.resolves_yes(3.4) is True
    assert above.resolves_yes(3.0) is False
    at_or_above = _contract(comparison="at_or_above", strike=3.0)
    assert at_or_above.resolves_yes(3.0) is True
    below = _contract(comparison="below", strike=3.0)
    assert below.resolves_yes(2.9) is True


def test_a_contract_without_a_strike_cannot_be_resolved_arithmetically() -> None:
    contract = _contract(strike=None)
    with pytest.raises(MarketDataError):
        contract.resolves_yes(3.4)


# ==========================================================================
# Order book and depth
# ==========================================================================
def test_walking_the_book_averages_across_levels() -> None:
    book = _book([(0.62, 10), (0.65, 10), (0.70, 100)])
    filled, average, levels = book.walk_asks(20)
    assert filled == 20
    assert average == pytest.approx(0.635)
    assert [lvl.contracts for lvl in levels] == [10, 10]


def test_a_thin_book_produces_a_partial_fill_not_a_pretend_one() -> None:
    """£100 meeting 30 contracts at the touch is a real constraint."""
    book = _book([(0.62, 30)])
    filled, average, _levels = book.walk_asks(500)
    assert filled == 30
    assert average == pytest.approx(0.62)


def test_market_refuses_a_book_with_no_offers() -> None:
    market = PaperPredictionMarket(clock=CLOCK)
    contract = _contract()
    market.register(contract)
    market.set_book(_book([]))
    with pytest.raises(MarketDataError) as exc:
        market.orderbook(contract.ticker)
    assert exc.value.failure == "no_liquidity"


def test_market_refuses_a_crossed_book() -> None:
    market = PaperPredictionMarket(clock=CLOCK)
    market.register(_contract())
    market.set_book(_book([(0.40, 50)], bids=((0.60, 50),)))
    with pytest.raises(MarketDataError) as exc:
        market.orderbook("CPI-26MAR-ABOVE-30")
    assert exc.value.failure == "malformed"


def test_market_refuses_an_unknown_contract() -> None:
    market = PaperPredictionMarket(clock=CLOCK)
    with pytest.raises(MarketDataError):
        market.rules("NOT-A-TICKER")


# ==========================================================================
# Paper execution
# ==========================================================================
def _market() -> tuple[PaperPredictionMarket, Contract]:
    market = PaperPredictionMarket(clock=CLOCK)
    contract = _contract()
    market.register(contract)
    market.set_book(_book([(0.62, 10), (0.65, 40), (0.70, 200)]))
    return market, contract


def test_paper_fill_crosses_the_spread_and_pays_the_fee() -> None:
    market, contract = _market()
    result = market.submit(ticker=contract.ticker, contracts=20)
    assert result.ok is True
    assert result.status == "FILLED"
    assert result.filled_contracts == 20
    assert result.average_price == pytest.approx(0.635)
    assert result.premium == pytest.approx(12.7)
    assert result.fee > 0
    assert result.live is False


def test_paper_fill_is_partial_when_the_book_runs_out() -> None:
    market = PaperPredictionMarket(clock=CLOCK)
    market.register(_contract())
    market.set_book(_book([(0.62, 7)]))
    result = market.submit(ticker="CPI-26MAR-ABOVE-30", contracts=100)
    assert result.status == "PARTIAL"
    assert result.filled_contracts == 7
    assert "only 7 of 100" in result.reason


def test_a_limit_price_is_never_paid_through() -> None:
    market, contract = _market()
    result = market.submit(ticker=contract.ticker, contracts=100, limit_price=0.65)
    assert result.filled_contracts == 50, "only the levels at or below 0.65"
    assert result.average_price <= 0.65


def test_idempotency_key_returns_the_same_order(tmp_path) -> None:
    market, contract = _market()
    first = market.submit(ticker=contract.ticker, contracts=5, idempotency_key="cycle-1")
    second = market.submit(ticker=contract.ticker, contracts=5, idempotency_key="cycle-1")
    assert first.order_id == second.order_id
    assert len(market.fills()) == 1, "a retry must not double-fill"


def test_no_side_orders_are_refused_in_this_build() -> None:
    market, contract = _market()
    result = market.submit(ticker=contract.ticker, contracts=5, side="NO")
    assert result.ok is False
    assert "YES-side" in result.reason


def test_venue_minimum_and_maximum_are_enforced() -> None:
    market = PaperPredictionMarket(clock=CLOCK)
    market.register(_contract(min_order=5, max_order=50))
    market.set_book(_book([(0.62, 500)]))
    assert market.submit(ticker="CPI-26MAR-ABOVE-30", contracts=1).ok is False
    assert market.submit(ticker="CPI-26MAR-ABOVE-30", contracts=100).ok is False
    assert market.submit(ticker="CPI-26MAR-ABOVE-30", contracts=10).ok is True


def test_the_paper_market_is_never_live() -> None:
    market, _contract = _market()
    assert market.live is False
    assert market.health()["live"] is False
    result = market.submit(ticker="CPI-26MAR-ABOVE-30", contracts=1)
    assert result.to_dict()["live"] is False


# ==========================================================================
# Contract ledger
# ==========================================================================
def _ledger() -> ContractLedger:
    ledger = ContractLedger(starting_cash=100.0, base_currency="GBP")
    ledger.set_fx("USD", 0.80)
    return ledger


def test_binary_accounting_records_premium_max_loss_and_max_gain() -> None:
    ledger = _ledger()
    position = ledger.open_position(
        ticker="T", event_key="E", contracts=20, price=0.60, fee=0.34,
        quote_currency="USD", opened_at="2026-03-02T14:00:00+00:00",
    )
    # 20 * 0.60 = $12 premium = £9.60; fee $0.34 = £0.27.
    assert position.premium_base == pytest.approx(9.60)
    assert position.fees_base == pytest.approx(0.27)
    assert position.max_loss_base == pytest.approx(9.87)
    assert position.max_payout_base == pytest.approx(16.0)
    assert position.max_gain_base == pytest.approx(6.13)
    assert ledger.cash == pytest.approx(90.13)


def test_a_losing_binary_takes_the_whole_premium() -> None:
    """There is no stop loss. This is the case sizing must survive."""
    ledger = _ledger()
    ledger.open_position(
        ticker="T", event_key="E", contracts=20, price=0.60, fee=0.34,
        quote_currency="USD", opened_at="t",
    )
    before = ledger.cash
    closed = ledger.settle("T", outcome=0, settled_at="2026-03-15T00:00:00+00:00")
    assert closed.settlement_base == 0.0
    assert closed.realised_pnl_base == pytest.approx(-9.87)
    assert ledger.cash == before, "nothing comes back on a NO resolution"
    assert ledger.equity() == pytest.approx(90.13)


def test_a_winning_binary_pays_one_per_contract() -> None:
    ledger = _ledger()
    ledger.open_position(
        ticker="T", event_key="E", contracts=20, price=0.60, fee=0.34,
        quote_currency="USD", opened_at="t",
    )
    closed = ledger.settle("T", outcome=1, settled_at="2026-03-15T00:00:00+00:00")
    # 20 contracts * $1 = $20 = £16.
    assert closed.settlement_base == pytest.approx(16.0)
    assert closed.realised_pnl_base == pytest.approx(6.13)
    assert ledger.equity() == pytest.approx(106.13)


def test_ledger_refuses_a_foreign_contract_without_an_fx_rate() -> None:
    ledger = ContractLedger(starting_cash=100.0, base_currency="GBP")
    with pytest.raises(ContractLedgerError, match="No FX rate"):
        ledger.open_position(
            ticker="T", event_key="E", contracts=10, price=0.5, fee=0.0,
            quote_currency="USD", opened_at="t",
        )


def test_ledger_refuses_an_unaffordable_position() -> None:
    ledger = _ledger()
    with pytest.raises(ContractLedgerError, match="Insufficient cash"):
        ledger.open_position(
            ticker="T", event_key="E", contracts=1000, price=0.6, fee=0.0,
            quote_currency="USD", opened_at="t",
        )


def test_ledger_refuses_prices_outside_zero_to_one() -> None:
    ledger = _ledger()
    for bad in (0.0, 1.0, 1.5, -0.2):
        with pytest.raises(ContractLedgerError):
            ledger.open_position(
                ticker=f"T{bad}", event_key="E", contracts=1, price=bad, fee=0.0,
                quote_currency="USD", opened_at="t",
            )


def test_ledger_refuses_adding_to_an_existing_position() -> None:
    ledger = _ledger()
    ledger.open_position(ticker="T", event_key="E", contracts=5, price=0.5, fee=0.0,
                         quote_currency="USD", opened_at="t")
    with pytest.raises(ContractLedgerError, match="Already holding"):
        ledger.open_position(ticker="T", event_key="E", contracts=5, price=0.5, fee=0.0,
                             quote_currency="USD", opened_at="t")


def test_exposure_rolls_up_by_event_because_one_release_moves_them_together() -> None:
    ledger = _ledger()
    ledger.open_position(ticker="A", event_key="CPI", contracts=10, price=0.5, fee=0.0,
                         quote_currency="USD", opened_at="t")
    ledger.open_position(ticker="B", event_key="CPI", contracts=10, price=0.4, fee=0.0,
                         quote_currency="USD", opened_at="t")
    ledger.open_position(ticker="C", event_key="NFP", contracts=10, price=0.3, fee=0.0,
                         quote_currency="USD", opened_at="t")
    assert ledger.exposure_for_event("CPI") == pytest.approx(7.20)
    assert ledger.exposure_for_event("NFP") == pytest.approx(2.40)
    assert ledger.total_exposure() == pytest.approx(9.60)


def test_equity_carries_open_binaries_at_cost_not_at_the_book() -> None:
    """A thin book must not make equity jump, because equity drives survival."""
    ledger = _ledger()
    ledger.open_position(ticker="T", event_key="E", contracts=10, price=0.5, fee=0.0,
                         quote_currency="USD", opened_at="t")
    assert ledger.equity() == pytest.approx(100.0)


def test_settling_an_absent_position_is_refused() -> None:
    ledger = _ledger()
    with pytest.raises(ContractLedgerError):
        ledger.settle("NOPE", outcome=1, settled_at="t")


# ==========================================================================
# Contract risk
# ==========================================================================
def test_sizing_keeps_the_whole_premium_inside_the_risk_budget() -> None:
    engine = ContractRiskEngine()
    sized = engine.size(
        price=0.60, equity=100.0, cash=100.0, fee_model=STANDARD_FEES, fx_rate=0.80
    )
    assert sized.approved is True
    # 10% of £100 is the cap; the entire premium is the loss.
    assert sized.max_loss_base <= 10.01
    assert sized.premium_base <= 10.01


def test_book_depth_can_be_the_binding_constraint() -> None:
    engine = ContractRiskEngine()
    sized = engine.size(
        price=0.10, equity=1000.0, cash=1000.0, fee_model=ZeroFeeModel(),
        available_contracts=3,
    )
    assert sized.contracts == 3
    assert sized.binding_constraint == "book_depth"


def test_event_exposure_cap_binds_across_correlated_contracts() -> None:
    engine = ContractRiskEngine()
    sized = engine.size(
        price=0.50, equity=100.0, cash=100.0, fee_model=ZeroFeeModel(),
        event_exposure=9.5,
    )
    assert sized.contracts <= 1
    sized_full = engine.size(
        price=0.50, equity=100.0, cash=100.0, fee_model=ZeroFeeModel(),
        event_exposure=10.0,
    )
    assert sized_full.approved is False
    assert "correlated" in sized_full.reason


def test_survival_policy_can_only_tighten_contract_sizing() -> None:
    engine = ContractRiskEngine()
    full = engine.size(price=0.5, equity=100.0, cash=100.0, fee_model=ZeroFeeModel())
    tight = engine.size(
        price=0.5, equity=100.0, cash=100.0, fee_model=ZeroFeeModel(),
        risk_multiplier=0.25, survival_max_premium_pct=0.02, survival_state="CRITICAL",
    )
    assert tight.contracts < full.contracts
    # A survival policy claiming to allow *more* must not loosen the static cap.
    loose = engine.size(
        price=0.5, equity=100.0, cash=100.0, fee_model=ZeroFeeModel(),
        risk_multiplier=5.0, survival_max_premium_pct=0.95,
    )
    assert loose.contracts == full.contracts


def test_terminated_and_halted_accounts_size_nothing() -> None:
    engine = ContractRiskEngine()
    assert engine.size(price=0.5, equity=100, cash=100, fee_model=ZeroFeeModel(),
                       terminated=True).approved is False
    assert engine.size(price=0.5, equity=100, cash=100, fee_model=ZeroFeeModel(),
                       halted=True).approved is False


def test_extreme_prices_are_refused() -> None:
    engine = ContractRiskEngine(ContractRiskLimits(min_price=0.02, max_price=0.98))
    assert engine.size(price=0.005, equity=100, cash=100,
                       fee_model=ZeroFeeModel()).approved is False
    assert engine.size(price=0.995, equity=100, cash=100,
                       fee_model=ZeroFeeModel()).approved is False


def test_daily_loss_limit_and_position_budget_stop_new_entries() -> None:
    engine = ContractRiskEngine()
    halted = engine.size(price=0.5, equity=95.0, cash=95.0, fee_model=ZeroFeeModel(),
                         daily_pnl=-5.0, day_start_equity=100.0)
    assert halted.approved is False
    assert "Daily loss" in halted.reason
    spent = engine.size(price=0.5, equity=100.0, cash=100.0, fee_model=ZeroFeeModel(),
                        positions_opened_today=4)
    assert spent.approved is False
    assert "budget is spent" in spent.reason


def test_a_sized_position_is_always_affordable_and_within_every_cap() -> None:
    """Fuzz: whatever the price and equity, the caps hold after fee rounding."""
    import random

    engine = ContractRiskEngine()
    rng = random.Random(4242)
    for _ in range(300):
        price = round(rng.uniform(0.03, 0.97), 2)
        equity = round(rng.uniform(20.0, 500.0), 2)
        cash = round(rng.uniform(1.0, equity), 2)
        sized = engine.size(
            price=price, equity=equity, cash=cash, fee_model=STANDARD_FEES,
            fx_rate=rng.choice([1.0, 0.8, 1.25]),
        )
        if not sized.approved:
            continue
        assert sized.max_loss_base <= cash + 0.01
        assert sized.max_loss_base <= equity * 0.10 + 0.01
        assert sized.contracts >= 1


# ==========================================================================
# Reconciliation
# ==========================================================================
def test_reconciliation_reports_a_divergence_rather_than_hiding_it() -> None:
    market, contract = _market()
    ledger = _ledger()
    market.submit(ticker=contract.ticker, contracts=10)
    # The ledger was never told, so the two must disagree loudly.
    report = market.reconcile(ledger)
    assert report["ok"] is False
    assert report["mismatches"][0]["venue_contracts"] == 10
    assert report["mismatches"][0]["ledger_contracts"] == 0


def test_reconciliation_is_clean_when_both_sides_agree() -> None:
    market, contract = _market()
    ledger = _ledger()
    result = market.submit(ticker=contract.ticker, contracts=10)
    ledger.open_position(
        ticker=contract.ticker, event_key=contract.event_key,
        contracts=result.filled_contracts, price=result.average_price,
        fee=result.fee, quote_currency="USD", opened_at="t",
    )
    assert market.reconcile(ledger)["ok"] is True
