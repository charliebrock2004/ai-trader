"""Research round 2: 4-hour bars, a drawdown brake, cross-asset rotation.

Round 1 found no qualifying strategy; its main failure was drawdown. These
candidates and grids were written on 2026-11-21, before any of them was run,
and are scored against the same unchanged gate. Round 1's candidate list and
the tournament registration are not touched.

Multiple testing is counted across both rounds: the timing test is corrected
for every candidate tried so far, not just the new ones.

Outputs are simulated historical results, never trading profits.
"""

from __future__ import annotations

import hashlib
import math
import time
from dataclasses import dataclass

from ai_trader.research import backtest as bt
from ai_trader.research import data as rd
from ai_trader.research import metrics as mx
from ai_trader.research import qualification as q
from ai_trader.research import strategies as st
from ai_trader.research import walkforward as wf
from ai_trader.research.data import Series
from ai_trader.research.strategies import Candidate, _grid

PLAN_4H = wf.Plan(warmup=1200, min_train=4380, fold=2190)  # same calendar spans as the daily plan


# --- drawdown brake ----------------------------------------------------------

def dd_brake(s: Series, base: list, limit: float, cooldown: int) -> list:
    """Go flat for ``cooldown`` bars once the rule's own (cost-free) equity is
    ``limit`` below its peak, then start again from a fresh peak.

    It caps each losing run, not the total: several stopped runs in a row can
    still add up to more than ``limit``.
    """
    out, eq, peak, halt_until = [], 1.0, 1.0, -1
    for i in range(len(s)):
        if i:
            eq *= 1 + out[i - 1] * (s.close[i] / s.close[i - 1] - 1)
        if i == halt_until:
            peak = eq
        peak = max(peak, eq)
        if i < halt_until:
            out.append(0.0)
        elif eq < peak * (1 - limit):
            halt_until = i + cooldown
            out.append(0.0)
        else:
            out.append(base[i])
    return out


def brake_ema(s: Series, fast: int, slow: int, limit: float, cooldown: int) -> list:
    return dd_brake(s, st.ema_cross(s, fast, slow), limit, cooldown)


def brake_ensemble(s: Series, target: float, limit: float, cooldown: int) -> list:
    return dd_brake(s, st.trend_ensemble(s, target), limit, cooldown)


# --- cross-asset basket ------------------------------------------------------

@dataclass(frozen=True)
class Basket:
    """Several assets on a shared clock. A weight is one tuple per bar, one entry per member."""

    symbol: str
    interval: str
    members: tuple
    ts: tuple

    @classmethod
    def of(cls, series: list) -> "Basket":
        common = set(series[0].ts).intersection(*(s.ts for s in series[1:]))
        ms = []
        for s in series:
            keep = [i for i, t in enumerate(s.ts) if t in common]
            ms.append(Series(s.symbol, s.interval, *(tuple(col[i] for i in keep)
                                                     for col in (s.ts, s.open, s.high, s.low, s.close, s.volume))))
        return cls("+".join(s.symbol for s in series), series[0].interval, tuple(ms), ms[0].ts)

    def __len__(self) -> int:
        return len(self.ts)

    @property
    def bars_per_year(self) -> float:
        return self.members[0].bars_per_year

    def prefix(self, n: int) -> "Basket":
        return Basket(self.symbol, self.interval, tuple(m.prefix(n) for m in self.members), self.ts[:n])

    def sha256(self) -> str:
        return hashlib.sha256("".join(m.sha256() for m in self.members).encode()).hexdigest()


