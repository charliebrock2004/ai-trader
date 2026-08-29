"""Run the four strategies on shared paper assumptions. Never a broker."""

from __future__ import annotations

from typing import Any, Iterable, Optional

from ai_trader.account.simulated import STARTING_CASH
from ai_trader.ai.base import Analyst
from ai_trader.ai.fixture import FixtureAnalyst
from ai_trader.benchmark.metrics import metrics_from_sim, pool_metrics
from ai_trader.benchmark.splits import (
    DEFAULT_MARKETS,
    DEFAULT_PERIODS,
    HEADLINE_SPLIT,
    BenchmarkPeriod,
    load_series,
)
from ai_trader.benchmark.strategies import (
    GROK_DECISION_BAR,
    RANDOM_SEED,
    BuyAndHoldSource,
    GrokOnceSource,
    RandomBaselineSource,
    SimpleTechnicalSource,
)
from ai_trader.paper.execution import ASSUMPTIONS, SLIPPAGE_BPS, SPREAD_BPS
from ai_trader.paper.models import PaperAction
from ai_trader.paper.simulator import PaperSimulator
from ai_trader.risk.engine import RiskEngine
from ai_trader.risk.limits import RiskLimits
from ai_trader.safety import LIVE_TRADING_ALLOWED
from ai_trader.types import CandleSeries, MarketAnalysis

BANNER = "PAPER SIMULATION — NO REAL TRADING"
BENCHMARK_NAME = "BUY_AND_HOLD"


