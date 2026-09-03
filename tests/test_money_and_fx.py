"""Accounting units. A currency mistake here is a silent wrong answer everywhere."""

from __future__ import annotations

from decimal import Decimal

import pytest

from ai_trader.clock import FrozenClock, SystemClock, ensure_utc
from ai_trader.fx.provider import FxRateUnavailableError, PinnedFxProvider, PublicFxFeed
from ai_trader.instruments import InstrumentSpec, instrument_for
from ai_trader.money import (
    BASE_CURRENCY,
    CurrencyMismatchError,
    FxRate,
    MissingFxRateError,
    Money,
    convert,
)


# --------------------------------------------------------------------------
# Money
# --------------------------------------------------------------------------
def test_base_currency_is_gbp() -> None:
    assert BASE_CURRENCY == "GBP"


def test_money_rejects_mixing_currencies() -> None:
    gbp = Money.of(100, "GBP")
    usd = Money.of(100, "USD")
    with pytest.raises(CurrencyMismatchError):
        _ = gbp + usd
    with pytest.raises(CurrencyMismatchError):
        _ = gbp - usd
    with pytest.raises(CurrencyMismatchError):
        _ = gbp < usd


def test_money_arithmetic_stays_exact() -> None:
    total = Money.zero("GBP")
    for _ in range(10):
        total = total + Money.of("0.1", "GBP")
    assert total.float_amount == 1.00
    assert total.amount == Decimal("1.0")


def test_unknown_currency_is_refused() -> None:
    with pytest.raises(ValueError):
        Money.of(1, "XYZ")


# --------------------------------------------------------------------------
# Conversion
# --------------------------------------------------------------------------
def test_same_currency_conversion_needs_no_rate() -> None:
    amount = Money.of(100, "GBP")
    assert convert(amount, "GBP", None) == amount


def test_conversion_without_a_rate_fails_closed() -> None:
    with pytest.raises(MissingFxRateError):
        convert(Money.of(100, "GBP"), "USD", None)


def test_conversion_applies_the_rate_in_both_directions() -> None:
    rate = FxRate(base="GBP", quote="USD", rate=Decimal("1.25"), as_of="t", source="test")
    assert convert(Money.of(100, "GBP"), "USD", rate).float_amount == 125.00
    assert convert(Money.of(125, "USD"), "GBP", rate).float_amount == 100.00


def test_round_trip_conversion_returns_the_original() -> None:
    rate = FxRate(base="GBP", quote="USD", rate=Decimal("1.2734"), as_of="t", source="test")
    start = Money.of("100.00", "GBP")
    there = convert(start, "USD", rate)
    back = convert(there, "GBP", rate)
    assert back.float_amount == start.float_amount


def test_a_rate_for_the_wrong_pair_is_refused() -> None:
    rate = FxRate(base="EUR", quote="USD", rate=Decimal("1.1"), as_of="t", source="test")
    with pytest.raises(MissingFxRateError):
        convert(Money.of(100, "GBP"), "USD", rate)


def test_inverse_rate_is_consistent() -> None:
    rate = FxRate(base="GBP", quote="USD", rate=Decimal("1.25"), as_of="t", source="test")
    inv = rate.inverse()
    assert inv.base == "USD" and inv.quote == "GBP"
    assert convert(Money.of(125, "USD"), "GBP", inv).float_amount == 100.00


def test_non_positive_rate_is_refused() -> None:
    for bad in ("0", "-1.5"):
        with pytest.raises(ValueError):
            FxRate(base="GBP", quote="USD", rate=Decimal(bad), as_of="t", source="test")


# --------------------------------------------------------------------------
# FX providers
# --------------------------------------------------------------------------
def test_pinned_provider_serves_both_directions() -> None:
    provider = PinnedFxProvider({("GBP", "USD"): "1.25"})
    assert float(provider.rate("GBP", "USD").rate) == 1.25
    assert round(float(provider.rate("USD", "GBP").rate), 4) == 0.8
    assert float(provider.rate("GBP", "GBP").rate) == 1.0


