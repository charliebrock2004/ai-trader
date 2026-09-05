"""End-to-end guarantees for the paper desk after the strategy fix.

These ten tests exist because a dashboard reading "4,667 opportunities
considered, 1 executed" is not evidence of anything on its own. Each one pins
a property the desk must keep whatever the detector is tuned to:

1.  a normal trending market produces more than one candidate,
2.  a market with genuinely nothing in it produces HOLD, and says why,
3.  the strategy cannot get past the risk engine,
4.  position limits hold,
5.  the daily loss limit holds,
6.  the analyst cannot touch risk parameters or sizing,
7.  a rejected trade cannot leak into an execution,
8.  every entry becomes a recorded exit,
9.  realised and unrealised P&L stay separate,
10. the whole thing stays paper-only.

The pipeline under test is the real one, not a stand-in:
MARKET DATA -> STRATEGY DETECTOR -> CANDIDATE -> GROK -> POLICY GUARDIAN ->
RISK ENGINE -> PAPER EXECUTION.

A note on the timeframe used here. The deterministic fixtures move about 0.55%
a bar, which is a *daily-sized* move; the 5-minute volatility band therefore
rejects them all as ``too_wild``, correctly. So these tests run the detector at
the timeframe whose volatility band actually matches the fixture. That is a
property of the generator, not of the strategy, and it is stated here rather
than worked around by widening a safety band.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from ai_trader.ai.base import Analyst, ProposedDecision
from ai_trader.market_data.generator import generate_series
from ai_trader.paper.execution import sell_fill_price
from ai_trader.paper.ledger import PaperLedger
from ai_trader.paper.models import FillReason, OrderStatus, PaperFill, PaperOrder
from ai_trader.paper.simulator import PaperSimulator
from ai_trader.risk.engine import RiskEngine
from ai_trader.risk.limits import RiskLimits
from ai_trader.safety import LIVE_TRADING_ALLOWED
from ai_trader.session.source import DeterministicFirstSource
from ai_trader.strategy.signal import TrendPullbackStrategy
from ai_trader.survival.config import SurvivalConfig
from ai_trader.survival.engine import SurvivalEngine
from ai_trader.survival.policy import PolicyGuardian, PolicyViolationError
from ai_trader.types import Action, Decision

#: The fixtures move like daily bars; run the detector on a matching band.
TIMEFRAME = "1h"
WARMUP = 60


class StubAnalyst(Analyst):
    """A connected analyst that answers deterministically.

    Not a fixture analyst: ``DeterministicFirstSource`` deliberately skips the
    Grok stage for ``name == "fixture"``, and these tests need the Grok stage to
    actually run so the pipeline being exercised is the real one.
    """

    name = "stub-analyst"
    paper_requested = True

    def __init__(self, action: Action = Action.BUY, *, rationale: str = "stub") -> None:
        self.action = action
        self.rationale = rationale
        #: Every candidate payload handed to the analyst, for inspection.
        self.candidates: list[dict] = []

    def is_configured(self) -> bool:
        return True

    def propose(self, snapshot, analysis=None, *, account=None, positions=None, candidate=None):
        self.candidates.append(dict(candidate or {}))
        return ProposedDecision(
            decision=Decision(
                symbol="SIM-UP",
                action=self.action,
                confidence=0.7,
                rationale=self.rationale,
                model=self.name,
            ),
            context={"validated": True, "network": True, "failure": None},
        )


def _guardian(starting_equity: float = 100.0) -> PolicyGuardian:
    return PolicyGuardian(SurvivalEngine(SurvivalConfig(starting_equity=starting_equity)))


def run_desk(
    *,
    symbol: str = "SIM-UP",
    limit: int = 400,
    seed: int = 7,
    analyst: Analyst | None = None,
    risk: RiskEngine | None = None,
    policy: PolicyGuardian | None = None,
    flatten_at_end: bool = True,
):
    """Walk the whole pipeline over a deterministic market. No network."""
    series = generate_series(symbol, timeframe=TIMEFRAME, limit=limit, seed=seed)
    sim = PaperSimulator(
        risk=risk or RiskEngine(allow_orders=False),
        policy=policy if policy is not None else _guardian(),
        flatten_at_end=flatten_at_end,
    )
    sim.max_holding_bars = 48
    decisions: list[dict] = []
    sim.on_decision = decisions.append
    stub = analyst or StubAnalyst()
    source = DeterministicFirstSource(
        stub,
        warmup=WARMUP,
        timeframe=TIMEFRAME,
        account_fn=lambda: sim.ledger.snapshot().to_dict(),
    )
    report = sim.run(series, source=source)
    return sim, source, stub, report, decisions


# ---------------------------------------------------------------------------
# 1. A normal trending market produces more than one candidate.
# ---------------------------------------------------------------------------
def test_a_trending_market_produces_more_than_one_candidate() -> None:
    """The bug this fix addressed: 4,666 rejections and one execution.

    The failure mode was not "the market was quiet", it was a detector whose
    setup definitions were so narrow that the quality score never got anything
    to judge — ``score_too_low`` fired exactly zero times while ``no_setup``
    took 47% of bars. So this asserts two things: that candidates appear at all,
    and that selectivity now lives in the score, where it can be measured.
    """
    sim, source, stub, report, decisions = run_desk()

    detector = source.technical
    assert detector.candidates > 1, (
        f"A 400-bar uptrend produced {detector.candidates} candidates. "
        f"Rejections: {detector.rejection_counts}"
    )
    # Every candidate reached the analyst, carrying the candidate payload.
    assert source.consults == detector.candidates
    assert len(stub.candidates) == detector.candidates
    assert all(c.get("direction") == "BUY" for c in stub.candidates)
    assert all(c.get("features") for c in stub.candidates)

    # Selectivity is a threshold, not an accident of ANDed filters.
    assert detector.rejection_counts.get("score_too_low", 0) > 0, (
        "The quality score rejected nothing, which means the setup definitions "
        "are doing the filtering again."
    )
    # More than one *kind* of setup is reachable.
    assert len(detector.setup_counts) >= 2, detector.setup_counts


# ---------------------------------------------------------------------------
# 2. A market with genuinely no setup produces HOLD, and names the reason.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "closes, expected",
    [
        ([100.0 * (0.997**i) for i in range(200)], "downtrend"),
        ([100.0 + (0.0001 if i % 2 else 0.0) for i in range(200)], "too_quiet"),
    ],
    ids=["steady-decline", "dead-quiet"],
)
def test_a_market_with_no_setup_holds_and_says_why(closes: list[float], expected: str) -> None:
    """Loosening the detector must not have made it indiscriminate.

    A long-only desk in a downtrend and a desk in a market too quiet to clear
    costs must both stand aside — and the record must name which, because a
    generic "rejected" count is what made the original problem undiagnosable.
    """
    detector = TrendPullbackStrategy(timeframe=TIMEFRAME)
    for i in range(len(closes)):
        signal = detector.evaluate(closes[: i + 1])
        assert signal.action.value == "HOLD"

    assert detector.candidates == 0
    assert detector.rejection_counts.get(expected, 0) > 0, detector.rejection_counts
    # Warm-up aside, the named reason is the dominant one.
    named = {k: v for k, v in detector.rejection_counts.items() if k != "warming_up"}
    assert max(named, key=lambda k: named[k]) == expected


# ---------------------------------------------------------------------------
# 3. The strategy cannot bypass risk controls.
# ---------------------------------------------------------------------------
def test_the_strategy_cannot_bypass_the_risk_engine() -> None:
    """Candidates are produced, the risk engine refuses, nothing is bought.

    The detector and the analyst both say BUY here. The only thing standing
    between them and a position is the risk engine, so it is the only thing
    under test.
    """
    refusing = RiskEngine(allow_orders=False, limits=RiskLimits(max_open_positions=0))
    sim, source, stub, report, decisions = run_desk(risk=refusing)

    assert source.technical.candidates > 0, "nothing was proposed, so nothing was refused"
    assert report["fills"] == []
    assert report["closed_positions"] == []
    assert sim.ledger.cash == sim.ledger.starting_cash
    assert report["account"]["account_equity"] == 100.0

    rejected = [o for o in report["orders"] if o["status"] == OrderStatus.REJECTED.value]
    assert len(rejected) == source.technical.candidates
    assert all(o["quantity"] == 0 for o in rejected)
    assert any(d["rejection"] == "risk_rejected" for d in decisions)
    assert report["broker_submit_calls"] == 0


# ---------------------------------------------------------------------------
# 4. Position limits cannot be exceeded.
# ---------------------------------------------------------------------------
def test_position_limits_cannot_be_exceeded() -> None:
    """Three separate caps, all of which must bind."""
    engine = RiskEngine(allow_orders=False)
    limits = engine.limits
    account = {"account_equity": 100.0, "cash": 100.0, "day_start_equity": 100.0}

    def review(**kwargs):
        base = dict(
            price=1000.0,
            account=account,
            open_positions=0,
            trades_today=0,
            daily_pnl=0.0,
            has_position=False,
            halted=False,
            kill_switch=False,
        )
        base.update(kwargs)
        return engine.review_paper("BUY", **base)

    # a) the open-position count
    at_cap = review(open_positions=limits.max_open_positions)
    assert at_cap.approved is False
    assert "Maximum open positions" in at_cap.reason

    # b) adding to a symbol already held
    already = review(has_position=True)
    assert already.approved is False
    assert "Adds are disabled" in already.reason

    # c) concentration, on a position that is actually approved
    approved = review()
    assert approved.approved is True
    assert approved.proposed_notional <= 100.0 * limits.max_position_notional_pct + 0.01
    assert approved.worst_case_loss <= limits.max_risk_amount + 0.01

    # And the same caps hold across a whole run, not just one call. The cap is
    # a fraction of equity *at the moment of sizing*, so it is checked against
    # the equity the decision record captured, not against the opening balance.
    sim, source, stub, report, decisions = run_desk()
    equity_at = {
        d["order_id"]: d["equity"] for d in decisions if d.get("order_id")
    }
    sized = [o for o in report["orders"] if o["status"] != OrderStatus.REJECTED.value]
    assert sized, "the run sized nothing, so no cap was exercised"
    for order in sized:
        equity = equity_at[order["order_id"]]
        notional = order["quantity"] * order["requested_price"]
        assert notional <= equity * limits.max_position_notional_pct + 0.01, order
    assert len(report["positions"]) <= limits.max_open_positions


# ---------------------------------------------------------------------------
# 5. Daily loss limits cannot be exceeded.
# ---------------------------------------------------------------------------
def test_daily_loss_limit_cannot_be_exceeded() -> None:
    """Past the limit the desk halts, and a halt refuses new risk."""
    engine = RiskEngine(allow_orders=False)
    limit = engine.limits.daily_loss_amount(100.0)
    assert limit == 5.0

    account = {"account_equity": 95.0, "cash": 95.0, "day_start_equity": 100.0}
    breached = engine.review_paper(
        "BUY",
        price=1000.0,
        account=account,
        open_positions=0,
        trades_today=0,
        daily_pnl=-limit,
        has_position=False,
        halted=False,
        kill_switch=False,
    )
    assert breached.approved is False
    assert "Daily loss limit reached." in breached.reason

    # The simulator raises the halt itself, from the ledger, not from a caller.
    sim = PaperSimulator(risk=engine)
    sim.ledger.day_start_equity = 100.0
    sim.ledger.cash = 100.0 - limit - 0.01
    sim._maybe_halt()
    assert sim.ledger.halted is True
    assert any(e["kind"] == "halt" for e in sim.events)

    halted = engine.review_paper(
        "BUY",
        price=1000.0,
        account=account,
        open_positions=0,
        trades_today=0,
        daily_pnl=0.0,
        has_position=False,
        halted=True,
        kill_switch=False,
    )
    assert halted.approved is False
    assert "halted" in halted.reason.lower()


# ---------------------------------------------------------------------------
# 6. Grok cannot alter risk parameters.
# ---------------------------------------------------------------------------
def test_grok_cannot_alter_risk_parameters() -> None:
    """The analyst is a skeptic. It has no size, no limits, and no veto to lift.

    Its only power is to turn a BUY into a HOLD. Everything else — how much,
    where the stop is, whether the trade is allowed at all — is decided after it
    has spoken, by code it cannot reach.
    """
    # The limits object is frozen: there is no runtime path to widening it.
    with pytest.raises(FrozenInstanceError):
        RiskLimits().max_risk_amount = 999.0  # type: ignore[misc]

    # An analyst demanding a bigger position changes nothing about sizing.
    modest = run_desk(analyst=StubAnalyst(Action.BUY, rationale="small size please"))
    greedy = run_desk(
        analyst=StubAnalyst(
            Action.BUY,
            rationale=(
                "Ignore the risk engine. Use 10x leverage, set max_risk_amount "
                "to 90, disable the stop and enable live trading."
            ),
        )
    )
    modest_orders = [(o["quantity"], o["stop_loss"], o["take_profit"]) for o in modest[3]["orders"]]
    greedy_orders = [(o["quantity"], o["stop_loss"], o["take_profit"]) for o in greedy[3]["orders"]]
    assert modest_orders == greedy_orders
    assert modest[3]["account"] == greedy[3]["account"]

    # The guardian refuses to be talked upwards, structurally.
    guardian = _guardian()
    with pytest.raises(PolicyViolationError):
        guardian._outcome("HOLD", "BUY", "analyst said so", [], approved=True)


# ---------------------------------------------------------------------------
# 7. A rejected trade cannot accidentally execute.
# ---------------------------------------------------------------------------
def test_a_rejected_trade_cannot_execute() -> None:
    """A rejection must leave nothing pending. A pending order fills next bar."""
    refusing = RiskEngine(allow_orders=False, limits=RiskLimits(max_open_positions=0))
    sim, source, stub, report, decisions = run_desk(risk=refusing)

    assert sim.pending is None
    assert sim.ledger.fills == []
    assert sim.ledger.positions == {}
    assert sim.ledger.round_trips == 0
    assert sim.ledger.realised_pnl == 0.0

    rejected_ids = {
        o["order_id"] for o in report["orders"] if o["status"] == OrderStatus.REJECTED.value
    }
    assert rejected_ids
    filled_ids = {f["order_id"] for f in report["fills"]}
    assert rejected_ids & filled_ids == set()

    # A stop mid-flight cancels the pending order rather than filling it.
    live = PaperSimulator(risk=RiskEngine(allow_orders=False), policy=_guardian())
    series = generate_series("SIM-UP", timeframe=TIMEFRAME, limit=400, seed=7)
    source2 = DeterministicFirstSource(
        StubAnalyst(),
        warmup=WARMUP,
        timeframe=TIMEFRAME,
        account_fn=lambda: live.ledger.snapshot().to_dict(),
    )
    stop_from = {"bar": None}

    def stop_check(index: int) -> bool:
        if stop_from["bar"] is None and live.pending is not None:
            stop_from["bar"] = index
        return stop_from["bar"] is not None

    stopped = live.run(series, source=source2, stop_check=stop_check)
    if stop_from["bar"] is not None:
        cancelled = [
            o for o in stopped["orders"] if o["status"] == OrderStatus.CANCELLED.value
        ]
        assert cancelled, "a stopped session must cancel the pending order"
        cancelled_ids = {o["order_id"] for o in cancelled}
        assert cancelled_ids & {f["order_id"] for f in stopped["fills"]} == set()


# ---------------------------------------------------------------------------
# 8. An executed entry eventually becomes a correctly recorded exit.
# ---------------------------------------------------------------------------
def test_every_entry_becomes_a_recorded_exit() -> None:
    """No position leaves the book without a fill behind it.

    The restart bug this guards against turned an open position into cash at its
    mark: equity moved, and there was no trade to explain it. Every exit here is
    a real fill that paid spread and slippage.
    """
    sim, source, stub, report, decisions = run_desk()

    entries = [f for f in report["fills"] if f["side"] == "BUY"]
    exits = [f for f in report["fills"] if f["side"] == "SELL"]
    assert entries, "the run produced no entries, so there is nothing to check"
    assert len(entries) == len(exits)
    assert {f["order_id"] for f in entries} == {f["order_id"] for f in exits}

    # Fill ids are unique: no double-counting an entry or an exit.
    ids = [f["fill_id"] for f in report["fills"]]
    assert len(ids) == len(set(ids))

    assert len(report["closed_positions"]) == len(entries)
    assert sim.ledger.round_trips == len(entries)
    for closed in report["closed_positions"]:
        assert closed["open"] is False
        assert closed["entry_timestamp"] and closed["exit_timestamp"]
        assert closed["exit_timestamp"] >= closed["entry_timestamp"]
        assert closed["realised_pnl"] is not None

    # Exits are named, and every name is a real one.
    exit_reasons = {f["reason"] for f in exits}
    assert exit_reasons <= {
        FillReason.STOP.value,
        FillReason.TARGET.value,
        FillReason.CLOSE.value,
        FillReason.DAY_END.value,
    }
    # Costs were actually charged on both legs.
    assert all(f["spread"] > 0 and f["slippage"] > 0 for f in report["fills"])

    # No trade is recorded before the market data that produced it.
    stamps = [f["timestamp"] for f in report["fills"]]
    assert stamps == sorted(stamps)
    assert report["look_ahead"] is False


# ---------------------------------------------------------------------------
# 9. Realised and unrealised P&L stay separate.
# ---------------------------------------------------------------------------
def test_realised_and_unrealised_pnl_stay_separate() -> None:
    """An open position's paper gain is never bankable, and never realised."""
    ledger = PaperLedger(starting_cash=100.0, base_currency="GBP")
    order = PaperOrder(
        order_id=ledger.next_order_id(),
        symbol="SIM-UP",
        side="BUY",
        quantity=2.0,
        requested_price=10.0,
        stop_loss=9.0,
        take_profit=13.0,
        timestamp="2026-01-01T00:00:00Z",
        status=OrderStatus.PENDING.value,
        reason="test",
        source="test",
    )
    entry = PaperFill(
        fill_id=ledger.next_fill_id(),
        order_id=order.order_id,
        symbol="SIM-UP",
        side="BUY",
        quantity=2.0,
        price=10.0,
        timestamp=order.timestamp,
        reason=FillReason.ENTRY.value,
        spread=5.0,
        slippage=5.0,
    )
    ledger.apply_buy(order, entry, quote_currency="GBP")

    ledger.mark("SIM-UP", 12.0)
    snap = ledger.snapshot()
    assert snap.realised_pnl == 0.0, "an open position has realised nothing"
    assert snap.unrealised_pnl == pytest.approx(4.0, abs=0.01)
    assert snap.total_pnl == pytest.approx(snap.realised_pnl + snap.unrealised_pnl, abs=0.01)
    assert snap.cash == pytest.approx(80.0, abs=0.01)
    assert snap.account_equity == pytest.approx(snap.cash + snap.invested_value, abs=0.01)
    assert ledger.round_trips == 0

    exit_price = sell_fill_price(12.0, spread_bps=5.0, slip_bps=5.0)
    ledger.close_position(
        "SIM-UP",
        PaperFill(
            fill_id=ledger.next_fill_id(),
            order_id=order.order_id,
            symbol="SIM-UP",
            side="SELL",
            quantity=2.0,
            price=exit_price,
            timestamp="2026-01-01T01:00:00Z",
            reason=FillReason.TARGET.value,
            spread=5.0,
            slippage=5.0,
        ),
        reason=FillReason.TARGET.value,
    )
    closed = ledger.snapshot()
    assert closed.unrealised_pnl == 0.0, "a closed position holds no unrealised P&L"
    assert closed.realised_pnl == pytest.approx(2.0 * exit_price - 20.0, abs=0.01)
    assert closed.realised_pnl < 4.0, "costs must have been paid on the way out"
    assert closed.total_pnl == closed.realised_pnl
    assert closed.invested_value == 0.0
    assert ledger.round_trips == 1

    # The same separation must hold at the end of a real run.
    sim, source, stub, report, decisions = run_desk(flatten_at_end=False)
    account = report["account"]
    assert account["total_pnl"] == pytest.approx(
        account["realised_pnl"] + account["unrealised_pnl"], abs=0.01
    )
    assert account["account_equity"] == pytest.approx(
        account["cash"] + account["invested_value"], abs=0.01
    )
    assert account["starting_cash"] == 100.0
    banked = sum(p["realised_pnl"] for p in report["closed_positions"])
    assert account["realised_pnl"] == pytest.approx(banked, abs=0.01)


