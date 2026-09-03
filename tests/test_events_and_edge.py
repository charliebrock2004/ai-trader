"""Official data, deterministic probability and edge.

The strategy rests on "an objective number was published and we read it
correctly". These tests are about refusing to trade whenever that is in doubt.
"""

from __future__ import annotations

import pytest

from ai_trader.clock import FrozenClock
from ai_trader.edge.edge import compute_edge
from ai_trader.edge.opportunity import OpportunityEngine, OpportunityFilters
from ai_trader.edge.probability import (
    probability_from_forecast,
    probability_from_resolved_value,
)
from ai_trader.events.base import (
    ReleaseObservation,
    ReleaseStatus,
    ScheduledRelease,
    verify_two_reads,
)
from ai_trader.events.bls import CPI_SERIES_ID, BLSCPISource, FixtureEventSource
from ai_trader.markets.base import BookLevel, Contract, OrderBook
from ai_trader.markets.fees import STANDARD_FEES, ZeroFeeModel

CLOCK = FrozenClock("2026-04-14T14:00:00+00:00")


def _payload(rows, series=CPI_SERIES_ID, status="REQUEST_SUCCEEDED"):
    return {
        "status": status,
        "Results": {"series": [{"seriesID": series, "data": rows}]},
    }


def _row(year, month, value):
    return {"year": str(year), "period": f"M{month:02d}", "periodName": "x", "value": str(value)}


class _Client:
    def __init__(self, payloads, error=None):
        self._payloads = list(payloads)
        self._error = error
        self.calls = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append(json)
        if self._error:
            raise self._error
        payload = self._payloads[min(len(self.calls) - 1, len(self._payloads) - 1)]

        class _R:
            def raise_for_status(self_inner):
                return None

            def json(self_inner):
                return payload

        return _R()


def _release(period="2026-03") -> ScheduledRelease:
    return ScheduledRelease(
        release_key=f"CPI:{period}",
        series_key=f"BLS:{CPI_SERIES_ID}",
        source="BLS",
        label=f"CPI {period}",
        scheduled_at="2026-04-12T13:30:00+00:00",
        period=period,
    )


# ==========================================================================
# Verification
# ==========================================================================
def test_two_agreeing_reads_are_required_for_verification() -> None:
    assert verify_two_reads(310.5, 310.5)[0] is ReleaseStatus.VERIFIED
    assert verify_two_reads(310.5, None)[0] is ReleaseStatus.UNVERIFIED
    assert verify_two_reads(None, None)[0] is ReleaseStatus.UNAVAILABLE
    assert verify_two_reads(310.5, 311.0)[0] is ReleaseStatus.CONFLICT


def test_a_single_read_is_not_verified_by_default() -> None:
    """One read could be a transient parse or cache fault and look identical."""
    status, detail = verify_two_reads(100.0, None)
    assert status is ReleaseStatus.UNVERIFIED
    assert "not independently confirmed" in detail


def test_bls_returns_verified_when_both_reads_agree() -> None:
    rows_2026 = [_row(2026, 3, 320.0)]
    rows_wide = [_row(2026, 3, 320.0), _row(2025, 3, 310.0)]
    source = BLSCPISource(http_client=_Client([_payload(rows_2026), _payload(rows_wide)]), clock=CLOCK)
    observation = source.observe(_release())
    assert observation.status is ReleaseStatus.VERIFIED
    assert observation.value == 320.0
    assert observation.second_read == 320.0
    assert observation.previous_value == 310.0
    assert observation.yoy_change == pytest.approx(3.2258, abs=1e-3)
    assert observation.tradeable is True


def test_bls_flags_a_conflict_when_reads_disagree() -> None:
    source = BLSCPISource(
        http_client=_Client([_payload([_row(2026, 3, 320.0)]), _payload([_row(2026, 3, 321.5)])]),
        clock=CLOCK,
    )
    observation = source.observe(_release())
    assert observation.status is ReleaseStatus.CONFLICT
    assert observation.tradeable is False
    assert "Refusing to trade" in observation.detail


def test_bls_reports_pending_when_the_number_is_not_published() -> None:
    source = BLSCPISource(http_client=_Client([_payload([_row(2026, 2, 318.0)])]), clock=CLOCK)
    observation = source.observe(_release("2026-03"))
    assert observation.status is ReleaseStatus.PENDING
    assert observation.tradeable is False


