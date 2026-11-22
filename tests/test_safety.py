from __future__ import annotations

import pytest
from pydantic import ValidationError

from ai_trader.config import Settings
from ai_trader.exceptions import LiveTradingBlockedError
from ai_trader.safety import (
    LIVE_TRADING_ALLOWED,
    assert_safe_to_run,
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


def test_paper_mode_is_accepted() -> None:
    assert assert_safe_to_run(mode="paper") == "paper"


def test_settings_refuse_live_mode(isolated_env: object, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRADING_MODE", "live")
    with pytest.raises((LiveTradingBlockedError, ValidationError)):
        Settings()
