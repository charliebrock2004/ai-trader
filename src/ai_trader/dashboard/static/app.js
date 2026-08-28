const stagesEl = document.getElementById("stages");
const modulesEl = document.getElementById("modules");
const countsEl = document.getElementById("counts");
const logEl = document.getElementById("log");
const haltCard = document.getElementById("halt-card");
const haltTitle = document.getElementById("halt-title");
const haltCopy = document.getElementById("halt-copy");
const haltMeta = document.getElementById("halt-meta");
const killToggle = document.getElementById("kill-toggle");
const dryRunBtn = document.getElementById("dry-run");
const modeChip = document.getElementById("mode-chip");
const footMeta = document.getElementById("foot-meta");

const MODULE_LABELS = {
  market_data: "Market data",
  analysis: "Analysis",
  ai: "Grok AI",
  risk: "Risk engine",
  broker_simulated: "Simulated broker",
  broker_alpaca: "Alpaca paper",
  database: "SQLite",
};

function chip(status) {
  return status || "";
}

function formatTime(value) {
  if (!value) return "";
  try {
    return new Date(value).toLocaleString();
  } catch {
    return value;
  }
}

async function fetchJson(url, options) {
  const response = await fetch(url, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || data.message || response.statusText);
  }
  return data;
}

function renderStages(architecture) {
  stagesEl.innerHTML = architecture
    .map(
      (stage, index) => `
      <li class="stage">
        <span class="stage-index">${String(index + 1).padStart(2, "0")}</span>
        <div>
          <h3>${stage.title}</h3>
          <p>${stage.detail}</p>
        </div>
        <span class="chip">${chip(stage.status)}</span>
      </li>`
    )
    .join("");
}

function renderModules(modules) {
  modulesEl.innerHTML = Object.entries(modules)
    .filter(([key]) => key !== "database")
    .map(([key, mod]) => {
      const ready = mod.ready || mod.connected ? "ready" : "held";
      return `
        <li>
          <div>
            <h3>${MODULE_LABELS[key] || key}</h3>
            <p>${mod.notes || mod.detail || ""}</p>
          </div>
          <span class="chip">${ready}</span>
        </li>`;
    })
    .join("");
}

function renderCounts(db) {
  const counts = db.counts || {};
  const keys = [
    ["ai_decisions", "Decisions"],
    ["trades", "Trades"],
    ["positions", "Positions"],
    ["events", "Events"],
    ["account_snapshots", "Snapshots"],
  ];
  countsEl.innerHTML = keys
    .map(
      ([key, label]) => `
      <div>
        <dt>${label}</dt>
        <dd>${counts[key] ?? 0}</dd>
      </div>`
    )
    .join("");
}

function renderLog(events) {
  if (!events.length) {
    logEl.innerHTML = '<p class="log-empty">Waiting for events.</p>';
    return;
  }
  logEl.innerHTML = events
    .slice(0, 40)
    .map(
      (event) => `
      <article class="log-row">
        <time>${formatTime(event.created_at)} · ${event.level} · ${event.event_type}</time>
        <div>${event.message}</div>
      </article>`
    )
    .join("");
}

function renderHalt(kill) {
  const engaged = Boolean(kill.engaged);
  haltCard.classList.toggle("is-clear", !engaged);
  haltTitle.textContent = engaged ? "Kill switch engaged" : "Kill switch clear";
  haltCopy.textContent = engaged
    ? "Pipeline halted. Dry runs and order paths are blocked until you disengage. Disengaging does not enable orders."
    : "Pipeline may run dry cycles. Order placement is still disabled in the broker and risk layers.";
  killToggle.textContent = engaged ? "Disengage kill switch" : "Engage kill switch";
  haltMeta.textContent = [kill.reason, formatTime(kill.at)].filter(Boolean).join(" · ");
  dryRunBtn.disabled = engaged;
  const lede = document.querySelector(".lede");
  if (lede) {
    lede.textContent = engaged
      ? "Market data, Grok, and Alpaca paper trading are wired as modules — not connected, not live. The risk engine sits between any future AI decision and execution. The kill switch is on."
      : "Market data, Grok, and Alpaca paper trading are wired as modules — not connected, not live. The risk engine sits between any future AI decision and execution. The kill switch is clear; orders are still blocked.";
  }
}

async function refresh() {
  const [status, events] = await Promise.all([
    fetchJson("/api/status"),
    fetchJson("/api/events"),
  ]);
  modeChip.textContent = status.trading_mode;
  renderHalt(status.kill_switch);
  renderStages(status.architecture);
  renderModules(status.modules);
  renderCounts(status.modules.database);
  renderLog(events.events || []);
  footMeta.textContent = `v${status.version} · live trading allowed: ${status.safety.live_trading_allowed}`;
}

killToggle.addEventListener("click", async () => {
  const status = await fetchJson("/api/status");
  const engaged = !status.kill_switch.engaged;
  killToggle.disabled = true;
  try {
    await fetchJson("/api/kill-switch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        engaged,
        reason: engaged ? "Engaged from dashboard" : "Disengaged from dashboard",
      }),
    });
    await refresh();
  } finally {
    killToggle.disabled = false;
  }
});

dryRunBtn.addEventListener("click", async () => {
  dryRunBtn.disabled = true;
  try {
    await fetchJson("/api/dry-run", { method: "POST" });
    await refresh();
  } catch (error) {
    haltMeta.textContent = error.message;
  } finally {
    dryRunBtn.disabled = false;
    const status = await fetchJson("/api/status");
    dryRunBtn.disabled = Boolean(status.kill_switch.engaged);
  }
});

refresh().catch((error) => {
  haltCopy.textContent = error.message;
});
