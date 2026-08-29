function money(value, currency) {
  const n = Number(value);
  const v = Number.isFinite(n) ? n : 0;
  const sign = v < 0 ? "−" : "";
  const symbol = currency === "USD" ? "$" : "£";
  return `${sign}${symbol}${Math.abs(v).toFixed(2)}`;
}

function positionLabel(pos) {
  if (!pos || pos === "flat") return "flat";
  const qty = pos.quantity != null ? pos.quantity : "";
  return `${pos.symbol || ""} ${pos.side || "LONG"} ${qty}`.trim();
}

function setText(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value;
}

function grokLabel(status) {
  if (status.running && !status.stopped) return "RUNNING";
  if (status.grok === "RUNNING" || status.grok === "ACTIVE") return "RUNNING";
  return "STOPPED";
}

function render(status) {
  const grok = grokLabel(status);
  const currency = status.currency || "GBP";
  const decision = (status.current_decision || status.decision || "HOLD").toUpperCase();
  setText("paper-kicker", `Grok ${grok}`);
  setText("decision", decision);
  setText("tile-balance", money(status.balance, currency));
  setText("tile-pnl", money(status.today_pnl, currency));
  setText("tile-position", positionLabel(status.position));
}

async function fetchJson(url, options) {
  const response = await fetch(url, options);
  const raw = await response.text();
  let data = {};
  try {
    data = raw ? JSON.parse(raw) : {};
  } catch {
    throw new Error(raw && !raw.trim().startsWith("<") ? raw.slice(0, 240) : `Request failed (${response.status}).`);
  }
  if (!response.ok) {
    throw new Error(data.data_error || data.detail || data.message || `Request failed (${response.status}).`);
  }
  return data;
}

const note = document.getElementById("paper-note");
const startBtn = document.getElementById("session-start");
const stopBtn = document.getElementById("session-stop");
let poll = null;

function startPoll() {
  if (poll) return;
  poll = setInterval(async () => {
    try {
      const status = await fetchJson("/api/paper-session");
      render(status);
      if (status.data_error && note) note.textContent = status.data_error;
      if (!status.running && poll) {
        clearInterval(poll);
        poll = null;
      }
    } catch (error) {
      if (note) note.textContent = error.message;
    }
  }, 2000);
}

if (startBtn) {
  startBtn.addEventListener("click", async () => {
    startBtn.disabled = true;
    if (note) note.textContent = "";
    try {
      const status = await fetchJson("/api/paper-session/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          symbol: "BTC-USD",
          source: "public",
          bars: 24,
          timeframe: "5m",
          grok_frequency: 8,
          warmup: 8,
          continuous: true,
        }),
      });
      render(status);
      if (note) {
        note.textContent = status.data_error ? status.data_error : "";
      }
      if (status.running) startPoll();
    } catch (error) {
      if (note) note.textContent = error.message;
    } finally {
      startBtn.disabled = false;
    }
  });
}

if (stopBtn) {
  stopBtn.addEventListener("click", async () => {
    stopBtn.disabled = true;
    try {
      const status = await fetchJson("/api/paper-session/stop", { method: "POST" });
      render(status);
      if (note) note.textContent = "Stopped. New paper trades blocked.";
      if (poll) {
        clearInterval(poll);
        poll = null;
      }
    } catch (error) {
      if (note) note.textContent = error.message;
    } finally {
      stopBtn.disabled = false;
    }
  });
}

fetchJson("/api/paper-session")
  .then((status) => {
    render(status);
    if (status.running) startPoll();
  })
  .catch(() => {});
