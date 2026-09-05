"""Performance metrics, computed from persisted data only.

Nothing here is estimated, remembered from a run, or carried in memory. Every
number is derived from what was actually written to the database, so a restart
cannot change the answer and the UI cannot show a figure the audit trail does
not support.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Optional

from ai_trader.analytics.calibration import build_report
from ai_trader.money import money_float


@dataclass(frozen=True)
class PerformanceSummary:
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return self.payload


def _safe_div(numerator: float, denominator: float) -> Optional[float]:
    if not denominator:
        return None
    return numerator / denominator


def _funnel(store: Any) -> dict[str, Any]:
    """Per-pipeline conversion. Reporting must never break a performance read."""
    fn = getattr(store, "pipeline_funnel", None)
    if not callable(fn):
        return {}
    try:
        return fn()
    except Exception:  # noqa: BLE001
        return {}


def compute_performance(
    store: Any,
    *,
    starting_equity: float,
    equity: float,
    terminal_threshold: float = 0.0,
    cost_ledger: Any = None,
) -> dict[str, Any]:
    """Everything the performance page shows, from the database."""
    positions = store.list_contract_positions(limit=1000)
    closed = [p for p in positions if not p["open"] and p["realised_pnl_base"] is not None]
    open_rows = [p for p in positions if p["open"]]

    pnls = [float(p["realised_pnl_base"]) for p in closed]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))

    trades = len(pnls)
    win_rate = _safe_div(len(wins), trades)
    average_win = _safe_div(gross_win, len(wins))
    average_loss = _safe_div(gross_loss, len(losses))
    expectancy = _safe_div(sum(pnls), trades)
    profit_factor = _safe_div(gross_win, gross_loss)

    decisions = store.decision_counts()
    considered = int(decisions.get("TOTAL", 0))
    executed = int(decisions.get("EXECUTED", 0))
    conversion = _safe_div(executed, considered)

    outcomes = store.list_outcomes(limit=2000)
    calibration = build_report(outcomes)

    predicted_edges = [
        float(o["predicted_edge"]) for o in outcomes if o.get("predicted_edge") is not None
    ]
    realised_edges = [
        float(o["realised_edge"]) for o in outcomes if o.get("realised_edge") is not None
    ]

    fees = 0.0
    costs_total = 0.0
    costs_by_category: dict[str, float] = {}
    if cost_ledger is not None:
        costs_by_category = cost_ledger.by_category()
        costs_total = cost_ledger.total()
        fees = costs_by_category.get("fees", 0.0)

    gross_pnl = money_float(sum(pnls))
    net_pnl = money_float(gross_pnl - costs_total)
    total_return = _safe_div(equity - starting_equity, starting_equity)

    return {
        "starting_equity": money_float(starting_equity),
        "equity": money_float(equity),
        "total_return_pct": round(total_return * 100.0, 4) if total_return is not None else None,
        "gross_pnl": gross_pnl,
        "realised_pnl": gross_pnl,
        "unrealised_pnl": 0.0,
        "operating_costs": costs_total,
        "costs_by_category": costs_by_category,
        "fees": fees,
        "net_pnl": net_pnl,
        "self_sustaining": bool(costs_total > 0 and gross_pnl >= costs_total),
        "trades": trades,
        "open_positions": len(open_rows),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate, 6) if win_rate is not None else None,
        "average_win": money_float(average_win) if average_win is not None else None,
        "average_loss": money_float(average_loss) if average_loss is not None else None,
        "expectancy": round(expectancy, 6) if expectancy is not None else None,
        "profit_factor": round(profit_factor, 6) if profit_factor is not None else None,
        "max_drawdown_pct": round(_max_drawdown(store, starting_equity) * 100.0, 4),
        "sharpe_like": _sharpe_like(pnls),
        "opportunities_considered": considered,
        "opportunities_executed": executed,
        "opportunities_rejected": considered - executed,
        "conversion_rate": round(conversion, 6) if conversion is not None else None,
        # The same numbers split by pipeline and by the stage that stopped each
        # decision. Without this the spot desk's conversion is buried under the
        # CPI pipeline's thousands of structural HOLDs, and the headline reads
        # 0.0% for reasons that have nothing to do with the spot strategy.
        "pipelines": _funnel(store),
        "average_predicted_edge": (
            round(sum(predicted_edges) / len(predicted_edges), 6) if predicted_edges else None
        ),
        "average_realised_edge": (
            round(sum(realised_edges) / len(realised_edges), 6) if realised_edges else None
        ),
        "calibration": calibration.to_dict(),
        "brier": calibration.brier,
        "terminal_threshold": money_float(terminal_threshold),
        "evidence_note": _evidence_note(trades, calibration.count),
    }


def _max_drawdown(store: Any, starting_equity: float) -> float:
    """Peak-to-trough on the realised equity path implied by settlements."""
    positions = sorted(
        [p for p in store.list_contract_positions(limit=1000) if not p["open"]],
        key=lambda p: p.get("closed_at") or "",
    )
    equity = float(starting_equity)
    peak = equity
    worst = 0.0
    for row in positions:
        pnl = row.get("realised_pnl_base")
        if pnl is None:
            continue
        equity += float(pnl)
        peak = max(peak, equity)
        if peak > 0:
            worst = max(worst, (peak - equity) / peak)
    return round(worst, 6)


def _sharpe_like(pnls: list[float]) -> Optional[float]:
    """Mean over standard deviation of per-trade P&L.

    Deliberately named ``sharpe_like``: it is not annualised and there is no
    risk-free rate, so calling it a Sharpe ratio would overstate what it is.
    """
    if len(pnls) < 2:
        return None
    mean = sum(pnls) / len(pnls)
    variance = sum((p - mean) ** 2 for p in pnls) / (len(pnls) - 1)
    sd = math.sqrt(variance)
    if sd == 0:
        return None
    return round(mean / sd, 6)


def _evidence_note(trades: int, resolved: int) -> str:
    """Say what the sample can and cannot support. This is not decoration."""
    if trades == 0:
        return "No completed trades. Nothing here supports any claim about edge."
    if trades < 30:
        return (
            f"{trades} completed trades and {resolved} resolved forecasts. Far too few "
            "to distinguish edge from luck. Treat every figure as provisional."
        )
    if trades < 100:
        return (
            f"{trades} completed trades. Enough to spot a large effect, not enough to "
            "confirm a small one."
        )
    return f"{trades} completed trades and {resolved} resolved forecasts."
