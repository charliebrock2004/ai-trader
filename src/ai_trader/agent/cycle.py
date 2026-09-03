"""One full agent cycle, end to end.

    calendar -> release -> verification -> deterministic probability -> edge
      -> opportunity ranking -> analyst review -> policy guardian
      -> contract risk -> paper fill -> ledger -> decision record

Every stage can only ever say "no" more firmly than the one before it. Nothing
downstream of the analyst can turn a PASS into a trade, and nothing downstream
of the guardian can enlarge what risk sized.

Every decision is recorded, including the ones where the agent did nothing. A
cycle that considers forty contracts and trades none of them leaves forty rows
explaining why.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from ai_trader.clock import Clock, default_clock, ensure_utc
from ai_trader.contracts.ledger import ContractLedger, ContractLedgerError
from ai_trader.contracts.risk import ContractRiskEngine
from ai_trader.edge.opportunity import Opportunity, OpportunityEngine
from ai_trader.edge.probability import (
    ProbabilityEstimate,
    probability_from_resolved_value,
)
from ai_trader.events.base import (
    EventDataError,
    ReleaseObservation,
    ReleaseStatus,
    ScheduledRelease,
)
from ai_trader.markets.base import Contract, MarketDataError, OrderBook
from ai_trader.survival.config import SurvivalState


@dataclass
class CycleReport:
    """What one cycle did, in the order it did it."""

    cycle_id: str
    started_at: str
    finished_at: Optional[str] = None
    survival_state: str = "HEALTHY"
    terminated: bool = False
    releases_checked: int = 0
    contracts_considered: int = 0
    opportunities: list[dict[str, Any]] = field(default_factory=list)
    shortlisted: list[str] = field(default_factory=list)
    analyst_calls: int = 0
    decisions: list[dict[str, Any]] = field(default_factory=list)
    orders: list[dict[str, Any]] = field(default_factory=list)
    settlements: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    equity_before: float = 0.0
    equity_after: float = 0.0

    @property
    def traded(self) -> int:
        return sum(1 for d in self.decisions if d.get("executed"))

    @property
    def rejected(self) -> int:
        return sum(1 for d in self.decisions if not d.get("executed"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "cycle_id": self.cycle_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "survival_state": self.survival_state,
            "terminated": self.terminated,
            "releases_checked": self.releases_checked,
            "contracts_considered": self.contracts_considered,
            "opportunities": self.opportunities,
            "shortlisted": self.shortlisted,
            "analyst_calls": self.analyst_calls,
            "decisions": self.decisions,
            "orders": self.orders,
            "settlements": self.settlements,
            "errors": self.errors,
            "equity_before": self.equity_before,
            "equity_after": self.equity_after,
            "traded": self.traded,
            "rejected": self.rejected,
            "live": False,
        }


class AgentCycle:
    """Runs the event-driven pipeline once. No network of its own."""

    def __init__(
        self,
        *,
        event_source: Any,
        market: Any,
        ledger: ContractLedger,
        risk: ContractRiskEngine,
        guardian: Any,
        survival: Any,
        store: Any,
        opportunities: Optional[OpportunityEngine] = None,
        analyst: Any = None,
        cost_ledger: Any = None,
        clock: Optional[Clock] = None,
        fx_rate: float = 1.0,
        quote_currency: str = "USD",
        require_analyst: bool = True,
    ) -> None:
        #: When True (the default) a missing or unconfigured analyst means HOLD.
        #: An adversarial review is part of the strategy, so losing it is a
        #: degraded system rather than a licence to trade unchallenged. Set it
        #: False only to measure deliberately what the analyst contributes.
        self.require_analyst = require_analyst
        self.event_source = event_source
        self.market = market
        self.ledger = ledger
        self.risk = risk
        self.guardian = guardian
        self.survival = survival
        self.store = store
        self.opportunities = opportunities or OpportunityEngine()
        self.analyst = analyst
        self.cost_ledger = cost_ledger
        self.clock = clock or default_clock()
        self.fx_rate = float(fx_rate)
        self.quote_currency = quote_currency
        self.ledger.set_fx(quote_currency, self.fx_rate)
        self._cycle_seq = 0

    def _next_cycle_id(self) -> str:
        """Derived from the clock and a counter, never from randomness.

        A random id would make two replays of the same tape differ in the one
        column that ties a run together, so tapes could not be diffed. Under a
        frozen clock this is fully reproducible; under the wall clock it still
        separates cycles.
        """
        self._cycle_seq += 1
        stamp = ensure_utc(self.clock.now()).strftime("%Y%m%dT%H%M%S")
        return f"{stamp}-{self._cycle_seq:04d}"

    # ------------------------------------------------------------------
    def run(self, *, cycle_id: Optional[str] = None) -> CycleReport:
        now = ensure_utc(self.clock.now())
        report = CycleReport(
            cycle_id=cycle_id or self._next_cycle_id(),
            started_at=self.clock.now_iso(),
            equity_before=self.ledger.equity(),
        )
        self.ledger.roll_day(report.started_at)

        # Survival first. A terminated agent does nothing at all.
        state = self.survival.observe(self.ledger.equity(), reason="cycle start")
        report.survival_state = state.value
        if state is SurvivalState.TERMINAL:
            report.terminated = True
            report.finished_at = self.clock.now_iso()
            report.equity_after = report.equity_before
            self._record_hold(
                report,
                ticker=None,
                stage="terminal",
                reason="Agent is TERMINATED. No cycle was run.",
            )
            return report

        # Settle anything that has resolved before looking for new risk.
        self._settle_resolved(report, now)

        candidates = self._collect(report, now)
        report.contracts_considered = len(candidates)

        for opportunity in candidates:
            self._persist_opportunity(report, opportunity)

        shortlist = self.opportunities.shortlist(candidates)
        report.shortlisted = [o.ticker for o in shortlist]

        # Everything filtered out is still a recorded decision.
        for opportunity in candidates:
            if opportunity.selected:
                continue
            self._record_hold(
                report,
                ticker=opportunity.ticker,
                stage="opportunity",
                reason=opportunity.reject_reason or "Filtered out.",
                opportunity=opportunity,
            )

        for opportunity in shortlist:
            self._consider(report, opportunity)

        report.equity_after = self.ledger.equity()
        self.survival.observe(report.equity_after, reason="cycle end")
        report.survival_state = self.survival.state.value
        report.finished_at = self.clock.now_iso()
        return report

    # ------------------------------------------------------------------
    def _collect(self, report: CycleReport, now: datetime) -> list[Opportunity]:
        """Walk the calendar, read releases, price every matching contract."""
        try:
            releases = self.event_source.calendar(limit=6)
        except Exception as exc:  # noqa: BLE001 — a broken calendar is not a trade
            report.errors.append(f"Calendar unavailable: {exc}")
            return []

        found: list[Opportunity] = []
        for release in releases:
            report.releases_checked += 1
            observation = self._observe(report, release)
            if observation is None:
                continue
            self._persist_observation(release, observation)

            try:
                contracts = self.market.discover(event_key=release.release_key)
            except Exception as exc:  # noqa: BLE001
                report.errors.append(f"Discovery failed for {release.release_key}: {exc}")
                continue

            for contract in contracts:
                found.append(self._evaluate(contract, observation, release, now))
        return found

    def _observe(
        self, report: CycleReport, release: ScheduledRelease
    ) -> Optional[ReleaseObservation]:
        try:
            return self.event_source.observe(release)
        except EventDataError as exc:
            report.errors.append(f"{release.release_key}: {exc}")
            return None
        except Exception as exc:  # noqa: BLE001
            report.errors.append(f"{release.release_key}: unexpected {type(exc).__name__}")
            return None

    def _evaluate(
        self,
        contract: Contract,
        observation: ReleaseObservation,
        release: ScheduledRelease,
        now: datetime,
    ) -> Opportunity:
        book: Optional[OrderBook] = None
        try:
            book = self.market.orderbook(contract.ticker)
        except MarketDataError:
            book = None

        estimate: Optional[ProbabilityEstimate] = None
        if observation.status is ReleaseStatus.VERIFIED and observation.value is not None:
            value = (
                observation.yoy_change
                if contract.settlement_rules and "yoy" in contract.settlement_rules.lower()
                and observation.yoy_change is not None
                else observation.value
            )
            if contract.strike is not None:
                margin = None
                if contract.strike:
                    margin = abs(value - contract.strike) / max(abs(contract.strike) * 0.01, 1e-9)
                estimate = probability_from_resolved_value(
                    observed_value=value,
                    strike=contract.strike,
                    comparison=contract.comparison,
                    margin_ratio=margin,
                )

        seconds = None
        try:
            resolution = ensure_utc(datetime.fromisoformat(contract.resolution_time))
            seconds = (resolution - now).total_seconds()
        except (TypeError, ValueError):
            seconds = None

        min_edge = self.survival.policy.min_edge
        return self.opportunities.evaluate(
            contract=contract,
            book=book,
            observation=observation,
            estimate=estimate,
            now_seconds_to_resolution=seconds,
            min_edge_override=min_edge,
        )

    # ------------------------------------------------------------------
    def _consider(self, report: CycleReport, opportunity: Opportunity) -> None:
        """Analyst -> guardian -> risk -> execution, for one shortlisted candidate."""
        contract = opportunity.contract
        assert opportunity.edge is not None and opportunity.estimate is not None
        equity = self.ledger.equity()

        # 1. Adversarial review. A missing analyst is a PASS, never a default yes.
        review = None
        if self.analyst is None and self.require_analyst:
            self._record_hold(
                report,
                ticker=contract.ticker,
                stage="analyst",
                reason=(
                    "No analyst is available to challenge this edge. An unchallenged "
                    "opportunity is not traded."
                ),
                opportunity=opportunity,
            )
            return
        if self.analyst is not None:
            review = self.analyst.review(
                contract=contract,
                observation=opportunity.observation,
                estimate=opportunity.estimate,
                edge=opportunity.edge,
                book=opportunity.book,
                survival_state=self.survival.state.value,
            )
            report.analyst_calls += 1
            if not review.proceed:
                self._record_hold(
                    report,
                    ticker=contract.ticker,
                    stage="analyst",
                    reason=review.failure or f"Analyst recommended {review.recommendation}.",
                    opportunity=opportunity,
                    review=review,
                )
                return

        # 2. Provisional size, so the guardian can judge real premium at risk.
        provisional = self.risk.size(
            price=opportunity.edge.price,
            equity=equity,
            cash=self.ledger.cash,
            fee_model=contract.fee_model,
            fx_rate=self.fx_rate,
            available_contracts=opportunity.book.total_ask_depth if opportunity.book else None,
            current_exposure=self.ledger.total_exposure(),
            event_exposure=self.ledger.exposure_for_event(contract.event_key),
            positions_opened_today=self.ledger.positions_opened_today,
            daily_pnl=self.ledger.daily_pnl(),
            day_start_equity=self.ledger.day_start_equity,
            risk_multiplier=self.survival.policy.risk_multiplier,
            survival_state=self.survival.state.value,
            survival_max_premium_pct=self.survival.policy.max_premium_pct,
            survival_max_exposure_pct=self.survival.policy.max_exposure_pct,
            terminated=self.survival.is_terminated(),
        )
        if not provisional.approved:
            self._record_hold(
                report,
                ticker=contract.ticker,
                stage="risk",
                reason=provisional.reason,
                opportunity=opportunity,
                review=review,
                sizing=provisional,
            )
            return

        # 3. The guardian. Deterministic, downgrade-only.
        outcome = self.guardian.review(
            proposed_action="BUY",
            net_edge=opportunity.edge.net_edge,
            equity=equity,
            venue=contract.venue,
            data_source=opportunity.observation.source if opportunity.observation else None,
            event_verified=bool(opportunity.observation and opportunity.observation.verified),
            resolution_known=contract.strike is not None,
            liquidity=opportunity.liquidity,
            min_liquidity=float(self.opportunities.filters.min_liquidity_contracts),
            premium_at_risk=provisional.max_loss_base,
            current_exposure=self.ledger.total_exposure(),
            event_exposure=self.ledger.exposure_for_event(contract.event_key),
            positions_opened_today=self.ledger.positions_opened_today,
            daily_loss=self.ledger.daily_pnl(),
            daily_loss_limit=self.risk.limits.daily_loss_amount(self.ledger.day_start_equity),
            duplicate_event=self.store.release_already_traded(contract.event_key),
        )
        if outcome.action != "BUY":
            self._record_hold(
                report,
                ticker=contract.ticker,
                stage="policy",
                reason=outcome.reason,
                opportunity=opportunity,
                review=review,
                sizing=provisional,
                policy=outcome,
            )
            return

        # 4. Execute against the observed book.
        self._execute(report, opportunity, provisional, review, outcome)

    def _execute(
        self,
        report: CycleReport,
        opportunity: Opportunity,
        sizing: Any,
        review: Any,
        policy: Any,
    ) -> None:
        contract = opportunity.contract
        idempotency = f"{report.cycle_id}:{contract.ticker}:YES"
        if self.store.order_exists(idempotency):
            self._record_hold(
                report,
                ticker=contract.ticker,
                stage="execution",
                reason="An order with this idempotency key already exists.",
                opportunity=opportunity,
                review=review,
                sizing=sizing,
                policy=policy,
            )
            return

        result = self.market.submit(
            ticker=contract.ticker,
            contracts=sizing.contracts,
            side="YES",
            limit_price=opportunity.edge.price,
            idempotency_key=idempotency,
        )
        report.orders.append(result.to_dict())
        if not result.ok or result.filled_contracts <= 0:
            self._record_hold(
                report,
                ticker=contract.ticker,
                stage="execution",
                reason=result.reason,
                opportunity=opportunity,
                review=review,
                sizing=sizing,
                policy=policy,
            )
            return

        decision_id = self._record_decision(
            report,
            ticker=contract.ticker,
            opportunity=opportunity,
            review=review,
            sizing=sizing,
            policy=policy,
            stage="execution",
            final_action="BUY",
            executed=True,
            reason=result.reason,
            order_ref=result.order_id,
        )

        try:
            position = self.ledger.open_position(
                ticker=contract.ticker,
                event_key=contract.event_key,
                contracts=result.filled_contracts,
                price=result.average_price,
                fee=result.fee,
                quote_currency=contract.quote_currency,
                opened_at=self.clock.now_iso(),
                decision_id=decision_id,
            )
        except ContractLedgerError as exc:
            # The venue filled but the ledger refused. That is a reconciliation
            # failure, not something to swallow.
            report.errors.append(f"Ledger refused a filled order on {contract.ticker}: {exc}")
            return

        self.store.record_contract_order(
            {
                "order_id": result.order_id,
                "idempotency_key": idempotency,
                "decision_id": decision_id,
                "venue": contract.venue,
                "ticker": contract.ticker,
                "side": "YES",
                "action": "BUY",
                "contracts": result.filled_contracts,
                "limit_price": opportunity.edge.price,
                "status": result.status,
                "reason": result.reason,
            }
        )
        if result.fill is not None:
            self.store.record_contract_fill(
                {
                    "fill_id": result.fill.fill_id,
                    "order_id": result.order_id,
                    "ticker": contract.ticker,
                    "side": "YES",
                    "contracts": result.filled_contracts,
                    "price": result.average_price,
                    "premium": result.premium,
                    "fee": result.fee,
                    "quote_currency": contract.quote_currency,
                    "fx_rate": self.fx_rate,
                    "premium_base": position.premium_base,
                    "fee_base": position.fees_base,
                }
            )
        self.store.upsert_contract_position(
            {
                "position_id": position.position_id,
                "decision_id": decision_id,
                "ticker": position.ticker,
                "event_key": position.event_key,
                "side": position.side,
                "contracts": position.contracts,
                "average_price": position.average_price,
                "premium_base": position.premium_base,
                "fees_base": position.fees_base,
                "max_loss_base": position.max_loss_base,
                "max_gain_base": position.max_gain_base,
                "open": True,
            }
        )
        if self.cost_ledger is not None and position.fees_base:
            self.cost_ledger.record_fee(
                amount_base=position.fees_base,
                description=f"entry fee {contract.ticker}",
                reference=result.order_id,
            )

    # ------------------------------------------------------------------
    def _settle_resolved(self, report: CycleReport, now: datetime) -> None:
        """Resolve any open position whose event has published and matured."""
        for position in list(self.ledger.open_positions()):
            try:
                contract = self.market.rules(position.ticker)
            except MarketDataError:
                continue
            try:
                resolution = ensure_utc(datetime.fromisoformat(contract.resolution_time))
            except (TypeError, ValueError):
                continue
            if now < resolution:
                continue

            release = ScheduledRelease(
                release_key=position.event_key,
                series_key=getattr(self.event_source, "series_key", ""),
                source=getattr(self.event_source, "name", "unknown"),
                label=position.event_key,
                scheduled_at=contract.resolution_time,
                period=position.event_key.split(":")[-1],
            )
            observation = self._observe(report, release)
            if observation is None or not observation.verified or observation.value is None:
                continue
            value = (
                observation.yoy_change
                if "yoy" in (contract.settlement_rules or "").lower()
                and observation.yoy_change is not None
                else observation.value
            )
            try:
                outcome = 1 if contract.resolves_yes(value) else 0
            except MarketDataError:
                continue

            settlement_fee = contract.fee_model.settlement_fee(contracts=position.contracts)
            closed = self.ledger.settle(
                position.ticker,
                outcome=outcome,
                settled_at=self.clock.now_iso(),
                settlement_fee=settlement_fee,
            )
            self.store.upsert_contract_position(
                {
                    "position_id": closed.position_id,
                    "decision_id": closed.decision_id,
                    "ticker": closed.ticker,
                    "event_key": closed.event_key,
                    "side": closed.side,
                    "contracts": closed.contracts,
                    "average_price": closed.average_price,
                    "premium_base": closed.premium_base,
                    "fees_base": closed.fees_base,
                    "max_loss_base": closed.max_loss_base,
                    "max_gain_base": closed.max_gain_base,
                    "open": False,
                    "resolved_outcome": outcome,
                    "settlement_base": closed.settlement_base,
                    "realised_pnl_base": closed.realised_pnl_base,
                    "closed_at": closed.closed_at,
                }
            )
            if closed.decision_id:
                decision = self.store.decision(closed.decision_id)
                predicted = float((decision or {}).get("model_probability") or 0.5)
                market_p = (decision or {}).get("market_probability")
                self.store.record_outcome(
                    decision_id=closed.decision_id,
                    predicted_probability=predicted,
                    market_probability=market_p,
                    resolved_outcome=outcome,
                    resolved_at=closed.closed_at or self.clock.now_iso(),
                    ticker=closed.ticker,
                    event_key=closed.event_key,
                    realised_pnl_base=closed.realised_pnl_base,
                    predicted_edge=(decision or {}).get("net_edge"),
                    realised_edge=round(outcome - float(market_p or 0.0), 6)
                    if market_p is not None
                    else None,
                    resolution_source=observation.source,
                )
            if self.cost_ledger is not None and settlement_fee:
                self.cost_ledger.record_fee(
                    amount_base=settlement_fee * self.fx_rate,
                    description=f"settlement fee {closed.ticker}",
                )
            report.settlements.append(closed.to_dict())

    # ------------------------------------------------------------------
    def _persist_observation(
        self, release: ScheduledRelease, observation: ReleaseObservation
    ) -> int:
        return self.store.record_official_data(
            series_key=release.series_key,
            release_key=release.release_key,
            source=observation.source,
            scheduled_at=release.scheduled_at,
            published_at=observation.published_at,
            observed_at=observation.observed_at,
            value=observation.value,
            unit=release.unit,
            status=observation.status.value,
            verified=observation.verified,
            verification_method=observation.verification_method,
            second_read=observation.second_read,
            notes=observation.detail,
        )

    def _persist_opportunity(self, report: CycleReport, opportunity: Opportunity) -> int:
        market_id = self.store.upsert_market(
            venue=opportunity.contract.venue,
            ticker=opportunity.contract.ticker,
            kind="binary",
            question=opportunity.contract.question,
            event_key=opportunity.contract.event_key,
            resolution_source=opportunity.contract.resolution_source,
            resolution_time=opportunity.contract.resolution_time,
            settlement_rules=opportunity.contract.settlement_rules,
            tick_size=opportunity.contract.tick_size,
            min_order=opportunity.contract.min_order,
            max_order=opportunity.contract.max_order,
            fee_model=opportunity.contract.fee_model.to_dict().get("name"),
            quote_currency=opportunity.contract.quote_currency,
        )
        if opportunity.book is not None:
            self.store.record_market_snapshot(
                market_id=market_id,
                ticker=opportunity.ticker,
                observed_at=opportunity.book.observed_at,
                yes_bid=opportunity.book.best_bid,
                yes_ask=opportunity.book.best_ask,
                mid=opportunity.book.mid,
                spread=opportunity.book.spread,
                top_depth=opportunity.book.top_depth,
                total_depth=opportunity.book.total_ask_depth,
                book=opportunity.book.to_dict(),
                source=opportunity.book.source,
            )
        record = opportunity.to_record(report.cycle_id, market_id=market_id)
        opportunity_id = self.store.record_opportunity(record)
        report.opportunities.append(opportunity.to_dict())
        return opportunity_id

    def _record_hold(
        self,
        report: CycleReport,
        *,
        ticker: Optional[str],
        stage: str,
        reason: str,
        opportunity: Optional[Opportunity] = None,
        review: Any = None,
        sizing: Any = None,
        policy: Any = None,
    ) -> int:
        return self._record_decision(
            report,
            ticker=ticker,
            opportunity=opportunity,
            review=review,
            sizing=sizing,
            policy=policy,
            stage=stage,
            final_action="HOLD",
            executed=False,
            reason=reason,
        )

    def _record_decision(
        self,
        report: CycleReport,
        *,
        ticker: Optional[str],
        opportunity: Optional[Opportunity],
        review: Any,
        sizing: Any,
        policy: Any,
        stage: str,
        final_action: str,
        executed: bool,
        reason: str,
        order_ref: Optional[str] = None,
    ) -> int:
        edge = opportunity.edge if opportunity else None
        estimate = opportunity.estimate if opportunity else None
        observation = opportunity.observation if opportunity else None
        payload: dict[str, Any] = {
            "cycle_id": report.cycle_id,
            "kind": "binary",
            "ticker": ticker,
            "event_key": opportunity.event_key if opportunity else None,
            "model_probability": estimate.probability if estimate else None,
            "market_probability": edge.market_probability if edge else None,
            "gross_edge": edge.gross_edge if edge else None,
            "net_edge": edge.net_edge if edge else None,
            "fees": edge.fee_cost if edge else None,
            "spread": edge.spread_cost if edge else None,
            "liquidity": opportunity.liquidity if opportunity else None,
            "ai_model": getattr(review, "model", None),
            "ai_action": getattr(review, "recommendation", None),
            "ai_confidence": getattr(review, "confidence", None),
            "ai_bull": getattr(review, "bull_case", None),
            "ai_bear": getattr(review, "bear_case", None),
            "ai_invalidators": getattr(review, "invalidators", None),
            "ai_raw": getattr(review, "raw", None),
            "ai_validated": bool(getattr(review, "ok", False)),
            "ai_failure": getattr(review, "failure", None),
            "proposed_action": "BUY" if opportunity and opportunity.selected else "HOLD",
            "policy_action": getattr(policy, "action", None),
            "policy_reason": getattr(policy, "reason", None),
            "survival_state": self.survival.state.value,
            "risk_multiplier": self.survival.policy.risk_multiplier,
            "risk_approved": bool(getattr(sizing, "approved", False)),
            "risk_reason": getattr(sizing, "reason", None),
            "risk_json": sizing.to_dict() if sizing is not None else None,
            "final_action": final_action,
            "executed": executed,
            "order_ref": order_ref,
            "stage": stage,
            "equity_before": self.ledger.equity(),
            "cash_before": self.ledger.cash,
            "base_currency": self.ledger.base_currency,
            "notes": reason,
        }
        inputs: list[dict[str, Any]] = []
        if observation is not None:
            inputs.append(
                {
                    "name": "official_data",
                    "kind": "official",
                    "value": observation.to_dict(),
                    "source": observation.source,
                    "as_of": observation.observed_at,
                }
            )
        if opportunity is not None and opportunity.book is not None:
            inputs.append(
                {
                    "name": "orderbook",
                    "kind": "market",
                    "value": opportunity.book.to_dict(),
                    "source": opportunity.book.source,
                    "as_of": opportunity.book.observed_at,
                }
            )
        if estimate is not None:
            inputs.append(
                {"name": "probability_estimate", "kind": "derived", "value": estimate.to_dict()}
            )
        if edge is not None:
            inputs.append({"name": "edge", "kind": "derived", "value": edge.to_dict()})
        inputs.append(
            {
                "name": "account",
                "kind": "account",
                "value": {
                    "equity": self.ledger.equity(),
                    "cash": self.ledger.cash,
                    "exposure": self.ledger.total_exposure(),
                    "survival_state": self.survival.state.value,
                },
            }
        )
        if policy is not None:
            inputs.append(
                {"name": "policy_checks", "kind": "config", "value": policy.to_dict()}
            )
        decision_id = self.store.record_decision(payload, inputs=inputs)
        report.decisions.append({**payload, "id": decision_id})
        return decision_id
