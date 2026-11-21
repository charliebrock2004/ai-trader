"""Candidate strategies, as causal target-weight rules.

Each rule maps a candle history to a list of target weights in ``[0, 1]``
(long or flat, never short, never leveraged). ``weights[t]`` may use bars up to
and including ``t`` only; the backtester trades it at the open of ``t + 1``.
``tests/test_research_lab.py`` checks that property for every rule by
recomputing on a truncated history.

The candidate list and every parameter grid are fixed here, before results
exist. Adding a candidate or widening a grid after seeing results is a new
experiment and must be logged as one.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

from ai_trader.research.data import Series

Weights = list[float]


def sma(x, n: int) -> list:
    out, s = [None] * len(x), 0.0
    for i, v in enumerate(x):
        s += v
        if i >= n:
            s -= x[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def ema(x, n: int) -> list:
    out, k, e = [None] * len(x), 2.0 / (n + 1), None
    for i, v in enumerate(x):
        e = v if e is None else e + k * (v - e)
        if i >= n - 1:
            out[i] = e
    return out


def rolling_std(x, n: int) -> list:
    out = [None] * len(x)
    for i in range(n - 1, len(x)):
        w = x[i - n + 1:i + 1]
        m = sum(w) / n
        out[i] = math.sqrt(sum((v - m) ** 2 for v in w) / n)
    return out


def returns(close) -> list:
    return [0.0] + [close[i] / close[i - 1] - 1 for i in range(1, len(close))]


def rsi(close, n: int) -> list:
    out = [None] * len(close)
    up = dn = 0.0
    for i in range(1, len(close)):
        d = close[i] - close[i - 1]
        g, l = max(d, 0.0), max(-d, 0.0)
        if i <= n:
            up += g / n
            dn += l / n
        else:
            up = (up * (n - 1) + g) / n
            dn = (dn * (n - 1) + l) / n
        if i >= n:
            out[i] = 100.0 if dn == 0 else 100 - 100 / (1 + up / dn)
    return out


def efficiency_ratio(close, n: int) -> list:
    """Kaufman: net move / path length. Near 1 = trending, near 0 = chop."""
    out = [None] * len(close)
    for i in range(n, len(close)):
        path = sum(abs(close[j] - close[j - 1]) for j in range(i - n + 1, i + 1))
        out[i] = abs(close[i] - close[i - n]) / path if path else 0.0
    return out


# --- rules -----------------------------------------------------------------

def buy_and_hold(s: Series) -> Weights:
    return [1.0] * len(s)


def tsmom(s: Series, lookback: int) -> Weights:
    """Trend following: long while price is above where it was ``lookback`` bars ago."""
    c = s.close
    return [1.0 if i >= lookback and c[i] > c[i - lookback] else 0.0 for i in range(len(c))]


def ema_cross(s: Series, fast: int, slow: int) -> Weights:
    f, sl = ema(s.close, fast), ema(s.close, slow)
    return [1.0 if sl[i] is not None and f[i] > sl[i] else 0.0 for i in range(len(s))]


def donchian(s: Series, entry: int, exit: int) -> Weights:
    """Breakout: enter on a close above the prior ``entry``-bar high, leave below the prior ``exit``-bar low."""
    out, pos = [], 0.0
    for i in range(len(s)):
        if i >= entry:
            if pos == 0 and s.close[i] > max(s.high[i - entry:i]):
                pos = 1.0
            elif pos and s.close[i] < min(s.low[i - exit:i]):
                pos = 0.0
        out.append(pos)
    return out


def volume_breakout(s: Series, entry: int, exit: int, vol_mult: float) -> Weights:
    """Donchian breakout that also needs above-average volume on the breakout bar."""
    out, pos, vavg = [], 0.0, sma(s.volume, entry)
    for i in range(len(s)):
        if i >= entry:
            if pos == 0 and s.close[i] > max(s.high[i - entry:i]) and s.volume[i] > vol_mult * vavg[i - 1]:
                pos = 1.0
            elif pos and s.close[i] < min(s.low[i - exit:i]):
                pos = 0.0
        out.append(pos)
    return out


def bollinger_reversion(s: Series, n: int, k: float, trend: int) -> Weights:
    """Mean reversion: buy a close below the lower band, sell at the mean. Only above the long trend average."""
    m, sd, t = sma(s.close, n), rolling_std(s.close, n), sma(s.close, trend)
    out, pos = [], 0.0
    for i in range(len(s)):
        if m[i] is not None and t[i] is not None:
            if pos == 0 and s.close[i] < m[i] - k * sd[i] and s.close[i] > t[i]:
                pos = 1.0
            elif pos and s.close[i] >= m[i]:
                pos = 0.0
        out.append(pos)
    return out


def rsi_reversion(s: Series, n: int, low: float, exit_n: int, trend: int) -> Weights:
    """Short-horizon oversold bounce inside an uptrend; exit above a short average or after losing the trend."""
    r, e, t = rsi(s.close, n), sma(s.close, exit_n), sma(s.close, trend)
    out, pos = [], 0.0
    for i in range(len(s)):
        if r[i] is not None and t[i] is not None:
            if pos == 0 and r[i] < low and s.close[i] > t[i]:
                pos = 1.0
            elif pos and (s.close[i] > e[i] or s.close[i] < t[i]):
                pos = 0.0
        out.append(pos)
    return out


def _vol_scale(s: Series, base: Weights, target: float, window: int) -> Weights:
    sd = rolling_std(returns(s.close), window)
    ann = math.sqrt(s.bars_per_year)
    out = []
    for i, w in enumerate(base):
        if sd[i] is None or sd[i] == 0:
            out.append(0.0)
        else:
            # Quantised to tenths so vol noise does not generate turnover.
            out.append(math.floor(min(1.0, target / (sd[i] * ann)) * w * 10) / 10)
    return out


def vol_target_trend(s: Series, fast: int, slow: int, target: float) -> Weights:
    """EMA trend, sized down when realised volatility exceeds the target (never sized up past 100%)."""
    return _vol_scale(s, ema_cross(s, fast, slow), target, 30)


def trend_ensemble(s: Series, target: float) -> Weights:
    """Average of five fixed trend rules, volatility-scaled. No parameter is selected from results."""
    parts = [tsmom(s, 30), tsmom(s, 90), ema_cross(s, 10, 40), ema_cross(s, 20, 100), donchian(s, 55, 20)]
    avg = [sum(p[i] for p in parts) / len(parts) for i in range(len(s))]
    return _vol_scale(s, avg, target, 30)


def regime_switch(s: Series, er_n: int, er_trend: float) -> Weights:
    """Regime-aware: follow the trend when the market is directional, buy dips when it is choppy but above trend."""
    er = efficiency_ratio(s.close, er_n)
    trend, mr = ema_cross(s, 20, 100), rsi_reversion(s, 2, 15.0, 5, 200)
    return [0.0 if er[i] is None else (trend[i] if er[i] >= er_trend else mr[i]) for i in range(len(s))]


@dataclass(frozen=True)
class Candidate:
    name: str
    family: str
    rule: Callable[..., Weights]
    grid: tuple[dict, ...]
    works_when: str
    fails_when: str

    def weights(self, s: Series, params: dict) -> Weights:
        return self.rule(s, **params)


def _grid(**axes) -> tuple[dict, ...]:
    out = [{}]
    for k, vs in axes.items():
        out = [{**d, k: v} for d in out for v in vs]
    return tuple(out)


CANDIDATES: tuple[Candidate, ...] = (
    Candidate("tsmom", "momentum", tsmom, _grid(lookback=(20, 30, 60, 90, 120, 180)),
              "persistent multi-week trends", "sideways chop: repeated whipsaw entries, each paying costs"),
    Candidate("ema_cross", "momentum", ema_cross,
              (dict(fast=10, slow=30), dict(fast=15, slow=30), dict(fast=10, slow=50), dict(fast=20, slow=50),
               dict(fast=20, slow=100), dict(fast=50, slow=200)),
              "long trends with shallow pullbacks", "V-shaped reversals: late in, late out"),
    Candidate("donchian", "breakout", donchian, _grid(entry=(20, 40, 55), exit=(10, 20)),
              "range expansion into a new trend", "false breakouts in a range"),
    Candidate("volume_breakout", "breakout", volume_breakout, _grid(entry=(20, 55), exit=(10, 20), vol_mult=(1.0, 1.5)),
              "breakouts backed by participation", "thin-volume drifts it never joins; false breakouts"),
    Candidate("bollinger_reversion", "mean_reversion", bollinger_reversion, _grid(n=(20, 30), k=(1.5, 2.0), trend=(100, 200)),
              "dips inside an established uptrend", "trend breaks: buys the first leg of a crash"),
    Candidate("rsi_reversion", "mean_reversion", rsi_reversion, _grid(n=(2, 3), low=(10.0, 20.0), exit_n=(5,), trend=(100, 200)),
              "short oversold bounces in an uptrend", "few signals; a gap down through the trend filter"),
    Candidate("vol_target_trend", "risk_adjusted", vol_target_trend,
              _grid(fast=(20,), slow=(50, 100), target=(0.3, 0.5)),
              "trends, with smaller size in turbulent periods", "calm-then-crash: sized up just before volatility arrives"),
    Candidate("trend_ensemble", "ensemble", trend_ensemble, _grid(target=(0.4,)),
              "any regime where several trend horizons agree", "chop; pays rebalancing costs for partial positions"),
    Candidate("regime_switch", "regime_aware", regime_switch, _grid(er_n=(20, 40), er_trend=(0.2, 0.3)),
              "markets that alternate between trend and range", "regime label lags the change of regime"),
)

BENCHMARK = Candidate("buy_and_hold", "benchmark", buy_and_hold, ({},), "rising markets", "every drawdown, in full")
