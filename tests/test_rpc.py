from __future__ import annotations

from ai_trader.rpc import handle
from ai_trader.safety import LIVE_TRADING_ALLOWED


def test_rpc_health_is_paper_only(isolated_env) -> None:
    result = handle({"cmd": "health"})
    assert result["ok"] is True
    assert result["live"] is False
    assert LIVE_TRADING_ALLOWED is False


def test_rpc_start_stop_never_uses_broker(isolated_env) -> None:
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
    assert started["live"] is False
    assert started["broker"] == "NOT USED"
    assert started["grok"] == "RUNNING"
    assert started["balance"] == 100 or started["balance"] == 100.0
    stopped = handle({"cmd": "stop"})
    assert stopped["grok"] == "STOPPED"
    assert stopped["live"] is False
    assert LIVE_TRADING_ALLOWED is False


def test_rpc_unknown_command_stays_paper(isolated_env) -> None:
    result = handle({"cmd": "live-trade"})
    assert result["ok"] is False
    assert result["live"] is False
    assert result["broker"] == "NOT USED"
    assert result["grok"] == "STOPPED"
    assert LIVE_TRADING_ALLOWED is False
