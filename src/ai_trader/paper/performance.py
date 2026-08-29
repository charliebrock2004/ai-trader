"""Performance stats from closed paper trades."""

from __future__ import annotations

from typing import Any

from ai_trader.paper.models import PaperPosition
from ai_trader.paper.ledger import money


def summarise(
    *,
    closed: list[PaperPosition],
    starting_cash: float,
    equity: float,
    max_drawdown: float,
) -> dict[str, Any]:
    pnls = [p.realised_pnl for p in closed]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = round(gross_win / gross_loss, 4) if gross_loss else (999.0 if gross_win else 0.0)
    ret = round((equity - starting_cash) / starting_cash * 100.0, 4) if starting_cash else 0.0
    return {
        "total_trades": len(pnls),
        "winning_trades": len(wins),
        "losing_trades": len(losses),
        "win_rate": round(len(wins) / len(pnls), 4) if pnls else 0.0,
        "profit_factor": profit_factor,
        "average_win": money(sum(wins) / len(wins)) if wins else 0.0,
        "average_loss": money(sum(losses) / len(losses)) if losses else 0.0,
        "maximum_drawdown": max_drawdown,
        "return_pct": ret,
        "gross_profit": money(gross_win),
        "gross_loss": money(gross_loss),
    }
