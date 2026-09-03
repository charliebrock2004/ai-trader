/**
 * Authorisation for mutating endpoints.
 *
 * This app is single-user by design. Without a gate, anyone with the URL could
 * start a session or run an agent cycle — spending the owner's model budget and
 * writing to their audit trail.
 *
 * Production (Vercel / NODE_ENV=production): a missing token refuses mutations.
 * Grok Build preview (`npm run dev`): the operator is the only client, so an
 * unconfigured token is allowed rather than leaving Start permanently 503.
 *
 * The token is read from the server environment only. It is never sent to the
 * browser, never prefixed with VITE_, and never included in any response.
 */

const TOKEN_HEADER = "x-ai-trader-token";

export type AuthResult = { ok: true } | { ok: false; status: number; message: string };

function configuredToken(): string {
  return (process.env.AI_TRADER_API_TOKEN ?? "").trim();
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
  const supplied = (request.headers.get(TOKEN_HEADER) ?? "").trim();
  if (!supplied || !tokensMatch(supplied, expected)) {
    return { ok: false, status: 401, message: "Unauthorised." };
  }
  return { ok: true };
}

export function unauthorised(result: Extract<AuthResult, { ok: false }>): Response {
  return Response.json(
    { ok: false, error: result.message, live: false },
    { status: result.status },
  );
}

/** True when Start/Stop can be used. Never reveals the token itself. */
export function mutationsEnabled(): boolean {
  return configuredToken().length > 0 || isPreviewHost();
}
