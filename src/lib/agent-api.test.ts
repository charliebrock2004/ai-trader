/**
 * Frontend contract tests.
 *
 * These assert the two properties the UI must have: it never invents a number,
 * and it renders what the engine actually returned.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  ApiError,
  ago,
  api,
  money,
  percent,
  points,
  probability,
  signedMoney,
} from "./agent-api.ts";

type FetchLike = typeof globalThis.fetch;

function withFetch(handler: (input: string) => { status: number; body: unknown }, run: () => Promise<void>) {
  const original = globalThis.fetch;
  globalThis.fetch = (async (input: RequestInfo | URL) => {
    const { status, body } = handler(String(input));
    return new Response(JSON.stringify(body), {
      status,
      headers: { "content-type": "application/json" },
    });
  }) as FetchLike;
  return run().finally(() => {
    globalThis.fetch = original;
  });
}

// ---------------------------------------------------------------------------
// Formatting never invents a value
// ---------------------------------------------------------------------------
test("missing values render as an em dash, never as zero", () => {
  for (const formatter of [money, signedMoney, probability, points]) {
    assert.equal(formatter(null), "—");
    assert.equal(formatter(undefined), "—");
  }
  assert.equal(percent(null), "—");
  assert.equal(ago(null), "—");
});

test("NaN and Infinity are not rendered as numbers", () => {
  assert.equal(money(Number.NaN), "—");
  assert.equal(money(Number.POSITIVE_INFINITY), "—");
  assert.equal(probability(Number.NaN), "—");
});

test("money formats to the penny with the right symbol", () => {
  assert.equal(money(100), "£100.00");
  assert.equal(money(1.5, "USD"), "$1.50");
  assert.equal(money(-2.5), "−£2.50");
});

test("signed money makes gains and losses unmistakable", () => {
  assert.equal(signedMoney(4.2), "+£4.20");
  assert.equal(signedMoney(-4.2), "−£4.20");
  assert.equal(signedMoney(0), "£0.00");
});

test("probability and edge render in the units people read them in", () => {
  assert.equal(probability(0.945), "94.5%");
  assert.equal(points(0.0175), "+1.75pp");
  assert.equal(points(-0.02), "−2.00pp");
});

// ---------------------------------------------------------------------------
// The client surfaces failures instead of hiding them
// ---------------------------------------------------------------------------
test("a 503 from the engine raises rather than returning empty state", async () => {
  await withFetch(
    () => ({ status: 503, body: { ok: false, error: "Paper engine could not start." } }),
    async () => {
      await assert.rejects(
        () => api.agent(),
        (error: unknown) => {
          assert.ok(error instanceof ApiError);
          assert.equal(error.status, 503);
          assert.match(error.message, /could not start/);
          return true;
        },
      );
    },
  );
});

test("a non-JSON response raises rather than being coerced", async () => {
  const original = globalThis.fetch;
  globalThis.fetch = (async () =>
    new Response("<html>gateway</html>", { status: 502 })) as FetchLike;
  try {
    await assert.rejects(() => api.agent(), /not JSON/);
  } finally {
    globalThis.fetch = original;
  }
});

// ---------------------------------------------------------------------------
// Real state passes through unchanged
// ---------------------------------------------------------------------------
test("agent status is returned as the engine reported it", async () => {
  const payload = {
    ok: true,
    alive: true,
    terminated: false,
    survival: { state: "CAUTION", equity: 84, terminal_threshold: 40, life_remaining_pct: 73.3 },
    account: { equity: 84, base_currency: "GBP" },
    decisions: { TOTAL: 12, EXECUTED: 1 },
  };
  await withFetch(
    (url) => {
      assert.equal(url, "/api/agent");
      return { status: 200, body: payload };
    },
    async () => {
      const status = await api.agent();
      assert.equal(status.survival.state, "CAUTION");
      assert.equal(status.account.equity, 84);
      assert.equal(status.decisions.TOTAL, 12);
    },
  );
});

test("a terminated agent is reported as terminated", async () => {
  await withFetch(
    () => ({
      status: 200,
      body: {
        ok: true,
        alive: false,
        terminated: true,
        survival: { state: "TERMINAL", terminated: true, life_remaining_pct: 0 },
      },
    }),
    async () => {
      const status = await api.agent();
      assert.equal(status.terminated, true);
      assert.equal(status.survival.state, "TERMINAL");
      assert.equal(status.survival.life_remaining_pct, 0);
    },
  );
});

test("the decision list keeps rejections, not just trades", async () => {
  await withFetch(
    () => ({
      status: 200,
      body: {
        decisions: [
          { id: 2, final_action: "HOLD", executed: 0, notes: "Net edge below the minimum." },
          { id: 1, final_action: "BUY", executed: 1, notes: "Cleared every check." },
        ],
      },
    }),
    async () => {
      const { decisions } = await api.decisions();
      assert.equal(decisions.length, 2);
      assert.equal(decisions.filter((d) => !d.executed).length, 1);
      assert.match(decisions[0].notes ?? "", /below the minimum/);
    },
  );
});

test("performance carries the evidence note through untouched", async () => {
  const note = "No completed trades. Nothing here supports any claim about edge.";
  await withFetch(
    () => ({
      status: 200,
      body: { trades: 0, evidence_note: note, calibration: { count: 0, buckets: [], verdict: "x" } },
    }),
    async () => {
      const performance = await api.performance();
      assert.equal(performance.evidence_note, note);
      assert.equal(performance.trades, 0);
    },
  );
});

test("system status reports a broken component as broken", async () => {
  await withFetch(
    () => ({
      status: 200,
      body: {
        ok: false,
        components: [
          { id: "agent", title: "Agent", ok: true, detail: "Alive." },
          { id: "events", title: "Event source", ok: false, detail: "Unreachable." },
        ],
        control_enabled: false,
      },
    }),
    async () => {
      const system = await api.system();
      assert.equal(system.ok, false);
      assert.equal(system.components.filter((c) => !c.ok).length, 1);
      assert.equal(system.control_enabled, false);
    },
  );
});

test("start posts to the paper-session start endpoint", async () => {
  await withFetch(
    (url) => {
      assert.equal(url, "/api/paper-session/start");
      return {
        status: 200,
        body: {
          ok: true,
          running: true,
          stopped: false,
          grok: "STARTING",
          status: "STARTING",
          balance: 100,
          live: false,
          engine: "python-worker",
          account: { equity: 100, base_currency: "GBP" },
        },
      };
    },
    async () => {
      const status = await api.start();
      assert.equal(status.running, true);
      assert.equal(status.engine, "python-worker");
      assert.equal(status.balance, 100);
    },
  );
});

test("stop posts to the paper-session stop endpoint", async () => {
  await withFetch(
    (url) => {
      assert.equal(url, "/api/paper-session/stop");
      return {
        status: 200,
        body: { ok: true, running: false, stopped: true, grok: "STOPPED", live: false },
      };
    },
    async () => {
      const status = await api.stop();
      assert.equal(status.stopped, true);
      assert.equal(status.grok, "STOPPED");
    },
  );
});
