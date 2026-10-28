"""Time-of-day and day-of-week descriptions.

Every hour and every weekday is tested. Nothing here is a claim that a window
is tradable. Net figures subtract a full round trip, because a rule that is
long for one hour (or one UTC day) and flat otherwise has to enter and exit.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Sequence

from btc_research.costs import CostModel
from btc_research.data.candles import Candle
from btc_research.stats import benjamini_hochberg, bonferroni, mean, mean_test, winsorize

WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def _utc(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def hourly_open_to_close(
    candles: Sequence[Candle], interval_ms: int
) -> dict[int, list[float]]:
    """Open-to-close return of each bar, dropped when the previous bar is missing."""
    buckets: dict[int, list[float]] = {hour: [] for hour in range(24)}
    for index in range(1, len(candles)):
        current = candles[index]
        previous = candles[index - 1]
        if current.open_time_ms - previous.open_time_ms != interval_ms:
            continue
        if current.open <= 0:
            continue
        buckets[_utc(current.open_time_ms).hour].append(current.close / current.open - 1.0)
    return buckets


def weekday_open_to_next_open(
    candles: Sequence[Candle], interval_ms: int
) -> dict[int, list[float]]:
    """Return of holding from a UTC day's first open to the next day's first open.

    Days without 24 contiguous hourly bars are dropped. Nothing is filled in.
    """
    by_day: dict[str, list[Candle]] = {}
    for candle in candles:
        key = _utc(candle.open_time_ms).strftime("%Y-%m-%d")
        by_day.setdefault(key, []).append(candle)
    keys = sorted(by_day)
    buckets: dict[int, list[float]] = {day: [] for day in range(7)}
    for key, nxt in zip(keys, keys[1:]):
        bars = by_day[key]
        nxt_bars = by_day[nxt]
        if len(bars) != 24 or not nxt_bars:
            continue
        if bars[-1].open_time_ms - bars[0].open_time_ms != 23 * interval_ms:
            continue
        if nxt_bars[0].open_time_ms - bars[0].open_time_ms != 24 * interval_ms:
            continue
        if bars[0].open <= 0:
            continue
        gross = nxt_bars[0].open / bars[0].open - 1.0
        weekday = datetime.strptime(key, "%Y-%m-%d").weekday()
        buckets[weekday].append(gross)
    return buckets


def _row(name: str, gross: list[float], cost: CostModel, family: str) -> dict[str, object]:
    test = mean_test(gross)
    net = [cost.round_trip_drag(value) for value in gross]
    net_test = mean_test(net)
    robust = mean(winsorize(gross)) if len(gross) >= 10 else test["mean"]
    return {
        "family": family,
        "name": name,
        "n": test["n"],
        "mean_gross": test["mean"],
        "se_hac_gross": test["se_hac"],
        "t_hac": test["t_hac"],
        "p_hac": test["p_hac"],
        "ci95_low_gross": test["ci95_low"],
        "ci95_high_gross": test["ci95_high"],
        "mean_gross_winsor_1_99": robust,
        "mean_net_of_round_trip": net_test["mean"],
        "ci95_low_net": net_test["ci95_low"],
        "ci95_high_net": net_test["ci95_high"],
        "p_hac_net": net_test["p_hac"],
    }


def describe_calendar(
    candles: Sequence[Candle], interval_ms: int, cost: CostModel
) -> dict[str, list[dict[str, object]]]:
    hours = [
        _row(f"hour_{hour:02d}utc", values, cost, "hour")
        for hour, values in hourly_open_to_close(candles, interval_ms).items()
    ]
    days = [
        _row(WEEKDAY_NAMES[day], values, cost, "weekday")
        for day, values in weekday_open_to_next_open(candles, interval_ms).items()
    ]
    for family_rows in (hours, days):
        raw = [float(row["p_hac"]) if row["p_hac"] is not None else 1.0 for row in family_rows]
        bh = benjamini_hochberg(raw)
        bonf = bonferroni(raw)
        for row, adj_bh, adj_bonf in zip(family_rows, bh, bonf):
            row["p_bh"] = adj_bh
            row["p_bonferroni"] = adj_bonf
            row["reject_bh_05"] = adj_bh < 0.05
            row["reject_bonferroni_05"] = adj_bonf < 0.05
    return {"hours": hours, "weekdays": days}