def test_pinned_provider_refuses_an_unknown_pair() -> None:
    provider = PinnedFxProvider({("GBP", "USD"): "1.25"})
    with pytest.raises(FxRateUnavailableError):
        provider.rate("EUR", "USD")


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _Client:
    def __init__(self, payload=None, error=None):
        self._payload = payload
        self._error = error
        self.calls = []

    def get(self, url, params=None, timeout=None, headers=None):
        self.calls.append((url, params))
        if self._error is not None:
            raise self._error
        return _Resp(self._payload)


def test_public_fx_feed_parses_a_rate() -> None:
    clock = FrozenClock("2026-03-02T12:00:00+00:00")
    client = _Client({"base": "GBP", "date": "2026-03-02", "rates": {"USD": 1.2712}})
    feed = PublicFxFeed(http_client=client, clock=clock)
    rate = feed.rate("GBP", "USD")
    assert rate.base == "GBP" and rate.quote == "USD"
    assert float(rate.rate) == 1.2712


def test_public_fx_feed_fails_closed_on_timeout() -> None:
    feed = PublicFxFeed(http_client=_Client(error=TimeoutError("read timeout")))
    with pytest.raises(FxRateUnavailableError) as exc:
        feed.rate("GBP", "USD")
    assert exc.value.failure == "timeout"


def test_public_fx_feed_fails_closed_on_malformed_payload() -> None:
    for payload in ([], {"rates": {}}, {"rates": {"USD": "not-a-number"}}, {"rates": {"USD": -1}}):
        feed = PublicFxFeed(http_client=_Client(payload))
        with pytest.raises(FxRateUnavailableError):
            feed.rate("GBP", "USD")


def test_public_fx_feed_refuses_a_stale_rate() -> None:
    clock = FrozenClock("2026-03-10T12:00:00+00:00")
    client = _Client({"date": "2026-03-02", "rates": {"USD": 1.27}})
    feed = PublicFxFeed(http_client=client, clock=clock)
    with pytest.raises(FxRateUnavailableError) as exc:
        feed.rate("GBP", "USD")
    assert exc.value.failure == "stale"


def test_fx_feed_refuses_broker_urls() -> None:
    feed = PublicFxFeed(http_client=_Client({}), url="https://paper-api.alpaca.markets/x")
    with pytest.raises(RuntimeError, match="broker URL"):
        feed.rate("GBP", "USD")


# --------------------------------------------------------------------------
# Clock
# --------------------------------------------------------------------------
def test_frozen_clock_advances_on_sleep_instead_of_blocking() -> None:
    clock = FrozenClock("2026-01-01T00:00:00+00:00")
    before = clock.now()
    clock.sleep(90)
    assert (clock.now() - before).total_seconds() == 90


def test_system_clock_is_utc_aware() -> None:
    now = SystemClock().now()
    assert now.tzinfo is not None
    assert ensure_utc(now) == now


# --------------------------------------------------------------------------
# Instruments
# --------------------------------------------------------------------------
def test_public_crypto_is_usd_quoted() -> None:
    assert instrument_for("BTC-USD").quote_currency == "USD"
    assert instrument_for("ETH-USD").quote_currency == "USD"


def test_simulated_symbols_are_base_currency_so_no_fx_is_invented() -> None:
    assert instrument_for("SIM-UP").quote_currency == BASE_CURRENCY
    assert instrument_for("SIM-DOWN").quote_currency == BASE_CURRENCY


def test_floor_qty_never_rounds_up() -> None:
    spec = InstrumentSpec("X", "GBP", qty_step=0.0001, min_qty=0.0001)
    assert spec.floor_qty(0.00019999) == 0.0001
    assert spec.floor_qty(0.25) == 0.25
    assert spec.floor_qty(0.00009) == 0.0
    assert spec.floor_qty(-5) == 0.0
    for raw in (0.1, 0.123456, 1.99999, 12.5):
        assert spec.floor_qty(raw) <= raw + 1e-12
