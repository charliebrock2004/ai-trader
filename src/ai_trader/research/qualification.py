"""The qualification gate. Written before any result existed; do not tune it to
pass a strategy. Changing a threshold is a logged decision, not a fix.

Stages, in order. A strategy never skips one:

``REJECTED``              fails on evidence that more data will not repair
``INSUFFICIENT_EVIDENCE`` too few observations to say either way
``PAPER_CANDIDATE``       passed history; may run in forward paper trading
``LIVE_PILOT_ELIGIBLE``   also passed forward paper trading; still needs the
                          owner's explicit approval of an exact proposal

``timing_edge`` is reported separately. A strategy that passes without it has
shown controlled risk while holding the asset, not skill at timing it, and is
labelled ``risk_managed_beta``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Criteria:
    # Evidence
    min_oos_trades: int = 30
    min_oos_years: float = 3.0
    min_folds_traded: float = 0.75
    # Profitability after costs
    min_profit_factor: float = 1.2
    # Risk
    max_drawdown: float = 0.25
    min_oos_sharpe: float = 0.8
    # Stability
    min_folds_positive: float = 0.6
    min_grid_positive: float = 0.5
    min_deflated_sharpe: float = 0.90
    # Holdout (scored once)
    min_holdout_trades: int = 5
    min_holdout_sharpe: float = 0.5
    # Timing skill versus same-exposure buy-and-hold, Bonferroni across candidates
    alpha: float = 0.05
    # Forward paper trading, before any live proposal
    min_paper_days: int = 60
    min_paper_trades: int = 20
    max_paper_drawdown: float = 0.15
    max_paper_execution_error_rate: float = 0.02


CRITERIA = Criteria()


def assess(ev: dict, *, n_candidates: int, deflated_sharpe: float, c: Criteria = CRITERIA) -> dict:
    """Judge one walk-forward evaluation. Returns verdict, label and every check."""
    oos, stress, hold = ev["oos"], ev["oos_stress"], ev["holdout"]
    checks: list[tuple[str, bool, str]] = []

    def chk(name: str, ok: bool, detail: str) -> bool:
        checks.append((name, bool(ok), detail))
        return bool(ok)

    enough = all([
        chk("oos_trades", oos["trades"] >= c.min_oos_trades, f"{oos['trades']} closed trades (need {c.min_oos_trades})"),
        chk("oos_years", oos["years"] >= c.min_oos_years, f"{oos['years']} years (need {c.min_oos_years})"),
        chk("folds_traded", ev["folds_traded"] >= c.min_folds_traded, f"traded in {ev['folds_traded']:.0%} of folds"),
    ])
    pf = oos["profit_factor"]
    hard = all([
        chk("net_positive", oos["net_return"] > 0, f"net {oos['net_return']:+.1%} after costs"),
        chk("net_positive_2x_costs", stress["net_return"] > 0, f"net {stress['net_return']:+.1%} at double costs"),
        chk("profit_factor", pf is not None and pf >= c.min_profit_factor, f"profit factor {pf if pf is None else round(pf, 2)}"),
        chk("max_drawdown", oos["max_drawdown"] <= c.max_drawdown, f"max drawdown {oos['max_drawdown']:.1%} (limit {c.max_drawdown:.0%})"),
        chk("sharpe", oos["sharpe"] >= c.min_oos_sharpe, f"Sharpe {oos['sharpe']:.2f} (need {c.min_oos_sharpe})"),
        chk("folds_positive", ev["folds_positive"] >= c.min_folds_positive, f"{ev['folds_positive']:.0%} of folds net positive"),
        chk("grid_positive", ev["grid_positive"] >= c.min_grid_positive, f"{ev['grid_positive']:.0%} of parameter grid net positive"),
        chk("deflated_sharpe", deflated_sharpe >= c.min_deflated_sharpe, f"deflated Sharpe probability {deflated_sharpe:.2f}"),
    ])
    p = ev["oos_excess"]["p_le_zero"]
    timing = chk("timing_edge", p * n_candidates < c.alpha,
                 f"excess over same-exposure buy-and-hold: p={p:.3f} x {n_candidates} candidates")
    holdout_ok = None
    if hold is not None:
        holdout_ok = all([
            chk("holdout_trades", hold["trades"] >= c.min_holdout_trades, f"{hold['trades']} holdout trades"),
            chk("holdout_net_positive", hold["net_return"] > 0, f"holdout net {hold['net_return']:+.1%}"),
            chk("holdout_max_drawdown", hold["max_drawdown"] <= c.max_drawdown, f"holdout drawdown {hold['max_drawdown']:.1%}"),
            chk("holdout_sharpe", hold["sharpe"] >= c.min_holdout_sharpe, f"holdout Sharpe {hold['sharpe']:.2f}"),
        ])

    # A loss or a breached drawdown limit is already observed; more trades cannot undo it.
    observed_failure = oos["net_return"] <= 0 or oos["max_drawdown"] > c.max_drawdown
    if not enough:
        verdict = "REJECTED" if observed_failure else "INSUFFICIENT_EVIDENCE"
    elif not hard or holdout_ok is False:
        verdict = "REJECTED"
    elif holdout_ok is None:
        verdict = "INSUFFICIENT_EVIDENCE"  # holdout not scored in this run
    else:
        verdict = "PAPER_CANDIDATE"
    return {
        "verdict": verdict,
        "label": None if verdict != "PAPER_CANDIDATE" else ("timing_edge" if timing else "risk_managed_beta"),
        "failed": [n for n, ok, _ in checks if not ok],
        "checks": [{"name": n, "ok": ok, "detail": d} for n, ok, d in checks],
    }


def assess_paper(stats: dict, c: Criteria = CRITERIA) -> dict:
    """Forward paper-trading gate. Passing makes a strategy *eligible* for a live
    pilot proposal; it never arms anything."""
    checks = [
        ("paper_days", stats.get("days", 0) >= c.min_paper_days),
        ("paper_trades", stats.get("trades", 0) >= c.min_paper_trades),
        ("paper_net_positive", stats.get("net_return", 0.0) > 0),
        ("paper_max_drawdown", stats.get("max_drawdown", 1.0) <= c.max_paper_drawdown),
        ("paper_execution_errors", stats.get("execution_error_rate", 1.0) <= c.max_paper_execution_error_rate),
    ]
    failed = [n for n, ok in checks if not ok]
    return {"verdict": "LIVE_PILOT_ELIGIBLE" if not failed else "PAPER_CANDIDATE", "failed": failed,
            "requires_owner_approval": True}


def criteria_dict(c: Criteria = CRITERIA) -> dict:
    return asdict(c)