class RecordingSource:
    """Wrap a source so every non-HOLD decision is stored. No look-ahead."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.name = getattr(inner, "name", "unknown")
        self.signals: list[dict[str, Any]] = []

    def decide(
        self,
        index: int,
        series: CandleSeries,
        analysis: Optional[MarketAnalysis],
    ) -> PaperAction:
        visible = len(series.candles)
        if visible != index + 1:
            raise RuntimeError("Look-ahead: strategy saw the wrong number of bars.")
        action = self.inner.decide(index, series, analysis)
        if action not in {PaperAction.HOLD, "HOLD"}:
            self.signals.append(
                {
                    "bar": index,
                    "bar_count": visible,
                    "action": action.value if hasattr(action, "value") else str(action),
                    "price": series.candles[index].close,
                    "timestamp": series.candles[index].timestamp,
                }
            )
        return action


def _starting_conditions(limits: RiskLimits) -> dict[str, Any]:
    return {
        "starting_cash": STARTING_CASH,
        "currency": "GBP",
        "spread_bps": SPREAD_BPS,
        "slippage_bps": SLIPPAGE_BPS,
        "max_risk_pct": limits.max_risk_pct,
        "max_risk_amount": limits.max_risk_amount,
        "max_open_positions": limits.max_open_positions,
        "max_daily_loss_pct": limits.max_daily_loss_pct,
        "max_trades_per_day": limits.max_trades_per_day,
        "leverage": limits.leverage,
        "flatten_at_end": True,
        "live": False,
        "live_trading_allowed": LIVE_TRADING_ALLOWED,
        "broker": "NOT USED",
    }


def _make_sources(grok_analyst: Analyst) -> dict[str, Any]:
    return {
        "BUY_AND_HOLD": BuyAndHoldSource(),
        "SIMPLE_TECHNICAL": SimpleTechnicalSource(),
        "RANDOM_BASELINE": RandomBaselineSource(seed=RANDOM_SEED),
        "GROK": GrokOnceSource(grok_analyst, decision_bar=GROK_DECISION_BAR),
    }


def run_one(
    series: CandleSeries,
    source: Any,
    *,
    risk: Optional[RiskEngine] = None,
    starting_cash: float = STARTING_CASH,
) -> dict[str, Any]:
    engine = risk or RiskEngine(allow_orders=False, limits=RiskLimits())
    wrapped = RecordingSource(source)
    sim = PaperSimulator(
        starting_cash=starting_cash,
        risk=engine,
        spread_bps=SPREAD_BPS,
        slip_bps=SLIPPAGE_BPS,
        flatten_at_end=True,
    )
    report = sim.run(series, source=wrapped, kill_switch=False)
    if report.get("look_ahead"):
        raise RuntimeError("Look-ahead bias detected in paper simulator.")
    metrics = metrics_from_sim(report)
    grok_decisions = list(getattr(source, "decisions", []) or [])
    return {
        "ok": True,
        "live": False,
        "broker": "NOT USED",
        "broker_submit_calls": 0,
        "look_ahead": False,
        "strategy": wrapped.name,
        "symbol": series.symbol,
        "scenario": series.scenario,
        "seed": series.seed,
        "bars": len(series.candles),
        "metrics": metrics,
        "closed": report.get("closed_positions") or [],
        "trades": report.get("closed_positions") or [],
        "orders": report.get("orders") or [],
        "fills": report.get("fills") or [],
        "signals": wrapped.signals,
        "ai_decisions": grok_decisions,
        "account": report.get("account"),
        "equity_curve": report.get("equity_curve") or [],
        "assumptions": ASSUMPTIONS,
        **metrics,
    }


def _compare(pooled: dict[str, dict[str, Any]]) -> dict[str, Any]:
    grok = pooled.get("GROK") or {}
    others = {name: row for name, row in pooled.items() if name != "GROK"}
    grok_ret = float(grok.get("return_pct") or 0.0)
    grok_trades = int(grok.get("trades") or 0)
    grok_traded = grok_trades > 0
    beats = {
        name: grok_traded and grok_ret > float(row.get("return_pct") or 0.0)
        for name, row in others.items()
    }
    return {
        "grok_return_pct": grok_ret,
        "benchmark_return_pct": float((pooled.get(BENCHMARK_NAME) or {}).get("return_pct") or 0.0),
        "grok_traded": grok_trades > 0,
        "beats": beats,
        "beats_buy_and_hold": beats.get("BUY_AND_HOLD", False),
        "beats_simple_technical": beats.get("SIMPLE_TECHNICAL", False),
        "beats_random": beats.get("RANDOM_BASELINE", False),
        "beats_all": bool(grok_trades > 0 and beats and all(beats.values())),
    }


def _pool_split(runs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_name: dict[str, list[dict[str, Any]]] = {}
    for run in runs:
        by_name.setdefault(run["strategy"], []).append(run)
    return {name: pool_metrics(items) for name, items in by_name.items()}


def build_public_report(
    runs: list[dict[str, Any]],
    *,
    grok_model: str,
    periods: Iterable[BenchmarkPeriod],
    conditions: dict[str, Any],
) -> dict[str, Any]:
    splits: dict[str, Any] = {}
    for period in periods:
        split_runs = [r for r in runs if r.get("split") == period.name]
        pooled = _pool_split(split_runs)
        markets: dict[str, dict[str, Any]] = {}
        for run in split_runs:
            markets.setdefault(run["symbol"], {})[run["strategy"]] = {
                "return_pct": run["return_pct"],
                "absolute_pnl": run["absolute_pnl"],
                "ending_balance": run["ending_balance"],
                "trades": run["trades"] if isinstance(run["trades"], int) else run["metrics"]["trades"],
                "win_rate": run["win_rate"],
                "profit_factor": run["profit_factor"],
                "maximum_drawdown": run["maximum_drawdown"],
                "volatility": run["volatility"],
                "risk_adjusted_return": run["risk_adjusted_return"],
            }
        splits[period.name] = {
            "period": period.public(),
            "pooled": pooled,
            "markets": markets,
            "comparison": _compare(pooled),
        }

    headline_split = splits.get(HEADLINE_SPLIT) or next(iter(splits.values()), {})
    pooled = headline_split.get("pooled") or {}
    grok = pooled.get("GROK") or {}
    bench = pooled.get(BENCHMARK_NAME) or {}
    comparison_table = []
    for name in ("GROK", "BUY_AND_HOLD", "SIMPLE_TECHNICAL", "RANDOM_BASELINE"):
        row = pooled.get(name) or {}
        comparison_table.append(
            {
                "strategy": name,
                "return_pct": row.get("return_pct", 0.0),
                "ending_balance": row.get("ending_balance", STARTING_CASH),
                "absolute_pnl": row.get("absolute_pnl", 0.0),
                "maximum_drawdown": row.get("maximum_drawdown", 0.0),
                "trades": row.get("trades", 0),
                "win_rate": row.get("win_rate", 0.0),
                "profit_factor": row.get("profit_factor", 0.0),
                "volatility": row.get("volatility", 0.0),
                "risk_adjusted_return": row.get("risk_adjusted_return", 0.0),
            }
        )
    verdict = headline_split.get("comparison") or {}
    compact_runs = []
    for run in runs:
        compact_runs.append(
            {
                "strategy": run["strategy"],
                "symbol": run["symbol"],
                "split": run["split"],
                "metrics": run["metrics"],
                "signals": run["signals"],
                "ai_decisions": run["ai_decisions"],
                "trades": [
                    {
                        "symbol": t.get("symbol"),
                        "quantity": t.get("quantity"),
                        "average_entry": t.get("average_entry"),
                        "realised_pnl": t.get("realised_pnl"),
                        "entry_timestamp": t.get("entry_timestamp"),
                        "exit_timestamp": t.get("exit_timestamp"),
                    }
                    for t in (run.get("closed") or [])
                ],
                "look_ahead": run["look_ahead"],
                "broker_submit_calls": 0,
            }
        )
    return {
        "ok": True,
        "banner": BANNER,
        "live": False,
        "broker": "NOT USED",
        "broker_submit_calls": 0,
        "live_trading_allowed": False,
        "grok_model": grok_model,
        "headline_split": HEADLINE_SPLIT,
        "starting_conditions": conditions,
        "headline": {
            "split": HEADLINE_SPLIT,
            "grok_return_pct": grok.get("return_pct", 0.0),
            "benchmark_return_pct": bench.get("return_pct", 0.0),
            "benchmark_name": BENCHMARK_NAME,
            "maximum_drawdown": grok.get("maximum_drawdown", 0.0),
            "trades": grok.get("trades", 0),
            "win_rate": grok.get("win_rate", 0.0),
            "profit_factor": grok.get("profit_factor", 0.0),
            "grok_model": grok_model,
        },
        "comparison": comparison_table,
        "verdict": verdict,
        "splits": splits,
        "runs": compact_runs,
        "notes": (
            "Same £100, same fills, same risk limits, same simulated tape. "
            "Grok is one paper decision per series. Strategies were not fitted to these paths. "
            "Out-of-sample is the score that counts."
        ),
    }


def run_benchmark(
    *,
    grok_analyst: Optional[Analyst] = None,
    symbols: Optional[Iterable[str]] = None,
    periods: Optional[Iterable[BenchmarkPeriod]] = None,
    risk: Optional[RiskEngine] = None,
    starting_cash: float = STARTING_CASH,
) -> dict[str, Any]:
    """Walk every strategy over every market and split. Paper only."""
    if LIVE_TRADING_ALLOWED:
        raise RuntimeError("Benchmark refused: live trading flag must stay False.")
    analyst = grok_analyst or FixtureAnalyst()
    grok_ready = (
        getattr(analyst, "name", "") == "grok"
        and bool(getattr(analyst, "paper_requested", False))
        and bool(analyst.is_configured())
    )
    grok_model = "real Grok" if grok_ready else "fixture-hold"
    markets = tuple(symbols or DEFAULT_MARKETS)
    used_periods = tuple(periods or DEFAULT_PERIODS)
    engine = risk or RiskEngine(allow_orders=False, limits=RiskLimits())
    if engine.allow_orders:
        raise RuntimeError("Benchmark refused: allow_orders must stay False.")
    conditions = _starting_conditions(engine.limits)
    conditions["starting_cash"] = starting_cash
    runs: list[dict[str, Any]] = []
    for period in used_periods:
        for symbol in markets:
            series = load_series(symbol, period)
            sources = _make_sources(analyst)
            for name, source in sources.items():
                result = run_one(
                    series,
                    source,
                    risk=engine,
                    starting_cash=starting_cash,
                )
                result["split"] = period.name
                result["period"] = period.public()
                result["starting_conditions"] = conditions
                runs.append(result)
    report = build_public_report(
        runs,
        grok_model=grok_model,
        periods=used_periods,
        conditions=conditions,
    )
    report["run_count"] = len(runs)
    return report
