const labels = {
  GROK: "Grok",
  BUY_AND_HOLD: "Buy & hold",
  SIMPLE_TECHNICAL: "Simple technical",
  RANDOM_BASELINE: "Random",
};

function pct(value) {
  const n = Number(value) || 0;
  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toFixed(2)}%`;
}

function dd(value) {
  return `${((Number(value) || 0) * 100).toFixed(2)}%`;
}

function wr(value) {
  return `${((Number(value) || 0) * 100).toFixed(0)}%`;
}

function pf(value) {
  const n = Number(value) || 0;
  if (n >= 100) return "—";
  return n.toFixed(2);
}

function verdictText(report) {
  const headline = report.headline || {};
  const v = report.verdict || {};
  if (!headline.trades || v.grok_traded === false) {
    return "Grok did not take a trade. Sitting in cash is not a measured edge.";
  }
  if (v.beats_all) return "Grok beat every baseline on out-of-sample data.";
  if (v.beats_buy_and_hold) return "Grok beat buy-and-hold. It did not beat every baseline.";
  return "Grok did not beat buy-and-hold on out-of-sample data.";
}

function render(report) {
  const headline = report.headline || {};
  document.getElementById("perf-kicker").textContent =
    `Out of sample · ${headline.grok_model || report.grok_model || "fixture-hold"}`;
  document.getElementById("perf-verdict").textContent = verdictText(report);
  document.getElementById("tile-grok").textContent = pct(headline.grok_return_pct);
  document.getElementById("tile-bench").textContent = pct(headline.benchmark_return_pct);
  document.getElementById("tile-dd").textContent = dd(headline.maximum_drawdown);
  document.getElementById("tile-trades").textContent = String(headline.trades ?? "—");
  document.getElementById("tile-win").textContent = wr(headline.win_rate);
  document.getElementById("tile-pf").textContent = pf(headline.profit_factor);

  const body = document.querySelector("#perf-table tbody");
  body.innerHTML = (report.comparison || [])
    .map(
      (row) => `
      <tr>
        <td>${labels[row.strategy] || row.strategy}</td>
        <td>${pct(row.return_pct)}</td>
        <td>${dd(row.maximum_drawdown)}</td>
        <td>${row.trades ?? 0}</td>
        <td>${wr(row.win_rate)}</td>
        <td>${pf(row.profit_factor)}</td>
      </tr>`
    )
    .join("");

  const markets = report.splits && report.splits.out_of_sample && report.splits.out_of_sample.markets;
  const detail = document.getElementById("perf-detail");
  if (markets) {
    const lines = Object.entries(markets).map(([symbol, by]) => {
      const g = by.GROK || {};
      const h = by.BUY_AND_HOLD || {};
      const t = by.SIMPLE_TECHNICAL || {};
      const r = by.RANDOM_BASELINE || {};
      return `${symbol}: Grok ${pct(g.return_pct)} · Hold ${pct(h.return_pct)} · Tech ${pct(t.return_pct)} · Random ${pct(r.return_pct)}`;
    });
    detail.textContent = lines.join(" · ");
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

const note = document.getElementById("perf-note");
const button = document.getElementById("run-benchmark");

button.addEventListener("click", async () => {
  button.disabled = true;
  note.textContent = "Running paper benchmark…";
  try {
    const report = await fetchJson("/api/benchmark", { method: "POST" });
    render(report);
    note.textContent = "Paper only. Broker not used.";
  } catch (error) {
    note.textContent = error.message;
  } finally {
    button.disabled = false;
  }
});

fetchJson("/api/benchmark")
  .then((report) => {
    if (report && report.headline) render(report);
  })
  .catch(() => {
    note.textContent = "Run the paper benchmark to fill this page.";
  });
