from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from ai_trader.analysis import indicators
from ai_trader.analysis.indicators import pct_change, sma
from ai_trader.analysis.technical import TechnicalAnalyst, analyse_series
from ai_trader.db.repository import Repository
from ai_trader.market_data.generator import generate_series
from ai_trader.market_data.simulated import SimulatedMarketData
from ai_trader.types import Candle, CandleSeries


def _series_from_closes(closes: list[float], symbol: str = "TEST") -> CandleSeries:
    candles = []
    for index, close in enumerate(closes):
        candles.append(
            Candle(
                timestamp=f"2024-01-02T14:{index:02d}:00+00:00",
                open=close,
                high=close + 0.5,
                low=close - 0.5,
                close=close,
                volume=1000 + index,
            )
        )
    return CandleSeries(
        symbol=symbol,
        timeframe="1m",
        scenario="sideways",
        seed=1,
        candles=tuple(candles),
    )


def test_sma_matches_hand_calculation() -> None:
    closes = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    assert sma(closes, 5) == pytest.approx(8.0)
    assert sma(closes, 10) == pytest.approx(5.5)
    assert sma(closes, 20) is None


def test_returns() -> None:
    closes = [100.0, 101.0, 103.0, 102.0]
    assert pct_change(closes, 1) == pytest.approx(102 / 103 - 1)
    assert pct_change(closes, 3) == pytest.approx(0.02)
    assert pct_change(closes, 10) is None
    assert pct_change([0.0, 1.0], 1) is None


def test_trend_up_down_sideways() -> None:
    climbing = _series_from_closes([100 + i * 0.8 for i in range(30)])
    falling = _series_from_closes([100 - i * 0.8 for i in range(30)])
    flat = _series_from_closes([100.0] * 30)
    assert analyse_series(climbing).trend == "UP"
    assert analyse_series(falling).trend == "DOWN"
    assert analyse_series(flat).trend == "SIDEWAYS"


def test_insufficient_data_leaves_sma_empty() -> None:
    short = _series_from_closes([10.0, 10.2, 10.1])
    result = analyse_series(short)
    assert result.sma_5 is None
    assert result.sma_10 is None
    assert result.sma_20 is None
    assert result.sma_50 is None
    assert result.trend == "UNKNOWN"
    assert result.current_price == 10.1
    assert result.last_pct is not None


def test_zero_close_does_not_raise() -> None:
    candles = (
        Candle("2024-01-02T14:30:00+00:00", 0, 0.1, 0.0, 0.0, 10),
        Candle("2024-01-02T14:31:00+00:00", 1, 1.1, 0.9, 1.0, 10),
    )
    series = CandleSeries("ZERO", "1m", "sideways", 1, candles)
    result = analyse_series(series)
    assert result.last_pct is None
    assert result.lookbacks["1"] is None


def test_deterministic() -> None:
    series = generate_series("SIM-UP", limit=60, seed=42)
    first = analyse_series(series).to_dict()
    second = analyse_series(series).to_dict()
    assert first == second


@pytest.mark.parametrize(
    ("symbol", "expected"),
    [
        ("SIM-UP", "UP"),
        ("SIM-DOWN", "DOWN"),
        ("SIM-FLAT", "SIDEWAYS"),
        ("SIM-SHOCK", None),
        ("SIM-VOL", None),
    ],
)
def test_scenarios(symbol: str, expected: str | None) -> None:
    series = generate_series(symbol, limit=60, seed=42)
    result = analyse_series(series)
    assert result.symbol == symbol
    assert result.current_price is not None
    assert result.sma_5 is not None
    assert result.sma_20 is not None
    assert result.sma_50 is not None
    assert result.trend in {"UP", "DOWN", "SIDEWAYS"}
    if expected:
        assert result.trend == expected


def test_shock_contains_a_gap() -> None:
    series = generate_series("SIM-SHOCK", limit=60, seed=42)
    closes = [c.close for c in series.candles]
    returns = [(closes[i] / closes[i - 1]) - 1 for i in range(1, len(closes))]
    assert min(returns) < -0.05


def test_sma50_requires_fifty_bars() -> None:
    short = generate_series("SIM-UP", limit=40, seed=42)
    long = generate_series("SIM-UP", limit=60, seed=42)
    assert analyse_series(short).sma_50 is None
    assert analyse_series(long).sma_50 is not None


def test_analyst_has_no_execution_path() -> None:
    source = inspect.getsource(indicators) + inspect.getsource(
        __import__("ai_trader.analysis.technical", fromlist=["technical"])
    )
    lowered = source.lower()
    assert "alpaca" not in lowered
    assert "place_order" not in lowered
    assert "submit" not in lowered


def test_technical_analyst_snapshot_and_store(tmp_path: Path) -> None:
    feed = SimulatedMarketData(seed=42)
    snapshot = feed.snapshot(["SIM-UP", "SIM-DOWN"], limit=60)
    bundle = TechnicalAnalyst().analyse(snapshot)
    assert len(bundle.analyses) == 2
    repo = Repository(tmp_path / "a.db")
    for item in bundle.analyses:
        repo.save_analysis(item)
    stored = repo.latest_analysis()
    assert {row["symbol"] for row in stored} == {"SIM-DOWN", "SIM-UP"}
    assert repo.list_trades() == []
    repo.close()
