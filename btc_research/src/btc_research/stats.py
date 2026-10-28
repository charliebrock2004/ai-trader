"""Small-sample-safe descriptive statistics. No third-party numerical library.

p-values use a normal approximation. Sample sizes in this study are large
enough that this is close to a Student-t p-value; the report says so.
"""

from __future__ import annotations

import math
from typing import Sequence


def mean(values: Sequence[float]) -> float:
    if not values:
        raise ValueError("mean of empty sample")
    return math.fsum(values) / len(values)


def variance(values: Sequence[float], *, sample: bool = True) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    mu = mean(values)
    total = math.fsum((value - mu) ** 2 for value in values)
    return total / (n - 1 if sample else n)


def stdev(values: Sequence[float], *, sample: bool = True) -> float:
    return math.sqrt(variance(values, sample=sample))


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def two_sided_p(statistic: float) -> float:
    return max(0.0, min(1.0, 2.0 * (1.0 - norm_cdf(abs(statistic)))))


def newey_west_mean_se(values: Sequence[float], lags: int | None = None) -> float:
    """Standard error of the sample mean with a Bartlett HAC kernel.

    Falls back to the iid standard error if the kernel estimate is not positive.
    """
    n = len(values)
    if n < 2:
        return float("inf")
    mu = mean(values)
    centred = [value - mu for value in values]
    if lags is None:
        lags = int(math.floor(4.0 * (n / 100.0) ** (2.0 / 9.0)))
    lags = max(0, min(lags, n - 2))
    gamma0 = math.fsum(value * value for value in centred) / n
    acc = gamma0
    for lag in range(1, lags + 1):
        weight = 1.0 - lag / (lags + 1.0)
        gamma = math.fsum(centred[i] * centred[i - lag] for i in range(lag, n)) / n
        acc += 2.0 * weight * gamma
    if acc <= 0:
        acc = gamma0
    return math.sqrt(acc / n)


def iid_mean_se(values: Sequence[float]) -> float:
    n = len(values)
    if n < 2:
        return float("inf")
    return stdev(values) / math.sqrt(n)


def mean_test(values: Sequence[float]) -> dict[str, float | int | None]:
    n = len(values)
    if n == 0:
        return {
            "n": 0,
            "mean": None,
            "stdev": None,
            "se_iid": None,
            "se_hac": None,
            "t_hac": None,
            "p_hac": None,
            "ci95_low": None,
            "ci95_high": None,
        }
    mu = mean(values)
    se = newey_west_mean_se(values)
    if se == float("inf") or se == 0:
        t_stat = 0.0 if mu == 0 else math.copysign(1e9, mu)
        p_value = 1.0 if mu == 0 else 0.0
        low = high = mu
    else:
        t_stat = mu / se
        p_value = two_sided_p(t_stat)
        low = mu - 1.96 * se
        high = mu + 1.96 * se
    return {
        "n": n,
        "mean": mu,
        "stdev": stdev(values) if n > 1 else 0.0,
        "se_iid": iid_mean_se(values) if n > 1 else None,
        "se_hac": None if se == float("inf") else se,
        "t_hac": t_stat,
        "p_hac": p_value,
        "ci95_low": low,
        "ci95_high": high,
    }


def lag1_autocorr(values: Sequence[float]) -> float:
    n = len(values)
    if n < 3:
        return 0.0
    mu = mean(values)
    denominator = math.fsum((value - mu) ** 2 for value in values)
    if denominator == 0:
        return 0.0
    numerator = math.fsum((values[i] - mu) * (values[i - 1] - mu) for i in range(1, n))
    return max(-0.99, min(0.99, numerator / denominator))


def effective_sample_size(n: int, rho: float) -> float:
    """Dependence adjustment that never *increases* the sample size.

    Positive autocorrelation reduces n_eff. Negative autocorrelation is not
    allowed to raise confidence: n_eff is capped at n.
    """
    if n <= 1:
        return float(max(n, 0))
    rho = max(-0.99, min(0.99, rho))
    if rho <= 0:
        return float(n)
    return max(1.0, n * (1.0 - rho) / (1.0 + rho))


def quantile(sorted_values: Sequence[float], q: float) -> float:
    if not sorted_values:
        raise ValueError("quantile of empty sample")
    if q <= 0:
        return sorted_values[0]
    if q >= 1:
        return sorted_values[-1]
    position = q * (len(sorted_values) - 1)
    low = int(math.floor(position))
    high = int(math.ceil(position))
    if low == high:
        return sorted_values[low]
    weight = position - low
    return sorted_values[low] * (1 - weight) + sorted_values[high] * weight


def winsorize(values: Sequence[float], lower: float = 0.01, upper: float = 0.99) -> list[float]:
    if len(values) < 10:
        return list(values)
    ordered = sorted(values)
    lo = quantile(ordered, lower)
    hi = quantile(ordered, upper)
    return [min(hi, max(lo, value)) for value in values]


def max_drawdown(equity: Sequence[float]) -> dict[str, float | int | None]:
    """Maximum peak-to-trough decline as a positive fraction."""
    if not equity:
        return {"max_drawdown": None, "peak_index": None, "trough_index": None}
    peak = equity[0]
    peak_index = 0
    best = 0.0
    best_peak = 0
    best_trough = 0
    for index, value in enumerate(equity):
        if value > peak:
            peak = value
            peak_index = index
        if peak > 0:
            drawdown = (peak - value) / peak
            if drawdown > best:
                best = drawdown
                best_peak = peak_index
                best_trough = index
    return {"max_drawdown": best, "peak_index": best_peak, "trough_index": best_trough}


def equity_returns(equity: Sequence[float]) -> list[float]:
    returns: list[float] = []
    for previous, current in zip(equity, equity[1:]):
        if previous <= 0:
            continue
        returns.append(current / previous - 1.0)
    return returns


def sharpe_ratio(returns: Sequence[float], periods_per_year: float) -> float | None:
    if len(returns) < 2 or periods_per_year <= 0:
        return None
    sigma = stdev(returns)
    if sigma == 0:
        return None
    return mean(returns) / sigma * math.sqrt(periods_per_year)


def cagr(equity: Sequence[float], elapsed_ms: int) -> float | None:
    if len(equity) < 2 or equity[0] <= 0 or equity[-1] <= 0 or elapsed_ms <= 0:
        return None
    years = elapsed_ms / (365.25 * 24.0 * 3600.0 * 1000.0)
    if years < 1.0 / 365.25:
        return None
    try:
        return (equity[-1] / equity[0]) ** (1.0 / years) - 1.0
    except OverflowError:
        return None


def benjamini_hochberg(p_values: Sequence[float]) -> list[float]:
    """BH adjusted p-values, monotone, capped at 1."""
    m = len(p_values)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda index: (p_values[index], index))
    adjusted = [1.0] * m
    running = 1.0
    for rank in range(m, 0, -1):
        index = order[rank - 1]
        raw = p_values[index]
        if raw is None or raw != raw:
            value = 1.0
        else:
            value = raw * m / rank
        running = min(running, value)
        adjusted[index] = min(1.0, running)
    return adjusted


def bonferroni(p_values: Sequence[float]) -> list[float]:
    m = len(p_values)
    return [1.0 if p != p else min(1.0, p * m) for p in p_values]
