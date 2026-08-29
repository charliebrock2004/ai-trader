from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from ai_trader.exceptions import InvalidMarketDataError, MarketDataUnavailableError, StaleMarketDataError
from ai_trader.market_data.public import PublicCryptoFeed, normalize_public_symbol
from ai_trader.market_data.validation import parse_utc
from ai_trader.safety import LIVE_TRADING_ALLOWED
from ai_trader.session.config import PaperSessionConfig
from ai_trader.session.runner import PaperSession


NOW = datetime(2026, 8, 28, 15, 0, tzinfo=timezone.utc)


class FakeResponse:
    def __init__(self, payload, status=200) -> None:
        self._payload = payload
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        if isinstance(self._payload, str):
            raise ValueError("not json")
        return self._payload


class FakeGet:
    def __init__(self, payload=None, error=None, status=200) -> None:
        self.payload = payload if payload is not None else []
        self.error = error
        self.status = status
        self.calls: list[dict] = []

    def get(self, url, headers=None, params=None, timeout=None):
        self.calls.append({"url": url, "params": params, "headers": headers, "timeout": timeout})
        if self.error:
            raise self.error
        return FakeResponse(self.payload, status=self.status)


def coinbase_rows(
    n: int = 24,
    *,
    granularity: int = 300,
    now: datetime = NOW,
    incomplete: bool = False,
    stale_bars: int = 0,
    high_below_low: bool = False,
) -> list[list[float]]:
    last_close = now - timedelta(seconds=stale_bars * granularity)
    rows: list[list[float]] = []
    for i in range(n):
        start = last_close - timedelta(seconds=granularity * (n - i))
        price = 100000.0 + i * 12.5
        high = price - 40 if high_below_low else price + 40
        low = price + 10 if high_below_low else price - 40
        rows.append([int(start.timestamp()), low, high, price, price + 5, 3.25])
    if incomplete:
        rows.append([int(now.timestamp()), 1.0, 2.0, 1.0, 1.5, 1.0])
        rows.append([int((now + timedelta(seconds=granularity)).timestamp()), 1.0, 2.0, 1.0, 1.5, 1.0])
    rows.reverse()
    return rows


def _feed(payload, **kwargs) -> tuple[PublicCryptoFeed, FakeGet]:
    http = FakeGet(payload, **kwargs)
    feed = PublicCryptoFeed(http_client=http, now_fn=lambda: NOW)
    return feed, http


def test_parses_completed_coinbase_candles() -> None:
    feed, http = _feed(coinbase_rows(24))
    series = feed.candles("BTC-USD", timeframe="5m", limit=24)
    assert series.symbol == "BTC-USD"
    assert series.source == "public"
    assert len(series.candles) == 24
    assert series.candles[0].timestamp < series.candles[-1].timestamp
    last_start = parse_utc(series.candles[-1].timestamp)
    assert last_start + timedelta(seconds=300) <= NOW
    assert all(c.high >= max(c.open, c.close, c.low) for c in series.candles)
    assert http.calls
    assert "alpaca" not in http.calls[0]["url"].lower()
    assert http.calls[0]["url"].endswith("/products/BTC-USD/candles")
    assert http.calls[0]["params"]["granularity"] == 300


def test_eth_and_btc_slash_aliases() -> None:
    assert normalize_public_symbol("btc/usd") == "BTC-USD"
    assert normalize_public_symbol("ETHUSD") == "ETH-USD"
    feed, _http = _feed(coinbase_rows(8))
    series = feed.candles("ETH/USD", timeframe="5m", limit=8)
    assert series.symbol == "ETH-USD"


def test_drops_incomplete_and_future_bars() -> None:
    rows = coinbase_rows(24, incomplete=True)
    feed, _http = _feed(rows)
    series = feed.candles("BTC-USD", timeframe="5m", limit=24)
    assert len(series.candles) == 24
    for candle in series.candles:
        start = parse_utc(candle.timestamp)
        assert start < NOW
        assert start + timedelta(seconds=300) <= NOW


def test_malformed_payload_is_rejected() -> None:
    feed, _http = _feed("<<<not json>>>")
    with pytest.raises(MarketDataUnavailableError) as exc:
        feed.candles("BTC-USD", limit=24)
    assert exc.value.failure in {"malformed", "network"}

    feed2, _http2 = _feed({"message": "nope"})
    with pytest.raises(MarketDataUnavailableError) as exc2:
        feed2.candles("BTC-USD", limit=24)
    assert exc2.value.failure == "malformed"

    feed3, _http3 = _feed([[1, 2]])
    with pytest.raises(MarketDataUnavailableError) as exc3:
        feed3.candles("BTC-USD", limit=24)
    assert exc3.value.failure == "malformed"

    feed4, _http4 = _feed(coinbase_rows(24, high_below_low=True))
    with pytest.raises((InvalidMarketDataError, MarketDataUnavailableError)):
        feed4.candles("BTC-USD", limit=24)


