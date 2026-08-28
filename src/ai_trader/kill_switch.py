"""File-backed kill switch.

When engaged, the orchestrator must refuse to run. Default is ENGAGED.
Disengaging the switch does not enable order placement — that stays blocked
in the broker and risk layers.
"""

from __future__ import annotations

import json
from pathlib import Path

from ai_trader.exceptions import KillSwitchEngagedError
from ai_trader.types import utc_now_iso


class KillSwitch:
    def __init__(self, path: Path, *, initially_engaged: bool = True) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if initially_engaged and not self.path.exists():
            self.engage("Initialised in the engaged (safe) state.")
        elif not initially_engaged and not self.path.exists():
            # Explicit disengage requested at boot still writes a record so
            # the dashboard has a known state. We do not create the halt file.
            self._last_reason = "Started disengaged at boot."
            self._last_at = utc_now_iso()

    def is_engaged(self) -> bool:
        return self.path.exists()

    def engage(self, reason: str = "Manual halt") -> dict:
        payload = {
            "engaged": True,
            "reason": reason,
            "at": utc_now_iso(),
        }
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return payload

    def disengage(self, reason: str = "Manual resume") -> dict:
        if self.path.exists():
            self.path.unlink()
        return {
            "engaged": False,
            "reason": reason,
            "at": utc_now_iso(),
        }

    def snapshot(self) -> dict:
        if not self.path.exists():
            return {
                "engaged": False,
                "reason": "Kill switch is not engaged.",
                "at": None,
                "path": str(self.path),
            }
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {"reason": "Kill switch file present (unreadable payload)."}
        return {
            "engaged": True,
            "reason": data.get("reason", "Engaged"),
            "at": data.get("at"),
            "path": str(self.path),
        }

    def assert_clear(self) -> None:
        if self.is_engaged():
            snap = self.snapshot()
            raise KillSwitchEngagedError(
                snap.get("reason") or "Kill switch is engaged."
            )


def get_kill_switch(
    path: Path, *, initially_engaged: bool = True, cache: dict | None = None
) -> KillSwitch:
    """Simple process cache so dashboard and pipeline share one instance."""
    store = cache if cache is not None else _SWITCHES
    key = str(path)
    if key not in store:
        store[key] = KillSwitch(path, initially_engaged=initially_engaged)
    return store[key]


_SWITCHES: dict[str, KillSwitch] = {}


def reset_kill_switch_cache() -> None:
    _SWITCHES.clear()
