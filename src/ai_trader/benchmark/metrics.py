"""Benchmark metrics. Pure functions. No I/O. No broker."""

from __future__ import annotations

from typing import Any, Optional

from ai_trader.analysis.indicators import sample_stdev
from ai_trader.paper.ledger import money


def max_drawdown_from_equity(equities: list[float]) -> float:
    if not equities:
        return 0.0
    peak = equities[0]
    worst = 0.0
    for equity in equities:
        if equity > peak:
            peak = equity
        if peak > 0:
            dd = (peak - equity) / peak
            if dd > worst:
                worst = dd
    return round(worst, 6)


def equity_volatility(equities: list[float]) -> float:
    if len(equities) < 3:
        return 0.0
    returns: list[float] = []
    for prev, cur in zip(equities, equities[1:]):
        if prev:
            returns.append((cur - prev) / prev)
    vol = sample_stdev(returns)
    return round(float(vol), 6) if vol is not None else 0.0


def compute_metrics(
    *,
    starting_balance: float,
    ending_balance: float,
    closed: list[dict[str, Any]],
    equity_curve: Optional[list] = None,
    maximum_drawdown: Optional[float] = None,
) -> dict[str, Any]:
    start = float(starting_balance)
    end = float(ending_balance)
    pnl = money(end - start)
    ret = round((end - start) / start * 100.0, 4) if start else 0.0
    pnls = [float(row.get("realised_pnl") or 0.0) for row in closed]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = round(gross_win / gross_loss, 4) if gross_loss else (999.0 if gross_win else 0.0)
    equities: list[float] = []
    if equity_curve:
        for point in equity_curve:
            if isinstance(point, dict):
                equities.append(float(point.get("equity") or 0.0))
            else:
                equities.append(float(point))
    if not equities:
        equities = [start, end]
    dd = maximum_drawdown if maximum_drawdown is not None else max_drawdown_from_equity(equities)
    vol = equity_volatility(equities)
    return_frac = (end - start) / start if start else 0.0
    sharpe_like = round(return_frac / vol, 4) if vol else 0.0
    calmar_like = round(return_frac / dd, 4) if dd else 0.0
    return {
        "starting_balance": money(start),
        "ending_balance": money(end),
        "absolute_pnl": pnl,
        "return_pct": ret,
        "trades": len(pnls),
        "winning_trades": len(wins),
        "losing_trades": len(losses),
        "win_rate": round(len(wins) / len(pnls), 4) if pnls else 0.0,
        "average_win": money(sum(wins) / len(wins)) if wins else 0.0,
        "average_loss": money(sum(losses) / len(losses)) if losses else 0.0,
        "profit_factor": profit_factor,
        "maximum_drawdown": round(float(dd), 6),
        "volatility": vol,
        "risk_adjusted_return": sharpe_like,
        "calmar_like": calmar_like,
    }


def metrics_from_sim(report: dict[str, Any]) -> dict[str, Any]:
    account = report.get("account") or {}
    performance = report.get("performance") or {}
    return compute_metrics(
        starting_balance=account.get("starting_cash") or 100.0,
        ending_balance=account.get("account_equity") or 0.0,
        closed=list(report.get("closed_positions") or []),
        equity_curve=report.get("equity_curve") or [],
        maximum_drawdown=performance.get("maximum_drawdown"),
    )


def pool_metrics(rows: list[dict[str, Any]], *, starting_balance: float = 100.0) -> dict[str, Any]:
    """Equal-weight average of independent £100 runs, with pooled trade stats."""
    if not rows:
        return compute_metrics(
            starting_balance=starting_balance,
            ending_balance=starting_balance,
            closed=[],
            equity_curve=[],
            maximum_drawdown=0.0,
        )
    n = len(rows)
    avg_end = sum(r["ending_balance"] for r in rows) / n
    closed: list[dict[str, Any]] = []
    for row in rows:
        closed.extend(row.get("closed") or [])
    max_dd = max(r["maximum_drawdown"] for r in rows)
    mean_vol = sum(r["volatility"] for r in rows) / n
    pooled = compute_metrics(
        starting_balance=starting_balance,
        ending_balance=avg_end,
        closed=closed,
        equity_curve=None,
        maximum_drawdown=max_dd,
    )
    pooled["volatility"] = round(mean_vol, 6)
    return_frac = (avg_end - starting_balance) / starting_balance if starting_balance else 0.0
    pooled["risk_adjusted_return"] = round(return_frac / pooled["volatility"], 4) if pooled["volatility"] else 0.0
    pooled["calmar_like"] = round(return_frac / max_dd, 4) if max_dd else 0.0
    pooled["runs"] = n
    return pooled