def run_basket(b: Basket, weights: list, costs: bt.Costs = bt.BASE_COSTS, *, start: int = 1, end=None,
               band: float = 0.10, close_at_end: bool = True) -> bt.Result:
    """Same conventions as ``backtest.run``: decide on the close, trade the next open, pay costs, start flat.

    ``asset_ret`` is the equal-weight basket, so the timing test asks whether
    choosing among the assets beat simply holding all of them.
    """
    end = len(b) if end is None else end
    if len(weights) != len(b):
        raise ValueError("weights and basket differ in length")
    if start < 1:
        raise ValueError("start must leave one prior bar to decide on")
    k = len(b.members)
    res = bt.Result(start, end)
    cash, units, basis, entry_eq = 1.0, [0.0] * k, [0.0] * k, [None] * k

    def sell(i: int, qty: float, px: float, eq: float) -> None:
        nonlocal cash
        gross = qty * px * (1 - costs.slippage)
        fee = gross * costs.fee
        cash += gross - fee
        units[i] -= qty
        basis[i] -= gross - fee
        res.slippage += qty * px * costs.slippage
        res.fees += fee
        res.turnover += qty * px / eq
        res.orders += 1
        if units[i] <= 1e-12:
            units[i] = 0.0
            res.trades.append(-basis[i] / entry_eq[i])
            basis[i], entry_eq[i] = 0.0, None

    for t in range(start, end):
        o = [m.open[t] for m in b.members]
        tgt = [min(1.0, max(0.0, w)) for w in weights[t - 1]]
        if sum(tgt) > 1.0:
            tgt = [w / sum(tgt) for w in tgt]
        eq = cash + sum(u * p for u, p in zip(units, o))
        delta = [0.0] * k
        for i in range(k):
            cur = units[i] * o[i] / eq if eq > 0 else 0.0
            if (tgt[i] == 0.0 and units[i] > 0) or (units[i] == 0 and tgt[i] > 0) or abs(tgt[i] - cur) > band:
                delta[i] = tgt[i] * eq - units[i] * o[i]
        for i in range(k):  # sells first, so the cash is there for the buys
            if delta[i] < 0:
                sell(i, units[i] if tgt[i] == 0.0 else min(units[i], -delta[i] / o[i]), o[i], eq)
        for i in range(k):
            if delta[i] > 0:
                spend = min(delta[i], cash)
                if spend <= 0:
                    continue
                fee = spend * costs.fee
                qty = (spend - fee) / (o[i] * (1 + costs.slippage))
                if units[i] == 0:
                    entry_eq[i] = eq
                units[i] += qty
                cash -= spend
                basis[i] += spend
                res.slippage += qty * o[i] * costs.slippage
                res.fees += fee
                res.turnover += spend / eq
                res.orders += 1
        c = [m.close[t] for m in b.members]
        held = sum(u * p for u, p in zip(units, c))
        res.equity.append(cash + held)
        res.exposure.append(held / (cash + held) if cash + held > 0 else 0.0)
        res.asset_ret.append(sum(c[i] / (o[i] if t == start else b.members[i].close[t - 1]) - 1 for i in range(k)) / k)
    res.open_trade = any(u > 0 for u in units)
    if res.open_trade and res.equity and close_at_end:
        eq = res.equity[-1]
        for i in range(k):
            if units[i] > 0:
                sell(i, units[i], b.members[i].close[end - 1], eq)
        res.equity[-1] = cash
    return res


def equal_weight(b: Basket) -> list:
    w = tuple(1.0 / len(b.members) for _ in b.members)
    return [w] * len(b)


def rotation(b: Basket, lookback: int, top: int) -> list:
    """Hold the ``top`` strongest assets over ``lookback`` bars, each at 1/top, and only those that are rising."""
    out = []
    for i in range(len(b)):
        if i < lookback:
            out.append(tuple(0.0 for _ in b.members))
            continue
        mom = [m.close[i] / m.close[i - lookback] - 1 for m in b.members]
        best = sorted(range(len(mom)), key=lambda j: (-mom[j], j))[:top]
        out.append(tuple(1.0 / top if j in best and mom[j] > 0 else 0.0 for j in range(len(mom))))
    return out


def rotation_vol(b: Basket, lookback: int, target: float) -> list:
    """Top-two rotation with each holding sized down when that asset's volatility exceeds the target."""
    base = rotation(b, lookback, 2)
    cols = [st._vol_scale(m, [2 * w[j] for w in base], target, 30) for j, m in enumerate(b.members)]
    return [tuple(math.floor(col[i] * 5) / 10 for col in cols) for i in range(len(b))]


