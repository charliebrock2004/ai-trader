from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ai_trader.market_data.generator import generate_series
from ai_trader.paper.execution import buy_fill_price, resolve_intrabar, sell_fill_price
from ai_trader.paper.models import PaperAction
from ai_trader.paper.signals import FixtureHoldSource, ScriptedSignalSource
from ai_trader.paper.simulator import PaperSimulator
from ai_trader.risk.engine import RiskEngine
from ai_trader.risk.limits import RiskLimits
from ai_trader.types import Candle, CandleSeries


def _ts(i: int) -> str:
    start = datetime(2024, 1, 2, 14, 30, tzinfo=timezone.utc)
    return (start + timedelta(minutes=5 * i)).isoformat()


def _c(i: int, o: float, h: float, l: float, c: float) -> Candle:
    return Candle(timestamp=_ts(i), open=o, high=h, low=l, close=c, volume=1000)


def _series(candles: list[Candle], symbol: str = "SIM-TEST") -> CandleSeries:
    return CandleSeries(
        symbol=symbol,
        timeframe="5m",
        scenario="test",
        seed=1,
        candles=tuple(candles),
    )


def test_fixture_hold_produces_zero_fills() -> None:
    series = generate_series("SIM-UP", limit=40, seed=42)
    report = PaperSimulator(spread_bps=0, slip_bps=0).run(series, source=FixtureHoldSource())
    assert report["fills"] == []
    assert report["account"]["cash"] == 100.00
    assert report["account"]["fill_count"] == 0
    assert report["performance"]["total_trades"] == 0
    assert report["broker_submit_calls"] == 0
    assert report["look_ahead"] is False


def test_entry_fill_stop_and_accounting() -> None:
    candles = [
        _c(0, 100, 100.5, 99.5, 100),
        _c(1, 100, 100.2, 99.8, 100),
        _c(2, 99.5, 99.6, 96.0, 97),
    ]
    sim = PaperSimulator(spread_bps=0, slip_bps=0, flatten_at_end=False)
    report = sim.run(_series(candles), source=ScriptedSignalSource({0: PaperAction.BUY}))
    fills = report["fills"]
    assert len(fills) == 2
    assert fills[0]["reason"] == "ENTRY"
    assert fills[0]["price"] == 100.0
    # 25% concentration cap on £100 equity at £100/unit.
    assert fills[0]["quantity"] == 0.25
    assert fills[1]["reason"] == "STOP"
    # Bar 2 opened at 99.5, above the 98 stop, so the stop price is achievable.
    assert fills[1]["price"] == 98.0
    account = report["account"]
    assert account["cash"] == 99.50
    assert account["realised_pnl"] == -0.50
    assert account["invested_value"] == 0
    assert account["positions"] == []
    assert account["fill_count"] == 2


def test_stop_that_gaps_realises_the_gap_not_the_stop_price() -> None:
    """A bar that opens through the stop cannot have exited at the stop."""
    candles = [
        _c(0, 100, 100.5, 99.5, 100),
        _c(1, 100, 100.2, 99.8, 100),
        _c(2, 90, 91, 88.0, 89),  # gaps far below the 98 stop
    ]
    sim = PaperSimulator(spread_bps=0, slip_bps=0, flatten_at_end=False)
    report = sim.run(_series(candles), source=ScriptedSignalSource({0: PaperAction.BUY}))
    stop_fill = report["fills"][1]
    assert stop_fill["reason"] == "STOP"
    assert stop_fill["price"] == 90.0, "must fill at the gapped open, not the stop"
    # 0.25 units * (90 - 100) = -2.50, worse than the -0.50 the stop implied.
    assert report["account"]["realised_pnl"] == -2.50


def test_take_profit_never_fills_on_a_favourable_gap() -> None:
    candles = [
        _c(0, 100, 100, 100, 100),
        _c(1, 100, 100.1, 99.9, 100),
        _c(2, 120, 130, 119, 125),  # gaps far above the 104 target
    ]
    report = PaperSimulator(spread_bps=0, slip_bps=0, flatten_at_end=False).run(
        _series(candles), source=ScriptedSignalSource({0: PaperAction.BUY})
    )
    assert report["fills"][1]["reason"] == "TARGET"
    assert report["fills"][1]["price"] == 104.0, "favourable gaps are never credited"


