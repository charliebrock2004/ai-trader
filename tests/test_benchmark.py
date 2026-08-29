from __future__ import annotations

import pytest

from ai_trader.ai.grok_client import GrokAnalyst
from ai_trader.benchmark.metrics import compute_metrics, max_drawdown_from_equity
from ai_trader.benchmark.runner import run_benchmark, run_one
from ai_trader.benchmark.splits import BenchmarkPeriod, load_series
from ai_trader.benchmark.strategies import (
    GROK_DECISION_BAR,
    RANDOM_SEED,
    SMA_FAST,
    SMA_SLOW,
    BuyAndHoldSource,
    GrokOnceSource,
    RandomBaselineSource,
    SimpleTechnicalSource,
)
from ai_trader.config import Settings, clear_settings_cache
from ai_trader.db.repository import Repository
from ai_trader.exceptions import HistoricalDataNotConfiguredError
from ai_trader.kill_switch import KillSwitch
from ai_trader.market_data.generator import generate_series
from ai_trader.paper.models import PaperAction
from ai_trader.pipeline.orchestrator import Orchestrator
from ai_trader.risk.engine import RiskEngine
from ai_trader.risk.limits import RiskLimits
from ai_trader.safety import LIVE_TRADING_ALLOWED
from tests.test_grok_paper import FakeHTTP


def test_metrics_pnl_drawdown_and_profit_factor() -> None:
    metrics = compute_metrics(
        starting_balance=100.0,
        ending_balance=108.0,
        closed=[
            {"realised_pnl": 10.0},
            {"realised_pnl": 5.0},
            {"realised_pnl": -3.0},
        ],
        equity_curve=[100.0, 120.0, 90.0, 108.0],
        maximum_drawdown=None,
    )
    assert metrics["absolute_pnl"] == 8.0
    assert metrics["return_pct"] == 8.0
    assert metrics["trades"] == 3
    assert metrics["winning_trades"] == 2
    assert metrics["losing_trades"] == 1
    assert metrics["win_rate"] == round(2 / 3, 4)
    assert metrics["profit_factor"] == round(15.0 / 3.0, 4)
    assert metrics["average_win"] == 7.5
    assert metrics["average_loss"] == -3.0
    assert metrics["maximum_drawdown"] == round((120.0 - 90.0) / 120.0, 6)
    assert max_drawdown_from_equity([100.0, 120.0, 90.0]) == round(0.25, 6)


def test_identical_starting_conditions() -> None:
    series = generate_series("SIM-FLAT", limit=40, seed=11)
    sources = [
        BuyAndHoldSource(),
        SimpleTechnicalSource(),
        RandomBaselineSource(seed=RANDOM_SEED),
        GrokOnceSource(__import__("ai_trader.ai.fixture", fromlist=["FixtureAnalyst"]).FixtureAnalyst()),
    ]
    reports = [run_one(series, source) for source in sources]
    first = reports[0]
    for report in reports:
        assert report["account"]["starting_cash"] == 100.0
        assert report["account"]["currency"] == "GBP"
        assert report["look_ahead"] is False
        assert report["broker_submit_calls"] == 0
        assert report["live"] is False
        assert report["assumptions"] == first["assumptions"]
        assert report["metrics"]["starting_balance"] == 100.0


def test_no_look_ahead_in_strategies() -> None:
    series = generate_series("SIM-UP", limit=30, seed=5)
    seen: list[int] = []

    class Probe(SimpleTechnicalSource):
        def decide(self, index, series, analysis):
            seen.append(len(series.candles))
            assert len(series.candles) == index + 1
            assert series.candles[-1].timestamp == series.candles[index].timestamp
            return super().decide(index, series, analysis)

    report = run_one(series, Probe())
    assert seen == list(range(1, 31))
    assert report["look_ahead"] is False


