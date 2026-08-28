from __future__ import annotations

import pytest
from pydantic import ValidationError

from ai_trader.config import Settings
from ai_trader.exceptions import LiveTradingBlockedError
from ai_trader.safety import (
    ALPACA_LIVE_BASE_URL,
    ALPACA_PAPER_BASE_URL,
    LIVE_TRADING_ALLOWED,
    assert_safe_to_run,
    is_alpaca_live_url,
)


def test_live_trading_flag_is_hardcoded_off() -> None:
    assert LIVE_TRADING_ALLOWED is False


def test_live_mode_is_rejected() -> None:
    with pytest.raises(LiveTradingBlockedError):
        assert_safe_to_run(mode="live")


@pytest.mark.parametrize("mode", ["production", "prod", "real", "cash"])
def test_forbidden_mode_tokens(mode: str) -> None:
    with pytest.raises(LiveTradingBlockedError):
        assert_safe_to_run(mode=mode)


def test_live_alpaca_url_detected() -> None:
    assert is_alpaca_live_url(ALPACA_LIVE_BASE_URL) is True
    assert is_alpaca_live_url(ALPACA_PAPER_BASE_URL) is False


def test_paper_url_accepted_in_paper_mode() -> None:
    assert assert_safe_to_run(mode="paper", alpaca_base_url=ALPACA_PAPER_BASE_URL) == "paper"


def test_live_url_rejected_even_in_paper_mode() -> None:
    with pytest.raises(LiveTradingBlockedError):
        assert_safe_to_run(mode="paper", alpaca_base_url=ALPACA_LIVE_BASE_URL)


def test_settings_refuse_live_mode(isolated_env: object, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRADING_MODE", "live")
    with pytest.raises((LiveTradingBlockedError, ValidationError)):
        Settings()


def test_settings_refuse_live_url(isolated_env: object, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRADING_MODE", "paper")
    monkeypatch.setenv("ALPACA_BASE_URL", ALPACA_LIVE_BASE_URL)
    with pytest.raises((LiveTradingBlockedError, ValidationError)):
        Settings()