def test_take_profit() -> None:
    candles = [
        _c(0, 100, 100, 100, 100),
        _c(1, 100, 100.1, 99.9, 100),
        _c(2, 101, 105, 100.5, 104),
    ]
    report = PaperSimulator(spread_bps=0, slip_bps=0, flatten_at_end=False).run(
        _series(candles), source=ScriptedSignalSource({0: PaperAction.BUY})
    )
    assert report["fills"][1]["reason"] == "TARGET"
    assert report["fills"][1]["price"] == 104.0
    assert report["account"]["cash"] == 101.00
    assert report["account"]["realised_pnl"] == 1.00


def test_ambiguous_candle_uses_stop() -> None:
    candle = _c(2, 100, 105, 96, 102)
    hit = resolve_intrabar(stop_loss=98.0, take_profit=104.0, candle=candle)
    assert hit == "stop"
    candles = [
        _c(0, 100, 100, 100, 100),
        _c(1, 100, 100.1, 99.9, 100),
        candle,
    ]
    report = PaperSimulator(spread_bps=0, slip_bps=0, flatten_at_end=False).run(
        _series(candles), source=ScriptedSignalSource({0: PaperAction.BUY})
    )
    assert report["fills"][1]["reason"] == "STOP"
    assert report["account"]["realised_pnl"] == -0.50


def test_spread_and_slippage_are_adverse() -> None:
    assert buy_fill_price(100, spread_bps=5, slip_bps=5) == 100.075
    assert sell_fill_price(100, spread_bps=5, slip_bps=5) == 99.925


def test_fill_not_guaranteed_on_last_bar() -> None:
    candles = [_c(0, 100, 100, 100, 100)]
    report = PaperSimulator(spread_bps=0, slip_bps=0, flatten_at_end=False).run(
        _series(candles), source=ScriptedSignalSource({0: PaperAction.BUY})
    )
    assert report["fills"] == []
    assert report["orders"][0]["status"] == "CANCELLED"


def test_no_look_ahead() -> None:
    seen: list[int] = []

    class Probe:
        name = "probe"

        def decide(self, index, series, analysis):
            seen.append(len(series.candles))
            assert series.candles[-1].timestamp == series.candles[index].timestamp
            return PaperAction.HOLD

    candles = [_c(i, 100, 101, 99, 100) for i in range(8)]
    report = PaperSimulator().run(_series(candles), source=Probe())
    assert seen == list(range(1, 9))
    assert report["look_ahead"] is False


def test_daily_loss_halts_new_entries() -> None:
    candles = [
        _c(0, 100, 100, 100, 100),
        _c(1, 100, 100, 99.8, 100),
        _c(2, 99, 99, 96, 97),
        _c(3, 97, 98, 96, 97),
        _c(4, 97, 98, 96, 97),
    ]
    # 0.25 units losing ~3 points is a ~£0.75 hit on a £100 book, so the halt
    # threshold has to be set against the position the engine actually takes.
    risk = RiskEngine(allow_orders=False, limits=RiskLimits(max_daily_loss_pct=0.005))
    sim = PaperSimulator(risk=risk, spread_bps=0, slip_bps=0, flatten_at_end=False)
    report = sim.run(
        _series(candles),
        source=ScriptedSignalSource({0: PaperAction.BUY, 3: PaperAction.BUY}),
    )
    buys = [o for o in report["orders"] if o["side"] == "BUY"]
    assert any(o["status"] == "REJECTED" and "Daily loss" in o["reason"] for o in buys) or sim.ledger.halted
    assert sim.ledger.halted is True


