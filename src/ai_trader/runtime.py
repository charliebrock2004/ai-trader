"""Process-wide runtime: settings, logging, database, kill switch, pipeline."""

from __future__ import annotations

from typing import Optional

from ai_trader.config import Settings, get_settings
from ai_trader.db.repository import Repository
from ai_trader.kill_switch import KillSwitch, get_kill_switch
from ai_trader.logging_setup import setup_logging
from ai_trader.pipeline.orchestrator import Orchestrator


class Runtime:
    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()
        setup_logging(self.settings.resolve_log_dir(), self.settings.log_level)
        self.repository = Repository(self.settings.resolve_database_path())
        self.kill_switch = get_kill_switch(
            self.settings.resolve_kill_switch_path(),
            initially_engaged=self.settings.kill_switch_engaged,
        )
        self.orchestrator = Orchestrator(
            self.settings, self.repository, self.kill_switch
        )
        if not self._bootstrapped():
            self.repository.record_event(
                level="INFO",
                source="runtime",
                event_type="boot",
                message="AI-Trader foundation started. Order placement is disabled.",
                details={
                    "mode": self.settings.trading_mode,
                    "kill_switch": self.kill_switch.is_engaged(),
                },
            )

    def _bootstrapped(self) -> bool:
        counts = self.repository.table_counts()
        return counts.get("events", 0) > 0

    def close(self) -> None:
        self.repository.close()


_RUNTIME: Optional[Runtime] = None


def get_runtime() -> Runtime:
    global _RUNTIME
    if _RUNTIME is None:
        _RUNTIME = Runtime()
    return _RUNTIME


def reset_runtime() -> None:
    global _RUNTIME
    if _RUNTIME is not None:
        _RUNTIME.close()
    _RUNTIME = None
