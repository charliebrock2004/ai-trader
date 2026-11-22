/**
 * Response headers for every page and API route.
 *
 * The Content-Security-Policy is report-only: it logs violations in the browser
 * console and blocks nothing. Enforce it only after a deployed build has run
 * clean. Framing is restricted there too, not by X-Frame-Options, so an
 * embedded preview keeps working until that decision is made deliberately.
 */
export const CONTENT_SECURITY_POLICY_REPORT_ONLY = [
  "default-src 'self'",
  "script-src 'self' 'unsafe-inline'",
  "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
  "font-src 'self' https://fonts.gstatic.com",
  "img-src 'self' data:",
  "connect-src 'self'",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'self'",
].join("; ");

export const SECURITY_HEADERS: Record<string, string> = {
  "x-content-type-options": "nosniff",
  "referrer-policy": "strict-origin-when-cross-origin",
  "permissions-policy":
    "camera=(), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()",
  "content-security-policy-report-only": CONTENT_SECURITY_POLICY_REPORT_ONLY,
};

/** Adds the headers without replacing one a route already set. */
export function applySecurityHeaders(headers: Headers): Headers {
  for (const [name, value] of Object.entries(SECURITY_HEADERS)) {
    if (!headers.has(name)) headers.set(name, value);
  }
  return headers;
}
