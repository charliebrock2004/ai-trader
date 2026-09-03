import type { SurvivalSnapshot } from "@/lib/agent-api";
import { money, percent } from "@/lib/agent-api";

/**
 * The survival meter.
 *
 * This is the emotional centre of the product and the one view that makes the
 * agent read as something with capital at stake rather than a chart page. It
 * shows exactly three numbers on one line — where it started, where it is, and
 * where it dies — because that is the whole story.
 *
 * The bar measures the distance from the terminal threshold up to the starting
 * stake, so "half empty" means half the expendable capital is gone. Growth past
 * the starting stake fills it completely and is reported separately; the point
 * of the bar is distance from death, not progress.
 */
export function SurvivalMeter({ survival }: { survival: SurvivalSnapshot }) {
  const {
    state,
    terminated,
    equity,
    starting_equity,
    terminal_threshold,
    base_currency,
    life_remaining_pct,
    distance_to_terminal,
  } = survival;

  const filled = Math.max(0, Math.min(100, life_remaining_pct));
  const above = equity > starting_equity;

  return (
    <section className={`desk-survival state-${state.toLowerCase()}`} aria-label="Survival">
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="m-0 font-mono text-[10px] tracking-[0.16em] text-faint uppercase">
          Survival
        </p>
        <p className={`desk-state-badge state-${state.toLowerCase()} m-0`}>
          {terminated ? "TERMINATED" : state}
        </p>
      </header>

      <div
        className="desk-meter"
        role="meter"
        aria-valuenow={Math.round(filled)}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label="Capital remaining above the terminal threshold"
      >
        <div className="desk-meter-fill" style={{ width: `${filled}%` }} />
        {above ? <div className="desk-meter-surplus" aria-hidden="true" /> : null}
      </div>

      <dl className="desk-meter-scale">
        <div>
          <dt>Terminal</dt>
          <dd className="text-halt">{money(terminal_threshold, base_currency)}</dd>
        </div>
        <div className="text-center">
          <dt>Now</dt>
          <dd className="desk-meter-now">{money(equity, base_currency)}</dd>
        </div>
        <div className="text-right">
          <dt>Started</dt>
          <dd>{money(starting_equity, base_currency)}</dd>
        </div>
      </dl>

      <p className="mt-3 mb-0 max-w-[52ch] text-sm leading-normal text-muted">
        {terminated ? (
          <>
            The agent reached its terminal threshold and was permanently shut down. It cannot
            trade or restart itself.
          </>
        ) : (
          <>
            {money(distance_to_terminal, base_currency)} above the terminal threshold —{" "}
            {percent(life_remaining_pct)} of the expendable stake remains.
          </>
        )}
      </p>
      <p className="mt-1.5 mb-0 max-w-[52ch] text-xs leading-normal text-faint">
        {survival.policy.description}
      </p>
    </section>
  );
}
