import {
  apiFetch,
  formatPerturbationStability,
  setCaption,
  stageScenario,
} from "./api_state.js";

export const FALLBACK_SCHEMA_EVOL_STATE = {
  entity_id: "",
  client_name: "",
  program_title: "",
  beat: 0,
  done: false,
  active_ontology_revision: "ont-rev-v1-1-baseline",
  active_ontology_version: "1.1.0",
  entity_count: 7,
  edge_count: 12,
  financial_summary: {
    total_insured_value: "$142,500,000.00",
    primary_plus_excess_limit: "$75,000,000.00",
    coastal_wind_deductible: "$50,000.00 (Base Quote)",
    deductible_authority: "commercial-property-quote (Prio 20 · v1.1.0)"
  },
  placement_checklist: [
    {
      id: "doc-1-sov",
      label: "14-Location Coastal Property Schedule (SOV — $142.5M TIV)",
      doctype: "structured-sov-xlsx",
      status: "satisfied",
      emergent: false,
      badge_text: "VERIFIED · PATH A (0 TOKENS)",
      reason: "Deterministic schema check verified 14 coastal locations ($142,500,000.00 TIV).",
      source_citation: "Schedule Tab 1 · Row 14",
      ontology_rev: "ont-rev-v1-1-baseline"
    },
    {
      id: "doc-2-chubb-quote",
      label: "Chubb Primary Commercial Property Quote ($50M Limit · $50K Base Deductible)",
      doctype: "commercial-property-quote",
      status: "satisfied",
      emergent: false,
      badge_text: "VERIFIED · PRIO 20 (v1.1.0)",
      reason: "24-page base quote populates all 7 required fields under ont-rev-v1-1-baseline.",
      source_citation: "Page 1 · Region [0.14, 0.31]",
      ontology_rev: "ont-rev-v1-1-baseline"
    },
    {
      id: "doc-3-aig-binder",
      label: "AIG Signed Excess Property Binder ($25M xs $50M · $412K Premium)",
      doctype: "commercial-property-quote",
      status: "satisfied",
      emergent: false,
      badge_text: "VERIFIED · PRIO 20 (v1.1.0)",
      reason: "Multi-window table merge verified excess attachment point and annual premium.",
      source_citation: "Page 11 · Region [0.18, 0.52]",
      ontology_rev: "ont-rev-v1-1-baseline"
    }
  ],
  graph_edges_preview: [
    {
      source: "commercial-property-quote (Prio 20)",
      relation: "[:FEEDS_METRIC]",
      target: "FinancialSummary.{total_premium, total_deductible, total_coverage_limit}",
      revision: "ont-rev-v1-1-baseline",
      is_new: false
    }
  ],
  banner: {
    tone: "info",
    headline: "Baseline Ontology Active · ont-rev-v1-1-baseline (v1.1.0 · 7 OKF Entities)",
    message: "All 3 base placement documents reconcile under v1.1.0 ($142.5M TIV, $50K Base Deductible). Click Beat 1 to upload Chubb's 1-page Schedule B Coastal Wind Deductible Endorsement ($100,000.00).",
    agent_note: "Under v1.1.0, 'commercial-property-quote' is a single monolithic doctype (Prio 20) with no separate endorsement entity or [:SUPERSEDES] edge."
  },
  agent1_status: {
    agent_name: "Agent 1 · Document Bridge (Sense A Contrastive Extractor)",
    model: "gemini-3.8-flash",
    headline: "3 Baseline Documents Extracted under ont-rev-v1-1-baseline",
    detail: "Pinned ontology_revision_id='ont-rev-v1-1-baseline' across all multi-window chunks. 3-part contrastive descriptions active on all 7 OKF doctypes.",
    pinned_ontology_revision: "ont-rev-v1-1-baseline"
  },
  agent2_status: {
    agent_name: "Agent 2 · Schema Refinery & Graph Consolidation (Sense B)",
    model: "gemini-4-argon",
    headline: "Zone 6 Knowledge Graph Monitoring Field-Presence Sparsity",
    detail: "Watching AlloyDB operational facts and BigQuery otel_spans for bimodal null clusters (Jaccard J(A,B) <= 0.10) and doctype priority collisions.",
    jaccard_similarity: 0.0667,
    nfr: 0.0,
    rogan_gladen_pass_rate: 0.988
  },
  proposal: null,
  assay_report: null,
  day2_payoff: null
};

