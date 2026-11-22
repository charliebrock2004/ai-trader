"""Write today's tournament leaderboard to an append-only folder.

One JSON file per last completed daily candle. An existing file is never
rewritten, so the folder's git history is the audit trail of what the board
said on each day. Simulated paper results; read-only against public candles.
"""

import json
import sys
import time
from pathlib import Path

from ai_trader.research import data as rd
from ai_trader.research import tournament as tm
from ai_trader.research.lab import INTERVAL, SYMBOLS


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
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(board, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {path}: {len(board['rows'])} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
