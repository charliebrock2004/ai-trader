"""Run the pre-registered BTC study and write the bundle.

`evaluate_candles` is pure: same candles, same config, same result.
`run_study` downloads or reads the cache, then calls `evaluate_candles`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from btc_research.backtest.engine import run_backtest
from btc_research.backtest.metrics import summarise
from btc_research.backtest.walkforward import adaptive_hour_oos, fold_bounds, oos_path
from btc_research.costs import CostModel, ExecutionConfig, sensitivity_cost
from btc_research.data.binance import load_or_download
from btc_research.data.candles import INTERVAL_MS, Candle
from btc_research.data.validate import validate_candles
from btc_research.report.writer import write_bundle
from btc_research.research.bayesian import estimate_positive_edge
from btc_research.research.temporal import describe_calendar
from btc_research.sizing.kelly import fractional_kelly
from btc_research.stats import equity_returns, max_drawdown, mean, mean_test, sharpe_ratio, variance
from btc_research.strategies.baselines import CANDIDATE_NAMES, default_strategies
from btc_research.strategies.base import Strategy

SCHEMA_VERSION = "btc-research-summary/v1"
DISCLAIMER = (
    "Historical backtest only. This is not paper-trading performance and not live performance. "
    "It is not a recommendation to trade. Costs, fills and the strategy list are assumptions."
)
GATE_DESCRIPTION = (
    "A candidate passes only if all of the following are true on the primary cost model: "
    "at least 5 walk-forward test folds; at least 60% of those folds have a positive net return; "
    "the Newey-West 95% interval for the mean out-of-sample bar return lies entirely above zero; "
    "the skeptical Bayesian probability that the out-of-sample mean is positive is at least 0.95; "
    "the out-of-sample HAC p-value survives a Bonferroni correction across the 9-candidate family; "
    "and the single final 30% holdout also has a positive total return whose HAC 95% interval lies above zero. "
    "Buy-and-hold is the benchmark and is not in the family. Failing the gate does not prove that no edge exists."
)
MIN_FOLDS = 5
MIN_POSITIVE_FOLD_FRACTION = 0.60
MIN_BAYES = 0.95
HOLDOUT_FRACTION = 0.30
KELLY_CAP = 0.25

REGIMES = (
    ("2019_to_covid", "2019-01-01", "2020-03-12"),
    ("covid_to_ath", "2020-03-12", "2021-11-10"),
    ("bear_to_ftx", "2021-11-10", "2022-11-21"),
    ("ftx_to_etf", "2022-11-21", "2024-01-11"),
    ("post_etf", "2024-01-11", None),
)


@dataclass(frozen=True)
class StudyConfig:
    symbol: str = "BTCUSDT"
    interval: str = "1h"
    start: str = "2019-01-01"
    end: str | None = None
    cache_path: Path = Path("data/cache/BTCUSDT_1h.csv")
    out_dir: Path = Path("outputs/latest")
    execution: ExecutionConfig = ExecutionConfig()
    train_bars: int = 24 * 180
    test_bars: int = 24 * 90
    step_bars: int = 24 * 90


def _iso(ms: int | None) -> str | None:
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_ms(text: str) -> int:
    stamp = datetime.fromisoformat(text)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return int(stamp.timestamp() * 1000)


def _downsample_pairs(times: Sequence[int], values: Sequence[float], points: int = 360) -> list[dict[str, object]]:
    n = len(values)
    if n == 0:
        return []
    if n <= points:
        indexes = range(n)
    else:
        last = n - 1
        indexes = []
        seen: set[int] = set()
        for step in range(points):
            index = round(last * step / (points - 1))
            if index not in seen:
                seen.add(index)
                indexes.append(index)
    peak = values[0]
    drawdowns = []
    for value in values:
        peak = value if value > peak else peak
        drawdowns.append(0.0 if peak <= 0 else (peak - value) / peak)
    return [
        {"t": _iso(times[index]), "equity": values[index], "drawdown": drawdowns[index]}
        for index in indexes
    ]


def _path_stats(stitched: Sequence[float], periods_per_year: float) -> dict[str, object]:
    returns = equity_returns(stitched)
    test = mean_test(returns)
    total = None
    if stitched and stitched[0] > 0:
        total = stitched[-1] / stitched[0] - 1.0
    return {
        "oos_total_return": total,
        "oos_max_drawdown": max_drawdown(stitched)["max_drawdown"],
        "oos_sharpe": sharpe_ratio(returns, periods_per_year),
        "oos_mean_bar_return": test["mean"],
        "oos_se_hac": test["se_hac"],
        "oos_p_hac": test["p_hac"],
        "oos_ci95_low": test["ci95_low"],
        "oos_ci95_high": test["ci95_high"],
        "oos_returns": returns,
    }


def _slice_for_regime(candles: Sequence[Candle], start_text: str, end_text: str | None) -> tuple[int, int] | None:
    start_ms = _parse_ms(start_text)
    end_ms = _parse_ms(end_text) if end_text else None
    first = None
    last = None
    for index, candle in enumerate(candles):
        if candle.open_time_ms < start_ms:
            continue
        if end_ms is not None and candle.open_time_ms >= end_ms:
            break
        if first is None:
            first = index
        last = index
    if first is None or last is None or last <= first:
        return None
    return first, last + 1


def _kelly_from_result(result, initial: float) -> float:
    returns = equity_returns([initial, *result.equity])
    kwargs: dict[str, float] = {}
    if len(returns) >= 2:
        kwargs["mean_return"] = mean(returns)
        kwargs["variance"] = variance(returns)
    wins = [trip.pnl for trip in result.round_trips if trip.pnl > 0]
    losses = [trip.pnl for trip in result.round_trips if trip.pnl < 0]
    if wins and losses and result.round_trips:
        kwargs["win_probability"] = len(wins) / len(result.round_trips)
        kwargs["avg_win"] = mean(wins)
        kwargs["avg_loss"] = mean(losses)
    if "mean_return" not in kwargs and "win_probability" not in kwargs:
        return 0.0
    return fractional_kelly(kelly_fraction=0.25, max_exposure=KELLY_CAP, **kwargs).fraction


def _hours(candles: Sequence[Candle]) -> list[int]:
    return [datetime.fromtimestamp(candle.open_time_ms / 1000, tz=timezone.utc).hour for candle in candles]


def evaluate_candles(
    candles: Sequence[Candle],
    *,
    execution: ExecutionConfig,
    interval_ms: int,
    train_bars: int,
    test_bars: int,
    step_bars: int,
    meta: dict | None = None,
    out_dir: Path | None = None,
    generated_at: datetime | None = None,
) -> dict:
    """Evaluate the pre-registered family. Does not download anything."""
    meta = dict(meta or {})
    n = len(candles)
    periods = (365.25 * 24.0 * 3600.0 * 1000.0) / interval_ms
    strategies: list[Strategy] = default_strategies(interval_ms)  # type: ignore[assignment]
    targets = {strategy.name: strategy.targets(candles) for strategy in strategies}
    hours = _hours(candles)
    hour_targets = {
        hour: [1.0 if value == hour else 0.0 for value in hours] for hour in range(24)
    }
    folds = fold_bounds(n, train_bars, test_bars, step_bars)
    holdout_start = int(n * (1.0 - HOLDOUT_FRACTION))
    if holdout_start < 2 or holdout_start >= n - 2:
        holdout_start = max(1, n // 2)

    sensitivity = ExecutionConfig(
        cost=sensitivity_cost(),
        initial_equity=execution.initial_equity,
        max_exposure=execution.max_exposure,
    )

    full_rows = []
    equity: dict[str, list[float]] = {}
    for strategy in strategies:
        result = run_backtest(candles, targets[strategy.name], execution)
        row = summarise(
            result, candles, start=0, end=n, periods_per_year=periods, initial_equity=execution.initial_equity
        )
        sens = run_backtest(candles, targets[strategy.name], sensitivity)
        row["name"] = strategy.name
        row["role"] = "benchmark" if strategy.name == "buy_and_hold" else "candidate"
        row["sensitivity_total_return"] = (
            sens.equity[-1] / execution.initial_equity - 1.0 if sens.equity else None
        )
        full_rows.append(row)
        equity[strategy.name] = [execution.initial_equity, *result.equity]

    oos_by_name: dict[str, dict] = {}
    fold_records: list[dict] = []
    for strategy in strategies:
        if strategy.name == "buy_and_hold":
            continue
        path = oos_path(candles, targets[strategy.name], execution, folds, periods)
        oos_by_name[strategy.name] = path
        for fold in path["folds"]:
            fold_records.append({"name": strategy.name, **fold})  # type: ignore[arg-type]

    adaptive = adaptive_hour_oos(hour_targets, candles, execution, folds, periods)
    oos_by_name["adaptive_best_hour"] = adaptive
    for fold in adaptive["folds"]:
        fold_records.append({"name": "adaptive_best_hour", **fold})  # type: ignore[arg-type]

    holdout_by_name: dict[str, dict] = {}
    for strategy in strategies:
        result = run_backtest(candles, targets[strategy.name], execution, start=holdout_start, end=n)
        holdout_by_name[strategy.name] = summarise(
            result,
            candles,
            start=holdout_start,
            end=n,
            periods_per_year=periods,
            initial_equity=execution.initial_equity,
        )
    chosen_holdout_hour, _train_return = (0, 0.0)
    if holdout_start > 10:
        from btc_research.backtest.walkforward import select_hour_on_train

        chosen_holdout_hour, _train_return = select_hour_on_train(
            hour_targets, candles, execution, 0, holdout_start
        )
    adaptive_holdout = run_backtest(
        candles, hour_targets[chosen_holdout_hour], execution, start=holdout_start, end=n
    )
    holdout_by_name["adaptive_best_hour"] = summarise(
        adaptive_holdout,
        candles,
        start=holdout_start,
        end=n,
        periods_per_year=periods,
        initial_equity=execution.initial_equity,
    )
    holdout_by_name["adaptive_best_hour"]["chosen_hour_utc"] = chosen_holdout_hour

    family_m = len(CANDIDATE_NAMES)
    ranking = []
    bayesian_rows = []
    for name in ("buy_and_hold", *CANDIDATE_NAMES):
        if name == "buy_and_hold":
            path = oos_path(candles, targets["buy_and_hold"], execution, folds, periods)
        else:
            path = oos_by_name[name]
        stats = _path_stats(path["stitched_equity"], periods)  # type: ignore[arg-type]
        bayes = estimate_positive_edge(stats["oos_returns"], periods_per_year=periods)  # type: ignore[arg-type]
        holdout = holdout_by_name[name]
        p_hac = stats["oos_p_hac"]
        ci_low = stats["oos_ci95_low"]
        holdout_low = holdout.get("ci95_low")
        holdout_total = holdout.get("total_return")
        passes = False
        if name != "buy_and_hold":
            passes = bool(
                path["n_folds"] >= MIN_FOLDS
                and path["positive_fold_fraction"] is not None
                and path["positive_fold_fraction"] >= MIN_POSITIVE_FOLD_FRACTION
                and isinstance(ci_low, float)
                and ci_low > 0
                and isinstance(bayes.get("p_mean_positive"), float)
                and bayes["p_mean_positive"] >= MIN_BAYES
                and isinstance(p_hac, float)
                and p_hac * family_m < 0.05
                and isinstance(holdout_total, float)
                and holdout_total > 0
                and isinstance(holdout_low, float)
                and holdout_low > 0
            )
        ranking.append(
            {
                "name": name,
                "role": "benchmark" if name == "buy_and_hold" else "candidate",
                "n_folds": path["n_folds"],
                "positive_fold_fraction": path["positive_fold_fraction"],
                "oos_total_return": stats["oos_total_return"],
                "oos_max_drawdown": stats["oos_max_drawdown"],
                "oos_sharpe": stats["oos_sharpe"],
                "oos_mean_bar_return": stats["oos_mean_bar_return"],
                "oos_se_hac": stats["oos_se_hac"],
                "oos_p_hac": p_hac,
                "oos_ci95_low": ci_low,
                "oos_ci95_high": stats["oos_ci95_high"],
                "p_mean_positive": bayes.get("p_mean_positive"),
                "n_eff": bayes.get("n_eff"),
                "holdout_total_return": holdout_total,
                "holdout_max_drawdown": holdout.get("max_drawdown"),
                "holdout_ci95_low": holdout_low,
                "passes_gate": passes,
            }
        )
        bayesian_rows.append({"name": name, "sample": "walk_forward_oos", **bayes})

    ranking.sort(
        key=lambda row: (
            0 if row["passes_gate"] else 1,
            0 if row["role"] == "candidate" else 1,
            -(row["oos_total_return"] if isinstance(row["oos_total_return"], float) else -1e9),
        )
    )

    sizing_rows = []
    for strategy in strategies:
        if strategy.name == "buy_and_hold":
            continue
        full = run_backtest(candles, targets[strategy.name], execution)
        in_sample_fraction = _kelly_from_result(full, execution.initial_equity)
        stitched = [execution.initial_equity]
        fractions: list[float] = []
        zeros = 0
        for train_start, test_start, test_end in folds:
            train = run_backtest(
                candles, targets[strategy.name], execution, start=train_start, end=test_start
            )
            fraction = _kelly_from_result(train, execution.initial_equity)
            fractions.append(fraction)
            if fraction <= 0:
                zeros += 1
            scaled = [min(1.0, value * fraction) for value in targets[strategy.name]]
            tested = run_backtest(candles, scaled, execution, start=test_start, end=test_end)
            level = execution.initial_equity
            for ret in equity_returns([level, *tested.equity]):
                stitched.append(stitched[-1] * (1.0 + ret))
        sized_return = stitched[-1] / stitched[0] - 1.0 if stitched[0] else None
        sizing_rows.append(
            {
                "name": strategy.name,
                "in_sample_fraction": in_sample_fraction,
                "oos_folds_zero": zeros,
                "oos_mean_fraction": (sum(fractions) / len(fractions)) if fractions else None,
                "oos_sized_total_return": sized_return,
                "max_exposure": KELLY_CAP,
                "kelly_fraction": 0.25,
                "note": "Zero when the training window does not show a positive conservative edge.",
            }
        )

    regimes = []
    for strategy in strategies:
        for regime_name, start_text, end_text in REGIMES:
            bounds = _slice_for_regime(candles, start_text, end_text)
            if bounds is None:
                continue
            result = run_backtest(candles, targets[strategy.name], execution, start=bounds[0], end=bounds[1])
            row = summarise(
                result,
                candles,
                start=bounds[0],
                end=bounds[1],
                periods_per_year=periods,
                initial_equity=execution.initial_equity,
            )
            regimes.append(
                {
                    "name": strategy.name,
                    "regime": regime_name,
                    "total_return": row["total_return"],
                    "max_drawdown": row["max_drawdown"],
                    "sharpe": row["sharpe"],
                    "n_bars": row["n_bars"],
                }
            )

    temporal = describe_calendar(candles, interval_ms, execution.cost)
    promising = [row["name"] for row in ranking if row["passes_gate"]]
    if promising:
        headline = "Some candidates passed the pre-registered out-of-sample gate. That is still a historical result, not a licence to trade."
        detail = (
            "The names listed below cleared walk-forward and holdout requirements after costs and after a Bonferroni correction. "
            "Treat that as a research flag for a later paper-trading check, not as established edge and not as live performance."
        )
        code = "oos_gate_passed"
    elif folds and len(folds) >= MIN_FOLDS:
        headline = "No candidate showed a reliable out-of-sample edge after costs."
        detail = (
            "None of the pre-registered candidates cleared the walk-forward, holdout, cost, Bayesian and multiple-testing bar. "
            "Buy-and-hold can still be up. That is exposure to BTC, not evidence that a timing rule has an edge. "
            "The evidence is insufficient to establish a trading edge."
        )
        code = "insufficient_evidence"
    else:
        headline = "The sample is too short for the pre-registered out-of-sample gate."
        detail = (
            f"This run produced {len(folds)} walk-forward folds; the gate requires {MIN_FOLDS}. "
            "Do not read the full-sample table as evidence."
        )
        code = "insufficient_sample"

    times = [candles[0].open_time_ms - interval_ms, *(candle.open_time_ms for candle in candles)]
    # equity vectors were stored with a synthetic starting point before bar 0.
    # Align the timestamp of that point one interval before the first open.
    if len(times) != len(next(iter(equity.values()))):
        times = [candles[0].open_time_ms, *(candle.open_time_ms for candle in candles[:-1])]
        # fall back to bar opens only, trimming the synthetic point if lengths differ
        if len(times) != len(next(iter(equity.values()))):
            times = [candle.open_time_ms for candle in candles]
            equity = {name: values[-len(times) :] for name, values in equity.items()}

    chart_names = ["buy_and_hold"]
    for row in ranking:
        if row["name"] == "buy_and_hold":
            continue
        if row["name"] in equity and row["name"] not in chart_names:
            chart_names.append(row["name"])
        if len(chart_names) == 4:
            break

    validation = meta.get("validation", {})
    summary = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": (generated_at or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "disclaimer": DISCLAIMER,
        "performance_classes": {
            "historical_backtest": "present",
            "paper_trading": "not_run",
            "live_trading": "not_run",
        },
        "data": {
            "symbol": meta.get("symbol"),
            "interval": meta.get("interval"),
            "source": meta.get("source"),
            "host": meta.get("host"),
            "venue": meta.get("venue", "supplied-candles"),
            "cache_hit": meta.get("cache_hit"),
            "n_input": validation.get("n_input", n),
            "n_valid": validation.get("n_valid", n),
            "n_duplicate_identical": validation.get("n_duplicate_identical", 0),
            "n_malformed": validation.get("n_malformed", 0),
            "n_gaps": validation.get("n_gaps", 0),
            "missing_bars": validation.get("missing_bars", 0),
            "start_utc": _iso(candles[0].open_time_ms) if candles else None,
            "end_utc": _iso(candles[-1].open_time_ms) if candles else None,
            "limitation": (
                "Public Binance spot klines. The forming candle is dropped. "
                "Gaps are left empty. api.binance.com may return HTTP 451 in some regions; "
                "the loader then uses data-api.binance.vision and does not invent bars. "
                "This is not a full tick history, so fills are bar-open approximations."
            ),
        },
        "costs": {
            "primary": execution.cost.to_dict(),
            "sensitivity": sensitivity.cost.to_dict(),
            "max_exposure": execution.max_exposure,
            "initial_equity": execution.initial_equity,
        },
        "gates": {
            "description": GATE_DESCRIPTION,
            "min_folds": MIN_FOLDS,
            "min_positive_fold_fraction": MIN_POSITIVE_FOLD_FRACTION,
            "min_bayes": MIN_BAYES,
            "family_size": family_m,
            "holdout_fraction": HOLDOUT_FRACTION,
            "family": list(CANDIDATE_NAMES),
        },
        "full_sample": full_rows,
        "ranking": ranking,
        "temporal": temporal,
        "bayesian": bayesian_rows,
        "sizing": sizing_rows,
        "regimes": regimes,
        "walk_forward_folds": [
            {
                "name": row.get("name"),
                "fold": row.get("fold"),
                "total_return": row.get("total_return"),
                "max_drawdown": row.get("max_drawdown"),
                "sharpe": row.get("sharpe"),
                "n_closed_trades": row.get("n_closed_trades"),
                "chosen_hour_utc": row.get("chosen_hour_utc"),
                "test_start_ms": row.get("test_start_ms"),
                "test_end_ms": row.get("test_end_ms"),
            }
            for row in fold_records
        ],
        "verdict": {
            "code": code,
            "headline": headline,
            "detail": detail,
            "promising": promising,
            "evidence_is_sufficient_for_an_edge": bool(promising),
        },
        "chart_names": chart_names,
        "curves": {
            name: _downsample_pairs(times, equity[name])
            for name in chart_names
            if name in equity
        },
    }
    if out_dir is not None:
        write_bundle(Path(out_dir), summary, times, equity)
    return summary


def run_study(config: StudyConfig | None = None) -> dict:
    """Download or reuse the cache, evaluate, and write the bundle."""
    config = config or StudyConfig()
    if config.interval not in INTERVAL_MS:
        raise ValueError(f"unsupported interval {config.interval}")
    interval_ms = INTERVAL_MS[config.interval]
    start_ms = _parse_ms(config.start)
    end_ms = _parse_ms(config.end) if config.end else int(datetime.now(timezone.utc).timestamp() * 1000)
    candles, meta = load_or_download(
        cache_path=config.cache_path,
        symbol=config.symbol,
        interval=config.interval,
        interval_ms=interval_ms,
        start_ms=start_ms,
        end_ms=end_ms,
        progress=lambda pages, count, last: print(
            f"downloaded page {pages}, bars {count}, through {_iso(last)}", flush=True
        ),
    )
    candles, report = validate_candles(list(candles), interval_ms)
    meta = dict(meta)
    meta["validation"] = report.to_dict()
    meta["symbol"] = config.symbol
    meta["interval"] = config.interval
    return evaluate_candles(
        candles,
        execution=config.execution,
        interval_ms=interval_ms,
        train_bars=config.train_bars,
        test_bars=config.test_bars,
        step_bars=config.step_bars,
        meta=meta,
        out_dir=config.out_dir,
    )
