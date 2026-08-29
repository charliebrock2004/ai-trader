from __future__ import annotations

from ai_trader.risk.engine import FOUNDATION_REJECT_REASON, RiskEngine
from ai_trader.risk.limits import RiskLimits
from ai_trader.types import Action, Decision


def test_size_long_two_percent_of_one_hundred() -> None:
    engine = RiskEngine(allow_orders=False)
    sized = engine.size_long(price=100.0, equity=100.0, cash=100.0)
    assert sized.approved is True
    assert sized.max_risk == 2.0
    assert sized.stop_distance == 2.0
    assert sized.proposed_qty == 1.0
    assert sized.proposed_notional == 100.0
    assert sized.max_loss_at_stop == 2.0
    assert sized.stop_price == 98.0
    assert sized.take_profit_price == 104.0
    assert sized.risk_reward == 2.0


def test_review_still_rejects_broker_orders() -> None:
    engine = RiskEngine(allow_orders=False)
    verdict = engine.review(
        Decision(symbol="SPY", action=Action.BUY, confidence=1, rationale="x", model="x")
    )
    assert verdict.approved is False
    assert verdict.reason == FOUNDATION_REJECT_REASON
    assert engine.allow_orders is False


def test_review_paper_rejects_hold_kill_halt_and_shorts() -> None:
    engine = RiskEngine()
    account = {"cash": 100, "account_equity": 100, "starting_cash": 100, "day_start_equity": 100}
    hold = engine.review_paper(
        "HOLD",
        price=100,
        account=account,
        open_positions=0,
        trades_today=0,
        daily_pnl=0,
        has_position=False,
        halted=False,
        kill_switch=False,
    )
    assert hold.approved is False
    killed = engine.review_paper(
        "BUY", price=100, account=account, open_positions=0, trades_today=0,
        daily_pnl=0, has_position=False, halted=False, kill_switch=True,
    )
    assert "Kill switch" in killed.reason
    halted = engine.review_paper(
        "BUY", price=100, account=account, open_positions=0, trades_today=0,
        daily_pnl=0, has_position=False, halted=True, kill_switch=False,
    )
    assert "halted" in halted.reason.lower()
    short = engine.review_paper(
        "SELL", price=100, account=account, open_positions=0, trades_today=0,
        daily_pnl=0, has_position=False, halted=False, kill_switch=False,
    )
    assert short.approved is False
    daily = engine.review_paper(
        "BUY", price=100, account=account, open_positions=0, trades_today=0,
        daily_pnl=-5.0, has_position=False, halted=False, kill_switch=False,
    )
    assert "Daily loss" in daily.reason
    many = engine.review_paper(
        "BUY", price=100, account=account, open_positions=0, trades_today=10,
        daily_pnl=0, has_position=False, halted=False, kill_switch=False,
    )
    assert "Maximum trades" in many.reason
    caps = engine.review_paper(
        "BUY", price=100, account=account, open_positions=2, trades_today=0,
        daily_pnl=0, has_position=False, halted=False, kill_switch=False,
    )
    assert "Maximum open positions" in caps.reason


def test_leverage_forbidden() -> None:
    engine = RiskEngine(limits=RiskLimits(leverage=1.0))
    sized = engine.size_long(price=100, equity=100, cash=100)
    assert sized.approved is False
    assert "Leverage" in sized.reason
