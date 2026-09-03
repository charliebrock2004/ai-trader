"""Benchmarks for the event strategy.

The old benchmark compared strategies on a synthetic random walk and labelled a
different RNG seed "out_of_sample". That is not out-of-sample and the numbers
meant nothing, so this replaces it for the event strategy.

The baselines here are meant to be **hard to beat**, not flattering:

* ``NO_TRADE`` — the honest null. Zero cost, zero risk. If the agent cannot
  beat sitting still after costs, there is no edge.
* ``ALWAYS_FAVOURITE`` — buy whichever side the market already prefers. Cheap
  to run and often accurate; beating it on accuracy is not the same as beating
  it on profit, because the favourite is priced accordingly.
* ``DETERMINISTIC`` — the model probability and edge rule with no analyst.
  Isolates what the LLM actually adds.
* ``AGENT`` — the full pipeline.

Out-of-sample means *information the strategy could not have seen*, which for
this system means a period after development stopped. :func:`split_by_time` is
how a period is carved; a different random seed is never a split.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional

from ai_trader.analytics.calibration import build_report
from ai_trader.markets.fees import FeeModel, ZeroFeeModel
from ai_trader.money import money_float


@dataclass(frozen=True)
class BenchmarkCase:
    """One resolved historical opportunity. The unit a benchmark runs over."""

    case_id: str
    event_key: str
    ticker: str
    resolved_at: str
    model_probability: float
    market_ask: float
    market_bid: float
    outcome: int
    depth: int = 100
    fee_model: FeeModel = field(default_factory=ZeroFeeModel)

    @property
    def market_probability(self) -> float:
        return self.market_ask

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "event_key": self.event_key,
            "ticker": self.ticker,
            "resolved_at": self.resolved_at,
            "model_probability": self.model_probability,
            "market_ask": self.market_ask,
            "market_bid": self.market_bid,
            "outcome": self.outcome,
            "depth": self.depth,
        }


@dataclass(frozen=True)
class BenchmarkResult:
    strategy: str
    split: str
    trades: int
    wins: int
    losses: int
    win_rate: Optional[float]
    gross_pnl: float
    fees: float
    net_pnl: float
    return_pct: float
    expectancy: Optional[float]
    max_drawdown_pct: float
    brier: Optional[float]
    average_edge: Optional[float]
    realised_edge: Optional[float]
    opportunities: int
    starting_equity: float
    ending_equity: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "split": self.split,
            "trades": self.trades,
            "wins": self.wins,
            "losses": self.losses,
            "win_rate": self.win_rate,
            "gross_pnl": self.gross_pnl,
            "fees": self.fees,
            "net_pnl": self.net_pnl,
            "return_pct": self.return_pct,
            "expectancy": self.expectancy,
            "max_drawdown_pct": self.max_drawdown_pct,
            "brier": self.brier,
            "average_edge": self.average_edge,
            "realised_edge": self.realised_edge,
            "opportunities": self.opportunities,
            "starting_equity": self.starting_equity,
            "ending_equity": self.ending_equity,
        }


#: A strategy decides how many contracts to buy. Zero means no trade.
Strategy = Callable[[BenchmarkCase, float], int]


def no_trade(case: BenchmarkCase, equity: float) -> int:
    """The null hypothesis. Beating this after costs is the minimum bar."""
    return 0


def always_favourite(case: BenchmarkCase, equity: float) -> int:
    """Buy YES whenever the market already thinks it is more likely than not."""
    if case.market_ask <= 0.5:
        return 0
    return _size(case, equity, fraction=0.10)


def deterministic_edge(min_edge: float = 0.05, fraction: float = 0.10) -> Strategy:
    """The model's own edge rule, with no analyst involved."""

    def strategy(case: BenchmarkCase, equity: float) -> int:
        fee = _fee_per_contract(case)
        spread = max(0.0, case.market_ask - case.market_bid) / 2.0
        edge = case.model_probability - case.market_ask - fee - spread
        if edge < min_edge:
            return 0
        return _size(case, equity, fraction=fraction)

    return strategy


