import assert from "node:assert/strict";
import { test } from "node:test";

import {
  applySecurityHeaders,
  CONTENT_SECURITY_POLICY_REPORT_ONLY,
  SECURITY_HEADERS,
} from "./security-headers.ts";

test("every response gets the baseline headers", () => {
  const headers = applySecurityHeaders(new Headers({ "content-type": "text/html" }));
  assert.equal(headers.get("x-content-type-options"), "nosniff");
  assert.equal(headers.get("referrer-policy"), "strict-origin-when-cross-origin");
  assert.match(headers.get("permissions-policy") ?? "", /payment=\(\)/);
  assert.equal(headers.get("content-type"), "text/html");
});

test("the content security policy is report-only, never enforced", () => {
  assert.equal(SECURITY_HEADERS["content-security-policy"], undefined);
  const headers = applySecurityHeaders(new Headers());
  assert.equal(headers.get("content-security-policy"), null);
  assert.equal(
    headers.get("content-security-policy-report-only"),
    CONTENT_SECURITY_POLICY_REPORT_ONLY,
  );
});

test("the policy allows what the app loads and nothing wider", () => {
  const csp = CONTENT_SECURITY_POLICY_REPORT_ONLY;
  assert.match(csp, /default-src 'self'/);
  assert.match(csp, /style-src [^;]*https:\/\/fonts\.googleapis\.com/);
  assert.match(csp, /font-src [^;]*https:\/\/fonts\.gstatic\.com/);
  assert.match(csp, /object-src 'none'/);
  assert.doesNotMatch(csp, /\*|unsafe-eval/);
});

test("a header a route already set is kept", () => {
  const headers = applySecurityHeaders(new Headers({ "referrer-policy": "no-referrer" }));
  assert.equal(headers.get("referrer-policy"), "no-referrer");
});
