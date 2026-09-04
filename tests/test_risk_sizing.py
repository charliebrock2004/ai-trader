from __future__ import annotations

from ai_trader.instruments import instrument_for
from ai_trader.risk.engine import FOUNDATION_REJECT_REASON, RiskEngine
from ai_trader.risk.limits import RiskLimits
from ai_trader.types import Action, Decision


def test_size_long_is_bound_by_concentration_not_just_risk_budget() -> None:
    """A £2 risk budget must not buy a £100 position.

    Sizing purely on the risk budget produces near-full-account notional
    whenever the stop is tight. The concentration cap is what stops that.
    """
    engine = RiskEngine(allow_orders=False)
    sized = engine.size_long(price=100.0, equity=100.0, cash=100.0)
    assert sized.approved is True
    assert sized.max_risk == 2.0
    assert sized.stop_distance == 2.0
    assert sized.stop_price == 98.0
    # Derived from the limit, not pinned: the take-profit multiple is a
    # strategy-economics choice that may change, while "target sits at rr times
    # the stop above entry" is the invariant worth asserting.
    expected_target = 100.0 + sized.stop_distance * RiskLimits().take_profit_rr
    assert sized.take_profit_price == expected_target
    assert sized.risk_reward == RiskLimits().take_profit_rr
    # 25% of £100 equity at £100/unit.
    assert sized.binding_constraint == "concentration"
    assert sized.proposed_qty == 0.25
    assert sized.proposed_notional == 25.0
    assert sized.max_loss_at_stop == 0.5
    # Worst case assumes the exit is 1.5x the stop distance away.
    assert sized.worst_case_loss == 0.75


def test_worst_case_loss_never_exceeds_the_risk_budget() -> None:
    """Whichever cap binds, the gap-adjusted loss stays inside the budget."""
    engine = RiskEngine(allow_orders=False)
    for price in (0.5, 5.0, 100.0, 4_000.0, 90_000.0):
        for equity in (25.0, 100.0, 1_000.0):
            sized = engine.size_long(price=price, equity=equity, cash=equity)
            if not sized.approved:
                continue
            assert sized.worst_case_loss <= engine.limits.risk_budget(equity) + 0.01, (
                price,
                equity,
                sized.to_dict(),
            )


def test_risk_budget_binds_when_the_stop_is_wide() -> None:
    limits = RiskLimits(default_stop_pct=0.40, max_position_notional_pct=1.0)
    engine = RiskEngine(allow_orders=False, limits=limits)
    sized = engine.size_long(price=100.0, equity=100.0, cash=100.0)
    assert sized.approved is True
    assert sized.binding_constraint == "risk_budget"
    # stop distance 40, worst case 60 per unit, budget 2 -> 0.0333 units.
    assert sized.proposed_qty == 0.0333
    assert sized.worst_case_loss <= 2.0


def test_cash_binds_when_equity_exceeds_available_cash() -> None:
    engine = RiskEngine(allow_orders=False, limits=RiskLimits(max_position_notional_pct=1.0))
    sized = engine.size_long(price=100.0, equity=1000.0, cash=10.0)
    assert sized.approved is True
    assert sized.binding_constraint == "cash"
    assert sized.proposed_notional <= 10.0


def test_survival_multiplier_can_only_shrink_a_position() -> None:
    engine = RiskEngine(allow_orders=False)
    full = engine.size_long(price=100.0, equity=100.0, cash=100.0, risk_multiplier=1.0)
    half = engine.size_long(price=100.0, equity=100.0, cash=100.0, risk_multiplier=0.5)
    # A multiplier above 1.0 must be clamped, never honoured.
    inflated = engine.size_long(price=100.0, equity=100.0, cash=100.0, risk_multiplier=4.0)
    assert half.proposed_qty < full.proposed_qty
    assert inflated.proposed_qty == full.proposed_qty
    assert inflated.risk_multiplier == 1.0


def test_sizing_uses_fx_so_a_usd_instrument_cannot_spend_pounds_as_dollars() -> None:
    """£100 buying a USD instrument must not behave as if it were $100."""
    engine = RiskEngine(allow_orders=False)
    spec = instrument_for("BTC-USD")
    # $1.25 to the pound: one pound of equity buys 1.25 dollars of instrument.
    gbp_per_usd = 0.80
    sized = engine.size_long(
        price=1000.0, equity=100.0, cash=100.0, instrument=spec, fx_rate=gbp_per_usd
    )
    assert sized.approved is True
    assert sized.quote_currency == "USD"
    assert sized.fx_rate == gbp_per_usd
    # Notional is reported in GBP and respects the 25% concentration cap.
    assert sized.proposed_notional <= 25.01
    # The same nominal price with no conversion would buy strictly less.
    unconverted = engine.size_long(price=1000.0, equity=100.0, cash=100.0, instrument=spec)
    assert sized.proposed_qty > unconverted.proposed_qty


def test_zero_or_negative_fx_rate_is_refused() -> None:
    engine = RiskEngine(allow_orders=False)
    for bad in (0.0, -1.0):
        sized = engine.size_long(price=100.0, equity=100.0, cash=100.0, fx_rate=bad)
        assert sized.approved is False
        assert "FX" in sized.reason


def test_terminated_agent_is_refused_before_any_other_check() -> None:
    engine = RiskEngine(allow_orders=False)
    account = {"cash": 100, "account_equity": 100, "starting_cash": 100, "day_start_equity": 100}
    verdict = engine.review_paper(
        "BUY",
        price=100,
        account=account,
        open_positions=0,
        trades_today=0,
        daily_pnl=0,
        has_position=False,
        halted=False,
        kill_switch=False,
        terminated=True,
    )
    assert verdict.approved is False
    assert "TERMINATED" in verdict.reason


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
