"""Hard safety rails.

The risk engine sits between AI and execution. This module sits under
everything: process start, broker construction, and any future order path.

Live trading cannot be enabled from the environment, config files, or the
dashboard. The flag below is the single source of truth.
"""

from __future__ import annotations

from ai_trader.exceptions import LiveTradingBlockedError

# ---------------------------------------------------------------------------
# Invariant. Do not change this to True. There is no supported live mode.
# ---------------------------------------------------------------------------
LIVE_TRADING_ALLOWED = False

ALLOWED_MODES = frozenset({"simulate", "paper"})
FORBIDDEN_MODE_TOKENS = ("live", "prod", "production", "real", "cash")


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


def assert_safe_to_run(*, mode: str) -> str:
    """Validate process-wide safety. Returns the normalised mode."""
    assert_live_trading_disabled()
    return assert_mode_allowed(mode)


def safety_report(*, mode: str) -> dict:
    """Dashboard-facing snapshot. Never includes secrets."""
    issues: list[str] = []
    try:
        assert_safe_to_run(mode=mode)
        ok = True
    except LiveTradingBlockedError as exc:
        ok = False
        issues.append(str(exc))
    return {
        "ok": ok,
        "live_trading_allowed": LIVE_TRADING_ALLOWED,
        "allowed_modes": sorted(ALLOWED_MODES),
        "mode": mode,
        "issues": issues,
    }
