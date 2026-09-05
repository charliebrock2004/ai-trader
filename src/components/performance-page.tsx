import { useCallback, useEffect, useState } from "react";
import { Empty, Failure, Loading, Shell } from "@/components/shell";
import {
  api,
  money,
  percent,
  points,
  probability,
  signedMoney,
  type CalibrationBucket,
  type Performance,
} from "@/lib/agent-api";

/**
 * Performance.
 *
 * Exactly two charts, both earning their place: the equity curve answers "is it
 * making money" and the calibration plot answers "are its probabilities honest".
 * A third chart would be decoration.
 *
 * Every number comes from persisted data, and the page states what the sample
 * can support rather than letting a small sample look conclusive.
 */
export function PerformancePage() {
  const [data, setData] = useState<Performance | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setData(await api.performance());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not reach the paper engine.");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  if (error && !data) {
    return (
      <Shell active="performance">
        <Failure message={error} onRetry={() => void load()} />
      </Shell>
    );
  }
  if (!data) {
    return (
      <Shell active="performance">
        <Loading label="Computing performance from the ledger…" />
      </Shell>
    );
  }

  return (
    <Shell active="performance">
      <section className="desk-section-head">
        <div>
          <h2 className="desk-heading">Performance</h2>
          <p className="mt-2 mb-0 max-w-[64ch] text-sm leading-normal text-muted">
            {data.evidence_note}
          </p>
        </div>
      </section>

      <dl className="desk-counts">
        <Stat label="Equity" value={money(data.equity)} />
        <Stat label="Net P&L" value={signedMoney(data.net_pnl)} tone={data.net_pnl} />
        <Stat label="Return" value={percent(data.total_return_pct, 2)} />
        <Stat label="Max drawdown" value={percent(data.max_drawdown_pct, 2)} />
      </dl>

      <section className="desk-panel">
        <h3 className="desk-panel-title">Equity</h3>
        {data.trades === 0 ? (
          <Empty
            title="No completed trades"
            detail="The equity curve appears once positions have settled. Nothing here supports any claim about edge yet."
          />
        ) : (
          <EquityCurve start={data.starting_equity} end={data.equity} trades={data.trades} />
        )}
      </section>

      <section className="desk-panel">
        <header className="desk-panel-head">
          <h3 className="desk-panel-title">Calibration</h3>
          <span className="font-mono text-[10px] text-faint">
            {data.calibration.count} resolved
          </span>
        </header>
        <p className="mt-0 mb-4 max-w-[64ch] text-sm leading-normal text-muted">
          {data.calibration.verdict}
        </p>
        {data.calibration.buckets.length === 0 ? (
          <Empty
            title="Nothing to plot"
            detail="Calibration needs resolved forecasts. Each settled contract adds one point."
          />
        ) : (
          <CalibrationChart buckets={data.calibration.buckets} />
        )}
      </section>

      <PipelineFunnel pipelines={data.pipelines} />

      <section className="desk-panel">
        <h3 className="desk-panel-title">Detail</h3>
        <div className="desk-scroll">
          <table className="desk-table">
            <tbody>
              <Row label="Trades" value={String(data.trades)} />
              <Row label="Wins / losses" value={`${data.wins} / ${data.losses}`} />
              <Row label="Win rate" value={probability(data.win_rate)} />
              <Row label="Average win" value={money(data.average_win)} />
              <Row label="Average loss" value={money(data.average_loss)} />
              <Row
                label="Expectancy per trade"
                value={data.expectancy === null ? "—" : money(data.expectancy)}
              />
              <Row
                label="Profit factor"
                value={data.profit_factor === null ? "—" : data.profit_factor.toFixed(2)}
              />
              <Row
                label="Risk-adjusted (per trade)"
                value={data.sharpe_like === null ? "—" : data.sharpe_like.toFixed(3)}
                note="Not annualised and no risk-free rate, so not a Sharpe ratio."
              />
              <Row label="Brier score" value={data.brier === null ? "—" : data.brier.toFixed(4)} />
              <Row
                label="Calibration skill"
                value={
                  data.calibration.skill_score === null
                    ? "—"
                    : data.calibration.skill_score.toFixed(3)
                }
                note="Versus always predicting the base rate. Zero or below means no skill."
              />
              <Row
                label="Opportunities considered"
                value={String(data.opportunities_considered)}
                note="Every pipeline combined. See the funnel above for the split."
              />
              <Row label="Executed" value={String(data.opportunities_executed)} />
              <Row label="Rejected" value={String(data.opportunities_rejected)} />
              <Row label="Conversion" value={probability(data.conversion_rate)} />
              <Row label="Average predicted edge" value={points(data.average_predicted_edge)} />
              <Row label="Average realised edge" value={points(data.average_realised_edge)} />
              <Row label="Fees" value={money(data.fees)} />
              <Row label="Operating costs" value={money(data.operating_costs)} />
              <Row label="Gross P&L" value={signedMoney(data.gross_pnl)} />
              <Row label="Net of costs" value={signedMoney(data.net_pnl)} />
              <Row
                label="Covers its own costs"
                value={data.self_sustaining ? "Yes" : "No"}
              />
            </tbody>
          </table>
        </div>
      </section>
    </Shell>
  );
}

/**
 * Equity from the opening stake to now.
 *
 * Two points, drawn honestly: the engine records settlements, not a tick-level
 * equity path, so drawing a smooth curve through them would be inventing shape
 * that the data does not have.
 */
