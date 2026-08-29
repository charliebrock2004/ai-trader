"""Stdio JSON-line paper-session worker.

No extra HTTP port. Never a broker. Never live trading.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from ai_trader.runtime import get_runtime


def _payload(req: dict[str, Any]) -> dict[str, Any]:
    raw = req.get("payload")
    return raw if isinstance(raw, dict) else {}


def handle(req: dict[str, Any]) -> dict[str, Any]:
    cmd = str(req.get("cmd") or "").strip().lower()
    runtime = get_runtime()
    if cmd == "health":
        return {"ok": True, "service": "ai-trader", "live": False}
    if cmd == "status":
        return runtime.orchestrator.paper_session.status()
    if cmd == "stop":
        return runtime.orchestrator.stop_paper_session()
    if cmd == "start":
        body = _payload(req)
        return runtime.orchestrator.start_paper_session(
            symbol=str(body.get("symbol") or "BTC-USD"),
            bars=int(body.get("bars") or 24),
            timeframe=str(body.get("timeframe") or "5m"),
            grok_frequency=int(body.get("grok_frequency") or 8),
            warmup=int(body.get("warmup") or 8),
            source=str(body.get("source") or "public"),
            continuous=bool(body.get("continuous", True)),
        )
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
        "data_error": f"Unknown paper-engine command '{cmd}'.",
    }


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
                    {
                        "id": ident,
                        "ok": False,
                        "error": str(exc),
                        "result": {
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
                            "data_error": str(exc),
                        },
                    },
                    default=str,
                ),
                flush=True,
            )
    return 0
