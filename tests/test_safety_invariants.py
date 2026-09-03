"""Whole-repository safety invariants.

The original audit only grepped `src/ai_trader`. These extend it to the
TypeScript app, the API routes and every other execution path, and add the
structural checks the audit could not express: that the simulator cannot make a
network call, that the LLM cannot reach an order, and that concurrency does not
corrupt a session.
"""

from __future__ import annotations

import ast
import re
import threading
from pathlib import Path

import pytest

from ai_trader.contracts.risk import ContractRiskEngine
from ai_trader.markets.cpi_contracts import cpi_contracts, register_cpi_ladder
from ai_trader.markets.fees import ZeroFeeModel
from ai_trader.paper.simulator import PaperSimulator
from ai_trader.safety import ALPACA_LIVE_BASE_URL, LIVE_TRADING_ALLOWED

ROOT = Path(__file__).resolve().parents[1]
PYTHON_ROOT = ROOT / "src" / "ai_trader"
TS_ROOTS = [ROOT / "src" / "lib", ROOT / "src" / "routes", ROOT / "src" / "components"]


def _python_files() -> list[Path]:
    return sorted(PYTHON_ROOT.rglob("*.py"))


def _ts_files() -> list[Path]:
    files: list[Path] = []
    for root in TS_ROOTS:
        if root.exists():
            files.extend(sorted(root.rglob("*.ts")))
            files.extend(sorted(root.rglob("*.tsx")))
    return files


# ==========================================================================
# The live-trading invariant, across every path
# ==========================================================================
def test_live_trading_stays_disabled_everywhere() -> None:
    assert LIVE_TRADING_ALLOWED is False
    for path in _python_files() + _ts_files():
        body = path.read_text(encoding="utf-8")
        assert "LIVE_TRADING_ALLOWED = True" not in body, path
        assert "live_trading_allowed: true" not in body.lower(), path


def test_the_live_broker_host_appears_nowhere_but_the_block_list() -> None:
    for path in _python_files() + _ts_files():
        body = path.read_text(encoding="utf-8").replace("paper-api.alpaca.markets", "")
        if path.name == "safety.py":
            assert ALPACA_LIVE_BASE_URL in path.read_text(encoding="utf-8")
            continue
        assert ALPACA_LIVE_BASE_URL not in body, path


def test_no_module_enables_orders() -> None:
    for path in _python_files():
        body = path.read_text(encoding="utf-8")
        assert "allow_orders = True" not in body, path
        assert "allow_orders=True" not in body, path


#: Word-bounded so `alive: true` does not match `live: true`.
_LIVE_TRUE = re.compile(r"\blive\s*:\s*true", re.IGNORECASE)


def test_the_frontend_never_names_a_broker_or_live_mode() -> None:
    for path in _ts_files():
        body = path.read_text(encoding="utf-8")
        assert "alpaca" not in body.lower(), path
        assert not _LIVE_TRUE.search(body), path


# ==========================================================================
# Network isolation
# ==========================================================================
NETWORK_FREE_MODULES = (
    "paper/simulator.py",
    "paper/ledger.py",
    "paper/execution.py",
    "paper/performance.py",
    "contracts/ledger.py",
    "contracts/risk.py",
    "risk/engine.py",
    "risk/limits.py",
    "survival/config.py",
    "survival/engine.py",
    "survival/latch.py",
    "survival/policy.py",
    "edge/edge.py",
    "edge/probability.py",
    "edge/opportunity.py",
    "analytics/calibration.py",
    "analytics/performance.py",
    "replay/recorder.py",
    "markets/fees.py",
    "markets/paper.py",
)

NETWORK_MODULES = {"httpx", "requests", "urllib", "urllib.request", "http", "socket", "aiohttp"}


