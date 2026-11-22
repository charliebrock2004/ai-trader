import assert from "node:assert/strict";
import { test } from "node:test";
import { parseRankHistory, rankMove } from "./rank-history.ts";

const ok = {
  simulated: true,
  live: false,
  days: ["2026-11-21", "2026-11-22"],
  ranks: { a: [3, 1], b: [1, 2], c: [null, 3] },
};

test("a well-formed history parses and gives moves between the last two days", () => {
  const h = parseRankHistory(ok);
  assert.ok(h);
  assert.equal(rankMove(h, "a"), 2);
  assert.equal(rankMove(h, "b"), -1);
  assert.equal(rankMove(h, "c"), null);
  assert.equal(rankMove(h, "missing"), null);
});

test("one recorded day gives no move", () => {
  const h = parseRankHistory({ ...ok, days: ["2026-11-21"], ranks: { a: [1] } });
  assert.ok(h);
  assert.equal(rankMove(h, "a"), null);
});

test("anything malformed or not marked simulated is rejected", () => {
  for (const bad of [
    null,
    "x",
    { ...ok, live: true },
    { ...ok, simulated: undefined },
    { ...ok, days: ["yesterday", "today"] },
    { ...ok, ranks: { a: [1] } },
    { ...ok, ranks: { a: [1, "2"] } },
    { ...ok, ranks: { a: [0, 1] } },
    { ...ok, ranks: [] },
  ]) {
    assert.equal(parseRankHistory(bad), null);
  }
});
