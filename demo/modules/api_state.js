export const FALLBACK_ASSETS = [
  { asset_id: "asset.cloud_storage_audit_vault", zone: "audit_vault", physical_uri: "gs://docubridge-audit-vault/vault/", owner_domain: "enterprise-governance", classification: "internal_only" },
  { asset_id: "asset.alloydb_operational_store", zone: "operational_store", physical_uri: "alloydb://docubridge-enterprise-data/operational/bridge_operational_facts", owner_domain: "data-engineering", classification: "internal_only" },
  { asset_id: "asset.view_underwriter_exposure", zone: "business_view", physical_uri: "alloydb://docubridge-enterprise-data/views/v_underwriter_exposure", owner_domain: "underwriting", classification: "internal_only" },
  { asset_id: "asset.bigquery_iceberg_facts", zone: "bigquery_iceberg", physical_uri: "bigquery://docubridge_lakehouse.bq_operational_facts_iceberg", owner_domain: "enterprise-analytics", classification: "internal_only" },
  { asset_id: "asset.gym_trust_policy_registry", zone: "knowledge_catalog_gym", physical_uri: "alloydb://docubridge-enterprise-data/gym/trust_policy_registry", owner_domain: "agent-gym-governance", classification: "internal_only" }
];

export const FALLBACK_SPIFFE_AGENTS = [
  { spiffe_id: "spiffe://agents.docubridge.io/internal/credit-officer-agent", display_name: "Internal Credit & Loan Underwriting Agent", security_zone: "internal" },
  { spiffe_id: "spiffe://agents.docubridge.io/internal/underwriter-agent", display_name: "Internal Technical Underwriting & Risk Agent", security_zone: "internal" },
  { spiffe_id: "spiffe://agents.docubridge.io/internal/broker-agent", display_name: "Internal Commercial Placement & Deal Desk Agent", security_zone: "internal" },
  { spiffe_id: "spiffe://agents.docubridge.io/internal/claims-agent", display_name: "Internal Covenant & Claims Review Agent", security_zone: "internal" },
  { spiffe_id: "spiffe://agents.docubridge.io/portal/external-portal-agent", display_name: "External Client, Borrower & Partner Portal Agent", security_zone: "external" }
];

export const FALLBACK_TRUST_POLICIES = [
  { slice_id: "slice:closing-disclosure:fee-schedule:LOW", issuer_pattern: "Meridian*", doctype: "closing-disclosure", risk_level: "LOW", autonomy_level: "trusted_auto_approve", consecutive_matches: 14, required_matches_for_promotion: 5, accuracy_rate: 1.0, drift_incidents: 0, last_updated_by: "senior.credit.officer@docubridge.io", last_updated_reason: "Promoted after 14 consecutive exact matches in Guided Review." },
  { slice_id: "slice:credit-agreement:addendum:MEDIUM", issuer_pattern: "Syndicated*", doctype: "addendum", risk_level: "MEDIUM", autonomy_level: "guided_review", consecutive_matches: 4, required_matches_for_promotion: 5, accuracy_rate: 1.0, drift_incidents: 0, last_updated_by: "senior.credit.officer@docubridge.io", last_updated_reason: "In Guided Human Review Queue (4/5 matches toward Auto-Approve)." },
  { slice_id: "slice:appraisal-report:commercial:HIGH", issuer_pattern: "CBRE*", doctype: "appraisal", risk_level: "HIGH", autonomy_level: "shadow_mode", consecutive_matches: 2, required_matches_for_promotion: 5, accuracy_rate: 1.0, drift_incidents: 0, last_updated_by: "system:bootstrap", last_updated_reason: "Running silently in Shadow Mode for commercial MAI appraisal template." },
  { slice_id: "slice:mortgage-trid:closing-disclosure:MEDIUM", issuer_pattern: "FirstNational*", doctype: "closing-disclosure", risk_level: "MEDIUM", autonomy_level: "guided_review", consecutive_matches: 4, required_matches_for_promotion: 5, accuracy_rate: 1.0, drift_incidents: 0, last_updated_by: "senior.credit.officer@docubridge.io", last_updated_reason: "In Guided Human Review Queue (4/5 matches on TRID Closing Disclosure rate-lock addenda)." },
  { slice_id: "slice:chubb:quote:LOW", issuer_pattern: "Chubb*", doctype: "quote", risk_level: "LOW", autonomy_level: "trusted_auto_approve", consecutive_matches: 12, required_matches_for_promotion: 5, accuracy_rate: 1.0, drift_incidents: 0, last_updated_by: "lead.broker@docubridge.io", last_updated_reason: "Promoted after 12 consecutive exact matches in Guided Review." },
  { slice_id: "slice:aig:endorsement:MEDIUM", issuer_pattern: "AIG*", doctype: "endorsement", risk_level: "MEDIUM", autonomy_level: "guided_review", consecutive_matches: 4, required_matches_for_promotion: 5, accuracy_rate: 1.0, drift_incidents: 0, last_updated_by: "lead.underwriter@docubridge.io", last_updated_reason: "In Guided Human Review Queue (4/5 matches toward Auto-Approve)." },
  { slice_id: "slice:lloyds:quote:HIGH", issuer_pattern: "Lloyd's*", doctype: "quote", risk_level: "HIGH", autonomy_level: "shadow_mode", consecutive_matches: 2, required_matches_for_promotion: 5, accuracy_rate: 1.0, drift_incidents: 0, last_updated_by: "system:bootstrap", last_updated_reason: "Running silently in Shadow Mode for new syndicate template." }
];

