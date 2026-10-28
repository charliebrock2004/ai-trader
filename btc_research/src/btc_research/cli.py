"""Command line entry.

    python -m btc_research run --start 2019-01-01 --out outputs/latest
    python -m btc_research --help

No private credentials. No order placement.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from btc_research.costs import CostModel, ExecutionConfig
from btc_research.pipeline import StudyConfig, run_study


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="btc-research",
        description="BTC spot historical research. Does not trade and does not read private keys.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="Download public candles if needed, evaluate, write the bundle")
    run.add_argument("--symbol", default="BTCUSDT")
    run.add_argument("--interval", default="1h", choices=("1h", "1d"))
    run.add_argument("--start", default="2019-01-01")
    run.add_argument("--end", default=None, help="UTC date YYYY-MM-DD. Default: now")
    run.add_argument("--cache", type=Path, default=Path("data/cache/BTCUSDT_1h.csv"))
    run.add_argument("--out", type=Path, default=Path("outputs/latest"))
    run.add_argument("--fee-bps", type=float, default=10.0)
    run.add_argument("--slippage-bps", type=float, default=1.0)
    run.add_argument("--half-spread-bps", type=float, default=0.5)
    run.add_argument("--initial-equity", type=float, default=10_000.0)
    run.add_argument("--max-exposure", type=float, default=1.0)
    run.add_argument("--train-bars", type=int, default=24 * 180)
    run.add_argument("--test-bars", type=int, default=24 * 90)
    run.add_argument("--step-bars", type=int, default=24 * 90)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "run":
        execution = ExecutionConfig(
            cost=CostModel(
                fee_bps=args.fee_bps,
                slippage_bps=args.slippage_bps,
                half_spread_bps=args.half_spread_bps,
            ),
            initial_equity=args.initial_equity,
            max_exposure=args.max_exposure,
        )
        summary = run_study(
            StudyConfig(
                symbol=args.symbol,
                interval=args.interval,
                start=args.start,
                end=args.end,
                cache_path=args.cache,
                out_dir=args.out,
                execution=execution,
                train_bars=args.train_bars,
                test_bars=args.test_bars,
                step_bars=args.step_bars,
            )
        )
        verdict = summary["verdict"]
        print(json.dumps({"verdict": verdict["code"], "promising": verdict["promising"], "out": str(args.out)}))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
