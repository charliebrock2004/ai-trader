"""HTTP transport for the paper desk.

The desk already has exactly one engine and one command surface:
:mod:`ai_trader.rpc`, reached over stdio when Node spawns Python as a child.
That works locally and cannot work on Vercel, where there is no Python binary
and no process that outlives a request.

This module puts the *same* command surface on a socket, so the engine can live
somewhere persistent while the UI stays on Vercel. It is transport only: every
route below resolves to one :func:`ai_trader.rpc.handle` call. There is no
second engine, no second ledger, and no route that reaches execution without
going through the policy guardian, the risk engine, the survival check and the
audit trail that ``handle`` already goes through.

Three properties it has to keep:

* **Fail closed.** Mutations require ``AI_TRADER_API_TOKEN``. Unset means every
  mutation is refused. This is stricter than the Node layer, which allows an
  unconfigured local preview — there is no "local" here, because anything
  reachable over a socket is reachable by someone else.
* **Reads are genuinely reads.** The GET routes map onto
  :data:`ai_trader.rpc.READ_COMMANDS` and nothing else, so no amount of URL
  guessing turns a read into a trade.
* **No CORS.** The browser must never hold the control token, so the browser
  never calls this service directly; the Vercel server proxies and attaches the
  token out of the browser's reach. Leaving CORS off is what enforces that.

Live trading is not reachable from here. It is not reachable from anywhere:
``ai_trader.safety.LIVE_TRADING_ALLOWED`` is a constant, and this layer adds no
flag, header or payload field that could change it.
"""

from __future__ import annotations

import hmac
import logging
import os
import threading
import time
from collections import deque
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Optional

from fastapi import Body, FastAPI, Path, Query, Request
from fastapi.responses import JSONResponse

from ai_trader.rpc import MUTATING_COMMANDS, READ_COMMANDS, handle
from ai_trader.safety import LIVE_TRADING_ALLOWED

log = logging.getLogger(__name__)

TOKEN_HEADER = "x-ai-trader-token"

#: Mutating calls per window, per client token. A stuck browser tab retrying
#: Start is the realistic threat, not an attacker — but the effect on the
#: model budget is the same either way, so the limit is deliberately low.
RATE_LIMIT_CALLS = 20
RATE_LIMIT_WINDOW_SECONDS = 60.0

_NO_STORE = {"cache-control": "no-store"}


def _configured_token() -> str:
    """The control token, read from the environment on every call.

    Read live rather than cached so a platform that injects secrets after
    import still gets a closed deployment rather than a silently open one.
    """
    return (os.environ.get("AI_TRADER_API_TOKEN") or os.environ.get("API_TOKEN") or "").strip()


class _RateLimiter:
    """Fixed-window counter, keyed by caller. In-process by design.

    The desk is one process with one ledger, so an in-process counter is the
    whole population. A distributed limiter here would be pretending the
    deployment is something it is not.
    """

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            window = self._hits.setdefault(key, deque())
            while window and now - window[0] > RATE_LIMIT_WINDOW_SECONDS:
                window.popleft()
            if len(window) >= RATE_LIMIT_CALLS:
                return False
            window.append(now)
            return True


def _json(payload: dict[str, Any], status: int = 200) -> JSONResponse:
    return JSONResponse(payload, status_code=status, headers=_NO_STORE)