export function renderSchemaRefineryRevisions(payload) {
  if (!payload) return;
  const active = payload.active_revision || {};
  const edges = payload.edges || active.edges || [];
  const revEl = document.getElementById("refinery-active-revision");
  if (revEl && active.revision_id) {
    revEl.textContent = `${active.revision_id} (v${active.version || "1.1.0"})`;
  }
  const edgeEl = document.getElementById("refinery-edge-count");
  if (edgeEl) {
    edgeEl.textContent = `${edges.length} Graph Edges`;
  }
  const assayBox = document.getElementById("refinery-assay-box");
  if (assayBox && edges.length && !window.lastRefineryProposalId) {
    const sampleEdges = edges.slice(0, 5).map(function(e) {
      return `<div class="mono" style="font-size:11px;margin-bottom:3px;">(:Entity {id: "${e.source_id}"})-[:${e.relation}${e.priority_rank ? " {priority: " + e.priority_rank + "}" : ""}]->(${e.target_id})</div>`;
    }).join("");
    assayBox.innerHTML = `<div style="margin-bottom:4px;font-weight:700;color:#0f172a;">Active Zone 6 Ontology Graph (${edges.length} declarative edges):</div>` + sampleEdges;
  }
}

export async function refreshSchemaRefinery() {
  try {
    const resp = await apiFetch("/v1/refinery/revisions");
    if (resp.ok) {
      renderSchemaRefineryRevisions(await resp.json());
    }
  } catch (_) {}
}

export async function runSchemaRefineryAssay() {
  const dom = window.currentDomain || "insurance";
  try {
    const resp = await apiFetch("/v1/refinery/assay", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ domain: dom })
    });
    if (!resp.ok) return;
    const data = await resp.json();
    const prop = data.proposal || {};
    const rep = data.assay_report || {};
    window.lastRefineryProposalId = prop.proposal_id || null;

    const propBox = document.getElementById("refinery-proposal-box");
    if (propBox) {
      const after = prop.after_description || {};
      propBox.innerHTML = `
        <div style="font-weight:800;color:#4f46e5;margin-bottom:4px;">${prop.title || "Entity Split & Contrastive Refinement"}</div>
        <div style="margin-bottom:6px;color:#475569;">${prop.rationale || ""}</div>
        <div style="background:#ffffff;border:1px solid #e2e8f0;border-radius:6px;padding:8px 10px;font-size:11.5px;">
          <div><strong>what_it_is:</strong> ${after.what_it_is || ""}</div>
          <div><strong>where_to_read:</strong> ${after.where_to_read || ""}</div>
          <div><strong>distinguish_from:</strong> ${after.distinguish_from || ""}</div>
        </div>
      `;
    }
    const assayBox = document.getElementById("refinery-assay-box");
    if (assayBox) {
      assayBox.innerHTML = `
        <div style="font-weight:800;color:#059669;margin-bottom:4px;">✓ ${rep.summary || "3-Tier Assay PASSED"}</div>
        <div class="mono" style="font-size:11.5px;line-height:1.5;background:#ffffff;border:1px solid #a7f3d0;border-radius:6px;padding:8px 10px;">
          <div>Tier 1 (Rule 0): DAG acyclic=${rep.dag_acyclic} · Decimal formulas=${rep.decimal_formulas_valid} · Paired removal=${rep.paired_removal_intact}</div>
          <div>Tier 2 (Replay): ${rep.replayed_deal_count || 3} deals replayed · NFR = ${((rep.negative_flip_rate || 0) * 100).toFixed(1)}% · False quarantines resolved: -${rep.quarantine_reduction_delta || 9}</div>
          <div>Tier 3 (Calibration): ${formatPerturbationStability(rep)} · Rogan-Gladen θ̂ = ${((rep.rogan_gladen_pass_rate || 0.96) * 100).toFixed(1)}%</div>
        </div>
      `;
    }
    setCaption(
      "STAGE 4 · SCHEMA REFINERY BIMODAL SPARSITY & 3-TIER ASSAY PASSED",
      `Detected bimodal Jaccard similarity J(A,B) = ${rep.bimodal_jaccard_similarity} <= 0.10 on ${prop.target_entity_id}; synthesized '${prop.new_entity_id}' with NFR = 0.0%; ${formatPerturbationStability(rep)}.`,
      "POST /v1/refinery/assay -> 3-Tier Counterfactual & Calibration Gate"
    );
  } catch (_) {}
}

