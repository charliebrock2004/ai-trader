"""BLS CPI release source.

The first event family, chosen because it satisfies the three things the
strategy needs: a published machine-readable schedule, an unambiguous released
number, and prediction-market contracts that resolve against it.

Network dependency (isolated on purpose)
----------------------------------------
This adapter talks to the BLS public timeseries API:

    POST https://api.bls.gov/publicAPI/v2/timeseries/data/

The HTTP client is injected, so every code path here is exercised offline in
the tests against recorded payload shapes. To run it against the real service
you need outbound network access, and optionally a free BLS registration key
(``BLS_API_KEY``) to lift the anonymous daily quota. Without either, the
adapter reports ``UNAVAILABLE`` and the agent HOLDs — which is the correct
behaviour, not a degraded one.

Verification
------------
Every reading is taken twice: once for the target period and once over a wider
window that must contain the same datapoint. Both must agree before the value
is VERIFIED. A single successful read is UNVERIFIED and is not tradeable.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

from ai_trader.clock import Clock, default_clock, ensure_utc, iso
from ai_trader.events.base import (
    EventDataError,
    ReleaseObservation,
    ReleaseStatus,
    ScheduledRelease,
    verify_two_reads,
)

BLS_API_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"

#: CPI-U, US city average, all items, not seasonally adjusted.
CPI_SERIES_ID = "CUUR0000SA0"
SERIES_KEY = f"BLS:{CPI_SERIES_ID}"

DEFAULT_TIMEOUT = 20.0

#: BLS publishes CPI mid-month at 08:30 US Eastern for the prior month. The
#: exact day varies, so the calendar is an estimate used only for *scheduling* —
#: a release is never treated as published until the API actually returns it.
CPI_NOMINAL_DAY = 12
CPI_NOMINAL_HOUR_UTC = 13  # 08:30 ET is 12:30/13:30 UTC depending on DST.

_PERIOD_TO_MONTH = {f"M{i:02d}": i for i in range(1, 13)}


class BLSCPISource:
    """Reads CPI from the BLS public API. Fails closed on anything unclear."""

    name = "BLS"
    series_key = SERIES_KEY

    def __init__(
        self,
        *,
        http_client: Any = None,
        clock: Optional[Clock] = None,
        api_key: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
        url: str = BLS_API_URL,
    ) -> None:
        self._http = http_client
        self.clock = clock or default_clock()
        self.api_key = api_key
        self.timeout = float(timeout)
        self.url = url
        self.http_calls: list[dict[str, Any]] = []

    # -- calendar ---------------------------------------------------------
    def calendar(self, *, limit: int = 12) -> list[ScheduledRelease]:
        """Upcoming CPI releases, soonest first.

        Each entry covers the *previous* month's data, which is how CPI is
        published. Times are nominal; publication is confirmed by the API.
        """
        now = ensure_utc(self.clock.now())
        releases: list[ScheduledRelease] = []
        cursor = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        # Start one month back so a release published in the last few days is
        # still on the calendar and can be observed.
        cursor = (cursor - timedelta(days=1)).replace(day=1)
        for _ in range(limit):
            scheduled = cursor.replace(
                day=CPI_NOMINAL_DAY, hour=CPI_NOMINAL_HOUR_UTC, minute=30
            )
            data_month = (cursor - timedelta(days=1)).replace(day=1)
            period = f"{data_month.year:04d}-{data_month.month:02d}"
            releases.append(
                ScheduledRelease(
                    release_key=f"CPI:{period}",
                    series_key=self.series_key,
                    source=self.name,
                    label=f"US CPI-U, all items, {period}",
                    scheduled_at=iso(scheduled),
                    period=period,
                    unit="index",
                    notes=(
                        "Nominal release time. Publication is confirmed by the API, "
                        "never assumed from the schedule."
                    ),
                )
            )
            cursor = (cursor + timedelta(days=32)).replace(day=1)
        return sorted(releases, key=lambda r: r.scheduled_at)

    # -- observation ------------------------------------------------------
    def observe(self, release: ScheduledRelease) -> ReleaseObservation:
        now = ensure_utc(self.clock.now())
        observed_at = iso(now)

        def result(status: ReleaseStatus, detail: str, **extra: Any) -> ReleaseObservation:
            return ReleaseObservation(
                release_key=release.release_key,
                series_key=release.series_key,
                source=self.name,
                status=status,
                observed_at=observed_at,
                detail=detail,
                **extra,
            )

        try:
            year, month = self._split_period(release.period)
        except ValueError:
            return result(ReleaseStatus.AMBIGUOUS, f"Unparseable period {release.period!r}.")

        # Read 1: the target year alone. Both the fetch and the parse must fail
        # closed — observe() returns a status, it never raises for bad data.
        try:
            narrow = self._fetch(start_year=year, end_year=year)
            first = self._extract(narrow, year=year, month=month)
        except EventDataError as exc:
            return result(
                ReleaseStatus.MALFORMED if exc.failure == "malformed" else ReleaseStatus.UNAVAILABLE,
                str(exc),
            )

        if first is None:
            if not release.is_due(now):
                return result(
                    ReleaseStatus.PENDING,
                    f"{release.release_key} is not due until {release.scheduled_at}.",
                )
            return result(
                ReleaseStatus.PENDING,
                f"{release.release_key} is due but has not been published yet.",
            )

        # Read 2: a wider window that must contain the same datapoint. A cache
        # or parse fault that affects one request rarely affects both.
        try:
            wide = self._fetch(start_year=year - 1, end_year=year)
            second = self._extract(wide, year=year, month=month)
            previous = self._extract(wide, year=year - 1, month=month)
        except EventDataError as exc:
            return result(
                ReleaseStatus.UNVERIFIED,
                f"Second read failed ({exc}). One read is not confirmation.",
                value=first["value"],
                published_at=first.get("published_at"),
            )
        second_value = second["value"] if second else None

        status, detail = verify_two_reads(first["value"], second_value)
        previous_value = previous["value"] if previous else None
        yoy = None
        if status is ReleaseStatus.VERIFIED and previous_value:
            yoy = round((first["value"] / previous_value - 1.0) * 100.0, 4)

        return ReleaseObservation(
            release_key=release.release_key,
            series_key=release.series_key,
            source=self.name,
            status=status,
            observed_at=observed_at,
            value=first["value"],
            previous_value=previous_value,
            yoy_change=yoy,
            published_at=first.get("published_at"),
            second_read=second_value,
            verification_method="two independent reads over different windows",
            detail=detail,
            raw={"first": first, "second": second},
        )

    # -- transport --------------------------------------------------------
    def _fetch(self, *, start_year: int, end_year: int) -> Any:
        if "alpaca" in self.url.lower():
            raise EventDataError("Refusing to call a broker URL from an event source.")
        body: dict[str, Any] = {
            "seriesid": [CPI_SERIES_ID],
            "startyear": str(start_year),
            "endyear": str(end_year),
        }
        if self.api_key:
            body["registrationkey"] = self.api_key
        self.http_calls.append({"url": self.url, "start": start_year, "end": end_year})
        client = self._http
        try:
            if client is None:
                import httpx

                with httpx.Client(timeout=self.timeout) as owned:
                    response = owned.post(
                        self.url, json=body, headers={"Content-Type": "application/json"}
                    )
                    response.raise_for_status()
                    return response.json()
            response = client.post(
                self.url, json=body, headers={"Content-Type": "application/json"},
                timeout=self.timeout,
            )
            if hasattr(response, "raise_for_status"):
                response.raise_for_status()
            return response.json() if hasattr(response, "json") else response
        except EventDataError:
            raise
        except Exception as exc:  # noqa: BLE001 — any transport failure is fail-closed
            name = type(exc).__name__
            text = str(exc).lower()
            if "timeout" in name.lower() or "timeout" in text:
                raise EventDataError("BLS request timed out.", failure="timeout") from exc
            raise EventDataError(f"BLS unavailable ({name}).", failure="network") from exc

    # -- parsing ----------------------------------------------------------
    @staticmethod
    def _split_period(period: str) -> tuple[int, int]:
        year_raw, month_raw = str(period).split("-")
        year, month = int(year_raw), int(month_raw)
        if not 1 <= month <= 12:
            raise ValueError(period)
        return year, month

    def _extract(self, payload: Any, *, year: int, month: int) -> Optional[dict[str, Any]]:
        """Pull one datapoint out of a BLS response, or None if it is absent."""
        if not isinstance(payload, dict):
            raise EventDataError("Malformed BLS payload.", failure="malformed")
        status = str(payload.get("status") or "")
        if status and status != "REQUEST_SUCCEEDED":
            messages = payload.get("message") or []
            raise EventDataError(
                f"BLS request was not successful: {status} {messages}", failure="network"
            )
        results = payload.get("Results")
        if not isinstance(results, dict):
            raise EventDataError("Malformed BLS payload: no Results.", failure="malformed")
        series_list = results.get("series")
        if not isinstance(series_list, list) or not series_list:
            raise EventDataError("Malformed BLS payload: no series.", failure="malformed")
        for series in series_list:
            if not isinstance(series, dict):
                continue
            if str(series.get("seriesID") or "") != CPI_SERIES_ID:
                # A payload for a different series must never be read as ours.
                continue
            for row in series.get("data") or []:
                if not isinstance(row, dict):
                    continue
                try:
                    row_year = int(row.get("year"))
                except (TypeError, ValueError):
                    continue
                row_month = _PERIOD_TO_MONTH.get(str(row.get("period") or ""))
                if row_month is None or row_year != year or row_month != month:
                    continue
                raw_value = row.get("value")
                try:
                    value = float(str(raw_value).replace(",", ""))
                except (TypeError, ValueError):
                    raise EventDataError(
                        f"BLS value {raw_value!r} is not numeric.", failure="malformed"
                    ) from None
                return {
                    "value": value,
                    "year": row_year,
                    "month": row_month,
                    "period_name": row.get("periodName"),
                    "published_at": row.get("latest") and iso(self.clock.now()) or None,
                    "footnotes": row.get("footnotes"),
                }
        return None

    def health(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ready": True,
            "series": CPI_SERIES_ID,
            "url": self.url,
            "api_key_configured": bool(self.api_key),
            "network": bool(self.http_calls),
            "live": False,
            "broker": False,
            "notes": (
                "Read-only official statistics. Every value is read twice and must "
                "agree before it is tradeable. Not a broker."
            ),
        }


class FixtureEventSource:
    """Deterministic offline event source for tests, replay and demos.

    Behaves exactly like a real source — including the ability to be pending,
    conflicted or unavailable — without touching the network.
    """

    name = "FIXTURE"

    def __init__(
        self,
        releases: Optional[list[ScheduledRelease]] = None,
        observations: Optional[dict[str, ReleaseObservation]] = None,
        *,
        clock: Optional[Clock] = None,
    ) -> None:
        self.clock = clock or default_clock()
        self._releases = list(releases or [])
        self._observations = dict(observations or {})

    def add(self, release: ScheduledRelease, observation: Optional[ReleaseObservation] = None) -> None:
        self._releases.append(release)
        if observation is not None:
            self._observations[release.release_key] = observation

    def calendar(self, *, limit: int = 12) -> list[ScheduledRelease]:
        return sorted(self._releases, key=lambda r: r.scheduled_at)[:limit]

    def observe(self, release: ScheduledRelease) -> ReleaseObservation:
        found = self._observations.get(release.release_key)
        if found is not None:
            return found
        return ReleaseObservation(
            release_key=release.release_key,
            series_key=release.series_key,
            source=self.name,
            status=ReleaseStatus.PENDING,
            observed_at=self.clock.now_iso(),
            detail="No fixture observation registered for this release.",
        )

    def health(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ready": True,
            "releases": len(self._releases),
            "live": False,
            "notes": "Deterministic offline event source. No network.",
        }
