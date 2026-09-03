"""Calibration.

A trading system can be profitable by luck and unprofitable by bad luck, but a
*calibrated* one is making honest probability statements — when it says 70%, it
is right about 70% of the time. That is the property that distinguishes a real
edge from a run of good outcomes, and it is measurable long before P&L is.

Everything here is computed from persisted outcomes. Nothing is estimated, and
nothing is fitted: these are scores, not parameters.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

#: Ten equal-width probability buckets.
DEFAULT_BUCKETS = tuple((i / 10.0, (i + 1) / 10.0) for i in range(10))


@dataclass(frozen=True)
class CalibrationBucket:
    lower: float
    upper: float
    count: int
    mean_predicted: float
    observed_rate: float
    gap: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "lower": self.lower,
            "upper": self.upper,
            "label": f"{int(self.lower * 100)}-{int(self.upper * 100)}%",
            "count": self.count,
            "mean_predicted": self.mean_predicted,
            "observed_rate": self.observed_rate,
            "gap": self.gap,
        }


@dataclass(frozen=True)
class CalibrationReport:
    count: int
    brier: Optional[float]
    baseline_brier: Optional[float]
    skill_score: Optional[float]
    accuracy: Optional[float]
    mean_predicted: Optional[float]
    observed_rate: Optional[float]
    buckets: list[CalibrationBucket] = field(default_factory=list)
    expected_calibration_error: Optional[float] = None
    verdict: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "count": self.count,
            "brier": self.brier,
            "baseline_brier": self.baseline_brier,
            "skill_score": self.skill_score,
            "accuracy": self.accuracy,
            "mean_predicted": self.mean_predicted,
            "observed_rate": self.observed_rate,
            "expected_calibration_error": self.expected_calibration_error,
            "buckets": [b.to_dict() for b in self.buckets],
            "verdict": self.verdict,
        }


def brier_score(predictions: Iterable[tuple[float, int]]) -> Optional[float]:
    """Mean squared error of probability forecasts. Lower is better; 0 is perfect."""
    rows = [(float(p), 1 if int(o) else 0) for p, o in predictions]
    if not rows:
        return None
    return round(sum((p - o) ** 2 for p, o in rows) / len(rows), 8)


def build_report(
    outcomes: Iterable[dict[str, Any]],
    *,
    buckets: tuple[tuple[float, float], ...] = DEFAULT_BUCKETS,
    min_sample: int = 30,
) -> CalibrationReport:
    """Calibration over recorded outcomes.

    ``skill_score`` compares the model against the trivial forecaster that
    always predicts the base rate. Positive means the model beat it; zero or
    negative means it did not, which is the honest answer when it is true.
    """
    rows: list[tuple[float, int]] = []
    for row in outcomes:
        predicted = row.get("predicted_probability")
        actual = row.get("resolved_outcome")
        if predicted is None or actual is None:
            continue
        rows.append((float(predicted), 1 if int(actual) else 0))

    if not rows:
        return CalibrationReport(
            count=0, brier=None, baseline_brier=None, skill_score=None,
            accuracy=None, mean_predicted=None, observed_rate=None,
            verdict="No resolved outcomes yet. Calibration is unknown.",
        )

    n = len(rows)
    brier = brier_score(rows)
    base_rate = sum(o for _p, o in rows) / n
    baseline = round(sum((base_rate - o) ** 2 for _p, o in rows) / n, 8)
    skill = None
    if baseline and baseline > 0:
        skill = round(1.0 - (brier / baseline), 6)
    accuracy = round(sum(1 for p, o in rows if (p >= 0.5) == (o == 1)) / n, 6)
    mean_predicted = round(sum(p for p, _o in rows) / n, 6)

    bucket_rows: list[CalibrationBucket] = []
    weighted_gap = 0.0
    for lower, upper in buckets:
        # The top bucket is closed so a prediction of exactly 1.0 lands somewhere.
        in_bucket = [
            (p, o) for p, o in rows
            if (lower <= p < upper) or (upper >= 1.0 and p == 1.0)
        ]
        if not in_bucket:
            continue
        count = len(in_bucket)
        mean_p = sum(p for p, _o in in_bucket) / count
        observed = sum(o for _p, o in in_bucket) / count
        gap = round(observed - mean_p, 6)
        weighted_gap += abs(gap) * count
        bucket_rows.append(
            CalibrationBucket(
                lower=lower, upper=upper, count=count,
                mean_predicted=round(mean_p, 6),
                observed_rate=round(observed, 6),
                gap=gap,
            )
        )
    ece = round(weighted_gap / n, 6)

    return CalibrationReport(
        count=n,
        brier=brier,
        baseline_brier=baseline,
        skill_score=skill,
        accuracy=accuracy,
        mean_predicted=mean_predicted,
        observed_rate=round(base_rate, 6),
        buckets=bucket_rows,
        expected_calibration_error=ece,
        verdict=_verdict(n, skill, ece, min_sample),
    )


def _verdict(n: int, skill: Optional[float], ece: Optional[float], min_sample: int) -> str:
    """Say plainly what the numbers support. Under-powered means under-powered."""
    if n < min_sample:
        return (
            f"{n} resolved outcomes is too few to judge calibration. "
            f"At least {min_sample} are needed before these numbers mean anything."
        )
    if skill is None:
        return "Every outcome resolved the same way, so there is nothing to score against."
    if skill <= 0:
        return (
            f"No skill demonstrated (skill score {skill:.3f}). The model has not beaten "
            "simply predicting the base rate."
        )
    if ece is not None and ece > 0.15:
        return (
            f"Some skill (score {skill:.3f}) but poorly calibrated "
            f"(mean gap {ece:.3f}). The probabilities should not be trusted as sizes."
        )
    return f"Calibrated with positive skill (score {skill:.3f}, mean gap {ece:.3f})."
