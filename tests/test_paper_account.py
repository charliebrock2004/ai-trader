from __future__ import annotations

from ai_trader.account.simulated import STARTING_CASH, SimulatedPaperAccount
from ai_trader.config import Settings
from ai_trader.db.repository import Repository
from ai_trader.kill_switch import KillSwitch
from ai_trader.pipeline.orchestrator import Orchestrator


def _initial_ok(state) -> None:
    assert state.currency == "GBP"
    assert state.starting_cash == 100.00
    assert state.cash == 100.00
    assert state.buying_power == 100.00
    assert state.account_equity == 100.00
    assert state.invested_value == 0.00
    assert state.realised_pnl == 0.00
    assert state.unrealised_pnl == 0.00
    assert state.total_pnl == 0.00
    assert list(state.positions) == []
    assert state.fill_count == 0
    assert state.source == "simulated-paper"
    assert state.to_dict()["live"] is False


def test_initial_account_is_one_hundred_pounds() -> None:
    account = SimulatedPaperAccount()
    assert STARTING_CASH == 100.00
    _initial_ok(account.snapshot())


def test_account_module_has_no_network_imports() -> None:
    import ai_trader.account.simulated as mod

    assert "httpx" not in mod.__dict__
    assert "requests" not in mod.__dict__
    assert "urllib" not in mod.__dict__
    assert "alpaca" not in mod.__dict__


def test_no_fill_or_live_conversion_methods() -> None:
    account = SimulatedPaperAccount()
    for name in ("apply_fill", "open_position", "close_position", "connect_live", "attach_broker"):
        assert not hasattr(account, name)


def test_dry_run_leaves_account_unchanged(isolated_env: object) -> None:
    settings = Settings()
    repo = Repository(settings.resolve_database_path())
    switch = KillSwitch(settings.resolve_kill_switch_path(), initially_engaged=True)
    switch.disengage("test")
    orch = Orchestrator(settings, repo, switch)

    seen: dict = {}
    original = orch.risk.review

    def wrapped(decision, **kwargs):
        seen["account"] = kwargs.get("account")
        seen["positions"] = kwargs.get("positions")
        return original(decision, **kwargs)

    orch.risk.review = wrapped  # type: ignore[method-assign]

    before = orch.paper_account.snapshot().to_dict()
    result = orch.dry_run(["SPY"])
    after = orch.paper_account.snapshot()

    assert result["ok"] is True
    assert result["orders_placed"] == 0
    assert result["fills"] == 0
    assert result["account_unchanged"] is True
    assert result["account"]["cash"] == 100.00
    assert result["account"]["fill_count"] == 0
    assert result["broker_submit_calls"] == 0
    assert repo.list_trades() == []
    assert repo.list_positions() == []
    _initial_ok(after)
    assert after.cash == before["cash"] == 100.00
    assert seen["account"]["cash"] == 100.00
    assert seen["positions"] == []
    stored = repo.latest_account()
    assert stored is not None
    assert stored["cash"] == 100.00
    assert stored["equity"] == 100.00
    repo.close()