def _refusal(message: str, status: int) -> JSONResponse:
    """A refusal the UI can render without special-casing.

    It carries the same "not running, not live" shape as a failed command so
    the dashboard degrades to an honest STOPPED rather than to a blank screen.
    """
    return _json(
        {
            "ok": False,
            "live": False,
            "live_trading_allowed": False,
            "running": False,
            "stopped": True,
            "engine": "python-worker",
            "error": message,
            "data_error": message,
        },
        status,
    )


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Wake the agent on boot; halt its loops on exit.

    Constructing the runtime opens the database, replays the survival latch and
    calls ``DeskWorker.recover()``, which restarts a session the operator had
    asked to keep running before the last restart. It also touches the network
    (the CPI calendar), so it happens on a background thread: the socket has to
    be listening promptly or the host's health check fails the deploy before
    the agent has finished waking up.
    """

    def _build() -> None:
        try:
            from ai_trader.runtime import get_runtime

            get_runtime()
            log.info("runtime ready; session recovery complete")
        except Exception:  # noqa: BLE001 — a boot failure must surface as a
            # degraded desk that can explain itself, not a process that crashes
            # and restarts in a loop without ever serving the reason.
            log.exception("runtime failed to start")

    threading.Thread(target=_build, daemon=True, name="ai-trader-boot").start()
    try:
        yield
    finally:
        try:
            from ai_trader.runtime import reset_runtime

            # Halts the loops and closes the database. It deliberately leaves
            # the desired-running latch set, so a redeploy resumes a session
            # the operator never asked to stop.
            reset_runtime()
        except Exception:  # noqa: BLE001
            log.exception("shutdown was not clean")


def create_app() -> FastAPI:
    app = FastAPI(
        title="AI-Trader paper desk",
        description="Paper trading only. No live-money execution exists in this service.",
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=_lifespan,
    )
    limiter = _RateLimiter()

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------
    # Deliberately cheap and runtime-free: the platform polls this to decide
    # whether the deploy is live, and it must answer during boot rather than
    # waiting on a database or a calendar fetch.
    @app.get("/health")
    def health() -> JSONResponse:
        return _json(
            {
                **handle({"cmd": "health"}),
                "live_trading_allowed": LIVE_TRADING_ALLOWED,
                "control_enabled": bool(_configured_token()),
            }
        )

    @app.get("/")
    def root() -> JSONResponse:
        """Names the service without describing its internals to the internet."""
        return _json({"service": "ai-trader", "paper_only": True, "live": False})

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    def read(cmd: str, payload: Optional[dict[str, Any]] = None) -> JSONResponse:
        # Guarded rather than asserted: this is the boundary that keeps a GET
        # from ever reaching a mutating command, and `python -O` strips asserts.
        if cmd not in READ_COMMANDS:
            raise RuntimeError(f"{cmd!r} is not a read command")
        return _dispatch(cmd, payload)

    @app.get("/api/status")
    def status() -> JSONResponse:
        return read("status")

    @app.get("/api/agent")
    def agent() -> JSONResponse:
        return read("agent")

    @app.get("/api/performance")
    def performance() -> JSONResponse:
        return read("performance")

    @app.get("/api/system")
    def system() -> JSONResponse:
        return read("system")

    @app.get("/api/decisions")
    def decisions(
        limit: int = Query(50, ge=1, le=200),
        only_executed: bool = Query(False),
    ) -> JSONResponse:
        return read("decisions", {"limit": limit, "only_executed": only_executed})

    @app.get("/api/decisions/{decision_id}")
    def decision(decision_id: int = Path(..., ge=1)) -> JSONResponse:
        return read("decision", {"id": decision_id})

    @app.get("/api/opportunities")
    def opportunities(limit: int = Query(50, ge=1, le=200)) -> JSONResponse:
        return read("opportunities", {"limit": limit})

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------
    def mutate(
        request: Request, cmd: str, payload: Optional[dict[str, Any]] = None
    ) -> JSONResponse:
        if cmd not in MUTATING_COMMANDS:
            raise RuntimeError(f"{cmd!r} is not a mutating command")
        expected = _configured_token()
        if not expected:
            return _refusal(
                "This deployment has no AI_TRADER_API_TOKEN configured, so Start, "
                "Stop and Run-cycle are disabled. Set one on the worker and on the "
                "frontend to enable control.",
                503,
            )
        supplied = (request.headers.get(TOKEN_HEADER) or "").strip()
        if not supplied or not hmac.compare_digest(supplied, expected):
            return _refusal("Unauthorised.", 401)
        if not limiter.allow(f"{supplied}:{cmd}"):
            return _refusal("Too many control requests. Wait a minute and retry.", 429)
        return _dispatch(cmd, payload)

    @app.post("/api/start")
    def start(request: Request, body: dict[str, Any] = Body(default_factory=dict)) -> JSONResponse:
        # The body is passed through unvalidated on purpose: rpc.handle coerces
        # and clamps every field itself, because "the caller already checked" is
        # not a security property when the caller is another process.
        return mutate(request, "start", body if isinstance(body, dict) else {})

    @app.post("/api/stop")
    def stop(request: Request) -> JSONResponse:
        return mutate(request, "stop")

    @app.post("/api/cycle")
    def cycle(request: Request) -> JSONResponse:
        return mutate(request, "cycle")

    return app


def _dispatch(cmd: str, payload: Optional[dict[str, Any]] = None) -> JSONResponse:
    """Run one engine command and shape the reply.

    Handlers are plain ``def``, so FastAPI runs them in a worker thread and a
    slow command (a market fetch, an analyst call) cannot stall the event loop
    and make the whole service look dead.

    ``ok: false`` means different things on the two kinds of command, and
    conflating them is a real bug: on a mutation it means the desk refused, so
    the status code has to say so and the UI must not read it as success. On a
    read it is a *finding* — ``system`` reports ``ok: false`` whenever any
    component is degraded, which is the report working, not the request
    failing. Sending 503 for that would make an honest health report look like
    an outage.
    """
    try:
        result = handle({"cmd": cmd, "payload": payload or {}})
    except Exception as exc:  # noqa: BLE001 — fail closed, and say why
        log.exception("engine command %s failed", cmd)
        return _refusal(f"Engine command '{cmd}' failed: {exc}", 500)
    if not isinstance(result, dict):
        return _refusal(f"Engine command '{cmd}' returned no result.", 500)
    refused = cmd in MUTATING_COMMANDS and result.get("ok") is False
    return _json(result, 503 if refused else 200)


#: Importable target for ``uvicorn ai_trader.http_api:app``.
app = create_app()


def serve() -> int:
    """Run the worker in the foreground. The process *is* the agent."""
    import uvicorn

    from ai_trader.config import get_settings

    settings = get_settings()
    # Settings resolve the port from PORT then WORKER_PORT — PORT is what a
    # managed host injects, and binding anything else is the difference between
    # a healthy deploy and a host reporting "no open ports detected".
    host = settings.worker_host.strip() or "0.0.0.0"
    port = settings.worker_port
    log.info(
        "ai-trader paper desk on %s:%d  mode=%s  control=%s  orders=disabled",
        host,
        port,
        settings.trading_mode,
        "enabled" if _configured_token() else "DISABLED (no token)",
    )
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level=settings.log_level.lower(),
        # One process, one ledger. Reloading or forking would give the agent a
        # second brain and two writers on the same database.
        workers=1,
        access_log=False,
    )
    return 0


__all__ = ["app", "create_app", "serve"]
