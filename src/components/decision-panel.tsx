import { useMemo, useState } from "react";
import type { FixtureDecision } from "@/lib/fixture-ai";

export function DecisionPanel({ rows }: { rows: FixtureDecision[] }) {
  const [symbol, setSymbol] = useState(rows[0]?.symbol ?? "");
  const active = useMemo(
    () => rows.find((row) => row.symbol === symbol) ?? rows[0],
    [rows, symbol],
  );

  if (!rows.length || !active) {
    return (
      <section className="desk-card mb-12">
        <h2 className="font-display m-0 text-[clamp(1.6rem,4vw,2.25rem)] leading-tight font-medium tracking-[-0.03em]">
          AI decision
        </h2>
        <p className="mt-2 text-sm text-muted">
          No fixture decision yet. Disengage the kill switch and run a dry cycle.
        </p>
      </section>
    );
  }

  const facts = [
    ["Action", active.action],
    ["Confidence", active.confidence.toFixed(2)],
    ["Model", active.model ?? "fixture-hold"],
    ["Risk", active.risk ?? "rejected"],
    ["Paper", active.execution ?? "none"],
    ["Broker", active.broker ?? "NOT USED"],
  ];

  return (
    <section className="desk-card mb-12" aria-labelledby="decision-title">
      <div className="mb-4 flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
        <div>
          <h2
            id="decision-title"
            className="font-display m-0 text-[clamp(1.6rem,4vw,2.25rem)] leading-tight font-medium tracking-[-0.03em]"
          >
            AI decision
          </h2>
          <p className="mt-2 text-sm text-muted">
            Paper simulation only. Risk sizes and can reject. Broker is not used.
          </p>
        </div>
        <p className="m-0 font-mono text-sm text-muted">{active.model}</p>
      </div>
      <div className="mb-4 flex flex-wrap gap-2">
        {rows.map((row) => (
          <button
            key={row.symbol}
            type="button"
            className={row.symbol === active.symbol ? "desk-chip min-h-11 bg-fg text-bg" : "desk-chip min-h-11"}
            onClick={() => setSymbol(row.symbol)}
          >
            {row.symbol}
          </button>
        ))}
      </div>
      <dl className="mb-4 grid grid-cols-2 gap-2.5 md:grid-cols-4">
        {facts.map(([label, value]) => (
          <div key={label} className="desk-count">
            <dt className="font-mono text-[10px] tracking-[0.12em] text-faint uppercase">{label}</dt>
            <dd className="mt-1.5 mb-0 break-all font-mono text-sm tabular-nums">{value}</dd>
          </div>
        ))}
      </dl>
      <p className="m-0 text-sm leading-normal text-muted">{active.reasoning}</p>
    </section>
  );
}
