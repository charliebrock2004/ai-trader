from __future__ import annotations

from pathlib import Path

from ai_trader.db.repository import Repository
from ai_trader.db.schema import REQUIRED_TABLES


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
