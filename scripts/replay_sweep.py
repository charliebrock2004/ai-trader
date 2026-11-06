"""Offline, chronological replay of the BTC 5m paper desk over real candles.

Usage::

    PYTHONPATH=src python3 scripts/replay_sweep.py candles.json [--out results.json]

``candles.json`` is a list of Coinbase rows ``[time, low, high, open, close,
volume]`` (the format ``GET /products/BTC-USD/candles`` returns). Nothing here
touches the network, a broker or the live worker.

What it runs is the production path, not a stand-in::

    PaperSimulator -> DeterministicFirstSource -> TrendPullbackStrategy
                   -> PolicyGuardian (survival) -> RiskEngine(allow_orders=False)
                   -> next-bar paper fill with spread + slippage

No look-ahead: the simulator hands the detector ``candles[: i + 1]`` and fills
an approved order on the *next* bar's open. The tape is split chronologically:
the first ``TUNING_FRACTION`` is used to choose settings, the remainder is
reported separately as an evaluation window that played no part in the choice.

The only liberty taken is speed: the detector is shown the latest
``LOOKBACK`` bars rather than the entire history. The live worker fetches 280
to 580 bars, so this is what production sees anyway; EMA50 is converged long
before 400 bars.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from datetime import datetime, timezone
from multiprocessing import Pool
from typing import Any, Optional

from ai_trader.paper import simulator as simulator_module
from ai_trader.paper.simulator import PaperSimulator
from ai_trader.risk.engine import RiskEngine
from ai_trader.session.source import DeterministicFirstSource
from ai_trader.strategy.signal import SignalConfig, TrendPullbackStrategy
from ai_trader.survival.config import SurvivalConfig
from ai_trader.survival.engine import SurvivalEngine
from ai_trader.survival.policy import PolicyGuardian
from ai_trader.types import Candle, CandleSeries

LOOKBACK = 400
TUNING_FRACTION = 2 / 3
#: GBP per USD. Constant across the replay; the live desk refuses to trade
#: without a real rate, but a constant rate does not change which trades occur.
FX_RATE = 0.76
BARS_PER_DAY = 288

# analyse_series is only used for the Grok snapshot, which this replay does not
# call. Skipping it keeps a 20,000-bar replay in seconds rather than minutes.
simulator_module.analyse_series = lambda visible: None  # type: ignore[assignment]


class WindowedStrategy(TrendPullbackStrategy):
    """The production detector, shown the latest LOOKBACK bars."""

    def last_signal(self, index, series, *, has_position: bool = False):
        if len(series.candles) != index + 1:
            raise RuntimeError("Look-ahead: strategy saw the wrong number of bars.")
        tail = series.candles[-LOOKBACK:]
        return self.evaluate(
            [c.close for c in tail],
            highs=[c.high for c in tail],
            lows=[c.low for c in tail],
            has_position=has_position,
        )


def load(path: str) -> list[Candle]:
    rows = sorted(json.load(open(path, encoding="utf-8")))
    return [
        Candle(
            timestamp=datetime.fromtimestamp(int(r[0]), tz=timezone.utc).isoformat().replace("+00:00", "Z"),
            open=float(r[3]),
            high=float(r[2]),
            low=float(r[1]),
            close=float(r[4]),
            volume=float(r[5]),
        )
        for r in rows
    ]


def run(candles: list[Candle], overrides: dict[str, Any], warmup: int = 0) -> dict[str, Any]:
    series = CandleSeries(
        symbol="BTC-USD", timeframe="5m", scenario="replay", seed=0,
        candles=tuple(candles), source="replay",
    )
    config = SignalConfig.for_timeframe("5m", **overrides)
    survival = SurvivalEngine(SurvivalConfig(starting_equity=100.0))
    sim = PaperSimulator(
        risk=RiskEngine(allow_orders=False),
        policy=PolicyGuardian(survival),
        flatten_at_end=True,
        fx_rate=FX_RATE,
        instrument=simulator_module.instrument_for("BTC-USD"),
        base_currency="GBP",
    )
    sim.max_holding_bars = config.max_holding_bars
    decisions: list[dict[str, Any]] = []
    sim.on_decision = decisions.append
    detector = WindowedStrategy(config, timeframe="5m")
    source = DeterministicFirstSource(
        None, warmup=warmup, timeframe="5m", technical=detector,
        account_fn=lambda: sim.ledger.snapshot().to_dict(),
    )
    report = sim.run(series, source=source)
    if report.get("look_ahead"):
        raise RuntimeError("look-ahead detected")
    closed = report["closed_positions"]
    pnls = [float(p.get("realised_pnl") or 0.0) for p in closed]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    days = len(candles) / BARS_PER_DAY
    risk_rejections = sum(1 for d in decisions if d.get("rejection") == "risk_rejected")
    policy_downgrades = sum(1 for d in decisions if d.get("rejection") == "policy_downgraded")
    scores = sorted(detector.scores)
    return {
        "overrides": overrides,
        "days": round(days, 2),
        "bars": len(candles),
        "candidates": detector.candidates,
        "candidates_per_day": round(detector.candidates / days, 2),
        "candidate_pct_of_bars": round(100.0 * detector.candidates / max(1, detector.evaluated), 3),
        "round_trips": len(closed),
        "round_trips_per_day": round(len(closed) / days, 2),
        "win_rate": round(100.0 * len(wins) / len(pnls), 1) if pnls else None,
        "net_pnl_gbp": round(sum(pnls), 2),
        "avg_win_gbp": round(sum(wins) / len(wins), 3) if wins else None,
        "avg_loss_gbp": round(sum(losses) / len(losses), 3) if losses else None,
        "expectancy_gbp": round(sum(pnls) / len(pnls), 3) if pnls else None,
        "max_drawdown_pct": round(100.0 * float(sim.max_drawdown), 2),
        "risk_rejections": risk_rejections,
        "policy_downgrades": policy_downgrades,
        "rejections": dict(sorted(detector.rejection_counts.items(), key=lambda kv: -kv[1])),
        "score_median": scores[len(scores) // 2] if scores else None,
        "score_best": scores[-1] if scores else None,
    }


def _job(args: tuple[str, list[Candle], dict[str, Any]]) -> dict[str, Any]:
    label, candles, overrides = args
    out = run(candles, overrides)
    out["window"] = label
    return out


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("candles")
    parser.add_argument("--out")
    parser.add_argument("--grid", choices=["threshold", "filters", "both"], default="both")
    args = parser.parse_args(argv)
    candles = load(args.candles)
    cut = int(len(candles) * TUNING_FRACTION)
    windows = {"tuning": candles[:cut], "evaluation": candles[cut:]}
    grid: list[dict[str, Any]] = []
    if args.grid in ("threshold", "both"):
        grid += [{"strong_score": t} for t in (0.78, 0.74, 0.70, 0.68, 0.65, 0.62)]
    if args.grid in ("filters", "both"):
        for t in (0.78, 0.70, 0.68):
            grid += [
                {"strong_score": t, "min_reward_to_cost": 3.0},
                {"strong_score": t, "overbought_rsi": 82.0},
            ]
    jobs = [(name, data, g) for name, data in windows.items() for g in grid]
    with Pool() as pool:
        results = pool.map(_job, jobs)
    for r in results:
        print(json.dumps({k: v for k, v in r.items() if k != "rejections"}))
    if args.out:
        json.dump(
            {
                "tuning": [str(windows["tuning"][0].timestamp), str(windows["tuning"][-1].timestamp)],
                "evaluation": [str(windows["evaluation"][0].timestamp), str(windows["evaluation"][-1].timestamp)],
                "results": results,
            },
            open(args.out, "w", encoding="utf-8"),
            indent=1,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
