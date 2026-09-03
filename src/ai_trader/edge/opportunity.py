"""Opportunity detection and ranking.

Cheap deterministic filtering happens first, so the model is never asked about
a candidate that could not have been traded anyway. That is both a cost control
and a correctness one: every rejection is recorded with its reason, which is
what makes "the agent considered 40 things and traded none of them" auditable
rather than mysterious.

Ordering of the gates is deliberate — the cheapest and most decisive checks
run first, and nothing reaches the analyst until it has survived all of them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from ai_trader.edge.edge import EdgeResult, compute_edge
from ai_trader.edge.probability import ProbabilityEstimate
from ai_trader.events.base import ReleaseObservation, ReleaseStatus
from ai_trader.markets.base import Contract, MarketDataError, OrderBook


@dataclass(frozen=True)
class Opportunity:
    """One candidate, whether or not it survived filtering."""

    ticker: str
    event_key: str
    contract: Contract
    book: Optional[OrderBook]
    observation: Optional[ReleaseObservation]
    estimate: Optional[ProbabilityEstimate]
    edge: Optional[EdgeResult]
    selected: bool
    reject_reason: Optional[str]
    rank_score: float = 0.0
    liquidity: float = 0.0
    time_to_resolution_seconds: Optional[float] = None
    data_confidence: float = 0.0
    resolution_confidence: float = 0.0
    inputs: dict[str, Any] = field(default_factory=dict)

    def to_record(self, cycle_id: str, *, market_id: Optional[int] = None,
                  official_data_id: Optional[int] = None) -> dict[str, Any]:
        """Shape expected by RecordStore.record_opportunity."""
        return {
            "cycle_id": cycle_id,
            "market_id": market_id,
            "ticker": self.ticker,
            "event_key": self.event_key,
            "official_data_id": official_data_id,
            "side": "YES",
            "model_probability": self.estimate.probability if self.estimate else None,
            "market_probability": self.edge.market_probability if self.edge else None,
            "fee_cost": self.edge.fee_cost if self.edge else None,
            "spread_cost": self.edge.spread_cost if self.edge else None,
            "gross_edge": self.edge.gross_edge if self.edge else None,
            "net_edge": self.edge.net_edge if self.edge else None,
            "liquidity": self.liquidity,
            "time_to_resolution_seconds": self.time_to_resolution_seconds,
            "data_confidence": self.data_confidence,
            "resolution_confidence": self.resolution_confidence,
            "rank_score": self.rank_score,
            "selected": self.selected,
            "reject_reason": self.reject_reason,
            "inputs": self.inputs,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "event_key": self.event_key,
            "selected": self.selected,
            "reject_reason": self.reject_reason,
            "rank_score": self.rank_score,
            "liquidity": self.liquidity,
            "model_probability": self.estimate.probability if self.estimate else None,
            "market_probability": self.edge.market_probability if self.edge else None,
            "net_edge": self.edge.net_edge if self.edge else None,
            "edge": self.edge.to_dict() if self.edge else None,
            "estimate": self.estimate.to_dict() if self.estimate else None,
            "observation": self.observation.to_dict() if self.observation else None,
            "question": self.contract.question,
            "settlement_rules": self.contract.settlement_rules,
        }


@dataclass(frozen=True)
class OpportunityFilters:
    """Deterministic gates. Every one produces a recorded rejection reason."""

    min_net_edge: float = 0.05
    min_liquidity_contracts: int = 5
    max_spread: float = 0.10
    min_price: float = 0.02
    max_price: float = 0.98
    require_verified_data: bool = True
    max_candidates_to_analyst: int = 3

    def to_dict(self) -> dict[str, Any]:
        return {
            "min_net_edge": self.min_net_edge,
            "min_liquidity_contracts": self.min_liquidity_contracts,
            "max_spread": self.max_spread,
            "min_price": self.min_price,
            "max_price": self.max_price,
            "require_verified_data": self.require_verified_data,
            "max_candidates_to_analyst": self.max_candidates_to_analyst,
        }


class OpportunityEngine:
    """Turns (contract, book, observation) triples into ranked candidates."""

    def __init__(
        self,
        *,
        filters: Optional[OpportunityFilters] = None,
        probability_fn: Any = None,
    ) -> None:
        self.filters = filters or OpportunityFilters()
        #: Injected so replay and tests can pin the estimator.
        self.probability_fn = probability_fn

    def evaluate(
        self,
        *,
        contract: Contract,
        book: Optional[OrderBook],
        observation: Optional[ReleaseObservation],
        estimate: Optional[ProbabilityEstimate],
        now_seconds_to_resolution: Optional[float] = None,
        min_edge_override: Optional[float] = None,
    ) -> Opportunity:
        """Run every gate in order. The first failure is the recorded reason."""
        f = self.filters
        min_edge = f.min_net_edge if min_edge_override is None else max(
            f.min_net_edge, float(min_edge_override)
        )

        def reject(reason: str, *, edge: Optional[EdgeResult] = None) -> Opportunity:
            return Opportunity(
                ticker=contract.ticker,
                event_key=contract.event_key,
                contract=contract,
                book=book,
                observation=observation,
                estimate=estimate,
                edge=edge,
                selected=False,
                reject_reason=reason,
                liquidity=float(book.total_ask_depth) if book else 0.0,
                time_to_resolution_seconds=now_seconds_to_resolution,
                data_confidence=estimate.confidence if estimate else 0.0,
                resolution_confidence=1.0 if contract.strike is not None else 0.0,
                inputs={
                    "filters": f.to_dict(),
                    "min_edge_applied": min_edge,
                    "book": book.to_dict() if book else None,
                    "observation": observation.to_dict() if observation else None,
                    "estimate": estimate.to_dict() if estimate else None,
                },
            )

        # 1. Resolution must be mechanically checkable.
        if contract.strike is None:
            return reject("Contract has no numeric strike; resolution is not mechanical.")

        # 2. Official data must exist and be trustworthy.
        if observation is None:
            return reject("No official-data observation for this event.")
        if f.require_verified_data and observation.status is not ReleaseStatus.VERIFIED:
            return reject(
                f"Official data status is {observation.status.value}, not verified. "
                "Uncertainty means HOLD."
            )
        if observation.value is None:
            return reject("Official data carries no value.")

        # 3. A probability must have been computed deterministically.
        if estimate is None:
            return reject("No deterministic probability estimate was produced.")

        # 4. The book must be usable.
        if book is None:
            return reject("No order book available.")
        if not book.asks:
            return reject("Order book has no offers; nothing to buy.")
        ask = book.best_ask
        if ask is None:
            return reject("Order book has no best ask.")
        if not f.min_price <= ask <= f.max_price:
            return reject(
                f"Ask {ask} is outside the tradeable band "
                f"[{f.min_price}, {f.max_price}]."
            )
        depth = book.total_ask_depth
        if depth < f.min_liquidity_contracts:
            return reject(
                f"Liquidity {depth} contracts is below the {f.min_liquidity_contracts} minimum."
            )
        spread = book.spread
        if spread is not None and spread > f.max_spread:
            return reject(f"Spread {spread:.4f} is wider than the {f.max_spread} maximum.")

        # 5. Edge, computed against the ask and net of costs.
        try:
            edge = compute_edge(
                model_probability=estimate.probability,
                ask_price=ask,
                bid_price=book.best_bid,
                fee_model=contract.fee_model,
            )
        except ValueError as exc:
            return reject(f"Edge could not be computed: {exc}")

        if edge.net_edge < min_edge:
            return reject(
                f"Net edge {edge.net_edge:.4f} is below the {min_edge:.4f} minimum.",
                edge=edge,
            )

        score = self._rank_score(
            edge=edge, estimate=estimate, depth=depth,
            seconds=now_seconds_to_resolution,
        )
        return Opportunity(
            ticker=contract.ticker,
            event_key=contract.event_key,
            contract=contract,
            book=book,
            observation=observation,
            estimate=estimate,
            edge=edge,
            selected=True,
            reject_reason=None,
            rank_score=score,
            liquidity=float(depth),
            time_to_resolution_seconds=now_seconds_to_resolution,
            data_confidence=estimate.confidence,
            resolution_confidence=1.0,
            inputs={
                "filters": f.to_dict(),
                "min_edge_applied": min_edge,
                "book": book.to_dict(),
                "observation": observation.to_dict(),
                "estimate": estimate.to_dict(),
                "edge": edge.to_dict(),
            },
        )

    @staticmethod
    def _rank_score(
        *,
        edge: EdgeResult,
        estimate: ProbabilityEstimate,
        depth: int,
        seconds: Optional[float],
    ) -> float:
        """Deterministic ranking. Bigger is better.

        Edge dominates, weighted by how much the estimate is trusted and by
        whether the book could actually absorb a position. Time to resolution
        is a mild preference for sooner, because capital tied up in a contract
        cannot be used elsewhere and the agent is small.
        """
        depth_factor = min(1.0, depth / 100.0)
        time_factor = 1.0
        if seconds is not None and seconds > 0:
            days = seconds / 86_400.0
            time_factor = 1.0 / (1.0 + days / 30.0)
        return round(edge.net_edge * estimate.confidence * (0.5 + 0.5 * depth_factor) * time_factor, 8)

    def rank(self, opportunities: list[Opportunity]) -> list[Opportunity]:
        """Selected candidates, best first. Ties break on ticker for determinism."""
        selected = [o for o in opportunities if o.selected]
        return sorted(selected, key=lambda o: (-o.rank_score, o.ticker))

    def shortlist(self, opportunities: list[Opportunity]) -> list[Opportunity]:
        """The few candidates worth spending an analyst call on."""
        return self.rank(opportunities)[: self.filters.max_candidates_to_analyst]