export async function promoteSchemaRefineryProposal() {
  if (!window.lastRefineryProposalId) {
    await runSchemaRefineryAssay();
  }
  if (!window.lastRefineryProposalId) return;
  try {
    const resp = await apiFetch("/v1/refinery/promote", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ proposal_id: window.lastRefineryProposalId, version: "1.2.0" })
    });
    if (!resp.ok) return;
    const data = await resp.json();
    const promoted = data.promoted_revision || {};
    await refreshSchemaRefinery();
    const assayBox = document.getElementById("refinery-assay-box");
    if (assayBox && promoted.edges) {
      const splitEdges = promoted.edges.filter(function(e) {
        return e.relation === "SPLIT_FROM" || e.relation === "SUPERSEDES";
      }).map(function(e) {
        return `<div class="mono" style="font-size:11px;color:#047857;">(:Entity {id: "${e.source_id}"})-[:${e.relation}${e.priority_rank ? " {priority: " + e.priority_rank + "}" : ""}]->(:Entity {id: "${e.target_id}"})</div>`;
      }).join("");
      assayBox.innerHTML += `<div style="margin-top:8px;padding-top:6px;border-top:1px dashed #cbd5e1;"><strong>Promoted Revision <span class="mono">${promoted.revision_id}</span> Lineage Edges:</strong>${splitEdges}</div>`;
    }
    setCaption(
      "STAGE 4 · ONTOLOGY REVISION v1.2 PROMOTED TO ZONE 6 KNOWLEDGE CATALOG",
      `Promoted ${promoted.revision_id} (SHA-256 ${(promoted.content_sha256 || "").slice(0, 12)}...) with [:SPLIT_FROM] & [:SUPERSEDES] lineage while preserving historical v1.1 facts.`,
      "POST /v1/refinery/promote -> GET /v1/refinery/revisions"
    );
    await refreshSchemaEvolutionCollab();
  } catch (_) {}
}

export async function switchSchemaEvolutionScenario(scenario) {
  const dom = (scenario === "insurance") ? "insurance" : "benefits";
  window.currentSchemaEvolutionScenario = dom;
  const btnBen = document.getElementById("btn-schema-evol-scenario-benefits");
  const btnIns = document.getElementById("btn-schema-evol-scenario-insurance");
  if (btnBen) btnBen.className = (dom === "benefits") ? "btn btn-primary" : "btn btn-outline";
  if (btnIns) btnIns.className = (dom === "insurance") ? "btn btn-primary" : "btn btn-outline";
  await refreshSchemaEvolutionCollab();
}

