import { Link } from "@tanstack/react-router";
import type { ReactNode } from "react";

/**
 * Shared frame: banner, brand, navigation.
 *
 * The paper-simulation banner is permanent and deliberately the first thing on
 * the page. Nothing in this product should ever be mistakable for real money.
 */
export function Shell({
  children,
  active,
}: {
  children: ReactNode;
  active: "home" | "decisions" | "performance" | "system";
}) {
  const tabs = [
    { key: "home", to: "/", label: "Agent" },
    { key: "decisions", to: "/decisions", label: "Decisions" },
    { key: "performance", to: "/performance", label: "Performance" },
    { key: "system", to: "/system", label: "System" },
  ] as const;

  return (
    <div className="desk-frame">
      <p className="desk-banner" role="status">
        Paper simulation — no real trading
      </p>
      <header className="desk-top">
        <div className="desk-brand">
          <span className="desk-mark" aria-hidden="true" />
          <div>
            <p className="m-0 font-mono text-[10px] tracking-[0.16em] text-faint uppercase">
              Autonomous paper agent
            </p>
            <h1 className="font-display m-0 text-[1.35rem] leading-tight font-medium tracking-[-0.03em]">
              AI-Trader
            </h1>
          </div>
        </div>
        <nav className="flex flex-wrap items-center justify-end gap-2" aria-label="Sections">
          {tabs.map((tab) => (
            <Link
              key={tab.key}
              to={tab.to}
              className={`desk-chip min-h-11 ${active === tab.key ? "desk-chip-active" : ""}`}
              aria-current={active === tab.key ? "page" : undefined}
            >
              {tab.label}
            </Link>
          ))}
        </nav>
      </header>
      {children}
      <footer className="mt-10 flex flex-col gap-1.5 border-t border-fg/12 pt-4 font-mono text-xs text-faint">
        <p className="m-0">Paper only. Live trading is disabled in code.</p>
        <p className="m-0">
          No strategy here has a demonstrated out-of-sample edge. Figures are evidence, not claims.
        </p>
      </footer>
    </div>
  );
}

/** Shown while real state is still loading. Never shows placeholder numbers. */
export function Loading({ label = "Reading engine state…" }: { label?: string }) {
  return (
    <section className="desk-state" role="status" aria-live="polite">
      <span className="desk-pulse" aria-hidden="true" />
      <p className="m-0 text-sm text-muted">{label}</p>
    </section>
  );
}

/** Shown when the engine cannot be reached. Says so plainly. */
export function Failure({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <section className="desk-state desk-state-error" role="alert">
      <p className="m-0 font-mono text-[11px] tracking-[0.14em] text-halt uppercase">
        Engine unreachable
      </p>
      <p className="mt-2 mb-0 max-w-[60ch] text-sm text-muted">{message}</p>
      <p className="mt-2 mb-0 max-w-[60ch] text-sm text-faint">
        No figures are shown, because none can be trusted right now.
      </p>
      {onRetry ? (
        <button type="button" className="desk-btn desk-btn-ghost mt-4 min-h-11" onClick={onRetry}>
          Retry
        </button>
      ) : null}
    </section>
  );
}

/** Shown when there is genuinely nothing yet — not an error. */
export function Empty({ title, detail }: { title: string; detail: string }) {
  return (
    <section className="desk-state">
      <p className="m-0 font-mono text-[11px] tracking-[0.14em] text-faint uppercase">{title}</p>
      <p className="mt-2 mb-0 max-w-[60ch] text-sm text-muted">{detail}</p>
    </section>
  );
}
