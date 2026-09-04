"""Free-host persistence for the paper ledger.

Render's free plan has no disk and sleeps when idle. The SQLite file and the
TERMINAL latch would vanish on every wake, which would silently reset the £100
book and resurrect a dead agent.

This module checkpoints the database onto the public ``worker-endpoint`` GitHub
branch (read with no token, write with ``GITHUB_TOKEN`` if present). A GitHub
Actions workflow also pulls ``GET /api/snapshot`` on a schedule so a worker
without write credentials still has a durable copy.

Local tests never hit the network: restore only runs when ``RENDER`` is set
(or ``PERSIST_RESTORE=true``). A missing checkpoint is reported, never faked.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

DEFAULT_REPO = "charliebrock2004/ai-trader"
SNAPSHOT_BRANCH = "worker-endpoint"
SNAPSHOT_PATH = "snapshot.json"

_state: dict[str, Any] = {
    "kind": "ephemeral",
    "durable": False,
    "restored": False,
    "updated_at": None,
    "warning": "No checkpoint has been loaded. A host restart would start a fresh £100 book.",
}
_lock = threading.Lock()


def persistence_status() -> dict[str, Any]:
    with _lock:
        return dict(_state)


def _repo() -> str:
    return (os.environ.get("GITHUB_REPOSITORY") or DEFAULT_REPO).strip()


def _token() -> str:
    return (os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or "").strip()


def _raw_url() -> str:
    return (
        f"https://raw.githubusercontent.com/{_repo()}/{SNAPSHOT_BRANCH}/{SNAPSHOT_PATH}"
    )


def _api_url() -> str:
    return (
        f"https://api.github.com/repos/{_repo()}/contents/{SNAPSHOT_PATH}"
        f"?ref={SNAPSHOT_BRANCH}"
    )


def should_restore() -> bool:
    flag = (os.environ.get("PERSIST_RESTORE") or "").strip().lower()
    if flag in {"0", "false", "no", "never"}:
        return False
    if flag in {"1", "true", "yes", "always"}:
        return True
    return bool(os.environ.get("RENDER") or os.environ.get("PERSIST_GITHUB"))


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _set_state(**fields: Any) -> None:
    with _lock:
        _state.update(fields)


def _db_is_empty(path: Path) -> bool:
    if not path.exists() or path.stat().st_size < 64:
        return True
    try:
        conn = sqlite3.connect(str(path))
        try:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            if "agent_life" not in tables:
                return True
            row = conn.execute("SELECT COUNT(*) FROM agent_life").fetchone()
            return not row or int(row[0]) == 0
        finally:
            conn.close()
    except sqlite3.Error:
        return True


#: Candle caches are rebuildable from public APIs. Dumping them would blow
#: past GitHub's file limits and is not the ledger.
_SKIP_DUMP_TABLES = frozenset({"market_series", "market_candles", "sqlite_stat1", "sqlite_stat4"})


def dump_sqlite(path: Path) -> str:
    if not path.exists():
        return ""
    conn = sqlite3.connect(str(path))
    try:
        lines: list[str] = []
        for line in conn.iterdump():
            stripped = line.lstrip()
            if stripped.upper().startswith("INSERT"):
                skipped = False
                for table in _SKIP_DUMP_TABLES:
                    if f'INSERT INTO "{table}"' in line or f"INSERT INTO {table}" in line:
                        skipped = True
                        break
                if skipped:
                    continue
            lines.append(line)
        return "\n".join(lines)
    finally:
        conn.close()


def _apply_sql(path: Path, sql: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(sql)
        conn.commit()
    finally:
        conn.close()


def _latch_payload(data_dir: Path) -> Optional[dict[str, Any]]:
    latch = data_dir / "TERMINAL"
    if not latch.exists():
        return None
    try:
        return json.loads(latch.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"terminated": True, "reason": "Terminal latch file is unreadable."}


def build_snapshot(db_path: Optional[Path] = None) -> dict[str, Any]:
    """Serialise the current ledger. Safe to serve over GET — paper only."""
    from ai_trader.config import get_settings

    settings = get_settings()
    path = Path(db_path) if db_path is not None else settings.resolve_database_path()
    data_dir = path.parent
    latch = _latch_payload(data_dir)
    sql = dump_sqlite(path)
    meta: dict[str, Any] = {
        "engine": "python-worker",
        "live": False,
        "live_trading_allowed": False,
        "updated_at": _now(),
        "sql": sql,
        "latch": latch,
        "sha": (os.environ.get("RENDER_GIT_COMMIT") or os.environ.get("GITHUB_SHA") or "")
        or None,
    }
    try:
        conn = sqlite3.connect(str(path))
        try:
            row = conn.execute(
                "SELECT desired_running, paper_equity, survival_state, terminated_at, "
                "last_processed_candle_ts "
                "FROM agent_life LIMIT 1"
            ).fetchone()
        except sqlite3.Error:
            try:
                row = conn.execute(
                    "SELECT desired_running, paper_equity, survival_state, terminated_at "
                    "FROM agent_life LIMIT 1"
                ).fetchone()
            except sqlite3.Error:
                row = None
        finally:
            conn.close()
        if row:
            meta["desired_running"] = bool(row[0])
            meta["paper_equity"] = row[1]
            meta["survival_state"] = row[2]
            meta["terminated"] = bool(row[3]) or bool(latch and latch.get("terminated"))
            meta["last_processed_candle_ts"] = row[4] if len(row) > 4 else None
        else:
            meta["desired_running"] = False
            meta["paper_equity"] = 100.0
            meta["survival_state"] = "HEALTHY"
            meta["terminated"] = bool(latch and latch.get("terminated"))
            meta["last_processed_candle_ts"] = None
    except Exception:  # noqa: BLE001
        meta["desired_running"] = False
        meta["paper_equity"] = 100.0
        meta["terminated"] = bool(latch and latch.get("terminated"))
    return meta


def fetch_checkpoint() -> Optional[dict[str, Any]]:
    try:
        import httpx

        response = httpx.get(
            _raw_url(),
            headers={"user-agent": "ai-trader-persist", "accept": "application/json"},
            timeout=8.0,
            follow_redirects=True,
        )
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            log.warning("checkpoint fetch returned %s", response.status_code)
            return None
        body = response.json()
        if not isinstance(body, dict) or not isinstance(body.get("sql"), str):
            return None
        if body.get("live") is True:
            return None
        return body
    except Exception:  # noqa: BLE001 — a failed restore must not crash boot
        log.exception("checkpoint fetch failed")
        return None


def restore_if_needed(db_path: Path) -> dict[str, Any]:
    """Overlay GitHub state onto an empty local database. Never clobbers a live db."""
    if not should_restore():
        _set_state(
            kind="local",
            durable=False,
            restored=False,
            warning="Local process. Checkpoint restore is disabled.",
        )
        return persistence_status()
    if not _db_is_empty(db_path):
        _set_state(
            kind="github-checkpoint",
            durable=True,
            restored=False,
            warning=None,
        )
        return persistence_status()
    snapshot = fetch_checkpoint()
    if not snapshot or not snapshot.get("sql"):
        _set_state(
            kind="ephemeral",
            durable=False,
            restored=False,
            warning=(
                "No GitHub checkpoint yet. This free host has no disk — a restart "
                "before the first checkpoint would start a fresh £100 book."
            ),
        )
        return persistence_status()
    try:
        _apply_sql(db_path, str(snapshot["sql"]))
        latch = snapshot.get("latch")
        if isinstance(latch, dict) and latch.get("terminated"):
            (db_path.parent / "TERMINAL").write_text(
                json.dumps(latch, indent=2) + "\n", encoding="utf-8"
            )
        _set_state(
            kind="github-checkpoint",
            durable=True,
            restored=True,
            updated_at=snapshot.get("updated_at"),
            warning=None,
        )
        log.info("restored paper ledger from GitHub checkpoint %s", snapshot.get("updated_at"))
    except Exception:  # noqa: BLE001
        log.exception("checkpoint restore failed; continuing with a local book")
        _set_state(
            kind="ephemeral",
            durable=False,
            restored=False,
            warning="Checkpoint restore failed. This process is using a local £100 book.",
        )
    return persistence_status()


def _put_github(payload: dict[str, Any]) -> bool:
    token = _token()
    if not token:
        return False
    try:
        import base64

        import httpx

        headers = {
            "authorization": f"Bearer {token}",
            "accept": "application/vnd.github+json",
            "user-agent": "ai-trader-persist",
        }
        current = httpx.get(_api_url(), headers=headers, timeout=8.0)
        sha = None
        if current.status_code == 200:
            body = current.json()
            if isinstance(body, dict):
                sha = body.get("sha")
        encoded = base64.b64encode(
            json.dumps(payload, indent=2).encode("utf-8")
        ).decode("ascii")
        message = {
            "message": "paper ledger checkpoint [skip ci]",
            "content": encoded,
            "branch": SNAPSHOT_BRANCH,
        }
        if sha:
            message["sha"] = sha
        put = httpx.put(
            f"https://api.github.com/repos/{_repo()}/contents/{SNAPSHOT_PATH}",
            headers=headers,
            json=message,
            timeout=12.0,
        )
        return put.status_code in {200, 201}
    except Exception:  # noqa: BLE001
        log.exception("checkpoint push failed")
        return False


def checkpoint(db_path: Optional[Path] = None) -> dict[str, Any]:
    """Write a snapshot locally and, if a token exists, to GitHub."""
    from ai_trader.config import get_settings

    settings = get_settings()
    path = Path(db_path) if db_path is not None else settings.resolve_database_path()
    snapshot = build_snapshot(path)
    local = path.parent / "checkpoint.json"
    try:
        tmp = tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", delete=False, dir=str(path.parent), suffix=".tmp"
        )
        with tmp as handle:
            json.dump(snapshot, handle, indent=2)
            handle.write("\n")
        Path(tmp.name).replace(local)
    except Exception:  # noqa: BLE001
        log.exception("local checkpoint failed")
    pushed = _put_github(snapshot)
    _set_state(
        kind="github-checkpoint" if (pushed or persistence_status()["kind"] == "github-checkpoint") else "ephemeral",
        durable=bool(pushed) or persistence_status().get("durable") is True,
        updated_at=snapshot.get("updated_at"),
        warning=(
            None
            if pushed or persistence_status().get("durable")
            else (
                "Checkpoint is local only on this free host. A GitHub Actions "
                "job copies it off the box; until that runs, a restart could "
                "lose the latest bars."
            )
        ),
    )
    return snapshot


__all__ = [
    "build_snapshot",
    "checkpoint",
    "persistence_status",
    "restore_if_needed",
    "should_restore",
]
