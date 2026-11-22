from __future__ import annotations

from pathlib import Path

from ai_trader.safety import LIVE_TRADING_ALLOWED


ROOT = Path(__file__).resolve().parents[1] / "src" / "ai_trader"


def test_live_flag_false() -> None:
    assert LIVE_TRADING_ALLOWED is False
    text = (ROOT / "safety.py").read_text()
    assert "LIVE_TRADING_ALLOWED = False" in text
    assert "LIVE_TRADING_ALLOWED = True" not in text


def test_no_live_host_assignment() -> None:
    for path in ROOT.rglob("*.py"):
        body = path.read_text()
        assert "alpaca.markets" not in body
        assert "LIVE_TRADING_ALLOWED = True" not in body


def test_grok_remains_a_stub_and_no_broker_adapter_exists() -> None:
    grok = (ROOT / "ai" / "grok_client.py").read_text()
    assert "LIVE_TRADING_ALLOWED = True" not in grok
    assert "allow_orders = True" not in grok
    assert "Broker.submit" not in grok
    assert "api.alpaca.markets" not in grok
    assert "Refusing to call a broker URL from GrokAnalyst." in grok
    engine = (ROOT / "risk" / "engine.py").read_text()
    assert "allow_orders: bool = False" in engine
    assert LIVE_TRADING_ALLOWED is False
    # The only broker modules are the interface and the internal simulator.
    brokers = sorted(p.name for p in (ROOT / "broker").glob("*.py"))
    assert brokers == ["__init__.py", "base.py", "simulated.py"]


def test_benchmark_stays_paper() -> None:
    folder = ROOT / "benchmark"
    assert folder.is_dir()
    for path in folder.rglob("*.py"):
        body = path.read_text()
        assert "LIVE_TRADING_ALLOWED = True" not in body
        assert "Broker.submit" not in body
        assert "api.alpaca.markets" not in body
        assert "allow_orders = True" not in body


def test_session_stays_paper() -> None:
    folder = ROOT / "session"
    assert folder.is_dir()
    for path in folder.rglob("*.py"):
        body = path.read_text()
        assert "LIVE_TRADING_ALLOWED = True" not in body
        assert "Broker.submit" not in body
        assert "api.alpaca.markets" not in body
        assert "allow_orders = True" not in body


def test_public_feed_stays_paper() -> None:
    path = ROOT / "market_data" / "public.py"
    body = path.read_text()
    assert "LIVE_TRADING_ALLOWED = True" not in body
    assert "Broker.submit" not in body
    assert "api.alpaca.markets" not in body
    assert "allow_orders = True" not in body
    assert "Refusing to call a broker URL from market data." in body
