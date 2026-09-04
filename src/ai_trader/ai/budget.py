"""Hard cap on Grok paper-analysis calls.

The analyst is optional and expensive. The deterministic layer runs on every
bar for free. This budget is the only thing that decides whether a surviving
candidate is allowed to become an HTTP call.

It never feeds back into sizing, risk, or the survival multiplier. Exhausting
it is not a reason to trade, and it is not a reason to skip a HOLD.
"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Any, Optional

from ai_trader.clock import Clock, default_clock, ensure_utc

DEFAULT_DAILY_LIMIT = 8
DEFAULT_MIN_INTERVAL_SECONDS = 1800  # 30 minutes


class GrokBudget:
    """Process + database view of today's Grok call allowance."""

    def __init__(
        self,
        store: Any,
        *,
        clock: Optional[Clock] = None,
        daily_limit: int = DEFAULT_DAILY_LIMIT,
        min_interval_seconds: int = DEFAULT_MIN_INTERVAL_SECONDS,
        model: str = "grok-4.3",
    ) -> None:
        self.store = store
        self.clock = clock or default_clock()
        self.daily_limit = max(0, int(daily_limit))
        self.min_interval_seconds = max(0, int(min_interval_seconds))
        self.model = model
        self._lock = threading.Lock()
        self._reserved = 0
        self._last_local: Optional[datetime] = None
        self.filter_holds = 0
        self.budget_skips = 0
        self.interval_skips = 0

    def day_start(self) -> datetime:
        now = ensure_utc(self.clock.now())
        return now.replace(hour=0, minute=0, second=0, microsecond=0)

    def _stats(self) -> tuple[int, float, Optional[str]]:
        since = self.day_start().isoformat()
        getter = getattr(self.store, "llm_stats_since", None)
        if callable(getter):
            row = getter(since) or {}
            return int(row.get("n") or 0), float(row.get("total") or 0.0), row.get("last_at")
        last_getter = getattr(self.store, "last_llm_call_at", None)
        last_at = last_getter() if callable(last_getter) else None
        return 0, 0.0, last_at

    def calls_today(self) -> int:
        n, _, _ = self._stats()
        return n + self._reserved

    def allow(self) -> tuple[bool, str]:
        with self._lock:
            return self._allow_unlocked()

    def _allow_unlocked(self) -> tuple[bool, str]:
        if self.daily_limit <= 0:
            return False, "Grok daily call budget is zero. Deterministic path only."
        n, _, last_at = self._stats()
        used = n + self._reserved
        if used >= self.daily_limit:
            return False, (
                f"Grok daily call budget exhausted ({used}/{self.daily_limit}). "
                "Paper trading continues on the deterministic filter only."
            )
        last = self._last_local
        if last_at:
            try:
                db_last = ensure_utc(datetime.fromisoformat(str(last_at)))
            except (TypeError, ValueError):
                db_last = None
            if db_last is not None and (last is None or db_last > last):
                last = db_last
        if last is not None and self.min_interval_seconds > 0:
            elapsed = (ensure_utc(self.clock.now()) - last).total_seconds()
            if elapsed < self.min_interval_seconds:
                wait = int(self.min_interval_seconds - elapsed)
                return False, (
                    f"Grok min interval not elapsed ({wait}s remaining). "
                    "Deterministic path only."
                )
        return True, "ok"

    def consume(self) -> tuple[bool, str]:
        """Reserve one slot before the HTTP call. Failed calls still count."""
        with self._lock:
            ok, reason = self._allow_unlocked()
            if not ok:
                self.budget_skips += 1
                return False, reason
            self._reserved += 1
            self._last_local = ensure_utc(self.clock.now())
            return True, "ok"

    def snapshot(self) -> dict[str, Any]:
        n, total, last_at = self._stats()
        used = n + self._reserved
        allowed, reason = self.allow()
        remaining = max(0, self.daily_limit - used)
        return {
            "model": self.model,
            "calls_today": used,
            "persisted_calls_today": n,
            "daily_budget": self.daily_limit,
            "remaining": remaining,
            "estimated_cost": round(total, 6),
            "min_interval_seconds": self.min_interval_seconds,
            "last_call_at": last_at,
            "allowed": allowed,
            "reason": None if allowed else reason,
            "filter_holds": self.filter_holds,
            "budget_skips": self.budget_skips,
            "interval_skips": self.interval_skips,
        }
