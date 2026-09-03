/**
 * Validation at the HTTP boundary.
 *
 * The Python worker validates its own arguments too — it is a separate process
 * and "the caller already checked" is not a security property. This layer exists
 * so that malformed input is rejected with a clear 400 before it crosses the
 * process boundary at all, and so the set of accepted values is written down in
 * one obvious place.
 *
 * Everything is an allow-list. Unknown keys are stripped rather than forwarded.
 */

import { z } from "zod";

export const StartSessionSchema = z
  .object({
    symbol: z.enum(["BTC-USD", "ETH-USD", "SIM-UP", "SIM-DOWN", "SIM-FLAT"]).default("BTC-USD"),
    source: z.enum(["public", "simulated"]).default("public"),
    timeframe: z.enum(["1m", "5m", "15m", "1h", "1d"]).default("5m"),
    bars: z.number().int().min(2).max(300).default(24),
    grok_frequency: z.number().int().min(1).max(60).default(8),
    warmup: z.number().int().min(0).max(120).default(8),
    continuous: z.boolean().default(true),
  })
  .strict();

export type StartSessionInput = z.infer<typeof StartSessionSchema>;

export const DecisionListSchema = z
  .object({
    limit: z.number().int().min(1).max(200).default(50),
    only_executed: z.boolean().default(false),
  })
  .strict();

export const DecisionIdSchema = z.object({ id: z.number().int().positive() }).strict();

/** Parse a request body, returning either the value or a 400 response. */
export async function parseBody<T extends z.ZodTypeAny>(
  request: Request,
  schema: T,
): Promise<{ ok: true; data: z.infer<T> } | { ok: false; response: Response }> {
  let raw: unknown = {};
  try {
    const text = await request.text();
    raw = text ? JSON.parse(text) : {};
  } catch {
    return {
      ok: false,
      response: Response.json(
        { ok: false, error: "Request body was not valid JSON.", live: false },
        { status: 400 },
      ),
    };
  }
  const parsed = schema.safeParse(raw);
  if (!parsed.success) {
    return {
      ok: false,
      response: Response.json(
        {
          ok: false,
          error: "Request failed validation.",
          issues: parsed.error.issues.map((i) => ({
            path: i.path.join("."),
            message: i.message,
          })),
          live: false,
        },
        { status: 400 },
      ),
    };
  }
  return { ok: true, data: parsed.data };
}
