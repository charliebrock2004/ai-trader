"""Read-only public crypto OHLCV.

Coinbase Exchange candles for BTC-USD and ETH-USD.
Never a broker. Never live trading. Completed bars only — no look-ahead.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from ai_trader.exceptions import (
    InvalidMarketDataError,
    MarketDataUnavailableError,
    StaleMarketDataError,
)
from ai_trader.market_data.base import MarketDataProvider
from ai_trader.market_data.timeframes import DEFAULT_TIMEFRAME, iso_utc, timeframe_seconds
from ai_trader.market_data.validation import parse_utc, validate_series
from ai_trader.types import Candle, CandleSeries, utc_now

COINBASE_REST = "https://api.exchange.coinbase.com"
COINBASE_CANDLES_PATH = "/products/{product}/candles"
PUBLIC_PRODUCTS = {
    "BTC-USD": "BTC-USD",
    "BTCUSD": "BTC-USD",
    "BTC/USD": "BTC-USD",
    "ETH-USD": "ETH-USD",
    "ETHUSD": "ETH-USD",
    "ETH/USD": "ETH-USD",
}
PUBLIC_GRANULARITY = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "1d": 86400,
}
STALE_BARS = 3
DEFAULT_TIMEOUT = 10.0
HEADERS = {
    "Accept": "application/json",
    "User-Agent": "AI-Trader-Paper/1.0",
}


def normalize_public_symbol(symbol: str) -> str:
    raw = (symbol or "").strip().upper().replace("/", "-")
    product = PUBLIC_PRODUCTS.get(raw)
    if product is None:
        raise MarketDataUnavailableError(
            "Public feed supports BTC-USD and ETH-USD only.",
            failure="unavailable",
        )
    return product


def public_granularity(timeframe: str) -> int:
    key = (timeframe or "").strip().lower()
    if key not in PUBLIC_GRANULARITY:
        allowed = ", ".join(sorted(PUBLIC_GRANULARITY))
        raise MarketDataUnavailableError(
            f"Public feed does not support timeframe '{timeframe}'. Allowed: {allowed}.",
            failure="unavailable",
        )
    timeframe_seconds(key)
    return PUBLIC_GRANULARITY[key]


class PublicCryptoFeed(MarketDataProvider):
    """GET-only Coinbase candles. Paper pipeline input. Not an order path."""

    name = "public"

    def __init__(
        self,
        *,
        http_client: Any = None,
        timeout: float = DEFAULT_TIMEOUT,
        now_fn: Optional[Callable[[], datetime]] = None,
        stale_bars: int = STALE_BARS,
    ) -> None:
        self._http = http_client
        self.timeout = float(timeout)
        self.now_fn = now_fn or utc_now
        self.stale_bars = max(1, int(stale_bars))
        self.http_calls: list[dict[str, Any]] = []

    def _now(self) -> datetime:
        moment = self.now_fn()
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc)

    def _assert_safe_url(self, url: str) -> None:
        if "alpaca" in (url or "").lower():
            raise RuntimeError("Refusing to call a broker URL from market data.")

    def _get(self, url: str, params: dict[str, Any]) -> Any:
        self._assert_safe_url(url)
        self.http_calls.append({"url": url, "params": params, "method": "GET"})
        client = self._http
        try:
            if client is None:
                import httpx

                with httpx.Client(timeout=self.timeout) as owned:
                    response = owned.get(url, headers=HEADERS, params=params)
                    response.raise_for_status()
                    return response.json()
            response = client.get(url, headers=HEADERS, params=params, timeout=self.timeout)
            if hasattr(response, "raise_for_status"):
                response.raise_for_status()
            if hasattr(response, "json"):
                return response.json()
            return response
        except (MarketDataUnavailableError, StaleMarketDataError, InvalidMarketDataError):
            raise
        except Exception as exc:  # noqa: BLE001 — any transport failure is fail-closed
            name = type(exc).__name__
            text = str(exc).lower()
            if "timeout" in name.lower() or "timeout" in text:
                raise MarketDataUnavailableError(
                    "Public market data timed out.", failure="timeout"
                ) from exc
            if "json" in name.lower() or "json" in text or "malformed" in text:
                raise MarketDataUnavailableError(
                    "Malformed public market-data payload.", failure="malformed"
                ) from exc
            raise MarketDataUnavailableError(
                f"Public market data unavailable ({name}).", failure="network"
            ) from exc

    def candles(
        self,
        symbol: str,
        *,
        timeframe: str | None = None,
        limit: int = 48,
        scenario: str | None = None,
    ) -> CandleSeries:
        product = normalize_public_symbol(symbol)
        tf = (timeframe or DEFAULT_TIMEFRAME).strip().lower()
        granularity = public_granularity(tf)
        if limit < 2:
            raise MarketDataUnavailableError(
                "Public feed needs at least 2 completed candles.", failure="unavailable"
            )
        url = COINBASE_REST + COINBASE_CANDLES_PATH.format(product=product)
        payload = self._get(url, {"granularity": granularity})
        series = self._parse(
            payload,
            product=product,
            timeframe=tf,
            granularity=granularity,
            limit=limit,
        )
        return validate_series(series)

    def _parse(
        self,
        payload: Any,
        *,
        product: str,
        timeframe: str,
        granularity: int,
        limit: int,
    ) -> CandleSeries:
        if not isinstance(payload, list):
            raise MarketDataUnavailableError(
                "Malformed public market-data payload.", failure="malformed"
            )
        now = self._now()
        parsed: list[tuple[datetime, Candle]] = []
        for row in payload:
            candle = self._row_to_candle(row)
            start = parse_utc(candle.timestamp)
            if start >= now:
                continue
            close_at = start + timedelta(seconds=granularity)
            if close_at > now:
                continue
            parsed.append((start, candle))
        if not parsed:
            raise MarketDataUnavailableError(
                "Public feed returned no completed candles.", failure="unavailable"
            )
        parsed.sort(key=lambda item: item[0])
        unique: list[Candle] = []
        previous: datetime | None = None
        for start, candle in parsed:
            if previous is not None and start == previous:
                raise MarketDataUnavailableError(
                    "Malformed public market-data payload: duplicate timestamps.",
                    failure="malformed",
                )
            unique.append(candle)
            previous = start
        chosen = unique[-limit:]
        if len(chosen) < 2:
            raise MarketDataUnavailableError(
                "Public feed returned too few completed candles.", failure="unavailable"
            )
        last_start = parse_utc(chosen[-1].timestamp)
        last_close = last_start + timedelta(seconds=granularity)
        age = (now - last_close).total_seconds()
        if age >= self.stale_bars * granularity:
            raise StaleMarketDataError(
                "Public market data is stale. Last completed candle is too old."
            )
        if last_start >= now:
            raise MarketDataUnavailableError(
                "Public feed tried to include a future candle.", failure="malformed"
            )
        return CandleSeries(
            symbol=product,
            timeframe=timeframe,
            scenario="public",
            seed=0,
            candles=tuple(chosen),
            source=self.name,
        )

    def _row_to_candle(self, row: Any) -> Candle:
        if not isinstance(row, (list, tuple)) or len(row) < 6:
            raise MarketDataUnavailableError(
                "Malformed public market-data payload.", failure="malformed"
            )
        try:
            time_raw, low, high, open_, close, volume = row[:6]
            start = datetime.fromtimestamp(int(float(time_raw)), tz=timezone.utc)
            candle = Candle(
                timestamp=iso_utc(start),
                open=float(open_),
                high=float(high),
                low=float(low),
                close=float(close),
                volume=float(volume),
            )
        except (TypeError, ValueError, OverflowError) as exc:
            raise MarketDataUnavailableError(
                "Malformed public market-data payload.", failure="malformed"
            ) from exc
        return candle

    def health(self) -> dict:
        return {
            "name": self.name,
            "ready": True,
            "provider": "coinbase",
            "symbols": ["BTC-USD", "ETH-USD"],
            "timeframes": sorted(PUBLIC_GRANULARITY),
            "live": False,
            "broker": False,
            "notes": "Read-only public OHLCV. Completed candles only. Not a broker.",
        }
