"""Write the research bundle: Markdown, CSV, JSON and an SVG chart.

Numbers are taken from the summary dict. This module does not recompute them
and does not soften them.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Iterable, Sequence


def _jsonable(value: object) -> object:
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return None
        return round(value, 10)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_jsonable(payload), indent=2, sort_keys=True) + "\n")


def write_csv(path: Path, rows: Sequence[dict], fields: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _jsonable(row.get(key)) for key in fields})


def write_equity_csv(path: Path, times_ms: Sequence[int], curves: dict[str, Sequence[float]]) -> None:
    names = list(curves)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["open_time_ms", *names])
        for index, stamp in enumerate(times_ms):
            writer.writerow([stamp, *(_jsonable(curves[name][index]) for name in names)])


def _pct(value: object) -> str:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return "n/a"
    return f"{100.0 * float(value):.2f}%"


def _num(value: object, digits: int = 4) -> str:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return "n/a"
    return f"{float(value):.{digits}f}"


def _table(headers: Sequence[str], rows: Iterable[Sequence[str]]) -> str:
    head = "| " + " | ".join(headers) + " |"
    rule = "| " + " | ".join("---" for _ in headers) + " |"
    body = ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join([head, rule, *body])


def render_markdown(summary: dict) -> str:
    data = summary["data"]
    verdict = summary["verdict"]
    costs = summary["costs"]["primary"]
    lines = [
        "# BTC spot research report",
        "",
        verdict["headline"],
        "",
        summary["disclaimer"],
        "",
        "## What this is",
        "",
        "- Result class: historical backtest.",
        "- Paper trading: not run.",
        "- Live trading: not run.",
        "- No orders were sent. No private API credentials were read.",
        "",
        "## Data",
        "",
        f"- Symbol: {data.get('symbol')} {data.get('interval')} spot.",
        f"- Source: {data.get('source')} ({data.get('host')}).",
        f"- Coverage: {data.get('start_utc')} to {data.get('end_utc')}.",
        f"- Valid bars: {data.get('n_valid')}. Identical duplicates dropped: {data.get('n_duplicate_identical')}. Malformed dropped: {data.get('n_malformed')}.",
        f"- Gaps: {data.get('n_gaps')} gaps, {data.get('missing_bars')} missing bars. Missing bars were not filled.",
        f"- Cache hit: {data.get('cache_hit')}.",
        "",
        data.get("limitation", ""),
        "",
        "## Costs",
        "",
        f"Primary per side: fee {costs['fee_bps']} bps, slippage {costs['slippage_bps']} bps, half-spread {costs['half_spread_bps']} bps.",
        f"Approximate round-trip drag on a zero gross move: {_pct(costs.get('approx_round_trip_return_drag'))}.",
        "This matches a Binance spot VIP 0 taker (10 bps) plus a small-order spread and slippage allowance. The 7.5 bp BNB-discount case is a sensitivity, not the base case. Maker and taker are the same at VIP 0 on spot, so a limit order does not remove the fee.",
        "",
        "## Pre-registered decision rule",
        "",
        summary["gates"]["description"],
        "",
        "## Walk-forward out of sample",
        "",
        "This is the table that matters. Full-sample results are below it and are not evidence of an edge.",
        "",
        _table(
            ["Strategy", "Role", "OOS return", "Max DD", "Sharpe", "Folds up", "P(mean>0)", "CI low", "Gate"],
            [
                [
                    row["name"],
                    row["role"],
                    _pct(row.get("oos_total_return")),
                    _pct(row.get("oos_max_drawdown")),
                    _num(row.get("oos_sharpe"), 2),
                    _pct(row.get("positive_fold_fraction")),
                    _num(row.get("p_mean_positive"), 3),
                    _pct(row.get("oos_ci95_low")),
                    "pass" if row.get("passes_gate") else "fail",
                ]
                for row in summary["ranking"]
            ],
        ),
        "",
        "## Verdict",
        "",
        verdict["detail"],
        "",
        "Promising names under the pre-registered rule: "
        + (", ".join(verdict["promising"]) if verdict["promising"] else "none"),
        "",
        "## Time-of-day and weekday (multiplicity corrected)",
        "",
        "Gross means test whether that clock bucket's average return differs from zero. Net means subtract one full round trip per trade. A rejected null on gross returns is not a tradable edge. p-values are HAC (Newey-West) normal approximations. BH and Bonferroni are computed inside each family (24 hours, and separately 7 weekdays).",
        "",
        "### Hours",
        "",
        _table(
            ["Hour", "N", "Mean gross", "Mean net", "p raw", "p BH", "BH 5%"],
            [
                [
                    row["name"],
                    str(row["n"]),
                    _pct(row.get("mean_gross")),
                    _pct(row.get("mean_net_of_round_trip")),
                    _num(row.get("p_hac"), 4),
                    _num(row.get("p_bh"), 4),
                    "yes" if row.get("reject_bh_05") else "no",
                ]
                for row in summary["temporal"]["hours"]
            ],
        ),
        "",
        "### Weekdays",
        "",
        _table(
            ["Day", "N", "Mean gross", "Mean net", "p raw", "p BH", "BH 5%"],
            [
                [
                    row["name"],
                    str(row["n"]),
                    _pct(row.get("mean_gross")),
                    _pct(row.get("mean_net_of_round_trip")),
                    _num(row.get("p_hac"), 4),
                    _num(row.get("p_bh"), 4),
                    "yes" if row.get("reject_bh_05") else "no",
                ]
                for row in summary["temporal"]["weekdays"]
            ],
        ),
        "",
        "## Full sample, descriptive only",
        "",
        "These numbers use the same bars that suggested the question. They are not out of sample.",
        "",
        _table(
            ["Strategy", "Return", "CAGR", "Max DD", "Sharpe", "Trades", "Win rate", "Expectancy", "Sensitivity return"],
            [
                [
                    row["name"],
                    _pct(row.get("total_return")),
                    _pct(row.get("cagr")),
                    _pct(row.get("max_drawdown")),
                    _num(row.get("sharpe"), 2),
                    str(row.get("n_closed_trades")),
                    _pct(row.get("win_rate")),
                    _num(row.get("expectancy"), 2),
                    _pct(row.get("sensitivity_total_return")),
                ]
                for row in summary["full_sample"]
            ],
        ),
        "",
        "## Bayesian probability",
        "",
        "Prior: arithmetic annualised mean ~ Normal(0, 0.10^2). One update. Effective sample size never exceeds the raw count. This is P(mean per-bar return > 0 | data, prior), not a trading instruction.",
        "",
        _table(
            ["Strategy", "N", "N eff", "rho", "P(mean>0)", "Posterior annual mean"],
            [
                [
                    row["name"],
                    str(row.get("n")),
                    _num(row.get("n_eff"), 1),
                    _num(row.get("rho_lag1"), 3),
                    _num(row.get("p_mean_positive"), 3),
                    _pct(row.get("posterior_mean_annual")),
                ]
                for row in summary["bayesian"]
            ],
        ),
        "",
        "## Position sizing",
        "",
        "Quarter-Kelly, hard cap 25% of equity, zero when the training-window edge is not positive. No leverage, no martingale. In-sample fractions are descriptive. The out-of-sample column resizes each test fold using only that fold's training returns.",
        "",
        _table(
            ["Strategy", "In-sample fraction", "Folds sized at zero", "Mean OOS fraction", "OOS return if sized"],
            [
                [
                    row["name"],
                    _pct(row.get("in_sample_fraction")),
                    str(row.get("oos_folds_zero")),
                    _pct(row.get("oos_mean_fraction")),
                    _pct(row.get("oos_sized_total_return")),
                ]
                for row in summary["sizing"]
            ],
        ),
        "",
        "## Regimes",
        "",
        "Pre-registered calendar slices. Descriptive. A strategy that only works in one regime is not stable.",
        "",
        _table(
            ["Strategy", "Regime", "Return", "Max DD"],
            [
                [row["name"], row["regime"], _pct(row.get("total_return")), _pct(row.get("max_drawdown"))]
                for row in summary["regimes"]
            ],
        ),
        "",
        "## Assumptions and limitations",
        "",
        "- Spot BTCUSDT only. No futures, no funding, no leverage, no borrow.",
        "- Signal at a bar uses completed bars only. The fill is that bar's open. The close is a mark, not a signal.",
        "- Walk-forward test accounts start flat, so a hold across a fold boundary pays an extra round trip.",
        "- Sharpe ratios annualise with sqrt(bars per year) and are descriptive. Crypto returns are fat-tailed; Sharpe is not a utility.",
        "- HAC p-values use a normal approximation and a Bartlett kernel.",
        "- Winsorised hour means are a robustness column. They are not the decision statistic.",
        "- Costs are assumptions, not the user's actual fee tier.",
        "- A rising buy-and-hold result is the market, not evidence of a timing edge.",
        "- Passing the gate would still be a historical result. It would not be permission to trade real money.",
        "",
        f"Schema: {summary['schema_version']}.",
        "",
    ]
    return "\n".join(lines)


def write_svg(path: Path, series: dict[str, Sequence[float]]) -> None:
    """Two-panel chart: equity rebased to 1, and drawdown. No third-party plot library."""
    if not series:
        return
    width, height = 960, 520
    left, right, top = 48, 16, 28
    panel_h = 200
    names = list(series)
    palette = ("#c6a15b", "#9aaa78", "#c47870", "#d7d2c8", "#7f8c8d")
    paths: list[str] = []
    for panel, mode in ((0, "equity"), (1, "drawdown")):
        y0 = top + panel * (panel_h + 48)
        prepared = []
        for name in names:
            values = list(series[name])
            if not values:
                continue
            if mode == "equity":
                base = values[0] if values[0] else 1.0
                values = [value / base for value in values]
            else:
                peak = values[0]
                dd = []
                for value in values:
                    peak = value if value > peak else peak
                    dd.append(0.0 if peak <= 0 else (peak - value) / peak)
                values = dd
            prepared.append((name, values))
        flat = [value for _name, values in prepared for value in values]
        if not flat:
            continue
        lo, hi = min(flat), max(flat)
        if hi == lo:
            hi = lo + 1.0
        plot_w = width - left - right
        n = max(len(values) for _name, values in prepared)
        def xy(index: int, value: float, count: int) -> tuple[float, float]:
            x = left + (0 if count == 1 else plot_w * index / (count - 1))
            y = y0 + panel_h * (1 - (value - lo) / (hi - lo))
            return x, y
        paths.append(
            f'<text x="{left}" y="{y0 - 8}" fill="#d7d2c8" font-size="13" font-family="ui-sans-serif, sans-serif">{mode}</text>'
        )
        for color_index, (name, values) in enumerate(prepared):
            points = " ".join(
                f"{xy(i, value, len(values))[0]:.1f},{xy(i, value, len(values))[1]:.1f}"
                for i, value in enumerate(values)
            )
            color = palette[color_index % len(palette)]
            paths.append(f'<polyline fill="none" stroke="{color}" stroke-width="1.6" points="{points}"/>')
            paths.append(
                f'<text x="{left + color_index * 180}" y="{y0 + panel_h + 22}" fill="{color}" font-size="12" font-family="ui-sans-serif, sans-serif">{name}</text>'
            )
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
        f'<rect width="100%" height="100%" fill="#121412"/>'
        + "".join(paths)
        + "</svg>"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(svg)


def write_bundle(out_dir: Path, summary: dict, equity_times: Sequence[int], equity: dict[str, Sequence[float]]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    write_json(out_dir / "summary.json", summary)
    (out_dir / "report.md").write_text(render_markdown(summary))
    comparison_fields = [
        "name", "role", "total_return", "cagr", "max_drawdown", "sharpe",
        "n_closed_trades", "win_rate", "avg_win", "avg_loss", "expectancy",
        "mean_bar_return", "se_hac_bar_return", "p_hac", "ci95_low", "ci95_high",
        "sensitivity_total_return", "result_kind",
    ]
    write_csv(out_dir / "strategy_comparison.csv", summary["full_sample"], comparison_fields)
    rank_fields = [
        "name", "role", "oos_total_return", "oos_max_drawdown", "oos_sharpe",
        "n_folds", "positive_fold_fraction", "oos_mean_bar_return", "oos_se_hac",
        "oos_p_hac", "oos_ci95_low", "oos_ci95_high", "p_mean_positive", "n_eff",
        "holdout_total_return", "holdout_ci95_low", "passes_gate",
    ]
    write_csv(out_dir / "ranking.csv", summary["ranking"], rank_fields)
    temporal_fields = [
        "family", "name", "n", "mean_gross", "se_hac_gross", "t_hac", "p_hac",
        "p_bh", "p_bonferroni", "reject_bh_05", "mean_gross_winsor_1_99",
        "mean_net_of_round_trip", "ci95_low_net", "ci95_high_net", "p_hac_net",
    ]
    write_csv(out_dir / "temporal_hours.csv", summary["temporal"]["hours"], temporal_fields)
    write_csv(out_dir / "temporal_weekdays.csv", summary["temporal"]["weekdays"], temporal_fields)
    write_csv(
        out_dir / "walkforward_folds.csv",
        summary["walk_forward_folds"],
        ["name", "fold", "total_return", "max_drawdown", "sharpe", "n_closed_trades", "chosen_hour_utc", "test_start_ms", "test_end_ms"],
    )
    write_csv(
        out_dir / "regimes.csv",
        summary["regimes"],
        ["name", "regime", "total_return", "max_drawdown", "sharpe", "n_bars"],
    )
    write_csv(
        out_dir / "bayesian.csv",
        summary["bayesian"],
        ["name", "n", "n_eff", "rho_lag1", "sample_mean", "posterior_mean_annual", "p_mean_positive", "note"],
    )
    write_equity_csv(out_dir / "equity_curves.csv", equity_times, equity)
    chart = {}
    for name in summary.get("chart_names", []):
        if name in equity:
            chart[name] = _downsample(list(equity[name]), 400)
    write_svg(out_dir / "equity_drawdown.svg", chart)


def _downsample(values: list[float], points: int) -> list[float]:
    if len(values) <= points:
        return values
    last = len(values) - 1
    indexes = []
    seen = set()
    for step in range(points):
        index = round(last * step / (points - 1))
        if index not in seen:
            seen.add(index)
            indexes.append(index)
    return [values[index] for index in indexes]
