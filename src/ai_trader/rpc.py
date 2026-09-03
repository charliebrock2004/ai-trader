"""Stdio JSON-line worker.

No extra HTTP port. Never a broker. Never live trading.

Every command validates its own arguments here rather than trusting the caller,
because the Node layer is a second process and "the other side already checked"
is not a security property.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from ai_trader.runtime import get_runtime

#: Commands that change state. Named so the transport can require authorisation
#: for exactly these and no others.
MUTATING_COMMANDS = frozenset({"start", "stop", "cycle"})

READ_COMMANDS = frozenset(
    {"health", "status", "agent", "performance", "system", "decisions", "decision", "opportunities"}
)


def _payload(req: dict[str, Any]) -> dict[str, Any]:
    raw = req.get("payload")
    return raw if isinstance(raw, dict) else {}


def _int(body: dict[str, Any], key: str, default: int, *, low: int, high: int) -> int:
    """Coerce and clamp. A bad value is clamped, never propagated."""
    try:
        value = int(body.get(key, default))
    except (TypeError, ValueError):
        return default
    return max(low, min(high, value))


def _symbol(body: dict[str, Any]) -> str:
    allowed = {"BTC-USD", "ETH-USD", "SIM-UP", "SIM-DOWN", "SIM-FLAT", "SIM-CHOP", "SIM-SHOCK"}
    raw = str(body.get("symbol") or "BTC-USD").strip().upper()
    return raw if raw in allowed else "BTC-USD"


def _timeframe(body: dict[str, Any]) -> str:
    allowed = {"1m", "5m", "15m", "1h", "1d"}
    raw = str(body.get("timeframe") or "5m").strip().lower()
    return raw if raw in allowed else "5m"


def _source(body: dict[str, Any]) -> str:
    raw = str(body.get("source") or "public").strip().lower()
    return raw if raw in {"public", "simulated"} else "public"


def _failure(message: str) -> dict[str, Any]:
    """The shape the UI can always render, whatever went wrong."""
    return {
        "ok": False,
        "live": False,
        "broker": "NOT USED",
        "grok": "STOPPED",
        "running": False,
        "stopped": True,
        "balance": 100,
        "today_pnl": 0,
        "current_decision": "HOLD",
        "position": "flat",
        "open_pnl": 0,
        "trades": 0,
        "engine": "python-worker",
        "data_error": message,
    }


def handle(req: dict[str, Any]) -> dict[str, Any]:
    cmd = str(req.get("cmd") or "").strip().lower()
    body = _payload(req)

    if cmd == "health":
        return {"ok": True, "service": "ai-trader", "live": False, "engine": "python-worker"}

    runtime = get_runtime()

    # -- desk (spot paper session + event cycles) ------------------------
    if cmd == "agent":
        return runtime.worker.status()
    if cmd == "status":
        return runtime.worker.status()
    if cmd == "start":
        return runtime.worker.start(
            symbol=_symbol(body),
            bars=_int(body, "bars", 24, low=2, high=300),
            timeframe=_timeframe(body),
            grok_frequency=_int(body, "grok_frequency", 8, low=1, high=60),
            warmup=_int(body, "warmup", 8, low=0, high=120),
            source=_source(body),
            continuous=True,
        )
    if cmd == "stop":
        return runtime.worker.stop()
    if cmd == "cycle":
        return runtime.agent.run_cycle()
    if cmd == "performance":
        return runtime.agent.performance()
    if cmd == "system":
        payload = runtime.agent.system()
        desk = runtime.worker.status()
        payload["worker"] = {
            "engine": "python-worker",
            "running": desk.get("running"),
            "worker_alive": desk.get("worker_alive"),
            "status": desk.get("status"),
        }
        return payload
    if cmd == "decisions":
        return {
            "decisions": runtime.repository.records.list_decisions(
                limit=_int(body, "limit", 50, low=1, high=200),
                only_executed=bool(body.get("only_executed")),
            )
        }
    if cmd == "decision":
        try:
            decision_id = int(body.get("id"))
        except (TypeError, ValueError):
            return {"ok": False, "error": "A numeric decision id is required."}
        row = runtime.repository.records.decision(decision_id)
        if row is None:
            return {"ok": False, "error": f"No decision {decision_id}."}
        return {"ok": True, "decision": row}
    if cmd == "opportunities":
        return {
            "opportunities": runtime.repository.records.list_opportunities(
                limit=_int(body, "limit", 50, low=1, high=200)
            )
        }

    return _failure(f"Unknown paper-engine command '{cmd}'.")


def serve() -> int:
    import logging

    logging.getLogger().handlers.clear()
    logging.basicConfig(stream=sys.stderr, level=logging.INFO)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as exc:
            print(json.dumps({"ok": False, "error": f"Invalid RPC JSON: {exc}"}), flush=True)
            continue
        ident = req.get("id") if isinstance(req, dict) else None
        try:
            result = handle(req if isinstance(req, dict) else {})
            print(json.dumps({"id": ident, "ok": True, "result": result}, default=str), flush=True)
        except Exception as exc:  # noqa: BLE001 — fail closed to the client
            print(
                json.dumps(
                    {"id": ident, "ok": False, "error": str(exc), "result": _failure(str(exc))},
                    default=str,
                ),
                flush=True,
            )
    return 0
