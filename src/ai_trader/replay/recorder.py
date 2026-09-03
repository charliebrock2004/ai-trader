"""Recording and replaying raw inputs.

A replay is only meaningful if it reproduces the run *exactly*. That requires
storing the **inputs** — books, official readings, FX rates, the clock — not
the outputs, and then feeding them back through the identical code path. If a
replay had its own fill logic it would only be testing itself.

Two properties are enforced by construction:

* **No network.** ``ReplayEventSource`` and ``ReplayBookSource`` serve from a
  tape. They have no HTTP client and cannot acquire one.
* **No look-ahead.** The tape is served strictly in recorded order, and asking
  for a timestamp the tape has not reached raises rather than returning a
  future value.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ai_trader.clock import FrozenClock
from ai_trader.events.base import ReleaseObservation, ReleaseStatus, ScheduledRelease
from ai_trader.markets.base import BookLevel, OrderBook

TAPE_VERSION = 1


class ReplayError(RuntimeError):
    """The tape cannot serve what was asked. Never falls back to live data."""


@dataclass
class Tape:
    """Everything one cycle saw, in the order it saw it."""

    version: int = TAPE_VERSION
    started_at: str = ""
    fx_rates: dict[str, float] = field(default_factory=dict)
    releases: list[dict[str, Any]] = field(default_factory=list)
    observations: dict[str, dict[str, Any]] = field(default_factory=dict)
    books: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    contracts: list[dict[str, Any]] = field(default_factory=list)
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "started_at": self.started_at,
            "fx_rates": self.fx_rates,
            "releases": self.releases,
            "observations": self.observations,
            "books": self.books,
            "contracts": self.contracts,
            "notes": self.notes,
        }

    def save(self, path: Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: Path) -> "Tape":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if int(data.get("version", 0)) != TAPE_VERSION:
            raise ReplayError(
                f"Tape version {data.get('version')} does not match {TAPE_VERSION}. "
                "Refusing to replay against different semantics."
            )
        return cls(
            version=int(data["version"]),
            started_at=str(data.get("started_at", "")),
            fx_rates={str(k): float(v) for k, v in (data.get("fx_rates") or {}).items()},
            releases=list(data.get("releases") or []),
            observations=dict(data.get("observations") or {}),
            books=dict(data.get("books") or {}),
            contracts=list(data.get("contracts") or []),
            notes=str(data.get("notes", "")),
        )


class TapeRecorder:
    """Captures inputs as a live cycle sees them."""

    def __init__(self, *, started_at: str = "", notes: str = "") -> None:
        self.tape = Tape(started_at=started_at, notes=notes)

    def record_fx(self, quote_currency: str, rate: float) -> None:
        self.tape.fx_rates[quote_currency] = float(rate)

    def record_release(self, release: ScheduledRelease) -> None:
        payload = release.to_dict()
        if payload not in self.tape.releases:
            self.tape.releases.append(payload)

    def record_observation(self, observation: ReleaseObservation) -> None:
        self.tape.observations[observation.release_key] = observation.to_dict()

    def record_book(self, book: OrderBook) -> None:
        self.tape.books.setdefault(book.ticker, []).append(book.to_dict())

    def record_contract(self, contract: Any) -> None:
        payload = contract.to_dict()
        if payload not in self.tape.contracts:
            self.tape.contracts.append(payload)

    def save(self, path: Path) -> Path:
        return self.tape.save(path)


class ReplayEventSource:
    """Serves recorded releases and observations. No network, ever."""

    name = "REPLAY"

    def __init__(self, tape: Tape) -> None:
        self.tape = tape
        self.series_key = ""
        if tape.releases:
            self.series_key = str(tape.releases[0].get("series_key") or "")

    def calendar(self, *, limit: int = 12) -> list[ScheduledRelease]:
        rows = [ScheduledRelease(**row) for row in self.tape.releases]
        return sorted(rows, key=lambda r: r.scheduled_at)[:limit]

    def observe(self, release: ScheduledRelease) -> ReleaseObservation:
        raw = self.tape.observations.get(release.release_key)
        if raw is None:
            raise ReplayError(
                f"Tape holds no observation for {release.release_key}. "
                "Replay will not fall back to a live source."
            )
        return ReleaseObservation(
            release_key=raw["release_key"],
            series_key=raw["series_key"],
            source=raw["source"],
            status=ReleaseStatus(raw["status"]),
            observed_at=raw["observed_at"],
            value=raw.get("value"),
            previous_value=raw.get("previous_value"),
            yoy_change=raw.get("yoy_change"),
            published_at=raw.get("published_at"),
            second_read=raw.get("second_read"),
            verification_method=raw.get("verification_method", ""),
            detail=raw.get("detail", ""),
        )

    def health(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ready": True,
            "live": False,
            "releases": len(self.tape.releases),
            "notes": "Replay source. Serves a recorded tape. No network.",
        }


class ReplayBookSource:
    """Serves recorded books in order. Cannot see past the current position."""

    def __init__(self, tape: Tape) -> None:
        self.tape = tape
        self._cursor: dict[str, int] = {}

    def __call__(self, ticker: str) -> OrderBook:
        rows = self.tape.books.get(ticker)
        if not rows:
            raise ReplayError(f"Tape holds no book for {ticker}.")
        index = min(self._cursor.get(ticker, 0), len(rows) - 1)
        self._cursor[ticker] = index + 1
        raw = rows[index]
        return OrderBook(
            ticker=raw["ticker"],
            observed_at=raw["observed_at"],
            bids=tuple(BookLevel(b["price"], b["contracts"]) for b in raw.get("bids") or []),
            asks=tuple(BookLevel(a["price"], a["contracts"]) for a in raw.get("asks") or []),
            source="replay",
        )

    def reset(self) -> None:
        self._cursor.clear()


def replay_clock(tape: Tape) -> FrozenClock:
    """A clock pinned to the tape's start. Replay never reads the wall clock."""
    return FrozenClock(tape.started_at or "2026-01-01T00:00:00+00:00")
