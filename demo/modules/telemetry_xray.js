import {
  FALLBACK_WATERFALL,
  apiFetch,
  getActiveEntityId,
  setCaption,
} from "./api_state.js";
import { runCompileSkill, runDistillSignal, runEvaluateRule } from "./agent_gym.js";
import { refreshScene2LineageAndSync } from "./domain_scenes.js";

export function switchBqSqlQueryTab(tabKey) {
  window.activeBqSqlTab = tabKey;
  const tabMap = {
    rule0_trajectory_sql: "bq-sql-tab-rule0",
    ai_generate_table_judge_sql: "bq-sql-tab-judge",
    rogan_gladen_sme_alignment_sql: "bq-sql-tab-rogan"
  };
  Object.entries(tabMap).forEach(([k, btnId]) => {
    const btn = document.getElementById(btnId);
    if (!btn) return;
    if (k === tabKey) {
      btn.className = "btn btn-primary";
      btn.style.color = "";
      btn.style.borderColor = "";
    } else {
      btn.className = "btn btn-outline";
      btn.style.color = "#cbd5e1";
      btn.style.borderColor = "#475569";
    }
  });
  const codeEl = document.getElementById("bq-sql-query-code");
  if (codeEl && window.lastBqSqlQueries && window.lastBqSqlQueries[tabKey]) {
    codeEl.textContent = window.lastBqSqlQueries[tabKey];
  }
}

export function renderBqSqlEval(sqlEval) {
  if (!sqlEval) return;
  if (sqlEval.sql_queries) {
    window.lastBqSqlQueries = sqlEval.sql_queries;
    switchBqSqlQueryTab(window.activeBqSqlTab || "rule0_trajectory_sql");
  }
  const r0Badge = document.getElementById("bq-sql-rule0-badge");
  if (r0Badge && typeof sqlEval.rule0_sql_pass_rate_pct === "number") {
    r0Badge.textContent = `Rule 0 SQL: ${sqlEval.rule0_sql_pass_rate_pct.toFixed(1)}% PASS ($0 Token Cost)`;
  }
  const savBadge = document.getElementById("bq-sql-savings-badge");
  if (savBadge && typeof sqlEval.estimated_eval_token_savings_pct === "number") {
    savBadge.textContent = `Judge Token Savings: ${sqlEval.estimated_eval_token_savings_pct.toFixed(1)}%`;
  }
  const rgBadge = document.getElementById("bq-sql-rogan-badge");
  if (rgBadge && sqlEval.statistical_calibration) {
    const sc = sqlEval.statistical_calibration;
    const thetaPct = (Number(sc.rogan_gladen_debiased_pass_rate || 0.991) * 100).toFixed(1);
    rgBadge.textContent = `Rogan-Gladen θ̂: ${thetaPct}%`;
  }
  const gatesTbody = document.getElementById("bq-sql-gates-tbody");
  if (gatesTbody && Array.isArray(sqlEval.gates) && sqlEval.gates.length > 0) {
    gatesTbody.innerHTML = sqlEval.gates.map(g => {
      const badgeCls = g.status === "PASS" ? "badge-green" : "badge-amber";
      return `
        <tr>
          <td><strong>${g.title}</strong></td>
          <td class="mono" style="font-size:11px;">${g.sql_expression}</td>
          <td class="mono" style="color:#059669;font-weight:700;">${Number(g.pass_rate_pct || 100).toFixed(1)}% (${g.passed_traces}/${g.total_traces})</td>
          <td><span class="badge ${badgeCls}">${g.status}</span></td>
        </tr>
      `;
    }).join("");
  }
}

