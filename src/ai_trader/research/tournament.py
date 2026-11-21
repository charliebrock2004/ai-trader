"""Forward paper tournament: every candidate, frozen in advance, scored only on
bars that did not exist when it was frozen.

A registration records each strategy's parameters, the costs, the first forward
bar and a hash of the history before it, and is committed to the repository
before any forward bar closes. From then on the leaderboard is a pure function
of that file and public candles, so:

* it needs no browser, no always-on process and no database: a restart, a
  sleeping host or a lost disk cannot lose or duplicate a paper trade;
* nobody, including the strategy author, can adjust a parameter after seeing a
  forward result without changing a committed file.

It is still a simulation. Fills are assumed at the next daily open with fixed
costs; no order is sent anywhere. It fails closed: stale candles, revised
history or a missing registration produce no leaderboard rather than a guess.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from ai_trader.research import backtest as bt
from ai_trader.research import data as rd
from ai_trader.research import metrics as mx
from ai_trader.research import qualification as q
from ai_trader.research.strategies import BENCHMARK, CANDIDATES

REGISTRATION = Path(__file__).with_name("tournament_registration.json")
WARMUP = 200
#: Completed bars may lag by this many intervals before data counts as stale.
MAX_LAG_BARS = 2
_BY_NAME = {c.name: c for c in CANDIDATES + (BENCHMARK,)}


class TournamentError(RuntimeError):
    """The tournament cannot be scored honestly right now."""


def register(series_by_symbol: dict, *, costs: bt.Costs = bt.BASE_COSTS) -> dict:
    """Freeze one parameter set per (candidate, symbol), chosen on all history so far.

    The first forward bar is the one after the last completed bar, so no
    registered choice has seen any bar it will be scored on.
    """
    entries, data = [], {}
    for sym, s in series_by_symbol.items():
        rd.validate(s)
        if len(s) <= WARMUP + 365:
            raise TournamentError(f"{sym}: too little history to choose parameters")
        step = rd.INTERVAL_SECONDS[s.interval]
        data[sym] = {"interval": s.interval, "history_bars": len(s), "history_sha256": s.sha256(),
                     "first_forward_ts": s.ts[-1] + step}
        for c in CANDIDATES + (BENCHMARK,):
            best, best_s = c.grid[0], float("-inf")
            for p in c.grid:
                sh = mx.sharpe(bt.run(s, c.weights(s, p), costs, start=WARMUP).bar_returns, s.bars_per_year)
                if sh > best_s:
                    best, best_s = p, sh
            entries.append({"id": f"{c.name}:{sym}", "candidate": c.name, "family": c.family, "symbol": sym,
                            "params": best})
    return {"schema": "ai-trader-tournament/v1", "simulated": True,
            "registered_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "costs": {"fee": costs.fee, "slippage": costs.slippage},
            "criteria": q.criteria_dict(), "data": data, "entries": entries}


def save_registration(reg: dict, path: Path = REGISTRATION) -> Path:
    if path.exists():
        raise TournamentError(f"{path} already exists. A registration is never overwritten; "
                              "a new tournament needs a new file and a logged reason.")
    path.write_text(json.dumps(reg, indent=1) + "\n")
    return path


def load_registration(path: Path = REGISTRATION) -> dict:
    if not path.exists():
        raise TournamentError("no tournament has been registered")
    return json.loads(path.read_text())


def board(reg: dict, series_by_symbol: dict, *, now: Optional[float] = None) -> dict:
    """Score every registered entry on forward bars only."""
    now = time.time() if now is None else now
    costs = bt.Costs(**reg["costs"])
    crit = q.Criteria(**reg["criteria"])
    rows, data = [], {}
    for sym, d in reg["data"].items():
        s = series_by_symbol.get(sym)
        if s is None:
            raise TournamentError(f"{sym}: no candles")
        rd.validate(s)
        step = rd.INTERVAL_SECONDS[s.interval]
        n0 = d["history_bars"]
        if len(s) < n0 or s.prefix(n0).sha256() != d["history_sha256"]:
            raise TournamentError(f"{sym}: history before registration no longer matches its recorded hash")
        last_close = s.ts[-1] + step
        if now - last_close > MAX_LAG_BARS * step:
            raise TournamentError(f"{sym}: candles are stale (last completed bar closed "
                                  f"{(now - last_close) / 3600:.0f}h ago)")
        if len(s) > n0 and s.ts[n0] != d["first_forward_ts"]:
            raise TournamentError(f"{sym}: first forward bar is missing")
        data[sym] = {"forward_bars": len(s) - n0, "last_bar": s.ts[-1], "sha256": s.sha256()}
    for e in reg["entries"]:
        s, n0 = series_by_symbol[e["symbol"]], reg["data"][e["symbol"]]["history_bars"]
        fwd = len(s) - n0
        row = {**e, "forward_bars": fwd, "benchmark": e["candidate"] == BENCHMARK.name}
        w = _BY_NAME[e["candidate"]].weights(s, e["params"])
        row["target_weight"] = w[-1]  # decided on the latest close, for the next open
        if fwd > 0:
            r = bt.run(s, w, costs, start=n0, close_at_end=False)
            m = mx.summarise(r, s.bars_per_year)
            row.update({k: m[k] for k in ("net_return", "max_drawdown", "trades", "open_trade", "orders", "win_rate",
                                          "profit_factor", "exposure", "fees", "slippage", "asset_return")})
            row["sharpe"] = m["sharpe"] if fwd >= 30 else None  # meaningless on a handful of bars
            row["equity"] = r.equity[-1]
            # Execution here is simulated, so there are no execution errors to count.
            gate = q.assess_paper({"days": fwd * rd.INTERVAL_SECONDS[s.interval] / 86400, "trades": m["trades"],
                                   "net_return": m["net_return"], "max_drawdown": m["max_drawdown"],
                                   "execution_error_rate": 0.0}, crit)
        else:
            row.update({"net_return": 0.0, "max_drawdown": 0.0, "trades": 0, "equity": 1.0, "sharpe": None})
            gate = q.assess_paper({}, crit)
        row["paper_gate"] = {"passed": not gate["failed"], "failed": gate["failed"]}
        rows.append(row)
    # Risk first: a gate pass outranks everything, then return net of the drawdown taken to earn it.
    rows.sort(key=lambda r: (not r["paper_gate"]["passed"], -(r["net_return"] - r["max_drawdown"]), r["id"]))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    min_bars = min(d["forward_bars"] for d in data.values())
    return {"ok": True, "simulated": True, "live": False, "registered_at": reg["registered_at"],
            "as_of": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)), "costs": reg["costs"],
            "provisional": min_bars < crit.min_paper_days,
            "note": "Simulated paper results on forward public candles. Not trading profits. Passing the paper "
                    "gate does not qualify a strategy on its own: it must also have passed historical "
                    "qualification, and any live pilot needs the owner's explicit approval.",
            "data": data, "rows": rows}


def markdown(b: dict) -> str:
    L = ["# Forward paper tournament", "",
         f"As of {b['as_of']}; registered {b['registered_at']}. **Simulated. Not trading profits.**", "",
         b["note"], ""]
    if b["provisional"]:
        L += ["**Provisional:** too few forward days for any ranking to mean anything yet.", ""]
    L += ["| # | Strategy | Asset | Bars | Net | Asset | Max DD | Trades | Target weight | Paper gate |", "|---|---|---|---|---|---|---|---|---|---|"]
    for r in b["rows"]:
        L.append(f"| {r['rank']} | {r['candidate']} | {r['symbol']} | {r['forward_bars']} | {r['net_return']:+.1%} | "
                 f"{r.get('asset_return', 0.0):+.1%} | {-r['max_drawdown']:.1%} | {r['trades']} | {r['target_weight']:.2f} | "
                 f"{'pass' if r['paper_gate']['passed'] else 'not yet: ' + ', '.join(r['paper_gate']['failed'])} |")
    return "\n".join(L) + "\n"


# -- worker-facing, cached ------------------------------------------------------

_cache: dict = {"at": 0.0, "board": None, "attempt": 0.0, "error": None}
_lock = threading.Lock()
REFRESH_SECONDS = 900
RETRY_SECONDS = 60


def live_board(*, fetch: Callable[..., rd.Series] = rd.fetch, now: Optional[float] = None,
               path: Path = REGISTRATION) -> dict:
    """The leaderboard for the HTTP worker. Refreshes at most every 15 minutes;
    after a failure it retries no sooner than a minute later and never serves a
    board older than one refresh interval as if it were current."""
    now = time.time() if now is None else now
    with _lock:
        fresh = _cache["board"] is not None and now - _cache["at"] < REFRESH_SECONDS
        if not fresh and now - _cache["attempt"] >= RETRY_SECONDS:
            _cache["attempt"] = now
            try:
                reg = load_registration(path)
                series = {sym: fetch(sym, d["interval"]) for sym, d in reg["data"].items()}
                _cache.update(board=board(reg, series, now=now), at=now, error=None)
                fresh = True
            except Exception as exc:  # noqa: BLE001 — fail closed and say why
                _cache.update(board=None, error=f"{type(exc).__name__}: {exc}")
        heartbeat = {"last_success": _cache["at"] or None, "last_attempt": _cache["attempt"] or None,
                     "error": _cache["error"]}
        if fresh:
            return {**_cache["board"], "heartbeat": heartbeat}
        return {"ok": False, "simulated": True, "live": False, "rows": [], "heartbeat": heartbeat,
                "error": _cache["error"] or "Leaderboard is refreshing."}


def _reset_cache() -> None:
    with _lock:
        _cache.update(at=0.0, board=None, attempt=0.0, error=None)
