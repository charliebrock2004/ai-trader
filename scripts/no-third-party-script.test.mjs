/**
 * Security (18 Nov 2026): no page may load the grok.com app-builder
 * "extensions.js" script. It ran with full access to every page, including
 * signed-in screens. These tests cover the head builder, the streaming
 * server middleware path (server/middleware/grok-pwa.ts uses
 * createHeadInjector) and the source tree, whatever VITE_GROK_EXTENSIONS says.
 */
import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { createHeadInjector, injectGrokPwaHead, stripGrokExtensionsScript } from "./grok-pwa-shared.mjs";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const SCRIPT = /grok-app-builder\/extensions\.js/i;
const PAGE = "<!doctype html><html><head><title>Inbox</title></head><body>private</body></html>";
const INJECTED = '<script src="https://grok.com/grok-app-builder/extensions.js" defer></script>';

function withFlag(value, fn) {
  const before = process.env.VITE_GROK_EXTENSIONS;
  if (value === undefined) delete process.env.VITE_GROK_EXTENSIONS;
  else process.env.VITE_GROK_EXTENSIONS = value;
  try {
    return fn();
  } finally {
    if (before === undefined) delete process.env.VITE_GROK_EXTENSIONS;
    else process.env.VITE_GROK_EXTENSIONS = before;
  }
}

function stream(chunks, ctx = {}) {
  const injector = createHeadInjector({ host: "app.example.test", ...ctx });
  const out = [];
  for (const chunk of chunks) out.push(...injector.push(chunk));
  out.push(...injector.flush());
  return Buffer.concat(out).toString("utf8");
}

for (const flag of [undefined, "1", "true", "0"]) {
  test(`the head builder never adds the script (VITE_GROK_EXTENSIONS=${flag ?? "unset"})`, () => {
    withFlag(flag, () => {
      for (const projectId of ["", "proj-123"]) {
        const out = injectGrokPwaHead(PAGE, { appName: "App", projectId });
        assert.doesNotMatch(out, SCRIPT);
        assert.match(out, /rel="manifest"/, "PWA manifest is kept");
      }
    });
  });

  test(`the streaming middleware path never adds the script (VITE_GROK_EXTENSIONS=${flag ?? "unset"})`, () => {
    withFlag(flag, () => {
      const html = stream([PAGE.slice(0, 20), PAGE.slice(20, 41), PAGE.slice(41)]);
      assert.doesNotMatch(html, SCRIPT);
      assert.match(html, /private/, "the page body passes through");
    });
  });
}

test("a copy already in the page is removed, in any letter case or attribute order", () => {
  for (const tag of [INJECTED, '<SCRIPT defer src="https://grok.com/grok-app-builder/extensions.js?v=2"></SCRIPT>', "<script data-project-id='x' src='//grok.com/grok-app-builder/extensions.js'></script>"]) {
    const page = PAGE.replace("</head>", `${tag}</head>`);
    assert.doesNotMatch(stripGrokExtensionsScript(page), SCRIPT);
    assert.doesNotMatch(injectGrokPwaHead(page, { appName: "App" }), SCRIPT);
    assert.doesNotMatch(stream([page.slice(0, 50), page.slice(50)]), SCRIPT);
  }
});

test("no source file contains the script URL", () => {
  const hits = [];
  const walk = (dir) => {
    for (const name of readdirSync(dir)) {
      if (["node_modules", ".vercel", ".output", ".git", "dist", ".venv", "__pycache__"].includes(name)) continue;
      const path = join(dir, name);
      if (statSync(path).isDirectory()) walk(path);
      else if (/\.(m?[jt]sx?|mts|html|py|json)$/.test(name) && !name.endsWith(".test.mjs") && !name.endsWith(".test.ts") && !/test_.*\.py$/.test(name)) {
        if (readFileSync(path, "utf8").includes("grok-app-builder/extensions.js")) hits.push(path.slice(ROOT.length + 1));
      }
    }
  };
  for (const top of ["src", "server", "scripts", "public"]) {
    try {
      walk(join(ROOT, top));
    } catch {
      // directory absent in this app
    }
  }
  assert.deepEqual(hits, []);
});
