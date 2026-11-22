"""Run the whole study and write the report.

    python -m ai_trader research fetch            # download public candles
    python -m ai_trader research run              # development run, holdout untouched
    python -m ai_trader research run2             # round 2 candidates, holdout untouched
    python -m ai_trader research run --holdout    # scores the holdout; logged, do it once

Outputs are simulated historical results, never trading profits.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

from ai_trader.research import backtest as bt
from ai_trader.research import data as rd
from ai_trader.research import metrics as mx
from ai_trader.research import qualification as q
from ai_trader.research import walkforward as wf
from ai_trader.research.strategies import BENCHMARK, CANDIDATES

#: Chosen for depth of liquidity and length of history. Picking today's large
#: caps is survivorship bias for ETH and SOL; BTC is the primary evidence.
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
PRIMARY = "BTCUSDT"
INTERVAL = "1d"
OUT_DIR = Path("docs/ai-trader/research")
LEDGER = OUT_DIR / "HOLDOUT_LEDGER.jsonl"


def study(series_by_symbol: dict, *, use_holdout: bool, plan: wf.Plan = wf.Plan()) -> dict:
    rows = []
    for sym, series in series_by_symbol.items():
        evs = [wf.evaluate(series, c, plan, use_holdout=use_holdout) for c in CANDIDATES]
        trials = [s for e in evs for s in e["trial_sharpes"]]  # every configuration tried on this asset
        bench = wf.evaluate(series, BENCHMARK, plan, use_holdout=use_holdout)
        for e in evs + [bench]:
            is_bench = e["candidate"] == BENCHMARK.name
            dsr = None if is_bench else _dsr(series, e, plan, trials)
            e["deflated_sharpe"] = dsr
            e["assessment"] = None if is_bench else q.assess(e, n_candidates=len(CANDIDATES), deflated_sharpe=dsr)
            del e["trial_sharpes"]
            rows.append(e)
    return {
        "schema": "ai-trader-research/v1",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "simulated": True,
        "interval": INTERVAL,
        "costs": {"base_per_side": bt.BASE_COSTS.__dict__, "stress_per_side": bt.STRESS_COSTS.__dict__},
        "criteria": q.criteria_dict(),
        "plan": plan.__dict__,
        "holdout_scored": use_holdout,
        "data": {s: {**rd.validate(v), "sha256": v.sha256()} for s, v in series_by_symbol.items()},
        "results": rows,
    }


def _dsr(series, ev, plan, trials) -> float:
    """Deflated Sharpe of the stitched out-of-sample returns."""
    parts = []
    by_ts = {t: i for i, t in enumerate(series.ts)}
    cand = next(c for c in CANDIDATES if c.name == ev["candidate"])
    for f in ev["folds"]:
        a, b = by_ts[f["start"]], by_ts[f["end"]] + 1
        parts.append(bt.run(series, cand.weights(series, f["params"]), start=a, end=b))
    return mx.deflated_sharpe(bt.stitch(parts).bar_returns, trials)


def _pct(v, d=1):
    return "n/a" if v is None else f"{v * 100:+.{d}f}%"


def table(rows: list) -> list:
    """Markdown result table plus the failed checks for one group of evaluations."""
    L = ["| Strategy | Verdict | OOS net | 2x costs | Gross | Max DD | Sharpe | Trades | Win | PF | Exposure | Folds + | Grid + | DSR | Timing p | Holdout net | Holdout DD |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for e in rows:
        o, a, h = e["oos"], e["assessment"], e["holdout"]
        L.append("| " + " | ".join([
            e["candidate"], (a["verdict"] + (f" ({a['label']})" if a["label"] else "")) if a else "benchmark",
            _pct(o["net_return"]), _pct(e["oos_stress"]["net_return"]), _pct(e["oos_gross_return"]),
            _pct(-o["max_drawdown"]), f"{o['sharpe']:.2f}", str(o["trades"]),
            "n/a" if o["win_rate"] is None else f"{o['win_rate']:.0%}",
            "n/a" if o["profit_factor"] is None else f"{o['profit_factor']:.2f}",
            f"{o['exposure']:.0%}", f"{e['folds_positive']:.0%}", f"{e['grid_positive']:.0%}",
            "n/a" if e["deflated_sharpe"] is None else f"{e['deflated_sharpe']:.2f}",
            f"{e['oos_excess']['p_le_zero']:.2f}",
            _pct(h["net_return"]) if h else "not scored", _pct(-h["max_drawdown"]) if h else "not scored"]) + " |")
    L += ["", "Failed checks:", ""]
    for e in (r for r in rows if r["assessment"]):
        L.append(f"- **{e['candidate']}**: {', '.join(e['assessment']['failed']) or 'none'}")
    return L


def markdown(report: dict) -> str:
    L = ["# Strategy research report", "",
         f"Generated {report['generated_at']}. **Simulated results on historical candles. Not trading profits.**", "",
         f"Daily Binance spot candles. Costs per side: fee {report['costs']['base_per_side']['fee']:.2%} + "
         f"slippage {report['costs']['base_per_side']['slippage']:.2%}; stress case is double. "
         "Orders fill at the next bar's open. Long or flat only, no leverage.", "",
         "Out-of-sample (OOS) = stitched walk-forward test folds; parameters for each fold were chosen only on earlier data. "
         + ("The final 20% holdout was scored in this run." if report["holdout_scored"]
            else "The final 20% holdout was **not** touched in this run."), ""]
    for sym, d in report["data"].items():
        L.append(f"- {sym}: {d['bars']} bars, {time.strftime('%Y-%m-%d', time.gmtime(d['first']))} to "
                 f"{time.strftime('%Y-%m-%d', time.gmtime(d['last']))}, {d['missing_bars']} missing, sha256 `{d['sha256'][:12]}`")
    for sym in report["data"]:
        L += ["", f"## {sym}", ""] + table([r for r in report["results"] if r["symbol"] == sym])
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="ai-trader research")
    ap.add_argument("action", choices=("fetch", "run", "run2", "tournament-register", "tournament"))
    ap.add_argument("--holdout", action="store_true", help="score the single-use holdout and log the access")
    ap.add_argument("--cache", default=str(rd.DEFAULT_CACHE))
    ap.add_argument("--out", default=str(OUT_DIR))
    args = ap.parse_args(argv)
    cache = Path(args.cache)
    if args.action == "fetch":
        for sym in SYMBOLS:
            for interval in (INTERVAL, "4h"):
                s = rd.fetch(sym, interval)
                print(sym, interval, rd.validate(s), rd.save(s, cache))
        return 0
    if args.action == "run2":
        from ai_trader.research import round2

        report = round2.study({(sym, i): rd.load(sym, i, cache) for sym in SYMBOLS for i in (INTERVAL, "4h")})
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "round2_development_report.json").write_text(json.dumps(report, indent=1, default=str))
        (out / "round2_development_report.md").write_text(round2.markdown(report))
        print(round2.markdown(report))
        return 0
    if args.action.startswith("tournament"):
        from ai_trader.research import tournament as tm

        series = {sym: rd.fetch(sym, INTERVAL) for sym in SYMBOLS}
        if args.action == "tournament-register":
            print(tm.save_registration(tm.register(series)))
            return 0
        board = tm.board(tm.load_registration(), series)
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "tournament_leaderboard.md").write_text(tm.markdown(board))
        print(tm.markdown(board))
        return 0
    series = {sym: rd.load(sym, INTERVAL, cache) for sym in SYMBOLS}
    report = study(series, use_holdout=args.holdout)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    name = "report" if args.holdout else "development_report"
    (out / f"{name}.json").write_text(json.dumps(report, indent=1, default=str))
    (out / f"{name}.md").write_text(markdown(report))
    if args.holdout:
        spec = json.dumps([[c.name, c.grid] for c in CANDIDATES] + [report["criteria"], report["costs"], report["plan"]], sort_keys=True)
        with (out / LEDGER.name).open("a") as f:
            f.write(json.dumps({"at": report["generated_at"], "data": {k: v["sha256"] for k, v in report["data"].items()},
                                "spec_sha256": hashlib.sha256(spec.encode()).hexdigest()}) + "\n")
    print(markdown(report))
    return 0