def test_a_round_trip_counts_as_one_trade() -> None:
    """Opening and closing one position is one trade, not two."""
    candles = [
        _c(0, 100, 100, 100, 100),
        _c(1, 100, 100.1, 99.9, 100),
        _c(2, 99.5, 99.6, 96.0, 97),  # stops out
    ]
    sim = PaperSimulator(spread_bps=0, slip_bps=0, flatten_at_end=False)
    sim.run(_series(candles), source=ScriptedSignalSource({0: PaperAction.BUY}))
    assert len(sim.ledger.closed_positions) == 1
    assert sim.ledger.trades_today == 1
    assert sim.ledger.round_trips == 1
    # Two fills, one trade.
    assert len(sim.ledger.fills) == 2


def test_open_position_is_not_yet_a_completed_trade() -> None:
    candles = [
        _c(0, 100, 100, 100, 100),
        _c(1, 100, 100.1, 99.9, 100),
        _c(2, 100, 100.2, 99.8, 100),
    ]
    sim = PaperSimulator(spread_bps=0, slip_bps=0, flatten_at_end=False)
    sim.run(_series(candles), source=ScriptedSignalSource({0: PaperAction.BUY}))
    assert sim.ledger.open_trades == 1
    assert sim.ledger.trades_today == 0
    assert sim.ledger.entries_today == 1


def test_warmup_bars_never_trade() -> None:
    """Historical warm-up may feed indicators but must not create fills."""
    candles = [_c(i, 100, 101, 99, 100) for i in range(10)]
    sim = PaperSimulator(spread_bps=0, slip_bps=0, flatten_at_end=False)
    sim.trade_from_index = 6
    report = sim.run(
        _series(candles),
        source=ScriptedSignalSource({0: PaperAction.BUY, 2: PaperAction.BUY}),
    )
    assert report["orders"] == []
    assert report["fills"] == []
    assert report["trade_from_index"] == 6


def test_kill_switch_blocks_new_entries() -> None:
    candles = [_c(i, 100, 101, 99, 100) for i in range(6)]
    report = PaperSimulator(spread_bps=0, slip_bps=0).run(
        _series(candles),
        source=ScriptedSignalSource({0: PaperAction.BUY}),
        kill_switch=True,
    )
    assert report["fills"] == []
    assert all(o["status"] != "FILLED" for o in report["orders"])


def test_max_trades_per_day() -> None:
    candles = [_c(i, 100, 100.2, 99.8, 100) for i in range(8)]
    risk = RiskEngine(allow_orders=False, limits=RiskLimits(max_trades_per_day=1))
    report = PaperSimulator(risk=risk, spread_bps=0, slip_bps=0, flatten_at_end=False).run(
        _series(candles),
        source=ScriptedSignalSource({0: PaperAction.BUY, 3: PaperAction.BUY}),
    )
    rejected = [o for o in report["orders"] if o["status"] == "REJECTED"]
    assert rejected


def test_deterministic_and_generated_scenarios() -> None:
    for symbol in ("SIM-UP", "SIM-DOWN", "SIM-FLAT", "SIM-VOL", "SIM-SHOCK"):
        series = generate_series(symbol, limit=40, seed=42)
        a = PaperSimulator(spread_bps=0, slip_bps=0).run(
            series, source=ScriptedSignalSource({10: PaperAction.BUY})
        )
        b = PaperSimulator(spread_bps=0, slip_bps=0).run(
            series, source=ScriptedSignalSource({10: PaperAction.BUY})
        )
        assert a["account"]["cash"] == b["account"]["cash"]
        assert a["look_ahead"] is False
        assert a["broker_submit_calls"] == 0


def test_equity_identity() -> None:
    series = generate_series("SIM-UP", limit=30, seed=42)
    report = PaperSimulator(spread_bps=0, slip_bps=0, flatten_at_end=True).run(
        series, source=ScriptedSignalSource({5: PaperAction.BUY})
    )
    acc = report["account"]
    assert acc["account_equity"] == round(acc["cash"] + acc["invested_value"], 2)
    assert acc["total_pnl"] == round(acc["realised_pnl"] + acc["unrealised_pnl"], 2)
    assert acc["live"] is False
