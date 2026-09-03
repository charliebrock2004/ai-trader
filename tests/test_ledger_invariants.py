"""Ledger invariants, including the ones only a foreign-quoted book can break."""

from __future__ import annotations

import random

import pytest

from ai_trader.paper.ledger import LedgerCurrencyError, PaperLedger
from ai_trader.paper.models import PaperFill, PaperOrder


def _order(ledger: PaperLedger, qty: float, price: float, *, stop=None, tp=None) -> PaperOrder:
    return PaperOrder(
        order_id=ledger.next_order_id(),
        symbol="BTC-USD",
        side="BUY",
        quantity=qty,
        requested_price=price,
        stop_loss=stop,
        take_profit=tp,
        timestamp="2026-01-01T00:00:00+00:00",
    )


def _fill(ledger: PaperLedger, order: PaperOrder, price: float, side: str = "BUY") -> PaperFill:
    return PaperFill(
        fill_id=ledger.next_fill_id(),
        order_id=order.order_id,
        symbol=order.symbol,
        side=side,
        quantity=order.quantity,
        price=price,
        timestamp="2026-01-01T00:05:00+00:00",
        reason="ENTRY" if side == "BUY" else "CLOSE",
        spread=0.0,
        slippage=0.0,
    )


# --------------------------------------------------------------------------
# Currency
# --------------------------------------------------------------------------
def test_ledger_refuses_a_foreign_fill_without_an_fx_rate() -> None:
    ledger = PaperLedger(starting_cash=100.0, base_currency="GBP")
    order = _order(ledger, 0.001, 90_000.0)
    with pytest.raises(LedgerCurrencyError):
        ledger.apply_buy(order, _fill(ledger, order, 90_000.0), quote_currency="USD")


def test_usd_position_is_valued_in_gbp() -> None:
    """£100 buying a $90k instrument must not act as if it had $100."""
    ledger = PaperLedger(starting_cash=100.0, base_currency="GBP")
    ledger.set_fx("USD", 0.80)  # 0.80 GBP per USD
    qty = 0.0005  # $45 of BTC = £36
    order = _order(ledger, qty, 90_000.0)
    ledger.apply_buy(order, _fill(ledger, order, 90_000.0), quote_currency="USD")
    assert ledger.cash == 64.00
    position = ledger.open_positions()[0]
    assert position.quote_currency == "USD"
    assert position.position_value_quote == 45.0
    assert position.position_value == 36.00
    assert ledger.equity() == 100.00


def test_fx_move_alone_produces_real_pnl() -> None:
    """Holding a USD asset while sterling moves is a real gain or loss."""
    ledger = PaperLedger(starting_cash=100.0, base_currency="GBP")
    ledger.set_fx("USD", 0.80)
    order = _order(ledger, 0.0005, 90_000.0)
    ledger.apply_buy(order, _fill(ledger, order, 90_000.0), quote_currency="USD")
    assert ledger.unrealised_pnl() == 0.0

    # Price unchanged; the dollar strengthens to 0.90 GBP.
    ledger.set_fx("USD", 0.90)
    ledger.mark("BTC-USD", 90_000.0, fx=0.90)
    # $45 is now worth £40.50, up from £36.
    assert ledger.unrealised_pnl() == 4.50
    assert ledger.equity() == 104.50


def test_realised_pnl_includes_the_fx_move() -> None:
    ledger = PaperLedger(starting_cash=100.0, base_currency="GBP")
    ledger.set_fx("USD", 0.80)
    order = _order(ledger, 0.0005, 90_000.0)
    ledger.apply_buy(order, _fill(ledger, order, 90_000.0), quote_currency="USD")
    ledger.set_fx("USD", 0.90)
    exit_fill = _fill(ledger, order, 90_000.0, side="SELL")
    closed = ledger.close_position("BTC-USD", exit_fill, reason="CLOSE")
    assert closed.realised_pnl == 4.50
    assert ledger.cash == 104.50
    assert ledger.realised_pnl == 4.50


# --------------------------------------------------------------------------
# Core invariants
# --------------------------------------------------------------------------
def test_equity_equals_cash_plus_invested_across_random_activity() -> None:
    rng = random.Random(20260302)
    for _ in range(200):
        ledger = PaperLedger(starting_cash=100.0)
        for _ in range(rng.randint(1, 4)):
            price = round(rng.uniform(1.0, 500.0), 2)
            affordable = ledger.cash / price
            qty = round(rng.uniform(0.0, affordable), 4)
            if qty <= 0:
                continue
            order = _order(ledger, qty, price)
            order.symbol = "SIM-X"
            fill = _fill(ledger, order, price)
            fill.symbol = "SIM-X"
            if ledger.positions.get("SIM-X"):
                continue
            ledger.apply_buy(order, fill)
            assert ledger.cash >= -0.001, "cash must never go negative"
            assert abs(ledger.equity() - (ledger.cash + ledger.invested_value())) < 0.011
            mark = round(price * rng.uniform(0.5, 1.5), 2)
            ledger.mark("SIM-X", mark)
            assert abs(ledger.equity() - (ledger.cash + ledger.invested_value())) < 0.011
            exit_fill = _fill(ledger, order, mark, side="SELL")
            exit_fill.symbol = "SIM-X"
            ledger.close_position("SIM-X", exit_fill, reason="CLOSE")
            assert ledger.cash >= -0.001


def test_total_pnl_equals_realised_plus_unrealised() -> None:
    ledger = PaperLedger(starting_cash=100.0)
    order = _order(ledger, 0.5, 50.0)
    order.symbol = "SIM-X"
    fill = _fill(ledger, order, 50.0)
    fill.symbol = "SIM-X"
    ledger.apply_buy(order, fill)
    ledger.mark("SIM-X", 60.0)
    snap = ledger.snapshot()
    assert snap.total_pnl == round(snap.realised_pnl + snap.unrealised_pnl, 2)
    assert snap.account_equity == round(snap.cash + snap.invested_value, 2)


def test_cash_cannot_go_negative_on_an_unaffordable_fill() -> None:
    ledger = PaperLedger(starting_cash=10.0)
    order = _order(ledger, 5.0, 100.0)
    order.symbol = "SIM-X"
    fill = _fill(ledger, order, 100.0)
    fill.symbol = "SIM-X"
    with pytest.raises(ValueError):
        ledger.apply_buy(order, fill)
    assert ledger.cash == 10.0


def test_closing_an_absent_position_is_refused() -> None:
    ledger = PaperLedger(starting_cash=100.0)
    order = _order(ledger, 1.0, 10.0)
    with pytest.raises(ValueError):
        ledger.close_position("NOPE", _fill(ledger, order, 10.0, side="SELL"), reason="CLOSE")


def test_day_roll_resets_counters_but_not_realised_pnl() -> None:
    ledger = PaperLedger(starting_cash=100.0)
    ledger.roll_day("2026-01-01T00:00:00+00:00")
    ledger.trades_today = 4
    ledger.entries_today = 4
    ledger.halted = True
    ledger.realised_pnl = -3.0
    ledger.roll_day("2026-01-02T00:00:00+00:00")
    assert ledger.trades_today == 0
    assert ledger.entries_today == 0
    assert ledger.halted is False
    assert ledger.realised_pnl == -3.0
