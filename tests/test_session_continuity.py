"""Indicator tape continuity across worker restarts. Does not change the strategy."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from dataclasses import replace

from ai_trader.strategy.signal import TrendPullbackStrategy
from ai_trader.config import Settings
from ai_trader.paper.models import PaperAction
from ai_trader.runtime import get_runtime
from ai_trader.safety import LIVE_TRADING_ALLOWED
from ai_trader.session.config import PaperSessionConfig
from ai_trader.session.continuity import (
    CONTINUOUS_FETCH_BARS,
    INDICATOR_HISTORY_BARS,
    fetch_limit,
    indicator_snapshot,
    resolve_trade_from_index,
)
from ai_trader.session.runner import PaperSession
from ai_trader.survival.config import SurvivalConfig
from ai_trader.types import Candle, CandleSeries

START = datetime(2026, 9, 4, 8, 0, tzinfo=timezone.utc)


def _ts(index: int) -> str:
    return (START + timedelta(minutes=5 * index)).isoformat()


def _candle(index: int, close: float) -> Candle:
    return Candle(
        timestamp=_ts(index),
        open=close,
        high=close + 0.5,
        low=close - 0.5,
        close=close,
        volume=1000,
    )


def _series(closes: list[float], symbol: str = "SIM-SMA") -> CandleSeries:
    return CandleSeries(
        symbol=symbol,
        timeframe="5m",
        scenario="continuity",
        seed=1,
        candles=tuple(_candle(i, close) for i, close in enumerate(closes)),
        source="simulated",
    )


def _session(series: CandleSeries, last_processed: str | None = None) -> PaperSession:
    return PaperSession(
        PaperSessionConfig(
            symbol=series.symbol,
            bars=len(series.candles),
            timeframe="5m",
            source="simulated",
            continuous=True,
            trade_historical_bars=False,
            warmup=8,
            flatten_at_end=False,
            last_processed_candle_ts=last_processed,
        ),
        gate_with_deterministic=True,
        poll_seconds=0.05,
    )


def trending_closes(count: int, *, base: float = 100.0) -> list[float]:
    """A deterministic uptrend with repeating pullbacks.

    A mechanism fixture, not evidence. These tests ask whether a restarted
    session resumes on the right bar and re-evaluates the bars it has not seen;
    they need a price path the live detector actually responds to, and they say
    nothing about whether responding to it makes money.

    The old fixture was sixty flat bars then a jump, which produced an exact
    SMA crossover. The live detector rightly refuses that series — zero
    volatility, then a 10% gap — so continuity has to be shown on a path that
    looks like a market.
    """
    drift, amplitude, period = 0.0012, 0.004, 13
    return [
        base * math.exp(drift * i) * (1.0 + amplitude * math.sin(2 * math.pi * i / period))
        for i in range(count)
    ]


def _buy_cross_closes(*, history: int = 60, after: int = 2) -> list[float]:
    """A path whose first BUY lands at or after ``history``."""
    return trending_closes(history + after)


def _signal_index(series: CandleSeries, *, start: int = 0) -> int | None:
    """First bar at or after ``start`` where the live detector wants to buy."""
    for index in range(start, len(series.candles)):
        visible = replace(series, candles=series.candles[: index + 1])
        strategy = TrendPullbackStrategy(timeframe="5m")
        if strategy.decide(index, visible, None) == PaperAction.BUY:
            return index
    return None


def test_fetch_limit_public_continuous_is_at_least_60() -> None:
    assert INDICATOR_HISTORY_BARS >= 60
    assert CONTINUOUS_FETCH_BARS >= INDICATOR_HISTORY_BARS
    assert fetch_limit(bars=24, continuous=True, source="public") >= 60
    assert fetch_limit(bars=24, continuous=True, source="public") >= CONTINUOUS_FETCH_BARS
    assert fetch_limit(bars=12, continuous=True, source="simulated") == 12
    assert fetch_limit(bars=24, continuous=False, source="public") == 24


def test_startup_loads_at_least_60_public_candles() -> None:
    class RecordingFeed:
        def __init__(self) -> None:
            self.limit: int | None = None

        def candles(self, symbol, *, timeframe=None, limit=48, scenario=None):
            self.limit = int(limit)
            closes = [100.0] * int(limit)
            return _series(closes, symbol=symbol)

    feed = RecordingFeed()
    session = PaperSession(
        PaperSessionConfig(
            symbol="SIM-SMA",
            bars=24,
            source="public",
            continuous=True,
            trade_historical_bars=False,
        ),
        market_data=feed,
        gate_with_deterministic=True,
    )
    loaded = session._load_series()
    assert feed.limit is not None
    assert feed.limit >= 60
    assert len(loaded.candles) >= 60


def test_warmup_bars_do_not_trade_or_count_as_live() -> None:
    series = _series(_buy_cross_closes(history=60, after=2))
    assert _signal_index(series) is not None, "fixture must contain a real signal"
    session = _session(series)
    report = session.start(series=series)
    assert report["trades"] == 0
    assert report["fills"] == []
    assert report["ai_decisions"] == []
    assert session.source is not None
    assert session.source.consults == 0
    assert session.sim is not None
    assert session.sim.trade_from_index == len(series.candles)
    assert session.last_processed_candle_ts == series.candles[-1].timestamp
    snap = indicator_snapshot(series.candles)
    assert snap["sma10"] is not None
    assert snap["sma20"] is not None


def test_last_processed_is_persisted_on_the_worker(isolated_env) -> None:
    runtime = get_runtime()
    stamp = "2026-09-04T11:05:00+00:00"
    runtime.worker._remember_processed(stamp)
    assert runtime.worker._last_processed_ts() == stamp
    life = runtime.repository.records.agent_life() or {}
    assert life.get("last_processed_candle_ts") == stamp


def test_restart_does_not_reprocess_the_previous_candle() -> None:
    series = _series(_buy_cross_closes(history=60, after=2))
    first = _session(series)
    first.start(series=series)
    baseline = first.last_processed_candle_ts
    assert baseline == series.candles[-1].timestamp

    second = _session(series, last_processed=baseline)
    report = second.start(series=series)
    assert report["trades"] == 0
    assert report["fills"] == []
    assert report["ai_decisions"] == []
    assert second.sim is not None
    assert second.sim.trade_from_index == len(series.candles)


def test_crossover_before_restart_is_not_traded_retroactively() -> None:
    series = _series(_buy_cross_closes(history=60, after=2))
    assert _signal_index(series) is not None, "fixture must contain a real signal"
    first = _session(series)
    first.start(series=series)
    assert first.last_processed_candle_ts is not None

    second = _session(series, last_processed=first.last_processed_candle_ts)
    report = second.start(series=series)
    assert report["trades"] == 0
    assert report["fills"] == []
    actions = [d.get("action") for d in report.get("ai_decisions") or []]
    assert "BUY" not in actions


def test_signal_after_last_processed_is_detected() -> None:
    """A signal on a bar the desk has not seen must be found and acted on."""
    history = _series(trending_closes(61))
    first = _session(history)
    first.start(series=history)
    baseline = first.last_processed_candle_ts
    assert baseline == history.candles[-1].timestamp
    assert first.sim is not None

    live = _series(trending_closes(63))
    assert _signal_index(live, start=61) == 61
    second = _session(live, last_processed=baseline)
    report = second.start(series=live)
    assert second.sim is not None
    assert second.sim.trade_from_index == 61
    actions = [d.get("action") for d in report.get("ai_decisions") or []]
    assert "BUY" in actions
    assert report["fills"], "A live signal must produce a genuine paper fill."
    assert report["fills"][0]["reason"] == "ENTRY"


def test_indicators_use_full_warmup_history_not_the_24_bar_stub() -> None:
    """A signal that exists on the long tape must survive a recover.

    The detector needs slow + slope + 1 bars before it can say anything at all,
    which is more than the 24-bar stub the session used to refetch. Truncating
    the tape does not make the desk cautious — it makes it blind, and a blind
    desk reports the same silence as a market with no opportunities.
    """
    full = _series(trending_closes(63))
    signal_at = _signal_index(full, start=61)
    assert signal_at == 61

    stub = replace(full, candles=full.candles[-24:])
    strategy = TrendPullbackStrategy(timeframe="5m")
    stub_signal = strategy.last_signal(len(stub.candles) - 1, stub)
    assert stub_signal.action != PaperAction.BUY
    assert stub_signal.rejection == "warming_up"

    baseline = full.candles[signal_at - 1].timestamp
    session = _session(full, last_processed=baseline)
    report = session.start(series=full)
    actions = [d.get("action") for d in report.get("ai_decisions") or []]
    assert "BUY" in actions
    assert session.sim is not None
    assert session.sim.trade_from_index == signal_at


def test_restart_between_candles_preserves_continuity() -> None:
    """One price path, walked in three sittings. Bar 60 is quiet; bar 61 signals."""
    history = _series(trending_closes(60))
    first = _session(history)
    first.start(series=history)
    baseline = first.last_processed_candle_ts

    one_new = _series(trending_closes(61))
    mid = _session(one_new, last_processed=baseline)
    mid_report = mid.start(series=one_new)
    assert mid_report["fills"] == []
    assert mid.last_processed_candle_ts == one_new.candles[-1].timestamp
    assert mid.sim is not None
    assert mid.sim.trade_from_index == 60

    with_signal = _series(trending_closes(62))
    later = _session(with_signal, last_processed=mid.last_processed_candle_ts)
    later_report = later.start(series=with_signal)
    assert later.sim is not None
    assert later.sim.trade_from_index == 61
    actions = [d.get("action") for d in later_report.get("ai_decisions") or []]
    assert "BUY" in actions
    assert later.last_processed_candle_ts == with_signal.candles[-1].timestamp


def test_repeated_starts_do_not_duplicate_processing() -> None:
    live = _series(trending_closes(63))
    history = _series(trending_closes(61))
    first = _session(history)
    first.start(series=history)
    baseline = first.last_processed_candle_ts

    fills = []
    last_ts = baseline
    for _ in range(3):
        session = _session(live, last_processed=last_ts)
        report = session.start(series=live)
        fills.append(len(report.get("fills") or []))
        last_ts = session.last_processed_candle_ts
    assert fills[0] >= 1
    assert fills[1] == 0
    assert fills[2] == 0


def test_risk_survival_and_grok_limits_unchanged() -> None:
    settings = Settings()
    assert settings.grok_daily_call_budget == 8
    assert settings.grok_min_interval_seconds == 1800
    assert settings.starting_equity == 100.00
    assert settings.terminal_threshold_pct == 0.40
    survival = SurvivalConfig()
    assert survival.starting_equity == 100.00
    assert survival.terminal_equity == 40.00
    assert LIVE_TRADING_ALLOWED is False


def test_paper_only_invariants_still_hold() -> None:
    series = _series([100.0] * 60)
    report = _session(series).start(series=series)
    assert report["live"] is False
    assert report["broker"] == "NOT USED"
    assert report["broker_submit_calls"] == 0
    assert LIVE_TRADING_ALLOWED is False


def test_resolve_trade_from_index_is_strictly_after_cutoff() -> None:
    series = _series([100.0] * 10)
    assert resolve_trade_from_index(series.candles, None) == 10
    assert resolve_trade_from_index(series.candles, series.candles[-1].timestamp) == 10
    assert resolve_trade_from_index(series.candles, series.candles[4].timestamp) == 5
