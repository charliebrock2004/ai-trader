"""Exchange-rate providers.

The engine needs GBP<->USD to price a USD-quoted instrument against a GBP book.
Every provider here fails closed: a stale, malformed or unreachable rate raises
rather than returning a plausible-looking number. A missing rate must collapse
to HOLD upstream, never to a guessed conversion.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any, Optional, Protocol, runtime_checkable

from ai_trader.clock import Clock, default_clock, ensure_utc, iso
from ai_trader.money import FxRate, normalise_currency

#: A rate older than this is refused.
DEFAULT_MAX_AGE_SECONDS = 36 * 3600
DEFAULT_TIMEOUT = 8.0

#: Public, key-free, read-only. Not a broker. Follows the 2026 host move.
FRANKFURTER_URL = "https://api.frankfurter.dev/v1/latest"


class FxRateUnavailableError(RuntimeError):
    """No trustworthy rate. Callers must fail closed."""

    def __init__(self, message: str, *, failure: str = "unavailable") -> None:
        super().__init__(message)
        self.failure = failure


@runtime_checkable
class FxProvider(Protocol):
    def rate(self, base: str, quote: str) -> FxRate: ...

    def health(self) -> dict[str, Any]: ...


class PinnedFxProvider:
    """A fixed rate, supplied explicitly.

    Used by replay (the recorded rate), by tests, and as the configured
    fallback when the operator pins a rate deliberately. It is never a silent
    default — something has to hand it a number.
    """

    name = "pinned"

    def __init__(
        self,
        rates: dict[tuple[str, str], Decimal | float | str],
        *,
        as_of: str = "pinned",
        source: str = "pinned",
    ) -> None:
        self._rates: dict[tuple[str, str], Decimal] = {}
        for (base, quote), value in rates.items():
            key = (normalise_currency(base), normalise_currency(quote))
            self._rates[key] = Decimal(str(value))
        self.as_of = as_of
        self.source = source

    def rate(self, base: str, quote: str) -> FxRate:
        b = normalise_currency(base)
        q = normalise_currency(quote)
        if b == q:
            return FxRate(base=b, quote=q, rate=Decimal(1), as_of=self.as_of, source=self.source)
        if (b, q) in self._rates:
            return FxRate(
                base=b, quote=q, rate=self._rates[(b, q)], as_of=self.as_of, source=self.source
            )
        if (q, b) in self._rates:
            return FxRate(
                base=q, quote=b, rate=self._rates[(q, b)], as_of=self.as_of, source=self.source
            ).inverse()
        raise FxRateUnavailableError(
            f"No pinned FX rate for {b}->{q}.", failure="not_configured"
        )

    def health(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ready": bool(self._rates),
            "pairs": [f"{b}/{q}" for (b, q) in sorted(self._rates)],
            "source": self.source,
            "as_of": self.as_of,
            "notes": "Fixed rates. Used for replay, tests and deliberate operator pins.",
        }


class PublicFxFeed:
    """Read-only public reference rates (Frankfurter / ECB).

    GET only, no key, not a broker. Refuses any URL containing ``alpaca``,
    matching the market-data adapter's rule. Any transport or shape problem
    raises :class:`FxRateUnavailableError`.
    """

    name = "public-fx"

    def __init__(
        self,
        *,
        http_client: Any = None,
        clock: Optional[Clock] = None,
        timeout: float = DEFAULT_TIMEOUT,
        max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
        url: str = FRANKFURTER_URL,
    ) -> None:
        self._http = http_client
        self.clock = clock or default_clock()
        self.timeout = float(timeout)
        self.max_age_seconds = int(max_age_seconds)
        self.url = url
        self.http_calls: list[dict[str, Any]] = []
        self._cache: dict[tuple[str, str], tuple[float, FxRate]] = {}

    def _assert_safe_url(self, url: str) -> None:
        if "alpaca" in (url or "").lower():
            raise RuntimeError("Refusing to call a broker URL from the FX feed.")

    def _get(self, params: dict[str, Any]) -> Any:
        self._assert_safe_url(self.url)
        self.http_calls.append({"url": self.url, "params": params, "method": "GET"})
        client = self._http
        try:
            if client is None:
                import httpx

                with httpx.Client(timeout=self.timeout, follow_redirects=True) as owned:
                    response = owned.get(self.url, params=params)
                    response.raise_for_status()
                    return response.json()
            response = client.get(self.url, params=params, timeout=self.timeout)
            if hasattr(response, "raise_for_status"):
                response.raise_for_status()
            return response.json() if hasattr(response, "json") else response
        except FxRateUnavailableError:
            raise
        except Exception as exc:  # noqa: BLE001 — every transport failure is fail-closed
            name = type(exc).__name__
            text = str(exc).lower()
            if "timeout" in name.lower() or "timeout" in text:
                raise FxRateUnavailableError("FX rate request timed out.", failure="timeout") from exc
            raise FxRateUnavailableError(
                f"FX rate unavailable ({name}).", failure="network"
            ) from exc

    def rate(self, base: str, quote: str) -> FxRate:
        b = normalise_currency(base)
        q = normalise_currency(quote)
        if b == q:
            return FxRate(
                base=b, quote=q, rate=Decimal(1), as_of=self.clock.now_iso(), source=self.name
            )
        now = self.clock.now()
        cached = self._cache.get((b, q))
        if cached is not None:
            cached_at, value = cached
            if now.timestamp() - cached_at < 300:
                return value
        payload = self._get({"from": b, "to": q})
        rate = self._parse(payload, base=b, quote=q, now_iso=iso(now))
        self._cache[(b, q)] = (now.timestamp(), rate)
        return rate

    def _parse(self, payload: Any, *, base: str, quote: str, now_iso: str) -> FxRate:
        if not isinstance(payload, dict):
            raise FxRateUnavailableError("Malformed FX payload.", failure="malformed")
        rates = payload.get("rates")
        if not isinstance(rates, dict) or quote not in rates:
            raise FxRateUnavailableError("FX payload did not contain the pair.", failure="malformed")
        raw = rates.get(quote)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise FxRateUnavailableError("FX rate was not numeric.", failure="malformed")
        if raw <= 0:
            raise FxRateUnavailableError("FX rate was not positive.", failure="malformed")
        stamp = payload.get("date")
        as_of = now_iso
        if isinstance(stamp, str) and stamp:
            try:
                from datetime import datetime, timezone

                observed = ensure_utc(datetime.fromisoformat(stamp))
                # ECB publishes a date, not a timestamp; treat it as end of day.
                observed = observed + timedelta(hours=23, minutes=59)
                now = self.clock.now()
                if now - observed > timedelta(seconds=self.max_age_seconds):
                    raise FxRateUnavailableError(
                        "FX rate is stale.", failure="stale"
                    )
                as_of = iso(observed.replace(tzinfo=timezone.utc))
            except FxRateUnavailableError:
                raise
            except ValueError:
                as_of = now_iso
        return FxRate(
            base=base, quote=quote, rate=Decimal(str(raw)), as_of=as_of, source=self.name
        )

    def health(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ready": True,
            "url": self.url,
            "max_age_seconds": self.max_age_seconds,
            "network": bool(self.http_calls),
            "live": False,
            "broker": False,
            "notes": "Read-only public reference rates. Fails closed. Not a broker.",
        }
