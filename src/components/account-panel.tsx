import type { PaperAccountState } from "@/lib/paper-account";

function gbp(value: number) {
  return `£${value.toFixed(2)}`;
}

export function AccountPanel({ account }: { account: PaperAccountState | null }) {
  const state = account;
  if (!state) {
    return (
      <section className="desk-card mb-12">
        <h2 className="font-display m-0 text-[clamp(1.6rem,4vw,2.25rem)] leading-tight font-medium tracking-[-0.03em]">
          Paper account
        </h2>
        <p className="mt-2 text-sm text-muted">
          No snapshot yet. Disengage the kill switch and run a dry cycle.
        </p>
      </section>
    );
  }

  const rows = [
    ["Starting cash", gbp(state.starting_cash)],
    ["Cash", gbp(state.cash)],
    ["Buying power", gbp(state.buying_power)],
    ["Equity", gbp(state.account_equity)],
    ["Invested", gbp(state.invested_value)],
    ["Realised P&L", gbp(state.realised_pnl)],
    ["Unrealised P&L", gbp(state.unrealised_pnl)],
    ["Total P&L", gbp(state.total_pnl)],
    ["Daily P&L", gbp(state.daily_pnl ?? 0)],
    ["Drawdown", `${((state.drawdown ?? 0) * 100).toFixed(2)}%`],
    ["Fills", String(state.fill_count)],
    ["Positions", String(state.positions.length)],
  ];

  return (
    <section className="desk-card mb-12" aria-labelledby="account-title">
      <div className="mb-4 flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
        <div>
          <h2
            id="account-title"
            className="font-display m-0 text-[clamp(1.6rem,4vw,2.25rem)] leading-tight font-medium tracking-[-0.03em]"
          >
            Paper account
          </h2>
          <p className="mt-2 text-sm text-muted">
            Offline simulated sterling. Paper only. Not live.
          </p>
        </div>
        <p className="m-0 font-mono text-sm text-muted">{state.currency} · {state.source}</p>
      </div>
      <dl className="grid grid-cols-2 gap-2.5 md:grid-cols-5">
        {rows.map(([label, value]) => (
          <div key={label} className="desk-count">
            <dt className="font-mono text-[10px] tracking-[0.12em] text-faint uppercase">{label}</dt>
            <dd className="mt-1.5 mb-0 font-mono text-sm tabular-nums">{value}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
