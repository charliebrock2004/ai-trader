import { Link } from "@tanstack/react-router";
import { useCallback, useEffect, useState } from "react";
import { Empty, Failure, Loading, Shell } from "@/components/shell";
import { api, points, probability, shortDate, type DecisionRow } from "@/lib/agent-api";

/**
 * The decision log.
 *
 * Every row is a real decision, including the ones where nothing happened.
 * Rejections are the majority and they are the point: an agent that considered
 * forty contracts and traded none of them should be legible, not silent.
 */
export function DecisionsPage() {
  const [rows, setRows] = useState<DecisionRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [onlyTrades, setOnlyTrades] = useState(false);

  const load = useCallback(async () => {
    try {
      const body = await api.decisions(100);
      setRows(body.decisions);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not reach the paper engine.");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  if (error && !rows) {
    return (
      <Shell active="decisions">
        <Failure message={error} onRetry={() => void load()} />
      </Shell>
    );
  }
  if (!rows) {
    return (
      <Shell active="decisions">
        <Loading label="Reading the decision log…" />
      </Shell>
    );
  }

  const visible = onlyTrades ? rows.filter((r) => r.executed) : rows;
  const executed = rows.filter((r) => r.executed).length;

  return (
    <Shell active="decisions">
      <section className="desk-section-head">
        <div>
          <h2 className="desk-heading">Decisions</h2>
          <p className="mt-2 mb-0 max-w-[62ch] text-sm leading-normal text-muted">
            {rows.length} recorded · {executed} executed · {rows.length - executed} held or
            rejected. Every entry keeps the reason it was refused.
          </p>
        </div>
        <button
          type="button"
          className={`desk-chip min-h-11 ${onlyTrades ? "desk-chip-active" : ""}`}
          onClick={() => setOnlyTrades((value) => !value)}
          aria-pressed={onlyTrades}
        >
          {onlyTrades ? "Showing trades" : "Show trades only"}
        </button>
      </section>

      {visible.length === 0 ? (
        <Empty
          title="Nothing recorded yet"
          detail="The agent has not reached a decision. Once a release publishes, every candidate it prices will appear here."
        />
      ) : (
        <div className="desk-scroll">
          <table className="desk-table">
            <thead>
              <tr>
                <th>When</th>
                <th>Market</th>
                <th>Action</th>
                <th className="num">Model</th>
                <th className="num">Market</th>
                <th className="num">Edge</th>
                <th>Why</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {visible.map((row) => (
                <tr key={row.id} className={row.executed ? "is-executed" : ""}>
                  <td className="whitespace-nowrap font-mono text-xs text-faint">
                    {shortDate(row.created_at)}
                  </td>
                  <td className="font-mono text-xs">{row.ticker ?? "—"}</td>
                  <td>
                    <span className={`desk-tag ${row.executed ? "tag-buy" : "tag-hold"}`}>
                      {row.final_action}
                    </span>
                  </td>
                  <td className="num">{probability(row.model_probability)}</td>
                  <td className="num">{probability(row.market_probability)}</td>
                  <td className="num">{points(row.net_edge)}</td>
                  <td className="desk-why">{row.notes ?? row.policy_reason ?? "—"}</td>
                  <td>
                    <Link
                      to="/decisions/$id"
                      params={{ id: String(row.id) }}
                      className="desk-link"
                      aria-label={`Open decision ${row.id}`}
                    >
                      →
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Shell>
  );
}