def agent_strategy(
    min_edge: float = 0.05,
    fraction: float = 0.10,
    analyst: Optional[Callable[[BenchmarkCase], bool]] = None,
) -> Strategy:
    """The deterministic rule plus an analyst veto."""
    base = deterministic_edge(min_edge=min_edge, fraction=fraction)

    def strategy(case: BenchmarkCase, equity: float) -> int:
        contracts = base(case, equity)
        if contracts <= 0:
            return 0
        if analyst is not None and not analyst(case):
            return 0
        return contracts

    return strategy


def _fee_per_contract(case: BenchmarkCase) -> float:
    model = case.fee_model
    if hasattr(model, "fee_per_contract"):
        return float(model.fee_per_contract(price=case.market_ask))
    return float(model.trade_fee(contracts=1, price=case.market_ask))


def _size(case: BenchmarkCase, equity: float, *, fraction: float) -> int:
    """Fixed-fraction sizing, capped by book depth. Same rule for every strategy."""
    budget = equity * fraction
    cost = case.market_ask + _fee_per_contract(case)
    if cost <= 0:
        return 0
    return max(0, min(case.depth, int(math.floor(budget / cost))))


def run_strategy(
    cases: Iterable[BenchmarkCase],
    strategy: Strategy,
    *,
    name: str,
    split: str,
    starting_equity: float = 100.0,
) -> BenchmarkResult:
    """Walk the cases in recorded order. Same fills and fees for every strategy."""
    equity = float(starting_equity)
    peak = equity
    worst_dd = 0.0
    gross = 0.0
    fees_paid = 0.0
    pnls: list[float] = []
    edges: list[float] = []
    realised: list[float] = []
    predictions: list[tuple[float, int]] = []
    opportunities = 0

    ordered = sorted(cases, key=lambda c: (c.resolved_at, c.case_id))
    for case in ordered:
        opportunities += 1
        predictions.append((case.model_probability, case.outcome))
        contracts = strategy(case, equity)
        if contracts <= 0:
            continue
        fee = case.fee_model.trade_fee(contracts=contracts, price=case.market_ask)
        premium = contracts * case.market_ask
        payout = contracts * 1.0 if case.outcome == 1 else 0.0
        pnl = payout - premium - fee
        equity += pnl
        gross += payout - premium
        fees_paid += fee
        pnls.append(pnl)
        spread = max(0.0, case.market_ask - case.market_bid) / 2.0
        edges.append(
            case.model_probability - case.market_ask - _fee_per_contract(case) - spread
        )
        realised.append(case.outcome - case.market_ask)
        peak = max(peak, equity)
        if peak > 0:
            worst_dd = max(worst_dd, (peak - equity) / peak)

    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    report = build_report(
        [{"predicted_probability": p, "resolved_outcome": o} for p, o in predictions]
    )
    return BenchmarkResult(
        strategy=name,
        split=split,
        trades=len(pnls),
        wins=len(wins),
        losses=len(losses),
        win_rate=round(len(wins) / len(pnls), 6) if pnls else None,
        gross_pnl=money_float(gross),
        fees=money_float(fees_paid),
        net_pnl=money_float(sum(pnls)),
        return_pct=round((equity - starting_equity) / starting_equity * 100.0, 4),
        expectancy=round(sum(pnls) / len(pnls), 6) if pnls else None,
        max_drawdown_pct=round(worst_dd * 100.0, 4),
        brier=report.brier,
        average_edge=round(sum(edges) / len(edges), 6) if edges else None,
        realised_edge=round(sum(realised) / len(realised), 6) if realised else None,
        opportunities=opportunities,
        starting_equity=money_float(starting_equity),
        ending_equity=money_float(equity),
    )


