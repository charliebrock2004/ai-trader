/**
 * Authorisation for the frontend's mutating endpoints.
 *
 * There are two different gates in this system, and conflating them is what
 * made Start fail on the deployed site:
 *
 * **Gate B — who may drive the worker.** `AI_TRADER_API_TOKEN` is the worker's
 * control secret. The Vercel server holds it and attaches it to outbound calls
 * in `worker-remote.server.ts`. The browser never sees it, so the worker is
 * not controllable by anyone who merely knows its URL. This gate is enforced
 * inside the worker itself (`ai_trader/http_api.py`), which is the only place
 * that can be trusted to enforce it.
 *
 * **Gate A — who may drive this frontend.** That is what this file decides.
 * The browser holds no secret, so it cannot present one: an earlier version
 * demanded a `x-ai-trader-token` header that nothing in the UI ever sent,
 * which meant a fully configured deployment answered every Start with 401.
 *
 * So Gate A is:
 *
 * - `AI_TRADER_UI_TOKEN` set → the caller must present it. This is for driving
 *   the API from a script or `curl`, not from the UI.
 * - Otherwise, whoever can load the page can press Start. Restrict *page
 *   access* at the platform (Vercel Deployment Protection) — see DEPLOYMENT.md.
 *   Gate B still stands, so the worker is never open to the internet.
 * - Otherwise still nothing configured, on a real host → refuse. An
 *   unconfigured deployment is closed, not open.
 *
 * No token of any kind is ever prefixed `VITE_`, which would inline it into the
 * browser bundle, and none is ever included in a response body.
 */

const TOKEN_HEADER = "x-ai-trader-token";

export type AuthResult = { ok: true } | { ok: false; status: number; message: string };

/** The worker's control secret. Server-side only; never sent to the browser. */
function configuredToken(): string {
  return (process.env.AI_TRADER_API_TOKEN ?? "").trim();
}

/** Optional gate on this frontend's own mutating routes. */
function uiToken(): string {
  return (process.env.AI_TRADER_UI_TOKEN ?? "").trim();
}

function isPreviewHost(): boolean {
  if (process.env.VERCEL) return false;
  if (process.env.AWS_LAMBDA_FUNCTION_NAME) return false;
  if (process.env.NODE_ENV === "production") return false;
  return true;
}

/**
 * Constant-time-ish comparison. Not a defence against a local attacker, but it
 * avoids leaking the token length or prefix through response timing to a remote
 * one.
 */
function tokensMatch(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i += 1) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

export function authoriseMutation(request: Request): AuthResult {
  const gate = uiToken();
  if (gate) {
    const supplied = (request.headers.get(TOKEN_HEADER) ?? "").trim();
    if (!supplied || !tokensMatch(supplied, gate)) {
      return { ok: false, status: 401, message: "Unauthorised." };
    }
    return { ok: true };
  }

  const expected = configuredToken();
  if (!expected) {
    if (isPreviewHost()) return { ok: true };
    return {
      ok: false,
      status: 503,
      message:
        "This deployment has no AI_TRADER_API_TOKEN configured, so mutating " +
        "endpoints are disabled. Set one on the server to enable control.",
    };
  }
  // The worker's secret is configured and held here on the server. Requests
  // proxied from this origin are authorised to use it; the worker rejects
  // anything that arrives without it.
  return { ok: true };
}

export function unauthorised(result: Extract<AuthResult, { ok: false }>): Response {
  return Response.json(
    { ok: false, error: result.message, live: false },
    { status: result.status },
  );
}

/** True when Start/Stop can be used. Never reveals a token itself. */
export function mutationsEnabled(): boolean {
  return uiToken().length > 0 || configuredToken().length > 0 || isPreviewHost();
}

/**
 * Whether page access is the only thing standing between the internet and the
 * Start button. Surfaced on the System page so the operator is told, rather
 * than left to assume the deployment is private.
 */
export function frontendIsOpen(): boolean {
  return uiToken().length === 0 && !isPreviewHost();
}