# --- the predeclared list ----------------------------------------------------

_BRAKE = dict(limit=(0.10, 0.15, 0.20), cooldown=(10, 30))

DAILY: tuple[Candidate, ...] = (
    Candidate("brake_ema", "drawdown_brake", brake_ema, _grid(fast=(20,), slow=(100,), **_BRAKE),
              "trends that end in a slide rather than a gap", "sharp dips inside a trend: stopped out, then misses the recovery"),
    Candidate("brake_ensemble", "drawdown_brake", brake_ensemble, _grid(target=(0.4,), **_BRAKE),
              "as trend_ensemble, with losing runs cut short", "chop: repeated stops, each paying costs"),
    Candidate("low_vol_ensemble", "risk_adjusted", st.trend_ensemble, _grid(target=(0.15, 0.20, 0.25)),
              "any trend, at a size that keeps drawdown small", "returns are small by construction; costs weigh more"),
)

FOUR_HOUR: tuple[Candidate, ...] = (
    Candidate("tsmom_4h", "momentum", st.tsmom, _grid(lookback=(120, 180, 360, 540, 720)),
              "multi-week trends, with exits up to a day sooner", "intraday noise: more whipsaws, more costs"),
    Candidate("ema_cross_4h", "momentum", st.ema_cross,
              (dict(fast=60, slow=180), dict(fast=60, slow=300), dict(fast=120, slow=300), dict(fast=120, slow=600)),
              "long trends with shallow pullbacks", "V-shaped reversals; more crossings than the daily rule"),
    Candidate("donchian_4h", "breakout", st.donchian, _grid(entry=(120, 240, 330), exit=(60, 120)),
              "range expansion into a new trend", "false breakouts in a range"),
    Candidate("brake_ema_4h", "drawdown_brake", brake_ema,
              _grid(fast=(120,), slow=(600,), limit=(0.10, 0.15), cooldown=(60, 180)),
              "trends that end in a slide", "sharp dips inside a trend"),
)

BASKET: tuple[Candidate, ...] = (
    Candidate("rotation", "cross_asset", rotation, _grid(lookback=(30, 60, 90), top=(1, 2)),
              "one asset leading the others for weeks", "leadership that flips: buys the last winner at its peak"),
    Candidate("rotation_vol", "cross_asset", rotation_vol, _grid(lookback=(60, 90), target=(0.2, 0.3)),
              "as rotation, with smaller positions in turbulent assets", "all assets falling together: nothing to rotate into but cash"),
)

BASKET_BENCHMARK = Candidate("equal_weight", "benchmark", equal_weight, ({},), "rising markets", "every drawdown, in full")

ALL = DAILY + FOUR_HOUR + BASKET
#: Every candidate tried so far, in both rounds; the timing test is corrected for all of them.
CANDIDATES_TRIED = len(st.CANDIDATES) + len(ALL)


def _evaluate(series, cands, bench, plan, extra_trials=()) -> list:
    evs = [wf.evaluate(series, c, plan) for c in cands]
    trials = [s for e in evs for s in e["trial_sharpes"]] + list(extra_trials)
    rows = []
    for c, e in list(zip(cands, evs)) + [(bench, wf.evaluate(series, bench, plan))]:
        dsr = None
        if c is not bench:
            by_ts = {t: i for i, t in enumerate(series.ts)}
            parts = [bt.run(series, c.weights(series, f["params"]), start=by_ts[f["start"]], end=by_ts[f["end"]] + 1)
                     for f in e["folds"]]
            dsr = mx.deflated_sharpe(bt.stitch(parts).bar_returns, trials)
        e["deflated_sharpe"] = dsr
        e["assessment"] = None if c is bench else q.assess(e, n_candidates=CANDIDATES_TRIED, deflated_sharpe=dsr)
        del e["trial_sharpes"]
        rows.append(e)
    return rows