def split_by_time(
    cases: Iterable[BenchmarkCase], *, cutoff: str
) -> tuple[list[BenchmarkCase], list[BenchmarkCase]]:
    """Split into development and out-of-sample by resolution time.

    This is the only honest split for this system. A different RNG seed is not
    out-of-sample: the strategy was written knowing the generator.
    """
    rows = list(cases)
    development = [c for c in rows if c.resolved_at < cutoff]
    out_of_sample = [c for c in rows if c.resolved_at >= cutoff]
    return development, out_of_sample


def run_benchmark(
    cases: Iterable[BenchmarkCase],
    *,
    cutoff: Optional[str] = None,
    starting_equity: float = 100.0,
    analyst: Optional[Callable[[BenchmarkCase], bool]] = None,
    min_edge: float = 0.05,
) -> dict[str, Any]:
    """Every baseline over every split. Reports what the numbers support."""
    rows = list(cases)
    strategies: dict[str, Strategy] = {
        "NO_TRADE": no_trade,
        "ALWAYS_FAVOURITE": always_favourite,
        "DETERMINISTIC": deterministic_edge(min_edge=min_edge),
        "AGENT": agent_strategy(min_edge=min_edge, analyst=analyst),
    }

    splits: dict[str, list[BenchmarkCase]] = {"all": rows}
    if cutoff:
        development, out_of_sample = split_by_time(rows, cutoff=cutoff)
        splits = {"development": development, "out_of_sample": out_of_sample}

    results: dict[str, dict[str, Any]] = {}
    for split_name, split_cases in splits.items():
        results[split_name] = {
            name: run_strategy(
                split_cases, strategy, name=name, split=split_name,
                starting_equity=starting_equity,
            ).to_dict()
            for name, strategy in strategies.items()
        }

    headline_split = "out_of_sample" if "out_of_sample" in results else "all"
    headline = results.get(headline_split, {})
    agent = headline.get("AGENT", {})
    baseline = headline.get("NO_TRADE", {})
    beats = {
        name: float(agent.get("net_pnl") or 0.0) > float(row.get("net_pnl") or 0.0)
        for name, row in headline.items()
        if name != "AGENT"
    }
    sample = len(splits.get(headline_split, []))

    return {
        "ok": True,
        "live": False,
        "broker": "NOT USED",
        "starting_equity": starting_equity,
        "cutoff": cutoff,
        "headline_split": headline_split,
        "sample_size": sample,
        "results": results,
        "beats": beats,
        "beats_all": bool(beats) and all(beats.values()) and int(agent.get("trades") or 0) > 0,
        "verdict": _verdict(agent, baseline, beats, sample),
        "note": (
            "Same cases, same fills, same fee model and the same fixed-fraction "
            "sizing for every strategy. Out-of-sample means a later time period, "
            "never a different random seed."
        ),
    }


def _verdict(
    agent: dict[str, Any], baseline: dict[str, Any], beats: dict[str, bool], sample: int
) -> str:
    trades = int(agent.get("trades") or 0)
    if sample < 30:
        return (
            f"{sample} cases is too small a sample to support any conclusion. "
            "These numbers are a smoke test of the machinery, not evidence of edge."
        )
    if trades == 0:
        return "The agent took no trades, so there is nothing to evaluate."
    net = float(agent.get("net_pnl") or 0.0)
    if net <= 0:
        return (
            f"The agent lost {abs(net):.2f} net of costs over {trades} trades. "
            "No edge is demonstrated."
        )
    if not all(beats.values()):
        lost_to = [name for name, won in beats.items() if not won]
        return (
            f"The agent made {net:.2f} but did not beat {', '.join(lost_to)}. "
            "A strategy that loses to a trivial baseline has not shown an edge."
        )
    return (
        f"The agent made {net:.2f} over {trades} trades and beat every baseline on "
        f"{sample} out-of-sample cases. Suggestive, not conclusive."
    )
