"""Check the deployed worker and leaderboard, and say plainly what is wrong.

Read-only GET requests against public endpoints. Exit 0 when healthy, 1 with
one problem per line otherwise. A failing safety marker (anything suggesting
live trading) is reported first.
"""

import json
import sys
import time
import urllib.request
from typing import Optional

WORKER = "https://ai-trader-elxv.onrender.com"
SITE = "https://ai-trader-snowy.vercel.app"
#: A daily candle closes every 24h; allow for exchange delay and a sleeping worker.
MAX_CANDLE_AGE_S = 2 * 86400 + 3600
ATTEMPTS = 3


def assess(health: Optional[dict], board: Optional[dict], *, now: float) -> list[str]:
    """Problems with one worker health reply and one leaderboard reply; empty when healthy."""
    problems = []
    for name, reply in (("worker /health", health), ("site /api/tournament", board)):
        if reply is None:
            problems.append(f"{name}: no valid reply")
            continue
        if reply.get("live") is not False:
            problems.insert(0, f"SAFETY: {name} does not report live=false")
    if health is not None:
        if health.get("live_trading_allowed") is not False:
            problems.insert(0, "SAFETY: worker /health does not report live_trading_allowed=false")
        if health.get("ok") is not True:
            problems.append("worker /health: ok is not true")
    if board is not None:
        if board.get("ok") is not True:
            problems.append(f"leaderboard: {board.get('error') or 'ok is not true'}")
        else:
            if board.get("simulated") is not True:
                problems.insert(0, "SAFETY: leaderboard is not marked simulated")
            if not board.get("rows"):
                problems.append("leaderboard: no rows")
            data = board.get("data") or {}
            bars = {d.get("last_bar") for d in data.values()}
            if len(bars) > 1:
                problems.append(f"leaderboard: assets disagree on the last candle: {sorted(data)}")
            for sym, d in sorted(data.items()):
                age = now - (d.get("last_bar") or 0)
                if age > MAX_CANDLE_AGE_S:
                    problems.append(f"leaderboard: {sym} last candle is {age / 3600:.0f}h old")
    return problems


def _get(url: str) -> Optional[dict]:
    for attempt in range(ATTEMPTS):
        try:
            with urllib.request.urlopen(url, timeout=90) as r:
                reply = json.load(r)
            if isinstance(reply, dict):
                return reply
        except Exception as e:  # a sleeping Render worker answers after a retry
            print(f"{url}: attempt {attempt + 1} failed: {type(e).__name__} {getattr(e, 'code', '')}", file=sys.stderr)
        time.sleep(20 * (attempt + 1))
    return None


def main() -> int:
    problems = assess(_get(f"{WORKER}/health"), _get(f"{SITE}/api/tournament"), now=time.time())
    for p in problems:
        print(p)
    if not problems:
        print("healthy", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