def test_the_simulation_and_decision_core_cannot_import_a_network_library() -> None:
    """Structural: these modules have no way to reach the network at all."""
    offenders: list[str] = []
    for relative in NETWORK_FREE_MODULES:
        path = PYTHON_ROOT / relative
        if not path.exists():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in NETWORK_MODULES:
                        offenders.append(f"{relative}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                if (node.module or "").split(".")[0] in NETWORK_MODULES:
                    offenders.append(f"{relative}: from {node.module}")
    assert not offenders, offenders


def test_the_simulator_makes_no_network_call_even_if_one_were_possible(monkeypatch) -> None:
    """Behavioural backstop: any socket use during a run fails the test."""
    import socket

    def explode(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("The paper simulator attempted a network connection.")

    monkeypatch.setattr(socket.socket, "connect", explode)
    monkeypatch.setattr(socket, "create_connection", explode)

    from ai_trader.market_data.generator import generate_series
    from ai_trader.paper.signals import FixtureHoldSource

    series = generate_series("SIM-UP", limit=40, seed=42)
    report = PaperSimulator(spread_bps=0, slip_bps=0).run(series, source=FixtureHoldSource())
    assert report["ok"] is True
    assert report["broker_submit_calls"] == 0


def test_the_contract_risk_engine_makes_no_network_call(monkeypatch) -> None:
    import socket

    monkeypatch.setattr(
        socket.socket, "connect",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("network call")),
    )
    sized = ContractRiskEngine().size(
        price=0.5, equity=100.0, cash=100.0, fee_model=ZeroFeeModel()
    )
    assert sized.approved is True


# ==========================================================================
# The model cannot reach an order
# ==========================================================================
def test_the_analyst_schema_has_no_execution_field() -> None:
    from ai_trader.ai.skeptic import SKEPTIC_SCHEMA

    for forbidden in ("ticker", "size", "contracts", "price", "venue", "order", "broker"):
        assert forbidden not in SKEPTIC_SCHEMA["properties"]
    assert SKEPTIC_SCHEMA["additionalProperties"] is False


def test_the_legacy_decision_schema_also_has_no_execution_field() -> None:
    from ai_trader.ai.validate import GROK_JSON_SCHEMA

    assert set(GROK_JSON_SCHEMA["properties"]) == {"action", "confidence", "reasoning"}
    assert GROK_JSON_SCHEMA["additionalProperties"] is False


def test_no_ai_module_imports_a_broker_or_a_ledger() -> None:
    """The reasoning layer must not be able to touch execution or accounting."""
    forbidden = {"broker", "ledger", "contracts.ledger", "paper.simulator"}
    for path in sorted((PYTHON_ROOT / "ai").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            module = ""
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
            elif isinstance(node, ast.Import):
                module = ",".join(a.name for a in node.names)
            for token in forbidden:
                assert token not in module, f"{path.name} imports {module}"


# ==========================================================================
# HTTP boundary
# ==========================================================================
MUTATING_ROUTES = (
    "src/routes/api/agent.cycle.ts",
    "src/routes/api/paper-session.start.ts",
    "src/routes/api/paper-session.stop.ts",
    "src/routes/api/paper-session.grok.ts",
)


def test_every_mutating_route_requires_authorisation() -> None:
    for relative in MUTATING_ROUTES:
        body = (ROOT / relative).read_text(encoding="utf-8")
        assert "authoriseMutation" in body, relative
        assert "unauthorised" in body, relative


def test_no_route_handler_is_missing_its_gate() -> None:
    """Any POST handler that is not on the known list is a review failure."""
    routes = sorted((ROOT / "src" / "routes" / "api").glob("*.ts"))
    for path in routes:
        body = path.read_text(encoding="utf-8")
        if "POST:" not in body:
            continue
        relative = str(path.relative_to(ROOT))
        assert relative in MUTATING_ROUTES, f"{relative} has a POST handler and no known gate"
        assert "authoriseMutation" in body, relative


def test_secrets_are_never_exposed_to_the_browser() -> None:
    for path in _ts_files():
        body = path.read_text(encoding="utf-8")
        assert "VITE_XAI" not in body, path
        assert "VITE_ALPACA" not in body, path
        assert "VITE_AI_TRADER_API_TOKEN" not in body, path
    # The token is read on the server only.
    auth = (ROOT / "src" / "lib" / "api-auth.server.ts").read_text(encoding="utf-8")
    assert "process.env.AI_TRADER_API_TOKEN" in auth
    assert auth.count("AI_TRADER_API_TOKEN") >= 1


def test_an_unconfigured_deployment_refuses_mutations_rather_than_allowing_them() -> None:
    auth = (ROOT / "src" / "lib" / "api-auth.server.ts").read_text(encoding="utf-8")
    assert "if (!expected)" in auth
    assert "503" in auth, "no token configured must refuse, not allow"


# ==========================================================================
# The deployed worker
# ==========================================================================
def test_the_worker_gates_every_mutating_route() -> None:
    """The worker enforces its own token.

    The frontend attaching a token is a convenience; the worker refusing
    without one is the security property, because the worker is the thing with
    a public URL.
    """
    body = (PYTHON_ROOT / "http_api.py").read_text(encoding="utf-8")
    assert "compare_digest" in body, "token comparison must be constant time"
    assert "MUTATING_COMMANDS" in body, "mutations must be recognised as such"
    assert "_configured_token()" in body
    # No token configured must refuse rather than allow.
    assert "503" in body and "401" in body


def test_the_worker_never_hands_the_browser_a_key() -> None:
    """No CORS on the worker, so the browser cannot be the one holding a token."""
    body = (PYTHON_ROOT / "http_api.py").read_text(encoding="utf-8")
    assert "CORSMiddleware" not in body, (
        "adding CORS would mean the browser calls the worker directly, which "
        "means the browser holds the control token"
    )


def test_the_worker_client_lives_on_the_server_only() -> None:
    """A `.server.ts` suffix is what keeps the token out of the client bundle."""
    remote = ROOT / "src" / "lib" / "worker-remote.server.ts"
    assert remote.exists(), "the worker client must exist"
    body = remote.read_text(encoding="utf-8")
    assert "process.env.AI_TRADER_API_TOKEN" in body
    # Reads, not prose: the file explains the `VITE_` rule in a comment.
    assert "import.meta.env.VITE_" not in body
    assert "process.env.VITE_" not in body
    # No component may import it: that would pull a server module, and the
    # token read inside it, toward the browser.
    for path in _ts_files():
        if path.name.endswith(".server.ts") or path.name.endswith(".server.test.ts"):
            continue
        if "src/routes/api/" in path.as_posix():
            continue
        text = path.read_text(encoding="utf-8")
        assert "worker-remote.server" not in text, path


def test_the_worker_url_is_never_a_browser_variable() -> None:
    """`VITE_`-prefixed values are inlined into the bundle."""
    for path in _ts_files():
        body = path.read_text(encoding="utf-8")
        assert "VITE_PAPER_WORKER_URL" not in body, path
        assert "VITE_WORKER_URL" not in body, path


def test_the_http_worker_exposes_no_live_trading_route() -> None:
    body = (PYTHON_ROOT / "http_api.py").read_text(encoding="utf-8")
    for forbidden in ("/api/live", "live_order", "submit_order", "place_order"):
        assert forbidden not in body, forbidden
    assert LIVE_TRADING_ALLOWED is False


def test_the_public_config_view_never_leaks_a_secret() -> None:
    from ai_trader.config import Settings

    settings = Settings()
    view = settings.public_view()
    blob = str(view).lower()
    for forbidden in ("secret", "bearer", "xai-", "sk-"):
        assert forbidden not in blob, forbidden
    # Presence flags only.
    assert view["api_token_configured"] in (True, False)


# ==========================================================================
# Concurrency
# ==========================================================================
def test_double_start_does_not_corrupt_a_session() -> None:
    from ai_trader.session.config import PaperSessionConfig
    from ai_trader.session.runner import PaperSession

    session = PaperSession(
        PaperSessionConfig(symbol="SIM-UP", bars=24, source="simulated", continuous=False)
    )
    results: list[dict] = []
    errors: list[BaseException] = []

    def start() -> None:
        try:
            results.append(session.start())
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=start) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors, errors
    assert len(results) == 4
    for report in results:
        assert report["live"] is False
        assert report["broker_submit_calls"] == 0
        assert report["look_ahead"] is False


def test_stop_during_a_run_leaves_a_consistent_report() -> None:
    from ai_trader.session.config import PaperSessionConfig
    from ai_trader.session.runner import PaperSession

    session = PaperSession(
        PaperSessionConfig(symbol="SIM-UP", bars=60, source="simulated", continuous=False)
    )
    stopper = threading.Thread(target=session.stop)
    stopper.start()
    report = session.start()
    stopper.join(timeout=10)
    assert report["look_ahead"] is False
    assert report["broker_submit_calls"] == 0


def test_the_record_store_survives_concurrent_writes(tmp_path) -> None:
    from ai_trader.db.records import RecordStore
    from ai_trader.db.schema import initialise_database

    store = RecordStore(initialise_database(tmp_path / "c.db"))
    errors: list[BaseException] = []

    def write(index: int) -> None:
        try:
            for i in range(25):
                store.record_decision(
                    {"cycle_id": f"c{index}", "final_action": "HOLD", "ticker": f"T{i}"}
                )
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=write, args=(i,)) for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors, errors
    assert store.decision_counts()["TOTAL"] == 100


