"""One clock for the whole engine.

Live paper trading, historical replay and tests all read time through a
``Clock``. Nothing in ``ai_trader`` may call ``datetime.now`` directly — that
is what makes a replay reproducible and a test deterministic.

``SystemClock`` is the only implementation that reads the wall clock.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from typing import Protocol, runtime_checkable


def ensure_utc(moment: datetime) -> datetime:
    """Coerce a datetime to timezone-aware UTC."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def iso(moment: datetime) -> str:
    return ensure_utc(moment).isoformat()


@runtime_checkable
class Clock(Protocol):
    """Time source. ``now`` is always timezone-aware UTC."""

    def now(self) -> datetime: ...

    def now_iso(self) -> str: ...

    def sleep(self, seconds: float) -> None:
        """Wait. Implementations may return immediately (replay, tests)."""


class SystemClock:
    """Wall-clock time. The only place ``datetime.now`` is allowed."""

    name = "system"

    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def now_iso(self) -> str:
        return iso(self.now())

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            threading.Event().wait(seconds)


class FrozenClock:
    """Deterministic clock for tests and replay.

    ``sleep`` advances the clock instead of blocking, so a session loop under
    test runs at full speed while still seeing time move forward.
    """

    name = "frozen"

    def __init__(self, start: datetime | str) -> None:
        if isinstance(start, str):
            start = datetime.fromisoformat(start.replace("Z", "+00:00"))
        self._now = ensure_utc(start)
        self._lock = threading.Lock()

    def now(self) -> datetime:
        with self._lock:
            return self._now

    def now_iso(self) -> str:
        return iso(self.now())

    def advance(self, seconds: float) -> datetime:
        with self._lock:
            self._now = self._now + timedelta(seconds=float(seconds))
            return self._now

    def set(self, moment: datetime | str) -> datetime:
        if isinstance(moment, str):
            moment = datetime.fromisoformat(moment.replace("Z", "+00:00"))
        with self._lock:
            self._now = ensure_utc(moment)
            return self._now

    def sleep(self, seconds: float) -> None:
        self.advance(seconds)


_DEFAULT = SystemClock()


def default_clock() -> Clock:
    """Process-wide fallback for call sites that have not been threaded yet."""
    return _DEFAULT
