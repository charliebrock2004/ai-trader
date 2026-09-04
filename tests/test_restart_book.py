"""An open position must survive the worker restarting.

The worker's host restarts it constantly. Before this, a restart rebuilt the
ledger from marked equity alone, which meant an open position silently became
cash at its mark: no exit fill, no spread, no slippage, and the stop loss gone.
The account looked right and the trade record was fiction — a round trip that
never closed, P&L booked at a price nobody traded at, and a position that could
no longer be stopped out.

On a host that restarts every few minutes that would have been most of the
evidence this experiment is meant to collect, so both halves are pinned here:
the book is carried across, and warm-up bars never touch it.
"""

from __future__ import annotations

from ai_trader.paper.ledger import PaperLedger
from ai_trader.paper.models import FillReason, OrderStatus, PaperFill, PaperOrder
from ai_trader.paper.simulator import PaperSimulator
from ai_trader.paper.signals import FixtureHoldSource
from ai_trader.types import Candle, CandleSeries

SYMBOL = "BTC-USD"


def _series(closes: list[float]) -> CandleSeries:
    return CandleSeries(
        symbol=SYMBOL,
        timeframe="5m",
        scenario="restart",
        seed=1,
        source="simulated",
        candles=tuple(
            Candle(
                timestamp=f"2026-09-04T{8 + i // 60:02d}:{i % 60:02d}:00+00:00",
                open=c,
                high=c * 1.002,
                low=c * 0.998,
                close=c,
                volume=10,
            )
            for i, c in enumerate(closes)
        ),
    )


def _open_position(ledger: PaperLedger, *, price: float, stop: float, target: float) -> None:
    order = PaperOrder(
        order_id="PAP-0001",
        symbol=SYMBOL,
        side="BUY",
        quantity=0.001,
        requested_price=price,
        stop_loss=stop,
        take_profit=target,
        timestamp="2026-09-04T08:00:00+00:00",
        status=OrderStatus.FILLED.value,
    )
    fill = PaperFill(
        fill_id="FIL-0001",
        order_id=order.order_id,
        symbol=SYMBOL,
        side="BUY",
        quantity=0.001,
        price=price,
        timestamp=order.timestamp,
        reason=FillReason.ENTRY.value,
        spread=5,
        slippage=5,
    )
    ledger.apply_buy(order, fill, quote_currency="USD")


def test_restore_carries_cash_and_the_position_separately() -> None:
    """Equity is preserved *and* the trade stays open with its stop."""
    ledger = PaperLedger(starting_cash=100.0, base_currency="GBP")
    ledger.set_fx("USD", 0.78)
    restored = ledger.restore(
        cash=75.77,
        positions=[
            {
                "symbol": SYMBOL,
                "quantity": 0.00046,
                "average_entry": 69971.94,
                "current_price": 71423.28,
                "stop_loss": 68335.82,
                "take_profit": 72519.64,
                "entry_timestamp": "2026-09-04T08:00:00+00:00",
                "order_id": "PAP-0002",
                "quote_currency": "USD",
                "entry_fx": 0.78,
                "current_fx": 0.78,
                "entry_cost_base": 25.11,
                "open": True,
            }
        ],
    )
    assert restored == 1
    assert ledger.cash == 75.77
    position = ledger.open_positions()[0]
    assert position.stop_loss == 68335.82, "a restored position without its stop is unprotected"
    assert position.take_profit == 72519.64
    assert ledger.equity() > ledger.cash, "the position must still be worth something"


def test_a_restored_position_is_not_stopped_out_by_bars_it_already_lived_through() -> None:
    """The bug that made restore pointless.

    Warm-up bars are history. Replaying them against a carried-over position
    triggered its stop on a candle from before the position existed, booking a
    loss the market never dealt.
    """
    # Price dips to 90 early, then recovers well above the stop.
    closes = [100.0] * 5 + [90.0] + [100.0] * 5 + [104.0, 105.0]
    series = _series(closes)

    # flatten_at_end=False is what a continuous session uses; a flat run would
    # close the position at the final bar for an unrelated reason.
    sim = PaperSimulator(starting_cash=100.0, base_currency="GBP", flatten_at_end=False)
    sim.ledger.set_fx("USD", 1.0)
    _open_position(sim.ledger, price=100.0, stop=95.0, target=110.0)
    assert sim.ledger.open_positions()

    # Everything except the final bar is history already processed.
    sim.trade_from_index = len(closes) - 1
    sim.run(series, source=FixtureHoldSource())

    assert sim.ledger.open_positions(), (
        "the dip to 90 is a bar the desk already lived through; it must not "
        "stop out a position carried across a restart"
    )
    assert not sim.ledger.closed_positions


def test_a_live_bar_still_stops_the_position_out() -> None:
    """The fix must not make stops unreachable — only history is excluded."""
    closes = [100.0] * 5 + [100.0] * 5 + [90.0]
    series = _series(closes)

    sim = PaperSimulator(starting_cash=100.0, base_currency="GBP", flatten_at_end=False)
    sim.ledger.set_fx("USD", 1.0)
    _open_position(sim.ledger, price=100.0, stop=95.0, target=110.0)

    sim.trade_from_index = len(closes) - 1  # the dip is the live bar
    sim.run(series, source=FixtureHoldSource())

    assert not sim.ledger.open_positions(), "a live bar through the stop must close the position"
    assert sim.ledger.closed_positions
    closed = sim.ledger.closed_positions[0]
    assert closed.realised_pnl < 0


def test_a_restart_never_invents_an_exit_fill() -> None:
    """Equity may only change through a recorded fill.

    A position that vanishes into cash is P&L with no trade behind it, which is
    exactly the kind of number this system must never produce.
    """
    ledger = PaperLedger(starting_cash=100.0, base_currency="GBP")
    ledger.set_fx("USD", 1.0)
    fills_before = len(ledger.fills)
    ledger.restore(
        cash=60.0,
        positions=[
            {
                "symbol": SYMBOL,
                "quantity": 0.4,
                "average_entry": 100.0,
                "current_price": 100.0,
                "stop_loss": 95.0,
                "take_profit": 110.0,
                "entry_timestamp": "t",
                "order_id": "o",
                "quote_currency": "USD",
                "entry_fx": 1.0,
                "current_fx": 1.0,
                "entry_cost_base": 40.0,
                "open": True,
            }
        ],
    )
    assert len(ledger.fills) == fills_before, "restoring is not a trade"
    assert len(ledger.closed_positions) == 0, "restoring must not close anything"


def test_restore_ignores_closed_and_malformed_rows() -> None:
    ledger = PaperLedger(starting_cash=100.0, base_currency="GBP")
    restored = ledger.restore(
        cash=100.0,
        positions=[
            {"symbol": SYMBOL, "quantity": 0.1, "open": False},
            {"symbol": None, "quantity": 0.1},
            {"symbol": SYMBOL, "quantity": 0},
            "not a dict",
        ],
    )
    assert restored == 0
    assert not ledger.open_positions()
