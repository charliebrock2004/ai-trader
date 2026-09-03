"""Deterministic probability estimation.

**The model does not compute this.** An LLM's stated confidence is not a
calibrated probability, and treating it as one is the single easiest way to
build a system that looks rigorous and is not. So the number that decides
whether an opportunity exists is produced here, in Python, from the official
value and the contract's own resolution rule — reproducibly, and with every
input recorded.

Two regimes
-----------
*Resolved*: the release has published and the contract's comparison can be
evaluated arithmetically. The probability is then near-certain, discounted only
by the chance of a revision or a resolution-source mismatch.

*Unresolved*: the release has not published. The estimate comes from the
distance between a forecast and the strike, scaled by the historical dispersion
of that series. This is deliberately a weak model — the strategy is not meant
to out-forecast the market before a release, it is meant to be right *after*
one.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Optional

#: Even a published number is not certain: it can be revised, and the contract
#: may resolve off a slightly different series. This is the cap on confidence.
DEFAULT_REVISION_RISK = 0.02

#: Floor and ceiling so a probability never reaches 0 or 1.
MIN_PROBABILITY = 0.005
MAX_PROBABILITY = 0.995


def clamp_probability(value: float) -> float:
    return round(min(MAX_PROBABILITY, max(MIN_PROBABILITY, float(value))), 6)


def normal_cdf(z: float) -> float:
    """Standard normal CDF via erf. No scipy dependency."""
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


@dataclass(frozen=True)
class ProbabilityEstimate:
    """A probability with everything needed to reproduce it."""

    probability: float
    method: str
    confidence: float
    inputs: dict[str, Any] = field(default_factory=dict)
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "probability": self.probability,
            "method": self.method,
            "confidence": self.confidence,
            "inputs": self.inputs,
            "detail": self.detail,
        }


def probability_from_resolved_value(
    *,
    observed_value: float,
    strike: float,
    comparison: str,
    revision_risk: float = DEFAULT_REVISION_RISK,
    margin_ratio: Optional[float] = None,
) -> ProbabilityEstimate:
    """Probability for a contract whose underlying number has been published.

    ``margin_ratio`` is how far the value cleared the strike relative to a
    plausible revision. A number that barely cleared deserves less confidence
    than one that cleared by a mile, because a revision could flip it.
    """
    value = float(observed_value)
    if comparison == "above":
        outcome = value > strike
    elif comparison == "at_or_above":
        outcome = value >= strike
    elif comparison == "below":
        outcome = value < strike
    elif comparison == "at_or_below":
        outcome = value <= strike
    else:
        raise ValueError(f"Unsupported comparison {comparison!r}.")

    risk = max(0.0, min(0.5, float(revision_risk)))
    if margin_ratio is not None:
        # A thin margin scales the revision risk up, to a maximum of 0.5.
        thinness = max(0.0, 1.0 - min(1.0, abs(float(margin_ratio))))
        risk = min(0.5, risk + thinness * 0.30)

    probability = 1.0 - risk if outcome else risk
    return ProbabilityEstimate(
        probability=clamp_probability(probability),
        method="resolved_comparison",
        confidence=round(1.0 - risk, 6),
        inputs={
            "observed_value": value,
            "strike": float(strike),
            "comparison": comparison,
            "revision_risk": risk,
            "margin_ratio": margin_ratio,
        },
        detail=(
            f"Published value {value} {'satisfies' if outcome else 'does not satisfy'} "
            f"'{comparison} {strike}'. Discounted for revision risk {risk:.3f}."
        ),
    )


def probability_from_forecast(
    *,
    forecast: float,
    strike: float,
    comparison: str,
    dispersion: float,
) -> ProbabilityEstimate:
    """Pre-release estimate from a forecast and the series' historical spread.

    Deliberately conservative and deliberately weak. If ``dispersion`` is not
    positive we refuse rather than pretending to a point estimate.
    """
    if dispersion <= 0:
        raise ValueError("Dispersion must be positive to form a pre-release estimate.")
    z = (float(forecast) - float(strike)) / float(dispersion)
    above = normal_cdf(z)
    if comparison in {"above", "at_or_above"}:
        probability = above
    elif comparison in {"below", "at_or_below"}:
        probability = 1.0 - above
    else:
        raise ValueError(f"Unsupported comparison {comparison!r}.")
    # Pre-release confidence is capped low on purpose: this model is not
    # expected to beat the market's own forecast.
    return ProbabilityEstimate(
        probability=clamp_probability(probability),
        method="forecast_normal",
        confidence=0.35,
        inputs={
            "forecast": float(forecast),
            "strike": float(strike),
            "comparison": comparison,
            "dispersion": float(dispersion),
            "z": round(z, 6),
        },
        detail=(
            f"Forecast {forecast} vs strike {strike} at dispersion {dispersion}: "
            f"z={z:.3f}. Pre-release estimates carry low confidence by design."
        ),
    )
