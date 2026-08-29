"""Paper-risk limits. AI cannot change these at runtime."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RiskLimits:
    starting_cash: float = 100.00
    max_risk_pct: float = 0.02
    max_risk_amount: float = 2.00
    max_open_positions: int = 2
    max_daily_loss_pct: float = 0.05
    max_trades_per_day: int = 10
    leverage: float = 0.0
    default_stop_pct: float = 0.02
    take_profit_rr: float = 2.0
    max_notional_pct: float = 1.0  # cash only, no leverage

    def daily_loss_amount(self, day_start_equity: float) -> float:
        return round(day_start_equity * self.max_daily_loss_pct, 2)

    def risk_budget(self, equity: float) -> float:
        return round(min(equity * self.max_risk_pct, self.max_risk_amount), 2)