def test_connection_failure() -> None:
    feed, http = _feed([], error=ConnectionError("down"))
    with pytest.raises(MarketDataUnavailableError) as exc:
        feed.candles("BTC-USD", limit=24)
    assert exc.value.failure == "network"
    assert http.calls


def test_timeout_failure() -> None:
    feed, _http = _feed([], error=TimeoutError("timed out"))
    with pytest.raises(MarketDataUnavailableError) as exc:
        feed.candles("ETH-USD", limit=24)
    assert exc.value.failure == "timeout"


def test_stale_data_is_rejected() -> None:
    feed, _http = _feed(coinbase_rows(24, stale_bars=4))
    with pytest.raises(StaleMarketDataError):
        feed.candles("BTC-USD", timeframe="5m", limit=24)


def test_http_error_is_unavailable() -> None:
    feed, _http = _feed(coinbase_rows(24), status=500)
    with pytest.raises(MarketDataUnavailableError):
        feed.candles("BTC-USD", limit=24)


def test_unknown_symbol_and_timeframe() -> None:
    feed, http = _feed(coinbase_rows(8))
    with pytest.raises(MarketDataUnavailableError):
        feed.candles("SIM-UP", limit=8)
    assert http.calls == []
    with pytest.raises(MarketDataUnavailableError):
        feed.candles("BTC-USD", timeframe="4h", limit=8)


def test_refuses_broker_url() -> None:
    feed, _http = _feed([])
    with pytest.raises(RuntimeError, match="broker"):
        feed._get("https://example.invalid/alpaca/candles", {})
    assert LIVE_TRADING_ALLOWED is False


def test_session_walks_public_tape_without_look_ahead() -> None:
    feed, http = _feed(coinbase_rows(24, incomplete=True))
    session = PaperSession(
        PaperSessionConfig(symbol="BTC-USD", bars=24, warmup=8, grok_frequency=8, source="public"),
        market_data=feed,
    )
    report = session.start()
    assert report["ok"] is True
    assert report["look_ahead"] is False
    assert report["live"] is False
    assert report["real_market_data"] is True
    assert report["market_data"] == "public"
    assert report["symbol"] == "BTC-USD"
    assert report["bars"] == 24
    assert report["broker_submit_calls"] == 0
    assert report["grok"] == "STOPPED"
    assert all(d["bar_count"] == d["bar"] + 1 for d in report["ai_decisions"])
    assert all("alpaca" not in c["url"].lower() for c in http.calls)


def test_session_connection_failure_holds() -> None:
    feed, _http = _feed([], error=ConnectionError("down"))
    session = PaperSession(
        PaperSessionConfig(symbol="BTC-USD", bars=24, source="public"),
        market_data=feed,
    )
    report = session.start()
    assert report["ok"] is False
    assert report["decision"] == "HOLD"
    assert report["grok"] == "STOPPED"
    assert report["trades"] == 0
    assert report["fills"] == []
    assert report["live"] is False
    assert report["data_failure"] == "network"
    assert report["broker"] == "NOT USED"


def test_session_malformed_holds() -> None:
    feed, _http = _feed([["bad"]])
    session = PaperSession(
        PaperSessionConfig(symbol="ETH-USD", bars=16, source="public"),
        market_data=feed,
    )
    report = session.start()
    assert report["decision"] == "HOLD"
    assert report["grok"] == "STOPPED"
    assert report["trades"] == 0
    assert report["data_failure"] == "malformed"


def test_session_stale_holds() -> None:
    feed, _http = _feed(coinbase_rows(24, stale_bars=5))
    session = PaperSession(
        PaperSessionConfig(symbol="BTC-USD", bars=24, source="public"),
        market_data=feed,
    )
    report = session.start()
    assert report["decision"] == "HOLD"
    assert report["grok"] == "STOPPED"
    assert report["trades"] == 0
    assert report["data_failure"] == "stale"
    assert report["stopped"] is True


def test_historical_still_refused() -> None:
    from ai_trader.exceptions import HistoricalDataNotConfiguredError

    with pytest.raises(HistoricalDataNotConfiguredError):
        PaperSessionConfig(source="historical").validate()
