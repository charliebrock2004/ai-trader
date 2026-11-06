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
    #: True once boot has decided what this process's ledger is (restored,
    #: deliberately fresh, or local). Until then nothing may be published: on
    #: 20 Sep 2026 a snapshot served mid-boot, before the database existed,
    #: published an empty ledger that every later boot restored, and the desk
    #: sat STOPPED on a blank book for seven weeks.
    "settled": False,
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
        #: Row counts the checkpoint job compares against the stored copy, so
        #: a ledger can only grow unless an operator explicitly resets it.
        "ledger_rows": ledger_row_counts(sql),
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


#: The checkpoint is a dump of the whole audit trail and is several MB; the old
#: 8-second budget was tight enough that a slow read could fail a restore.
FETCH_TIMEOUT_SECONDS = 45.0
FETCH_ATTEMPTS = 3


def _fetch_checkpoint_detailed() -> tuple[str, Optional[dict[str, Any]]]:
    """Return ``("ok", body)``, ``("absent", None)`` or ``("failed", None)``.

    "absent" means GitHub answered that there is no checkpoint. "failed" means
    we could not find out, and the two must never be confused: treating a
    failed read as "no checkpoint" is how a real ledger gets replaced by a
    fresh one.
    """
    import time

    for attempt in range(FETCH_ATTEMPTS):
        try:
            import httpx

            response = httpx.get(
                _raw_url(),
                headers={"user-agent": "ai-trader-persist", "accept": "application/json"},
                timeout=FETCH_TIMEOUT_SECONDS,
                follow_redirects=True,
            )
            if response.status_code == 404:
                return "absent", None
            if response.status_code != 200:
                log.warning("checkpoint fetch returned %s", response.status_code)
            else:
                body = response.json()
                if not isinstance(body, dict) or not isinstance(body.get("sql"), str):
                    return "invalid", None
                if body.get("live") is True:
                    return "invalid", None
                return "ok", body
        except Exception:  # noqa: BLE001 — a failed restore must not crash boot
            log.exception("checkpoint fetch failed (attempt %s)", attempt + 1)
        if attempt + 1 < FETCH_ATTEMPTS:
            time.sleep(2.0 * (attempt + 1))
    return "failed", None


class CheckpointReadError(RuntimeError):
    """The checkpoint exists or might exist, but could not be read."""


def fetch_checkpoint() -> Optional[dict[str, Any]]:
    """The stored checkpoint, ``None`` if there genuinely is none.

    Raises :class:`CheckpointReadError` when GitHub could not be asked, so a
    network failure can never be mistaken for "there is no ledger".
    """
    status, body = _fetch_checkpoint_detailed()
    if status == "failed":
        raise CheckpointReadError("checkpoint could not be read")
    return body if status == "ok" else None


def ledger_row_counts(sql: str) -> dict[str, int]:
    """Rows per ledger table in a dump. Used to refuse a checkpoint that shrinks."""
    counts = {"agent_life": 0, "decisions": 0, "paper_fills": 0}
    for line in (sql or "").splitlines():
        for table in counts:
            if line.startswith(f'INSERT INTO "{table}"') or line.startswith(f"INSERT INTO {table} "):
                counts[table] += 1
    return counts


def snapshot_problem(snapshot: dict[str, Any]) -> Optional[str]:
    """Why this snapshot must not be published, or None if it may be."""
    if not persistence_status().get("settled"):
        return "The worker has not finished deciding its ledger yet (boot or restore in progress)."
    if persistence_status().get("kind") == "restore_failed":
        return (
            "The checkpoint could not be read at boot. Publishing this process's "
            "book would overwrite the real ledger."
        )
    if snapshot.get("live") is True:
        return "Snapshot claims live trading."
    sql = snapshot.get("sql")
    if not isinstance(sql, str) or not sql.strip():
        return "Snapshot has no ledger (empty database dump)."
    if ledger_row_counts(sql)["agent_life"] < 1:
        return "Snapshot has no agent_life row; it is not a ledger."
    return None


def restore_if_needed(db_path: Path) -> dict[str, Any]:
    """Overlay GitHub state onto an empty local database. Never clobbers a live db."""
    if not should_restore():
        _set_state(
            settled=True,
            kind="local",
            durable=False,
            restored=False,
            warning="Local process. Checkpoint restore is disabled.",
        )
        return persistence_status()
    if not _db_is_empty(db_path):
        _set_state(
            settled=True,
            kind="github-checkpoint",
            durable=True,
            restored=False,
            warning=None,
        )
        return persistence_status()
    try:
        snapshot = fetch_checkpoint()
        status = "ok" if snapshot else "absent"
    except CheckpointReadError:
        snapshot, status = None, "failed"
    if status == "failed":
        # We do not know what the ledger is. Run, but publish nothing: a fresh
        # £100 book written over the real checkpoint is unrecoverable from
        # here, while a missed checkpoint costs one wake's worth of history.
        _set_state(
            settled=True,
            kind="restore_failed",
            durable=False,
            restored=False,
            warning=(
                "The GitHub checkpoint could not be read. This process is running "
                "a temporary book and will not publish it, so the real ledger is "
                "not overwritten. The next wake retries the restore."
            ),
        )
        return persistence_status()
    if status != "ok" or not snapshot or not str(snapshot.get("sql") or "").strip():
        _set_state(
            settled=True,
            kind="ephemeral",
            durable=False,
            restored=False,
            warning=(
                "No usable GitHub checkpoint. This free host has no disk; the "
                "book starts at £100 and is published from the first checkpoint."
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
            settled=True,
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
            settled=True,
            kind="restore_failed",
            durable=False,
            restored=False,
            warning=(
                "Checkpoint restore failed. This process is using a temporary book "
                "and will not publish it."
            ),
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
    problem = snapshot_problem(snapshot)
    if problem:
        log.warning("checkpoint not published: %s", problem)
        return snapshot
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
    "ledger_row_counts",
    "snapshot_problem",
    "build_snapshot",
    "checkpoint",
    "persistence_status",
    "restore_if_needed",
    "should_restore",
]
