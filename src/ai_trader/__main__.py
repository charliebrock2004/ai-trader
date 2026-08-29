"""CLI: python -m ai_trader [dashboard|status|init-db]"""

from __future__ import annotations

import argparse
import json
import sys

import uvicorn


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ai-trader",
        description="AI-Trader foundation. Paper/simulate only. No live trading.",
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="dashboard",
        choices=("dashboard", "status", "init-db", "grok-paper", "benchmark", "paper-session", "rpc"),
    )
    args = parser.parse_args(argv)

    from ai_trader.config import get_settings
    from ai_trader.runtime import get_runtime

    settings = get_settings()

    if args.command == "init-db":
        runtime = get_runtime()
        print(json.dumps(runtime.repository.health(), indent=2))
        return 0

    if args.command == "status":
        runtime = get_runtime()
        print(json.dumps(runtime.orchestrator.status(), indent=2, default=str))
        return 0

    if args.command == "grok-paper":
        runtime = get_runtime()
        print(json.dumps(runtime.orchestrator.grok_paper_cycle(symbol="SIM-UP"), indent=2, default=str))
        return 0

    if args.command == "benchmark":
        runtime = get_runtime()
        print(json.dumps(runtime.orchestrator.benchmark(), indent=2, default=str))
        return 0

    if args.command == "paper-session":
        runtime = get_runtime()
        print(json.dumps(runtime.orchestrator.start_paper_session(), indent=2, default=str))
        return 0

    if args.command == "rpc":
        from ai_trader.rpc import serve

        return serve()

    print(
        f"AI-Trader foundation  mode={settings.trading_mode}  "
        f"http://{settings.dashboard_host}:{settings.dashboard_port}  "
        "orders=disabled",
        file=sys.stderr,
    )
    uvicorn.run(
        "ai_trader.dashboard.app:app",
        host=settings.dashboard_host,
        port=settings.dashboard_port,
        log_level=settings.log_level.lower(),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