def test_grok_sees_only_visible_bars(isolated_env, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XAI_API_KEY", "test-key-not-real")
    monkeypatch.setenv("GROK_PAPER_ANALYSIS", "true")
    clear_settings_cache()
    http = FakeHTTP({"action": "HOLD", "confidence": 0.3, "reasoning": "wait"})
    analyst = GrokAnalyst(Settings(), enable_paper=True, http_client=http)
    series = generate_series("SIM-UP", limit=40, seed=42)
    source = GrokOnceSource(analyst, decision_bar=20)
    report = run_one(series, source)
    assert report["look_ahead"] is False
    assert source.decisions
    assert source.decisions[0]["bar_count"] == 21
    assert source.decisions[0]["bar"] == 20
    body = http.calls[0]["json"]["messages"][1]["content"]
    assert "SIM-UP" in body
    assert http.calls[0]["json"]["model"] == "grok-4.6"
    assert "tools" not in http.calls[0]["json"]
    assert all("alpaca" not in c["url"] for c in http.calls)


def test_random_baseline_is_deterministic() -> None:
    series = generate_series("SIM-VOL", limit=40, seed=9)
    a = run_one(series, RandomBaselineSource(seed=7))
    b = run_one(series, RandomBaselineSource(seed=7))
    c = run_one(series, RandomBaselineSource(seed=99))
    assert a["signals"] == b["signals"]
    assert a["return_pct"] == b["return_pct"]
    assert a["signals"] != c["signals"]


def test_buy_and_hold_pnl_on_handmade_path() -> None:
    from datetime import datetime, timedelta, timezone

    from ai_trader.types import Candle, CandleSeries

    start = datetime(2024, 1, 2, 14, 30, tzinfo=timezone.utc)

    def ts(i: int) -> str:
        return (start + timedelta(minutes=5 * i)).isoformat()

    candles = [
        Candle(timestamp=ts(0), open=100, high=100, low=100, close=100, volume=1),
        Candle(timestamp=ts(1), open=100, high=100.2, low=99.8, close=100, volume=1),
        Candle(timestamp=ts(2), open=102, high=102.4, low=101.6, close=102, volume=1),
    ]
    series = CandleSeries(
        symbol="SIM-TEST",
        timeframe="5m",
        scenario="test",
        seed=1,
        candles=tuple(candles),
    )
    risk = RiskEngine(allow_orders=False, limits=RiskLimits())
    from ai_trader.paper.simulator import PaperSimulator

    sim = PaperSimulator(
        starting_cash=100,
        risk=risk,
        spread_bps=0,
        slip_bps=0,
        flatten_at_end=True,
    )
    report = sim.run(series, source=BuyAndHoldSource())
    fills = report["fills"]
    assert fills[0]["reason"] == "ENTRY"
    assert fills[0]["price"] == 100.0
    assert fills[-1]["reason"] == "DAY_END"
    assert fills[-1]["price"] == 102.0
    assert report["account"]["realised_pnl"] == 2.0
    assert report["account"]["account_equity"] == 102.0
    assert report["look_ahead"] is False


def test_strategy_parameters_are_frozen() -> None:
    assert SMA_FAST == 10
    assert SMA_SLOW == 20
    assert GROK_DECISION_BAR == 20
    assert RANDOM_SEED == 7
    technical = SimpleTechnicalSource()
    series = generate_series("SIM-FLAT", limit=30, seed=3)
    run_one(series, technical)
    assert technical.fast == 10
    assert technical.slow == 20


def test_historical_period_stays_offline() -> None:
    with pytest.raises(HistoricalDataNotConfiguredError):
        load_series("SIM-UP", BenchmarkPeriod(name="out_of_sample", seed=1, source="historical"))


def test_full_benchmark_zero_broker(isolated_env, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XAI_API_KEY", "test-key-not-real")
    monkeypatch.setenv("GROK_PAPER_ANALYSIS", "true")
    clear_settings_cache()
    http = FakeHTTP({"action": "BUY", "confidence": 0.8, "reasoning": "uptrend continuation in sim"})
    settings = Settings()
    analyst = GrokAnalyst(settings, enable_paper=True, http_client=http)
    report = run_benchmark(
        grok_analyst=analyst,
        symbols=("SIM-UP", "SIM-DOWN"),
        periods=(
            BenchmarkPeriod("training", seed=101, limit=40),
            BenchmarkPeriod("validation", seed=202, limit=40),
            BenchmarkPeriod("out_of_sample", seed=303, limit=40),
        ),
    )
    assert report["live"] is False
    assert report["broker"] == "NOT USED"
    assert report["broker_submit_calls"] == 0
    assert report["banner"] == "PAPER SIMULATION — NO REAL TRADING"
    assert LIVE_TRADING_ALLOWED is False
    assert report["run_count"] == 2 * 3 * 4
    grok_runs = [r for r in report["runs"] if r["strategy"] == "GROK"]
    assert grok_runs
    assert all(r["ai_decisions"] for r in grok_runs)
    assert all(r["look_ahead"] is False for r in report["runs"])
    assert all(d["action"] == "BUY" for r in grok_runs for d in r["ai_decisions"])
    assert all("alpaca" not in c["url"] for c in http.calls)
    assert "https://api.x.ai/v1/chat/completions" in http.calls[0]["url"]
    headline = report["headline"]
    assert "grok_return_pct" in headline
    assert "benchmark_return_pct" in headline
    names = {row["strategy"] for row in report["comparison"]}
    assert names == {"GROK", "BUY_AND_HOLD", "SIMPLE_TECHNICAL", "RANDOM_BASELINE"}


def test_orchestrator_benchmark_never_calls_broker(isolated_env) -> None:
    settings = Settings()
    repo = Repository(settings.resolve_database_path())
    switch = KillSwitch(settings.resolve_kill_switch_path(), initially_engaged=True)
    orch = Orchestrator(settings, repo, switch)
    trapped: list[str] = []

    def trap(*args, **kwargs):
        trapped.append("submit")
        raise AssertionError("broker submit")

    orch.broker.submit = trap  # type: ignore[method-assign]
    orch.simulated_broker.submit = trap  # type: ignore[method-assign]
    orch.alpaca_broker.submit = trap  # type: ignore[method-assign]
    result = orch.benchmark()
    assert result["broker"] == "NOT USED"
    assert result["broker_submit_calls"] == 0
    assert result["grok_model"] == "fixture-hold"
    assert result["run_count"] == 5 * 3 * 4
    stored = repo.list_decisions(limit=50)
    assert stored
    assert all(row["status"] == "benchmark" for row in stored)
    assert all(row["action"] == "HOLD" for row in stored)
    assert trapped == []
    assert orch.simulated_broker.submit_calls == 0
    assert orch.alpaca_broker.health()["connected"] is False
    repo.close()


def test_oos_split_is_independent() -> None:
    train = load_series("SIM-UP", BenchmarkPeriod("training", seed=101, limit=20))
    oos = load_series("SIM-UP", BenchmarkPeriod("out_of_sample", seed=303, limit=20))
    assert [c.close for c in train.candles] != [c.close for c in oos.candles]
    assert train.seed == 101
    assert oos.seed == 303


def test_baselines_never_call_grok() -> None:
    from pathlib import Path

    text = Path(__file__).resolve().parents[1].joinpath("src/ai_trader/benchmark/strategies.py").read_text()
    assert "grok_client" not in text
    http = FakeHTTP({"action": "BUY", "confidence": 0.9, "reasoning": "should not be called"})
    series = generate_series("SIM-UP", limit=40, seed=4)
    for source in (
        BuyAndHoldSource(),
        SimpleTechnicalSource(),
        RandomBaselineSource(seed=RANDOM_SEED),
    ):
        report = run_one(series, source)
        assert report["ai_decisions"] == []
        assert report["broker_submit_calls"] == 0
    assert http.calls == []


def test_hold_decision_is_recorded() -> None:
    from ai_trader.ai.fixture import FixtureAnalyst

    series = generate_series("SIM-FLAT", limit=40, seed=8)
    source = GrokOnceSource(FixtureAnalyst(), decision_bar=20)
    report = run_one(series, source)
    assert report["ai_decisions"]
    assert report["ai_decisions"][0]["action"] == "HOLD"
    assert report["ai_decisions"][0]["bar"] == 20
    assert report["trades"] == [] or report["metrics"]["trades"] == 0
    assert report["broker_submit_calls"] == 0


def test_cash_is_not_an_edge() -> None:
    from ai_trader.benchmark.runner import _compare

    verdict = _compare(
        {
            "GROK": {"return_pct": 0.0, "trades": 0},
            "BUY_AND_HOLD": {"return_pct": -1.2, "trades": 5},
            "SIMPLE_TECHNICAL": {"return_pct": -0.4, "trades": 4},
            "RANDOM_BASELINE": {"return_pct": -3.0, "trades": 20},
        }
    )
    assert verdict["grok_traded"] is False
    assert verdict["beats_buy_and_hold"] is False
    assert verdict["beats_all"] is False


def test_full_grid_is_five_markets_three_splits(isolated_env) -> None:
    report = run_benchmark()
    assert report["run_count"] == 5 * 3 * 4
    assert {row["symbol"] for row in report["runs"]} == {
        "SIM-UP",
        "SIM-DOWN",
        "SIM-FLAT",
        "SIM-VOL",
        "SIM-SHOCK",
    }
    assert {row["split"] for row in report["runs"]} == {
        "training",
        "validation",
        "out_of_sample",
    }
    assert report["headline_split"] == "out_of_sample"
    assert report["starting_conditions"]["starting_cash"] == 100.0
    assert report["starting_conditions"]["live"] is False
    assert report["broker_submit_calls"] == 0
    assert LIVE_TRADING_ALLOWED is False
