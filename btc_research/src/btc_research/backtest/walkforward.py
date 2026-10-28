"""Walk-forward evaluation.

Each fold trains on [start, train_end) and tests on [train_end, test_end).
Folds do not overlap in the test window when step == test length.

The test account always starts flat at initial equity. Indicator state may use
bars before the test window; the account may not. A fold boundary therefore
flattens inventory. That charges a strategy which would have kept holding an
extra round trip per boundary (one per test block). It is disclosed in the
report and is slightly conservative for slow signals.
"""

from __future__ import annotations

from typing import Sequence

from btc_research.backtest.engine import run_backtest
from btc_research.backtest.metrics import summarise
from btc_research.costs import ExecutionConfig
from btc_research.data.candles import Candle
from btc_research.stats import equity_returns


def fold_bounds(n_bars: int, train_bars: int, test_bars: int, step_bars: int) -> list[tuple[int, int, int]]:
    if min(train_bars, test_bars, step_bars) < 1:
        raise ValueError("fold lengths must be positive")
    folds: list[tuple[int, int, int]] = []
    start = 0
    while start + train_bars + test_bars <= n_bars:
        train_end = start + train_bars
        test_end = train_end + test_bars
        folds.append((start, train_end, test_end))
        start += step_bars
    return folds


def _total_return(equity: Sequence[float], initial: float) -> float:
    if not equity or initial <= 0:
        return 0.0
    return equity[-1] / initial - 1.0


def oos_path(
    candles: Sequence[Candle],
    targets: Sequence[float],
    execution: ExecutionConfig,
    folds: Sequence[tuple[int, int, int]],
    periods_per_year: float,
) -> dict[str, object]:
    """Stitch non-overlapping test windows. Training bars are not scored."""
    initial = execution.initial_equity
    stitched = [initial]
    returns: list[float] = []
    fold_rows: list[dict[str, object]] = []
    positive = 0
    for fold_id, (_train_start, test_start, test_end) in enumerate(folds):
        result = run_backtest(candles, targets, execution, start=test_start, end=test_end)
        summary = summarise(
            result,
            candles,
            start=test_start,
            end=test_end,
            periods_per_year=periods_per_year,
            initial_equity=initial,
        )
        summary["fold"] = fold_id
        summary["test_start_index"] = test_start
        summary["test_end_index"] = test_end
        summary["test_start_ms"] = candles[test_start].open_time_ms
        summary["test_end_ms"] = candles[test_end - 1].open_time_ms
        fold_rows.append(summary)
        total = summary["total_return"]
        if isinstance(total, float) and total > 0:
            positive += 1
        for ret in equity_returns([initial, *result.equity]):
            returns.append(ret)
            stitched.append(stitched[-1] * (1.0 + ret))
    return {
        "folds": fold_rows,
        "n_folds": len(fold_rows),
        "positive_folds": positive,
        "positive_fold_fraction": (positive / len(fold_rows)) if fold_rows else None,
        "stitched_equity": stitched,
        "oos_returns": returns,
    }


def select_hour_on_train(
    hour_targets: dict[int, list[float]],
    candles: Sequence[Candle],
    execution: ExecutionConfig,
    train_start: int,
    train_end: int,
) -> tuple[int, float]:
    """Pick the UTC hour with the highest train total return. Ties take the earlier hour.

    Selection sees only the train slice of the account. It does not see the test.
    """
    best_hour = 0
    best_return = float("-inf")
    for hour in range(24):
        result = run_backtest(
            candles,
            hour_targets[hour],
            execution,
            start=train_start,
            end=train_end,
        )
        total = _total_return(result.equity, execution.initial_equity)
        if total > best_return:
            best_return = total
            best_hour = hour
    return best_hour, best_return


def adaptive_hour_oos(
    hour_targets: dict[int, list[float]],
    candles: Sequence[Candle],
    execution: ExecutionConfig,
    folds: Sequence[tuple[int, int, int]],
    periods_per_year: float,
) -> dict[str, object]:
    """Each fold trades only the hour that won on that fold's training window."""
    initial = execution.initial_equity
    stitched = [initial]
    returns: list[float] = []
    fold_rows: list[dict[str, object]] = []
    positive = 0
    chosen: list[int] = []
    for fold_id, (train_start, test_start, test_end) in enumerate(folds):
        hour, train_return = select_hour_on_train(
            hour_targets, candles, execution, train_start, test_start
        )
        chosen.append(hour)
        result = run_backtest(
            candles, hour_targets[hour], execution, start=test_start, end=test_end
        )
        summary = summarise(
            result,
            candles,
            start=test_start,
            end=test_end,
            periods_per_year=periods_per_year,
            initial_equity=initial,
        )
        summary["fold"] = fold_id
        summary["chosen_hour_utc"] = hour
        summary["train_total_return"] = train_return
        summary["test_start_ms"] = candles[test_start].open_time_ms
        summary["test_end_ms"] = candles[test_end - 1].open_time_ms
        fold_rows.append(summary)
        total = summary["total_return"]
        if isinstance(total, float) and total > 0:
            positive += 1
        for ret in equity_returns([initial, *result.equity]):
            returns.append(ret)
            stitched.append(stitched[-1] * (1.0 + ret))
    return {
        "folds": fold_rows,
        "n_folds": len(fold_rows),
        "positive_folds": positive,
        "positive_fold_fraction": (positive / len(fold_rows)) if fold_rows else None,
        "stitched_equity": stitched,
        "oos_returns": returns,
        "chosen_hours": chosen,
    }
