"""Timestamp, duplicate, gap and OHLC checks.

Policy:
- Time that moves backwards is an error. The loader does not silently sort,
  because a silent sort can hide a corrupt cache.
- Identical duplicate timestamps are counted and dropped.
- Two rows with the same timestamp and different prices are an error.
- Malformed OHLC or non-positive prices are counted and dropped. They are
  not repaired.
- Missing bars are counted and left missing. Nothing is forward-filled,
  interpolated or invented.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from btc_research.data.candles import Candle
from btc_research.exceptions import DataIntegrityError


@dataclass
class Gap:
    after_open_time_ms: int
    before_open_time_ms: int
    missing_bars: int


@dataclass
class ValidationReport:
    n_input: int = 0
    n_valid: int = 0
    n_duplicate_identical: int = 0
    n_malformed: int = 0
    n_gaps: int = 0
    missing_bars: int = 0
    start_ms: int | None = None
    end_ms: int | None = None
    issues: list[str] = field(default_factory=list)
    gaps: list[Gap] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "n_input": self.n_input,
            "n_valid": self.n_valid,
            "n_duplicate_identical": self.n_duplicate_identical,
            "n_malformed": self.n_malformed,
            "n_gaps": self.n_gaps,
            "missing_bars": self.missing_bars,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "issues": list(self.issues[:50]),
            "issues_truncated": max(0, len(self.issues) - 50),
        }


def _malformed_reason(candle: Candle) -> str | None:
    prices = (candle.open, candle.high, candle.low, candle.close)
    if any(price != price or price == float("inf") or price == float("-inf") for price in prices):
        return "non-finite price"
    if any(price <= 0 for price in prices):
        return "non-positive price"
    if candle.volume != candle.volume or candle.volume < 0 or candle.volume == float("inf"):
        return "bad volume"
    if candle.high < candle.low:
        return "high below low"
    if candle.high + 1e-9 < max(candle.open, candle.close, candle.low):
        return "high below a print"
    if candle.low - 1e-9 > min(candle.open, candle.close, candle.high):
        return "low above a print"
    if candle.close_time_ms <= candle.open_time_ms:
        return "close_time not after open_time"
    if candle.open_time_ms < 10**12:
        return "open_time does not look like UTC milliseconds"
    return None


def _same_prices(left: Candle, right: Candle) -> bool:
    return (
        left.open == right.open
        and left.high == right.high
        and left.low == right.low
        and left.close == right.close
        and left.volume == right.volume
        and left.close_time_ms == right.close_time_ms
    )


def validate_candles(candles: list[Candle], interval_ms: int) -> tuple[list[Candle], ValidationReport]:
    if interval_ms <= 0:
        raise ValueError("interval_ms must be positive")
    report = ValidationReport(n_input=len(candles))
    cleaned: list[Candle] = []
    index_by_time: dict[int, int] = {}

    for offset, candle in enumerate(candles):
        reason = _malformed_reason(candle)
        if reason is not None:
            report.n_malformed += 1
            report.issues.append(f"row {offset}: {reason}")
            continue
        previous_index = index_by_time.get(candle.open_time_ms)
        if previous_index is not None:
            if _same_prices(cleaned[previous_index], candle):
                report.n_duplicate_identical += 1
                continue
            raise DataIntegrityError(
                f"conflicting duplicate at open_time_ms={candle.open_time_ms}"
            )
        if cleaned and candle.open_time_ms < cleaned[-1].open_time_ms:
            raise DataIntegrityError(
                "candles are not chronological at "
                f"open_time_ms={candle.open_time_ms}; refusing to sort silently"
            )
        index_by_time[candle.open_time_ms] = len(cleaned)
        cleaned.append(candle)

    gaps: list[Gap] = []
    missing = 0
    for previous, current in zip(cleaned, cleaned[1:]):
        delta = current.open_time_ms - previous.open_time_ms
        if delta == interval_ms:
            continue
        if delta < interval_ms:
            raise DataIntegrityError(
                f"bar spacing {delta}ms is shorter than interval {interval_ms}ms "
                f"at {current.open_time_ms}"
            )
        missing_bars = delta // interval_ms - 1
        if delta % interval_ms != 0:
            report.issues.append(
                f"unaligned gap at {previous.open_time_ms}: delta {delta}ms"
            )
        gaps.append(
            Gap(
                after_open_time_ms=previous.open_time_ms,
                before_open_time_ms=current.open_time_ms,
                missing_bars=int(missing_bars),
            )
        )
        missing += int(missing_bars)

    report.n_valid = len(cleaned)
    report.n_gaps = len(gaps)
    report.missing_bars = missing
    report.gaps = gaps
    if cleaned:
        report.start_ms = cleaned[0].open_time_ms
        report.end_ms = cleaned[-1].open_time_ms
    return cleaned, report