def test_bls_fails_closed_on_network_and_timeout() -> None:
    for error, expected in [
        (TimeoutError("timed out"), ReleaseStatus.UNAVAILABLE),
        (ConnectionError("down"), ReleaseStatus.UNAVAILABLE),
    ]:
        source = BLSCPISource(http_client=_Client([], error=error), clock=CLOCK)
        assert source.observe(_release()).status is expected


def test_bls_rejects_a_payload_for_a_different_series() -> None:
    """A response about another series must never be read as ours."""
    payload = _payload([_row(2026, 3, 999.0)], series="SOMETHING-ELSE")
    source = BLSCPISource(http_client=_Client([payload]), clock=CLOCK)
    observation = source.observe(_release())
    assert observation.status is ReleaseStatus.PENDING
    assert observation.value is None


def test_bls_rejects_a_non_numeric_value() -> None:
    payload = _payload([{"year": "2026", "period": "M03", "value": "n/a"}])
    source = BLSCPISource(http_client=_Client([payload]), clock=CLOCK)
    observation = source.observe(_release())
    assert observation.status is ReleaseStatus.MALFORMED


def test_bls_rejects_an_unsuccessful_request() -> None:
    payload = _payload([], status="REQUEST_NOT_PROCESSED")
    source = BLSCPISource(http_client=_Client([payload]), clock=CLOCK)
    assert source.observe(_release()).status is ReleaseStatus.UNAVAILABLE


def test_bls_calendar_is_ordered_and_covers_the_prior_month() -> None:
    source = BLSCPISource(http_client=_Client([]), clock=CLOCK)
    calendar = source.calendar(limit=4)
    assert [r.scheduled_at for r in calendar] == sorted(r.scheduled_at for r in calendar)
    assert all(r.source == "BLS" for r in calendar)
    assert all(r.release_key.startswith("CPI:") for r in calendar)


def test_event_source_refuses_a_broker_url() -> None:
    source = BLSCPISource(http_client=_Client([]), url="https://x/alpaca/y", clock=CLOCK)
    observation = source.observe(_release())
    assert observation.status is ReleaseStatus.UNAVAILABLE


# ==========================================================================
# Probability
# ==========================================================================
def test_a_published_number_gives_a_near_certain_probability() -> None:
    est = probability_from_resolved_value(observed_value=3.4, strike=3.0, comparison="above")
    assert est.probability == pytest.approx(0.98)
    assert est.method == "resolved_comparison"
    assert est.inputs["observed_value"] == 3.4


def test_a_number_that_misses_gives_a_near_zero_probability() -> None:
    est = probability_from_resolved_value(observed_value=2.8, strike=3.0, comparison="above")
    assert est.probability == pytest.approx(0.02)


def test_a_thin_margin_is_discounted_because_a_revision_could_flip_it() -> None:
    wide = probability_from_resolved_value(
        observed_value=3.9, strike=3.0, comparison="above", margin_ratio=5.0
    )
    thin = probability_from_resolved_value(
        observed_value=3.01, strike=3.0, comparison="above", margin_ratio=0.05
    )
    assert thin.probability < wide.probability
    assert thin.confidence < wide.confidence


def test_probability_is_never_exactly_zero_or_one() -> None:
    est = probability_from_resolved_value(
        observed_value=100.0, strike=1.0, comparison="above", revision_risk=0.0
    )
    assert 0.0 < est.probability < 1.0


def test_every_comparison_is_supported_and_an_unknown_one_raises() -> None:
    for comparison, value, expected_high in [
        ("above", 3.4, True), ("at_or_above", 3.0, True),
        ("below", 2.5, True), ("at_or_below", 3.0, True),
    ]:
        est = probability_from_resolved_value(
            observed_value=value, strike=3.0, comparison=comparison
        )
        assert (est.probability > 0.5) is expected_high
    with pytest.raises(ValueError):
        probability_from_resolved_value(observed_value=1, strike=1, comparison="sideways")


