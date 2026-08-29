import type { PaperReport } from "@/lib/paper-sim";

function gbp(value: number) {
  return `£${value.toFixed(2)}`;
}
function pct(value: number) {
  return `${(value * 100).toFixed(2)}%`;
}

export function PaperBoard({ report }: { report: PaperReport | null }) {
  if (!report) {
    return (
      <section className="desk-card mb-12">
        <h2 className="font-display m-0 text-[clamp(1.6rem,4vw,2.25rem)] leading-tight font-medium tracking-[-0.03em]">
          Paper book
        </h2>
        <p className="mt-2 text-sm text-muted">
          Run a paper simulation to see orders, fills, positions, risk, and performance. Still not live.
        </p>
      </section>
    );
  }

  const { risk, performance, orders, fills, closed } = report;
  const riskRows = [
    ["Risk / trade", pct(risk.max_risk_pct)],
    ["Max loss", gbp(risk.max_risk_amount)],
    ["Daily loss cap", pct(risk.max_daily_loss_pct)],
    ["Max positions", String(risk.max_open_positions)],
    ["Max trades / day", String(risk.max_trades_per_day)],
    ["Leverage", String(risk.leverage)],
    ["Mode", risk.trading_mode],
    ["Kill switch", risk.kill_switch ? "on" : "off"],
  ];
  const perfRows = [
    ["Trades", String(performance.total_trades)],
    ["Wins", String(performance.winning_trades)],
    ["Losses", String(performance.losing_trades)],
    ["Win rate", pct(performance.win_rate)],
    ["Profit factor", performance.profit_factor.toFixed(2)],
    ["Avg win", gbp(performance.average_win)],
    ["Avg loss", gbp(performance.average_loss)],
    ["Max drawdown", pct(performance.maximum_drawdown)],
    ["Return", `${performance.return_pct.toFixed(2)}%`],
  ];

  return (
    <div className="mb-12 grid gap-6">
      <section className="desk-card" aria-labelledby="risk-title">
        <h2 id="risk-title" className="font-display m-0 text-[clamp(1.4rem,3vw,2rem)] leading-tight font-medium tracking-[-0.03em]">
          Risk
        </h2>
        <p className="mt-2 text-sm text-muted">AI cannot change these limits. Broker orders remain off.</p>
        <dl className="mt-4 grid grid-cols-2 gap-2.5 md:grid-cols-4">
          {riskRows.map(([label, value]) => (
            <div key={label} className="desk-count">
              <dt className="font-mono text-[10px] tracking-[0.12em] text-faint uppercase">{label}</dt>
              <dd className="mt-1.5 mb-0 font-mono text-sm tabular-nums">{value}</dd>
            </div>
          ))}
        </dl>
      </section>

      <section className="desk-card" aria-labelledby="pos-title">
        <h2 id="pos-title" className="font-display m-0 text-[clamp(1.4rem,3vw,2rem)] leading-tight font-medium tracking-[-0.03em]">
          Positions
        </h2>
        {closed.length === 0 ? (
          <p className="mt-2 mb-0 text-sm text-muted">No simulated positions.</p>
        ) : (
          <div className="mt-4 overflow-x-auto">
            <table className="w-full min-w-[40rem] border-collapse text-left text-sm">
              <thead>
                <tr className="font-mono text-[10px] tracking-[0.12em] text-faint uppercase">
                  {["Symbol", "Side", "Qty", "Entry", "Exit", "Stop", "Target", "P&L"].map((h) => (
                    <th key={h} className="pb-2 font-medium">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {closed.map((p) => (
                  <tr key={`${p.symbol}-${p.entry_timestamp}`} className="font-mono tabular-nums">
                    <td className="py-1.5">{p.symbol}</td>
                    <td>{p.side}</td>
                    <td>{p.quantity}</td>
                    <td>{p.average_entry.toFixed(4)}</td>
                    <td>{p.current_price.toFixed(4)}</td>
                    <td>{p.stop_loss?.toFixed(4) ?? "—"}</td>
                    <td>{p.take_profit?.toFixed(4) ?? "—"}</td>
                    <td>{gbp(p.realised_pnl)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="desk-card" aria-labelledby="ord-title">
        <h2 id="ord-title" className="font-display m-0 text-[clamp(1.4rem,3vw,2rem)] leading-tight font-medium tracking-[-0.03em]">
          Orders
        </h2>
        {orders.length === 0 ? (
          <p className="mt-2 mb-0 text-sm text-muted">No paper orders.</p>
        ) : (
          <div className="mt-4 overflow-x-auto">
            <table className="w-full min-w-[40rem] border-collapse text-left text-sm">
              <thead>
                <tr className="font-mono text-[10px] tracking-[0.12em] text-faint uppercase">
                  {["ID", "Symbol", "Side", "Qty", "Price", "Status", "Time"].map((h) => (
                    <th key={h} className="pb-2 font-medium">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {orders.map((o) => (
                  <tr key={o.order_id} className="font-mono tabular-nums">
                    <td className="py-1.5">{o.order_id}</td>
                    <td>{o.symbol}</td>
                    <td>{o.side}</td>
                    <td>{o.quantity}</td>
                    <td>{(o.filled_price ?? o.requested_price).toFixed(4)}</td>
                    <td>{o.status}</td>
                    <td>{o.timestamp.replace("T", " ").slice(0, 16)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="mt-3 mb-0 font-mono text-[11px] text-faint">{fills.length} fill(s). Internal paper book only.</p>
      </section>

      <section className="desk-card" aria-labelledby="perf-title">
        <h2 id="perf-title" className="font-display m-0 text-[clamp(1.4rem,3vw,2rem)] leading-tight font-medium tracking-[-0.03em]">
          Performance
        </h2>
        <dl className="mt-4 grid grid-cols-2 gap-2.5 md:grid-cols-3">
          {perfRows.map(([label, value]) => (
            <div key={label} className="desk-count">
              <dt className="font-mono text-[10px] tracking-[0.12em] text-faint uppercase">{label}</dt>
              <dd className="mt-1.5 mb-0 font-mono text-sm tabular-nums">{value}</dd>
            </div>
          ))}
        </dl>
      </section>
    </div>
  );
}
