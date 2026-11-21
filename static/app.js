/* Dashboard: packet → two gates → cited draft. Synthetic demo only. */

let currentRun = null;

async function api(path, opts) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

function setPipeline(step) {
  const order = ["scan", "validate", "gate1", "draft", "gate2", "log"];
  const idx = order.indexOf(step);
  document.querySelectorAll("#pipeline li").forEach((el) => {
    const s = el.getAttribute("data-step");
    const i = order.indexOf(s);
    el.classList.toggle("active", i === idx);
    el.classList.toggle("done", i < idx);
  });
}

function verdictClass(v) {
  if (v === "BLOCK") return "badge-block";
  if (v === "HOLD") return "badge-hold";
  if (v === "ALLOW") return "badge-allow";
  return "";
}

function renderResult(result) {
  currentRun = result;
  const badge = document.getElementById("verdict-badge");
  const v = result.guardrail_verdict || result.status || "unknown";
  badge.textContent = v;
  badge.className = `badge ${verdictClass(result.guardrail_verdict)}`;

  const meta = document.getElementById("result-meta");
  meta.innerHTML = [
    ["run", result.run_id],
    ["trace", result.trace_id],
    ["status", result.status],
    ["reason", result.reason_code],
    ["cost", `$${(result.cost_usd || 0).toFixed(2)}`],
  ]
    .map(([k, val]) => `<div><span>${k}</span><strong>${val ?? "—"}</strong></div>`)
    .join("");

  document.getElementById("validation-box").textContent = JSON.stringify(
    result.validation || result.input_scan || {},
    null,
    2
  );

  const draft = result.draft || {};
  document.getElementById("draft-box").textContent = draft.draft_text
    || draft.message
    || (result.status === "blocked"
      ? `Blocked by guardrail: ${result.reason_code}`
      : "No draft (abstained or waiting on a gate).");

  const awaiting1 = result.status === "awaiting_gate1";
  const awaiting2 = result.status === "awaiting_gate2" || result.status === "draft_ready";
  document.querySelectorAll('#gate1-row .gate-btn').forEach((b) => {
    b.disabled = !awaiting1 && result.status !== "gate1_approved";
    if (result.gate1_decision) b.disabled = true;
    if (awaiting1) b.disabled = false;
  });
  document.querySelectorAll('#gate2-row .gate-btn').forEach((b) => {
    b.disabled = !awaiting2;
  });

  if (result.status === "blocked") setPipeline("scan");
  else if (result.abstained) setPipeline("validate");
  else if (result.status === "awaiting_gate1") setPipeline("gate1");
  else if (result.status === "awaiting_gate2" || result.status === "draft_ready") setPipeline("gate2");
  else if (result.status === "logged") setPipeline("log");
  else if (result.draft?.ok) setPipeline("draft");
  else setPipeline("validate");
}

async function loadPackets() {
  const packets = await api("/api/packets");
  const sel = document.getElementById("packet-select");
  sel.innerHTML = packets
    .map((p) => {
      const tags = (p.scenario_tags || []).join(",");
      return `<option value="${p.id}">${p.id} · ${tags || "packet"}</option>`;
    })
    .join("");
}

async function loadEval() {
  try {
    const report = await api("/api/evals/latest?mode=stub");
    const m = report.metrics;
    document.getElementById("eval-table").innerHTML = `
      <table>
        <tr><th>Metric</th><th>Value</th></tr>
        <tr><td>Draft factual accuracy</td><td>${(m.draft_factual_accuracy * 100).toFixed(2)}%</td></tr>
        <tr><td>Abstention (unanswerable)</td><td>${(m.abstention_rate_unanswerable * 100).toFixed(2)}%</td></tr>
        <tr><td>Injection block rate</td><td>${m.injection_block_rate}</td></tr>
        <tr><td>Citation coverage</td><td>${(m.citation_coverage * 100).toFixed(2)}%</td></tr>
        <tr><td>Pause detection</td><td>${(m.pause_detection_rate * 100).toFixed(2)}%</td></tr>
        <tr><td>LLM cost</td><td>$${Number(m.llm_cost_usd).toFixed(2)}</td></tr>
        <tr><td>Passed</td><td>${m.passed}/${m.n_cases}</td></tr>
      </table>`;
    document.getElementById("eval-note").textContent =
      `Loaded from committed stub report (${report.timestamp}). Synthetic data.`;
  } catch (err) {
    document.getElementById("eval-note").textContent = `No eval report yet: ${err.message}`;
  }
}

document.getElementById("run-btn").addEventListener("click", async () => {
  const packet_id = document.getElementById("packet-select").value;
  setPipeline("scan");
  // Stop before gates so the human can click Gate 1 / Gate 2 in the UI.
  const result = await api("/api/run", {
    method: "POST",
    body: JSON.stringify({ packet_id, auto_gates: false }),
  });
  renderResult(result);
});

document.querySelectorAll(".gate-btn").forEach((btn) => {
  btn.addEventListener("click", async () => {
    if (!currentRun?.run_id) return;
    const gate = btn.getAttribute("data-gate");
    const decision = btn.getAttribute("data-decision");
    // If still mid-graph awaiting gate1, re-run with decision.
    if (currentRun.status === "awaiting_gate1" && gate === "gate1") {
      const result = await api("/api/run", {
        method: "POST",
        body: JSON.stringify({
          packet_id: currentRun.packet_id || document.getElementById("packet-select").value,
          gate1_decision: decision,
          auto_gates: false,
        }),
      });
      // Preserve packet_id on result for gate2
      result.packet_id = result.packet_id || document.getElementById("packet-select").value;
      renderResult(result);
      return;
    }
    if (
      (currentRun.status === "awaiting_gate2" || currentRun.status === "draft_ready") &&
      gate === "gate2"
    ) {
      const result = await api("/api/run", {
        method: "POST",
        body: JSON.stringify({
          packet_id: document.getElementById("packet-select").value,
          gate1_decision: "approve",
          gate2_decision: decision,
          auto_gates: false,
        }),
      });
      renderResult(result);
      return;
    }
    // Fallback: persist decision on existing run via review API
    const updated = await api(`/api/runs/${currentRun.run_id}/gate`, {
      method: "POST",
      body: JSON.stringify({
        gate,
        decision,
        reason: `Dashboard ${gate} ${decision}`,
        actor: "adjuster",
      }),
    });
    renderResult({ ...currentRun, ...updated, draft: updated.draft || currentRun.draft });
  });
});

loadPackets();
loadEval();