def test_pre_release_forecasts_carry_deliberately_low_confidence() -> None:
    est = probability_from_forecast(forecast=3.2, strike=3.0, comparison="above", dispersion=0.2)
    assert est.probability > 0.5
    assert est.confidence == 0.35, "not expected to beat the market's own forecast"
    with pytest.raises(ValueError):
        probability_from_forecast(forecast=1, strike=1, comparison="above", dispersion=0)


# ==========================================================================
# Edge
# ==========================================================================
def test_edge_is_computed_against_the_ask_not_the_mid() -> None:
    """Using the mid would credit half the spread as edge that does not exist."""
    edge = compute_edge(
        model_probability=0.90, ask_price=0.75, bid_price=0.70, fee_model=ZeroFeeModel()
    )
    assert edge.market_probability == 0.75
    assert edge.gross_edge == pytest.approx(0.15)
    assert edge.spread_cost == pytest.approx(0.025)
    assert edge.net_edge == pytest.approx(0.125)


def test_fees_reduce_the_edge() -> None:
    free = compute_edge(model_probability=0.9, ask_price=0.5, fee_model=ZeroFeeModel())
    charged = compute_edge(model_probability=0.9, ask_price=0.5, fee_model=STANDARD_FEES)
    assert charged.net_edge < free.net_edge
    assert charged.fee_cost == pytest.approx(0.0175)


def test_a_market_priced_above_the_model_is_not_an_opportunity() -> None:
    edge = compute_edge(model_probability=0.40, ask_price=0.60, fee_model=ZeroFeeModel())
    assert edge.net_edge < 0
    assert edge.is_opportunity is False


def test_expected_value_per_contract_matches_p_minus_price_minus_costs() -> None:
    edge = compute_edge(model_probability=0.80, ask_price=0.60, fee_model=ZeroFeeModel())
    assert edge.expected_value_per_contract() == pytest.approx(0.20)


def test_an_impossible_price_is_refused() -> None:
    for bad in (0.0, 1.0, -0.5, 1.5):
        with pytest.raises(ValueError):
            compute_edge(model_probability=0.5, ask_price=bad)


# ==========================================================================
# Opportunity filtering
# ==========================================================================
def _contract(**kwargs) -> Contract:
    defaults = dict(
        ticker="CPI-ABOVE-30",
        question="CPI YoY above 3.0%?",
        event_key="CPI:2026-03",
        resolution_source="BLS",
        resolution_time="2026-04-20T00:00:00+00:00",
        settlement_rules="Resolves YES if CPI YoY exceeds 3.0.",
        strike=3.0,
        comparison="above",
        fee_model=ZeroFeeModel(),
    )
    defaults.update(kwargs)
    return Contract(**defaults)


def _book(asks=((0.70, 200),), bids=((0.68, 200),)) -> OrderBook:
    return OrderBook(
        ticker="CPI-ABOVE-30",
        observed_at="2026-04-14T14:00:00+00:00",
        bids=tuple(BookLevel(p, c) for p, c in bids),
        asks=tuple(BookLevel(p, c) for p, c in asks),
    )


def _observation(status=ReleaseStatus.VERIFIED, value=3.4) -> ReleaseObservation:
    return ReleaseObservation(
        release_key="CPI:2026-03",
        series_key="BLS:X",
        source="BLS",
        status=status,
        observed_at="2026-04-14T13:35:00+00:00",
        value=value,
        yoy_change=value,
        verification_method="two reads agreed",
    )


def _estimate(value=3.4):
    return probability_from_resolved_value(observed_value=value, strike=3.0, comparison="above")


def test_a_clean_candidate_is_selected_and_scored() -> None:
    engine = OpportunityEngine()
    opp = engine.evaluate(
        contract=_contract(), book=_book(), observation=_observation(), estimate=_estimate()
    )
    assert opp.selected is True
    assert opp.reject_reason is None
    assert opp.rank_score > 0
    assert opp.edge is not None and opp.edge.net_edge > 0.05


