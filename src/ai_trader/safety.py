"""Hard safety rails.

The risk engine sits between AI and execution. This module sits under
everything: process start, broker construction, and any future order path.

Live trading cannot be enabled from the environment, config files, or the
dashboard. The flag below is the single source of truth.
"""

from __future__ import annotations

from typing import Optional
from urllib.parse import urlparse

from ai_trader.exceptions import LiveTradingBlockedError

# ---------------------------------------------------------------------------
# Invariant. Do not change this to True. There is no supported live mode.
# ---------------------------------------------------------------------------
LIVE_TRADING_ALLOWED = False

ALLOWED_MODES = frozenset({"simulate", "paper"})
FORBIDDEN_MODE_TOKENS = ("live", "prod", "production", "real", "cash")

ALPACA_PAPER_BASE_URL = "https://paper-api.alpaca.markets"
ALPACA_LIVE_BASE_URL = "https://api.alpaca.markets"

_LIVE_HOST_FRAGMENTS = (
    "api.alpaca.markets",
)


def _normalise_url(url: str) -> str:
    return url.strip().rstrip("/").lower()


def is_alpaca_live_url(url: str) -> bool:
    if not url:
        return False
    parsed = urlparse(_normalise_url(url))
    host = (parsed.hostname or "").lower()
    if host.startswith("paper-"):
        return False
    if host == "paper-api.alpaca.markets":
        return False
    return host in _LIVE_HOST_FRAGMENTS or host.endswith(".alpaca.markets") and "paper" not in host


def assert_live_trading_disabled() -> None:
    if LIVE_TRADING_ALLOWED:
        raise LiveTradingBlockedError(
            "Invariant broken: LIVE_TRADING_ALLOWED must remain False."
        )


def assert_mode_allowed(mode: str) -> str:
    cleaned = (mode or "").strip().lower()
    if any(token in cleaned for token in FORBIDDEN_MODE_TOKENS):
        raise LiveTradingBlockedError(
            f"Trading mode '{mode}' is forbidden. Allowed: simulate, paper."
        )
    if cleaned not in ALLOWED_MODES:
        raise LiveTradingBlockedError(
            f"Trading mode '{mode}' is not allowed. Allowed: simulate, paper."
        )
    return cleaned


def assert_broker_url_safe(url: Optional[str], *, mode: str) -> Optional[str]:
    if not url:
        if mode == "paper":
            return ALPACA_PAPER_BASE_URL
        return url
    cleaned = url.strip()
    if is_alpaca_live_url(cleaned):
        raise LiveTradingBlockedError(
            "Alpaca live API URL is blocked. Use https://paper-api.alpaca.markets only."
        )
    if mode == "paper":
        normalised = _normalise_url(cleaned)
        if normalised != _normalise_url(ALPACA_PAPER_BASE_URL):
            raise LiveTradingBlockedError(
                "Paper mode only accepts the Alpaca paper endpoint "
                f"({ALPACA_PAPER_BASE_URL})."
            )
    return cleaned


def assert_safe_to_run(*, mode: str, alpaca_base_url: Optional[str] = None) -> str:
    """Validate process-wide safety. Returns the normalised mode."""
    assert_live_trading_disabled()
    normalised = assert_mode_allowed(mode)
    assert_broker_url_safe(alpaca_base_url, mode=normalised)
    return normalised


def safety_report(*, mode: str, alpaca_base_url: Optional[str] = None) -> dict:
    """Dashboard-facing snapshot. Never includes secrets."""
    issues: list[str] = []
    try:
        assert_safe_to_run(mode=mode, alpaca_base_url=alpaca_base_url)
        ok = True
    except LiveTradingBlockedError as exc:
        ok = False
        issues.append(str(exc))
    return {
        "ok": ok,
        "live_trading_allowed": LIVE_TRADING_ALLOWED,
        "allowed_modes": sorted(ALLOWED_MODES),
        "mode": mode,
        "alpaca_paper_url": ALPACA_PAPER_BASE_URL,
        "alpaca_live_url_blocked": True,
        "issues": issues,
    }
