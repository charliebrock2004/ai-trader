from __future__ import annotations

import pytest

from ai_trader.exceptions import InvalidMarketDataError
from ai_trader.market_data.generator import generate_series
from ai_trader.market_data.simulated import SimulatedMarketData
from ai_trader.market_data.timeframes import TIMEFRAME_SECONDS, bar_time
from ai_trader.market_data.validation import parse_utc, validate_candle, validate_series
from ai_trader.types import Candle, CandleSeries


def test_same_seed_is_reproducible() -> None:
    first = generate_series("SIM-UP", timeframe="5m", limit=24, seed=42)
    second = generate_series("SIM-UP", timeframe="5m", limit=24, seed=42)
    assert [c.to_dict() for c in first.candles] == [c.to_dict() for c in second.candles]


def test_different_seed_changes_path() -> None:
    a = generate_series("SIM-UP", limit=24, seed=1)
    b = generate_series("SIM-UP", limit=24, seed=2)
    assert a.candles[-1].close != b.candles[-1].close


def test_timestamps_are_ordered_and_spaced() -> None:
    series = generate_series("SIM-FLAT", timeframe="15m", limit=12, seed=7)
    step = TIMEFRAME_SECONDS["15m"]
    previous = None
    for index, candle in enumerate(series.candles):
        moment = parse_utc(candle.timestamp)
        expected = bar_time(index, timeframe="15m")
        assert moment == expected
        if previous is not None:
            assert (moment - previous).total_seconds() == step
        previous = moment


@pytest.mark.parametrize(
    ("symbol", "scenario", "expect"),
    [
        ("SIM-UP", "uptrend", "up"),
        ("SIM-DOWN", "downtrend", "down"),
        ("SIM-FLAT", "sideways", "flat"),
        ("SIM-VOL", "high_volatility", "vol"),
        ("SIM-SHOCK", "shock", "shock"),
    ],
)
def test_scenarios_have_expected_shape(symbol: str, scenario: str, expect: str) -> None:
    series = generate_series(symbol, scenario=scenario, limit=48, seed=42)
    assert series.scenario == scenario
    first = series.candles[0].close
    last = series.candles[-1].close
    returns = [
        (series.candles[i].close / series.candles[i - 1].close) - 1
        for i in range(1, len(series.candles))
    ]
    if expect == "up":
        assert last > first
    elif expect == "down":
        assert last < first
    elif expect == "flat":
        assert abs(last - first) / first < 0.12
    elif expect == "vol":
        sideways = generate_series("SIM-FLAT", scenario="sideways", limit=48, seed=42)
        side_ret = [
            abs((sideways.candles[i].close / sideways.candles[i - 1].close) - 1)
            for i in range(1, len(sideways.candles))
        ]
        assert sum(abs(r) for r in returns) > sum(side_ret)
    elif expect == "shock":
        biggest = min(returns)
        assert biggest < -0.05


def test_ohlcv_invariants() -> None:
    series = generate_series("SPY", timeframe="1h", limit=30, seed=99)
    for candle in series.candles:
        assert candle.high >= max(candle.open, candle.close, candle.low)
        assert candle.low <= min(candle.open, candle.close, candle.high)
        assert candle.open > 0 and candle.close > 0
        assert candle.volume >= 0


def test_timeframes_change_spacing() -> None:
    m1 = generate_series("SIM-UP", timeframe="1m", limit=3, seed=3)
    d1 = generate_series("SIM-UP", timeframe="1d", limit=3, seed=3)
    t0 = parse_utc(m1.candles[0].timestamp)
    t1 = parse_utc(m1.candles[1].timestamp)
    d0 = parse_utc(d1.candles[0].timestamp)
    d_next = parse_utc(d1.candles[1].timestamp)
    assert (t1 - t0).total_seconds() == 60
    assert (d_next - d0).total_seconds() == 86400


def test_provider_snapshot_validates() -> None:
    feed = SimulatedMarketData(seed=42)
    snapshot = feed.snapshot(["SIM-UP", "SIM-DOWN"], timeframe="5m", limit=16)
    assert snapshot.source == "simulated"
    assert len(snapshot.bars) == 2
    assert len(snapshot.series) == 2
    last = snapshot.series[0].last()
    assert last is not None
    assert snapshot.bars[0].close == last.close


def test_rejects_malformed_candle() -> None:
    bad = Candle(
        timestamp="2024-01-02T14:30:00+00:00",
        open=10,
        high=9,
        low=8,
        close=9.5,
        volume=100,
    )
    with pytest.raises(InvalidMarketDataError):
        validate_candle(bad, symbol="SPY")


def test_rejects_out_of_order_timestamps() -> None:
    candles = (
        Candle("2024-01-02T14:35:00+00:00", 10, 11, 9, 10.5, 1000),
        Candle("2024-01-02T14:30:00+00:00", 10.5, 11, 10, 10.2, 1000),
    )
    series = CandleSeries(
        symbol="SPY",
        timeframe="5m",
        scenario="uptrend",
        seed=1,
        candles=candles,
    )
    with pytest.raises(InvalidMarketDataError):
        validate_series(series)


def test_rejects_unknown_timeframe() -> None:
    with pytest.raises(InvalidMarketDataError):
        generate_series("SPY", timeframe="3m")


def test_rejects_bad_symbol() -> None:
    with pytest.raises(InvalidMarketDataError):
        generate_series("nope!")