export const FALLBACK_WATERFALL = [
  { name: "docubridge.intake.ingest_accepted_submission", zone: "2_3_6_enterprise_ingestion", offset_ms: 0.0, duration_ms: 18.4, status: "OK", gen_ai_model: "0-token-deterministic", traffic_type: "deterministic_code", input_tokens: 0, output_tokens: 0 },
  { name: "docubridge.intake.redact_pii", zone: "1_edge_intake", offset_ms: 1.2, duration_ms: 2.1, status: "OK", gen_ai_model: "0-token-deterministic", traffic_type: "deterministic_code", input_tokens: 0, output_tokens: 0 },
  { name: "docubridge.extract.call_llm", zone: "2_extraction_engine", offset_ms: 2.4, duration_ms: 4.1, status: "OK", gen_ai_model: "gemini-3.8-flash", traffic_type: "production_inference", input_tokens: 1420, output_tokens: 245 },
  { name: "docubridge.vault.lock_worm_artifact", zone: "3_audit_vault", offset_ms: 6.6, duration_ms: 4.8, status: "OK", gen_ai_model: "0-token-deterministic", traffic_type: "deterministic_code", input_tokens: 0, output_tokens: 0 },
  { name: "docubridge.consolidation.promote_to_operational_store", zone: "3_operational_store", offset_ms: 11.7, duration_ms: 5.2, status: "OK", gen_ai_model: "0-token-deterministic", traffic_type: "deterministic_code", input_tokens: 0, output_tokens: 0 },
  { name: "docubridge.consolidation.adjudicate_conflict", zone: "4_consolidation_engine", offset_ms: 14.2, duration_ms: 3.1, status: "OK", gen_ai_model: "gemini-4-argon", traffic_type: "conflict_escalation", input_tokens: 1650, output_tokens: 340 },
  { name: "docubridge.consolidation.bitemporal_query_as_of", zone: "3_operational_store", offset_ms: 17.5, duration_ms: 1.9, status: "OK", gen_ai_model: "0-token-deterministic", traffic_type: "deterministic_code", input_tokens: 0, output_tokens: 0 },
  { name: "docubridge.catalog.register_lineage", zone: "6_knowledge_catalog", offset_ms: 19.6, duration_ms: 2.0, status: "OK", gen_ai_model: "0-token-deterministic", traffic_type: "deterministic_code", input_tokens: 0, output_tokens: 0 },
  { name: "docubridge.consolidation.bigquery_iceberg_sync", zone: "4_bigquery_analytics", offset_ms: 21.8, duration_ms: 2.6, status: "OK", gen_ai_model: "0-token-deterministic", traffic_type: "deterministic_code", input_tokens: 0, output_tokens: 0 },
  { name: "docubridge.identity.verify_jwt_svid", zone: "5_spiffe_identity", offset_ms: 24.4, duration_ms: 1.1, status: "OK", gen_ai_model: "0-token-deterministic", traffic_type: "deterministic_code", input_tokens: 0, output_tokens: 0 },
  { name: "docubridge.a2ui.resolve_inbox_action", zone: "5_user_interaction_a2ui", offset_ms: 25.8, duration_ms: 2.9, status: "OK", gen_ai_model: "0-token-deterministic", traffic_type: "deterministic_code", input_tokens: 0, output_tokens: 0 },
  { name: "docubridge.agent_gym.distill_signal", zone: "6_knowledge_catalog_agent_gym", offset_ms: 29.1, duration_ms: 3.5, status: "OK", gen_ai_model: "gemini-4-argon", traffic_type: "agent_gym_eval", input_tokens: 940, output_tokens: 185 },
  { name: "docubridge.agent_gym.evaluate_rule", zone: "6_knowledge_catalog_agent_gym", offset_ms: 32.9, duration_ms: 4.1, status: "OK", gen_ai_model: "gemini-4-argon", traffic_type: "agent_gym_eval", input_tokens: 2860, output_tokens: 520 },
  { name: "docubridge.agent_gym.compile_skill", zone: "6_knowledge_catalog_agent_gym", offset_ms: 37.3, duration_ms: 3.2, status: "OK", gen_ai_model: "gemini-4-argon", traffic_type: "agent_gym_eval", input_tokens: 1850, output_tokens: 445 }
];

