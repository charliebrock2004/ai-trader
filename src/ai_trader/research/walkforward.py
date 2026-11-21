"""Walk-forward selection with a holdout that selection never sees.

History is cut once, by time::

    [ warm-up | development ................................ | holdout ]

Inside development, parameters are chosen on an expanding training window and
scored on the following test fold; only the stitched test folds count as
out-of-sample. The holdout is scored once, with the parameters chosen on all
of development. Each window starts flat, so a position carried across a
boundary pays its costs again; that errs against the strategy.
"""

from __future__ import annotations

from dataclasses import dataclass

from ai_trader.research import backtest as bt
from ai_trader.research import metrics as mx
from ai_trader.research.data import Series
from ai_trader.research.strategies import Candidate


@dataclass(frozen=True)
class Plan:
    warmup: int = 200       # bars the slowest indicator needs
    min_train: int = 730    # bars before the first test fold
    fold: int = 365         # bars per test fold
    holdout_frac: float = 0.20

    def split(self, n: int) -> tuple[int, list[tuple[int, int]]]:
        """Return (holdout_start, [(test_start, test_end), ...])."""
        holdout = n - int((n - self.warmup) * self.holdout_frac)
        folds, s = [], self.warmup + self.min_train
        while s + self.fold <= holdout:
            folds.append((s, s + self.fold))
            s += self.fold
        if folds and holdout - folds[-1][1] >= self.fold // 2:
            folds.append((folds[-1][1], holdout))
        return holdout, folds


def _select(series: Series, all_w: list[list[float]], costs: bt.Costs, a: int, b: int) -> int:
    """Index of the grid point with the best net Sharpe on [a, b). First wins ties."""
    best, best_s = 0, float("-inf")
    for i, w in enumerate(all_w):
        s = mx.sharpe(bt.run(series, w, costs, start=a, end=b).bar_returns, series.bars_per_year)
        if s > best_s:
            best, best_s = i, s
    return best


def evaluate(series: Series, cand: Candidate, plan: Plan = Plan(), *, costs: bt.Costs = bt.BASE_COSTS,
             stress: bt.Costs = bt.STRESS_COSTS, use_holdout: bool = False) -> dict:
    n, py = len(series), series.bars_per_year
    holdout, folds = plan.split(n)
    if not folds:
        raise ValueError(f"{series.symbol}: not enough history for one walk-forward fold")
    all_w = [cand.weights(series, p) for p in cand.grid]

    parts, parts_stress, parts_gross, fold_rows = [], [], [], []
    for a, b in folds:
        i = _select(series, all_w, costs, plan.warmup, a)
        r = bt.run(series, all_w[i], costs, start=a, end=b)
        parts.append(r)
        parts_stress.append(bt.run(series, all_w[i], stress, start=a, end=b))
        parts_gross.append(bt.run(series, all_w[i], bt.ZERO_COSTS, start=a, end=b))
        fold_rows.append({"start": series.ts[a], "end": series.ts[b - 1], "params": cand.grid[i],
                          "net_return": r.equity[-1] - 1, "asset_return": mx.summarise(r, py)["asset_return"],
                          "trades": len(r.trades)})
    oos = bt.stitch(parts)

    # Development-wide view of the whole grid: how fragile is the choice of parameters?
    dev = [bt.run(series, w, costs, start=plan.warmup, end=holdout) for w in all_w]
    dev_net = [d.equity[-1] - 1 for d in dev]
    trial_sr = [mx.sharpe(d.bar_returns, 1.0) for d in dev]

    out = {
        "candidate": cand.name, "family": cand.family, "symbol": series.symbol, "interval": series.interval,
        "grid_size": len(cand.grid),
        "oos": mx.summarise(oos, py),
        "oos_stress": mx.summarise(bt.stitch(parts_stress), py),
        "oos_gross_return": bt.stitch(parts_gross).equity[-1] - 1,
        "oos_excess": mx.block_bootstrap(mx.same_exposure_excess(oos)),
        "folds": fold_rows,
        "folds_positive": sum(1 for f in fold_rows if f["net_return"] > 0) / len(fold_rows),
        "folds_traded": sum(1 for f in fold_rows if f["trades"] > 0) / len(fold_rows),
        "grid_positive": sum(1 for v in dev_net if v > 0) / len(dev_net),
        "grid_net_min": min(dev_net), "grid_net_max": max(dev_net),
        "trial_sharpes": trial_sr,
        "holdout": None,
    }
    if use_holdout:
        i = _select(series, all_w, costs, plan.warmup, holdout)
        h = bt.run(series, all_w[i], costs, start=holdout, end=n)
        out["holdout"] = {"params": cand.grid[i], "start": series.ts[holdout], "end": series.ts[n - 1],
                          **mx.summarise(h, py), "excess": mx.block_bootstrap(mx.same_exposure_excess(h))}
    return out
