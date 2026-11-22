import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "tournament_snapshot", Path(__file__).resolve().parents[1] / "scripts" / "tournament_snapshot.py"
)
ts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ts)


def _day(folder: Path, day: str, ranks: dict) -> None:
    folder.mkdir(exist_ok=True)
    rows = [{"id": i, "rank": r} for i, r in ranks.items()]
    (folder / f"{day}.json").write_text(json.dumps({"rows": rows}), encoding="utf-8")


def test_history_lists_each_rank_by_day_oldest_first(tmp_path):
    daily = tmp_path / "daily"
    _day(daily, "2026-11-22", {"a": 2, "b": 1})
    _day(daily, "2026-11-21", {"a": 1, "b": 2})
    _day(daily, "2026-11-23", {"a": 1, "c": 2})
    (daily / "notes.json").write_text("{}", encoding="utf-8")
    h = ts.history(daily)
    assert h["days"] == ["2026-11-21", "2026-11-22", "2026-11-23"]
    assert h["ranks"] == {"a": [1, 2, 1], "b": [2, 1, None], "c": [None, None, 2]}
    assert h["live"] is False and h["simulated"] is True


def test_write_history_leaves_daily_files_alone(tmp_path):
    daily = tmp_path / "daily"
    _day(daily, "2026-11-21", {"a": 1})
    before = (daily / "2026-11-21.json").read_bytes()
    ts.write_history(daily)
    assert (daily / "2026-11-21.json").read_bytes() == before
    assert json.loads((tmp_path / "history.json").read_text())["days"] == ["2026-11-21"]
    assert sorted(p.name for p in daily.iterdir()) == ["2026-11-21.json"]
