import { useCallback, useEffect, useState } from "react";
import { Failure, Loading, Shell } from "@/components/shell";
import { ago, api, money, percent, type SystemStatus } from "@/lib/agent-api";

/**
 * System health.
 *
 * The rule this page exists to honour: if a component is broken, say so. A
 * dashboard that looks green while the engine is down is worse than no
 * dashboard, so the overall banner is red whenever any component is.
 */
export function SystemPage() {
  const [data, setData] = useState<SystemStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setData(await api.system());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not reach the paper engine.");
    }
  }, []);

  useEffect(() => {
    void load();
    const id = window.setInterval(() => void load(), 15_000);
    return () => window.clearInterval(id);
  }, [load]);

  if (error && !data) {
    return (
      <Shell active="system">
        <Failure message={error} onRetry={() => void load()} />
      </Shell>
    );
  }
  if (!data) {
    return (
      <Shell active="system">
        <Loading label="Checking components…" />
      </Shell>
    );
  }

  const broken = data.components.filter((c) => !c.ok);

  return (
    <Shell active="system">
      <section className="desk-section-head">
        <div>
          <h2 className="desk-heading">System</h2>
          <p className="mt-2 mb-0 max-w-[64ch] text-sm leading-normal text-muted">
            {broken.length === 0
              ? "Every component reports healthy."
              : `${broken.length} component${broken.length === 1 ? "" : "s"} not healthy: ${broken
                  .map((c) => c.title)
                  .join(", ")}.`}
          </p>
        </div>
        <span className={`desk-tag desk-tag-lg ${data.ok ? "tag-ok" : "tag-halt"}`}>
          {data.ok ? "HEALTHY" : "DEGRADED"}
        </span>
      </section>

      <section className="desk-panel">
        <h3 className="desk-panel-title">Components</h3>
        <ul className="desk-components">
          {data.components.map((component) => (
            <li key={component.id} className={component.ok ? "is-ok" : "is-broken"}>
              <span className="desk-dot" aria-hidden="true" />
              <div>
                <p className="m-0 text-sm font-medium">{component.title}</p>
                <p className="mt-0.5 mb-0 text-sm text-muted">{component.detail}</p>
              </div>
              <span className="desk-tag tag-muted">{component.ok ? "OK" : "FAIL"}</span>
            </li>
          ))}
        </ul>
      </section>

      <section className="desk-panel">
        <h3 className="desk-panel-title">Configuration</h3>
        <div className="desk-scroll">
          <table className="desk-table">
            <tbody>
              <Row label="Strategy" value={data.strategy} />
              <Row label="Event family" value={data.event_family} />
              <Row label="Survival state" value={data.survival.state} />
              <Row
                label="Terminal threshold"
                value={money(data.survival.terminal_threshold, data.survival.base_currency)}
              />
              <Row
                label="Life remaining"
                value={percent(data.survival.life_remaining_pct)}
              />
              <Row label="Last cycle" value={ago(data.last_cycle_at)} />
              <Row label="Last error" value={data.last_error ?? "None"} />
              <Row label="Live trading" value="Disabled in code" />
              <Row label="Paper only" value={data.paper_only ? "Yes" : "No"} />
              <Row
                label="Control endpoints"
                value={data.control_enabled ? "Enabled" : "Disabled"}
                note={
                  data.control_enabled
                    ? undefined
                    : "Mutating endpoints are refused until AI_TRADER_API_TOKEN is set on the server."
                }
              />
              <Row
                label="Trading worker"
                value={data.worker_connected ? "Connected over HTTPS" : "Local process"}
                note={
                  data.worker_connected
                    ? "A persistent worker owns the session. Closing this page does not stop it."
                    : "No PAPER_WORKER_URL is set, so the engine runs beside this server. On a serverless host that means the desk cannot run at all."
                }
              />
              {data.frontend_open ? (
                <Row
                  label="Who can press Start"
                  value="Anyone with this URL"
                  note="The worker itself is protected by its token, but this page is not gated. Turn on Vercel Deployment Protection, or set AI_TRADER_UI_TOKEN."
                />
              ) : null}
            </tbody>
          </table>
        </div>
      </section>

      <section className="desk-panel">
        <h3 className="desk-panel-title">Terminal latch</h3>
        <p className="m-0 text-sm leading-normal text-muted">
          {data.survival.latch.terminated
            ? data.survival.latch.reason
            : "Not engaged. If equity reaches the terminal threshold the agent is permanently shut down; it cannot clear that state itself."}
        </p>
      </section>
    </Shell>
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
