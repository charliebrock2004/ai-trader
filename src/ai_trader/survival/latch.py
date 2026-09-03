"""The terminal latch: the one thing in this system that is truly one-way.

When equity reaches the terminal threshold the agent is dead. Not paused, not
halted — dead. This module is what makes that stick:

* It is written to **two** places: a file on disk and the ``agent_life`` row.
  Either one reading TERMINAL is enough to refuse startup, so losing one does
  not resurrect the agent.
* There is no ``clear()``, no ``reset()``, no ``resume()``. Not a private one
  either. Reviving an agent means a human deleting the file and editing the
  database by hand, which is deliberate.
* Nothing reachable from an LLM response can call ``engage`` with a false
  reason or unset it; the only caller is the survival engine, driven by an
  equity number the LLM does not produce.

This is distinct from the kill switch. The kill switch is an operator pause
and is reversible. The latch is not.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from ai_trader.clock import Clock, default_clock

LATCH_FILENAME = "TERMINAL"


class AgentTerminatedError(RuntimeError):
    """Raised when a terminated agent is asked to do anything."""

    def __init__(self, detail: dict[str, Any]) -> None:
        reason = detail.get("reason") or "Agent is terminated."
        super().__init__(f"Agent is TERMINATED and cannot trade or restart. {reason}")
        self.detail = detail


class TerminalLatch:
    """One-way termination flag, persisted to disk and to the database."""

    def __init__(
        self,
        path: Path,
        *,
        store: Any = None,
        clock: Optional[Clock] = None,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.store = store
        self.clock = clock or default_clock()

    # -- reads ------------------------------------------------------------
    def _file_state(self) -> Optional[dict[str, Any]]:
        if not self.path.exists():
            return None
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # A latch file that exists but cannot be parsed still means dead.
            # Failing open here would be the worst possible bug.
            return {"terminated": True, "reason": "Terminal latch file is unreadable."}

    def _db_state(self) -> Optional[dict[str, Any]]:
        if self.store is None:
            return None
        life = self.store.agent_life()
        if not life:
            return None
        if life.get("terminated_at") or life.get("survival_state") == "TERMINAL":
            return {
                "terminated": True,
                "reason": life.get("terminal_reason") or "Recorded TERMINAL in agent_life.",
                "at": life.get("terminated_at"),
                "source": "database",
            }
        return None

    def is_terminated(self) -> bool:
        return self._file_state() is not None or self._db_state() is not None

    def snapshot(self) -> dict[str, Any]:
        file_state = self._file_state()
        db_state = self._db_state()
        if file_state is None and db_state is None:
            return {"terminated": False, "path": str(self.path)}
        detail = file_state or db_state or {}
        return {
            "terminated": True,
            "reason": detail.get("reason", "Terminated."),
            "at": detail.get("at"),
            "equity": detail.get("equity"),
            "threshold": detail.get("threshold"),
            "in_file": file_state is not None,
            "in_database": db_state is not None,
            "path": str(self.path),
        }

    # -- the single write -------------------------------------------------
    def engage(self, *, reason: str, equity: float, threshold: float) -> dict[str, Any]:
        """Terminate. Idempotent, and there is deliberately no inverse."""
        existing = self.snapshot()
        if existing.get("terminated"):
            return existing
        payload = {
            "terminated": True,
            "reason": reason,
            "at": self.clock.now_iso(),
            "equity": round(float(equity), 2),
            "threshold": round(float(threshold), 2),
            "note": (
                "One-way. The agent cannot clear this. Reviving it requires a human "
                "to delete this file and clear agent_life.terminated_at by hand."
            ),
        }
        # Database first: if the process dies between the two writes, the
        # durable record is already TERMINAL rather than already alive.
        if self.store is not None:
            self.store.update_agent_life(
                survival_state="TERMINAL",
                terminated_at=payload["at"],
                terminal_reason=reason,
            )
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(self.path)
        return payload

    def assert_alive(self) -> None:
        snap = self.snapshot()
        if snap.get("terminated"):
            raise AgentTerminatedError(snap)
