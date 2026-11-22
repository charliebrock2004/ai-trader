import { applySecurityHeaders } from "../../src/lib/security-headers";

export default async function securityHeadersMiddleware(
  _event: unknown,
  next: () => unknown | Promise<unknown>,
): Promise<unknown> {
  const result = await next();
  if (!(result instanceof Response)) return result;
  try {
    applySecurityHeaders(result.headers);
    return result;
  } catch {
    // Immutable headers (a fetched upstream response): copy instead.
    return new Response(result.body, {
      status: result.status,
      statusText: result.statusText,
      headers: applySecurityHeaders(new Headers(result.headers)),
    });
  }
}
