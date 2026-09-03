import assert from "node:assert/strict";
import test from "node:test";
import { lastEligibleIndex, parseCoinbaseRows } from "./paper-core.ts";

test("parseCoinbaseRows keeps completed BTC candles and drops the forming bar", () => {
  const now = Date.UTC(2026, 7, 30, 12, 0, 0);
  const gran = 300;
  const rows = [];
  for (let i = 0; i < 24; i += 1) {
    const start = now / 1000 - gran * (24 - i);
    const price = 70000 + i;
    rows.push([start, price - 10, price + 10, price, price + 1, 1.5]);
  }
  rows.push([now / 1000, 70100, 70200, 70150, 70180, 1]);
  const candles = parseCoinbaseRows(rows, {
    product: "BTC-USD",
    granularity: gran,
    limit: 24,
    nowMs: now,
  });
  assert.equal(candles.length, 24);
  assert.ok(candles.every((c) => c.startMs + gran * 1000 <= now));
});

test("parseCoinbaseRows rejects an empty candle payload", () => {
  assert.throws(() => parseCoinbaseRows([], { product: "BTC-USD", granularity: 300, limit: 24 }));
});

test("lastEligibleIndex lands on a grok bar", () => {
  assert.equal(lastEligibleIndex(24, 8, 8), 23);
});