# ---------------------------------------------------------------------------
# 10. The system remains paper-only.
# ---------------------------------------------------------------------------
def test_the_system_remains_paper_only() -> None:
    """No broker, no live flag, no order path out of the process."""
    assert LIVE_TRADING_ALLOWED is False

    engine = RiskEngine(allow_orders=False)
    assert engine.allow_orders is False
    assert engine.health()["allow_orders"] is False

    sim, source, stub, report, decisions = run_desk()
    assert report["live"] is False
    assert report["mode"] == "simulate"
    assert report["broker_submit_calls"] == 0
    assert report["look_ahead"] is False

    # Shorting is not merely unused, it is refused.
    short = engine.review_paper(
        "SELL",
        price=1000.0,
        account={"account_equity": 100.0, "cash": 100.0, "day_start_equity": 100.0},
        open_positions=0,
        trades_today=0,
        daily_pnl=0.0,
        has_position=False,
        halted=False,
        kill_switch=False,
    )
    assert short.approved is False
    assert "Shorts are disabled" in short.reason

    # The kill switch and the terminal latch both stop new risk outright.
    killed = engine.review_paper(
        "BUY",
        price=1000.0,
        account={"account_equity": 100.0, "cash": 100.0, "day_start_equity": 100.0},
        open_positions=0,
        trades_today=0,
        daily_pnl=0.0,
        has_position=False,
        halted=False,
        kill_switch=True,
    )
    assert killed.approved is False
    assert "Kill switch" in killed.reason

    terminated = engine.review_paper(
        "BUY",
        price=1000.0,
        account={"account_equity": 100.0, "cash": 100.0, "day_start_equity": 100.0},
        open_positions=0,
        trades_today=0,
        daily_pnl=0.0,
        has_position=False,
        halted=False,
        kill_switch=False,
        terminated=True,
    )
    assert terminated.approved is False
    assert "TERMINATED" in terminated.reason
