"""Fractional Kelly sizing with hard caps.

Returns a fraction of equity in [0, max_exposure]. max_exposure cannot exceed 1,
so the module cannot recommend leverage. The fraction is zero when the
conservative edge is not strictly positive.

Two views:
- return space: f* = mean / variance
- closed trades: f* = p - (1-p) / b, b = avg_win / |avg_loss|

When both are supplied, the smaller raw Kelly fraction is kept. It is then
scaled by kelly_fraction (default 0.25) and capped.

Martingale staking, loss recovery, leverage and borrowed money are not
implemented.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SizeDecision:
    fraction: float
    raw_kelly: float
    reason: str
    binding_constraint: str

    def to_dict(self) -> dict[str, float | str]:
        return {
            "fraction": self.fraction,
            "raw_kelly": self.raw_kelly,
            "reason": self.reason,
            "binding_constraint": self.binding_constraint,
        }


def fractional_kelly(
    *,
    mean_return: float | None = None,
    variance: float | None = None,
    win_probability: float | None = None,
    avg_win: float | None = None,
    avg_loss: float | None = None,
    kelly_fraction: float = 0.25,
    max_exposure: float = 0.25,
) -> SizeDecision:
    if not 0 < kelly_fraction <= 1:
        raise ValueError("kelly_fraction must be in (0, 1]")
    if not 0 <= max_exposure <= 1:
        raise ValueError("max_exposure must be in [0, 1]; leverage is not allowed")

    pieces: list[tuple[float, str]] = []
    if mean_return is not None or variance is not None:
        if mean_return is None or variance is None:
            raise ValueError("mean_return and variance must be supplied together")
        if mean_return <= 0 or variance <= 0:
            pieces.append((0.0, "return-space edge is not positive"))
        else:
            pieces.append((mean_return / variance, "return-space Kelly mean/variance"))

    trade_inputs = (win_probability, avg_win, avg_loss)
    if any(value is not None for value in trade_inputs):
        if any(value is None for value in trade_inputs):
            raise ValueError("win_probability, avg_win and avg_loss must be supplied together")
        assert win_probability is not None and avg_win is not None and avg_loss is not None
        if not 0.0 <= win_probability <= 1.0 or avg_win <= 0 or avg_loss >= 0:
            pieces.append((0.0, "trade payoff is not a positive-edge bet"))
        else:
            edge = win_probability * avg_win + (1.0 - win_probability) * avg_loss
            if edge <= 0:
                pieces.append((0.0, "trade expectancy is not positive"))
            else:
                payoff_ratio = avg_win / abs(avg_loss)
                raw_trade = win_probability - (1.0 - win_probability) / payoff_ratio
                pieces.append((max(0.0, raw_trade), "trade Kelly p-q/b"))

    if not pieces:
        raise ValueError("provide return moments or a trade payoff")

    raw = min(piece for piece, _reason in pieces)
    reasons = "; ".join(reason for _piece, reason in pieces)
    if raw <= 0:
        return SizeDecision(0.0, raw, reasons, "no positive edge")

    scaled = raw * kelly_fraction
    binding = "fractional-kelly"
    if scaled > 1.0:
        scaled = 1.0
        binding = "no-leverage cap"
    if scaled > max_exposure:
        scaled = max_exposure
        binding = "max_exposure cap"
    return SizeDecision(scaled, raw, reasons, binding)
