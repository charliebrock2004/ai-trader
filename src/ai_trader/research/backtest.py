"""Cost-aware, next-bar-open backtester for one long-only book.

A weight decided on the close of bar ``t - 1`` is traded at the open of bar
``t``, worse by the slippage, and pays the fee. The book starts flat with
equity 1.0 and is closed out, at cost, on the last close of the window. Trades smaller than ``band`` of equity are skipped so sizing noise
does not churn the account; entries from flat and exits to flat always trade.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ai_trader.research.data import Series


@dataclass(frozen=True)
class Costs:
    """Per-side costs as fractions of traded value."""

    fee: float = 0.0025
    slippage: float = 0.0005

    def scaled(self, k: float) -> "Costs":
        return Costs(self.fee * k, self.slippage * k)


#: Base case: a UK retail taker fee tier (Kraken Pro maker/taker 0.25%/0.40%;
#: a Jupiter cbBTC/USDC route is cheaper) plus half the spread. Stress = 2x.
BASE_COSTS = Costs()
STRESS_COSTS = BASE_COSTS.scaled(2.0)
ZERO_COSTS = Costs(0.0, 0.0)


@dataclass
class Result:
    start: int
    end: int
    equity: list[float] = field(default_factory=list)  # marked at each bar's close
    exposure: list[float] = field(default_factory=list)
    asset_ret: list[float] = field(default_factory=list)  # asset return over the same bar
    trades: list[float] = field(default_factory=list)  # closed round trips, P&L as fraction of equity at entry
    orders: int = 0
    fees: float = 0.0
    slippage: float = 0.0
    turnover: float = 0.0
    open_trade: bool = False

    @property
    def bar_returns(self) -> list[float]:
        prev, out = 1.0, []
        for e in self.equity:
            out.append(e / prev - 1)
            prev = e
        return out


def run(series: Series, weights: list[float], costs: Costs = BASE_COSTS, *,
        start: int = 1, end: Optional[int] = None, band: float = 0.10) -> Result:
    end = len(series) if end is None else end
    if len(weights) != len(series):
        raise ValueError("weights and series differ in length")
    if start < 1:
        raise ValueError("start must leave one prior bar to decide on")
    res = Result(start, end)
    cash, units, entry_equity = 1.0, 0.0, None
    for t in range(start, end):
        o = series.open[t]
        target = min(1.0, max(0.0, weights[t - 1]))
        eq = cash + units * o
        cur = units * o / eq if eq > 0 else 0.0
        if (target == 0.0 and units > 0) or (units == 0 and target > 0) or abs(target - cur) > band:
            delta = target * eq - units * o
            if delta > 0:
                spend = min(delta, cash)
                fee = spend * costs.fee
                qty = (spend - fee) / (o * (1 + costs.slippage))
                if units == 0:
                    entry_equity = eq
                units += qty
                cash -= spend
                res.slippage += qty * o * costs.slippage
            else:
                qty = units if target == 0.0 else min(units, -delta / o)
                gross = qty * o * (1 - costs.slippage)
                fee = gross * costs.fee
                cash += gross - fee
                units -= qty
                res.slippage += qty * o * costs.slippage
                if units <= 1e-12:
                    units = 0.0
                    res.trades.append(cash / entry_equity - 1)
                    entry_equity = None
            res.fees += fee
            res.turnover += abs(delta) / eq
            res.orders += 1
        c = series.close[t]
        equity = cash + units * c
        res.equity.append(equity)
        res.exposure.append(units * c / equity if equity > 0 else 0.0)
        # The book starts flat and can first buy at this window's opening price.
        res.asset_ret.append(c / (o if t == start else series.close[t - 1]) - 1)
    if units > 0 and res.equity:
        # Close the book at the window's last close, paying costs, so every
        # trade is counted and a window never ends on unrealised P&L.
        c = series.close[end - 1]
        gross = units * c * (1 - costs.slippage)
        fee = gross * costs.fee
        res.slippage += units * c * costs.slippage
        res.fees += fee
        res.turnover += units * c / res.equity[-1]
        res.orders += 1
        cash += gross - fee
        res.equity[-1] = cash  # exposure[-1] stays: the position was held through this bar
        res.trades.append(cash / entry_equity - 1)
        res.open_trade = True  # the last trade was closed by the window, not the rule
    return res


def stitch(parts: list[Result]) -> Result:
    """Chain consecutive windows into one curve (each window started flat)."""
    out = Result(parts[0].start, parts[-1].end)
    level = 1.0
    for p in parts:
        out.equity.extend(level * e for e in p.equity)
        level = out.equity[-1] if out.equity else level
        out.exposure.extend(p.exposure)
        out.asset_ret.extend(p.asset_ret)
        out.trades.extend(p.trades)
        out.orders += p.orders
        out.fees += p.fees
        out.slippage += p.slippage
        out.turnover += p.turnover
    return out