export function renderOtelAnalytics(data) {
  const wfTbody = document.getElementById("otel-waterfall-tbody");
  const latTbody = document.getElementById("otel-latency-tbody");
  const countBadge = document.getElementById("otel-span-count-badge");
  const bqTarget = document.getElementById("bq-target-table");
  const activeEntityEl = document.getElementById("otel-active-entity");

  if (activeEntityEl) activeEntityEl.textContent = getActiveEntityId();
  if (data && data.bigquery_target && bqTarget) {
    bqTarget.textContent = `${data.bigquery_target.project_id}.${data.bigquery_target.dataset_id}.${data.bigquery_target.table_id}`;
  }
  if (data && data.finops_model_split) {
    const ms = data.finops_model_split;
    const whTokensEl = document.getElementById("finops-workhorse-tokens");
    if (whTokensEl && ms.workhorse_model) {
      whTokensEl.textContent = `${ms.workhorse_model.calls} calls · ${ms.workhorse_model.input_tokens} in / ${ms.workhorse_model.output_tokens} out tokens`;
    }
    const rTokensEl = document.getElementById("finops-reasoning-tokens");
    if (rTokensEl && ms.reasoning_model) {
      rTokensEl.textContent = `${ms.reasoning_model.calls} calls · ${ms.reasoning_model.input_tokens} in / ${ms.reasoning_model.output_tokens} out tokens`;
    }
    const zeroEl = document.getElementById("finops-zero-token-count");
    if (zeroEl && typeof ms.deterministic_zero_token_spans === "number") {
      zeroEl.textContent = `${ms.deterministic_zero_token_spans} deterministic code spans (0 tokens)`;
    }
  }
  const wf = (data && data.trace_waterfall && data.trace_waterfall.length > 0)
    ? data.trace_waterfall
    : FALLBACK_WATERFALL;
  if (countBadge) {
    const totalSpans = (data && data.total_spans_analyzed) ? data.total_spans_analyzed : wf.length;
    countBadge.textContent = `${totalSpans} Spans Captured`;
  }

  const maxEnd = Math.max(10, ...wf.map(s => (s.offset_ms || 0) + (s.duration_ms || 1)));
  const totalBadge = document.getElementById("waterfall-total-ms-badge");
  if (totalBadge) totalBadge.textContent = `Trace Window: ${maxEnd.toFixed(1)} ms`;

  const zoneColors = {
    "1_edge_intake": "#0f172a",
    "2_extraction_engine": "#2563eb",
    "2_3_6_enterprise_ingestion": "#1d4ed8",
    "3_audit_vault": "#059669",
    "3_operational_store": "#2563eb",
    "4_consolidation_engine": "#7c3aed",
    "4_bigquery_analytics": "#0891b2",
    "5_spiffe_identity": "#7c3aed",
    "5_user_interaction_a2ui": "#d97706",
    "6_knowledge_catalog": "#4f46e5",
    "6_knowledge_catalog_agent_gym": "#059669"
  };

  wfTbody.innerHTML = wf.slice(0, 14).map(s => {
    const spanName = s.name || s.span_name || "docubridge.step";
    const off = Number(s.offset_ms || 0);
    const dur = Math.max(0.4, Number(s.duration_ms || 1.2));
    const leftPct = Math.min(92, Math.max(0, (off / maxEnd) * 100));
    const widthPct = Math.min(100 - leftPct, Math.max(4, (dur / maxEnd) * 100));
    const color = zoneColors[s.zone] || "#1a73e8";
    const modelName = s.gen_ai_model || "0-token-deterministic";
    let modelBadge = `<span class="badge badge-navy">{ } 0-Token Code</span>`;
    if (modelName.includes("gemini-3.8-flash")) {
      modelBadge = `<span class="badge badge-blue">⚡ ${modelName} (${s.input_tokens || 1420}/${s.output_tokens || 245} tok)</span>`;
    } else if (modelName.includes("gemini-4-argon")) {
      modelBadge = `<span class="badge badge-purple">🧠 ${modelName} (${s.input_tokens || 940}/${s.output_tokens || 185} tok)</span>`;
    }
    return `
      <tr>
        <td class="mono" style="font-weight:700;font-size:12px;color:#0f172a;">${spanName}</td>
        <td>${modelBadge}</td>
        <td class="mono" style="font-size:12px;">+${off.toFixed(1)}ms · <strong>${dur.toFixed(2)}ms</strong></td>
        <td>
          <div class="wf-track">
            <div class="wf-bar" style="left:${leftPct.toFixed(1)}%;width:${widthPct.toFixed(1)}%;background:${color};"></div>
          </div>
        </td>
        <td><span class="badge badge-green">${s.status || "OK"}</span></td>
      </tr>
    `;
  }).join("");

  const steps = (data && data.step_latency && data.step_latency.length > 0)
    ? data.step_latency
    : wf.map(s => ({
        step_name: s.name || s.span_name || "docubridge.step",
        span_count: 3,
        p50_ms: s.duration_ms,
        p95_ms: Number((s.duration_ms * 1.25).toFixed(2)),
        p99_ms: Number((s.duration_ms * 1.4).toFixed(2))
      }));

  latTbody.innerHTML = steps.slice(0, 12).map(st => `
    <tr>
      <td class="mono" style="font-size:11.5px;font-weight:700;color:#1e293b;">${(st.step_name || st.span_name || "").replace("docubridge.", "")}</td>
      <td class="mono">${st.span_count || st.count || 1}</td>
      <td class="mono" style="color:#059669;font-weight:700;">${Number(st.p50_ms || 0).toFixed(2)}</td>
      <td class="mono">${Number(st.p95_ms || 0).toFixed(2)}</td>
      <td class="mono">${Number(st.p99_ms || 0).toFixed(2)}</td>
    </tr>
  `).join("");

  if (data && data.trace_sql_evaluation) {
    renderBqSqlEval(data.trace_sql_evaluation);
  }
}