export function renderSchemaEvolutionCollab(state) {
  const st = state || FALLBACK_SCHEMA_EVOL_STATE;
  const beat = Number(st.beat || 0);
  const scen = st.scenario_domain || window.currentSchemaEvolutionScenario || "benefits";
  const isBenefits = scen === "benefits";

  const btnBen = document.getElementById("btn-schema-evol-scenario-benefits");
  const btnIns = document.getElementById("btn-schema-evol-scenario-insurance");
  if (btnBen) btnBen.className = isBenefits ? "btn btn-primary" : "btn btn-outline";
  if (btnIns) btnIns.className = !isBenefits ? "btn btn-primary" : "btn btn-outline";

  const entBadge = document.getElementById("schema-evol-entity-badge");
  if (entBadge) entBadge.textContent = st.entity_id || ((stageScenario("schema_evolution", scen) || {}).entity_id || "");
  const titleEl = document.getElementById("schema-evol-card-title");
  if (titleEl && st.client_name && st.program_title) {
    titleEl.textContent = `${st.client_name} — ${st.program_title}`;
  }

  const b1Btn = document.getElementById("btn-schema-evol-beat-1");
  const b2Btn = document.getElementById("btn-schema-evol-beat-2");
  const b3Btn = document.getElementById("btn-schema-evol-beat-3");
  const b4Btn = document.getElementById("btn-schema-evol-beat-4");
  if (isBenefits) {
    if (b1Btn) b1Btn.textContent = '1. Upload Page 4 HDHP Rider & $125K Rate Addendum (Guesses "FSA: $1,000" ✕)';
    if (b2Btn) b2Btn.textContent = '2. Challenge Fix ("FSA" → "HSA") & Run 3-Tier Assay (0% Backsliding)';
    if (b3Btn) b3Btn.textContent = '3. Promote Ontology v1.2.0 ("HSA: $1,000" ✓ + $125K Addendum Wins)';
    if (b4Btn) b4Btn.textContent = '4. Verify Day-2 Payoff (Next HDHP Renewal · 134ms)';
  } else {
    if (b1Btn) b1Btn.textContent = "1. Upload Schedule B Wind Endorsement (Sparsity Gap)";
    if (b2Btn) b2Btn.textContent = "2. Run 3-Tier Refinery Assay (SplitEntity J=0.0667)";
    if (b3Btn) b3Btn.textContent = "3. Promote Ontology v1.2.0 ([:SUPERSEDES] + [:SPLIT_FROM])";
    if (b4Btn) b4Btn.textContent = "4. Verify Day-2 Payoff (Zero-Quarantine 138ms)";
  }

  const beatLabels = isBenefits ? {
    0: "Beat 0 of 4 · Baseline Ontology v1.1.0 (7 Entities)",
    1: 'Beat 1 of 4 · Prompt Alone (~60%) Guesses "FSA: $1,000" ✕ + Addendum Sparsity',
    2: 'Beat 2 of 4 · Socratic Challenge ("Can ANY HDHP Have FSA?") · 0% Backsliding',
    3: 'Beat 3 of 4 · Ontology v1.2.0 Active · Extracts "HSA: $1,000" ✓ + $125K Wins',
    4: "Beat 4 of 4 · Day-2 HDHP Renewal Auto-Reconciled (134ms · 0% Backsliding)"
  } : {
    0: "Beat 0 of 4 · Baseline Ontology v1.1.0 (7 Entities)",
    1: "Beat 1 of 4 · Bimodal Sparsity & Priority Tie Exposed (J=0.0667)",
    2: "Beat 2 of 4 · 3-Tier Assay PASSED · SplitEntity Proposed",
    3: "Beat 3 of 4 · Ontology v1.2.0 Promoted · $100K Deductible Reconciled",
    4: "Beat 4 of 4 · Day-2 Zero-Quarantine Endorsement Auto-Cleared (138ms)"
  };
  const beatPill = document.getElementById("schema-evol-beat-pill");
  if (beatPill) {
    beatPill.textContent = beatLabels[beat] || beatLabels[0];
    beatPill.className = st.done ? "badge badge-green" : (beat === 1 ? "badge badge-amber" : "badge badge-purple");
  }
  const revPill = document.getElementById("schema-evol-rev-pill");
  if (revPill) {
    revPill.textContent = `${st.active_ontology_revision || "ont-rev-v1-1-baseline"} · v${st.active_ontology_version || "1.1.0"} (${st.entity_count || 7} Entities)`;
    revPill.className = beat >= 3 ? "badge badge-green mono" : "badge badge-navy mono";
  }
  const pinnedBadge = document.getElementById("schema-evol-pinned-badge");
  if (pinnedBadge) {
    pinnedBadge.textContent = `Pinned: ${st.active_ontology_revision || "ont-rev-v1-1-baseline"}`;
  }

  const fin = st.financial_summary || {};
  const mLabels = fin.metric_labels || {};
  const lCol1 = document.getElementById("schema-evol-label-col1");
  const sCol1 = document.getElementById("schema-evol-sub-col1");
  const lCol2 = document.getElementById("schema-evol-label-col2");
  const sCol2 = document.getElementById("schema-evol-sub-col2");
  const lCol3 = document.getElementById("schema-evol-label-col3");
  if (lCol1) lCol1.textContent = mLabels.col1_label || "Total Insured Value (SOV)";
  if (sCol1) sCol1.textContent = mLabels.col1_sub || "14 Coastal Locations · Path A (0 Tokens)";
  if (lCol2) lCol2.textContent = mLabels.col2_label || "Primary + Excess Limit";
  if (sCol2) sCol2.textContent = mLabels.col2_sub || "$50M Chubb + $25M AIG Excess";
  if (lCol3) lCol3.textContent = mLabels.col3_label || "Coastal Wind Deductible";

  const tivEl = document.getElementById("schema-evol-fin-tiv");
  if (tivEl) tivEl.textContent = fin.total_insured_value || "$142,500,000.00";
  const limEl = document.getElementById("schema-evol-fin-limit");
  if (limEl) limEl.textContent = fin.primary_plus_excess_limit || "$75,000,000.00";
  const dedEl = document.getElementById("schema-evol-fin-deductible");
  if (dedEl) {
    dedEl.textContent = fin.coastal_wind_deductible || "$50,000.00";
    dedEl.style.color = beat >= 3 ? "#059669" : "#b45309";
  }
  const authEl = document.getElementById("schema-evol-fin-authority");
  if (authEl) authEl.textContent = fin.deductible_authority || "commercial-property-quote (Prio 20)";
  const ontEl = document.getElementById("schema-evol-fin-ontology");
  if (ontEl) {
    ontEl.textContent = `v${st.active_ontology_version || "1.1.0"} · ${st.entity_count || 7} Entities`;
    ontEl.style.color = beat >= 3 ? "#059669" : "#6d28d9";
  }
  const edgesMetricEl = document.getElementById("schema-evol-fin-edges");
  if (edgesMetricEl) edgesMetricEl.textContent = `${st.edge_count || 12} Declarative Graph Edges`;

  const progBadge = document.getElementById("schema-evol-progress-badge");
  if (progBadge) {
    const totalItems = Array.isArray(st.placement_checklist) ? st.placement_checklist.length : 3;
    const satItems = Array.isArray(st.placement_checklist)
      ? st.placement_checklist.filter(i => i.status === "satisfied").length
      : 3;
    if (beat === 0) {
      progBadge.className = "badge badge-green";
      progBadge.textContent = `${satItems} / ${totalItems} Base Verified (v1.1.0)`;
    } else if (beat < 3) {
      progBadge.className = "badge badge-amber";
      progBadge.textContent = `${satItems} / ${totalItems} Verified · ${totalItems - satItems} Quarantined`;
    } else {
      progBadge.className = "badge badge-green";
      progBadge.textContent = `✓ ${satItems} / ${totalItems} Verified under Ontology v1.2.0 · DONE`;
    }
  }

  const bannerEl = document.getElementById("schema-evol-banner");
  const bannerHead = document.getElementById("schema-evol-banner-headline");
  const bannerMsg = document.getElementById("schema-evol-banner-msg");
  if (bannerEl && st.banner) {
    bannerEl.className = `collab-banner collab-banner-${st.banner.tone || "info"}`;
    if (bannerHead) bannerHead.textContent = st.banner.headline || "";
    if (bannerMsg) {
      bannerMsg.innerHTML = `${st.banner.message || ""}${
        st.banner.agent_note
          ? `<div style="margin-top:4px;font-weight:700;font-size:11.5px;opacity:0.9;">🧬 ${st.banner.agent_note}</div>`
          : ""
      }`;
    }
  }

  const checklistEl = document.getElementById("schema-evol-checklist");
  if (checklistEl && Array.isArray(st.placement_checklist)) {
    checklistEl.innerHTML = st.placement_checklist.map(function(item, idx) {
      const status = item.status || "satisfied";
      const badgeCls = status === "satisfied" ? "badge-green" : "badge-amber";
      const icon = status === "satisfied" ? "✓" : "⚠";
      const citationChip = item.source_citation
        ? `<span class="anchor-chip" title="Click to open WORM PDF Source Proof">${item.source_citation}</span>`
        : "";
      return `
        <div class="collab-check-item status-${status === "satisfied" ? "satisfied" : "required"}">
          <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;">
            <div style="font-weight:800;font-size:12.5px;color:#0f172a;">
              ${icon} ${idx + 1}. ${item.label}
              <span class="badge badge-navy mono" style="font-size:9.5px;padding:1px 6px;">${item.doctype}</span>
            </div>
            <span class="badge ${badgeCls}">${item.badge_text}</span>
          </div>
          <div style="font-size:11.5px;color:#334155;margin-top:4px;display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;">
            <span>${item.reason || ""}</span>
            <div style="display:flex;align-items:center;gap:6px;">
              <span class="badge badge-blue mono" style="font-size:9.5px;">${item.ontology_rev || "ont-rev-v1-1-baseline"}</span>
              ${citationChip}
            </div>
          </div>
        </div>
      `;
    }).join("");
  }

  const edgesListEl = document.getElementById("schema-evol-edges-list");
  if (edgesListEl && Array.isArray(st.graph_edges_preview)) {
    edgesListEl.innerHTML = st.graph_edges_preview.map(function(e) {
      const src = e.source_id || e.source || "entity";
      const tgt = e.target_id || e.target || "target";
      const isNew = e.is_new || (e.status && (e.status.includes("v1.2.0") || e.status.includes("CANDIDATE")));
      const color = isNew ? "#047857" : "#334155";
      const tag = isNew ? `<span class="badge badge-green" style="font-size:9px;margin-right:4px;">${e.status || "NEW v1.2.0"}</span>` : "";
      return `<div class="mono" style="color:${color};padding:3px 6px;background:#ffffff;border:1px solid #e2e8f0;border-radius:5px;">${tag}(:${src})-[:${e.relation}]->(:${tgt})</div>`;
    }).join("");
  }

  const a1 = st.agent1_status || {};
  const a1Box = document.getElementById("schema-evol-agent1-box");
  if (a1Box) {
    a1Box.innerHTML = `
      <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;">
        <strong style="font-size:12.5px;color:#0f172a;">⚡ ${a1.agent_name || "Agent 1 · Contrastive Extractor"}</strong>
        <span class="badge badge-blue mono">${a1.model || "gemini-3.8-flash"} · thinking_budget=0</span>
      </div>
      <div style="font-weight:700;font-size:12px;color:#1d4ed8;margin-top:4px;">${a1.headline || ""}</div>
      <div style="font-size:11.5px;color:#475569;margin-top:2px;">${a1.detail || ""}</div>
    `;
  }

  const a2 = st.agent2_status || {};
  const a2Box = document.getElementById("schema-evol-agent2-box");
  if (a2Box) {
    a2Box.innerHTML = `
      <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;">
        <strong style="font-size:12.5px;color:#0f172a;">🧬 ${a2.agent_name || "Agent 2 · Schema Refinery"}</strong>
        <span class="badge badge-purple mono">${a2.model || "gemini-4-argon"} · 3-Tier Assay</span>
      </div>
      <div style="font-weight:700;font-size:12px;color:#6d28d9;margin-top:4px;">${a2.headline || ""}</div>
      <div style="font-size:11.5px;color:#475569;margin-top:2px;">${a2.detail || ""}</div>
    `;
  }

  const refBox = document.getElementById("schema-evol-refinery-box");
  if (refBox) {
    const soc = st.socratic_challenge;
    if (beat === 0) {
      refBox.innerHTML = isBenefits ? `
        <div style="font-size:12px;color:#475569;">
          <strong>Ready for HDHP (HSA vs. FSA) &amp; Rate Addendum Walkthrough:</strong> Click <strong>1. Upload Page 4 HDHP Rider &amp; $125K Rate Addendum</strong> above to see why prompt-only extraction (~60%) guesses <span class="mono">"FSA: $1,000.00" ✕</span> on <span class="mono">"Plan: HDHP · Employer Account Seed: $1,000.00"</span> and how the Socratic Continuous Learning loop builds the <span class="mono">HDHP [:INCLUDES] HSA · [:EXCLUDES] FSA</span> Knowledge Graph rule.
        </div>
      ` : `
        <div style="font-size:12px;color:#475569;">
          <strong>Ready for Schema Evolution Walkthrough:</strong> Click <strong>1. Upload Schedule B Wind Endorsement</strong> above to see how a 1-page carrier endorsement stresses a monolithic doctype ontology and triggers closed-loop Schema Refinery evolution.
        </div>
      `;
    } else if (beat === 1) {
      refBox.innerHTML = isBenefits && soc ? `
        <div style="padding:10px;background:#fffbeb;border:1px solid #fde68a;border-radius:8px;font-size:12px;">
          <div style="font-weight:800;color:#b45309;">⚠ 1. Expert's One-Off Fix + Socratic Challenge Triggered</div>
          <div class="mono" style="font-size:11px;margin-top:4px;color:#78350f;line-height:1.5;">
            • <strong>01 · One-Off Fix:</strong> ${soc.step1_expert_fix}<br/>
            • <strong>02 · System Challenges One-Off:</strong> ${soc.step2_system_question}<br/>
            • <strong>Sparsity Gap:</strong> 2-Page Signed Rate Addendum ($125,000.00) has J(A,B) = 0.0714 &le; 0.10 on 150-page benefits-quote
          </div>
        </div>
      ` : `
        <div style="padding:10px;background:#fffbeb;border:1px solid #fde68a;border-radius:8px;font-size:12px;">
          <div style="font-weight:800;color:#b45309;">⚠ Bimodal Null-Matrix &amp; Priority Tie Detected in AlloyDB</div>
          <div class="mono" style="font-size:11px;margin-top:4px;color:#78350f;">
            • Cluster A (Base Quotes): {premium, deductible, coverage_limits, exclusions, effective_date, expiry_date}<br/>
            • Cluster B (Schedule B Endorsements): {deductible, effective_date} (3 required fields absent_from_document)<br/>
            • Jaccard Similarity J(A, B) = 0.0667 &le; 0.10 Split Threshold
          </div>
        </div>
      `;
    } else if (beat === 2) {
      const prop = st.proposal || {};
      const rep = st.assay_report || {};
      const after = prop.after_description || {};
      refBox.innerHTML = `
        <div style="padding:10px;background:#f5f3ff;border:1px solid #ddd6fe;border-radius:8px;font-size:12px;">
          <div style="display:flex;justify-content:space-between;align-items:center;">
            <strong style="color:#4f46e5;">✦ ${isBenefits ? "03 · Broad Graph Rule + SplitEntity:" : "Candidate OKF Entity:"} <span class="mono">${prop.new_entity_id || "wind-deductible-endorsement"}.okf.md</span> (Prio 40)</strong>
            <span class="badge badge-green">3-Tier Assay: PASS (0% Backsliding)</span>
          </div>
          <div style="font-size:11.5px;margin-top:5px;color:#334155;">
            ${soc ? `<div style="margin-bottom:4px;"><strong>Socratic Challenge Confirmed:</strong> <span class="mono" style="color:#6d28d9;">${soc.step2_system_question}</span> → <strong>${soc.step3_broad_rule}</strong></div>` : ""}
            <div><strong>Contrastive distinguish_from:</strong> ${after.distinguish_from || "Separates 1-page endorsements from base quotes."}</div>
            <div class="mono" style="margin-top:4px;font-size:11px;color:#047857;">
              NFR = ${((rep.negative_flip_rate || 0) * 100).toFixed(1)}% (0% Backsliding) · ${formatPerturbationStability(rep)} · Rogan-Gladen θ̂ = ${((rep.rogan_gladen_pass_rate || 0.988) * 100).toFixed(1)}%
            </div>
          </div>
        </div>
      `;
    } else if (beat === 3) {
      refBox.innerHTML = isBenefits ? `
        <div style="padding:10px;background:#ecfdf5;border:1px solid #a7f3d0;border-radius:8px;font-size:12px;">
          <div style="font-weight:800;color:#047857;">✓ Promoted ont-rev-v1-2-benefits (v1.2.0) · Graph in Action (95%+ Accuracy)</div>
          <div class="mono" style="font-size:11px;margin-top:4px;color:#065f46;line-height:1.5;">
            • <strong>Page 4 HDHP Schedule:</strong> Follows [:EXCLUDES] (✕ Cannot Be FSA) &amp; [:INCLUDES] (✓ Must Be HSA) → Extracts <strong>"HSA: $1,000.00" ✓</strong><br/>
            • <strong>Which Source Wins:</strong> Signed Rate Addendum ($125,000.00 · Prio 40) overrides Base Quote PDF ($140,000.00 · Prio 20) overrides Manual Entry ($145,000.00 · Prio 10)<br/>
            • <strong>Live Renewal Summary:</strong> Feeds all 8 specialty teams with 0% backsliding on locked v1.1.0 deals
          </div>
        </div>
      ` : `
        <div style="padding:10px;background:#ecfdf5;border:1px solid #a7f3d0;border-radius:8px;font-size:12px;">
          <div style="font-weight:800;color:#047857;">✓ Promoted ont-rev-v1-2-insurance (v1.2.0) · Two-Dimensional Lineage Intact</div>
          <div class="mono" style="font-size:11px;margin-top:4px;color:#065f46;">
            • Turn-1 Base Quote Facts: pinned to ont-rev-v1-1-baseline (Immutable)<br/>
            • Turn-2 Wind Endorsement: ont-rev-v1-2-insurance · [:SUPERSEDES {priority: 40}] -> $100,000.00<br/>
            • Evolution Lineage Edge: (:wind-deductible-endorsement)-[:SPLIT_FROM]->(:commercial-property-quote)
          </div>
        </div>
      `;
    } else {
      const d2 = st.day2_payoff || {};
      refBox.innerHTML = `
        <div style="padding:10px;background:#ecfdf5;border:1.5px solid #10b981;border-radius:8px;font-size:12px;">
          <div style="display:flex;justify-content:space-between;align-items:center;">
            <strong style="color:#047857;">⚡ Day-2 Payoff: ${d2.day2_client || ""} (${d2.day2_entity_id || ""})</strong>
            <span class="badge badge-green">${d2.latency_ms || 134} ms · 0 Touchpoints</span>
          </div>
          <div class="mono" style="font-size:11px;margin-top:4px;color:#065f46;">
            • Classified Doctype: ${d2.classified_doctype || "rate-guarantee-addendum"} (Prio 40 · ${d2.ontology_revision_id || "ont-rev-v1-2-benefits"})<br/>
            • Resolved Outcome: ${d2.resolved_deductible || "HSA: $1,500.00 ✓ · Premium: $118,000.00"}<br/>
            • False-Positive Missing-Field Parks: 0 · Escalation Tokens: 0
          </div>
        </div>
      `;
    }
  }
}

