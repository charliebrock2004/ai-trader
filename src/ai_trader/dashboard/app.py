"""Foundation dashboard. Read-only status plus the kill switch.

No secrets are returned. No orders can be placed from this API.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ai_trader import __version__
from ai_trader.runtime import get_runtime

STATIC_DIR = Path(__file__).resolve().parent / "static"
TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"


class KillSwitchRequest(BaseModel):
    engaged: bool
    reason: str = Field(default="Dashboard toggle", max_length=400)


def create_app() -> FastAPI:
    app = FastAPI(
        title="AI-Trader",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", response_class=HTMLResponse)
    def index() -> FileResponse:
        return FileResponse(TEMPLATE_DIR / "index.html")

    @app.get("/favicon.svg")
    def favicon() -> FileResponse:
        return FileResponse(STATIC_DIR / "favicon.svg", media_type="image/svg+xml")

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True, "service": "ai-trader", "version": __version__}

    @app.get("/api/status")
    def status() -> dict:
        runtime = get_runtime()
        payload = runtime.orchestrator.status()
        payload["version"] = __version__
        return payload

    @app.get("/api/events")
    def events(limit: int = 50) -> dict:
        runtime = get_runtime()
        return {"events": runtime.repository.list_events(limit=min(limit, 200))}

    @app.get("/api/decisions")
    def decisions(limit: int = 50) -> dict:
        runtime = get_runtime()
        return {"decisions": runtime.repository.list_decisions(limit=min(limit, 200))}

    @app.get("/api/trades")
    def trades(limit: int = 50) -> dict:
        runtime = get_runtime()
        return {"trades": runtime.repository.list_trades(limit=min(limit, 200))}

    @app.get("/api/positions")
    def positions() -> dict:
        runtime = get_runtime()
        return {"positions": runtime.repository.list_positions()}

    @app.get("/api/account")
    def account() -> dict:
        runtime = get_runtime()
        return {"account": runtime.repository.latest_account()}

    @app.post("/api/kill-switch")
    def set_kill_switch(body: KillSwitchRequest) -> dict:
        runtime = get_runtime()
        if body.engaged:
            snap = runtime.kill_switch.engage(body.reason)
            event_type = "kill_switch_engaged"
            message = "Kill switch engaged."
        else:
            snap = runtime.kill_switch.disengage(body.reason)
            event_type = "kill_switch_disengaged"
            message = "Kill switch disengaged. Order placement remains disabled."
        runtime.repository.record_event(
            level="WARNING" if body.engaged else "INFO",
            source="dashboard",
            event_type=event_type,
            message=message,
            details={"reason": body.reason},
        )
        return {"kill_switch": snap}

    @app.post("/api/dry-run")
    def dry_run() -> dict:
        runtime = get_runtime()
        return runtime.orchestrator.dry_run()

    @app.post("/api/orders")
    def orders_blocked(request: Request) -> None:
        get_runtime().repository.record_event(
            level="ERROR",
            source="dashboard",
            event_type="order_blocked",
            message="Dashboard refused an order attempt.",
            details={"path": str(request.url)},
        )
        raise HTTPException(
            status_code=403,
            detail="Order placement is disabled in the foundation build.",
        )

    return app


app = create_app()