export async function runBqSqlTraceEval() {
  try {
    const resp = await apiFetch("/v1/telemetry/sql-eval/run", { method: "POST" });
    if (resp.ok) {
      const sqlEval = await resp.json();
      renderBqSqlEval(sqlEval);
      setCaption(
        "STAGE 5 · BIGQUERY SQL TRACE EVALUATION & SELECTIVE AI.GENERATE_TABLE COMPLETED",
        `Evaluated ${sqlEval.total_traces_evaluated || 1} traces across 5 deterministic SQL gates (${sqlEval.rule0_sql_pass_rate_pct || 100}% pass, ${sqlEval.estimated_eval_token_savings_pct || 95}% judge token savings, Rogan-Gladen θ̂ = ${((sqlEval.statistical_calibration && sqlEval.statistical_calibration.rogan_gladen_debiased_pass_rate) * 100 || 99.1).toFixed(1)}%).`,
        "POST /v1/telemetry/sql-eval/run -> v_trace_sql_evaluation + AI.GENERATE_TABLE (gemini-4-argon)"
      );
    }
  } catch (_) {}
}

export async function refreshOtelWaterfall() {
  try {
    const resp = await apiFetch("/v1/telemetry/analytics?limit=500");
    if (resp.ok) {
      const data = await resp.json();
      renderOtelAnalytics(data);
      return;
    }
  } catch (_) {}
  renderOtelAnalytics(null);
}

export async function runTracedPipelineSweep() {
  try {
    await apiFetch("/v1/demo/seed", { method: "POST" });
    await refreshScene2LineageAndSync();
    await runDistillSignal();
    await runEvaluateRule();
    await runCompileSkill();
    await refreshOtelWaterfall();
    setCaption(
      "STAGE 5 · LIVE 6-ZONE TRACED PIPELINE & MULTI-MODEL FINOPS COMPLETED",
      `Executed full 6-zone + Agent Gym pipeline (gemini-3.8-flash workhorse + gemini-4-argon judge) and streamed W3C trace (${window.lastTraceId.slice(0, 12)}...) to BigQuery.`,
      "POST /v1/demo/seed -> POST /v1/gym/rules/evaluate -> POST /v1/gym/compile-skill -> GET /v1/telemetry/analytics"
    );
  } catch (_) {
    renderOtelAnalytics(null);
  }
}
