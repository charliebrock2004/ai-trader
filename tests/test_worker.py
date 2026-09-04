"""Desk worker: Start actually runs, Stop blocks, recover resumes, HOLDs are recorded."""

from __future__ import annotations

from ai_trader.rpc import handle
from ai_trader.runtime import get_runtime
from ai_trader.safety import LIVE_TRADING_ALLOWED


def test_worker_start_stop_is_paper_only(isolated_env) -> None:
    started = handle(
        {
            "cmd": "start",
            "payload": {
                "symbol": "SIM-UP",
                "bars": 12,
                "warmup": 4,
                "grok_frequency": 4,
                "source": "simulated",
                "continuous": True,
            },
        }
    )
    assert LIVE_TRADING_ALLOWED is False
    assert started["live"] is False
    assert started["broker"] == "NOT USED"
    assert started["engine"] == "python-worker"
    assert started["running"] is True
    assert started["stopped"] is False
    assert started["status"] in {"STARTING", "RUNNING"}
    assert started["grok"] in {"STARTING", "RUNNING"}
    assert started["balance"] == 100 or started["balance"] == 100.0
    assert started["currency"] == "GBP"

    status = handle({"cmd": "status"})
    assert status["running"] is True
    assert status["worker_alive"] is True
    assert status["engine"] == "python-worker"

    agent = handle({"cmd": "agent"})
    assert agent["running"] is True
    assert agent["account"]["equity"] == 100 or agent["balance"] == 100

    stopped = handle({"cmd": "stop"})
    assert stopped["running"] is False
    assert stopped["stopped"] is True
    assert stopped["grok"] == "STOPPED"
    assert stopped["live"] is False
    assert stopped["hold_reason"]


def test_worker_records_hold_with_reason(isolated_env) -> None:
    runtime = get_runtime()
    runtime.worker.start(
        symbol="SIM-UP", source="simulated", timeframe="5m", bars=12, continuous=True
    )
    runtime._record_spot_decision(
        {
            "symbol": "SIM-UP",
            "final_action": "HOLD",
            "proposed_action": "HOLD",
            "reason": "No executable edge after costs.",
            "stage": "spot",
            "equity": 100,
            "cash": 100,
            "bar": 0,
            "approved": False,
        }
    )
    rows = runtime.repository.records.list_decisions(limit=10)
    holds = [row for row in rows if row["final_action"] == "HOLD"]
    assert holds, "A HOLD must be written, not inferred."
    assert "edge" in (holds[0]["notes"] or holds[0]["policy_reason"] or "").lower()
    runtime.worker.stop()


def test_running_spot_session_does_not_show_event_hold_as_the_reason(isolated_env) -> None:
    """CPI/event HOLDs are recorded. They must not masquerade as the BTC desk's reason."""
    runtime = get_runtime()
    started = runtime.worker.start(
        symbol="SIM-UP", source="simulated", timeframe="5m", bars=12, continuous=True
    )
    assert started["running"] is True
    runtime.repository.records.record_decision(
        {
            "kind": "binary",
            "ticker": "CPI-2026-07-ABOVE-4",
            "final_action": "HOLD",
            "notes": "Official data status is unavailable, not verified. Uncertainty means HOLD.",
            "stage": "opportunity",
            "executed": False,
        }
    )
    status = runtime.worker.status()
    reason = str(status.get("hold_reason") or "")
    assert "Official data" not in reason
    assert "SIM-UP" in reason or "paper session" in reason.lower()
    runtime.worker.stop()


def test_worker_shutdown_keeps_recover_latch(isolated_env) -> None:
    runtime = get_runtime()
    runtime.worker.start(
        symbol="SIM-UP", source="simulated", timeframe="5m", bars=12, continuous=True
    )
    assert runtime.worker.desired_running() is True
    runtime.worker.shutdown()
    assert runtime.worker.desired_running() is True
    runtime.worker.recover()
    status = runtime.worker.status()
    assert status["running"] is True
    runtime.worker.stop()
    assert runtime.worker.desired_running() is False
    status = runtime.worker.status()
    assert status["running"] is False


def test_worker_does_not_claim_running_when_stopped(isolated_env) -> None:
    status = handle({"cmd": "status"})
    assert status["running"] is False
    assert status["stopped"] is True
    assert status["grok"] == "STOPPED"
    assert status["status"] == "STOPPED"
    assert status["live"] is False
    assert LIVE_TRADING_ALLOWED is False