def _round1_trials(series, plan) -> list:
    """Development Sharpes of every round-1 configuration: they were tried on this data too."""
    holdout, _ = plan.split(len(series))
    return [mx.sharpe(bt.run(series, c.weights(series, p), start=plan.warmup, end=holdout).bar_returns, 1.0)
            for c in st.CANDIDATES for p in c.grid]


def study(series: dict) -> dict:
    """``series`` maps (symbol, interval) to candles; needs 1d and 4h for every symbol."""
    daily = {sym: s for (sym, i), s in series.items() if i == "1d"}
    four = {sym: s for (sym, i), s in series.items() if i == "4h"}
    syms = list(daily)
    baskets = [Basket.of([daily[s] for s in syms[:2]])] + ([Basket.of([daily[s] for s in syms])] if len(syms) > 2 else [])
    sections, plan = [], wf.Plan()
    for sym, s in daily.items():
        sections.append({"title": f"{sym} daily", "bars": len(s), "first": s.ts[0], "last": s.ts[-1], "sha256": s.sha256(),
                         "results": _evaluate(s, DAILY, st.BENCHMARK, plan, _round1_trials(s, plan))})
    for sym, s in four.items():
        rd.validate(s)
        sections.append({"title": f"{sym} 4-hour", "bars": len(s), "first": s.ts[0], "last": s.ts[-1], "sha256": s.sha256(),
                         "results": _evaluate(s, FOUR_HOUR, st.BENCHMARK, PLAN_4H)})
    for b in baskets:
        sections.append({"title": f"Basket {b.symbol} daily", "bars": len(b), "first": b.ts[0], "last": b.ts[-1],
                         "sha256": b.sha256(), "results": _evaluate(b, BASKET, BASKET_BENCHMARK, plan)})
    return {
        "schema": "ai-trader-research-round2/v1",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "simulated": True,
        "holdout_scored": False,
        "candidates_tried_all_rounds": CANDIDATES_TRIED,
        "costs": {"base_per_side": bt.BASE_COSTS.__dict__, "stress_per_side": bt.STRESS_COSTS.__dict__},
        "criteria": q.criteria_dict(),
        "plans": {"daily": plan.__dict__, "4h": PLAN_4H.__dict__},
        "sections": sections,
    }


def markdown(report: dict) -> str:
    from ai_trader.research.lab import table

    rows = [r for s in report["sections"] for r in s["results"] if r["assessment"]]
    counts: dict = {}
    for r in rows:
        counts[r["assessment"]["verdict"]] = counts.get(r["assessment"]["verdict"], 0) + 1
    c = report["costs"]["base_per_side"]
    L = ["# Strategy research report, round 2", "",
         f"Generated {report['generated_at']}. **Simulated results on historical candles. Not trading profits.**", "",
         f"Binance spot candles. Costs per side: fee {c['fee']:.2%} + slippage {c['slippage']:.2%}; stress case is double. "
         "Orders fill at the next bar's open. Long or flat only, no leverage. "
         "The final 20% holdout was **not** touched in this run, so no strategy can be marked as qualified here; "
         "`INSUFFICIENT_EVIDENCE` means it has not failed a development check yet.", "",
         f"The timing test is corrected for all {report['candidates_tried_all_rounds']} candidates tried across both rounds. "
         "For baskets, the benchmark and the timing test use an equal-weight hold of the same assets.", "",
         "Verdicts: " + ", ".join(f"{v} {k}" for k, v in sorted(counts.items())) + f" (of {len(rows)})."]
    for s in report["sections"]:
        L += ["", f"## {s['title']}", "",
              f"{s['bars']} bars, {time.strftime('%Y-%m-%d', time.gmtime(s['first']))} to "
              f"{time.strftime('%Y-%m-%d', time.gmtime(s['last']))}, sha256 `{s['sha256'][:12]}`", ""] + table(s["results"])
    return "\n".join(L) + "\n"