export async function refreshSchemaEvolutionCollab() {
  const scen = window.currentSchemaEvolutionScenario || "benefits";
  try {
    const resp = await apiFetch(`/v1/demo/schema-evolution/state?domain=${encodeURIComponent(scen)}`);
    if (resp.ok) {
      renderSchemaEvolutionCollab(await resp.json());
    }
  } catch (_) {}
}

export async function advanceSchemaEvolutionCollab(beat) {
  const scen = window.currentSchemaEvolutionScenario || "benefits";
  try {
    const resp = await apiFetch("/v1/demo/schema-evolution/advance", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ beat: Number(beat), domain: scen })
    });
    if (resp.ok) {
      const st = await resp.json();
      renderSchemaEvolutionCollab(st);
      refreshSchemaRefinery();
      setCaption(
        `SCHEMA EVOLUTION BEAT ${beat} OF 4 · ${st.active_ontology_revision}`,
        (st.banner && st.banner.headline) || "Updated Schema Refinery & Zone 6 Ontology Graph.",
        "POST /v1/demo/schema-evolution/advance -> bridge/refinery/ + Zone 6 KnowledgeCatalog"
      );
    }
  } catch (_) {}
}

export async function resetSchemaEvolutionCollab() {
  const scen = window.currentSchemaEvolutionScenario || "benefits";
  try {
    const resp = await apiFetch(`/v1/demo/schema-evolution/reset?domain=${encodeURIComponent(scen)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" }
    });
    if (resp.ok) {
      const st = await resp.json();
      renderSchemaEvolutionCollab(st);
      refreshSchemaRefinery();
    }
  } catch (_) {}
}