export function initSharedState() {
  window.currentDomain = "banking";
  window.currentPersonaMode = "senior";
  window.lastCandidateRuleId = null;
  window.lastTraceId = "4bf92f3577b34da6a3ce929d0e0e4736";
  window.cachedTrustPolicies = [];
  window.SCENARIOS = [];
  window._demoPasscode = "";
  window.currentRegistryTab = "agents";
  window.cachedRegistryCatalog = null;
  window.lastBqSqlQueries = null;
  window.activeBqSqlTab = "rule0_trajectory_sql";
  window.lastRefineryProposalId = null;
  window.currentSchemaEvolutionScenario = "benefits";
}

export function formatPerturbationStability(rep) {
  const r = rep || {};
  if (!r.perturbation_evaluated || r.perturbation_krippendorff_alpha == null) {
    return "Perturbation stability: not evaluated (no live extractor)";
  }
  const alpha = Number(r.perturbation_krippendorff_alpha).toFixed(2);
  const ac2 = r.workhorse_teacher_ac2 == null ? "n/a" : Number(r.workhorse_teacher_ac2).toFixed(2);
  return `Perturbation stability α = ${alpha} · AC2 = ${ac2} (${r.perturbation_items || 0} doc×field items × ${r.perturbation_permutations || 0} permutations)`;
}

export function scenarioForDomain(domain) {
  const matches = window.SCENARIOS.filter(s => s.domain === domain);
  return matches.find(s => s.seeded) || matches[0] || null;
}

export function stageScenario(stageKind, domain) {
  const matches = window.SCENARIOS.filter(s => s.stage_kind === stageKind);
  return matches.find(s => s.domain === domain) || null;
}

export function scenarioLabel(domain, key, fallback) {
  const s = scenarioForDomain(domain);
  if (!s) return fallback === undefined ? "" : fallback;
  if (key === "client") return s.display.client;
  if (key === "program") return s.display.program;
  if (key === "entity_id") return s.entity_id;
  const v = s.display.labels ? s.display.labels[key] : undefined;
  return v === undefined ? (fallback === undefined ? "" : fallback) : v;
}

export function switcherDomains() {
  const seen = [];
  window.SCENARIOS.filter(s => s.seeded).forEach(s => {
    if (!seen.includes(s.domain)) seen.push(s.domain);
  });
  return seen;
}

export function renderDomainSwitcher() {
  const container = document.getElementById("domain-switcher");
  switcherDomains().forEach(domain => {
    let btn = document.getElementById("domain-btn-" + domain);
    if (!btn && container) {
      btn = document.createElement("button");
      btn.id = "domain-btn-" + domain;
      btn.className = "domain-btn";
      btn.onclick = () => window.switchDomain(domain);
      container.appendChild(btn);
    }
    if (btn) btn.textContent = scenarioLabel(domain, "switcher", scenarioForDomain(domain).title);
  });
}

