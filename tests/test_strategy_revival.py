"""Acceptance tests for the November 2026 paper-desk revival.

The desk recorded zero BTC trades for weeks. The causes, in order of weight:

1. A checkpoint published mid-boot carried an empty ledger, so every later
   boot restored a blank, STOPPED book (``persist.py``).
2. After any sleep longer than ~18.7 h the first live candles had too little
   history for EMA50, so 57% of evaluated bars were spent as warm-up holds
   (``continuity.live_trade_from_index``, two-page fetch).
3. Selectivity: a 0.78 threshold and an RSI gate that duplicated the momentum
   score (replay-calibrated to 0.70 and 82).

These tests use a slice of real Coinbase BTC-USD 5m candles
(``fixtures/btc_usd_5m_real.json``, 2-13 Oct 2026). No network, no broker.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ai_trader.ai.base import Analyst, ProposedDecision
from ai_trader.exceptions import MarketDataUnavailableError, StaleMarketDataError
from ai_trader.instruments import instrument_for
from ai_trader.market_data.public import PublicCryptoFeed
from ai_trader.paper import simulator as simulator_module
from ai_trader.paper.execution import SLIPPAGE_BPS, SPREAD_BPS
from ai_trader.paper.models import PaperAction
from ai_trader.paper.simulator import PaperSimulator
from ai_trader.risk.engine import RiskEngine
from ai_trader.risk.limits import RiskLimits
from ai_trader.safety import LIVE_TRADING_ALLOWED
from ai_trader.session.continuity import (
    CONTINUOUS_FETCH_BARS,
    DETECTOR_HISTORY_BARS,
    live_trade_from_index,
)
from ai_trader.session.source import DeterministicFirstSource
from ai_trader.strategy.signal import (
    DEFAULT_SCORE_THRESHOLD,
    MIN_SCORE_THRESHOLD,
    SignalConfig,
    TrendPullbackStrategy,
    validate_score_threshold,
)
from ai_trader.survival.config import SurvivalConfig
from ai_trader.survival.engine import SurvivalEngine
from ai_trader.survival.policy import PolicyGuardian
from ai_trader.types import Action, Candle, CandleSeries, Decision

FIXTURE = Path(__file__).parent / "fixtures" / "btc_usd_5m_real.json"


def _candles() -> list[Candle]:
    rows = sorted(json.loads(FIXTURE.read_text(encoding="utf-8")))
    return [
        Candle(
            timestamp=datetime.fromtimestamp(int(r[0]), tz=timezone.utc).isoformat(),
            open=float(r[3]), high=float(r[2]), low=float(r[1]),
            close=float(r[4]), volume=float(r[5]),
        )
        for r in rows
    ]


CANDLES = _candles()


def _series(candles=None) -> CandleSeries:
    return CandleSeries(
        symbol="BTC-USD", timeframe="5m", scenario="real", seed=0,
        candles=tuple(candles or CANDLES), source="replay",
    )


class _Windowed(TrendPullbackStrategy):
    """Production detector shown the latest 400 bars (as the live tape does)."""

    def last_signal(self, index, series, *, has_position: bool = False):
        if len(series.candles) != index + 1:
            raise RuntimeError("Look-ahead: strategy saw the wrong number of bars.")
        tail = series.candles[-400:]
        return self.evaluate(
            [c.close for c in tail], highs=[c.high for c in tail],
            lows=[c.low for c in tail], has_position=has_position,
        )


def _replay(threshold: float | None = None, *, risk=None, analyst=None, budget=None, **overrides):
    if threshold is not None:
        overrides["strong_score"] = threshold
    config = SignalConfig.for_timeframe("5m", **overrides)
    sim = PaperSimulator(
        risk=risk or RiskEngine(allow_orders=False),
        policy=PolicyGuardian(SurvivalEngine(SurvivalConfig(starting_equity=100.0))),
        fx_rate=0.76,
        instrument=instrument_for("BTC-USD"),
        base_currency="GBP",
    )
    sim.max_holding_bars = config.max_holding_bars
    decisions: list[dict] = []
    sim.on_decision = decisions.append
    detector = _Windowed(config, timeframe="5m")
    source = DeterministicFirstSource(
        analyst, budget, technical=detector, warmup=0, timeframe="5m",
        account_fn=lambda: sim.ledger.snapshot().to_dict(),
    )
    original = simulator_module.analyse_series
    simulator_module.analyse_series = lambda visible: None
    try:
        report = sim.run(_series(), source=source)
    finally:
        simulator_module.analyse_series = original
    return sim, source, detector, report, decisions


@pytest.fixture(scope="module")
def baseline():
    return _replay(0.78)


@pytest.fixture(scope="module")
def calibrated():
    return _replay()


# 1. The calibrated detector produces more opportunities than the old default.
def test_calibrated_default_finds_more_opportunities_than_078(baseline, calibrated) -> None:
    assert SignalConfig().strong_score == DEFAULT_SCORE_THRESHOLD == 0.70
    assert SignalConfig().overbought_rsi == 82.0
    old, new = baseline[2], calibrated[2]
    assert new.candidates > old.candidates, (old.candidates, new.candidates)
    assert len(calibrated[3]["closed_positions"]) >= len(baseline[3]["closed_positions"])
    # A real ten-day stretch of BTC must give the desk something to do.
    assert len(calibrated[3]["closed_positions"]) >= 5


# 2. Lowering the threshold never reduces candidate generation.
def test_lower_threshold_never_produces_fewer_candidates() -> None:
    counts = [_replay(t)[2].candidates for t in (0.78, 0.70, 0.62)]
    assert counts[0] <= counts[1] <= counts[2], counts


# 3. A genuinely unsuitable market still produces HOLD, with a named reason.
@pytest.mark.parametrize(
    "closes, expected",
    [
        ([100.0 * (0.997**i) for i in range(200)], "downtrend"),
        ([100.0 + (0.0001 if i % 2 else 0.0) for i in range(200)], "too_quiet"),
    ],
)
def test_unsuitable_market_still_holds(closes, expected) -> None:
    signal = TrendPullbackStrategy(SignalConfig.for_timeframe("5m")).evaluate(closes)
    assert signal.action == PaperAction.HOLD
    assert signal.rejection == expected


def test_volatility_floor_replaces_the_reward_to_cost_gate() -> None:
    cfg = SignalConfig.for_timeframe("5m")
    # Any bar that clears the single floor has a target worth min_reward_to_cost
    # round trips, so no candidate can carry a reward below that.
    assert cfg.volatility_floor * cfg.target_atr == pytest.approx(
        max(cfg.min_volatility * cfg.target_atr, cfg.round_trip_cost_pct * cfg.min_reward_to_cost)
    )


def test_every_candidate_clears_four_round_trips_of_cost(calibrated) -> None:
    cfg = SignalConfig.for_timeframe("5m")
    floor = cfg.round_trip_cost_pct * cfg.min_reward_to_cost
    candidates = [d for d in calibrated[4] if d["stage"] == "execution"]
    assert candidates
    for d in candidates:
        assert d["context"].get("reward_pct", 1.0) + 1e-12 >= floor


# 4. A candidate cannot bypass the paper risk engine.
def test_candidate_cannot_bypass_risk_engine() -> None:
    refusing = RiskEngine(allow_orders=False, limits=RiskLimits(max_open_positions=0))
    sim, _source, detector, report, decisions = _replay(risk=refusing)
    assert detector.candidates > 0
    assert report["fills"] == []
    assert any(d["rejection"] == "risk_rejected" for d in decisions)
    assert sim.stage_counts.get("execution", 0) == 0


# 5. Daily loss, trade count and open-position limits still bind.
def test_position_and_trade_limits_hold(calibrated) -> None:
    sim, _s, _d, report, _decisions = calibrated
    limits = sim.risk.limits
    entries_per_day: dict[str, int] = {}
    for fill in report["fills"]:
        if fill["side"] == "BUY":
            day = fill["timestamp"][:10]
            entries_per_day[day] = entries_per_day.get(day, 0) + 1
    assert entries_per_day and max(entries_per_day.values()) <= limits.max_trades_per_day
    assert len(report["positions"]) <= limits.max_open_positions
    assert sim.risk.allow_orders is False


def test_daily_loss_limit_halts_new_entries() -> None:
    normal = _replay(0.62)
    tight = RiskEngine(allow_orders=False, limits=RiskLimits(max_daily_loss_pct=0.0001))
    _sim, _s, _d, report, decisions = _replay(0.62, risk=tight)
    halts = [e for e in report["events"] if e.get("kind") == "halt"]
    assert halts and all(e["reason"] == "daily_loss_limit" for e in halts)
    # The halt refuses later candidates the same day, so fewer trades happen.
    assert any(d["rejection"] == "risk_rejected" for d in decisions)
    assert len(report["closed_positions"]) < len(normal[3]["closed_positions"])


# 6. No look-ahead.
def test_no_look_ahead(calibrated) -> None:
    assert calibrated[3]["look_ahead"] is False
    detector = TrendPullbackStrategy()
    with pytest.raises(RuntimeError, match="Look-ahead"):
        detector.last_signal(5, _series(CANDLES[:10]))


# 7. Missing, stale or invalid market data fails safely.
class _Http:
    def __init__(self, pages):
        self.pages = list(pages)
        self.calls: list[dict] = []

    def get(self, url, headers=None, params=None, timeout=None):
        self.calls.append(dict(params or {}))
        page = self.pages.pop(0)
        if isinstance(page, Exception):
            raise page
        return page


def _rows(end: datetime, count: int) -> list[list[float]]:
    out = []
    for i in range(count):
        t = end - timedelta(minutes=5 * (i + 1))
        out.append([int(t.timestamp()), 99.0, 101.0, 100.0, 100.5, 1.0])
    return out


def test_stale_market_data_fails_closed() -> None:
    now = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)
    stale = _rows(now - timedelta(hours=3), 50)
    feed = PublicCryptoFeed(http_client=_Http([stale]), now_fn=lambda: now)
    with pytest.raises(StaleMarketDataError):
        feed.candles("BTC-USD", timeframe="5m", limit=50)


def test_malformed_market_data_fails_closed() -> None:
    feed = PublicCryptoFeed(http_client=_Http([{"error": "nope"}]))
    with pytest.raises(MarketDataUnavailableError):
        feed.candles("BTC-USD", timeframe="5m", limit=50)


def test_feed_pages_back_for_history_and_survives_a_failed_older_page() -> None:
    now = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)
    newest = _rows(now, 300)
    oldest_ts = min(r[0] for r in newest)
    older = _rows(datetime.fromtimestamp(oldest_ts, tz=timezone.utc), 180)
    http = _Http([newest, older])
    series = PublicCryptoFeed(http_client=http, now_fn=lambda: now).candles(
        "BTC-USD", timeframe="5m", limit=CONTINUOUS_FETCH_BARS
    )
    assert len(series.candles) == CONTINUOUS_FETCH_BARS
    assert "start" in http.calls[1] and "end" in http.calls[1]
    # A failed older page still leaves a valid (shorter) fresh tape.
    http = _Http([newest, RuntimeError("boom")])
    series = PublicCryptoFeed(http_client=http, now_fn=lambda: now).candles(
        "BTC-USD", timeframe="5m", limit=CONTINUOUS_FETCH_BARS
    )
    assert len(series.candles) == 300


def test_live_bars_always_have_detector_history() -> None:
    candles = CANDLES[:CONTINUOUS_FETCH_BARS]
    # Slept longer than the whole window: every bar is "new", none may be
    # handed to the detector without history.
    long_ago = "2026-01-01T00:00:00+00:00"
    assert live_trade_from_index(candles, long_ago) == DETECTOR_HISTORY_BARS
    # Normal catch-up is unaffected.
    assert live_trade_from_index(candles, candles[400].timestamp) == 401


# 8 & 9. Grok: a budget skip keeps the deterministic candidate; a failure is recorded.
class _Raising(Analyst):
    name = "grok"
    paper_requested = True

    def is_configured(self) -> bool:
        return True

    def propose(self, *a, **k):
        raise TimeoutError("analyst timed out")


class _Agreeing(Analyst):
    name = "grok"
    paper_requested = True

    def is_configured(self) -> bool:
        return True

    def propose(self, snapshot, analysis=None, *, account=None, positions=None, candidate=None):
        return ProposedDecision(
            decision=Decision(symbol="BTC-USD", action=Action.BUY, confidence=0.6,
                              rationale="agree", model="grok"),
            context={"validated": True, "network": True, "failure": None},
        )


class _Exhausted:
    filter_holds = 0

    def allow(self):
        return False, "Daily Grok budget used. Deterministic candidate continues."


def test_grok_budget_skip_does_not_stop_a_deterministic_candidate(calibrated) -> None:
    _sim, source, detector, report, _d = _replay(analyst=_Agreeing(), budget=_Exhausted())
    assert source.budget_skips == detector.candidates > 0
    assert source.consults == 0
    assert report["fills"] == calibrated[3]["fills"]


def test_failed_analyst_call_is_recorded_as_a_hold() -> None:
    _sim, source, detector, report, _d = _replay(analyst=_Raising())
    failures = [d for d in source.decisions if d["source"] == "grok-failure"]
    assert detector.candidates > 0
    assert len(failures) == detector.candidates
    assert all(d["failure"] == "exception" and d["action"] == "HOLD" for d in failures)
    assert report["fills"] == []


# 10 & 11. A candidate reaches the simulator, fills on the next candle, and
# exits pay spread and slippage.
def test_candidate_fills_on_next_candle_and_costs_are_charged(calibrated) -> None:
    sim, _s, _d, report, decisions = calibrated
    orders = [d for d in decisions if d["stage"] == "execution"]
    entries = [f for f in report["fills"] if f["side"] == "BUY"]
    assert orders and entries
    by_ts = {c.timestamp: i for i, c in enumerate(CANDLES)}
    first_order, first_fill = orders[0], entries[0]
    assert by_ts[first_fill["timestamp"]] == first_order["bar"] + 1
    assert first_fill["price"] > CANDLES[first_order["bar"] + 1].open  # adverse spread
    for fill in report["fills"]:
        assert fill["spread"] == SPREAD_BPS and fill["slippage"] == SLIPPAGE_BPS
    # Every entry is matched by an exit (flatten at end), and P&L reconciles.
    assert len(report["closed_positions"]) == len(entries)
    realised = sum(p["realised_pnl"] for p in report["closed_positions"])
    assert realised == pytest.approx(report["account"]["realised_pnl"], abs=0.02)


# 12. The threshold the dashboard shows is the threshold the engine uses.
def test_dashboard_threshold_matches_engine(isolated_env, monkeypatch) -> None:
    monkeypatch.setenv("STRATEGY_SCORE_THRESHOLD", "0.66")
    from ai_trader.config import clear_settings_cache
    from ai_trader.runtime import get_runtime, reset_runtime

    clear_settings_cache()
    reset_runtime()
    runtime = get_runtime()
    report = runtime.orchestrator.start_paper_session(
        symbol="SIM-UP", bars=120, source="simulated", timeframe="5m"
    )
    detector = runtime.orchestrator.paper_session.source.technical
    assert detector.config.strong_score == 0.66
    assert report["signal"]["threshold"] == 0.66
    assert report["config"]["score_threshold"] == 0.66
    assert runtime.settings.public_view()["strategy_score_threshold"] == 0.66


def test_changing_threshold_changes_candidate_generation() -> None:
    loose = _replay(0.62)[2].candidates
    strict = _replay(0.90)[2].candidates
    assert loose > strict


def test_invalid_thresholds_are_refused(monkeypatch) -> None:
    from ai_trader.config import Settings
    from ai_trader.session.config import PaperSessionConfig
    from ai_trader.exceptions import InvalidMarketDataError

    for bad in ("0.3", "1.5", "abc"):
        monkeypatch.setenv("STRATEGY_SCORE_THRESHOLD", bad)
        with pytest.raises(Exception):
            Settings()
    with pytest.raises(ValueError):
        validate_score_threshold(MIN_SCORE_THRESHOLD - 0.01)
    with pytest.raises(InvalidMarketDataError):
        PaperSessionConfig(score_threshold=0.2).validate()


# 13. No configuration creates a live trading path.
def test_threshold_change_cannot_enable_live_trading(monkeypatch) -> None:
    from ai_trader.config import Settings
    from ai_trader.exceptions import OrderPlacementDisabledError
    from ai_trader.session.config import PaperSessionConfig
    from ai_trader.session.runner import PaperSession

    monkeypatch.setenv("STRATEGY_SCORE_THRESHOLD", "0.60")
    settings = Settings()
    assert LIVE_TRADING_ALLOWED is False
    assert settings.public_view()["live_trading"] is False
    assert settings.trading_mode == "simulate"
    session = PaperSession(PaperSessionConfig(score_threshold=0.60, bars=80))
    with pytest.raises(OrderPlacementDisabledError):
        session.start(risk=RiskEngine(allow_orders=True))


# 14. Worker state: nothing is published while boot is unsettled or a restore failed.
def test_unsettled_or_failed_restore_never_publishes(isolated_env, monkeypatch) -> None:
    from ai_trader import persist

    snapshot = {"sql": 'CREATE TABLE agent_life(id);\nINSERT INTO "agent_life" VALUES(1);', "live": False}
    monkeypatch.setattr(persist, "_state", dict(persist._state, settled=False, kind="ephemeral"))
    assert "not finished" in persist.snapshot_problem(snapshot)
    monkeypatch.setattr(persist, "_state", dict(persist._state, settled=True, kind="restore_failed"))
    assert "could not be read" in persist.snapshot_problem(snapshot)
    monkeypatch.setattr(persist, "_state", dict(persist._state, settled=True, kind="github-checkpoint"))
    assert persist.snapshot_problem(snapshot) is None
    assert "empty" in persist.snapshot_problem({"sql": "", "live": False})
    assert "agent_life" in persist.snapshot_problem({"sql": "CREATE TABLE x(id);", "live": False})


def test_failed_checkpoint_read_is_not_treated_as_no_checkpoint(isolated_env, monkeypatch) -> None:
    from ai_trader import persist

    monkeypatch.setenv("PERSIST_RESTORE", "true")

    def boom():
        raise persist.CheckpointReadError("timeout")

    monkeypatch.setattr(persist, "fetch_checkpoint", boom)
    status = persist.restore_if_needed(isolated_env / "fresh.db")
    assert status["kind"] == "restore_failed"
    assert status["restored"] is False
    assert persist.snapshot_problem({"sql": 'INSERT INTO "agent_life" VALUES(1);'})


def test_status_is_stopped_not_running_when_no_session(isolated_env) -> None:
    from ai_trader.runtime import get_runtime

    status = get_runtime().worker.status()
    assert status["running"] is False
    assert status["status"] == "STOPPED"
    assert status["live_trading_allowed"] is False
