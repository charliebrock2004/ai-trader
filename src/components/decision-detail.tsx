import { Link } from "@tanstack/react-router";
import { useCallback, useEffect, useState } from "react";
import { Failure, Loading, Shell } from "@/components/shell";
import {
  api,
  money,
  points,
  probability,
  shortDate,
  signedMoney,
  type DecisionDetail as Detail,
} from "@/lib/agent-api";

/**
 * The black box: one decision, top to bottom, in the order the pipeline ran.
 *
 * Inputs → model probability → market probability → edge → analyst bull and
 * bear → policy verdict → risk verdict → execution → outcome → was it right.
 *
 * This is the screen that makes the project auditable rather than merely
 * plausible, so it shows the refusals as prominently as the approvals.
 */
export function DecisionDetailPage({ id }: { id: string }) {
  const [detail, setDetail] = useState<Detail | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const body = await api.decision(Number(id));
      setDetail(body.decision);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not reach the paper engine.");
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  if (error) {
    return (
      <Shell active="decisions">
        <Failure message={error} onRetry={() => void load()} />
      </Shell>
    );
  }
  if (!detail) {
    return (
      <Shell active="decisions">
        <Loading label="Reading the decision record…" />
      </Shell>
    );
  }

  const currency = detail.base_currency ?? "GBP";
  const invalidators = parseList(detail.ai_invalidators);
  const risk = parseObject(detail.risk_json);

  return (
    <Shell active="decisions">
      <section className="desk-section-head">
        <div>
          <Link to="/decisions" className="desk-link">
            ← All decisions
          </Link>
          <h2 className="desk-heading mt-2">{detail.ticker ?? "System decision"}</h2>
          <p className="mt-2 mb-0 font-mono text-xs text-faint">
            #{detail.id} · {shortDate(detail.created_at)} · cycle {detail.cycle_id}
          </p>
        </div>
        <span className={`desk-tag ${detail.executed ? "tag-buy" : "tag-hold"} desk-tag-lg`}>
          {detail.final_action}
        </span>
      </section>

      <Step index="1" title="What it knew">
        {detail.inputs.length === 0 ? (
          <p className="m-0 text-sm text-muted">No inputs were recorded for this decision.</p>
        ) : (
          <div className="desk-inputs">
            {detail.inputs.map((input) => (
              <details key={input.name} className="desk-input">
                <summary>
                  <span className="font-mono text-xs">{input.name}</span>
                  <span className="desk-tag tag-muted">{input.kind}</span>
                  {input.source ? (
                    <span className="font-mono text-[10px] text-faint">{input.source}</span>
                  ) : null}
                </summary>
                <pre className="desk-pre">{pretty(input.value_json)}</pre>
              </details>
            ))}
          </div>
        )}
      </Step>

      <Step index="2" title="What the deterministic model computed">
        <div className="desk-reason-grid">
          <Figure label="Model probability" value={probability(detail.model_probability)} />
          <Figure label="Market probability" value={probability(detail.market_probability)} />
          <Figure label="Gross edge" value={points(detail.gross_edge)} />
          <Figure label="Fees" value={points(detail.fees)} />
          <Figure label="Spread" value={points(detail.spread)} />
          <Figure label="Net edge" value={points(detail.net_edge)} emphasis />
          <Figure
            label="Liquidity"
            value={detail.liquidity === null ? "—" : `${detail.liquidity} contracts`}
          />
        </div>
        <p className="mt-3 mb-0 max-w-[70ch] text-xs leading-normal text-faint">
          The probability is computed in Python from the published number and the contract's own
          resolution rule. The analyst does not produce it.
        </p>
      </Step>

      <Step index="3" title="What the analyst argued">
        {detail.ai_action ? (
          <>
            <p className="m-0 font-mono text-xs text-faint">
              {detail.ai_model} · recommended <strong>{detail.ai_action}</strong>
              {detail.ai_confidence !== null
                ? ` · stated confidence ${probability(detail.ai_confidence)}`
                : ""}
            </p>
            <p className="mt-1 mb-3 text-xs text-faint">
              Stated confidence is a label, not a calibrated probability. It does not size
              anything.
            </p>
            <div className="desk-cases">
              <div className="desk-case case-bull">
                <p className="desk-case-title">Bull case</p>
                <p className="m-0 text-sm leading-normal">{detail.ai_bull || "—"}</p>
              </div>
              <div className="desk-case case-bear">
                <p className="desk-case-title">Bear case</p>
                <p className="m-0 text-sm leading-normal">{detail.ai_bear || "—"}</p>
              </div>
            </div>
            {invalidators.length ? (
              <>
                <p className="mt-4 mb-1.5 font-mono text-[10px] tracking-[0.12em] text-faint uppercase">
                  What would make this edge false
                </p>
                <ul className="desk-list">
                  {invalidators.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              </>
            ) : null}
          </>
        ) : (
          <p className="m-0 text-sm text-muted">
            {detail.ai_failure
              ? `The analyst was not consulted or did not return a usable answer: ${detail.ai_failure}`
              : "The candidate never reached the analyst — a deterministic gate refused it first."}
          </p>
        )}
      </Step>

      <Step index="4" title="What the policy guardian decided">
        <Verdict
          allowed={detail.policy_action === detail.proposed_action}
          headline={`${detail.proposed_action ?? "—"} → ${detail.policy_action ?? "HOLD"}`}
          detail={detail.policy_reason ?? "The guardian was not reached."}
        />
        <p className="mt-2 mb-0 text-xs text-faint">
          Survival state at the time: <strong>{detail.survival_state ?? "—"}</strong>. The guardian
          can only ever make a decision more conservative.
        </p>
      </Step>

      <Step index="5" title="What the risk engine decided">
        <Verdict
          allowed={Boolean(detail.risk_approved)}
          headline={detail.risk_approved ? "Approved" : "Refused"}
          detail={detail.risk_reason ?? "Risk was not reached."}
        />
        {risk ? (
          <div className="desk-reason-grid mt-3">
            <Figure label="Contracts" value={String(risk.contracts ?? "—")} />
            <Figure
              label="Premium"
              value={money(numberOrNull(risk.premium_base), currency)}
            />
            <Figure
              label="Max loss"
              value={money(numberOrNull(risk.max_loss_base), currency)}
            />
            <Figure
              label="Max gain"
              value={money(numberOrNull(risk.max_gain_base), currency)}
            />
            <Figure label="Bound by" value={String(risk.binding_constraint ?? "—")} />
          </div>
        ) : null}
      </Step>

      <Step index="6" title="What happened">
        {detail.executed ? (
          <p className="m-0 text-sm leading-normal text-muted">
            Executed as order <span className="font-mono">{detail.order_ref ?? "—"}</span> against
            the observed book.
          </p>
        ) : (
          <p className="m-0 text-sm leading-normal text-muted">
            No order was placed. Refused at the <strong>{detail.stage ?? "—"}</strong> stage.
          </p>
        )}
        <p className="mt-2 mb-0 text-sm text-muted">{detail.notes ?? ""}</p>
      </Step>

      <Step index="7" title="Was the model right?">
        {detail.outcome ? (
          <>
            <div className="desk-reason-grid">
              <Figure
                label="Predicted"
                value={probability(detail.outcome.predicted_probability)}
              />
              <Figure
                label="Resolved"
                value={detail.outcome.resolved_outcome === 1 ? "YES" : "NO"}
              />
              <Figure
                label="Correct"
                value={detail.outcome.correct === 1 ? "Yes" : "No"}
                emphasis
              />
              <Figure label="Brier" value={detail.outcome.brier.toFixed(4)} />
              <Figure
                label="Realised P&L"
                value={signedMoney(detail.outcome.realised_pnl_base, currency)}
              />
            </div>
            <p className="mt-3 mb-0 text-xs text-faint">
              Resolved {shortDate(detail.outcome.resolved_at)}. A low Brier score means the
              probability was both confident and right; a high one means confidently wrong.
            </p>
          </>
        ) : (
          <p className="m-0 text-sm text-muted">
            Not resolved yet. When the event settles, the outcome and its Brier score are recorded
            against this decision.
          </p>
        )}
      </Step>
    </Shell>
  );
}

function Step({
  index,
  title,
  children,
}: {
  index: string;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="desk-step">
      <header className="desk-step-head">
        <span className="desk-step-index" aria-hidden="true">
          {index}
        </span>
        <h3 className="desk-step-title">{title}</h3>
      </header>
      <div className="desk-step-body">{children}</div>
    </section>
  );
}

function Figure({
  label,
  value,
  emphasis = false,
}: {
  label: string;
  value: string;
  emphasis?: boolean;
}) {
  return (
    <div>
      <p className="m-0 font-mono text-[10px] tracking-[0.12em] text-faint uppercase">{label}</p>
      <p className={`mt-1 mb-0 font-mono tabular-nums ${emphasis ? "text-lg" : "text-base"}`}>
        {value}
      </p>
    </div>
  );
}

function Verdict({
  allowed,
  headline,
  detail,
}: {
  allowed: boolean;
  headline: string;
  detail: string;
}) {
  return (
    <div className={`desk-verdict ${allowed ? "is-allowed" : "is-refused"}`}>
      <p className="m-0 font-mono text-sm">{headline}</p>
      <p className="mt-1.5 mb-0 max-w-[70ch] text-sm leading-normal text-muted">{detail}</p>
    </div>
  );
}

function parseList(raw: string | null): string[] {
  if (!raw) return [];
  try {
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.filter((i): i is string => typeof i === "string") : [];
  } catch {
    return [];
  }
}

function parseObject(raw: string | null): Record<string, unknown> | null {
  if (!raw) return null;
  try {
    const parsed: unknown = JSON.parse(raw);
    return parsed && typeof parsed === "object" ? (parsed as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}

function numberOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function pretty(raw: string): string {
  try {
    return JSON.stringify(JSON.parse(raw), null, 2);
  } catch {
    return raw;
  }
}