export async function loadScenarios() {
  try {
    const resp = await apiFetch("/v1/demo/scenarios");
    if (resp.ok) window.SCENARIOS = (await resp.json()).scenarios || [];
  } catch (_) {}
  renderDomainSwitcher();
  const consol = window.SCENARIOS.find(s => s.stage_kind === "consolidation");
  if (consol && window.FALLBACK_COLLAB_STATE) {
    window.FALLBACK_COLLAB_STATE.entity_id = consol.entity_id;
    window.FALLBACK_COLLAB_STATE.borrower_name = consol.display.client;
    window.FALLBACK_COLLAB_STATE.facility_title = consol.display.program;
  }
  const evol = stageScenario("schema_evolution", window.currentSchemaEvolutionScenario)
    || window.SCENARIOS.find(s => s.stage_kind === "schema_evolution");
  if (evol && window.FALLBACK_SCHEMA_EVOL_STATE) {
    window.FALLBACK_SCHEMA_EVOL_STATE.entity_id = evol.entity_id;
    window.FALLBACK_SCHEMA_EVOL_STATE.client_name = evol.display.client;
    window.FALLBACK_SCHEMA_EVOL_STATE.program_title = evol.display.program;
    window.FALLBACK_SCHEMA_EVOL_STATE.scenario_domain = evol.domain;
  }
}

export function applyScenarioLabels() {
  const dom = window.currentDomain;
  const setText = (id, value) => {
    const el = document.getElementById(id);
    if (el && value) el.textContent = value;
  };
  setText("header-entity-tag", scenarioLabel(dom, "header_tag"));
  setText("header-entity-name", scenarioLabel(dom, "client"));
  setText("header-entity-sub", scenarioLabel(dom, "header_sub", scenarioLabel(dom, "program")));
  setText("portal-header-title", scenarioLabel(dom, "portal_title"));
  setText("portal-partner-pill", scenarioLabel(dom, "portal_pill"));
  setText("portal-page-heading", scenarioLabel(dom, "portal_heading"));
  setText("portal-checklist-title", scenarioLabel(dom, "checklist_title"));
  setText("iceberg-watermark-label", scenarioLabel(dom, "watermark"));
  setText("inbox-card-heading", scenarioLabel(dom, "inbox_heading"));
  setText("otel-active-entity", getActiveEntityId());
  setText("proof-vault-uri", scenarioLabel(dom, "vault_uri"));
  const consol = stageScenario("consolidation", dom);
  if (consol) {
    setText("collab-entity-badge", consol.entity_id);
    setText("collab-stage-title", (consol.display.labels && consol.display.labels.stage_title) || `${consol.display.client} — ${consol.display.program}`);
  }
}

export function getApiBase() {
  if (window.location.protocol === "http:" || window.location.protocol === "https:") {
    const path = window.location.pathname;
    if (path.startsWith("/ops/")) {
      return window.location.origin + "/ops";
    }
    return window.location.origin;
  }
  return "http://127.0.0.1:8003";
}

export function getActiveEntityId() {
  return scenarioLabel(window.currentDomain, "entity_id", "");
}

export function updateTraceBadge(traceId) {
  if (!traceId) return;
  window.lastTraceId = traceId;
  const shortTrace = traceId.length > 16 ? (traceId.slice(0, 8) + "..." + traceId.slice(-5)) : traceId;
  const pillLabel = document.getElementById("live-trace-id-label");
  if (pillLabel) pillLabel.textContent = shortTrace;
  const activeTraceEl = document.getElementById("otel-active-trace-id");
  if (activeTraceEl) activeTraceEl.textContent = traceId;
}

export async function unlockDemoSession(persona, passcode) {
  const code = passcode !== undefined ? passcode : (window._demoPasscode || window.prompt("Enter demo passcode to unlock interactive session:", ""));
  if (!code) return false;
  const resp = await fetch(getApiBase() + "/v1/auth/demo-session", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ passcode: code, persona: persona || "underwriter" })
  });
  if (!resp.ok) return false;
  window._demoPasscode = code;
  return true;
}

export async function apiFetch(path, options) {
  const opts = Object.assign({ credentials: "same-origin" }, options || {});
  let resp = await fetch(getApiBase() + path, opts);
  if ((resp.status === 401 || resp.status === 403) && opts.method && opts.method !== "GET" && path.indexOf("/v1/auth/") !== 0) {
    const persona = window.currentPersonaMode === "junior" ? "broker" : "underwriter";
    if (await unlockDemoSession(persona)) resp = await fetch(getApiBase() + path, opts);
  }
  const traceHdr = resp.headers.get("x-docubridge-trace-id");
  if (traceHdr) updateTraceBadge(traceHdr);
  return resp;
}

export function setCaption(badge, headline, sub) {
  document.getElementById("caption-badge").textContent = badge;
  document.getElementById("caption-headline").textContent = headline;
  document.getElementById("caption-sub").textContent = sub;
}
