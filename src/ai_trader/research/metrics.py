"""Performance measures and the statistics that say whether they mean anything."""

from __future__ import annotations

import math
import random
from statistics import NormalDist

from ai_trader.research.backtest import Result

_N = NormalDist()


def _mean(x):
    return sum(x) / len(x) if x else 0.0


def _std(x):
    if len(x) < 2:
        return 0.0
    m = _mean(x)
    return math.sqrt(sum((v - m) ** 2 for v in x) / (len(x) - 1))


def sharpe(rets, per_year: float) -> float:
    sd = _std(rets)
    return _mean(rets) / sd * math.sqrt(per_year) if sd > 0 else 0.0


def max_drawdown(equity) -> float:
    peak, dd = 1.0, 0.0
    for e in equity:
        peak = max(peak, e)
        dd = max(dd, 1 - e / peak)
    return dd


def same_exposure_excess(res: Result) -> list[float]:
    """Strategy return minus buy-and-hold held at the strategy's own average exposure.

    Separates timing from simply owning the asset. The comparator pays no
    costs, which is generous to it.
    """
    avg = _mean(res.exposure)
    return [r - avg * a for r, a in zip(res.bar_returns, res.asset_ret)]


def block_bootstrap(x, *, block: int = 20, n: int = 2000, seed: int = 11) -> dict:
    """Circular block bootstrap of the mean: 95% interval and one-sided p(mean <= 0)."""
    if len(x) < block * 2:
        return {"mean": _mean(x), "lo": None, "hi": None, "p_le_zero": 1.0}
    rng, L, means = random.Random(seed), len(x), []
    for _ in range(n):
        tot = cnt = 0
        while cnt < L:
            s = rng.randrange(L)
            for j in range(min(block, L - cnt)):
                tot += x[(s + j) % L]
            cnt += block
        means.append(tot / L)
    means.sort()
    return {"mean": _mean(x), "lo": means[int(0.025 * n)], "hi": means[int(0.975 * n) - 1],
            "p_le_zero": sum(1 for m in means if m <= 0) / n}


def deflated_sharpe(rets, trial_sharpes: list[float]) -> float:
    """Bailey & Lopez de Prado: P(true Sharpe > 0) after allowing for the number of
    configurations tried. ``trial_sharpes`` are per-bar Sharpes of every trial."""
    T, sd = len(rets), _std(rets)
    if T < 30 or sd == 0:
        return 0.0
    m = _mean(rets)
    sr = m / sd
    skew = _mean([((r - m) / sd) ** 3 for r in rets])
    kurt = _mean([((r - m) / sd) ** 4 for r in rets])
    k = max(2, len(trial_sharpes))
    v = _std(trial_sharpes) if len(trial_sharpes) > 1 else 0.0
    g = 0.5772156649
    sr0 = v * ((1 - g) * _N.inv_cdf(1 - 1 / k) + g * _N.inv_cdf(1 - 1 / (k * math.e)))
    denom = 1 - skew * sr + (kurt - 1) / 4 * sr * sr
    if denom <= 0:
        return 0.0
    return _N.cdf((sr - sr0) * math.sqrt(T - 1) / math.sqrt(denom))


def summarise(res: Result, per_year: float) -> dict:
    rets = res.bar_returns
    if not rets:
        return {"bars": 0}
    wins = [t for t in res.trades if t > 0]
    losses = [t for t in res.trades if t <= 0]
    streak = worst = 0
    for t in res.trades:
        streak = streak + 1 if t <= 0 else 0
        worst = max(worst, streak)
    downside = math.sqrt(_mean([min(r, 0.0) ** 2 for r in rets]))
    years = len(rets) / per_year
    final = res.equity[-1]
    asset = 1.0
    for a in res.asset_ret:
        asset *= 1 + a
    return {
        "bars": len(rets),
        "years": round(years, 2),
        "net_return": final - 1,
        "cagr": final ** (1 / years) - 1 if final > 0 and years > 0 else -1.0,
        "max_drawdown": max_drawdown(res.equity),
        "sharpe": sharpe(rets, per_year),
        "sortino": _mean(rets) / downside * math.sqrt(per_year) if downside > 0 else 0.0,
        "exposure": _mean(res.exposure),
        "trades": len(res.trades),
        "open_trade": res.open_trade,
        "orders": res.orders,
        "win_rate": len(wins) / len(res.trades) if res.trades else None,
        "avg_win": _mean(wins) if wins else None,
        "avg_loss": _mean(losses) if losses else None,
        "profit_factor": (sum(wins) / -sum(losses)) if losses and sum(losses) < 0 else None,
        "expectancy": _mean(res.trades) if res.trades else None,
        "max_consecutive_losses": worst,
        "fees": res.fees,
        "slippage": res.slippage,
        "turnover": res.turnover,
        "asset_return": asset - 1,
        "asset_max_drawdown": max_drawdown(_cum(res.asset_ret)),
    }


def _cum(rets):
    out, e = [], 1.0
    for r in rets:
        e *= 1 + r
        out.append(e)
    return out
