from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ai_trader.config import clear_settings_cache
from ai_trader.kill_switch import reset_kill_switch_cache
from ai_trader.runtime import reset_runtime


@pytest.fixture
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("TRADING_MODE", "simulate")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "ai_trader.db"))
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("KILL_SWITCH_ENGAGED", "true")
    monkeypatch.setenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
    monkeypatch.setenv("ALPACA_API_KEY", "")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "")
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    clear_settings_cache()
    reset_kill_switch_cache()
    reset_runtime()
    yield tmp_path
    reset_runtime()
    reset_kill_switch_cache()
    clear_settings_cache()


@pytest.fixture
def client(isolated_env: Path) -> TestClient:
    from ai_trader.dashboard.app import create_app

    with TestClient(create_app()) as test_client:
        yield test_client