def test_every_rejection_carries_a_recorded_reason() -> None:
    engine = OpportunityEngine()
    cases = [
        (dict(contract=_contract(strike=None)), "no numeric strike"),
        (dict(observation=None), "No official-data observation"),
        (dict(observation=_observation(status=ReleaseStatus.PENDING)), "not verified"),
        (dict(observation=_observation(status=ReleaseStatus.CONFLICT)), "not verified"),
        (dict(estimate=None), "No deterministic probability"),
        (dict(book=None), "No order book"),
        (dict(book=_book(asks=())), "no offers"),
        (dict(book=_book(asks=((0.995, 500),))), "outside the tradeable band"),
        (dict(book=_book(asks=((0.70, 2),))), "below the 5 minimum"),
        (dict(book=_book(asks=((0.70, 200),), bids=((0.40, 200),))), "wider than"),
    ]
    for override, expected in cases:
        kwargs = dict(
            contract=_contract(), book=_book(), observation=_observation(), estimate=_estimate()
        )
        kwargs.update(override)
        opp = engine.evaluate(**kwargs)
        assert opp.selected is False, override
        assert expected in (opp.reject_reason or ""), (override, opp.reject_reason)


def test_a_thin_edge_is_rejected_with_the_threshold_named() -> None:
    engine = OpportunityEngine(filters=OpportunityFilters(min_net_edge=0.05))
    opp = engine.evaluate(
        contract=_contract(), book=_book(asks=((0.96, 500),), bids=((0.95, 500),)),
        observation=_observation(), estimate=_estimate(),
    )
    assert opp.selected is False
    assert "below the 0.0500 minimum" in opp.reject_reason


def test_survival_can_raise_the_edge_bar_but_never_lower_it() -> None:
    engine = OpportunityEngine(filters=OpportunityFilters(min_net_edge=0.05))
    kwargs = dict(
        contract=_contract(), book=_book(asks=((0.90, 500),), bids=((0.89, 500),)),
        observation=_observation(), estimate=_estimate(),
    )
    assert engine.evaluate(**kwargs).selected is True
    # A stricter survival state raises the bar.
    assert engine.evaluate(**kwargs, min_edge_override=0.20).selected is False
    # A laxer one cannot lower it below the configured floor.
    assert engine.evaluate(**kwargs, min_edge_override=0.0).selected is True
    thin = dict(kwargs)
    thin["book"] = _book(asks=((0.96, 500),), bids=((0.955, 500),))
    assert engine.evaluate(**thin, min_edge_override=0.0).selected is False


def test_ranking_is_deterministic_and_puts_the_best_edge_first() -> None:
    engine = OpportunityEngine(filters=OpportunityFilters(max_candidates_to_analyst=2))
    good = engine.evaluate(
        contract=_contract(ticker="A"), book=_book(asks=((0.60, 500),), bids=((0.59, 500),)),
        observation=_observation(), estimate=_estimate(),
    )
    better = engine.evaluate(
        contract=_contract(ticker="B"), book=_book(asks=((0.40, 500),), bids=((0.39, 500),)),
        observation=_observation(), estimate=_estimate(),
    )
    weakest = engine.evaluate(
        contract=_contract(ticker="C"), book=_book(asks=((0.85, 500),), bids=((0.84, 500),)),
        observation=_observation(), estimate=_estimate(),
    )
    ranked = engine.rank([good, better, weakest])
    assert [o.ticker for o in ranked] == ["B", "A", "C"]
    assert [o.ticker for o in engine.shortlist([good, better, weakest])] == ["B", "A"]
    # Same inputs, same order, every time.
    assert engine.rank([weakest, good, better]) == ranked


def test_the_shortlist_is_what_limits_analyst_spend() -> None:
    engine = OpportunityEngine(filters=OpportunityFilters(max_candidates_to_analyst=1))
    opps = [
        engine.evaluate(
            contract=_contract(ticker=f"T{i}"),
            book=_book(asks=((0.50 + i * 0.05, 500),), bids=((0.49 + i * 0.05, 500),)),
            observation=_observation(), estimate=_estimate(),
        )
        for i in range(5)
    ]
    assert len(engine.shortlist(opps)) == 1


def test_fixture_event_source_is_offline_and_deterministic() -> None:
    source = FixtureEventSource(clock=CLOCK)
    release = _release()
    source.add(release, _observation())
    assert source.observe(release).status is ReleaseStatus.VERIFIED
    assert source.observe(_release("2099-01")).status is ReleaseStatus.PENDING
    assert source.health()["live"] is False
