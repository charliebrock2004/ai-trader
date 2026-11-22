"""Write today's tournament leaderboard to an append-only folder.

One JSON file per last completed daily candle. An existing file is never
rewritten, so the folder's git history is the audit trail of what the board
said on each day. Simulated paper results; read-only against public candles.

``history.json`` beside the folder is a small index of each entry's rank per
day. It is derived: rebuilt from the daily files on every run, never a source.
"""

import json
import sys
import time
from pathlib import Path

from ai_trader.research import data as rd
from ai_trader.research import tournament as tm
from ai_trader.research.lab import INTERVAL, SYMBOLS


def history(out_dir: Path) -> dict:
    """Each entry's rank on each recorded day, oldest first; ``None`` where an entry was absent."""
    days, boards = [], []
    for path in sorted(out_dir.glob("????-??-??.json")):
        days.append(path.stem)
        boards.append({r["id"]: r["rank"] for r in json.loads(path.read_text(encoding="utf-8"))["rows"]})
    ids = sorted({i for b in boards for i in b})
    return {"simulated": True, "live": False, "days": days, "ranks": {i: [b.get(i) for b in boards] for i in ids}}


def write_history(out_dir: Path) -> None:
    path = out_dir.parent / "history.json"
    path.write_text(json.dumps(history(out_dir), sort_keys=True) + "\n", encoding="utf-8")


def main(out_dir: str) -> int:
    board = tm.board(tm.load_registration(), {sym: rd.fetch(sym, INTERVAL) for sym in SYMBOLS})
    if board.get("live") is not False or board.get("simulated") is not True:
        raise SystemExit("refusing: board is not marked simulated and not live")
    bars = {d["last_bar"] for d in board["data"].values()}
    if len(bars) != 1:
        # Recording now would freeze a board where one asset is a day behind; a later run retries.
        raise SystemExit(f"refusing: assets disagree on the last completed candle: {board['data']}")
    day = time.strftime("%Y-%m-%d", time.gmtime(bars.pop()))
    path = Path(out_dir) / f"{day}.json"
    if path.exists():
        print(f"{path} already recorded; left unchanged")
        write_history(path.parent)
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(board, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    write_history(path.parent)
    print(f"wrote {path}: {len(board['rows'])} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
