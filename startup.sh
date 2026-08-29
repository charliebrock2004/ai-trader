#!/bin/sh
set -eu
cd /workspace
# :8081 is QA-only — a revive must never inherit a stale built-output preview.
node scripts/preview.mjs stop || true
# Free 8080 if a Python dashboard still owns it. Do not bind a second HTTP port.
python3 - <<'PY' || true
import os, signal
for pid in os.listdir("/proc"):
    if not pid.isdigit():
        continue
    try:
        raw = open(f"/proc/{pid}/cmdline", "rb").read()
        cmd = raw.replace(b"\0", b" ").decode()
    except OSError:
        continue
    if len(cmd) > 280:
        continue
    if " -m uvicorn ai_trader.dashboard.app:app" in cmd:
        try:
            os.kill(int(pid), signal.SIGTERM)
        except OSError:
            pass
PY
sleep 0.3
if curl -sf -o /dev/null --max-time 2 http://127.0.0.1:8080/; then
  exit 0
fi
npm run dev >>/tmp/app-startup.log 2>&1 &
