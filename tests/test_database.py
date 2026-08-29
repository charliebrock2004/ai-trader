from __future__ import annotations

from pathlib import Path

from ai_trader.db.repository import Repository
from ai_trader.db.schema import REQUIRED_TABLES
from ai_trader.market_data.generator import generate_series


def test_schema_creates_required_tables(tmp_path: Path) -> None:
    repo = Repository(tmp_path / "test.db")
    health = repo.health()
    for table in REQUIRED_TABLES:
        assert table in health["tables"]
        assert health["counts"][table] == 0
    repo.close()


def test_event_roundtrip(tmp_path: Path) -> None:
    repo = Repository(tmp_path / "test.db")
    event_id = repo.record_event(
        level="INFO",
        source="test",
        event_type="unit",
        message="hello",
        details={"k": 1},
    )
    assert event_id >= 1
    events = repo.list_events()
    assert events[0]["message"] == "hello"
    repo.close()


def test_market_series_roundtrip(tmp_path: Path) -> None:
    repo = Repository(tmp_path / "test.db")
    series = generate_series("SIM-UP", limit=8, seed=42)
    series_id = repo.save_series(series)
    assert series_id >= 1
    stored = repo.latest_series("SIM-UP")
    assert stored[0]["scenario"] == "uptrend"
    assert stored[0]["candles"][-1]["close"] == series.candles[-1].close
    repo.close()
