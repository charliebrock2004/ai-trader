"""Paper-risk limits. AI cannot change these at runtime.

All monetary limits are in the account's **base** currency (GBP by default).

Two limits work together and are easy to confuse, so they are named apart:

``max_risk_pct`` / ``max_risk_amount``
    How much the account may *lose* on one position — the risk budget.
``max_position_notional_pct``
    How much of the account may *sit in* one position — the concentration cap.

Sizing purely on the risk budget produces a position worth nearly the whole
account whenever the stop is tight, so the concentration cap has to exist
separately. ``stop_gap_buffer`` then acknowledges that a stop is not a
guarantee: sizing assumes the exit can be worse than the stop by that fraction
of the stop distance.
"""

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
    #: Target distance as a multiple of the stop. Raised from 2.0 to 2.5 so the
    #: target clears round-trip costs by a sensible margin; this moves where
    #: profit is taken and changes neither the stop nor the position size, so
    #: nothing about the amount at risk changes.
    take_profit_rr: float = 2.5
    max_notional_pct: float = 1.0  # cash only, no leverage
    #: Fraction of equity that may sit in a single position.
    max_position_notional_pct: float = 0.25
    #: Assumed adverse slip beyond the stop, as a fraction of stop distance.
    #: 0.5 means "size as if the exit could be 1.5x the stop distance away".
    stop_gap_buffer: float = 0.5

    #: A strategy may propose a stop distance derived from current volatility,
    #: which suits the market far better than one fixed percentage. It is a
    #: *suggestion*: the engine clamps it into this band and remains the only
    #: thing that decides what the stop actually is. The floor stops a hint so
    #: tight that the resulting position breaches concentration; the ceiling
    #: stops one so wide the trade never resolves.
    min_stop_pct: float = 0.0025
    max_stop_pct: float = 0.0400

    def clamp_stop_pct(self, hint: float | None) -> tuple[float, str]:
        """Resolve the stop fraction to use, and say where it came from."""
        if hint is None:
            return self.default_stop_pct, "default"
        try:
            value = float(hint)
        except (TypeError, ValueError):
            return self.default_stop_pct, "default"
        if value <= 0:
            return self.default_stop_pct, "default"
        if value < self.min_stop_pct:
            return self.min_stop_pct, "clamped_min"
        if value > self.max_stop_pct:
            return self.max_stop_pct, "clamped_max"
        return value, "strategy"

    def daily_loss_amount(self, day_start_equity: float) -> float:
        return round(day_start_equity * self.max_daily_loss_pct, 2)

    def risk_budget(self, equity: float) -> float:
        return round(min(equity * self.max_risk_pct, self.max_risk_amount), 2)

    def worst_case_stop_distance(self, stop_distance: float) -> float:
        """Stop distance widened by the assumed gap. Used for sizing."""
        return float(stop_distance) * (1.0 + max(0.0, self.stop_gap_buffer))
