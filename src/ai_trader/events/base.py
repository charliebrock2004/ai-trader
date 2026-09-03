"""Event sources: scheduled official releases and their verified values.

The whole strategy rests on one claim — *an objective number was published and
we read it correctly*. If that claim is shaky, nothing downstream matters, so
this layer is built to refuse rather than guess.

Refusal cases, all of which mean HOLD upstream:

* the release is not due yet, or is due but has not appeared
* the payload is malformed, or the value is not numeric
* two independent reads disagree (``conflict``)
* the value is present but the series or period does not match what was asked
* the source is unreachable

``ReleaseObservation.status`` carries which of those happened, and only
``VERIFIED`` is ever tradeable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Optional, Protocol, runtime_checkable

from ai_trader.clock import ensure_utc, iso


class ReleaseStatus(str, Enum):
    PENDING = "pending"          # scheduled, not yet published
    VERIFIED = "verified"        # published and independently confirmed
    UNVERIFIED = "unverified"    # published but only one read
    CONFLICT = "conflict"        # two reads disagreed
    UNAVAILABLE = "unavailable"  # source down or empty
    MALFORMED = "malformed"      # payload shape wrong
    AMBIGUOUS = "ambiguous"      # cannot tell which period this is


TRADEABLE_STATUSES = frozenset({ReleaseStatus.VERIFIED})


class EventDataError(RuntimeError):
    """The source could not be read. Callers fail closed."""

    def __init__(self, message: str, *, failure: str = "unavailable") -> None:
        super().__init__(message)
        self.failure = failure


@dataclass(frozen=True)
class ScheduledRelease:
    """One entry on the release calendar."""

    release_key: str          # e.g. "CPI:2026-03"
    series_key: str           # e.g. "BLS:CUUR0000SA0"
    source: str               # e.g. "BLS"
    label: str
    scheduled_at: str         # ISO UTC, when the number is due
    period: str               # e.g. "2026-03"
    unit: str = "index"
    notes: str = ""

    def is_due(self, now: datetime) -> bool:
        return ensure_utc(now) >= ensure_utc(datetime.fromisoformat(self.scheduled_at))

    def seconds_until(self, now: datetime) -> float:
        due = ensure_utc(datetime.fromisoformat(self.scheduled_at))
        return (due - ensure_utc(now)).total_seconds()

    def to_dict(self) -> dict[str, Any]:
        return {
            "release_key": self.release_key,
            "series_key": self.series_key,
            "source": self.source,
            "label": self.label,
            "scheduled_at": self.scheduled_at,
            "period": self.period,
            "unit": self.unit,
            "notes": self.notes,
        }


@dataclass(frozen=True)
class ReleaseObservation:
    """What we actually read, and how much we trust it."""

    release_key: str
    series_key: str
    source: str
    status: ReleaseStatus
    observed_at: str
    value: Optional[float] = None
    previous_value: Optional[float] = None
    yoy_change: Optional[float] = None
    published_at: Optional[str] = None
    second_read: Optional[float] = None
    verification_method: str = ""
    detail: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def tradeable(self) -> bool:
        return self.status in TRADEABLE_STATUSES and self.value is not None

    @property
    def verified(self) -> bool:
        return self.status is ReleaseStatus.VERIFIED

    def to_dict(self) -> dict[str, Any]:
        return {
            "release_key": self.release_key,
            "series_key": self.series_key,
            "source": self.source,
            "status": self.status.value,
            "observed_at": self.observed_at,
            "value": self.value,
            "previous_value": self.previous_value,
            "yoy_change": self.yoy_change,
            "published_at": self.published_at,
            "second_read": self.second_read,
            "verification_method": self.verification_method,
            "detail": self.detail,
            "tradeable": self.tradeable,
        }


@runtime_checkable
class EventSource(Protocol):
    """One family of official releases."""

    name: str

    def calendar(self, *, limit: int = 12) -> list[ScheduledRelease]:
        """Scheduled releases, soonest first."""

    def observe(self, release: ScheduledRelease) -> ReleaseObservation:
        """Read the release. Never raises for a missing number — returns a status."""

    def health(self) -> dict[str, Any]: ...


def verify_two_reads(
    first: Optional[float],
    second: Optional[float],
    *,
    tolerance: float = 0.0,
) -> tuple[ReleaseStatus, str]:
    """Compare two independent reads of the same number.

    Agreement is the only route to VERIFIED. A single read is UNVERIFIED, not
    verified-by-default: the whole point of the second read is that a transient
    parse or caching error in the first is otherwise invisible.
    """
    if first is None:
        return ReleaseStatus.UNAVAILABLE, "No value on the first read."
    if second is None:
        return ReleaseStatus.UNVERIFIED, "Only one read succeeded; not independently confirmed."
    if abs(float(first) - float(second)) <= tolerance:
        return ReleaseStatus.VERIFIED, f"Two independent reads agreed on {first}."
    return (
        ReleaseStatus.CONFLICT,
        f"Reads disagreed: {first} vs {second}. Refusing to trade an unresolved number.",
    )


def month_period(moment: datetime) -> str:
    m = ensure_utc(moment)
    return f"{m.year:04d}-{m.month:02d}"


def next_months(start: datetime, count: int) -> list[str]:
    periods: list[str] = []
    cursor = ensure_utc(start).replace(day=1)
    for _ in range(count):
        periods.append(month_period(cursor))
        # Step to the first of the next month without a calendar dependency.
        cursor = (cursor + timedelta(days=32)).replace(day=1)
    return periods


def iso_at(moment: datetime) -> str:
    return iso(moment)