# ==========================================================================
# Contract ladder
# ==========================================================================
def test_the_cpi_ladder_states_its_resolution_rule() -> None:
    from ai_trader.events.base import ScheduledRelease

    release = ScheduledRelease(
        release_key="CPI:2026-03", series_key="BLS:X", source="BLS",
        label="CPI", scheduled_at="2026-04-12T13:30:00+00:00", period="2026-03",
    )
    rows = cpi_contracts(release)
    assert len(rows) == 5
    for contract in rows:
        assert contract.event_key == "CPI:2026-03"
        assert contract.strike is not None
        assert "year-on-year" in contract.settlement_rules
        assert contract.quote_currency == "USD"
    assert len({c.ticker for c in rows}) == 5


def test_registering_the_ladder_gives_the_market_contracts() -> None:
    from ai_trader.clock import FrozenClock
    from ai_trader.events.bls import BLSCPISource
    from ai_trader.markets.paper import PaperPredictionMarket

    clock = FrozenClock("2026-04-14T14:00:00+00:00")
    market = PaperPredictionMarket(clock=clock)
    source = BLSCPISource(http_client=None, clock=clock)
    count = register_cpi_ladder(market, source, limit=2)
    assert count == 10
    assert len(market.discover()) == 10


def test_a_contract_with_no_book_is_refused_not_guessed() -> None:
    """With no venue connected, the agent holds rather than inventing a price."""
    from ai_trader.clock import FrozenClock
    from ai_trader.markets.base import MarketDataError
    from ai_trader.markets.paper import PaperPredictionMarket
    from ai_trader.events.base import ScheduledRelease

    market = PaperPredictionMarket(clock=FrozenClock("2026-04-14T14:00:00+00:00"))
    release = ScheduledRelease(
        release_key="CPI:2026-03", series_key="BLS:X", source="BLS",
        label="CPI", scheduled_at="2026-04-12T13:30:00+00:00", period="2026-03",
    )
    for contract in cpi_contracts(release):
        market.register(contract)
    with pytest.raises(MarketDataError):
        market.orderbook(cpi_contracts(release)[0].ticker)
