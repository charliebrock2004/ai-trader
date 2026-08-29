#!/usr/bin/env node
/**
 * Paper-session engine sidecar. FastAPI on 127.0.0.1:8090.
 * Never binds 8080. Never live trading.
 */
import { spawn } from "node:child_process";
import { writeFileSync, readFileSync, existsSync, unlinkSync } from "node:fs";

const PORT = Number(process.env.PAPER_ENGINE_PORT || 8090);
const HOST = "127.0.0.1";
export const ENGINE_URL = process.env.PAPER_ENGINE_URL || `http://${HOST}:${PORT}`;
const PIDFILE = "/tmp/ai-trader-paper-engine.pid";
const HEALTH = `${ENGINE_URL}/api/health`;

export async function engineHealthy() {
  try {
    const res = await fetch(HEALTH, { signal: AbortSignal.timeout(800) });
    return res.ok;
  } catch {
    return false;
  }
}

function grokPaperFlag() {
  if (process.env.GROK_PAPER_ANALYSIS) return process.env.GROK_PAPER_ANALYSIS;
  return process.env.XAI_API_KEY ? "true" : "false";
}

export async function startPaperEngine() {
  if (await engineHealthy()) return true;
  const child = spawn(
    "python3",
    [
      "-m",
      "uvicorn",
      "ai_trader.dashboard.app:app",
      "--host",
      HOST,
      "--port",
      String(PORT),
      "--log-level",
      "warning",
    ],
    {
      cwd: process.cwd(),
      env: {
        ...process.env,
        PYTHONPATH: `${process.cwd()}/src`,
        GROK_PAPER_ANALYSIS: grokPaperFlag(),
        TRADING_MODE: process.env.TRADING_MODE || "simulate",
        DASHBOARD_PORT: String(PORT),
        KILL_SWITCH_ENGAGED: process.env.KILL_SWITCH_ENGAGED || "true",
      },
      detached: true,
      stdio: "ignore",
    },
  );
  child.unref();
  if (child.pid) writeFileSync(PIDFILE, String(child.pid));
  const deadline = Date.now() + 15000;
  while (Date.now() < deadline) {
    if (await engineHealthy()) return true;
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  return false;
}

export async function stopPaperEngine() {
  if (existsSync(PIDFILE)) {
    const pid = Number(readFileSync(PIDFILE, "utf8"));
    if (pid) {
      try {
        process.kill(pid, "SIGTERM");
      } catch {
        /* already gone */
      }
    }
    try {
      unlinkSync(PIDFILE);
    } catch {
      /* ignore */
    }
  }
}

const command = process.argv[2];
if (command === "start") {
  const ok = await startPaperEngine();
  if (!ok) {
    console.error("paper engine failed to start");
    process.exit(1);
  }
} else if (command === "stop") {
  await stopPaperEngine();
} else if (command === "health") {
  process.exit((await engineHealthy()) ? 0 : 1);
}
