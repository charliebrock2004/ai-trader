"""Performance numbers computed from a finished simulation.

These describe a historical simulation. They are not paper-trading results
and they are not live results.
"""

from __future__ import annotations

from typing import Sequence

from btc_research.backtest.engine import BacktestResult
from btc_research.data.candles import Candle
from btc_research.stats import cagr, equity_returns, max_drawdown, mean, mean_test, sharpe_ratio


def summarise(
    result: BacktestResult,
    candles: Sequence[Candle],
    *,
    start: int,
    end: int,
    periods_per_year: float,
    initial_equity: float,
) -> dict[str, object]:
    equity = result.equity
    returns = equity_returns([initial_equity, *equity])
    drawdown = max_drawdown([initial_equity, *equity])
    elapsed = 0
    if end - start >= 1:
        elapsed = candles[end - 1].close_time_ms - candles[start].open_time_ms
    closed = result.round_trips
    wins = [trip for trip in closed if trip.pnl > 0]
    losses = [trip for trip in closed if trip.pnl < 0]
    test = mean_test(returns)
    return {
        "n_bars": len(equity),
        "n_returns": len(returns),
        "start_equity": initial_equity,
        "end_equity": equity[-1] if equity else None,
        "total_return": (equity[-1] / initial_equity - 1.0) if equity and initial_equity else None,
        "cagr": cagr([initial_equity, *equity], elapsed) if equity else None,
        "sharpe": sharpe_ratio(returns, periods_per_year),
        "max_drawdown": drawdown["max_drawdown"],
        "drawdown_peak_index": drawdown["peak_index"],
        "drawdown_trough_index": drawdown["trough_index"],
        "n_fills": len(result.fills),
        "n_closed_trades": len(closed),
        "win_rate": (len(wins) / len(closed)) if closed else None,
        "avg_win": mean([trip.pnl for trip in wins]) if wins else None,
        "avg_loss": mean([trip.pnl for trip in losses]) if losses else None,
        "expectancy": mean([trip.pnl for trip in closed]) if closed else None,
        "open_qty_at_end": result.final_qty,
        "mean_bar_return": test["mean"],
        "se_hac_bar_return": test["se_hac"],
        "t_hac": test["t_hac"],
        "p_hac": test["p_hac"],
        "ci95_low": test["ci95_low"],
        "ci95_high": test["ci95_high"],
        "result_kind": "historical_backtest",
    }