function EquityCurve({ start, end, trades }: { start: number; end: number; trades: number }) {
  const width = 640;
  const height = 160;
  const pad = 24;
  const min = Math.min(start, end) * 0.98;
  const max = Math.max(start, end) * 1.02;
  const span = Math.max(max - min, 0.01);
  const y = (value: number) => pad + (1 - (value - min) / span) * (height - pad * 2);
  const rising = end >= start;

  return (
    <figure className="desk-chart">
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Equity from start to now">
        <line
          x1={pad}
          x2={width - pad}
          y1={y(start)}
          y2={y(start)}
          className="chart-baseline"
          strokeDasharray="3 4"
        />
        <polyline
          points={`${pad},${y(start)} ${width - pad},${y(end)}`}
          className={rising ? "chart-line up" : "chart-line down"}
        />
        <circle cx={pad} cy={y(start)} r={3.5} className="chart-dot" />
        <circle cx={width - pad} cy={y(end)} r={4.5} className="chart-dot last" />
      </svg>
      <figcaption className="desk-chart-caption">
        {money(start)} → {money(end)} over {trades} settled {trades === 1 ? "trade" : "trades"}.
        Points are settlements; the line between them is not a price path.
      </figcaption>
    </figure>
  );
}

/** Predicted probability against observed frequency. The diagonal is perfect. */
function CalibrationChart({ buckets }: { buckets: CalibrationBucket[] }) {
  const size = 260;
  const pad = 28;
  const scale = (value: number) => pad + value * (size - pad * 2);
  const maxCount = Math.max(...buckets.map((b) => b.count), 1);

  return (
    <figure className="desk-chart">
      <svg
        viewBox={`0 0 ${size} ${size}`}
        role="img"
        aria-label="Predicted probability against observed frequency"
        className="desk-calibration"
      >
        <line
          x1={scale(0)}
          y1={size - scale(0)}
          x2={scale(1)}
          y2={size - scale(1)}
          className="chart-baseline"
          strokeDasharray="3 4"
        />
        {buckets.map((bucket) => (
          <circle
            key={bucket.label}
            cx={scale(bucket.mean_predicted)}
            cy={size - scale(bucket.observed_rate)}
            r={4 + (bucket.count / maxCount) * 5}
            className={Math.abs(bucket.gap) > 0.15 ? "chart-dot off" : "chart-dot"}
          >
            <title>
              {`${bucket.label}: predicted ${(bucket.mean_predicted * 100).toFixed(0)}%, ` +
                `happened ${(bucket.observed_rate * 100).toFixed(0)}% (n=${bucket.count})`}
            </title>
          </circle>
        ))}
        <text x={scale(0)} y={size - 6} className="chart-axis">
          0%
        </text>
        <text x={scale(1) - 18} y={size - 6} className="chart-axis">
          100%
        </text>
        <text x={4} y={size - scale(1) + 10} className="chart-axis">
          100%
        </text>
      </svg>
      <figcaption className="desk-chart-caption">
        Predicted (x) against observed (y). On the dashed line means honest; above means the agent
        was too pessimistic, below means overconfident. Dot size is sample count.
      </figcaption>
    </figure>
  );
}

function Stat({ label, value, tone }: { label: string; value: string; tone?: number }) {
  const cls = tone === undefined ? "" : tone > 0 ? "text-ok" : tone < 0 ? "text-halt" : "";
  return (
    <div className="desk-count">
      <dt className="font-mono text-[10px] tracking-[0.12em] text-faint uppercase">{label}</dt>
      <dd className={`mt-1.5 mb-0 font-mono text-xl tabular-nums ${cls}`}>{value}</dd>
    </div>
  );
}

function Row({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <tr>
      <th scope="row" className="desk-row-label">
        {label}
        {note ? <span className="desk-row-note">{note}</span> : null}
      </th>
      <td className="num">{value}</td>
    </tr>
  );
}


/**
 * Conversion, split by pipeline and by the stage that stopped each decision.
 *
 * The combined "opportunities considered" number is close to useless on its
 * own: the CPI prediction-market pipeline records thousands of holds because no
 * venue order book is attached, which swamps the spot desk and reports a
 * conversion rate that has nothing to do with the spot strategy. Reading
 * "4,666 rejected" and concluding the strategy is broken is the mistake this
 * panel exists to prevent.
 */
function PipelineFunnel({ pipelines }: { pipelines?: Performance["pipelines"] }) {
  const rows = Object.entries(pipelines ?? {}).sort(
    (a, b) => b[1].considered - a[1].considered,
  );
  if (!rows.length) return null;

  return (
    <section className="desk-panel">
      <h3 className="desk-panel-title">Where decisions stop</h3>
      {rows.map(([kind, funnel]) => {
        const reasons = Object.entries(funnel.by_rejection).sort((a, b) => b[1] - a[1]);
        const total = reasons.reduce((sum, [, n]) => sum + n, 0);
        return (
          <div key={kind} className="desk-funnel">
            <header className="desk-panel-head">
              <h4 className="m-0 font-mono text-xs tracking-[0.12em] uppercase">{kind}</h4>
              <span className="font-mono text-[11px] text-faint">
                {funnel.considered} considered · {funnel.executed} executed ·{" "}
                {funnel.conversion_rate === null
                  ? "—"
                  : `${(funnel.conversion_rate * 100).toFixed(2)}%`}
              </span>
            </header>
            <ul className="desk-rejections">
              {reasons.map(([reason, n]) => (
                <li key={reason}>
                  <span className="desk-rejection-count">{n}</span>
                  <span>{reason.replace(/_/g, " ")}</span>
                  <span className="desk-rejection-share">
                    {total ? `${Math.round((n / total) * 100)}%` : ""}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        );
      })}
    </section>
  );
}
