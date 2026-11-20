import { apiFetch, setCaption, stageScenario } from "./api_state.js";

export const FALLBACK_COLLAB_STATE = {
  entity_id: "",
  borrower_name: "",
  facility_title: "",
  beat: 0,
  done: false,
  counts: { total: 3, satisfied: 0, waived: 0, required: 3 },
  borrower_checklist: [
    {
      id: "item-1-financials",
      label: "2025 Audited Financials & Note 6 Debt Schedule",
      doctype: "cre-credit-agreement",
      status: "required",
      emergent: false,
      badge_text: "REQUIRED",
      reason: "Base underwriting requirement to verify borrower NOI and existing debt obligations.",
      source_citation: null
    },
    {
      id: "item-2-bank-stmt",
      label: "Oct 2026 Operating Bank Statement (90-Day Liquidity & Cash Flow)",
      doctype: "closing-disclosure",
      status: "required",
      emergent: false,
      badge_text: "REQUIRED",
      reason: "Base underwriting requirement to verify operating cash reserves and debt-service debits.",
      source_citation: null
    },
    {
      id: "item-3-appraisal",
      label: "MAI Commercial Property Appraisal ($2.60M Industrial Warehouse)",
      doctype: "mai-appraisal",
      status: "required",
      emergent: false,
      badge_text: "REQUIRED",
      reason: "Collateral valuation required to confirm consolidated LTV <= 75.00%.",
      source_citation: null
    }
  ],
  borrower_banner: {
    tone: "info",
    headline: "Application Opened · 3 Baseline Documents Requested",
    message: "Upload your 2025 Audited Financials, Oct 2026 Operating Bank Statement, and MAI Appraisal to begin automated underwriting.",
    agent_note: "Document Bridge (Sense A) will inspect your Debt Schedule and dynamically tailor payoff requirements."
  },
  agent1_bridge_status: {
    agent_name: "Agent 1 · Document Bridge (Sense A Intake & OCR)",
    spiffe_id: "spiffe://agents.docubridge.io/internal/broker-placement-agent",
    model: "gemini-3.8-flash",
    headline: "Awaiting Borrower Document Upload",
    detail: "Ready to run Edge PII Redaction, 7-Year WORM Audit Vault lock, and Document AI page/bbox anchor extraction.",
    documents_processed: 0,
    active_anchors: [],
    skill_revision: "consolidation-payoff@1.0.0"
  },
  agent2_policy_status: {
    agent_name: "Agent 2 · Bank Underwriting & Policy Agent (Sense B)",
    spiffe_id: "spiffe://agents.docubridge.io/internal/credit-officer-agent",
    model: "gemini-4-argon",
    headline: "Baseline 3-Document Checklist Active",
    detail: "Monitoring Operational Store for Note 6 Schedule of Existing Liabilities to compute Decimal LTV/DSCR and spawn creditor-specific payoff tasks.",
    checklist_mutations: [],
    policy_rules_evaluated: [
      "Rule 0 Decimal LTV Cap <= 75.00%",
      "Rule 0 Decimal DSCR Floor >= 1.25x"
    ]
  },
  cross_doc_discovery: null,
  underwriter_overwatch: null,
  agent_gym_payoff: null,
  financial_summary: {
    consolidated_loan_amount: "$1,800,000.00",
    appraised_collateral_value: "$2,600,000.00",
    ltv_formatted: "69.23%",
    dscr_formatted: "1.44x"
  }
};

export function renderConsolidationCollab(state) {
  const st = state || FALLBACK_COLLAB_STATE;
  const beat = Number(st.beat || 0);
  const counts = st.counts || { total: 3, satisfied: 0, waived: 0, required: 3 };

  const beatLabels = {
    0: "Beat 0 of 4 · Initial 3-Doc Checklist",
    1: "Beat 1 of 4 · Debt Schedule Discovered (3 → 6 Items)",
    2: "Beat 2 of 4 · Cross-Doc $48.2K Payoff Wire Spotted",
    3: "Beat 3 of 4 · Underwriter 1-Click Waiver Approved (DONE!)",
    4: "Beat 4 of 4 · Agent Gym v1.1.0 Compiled & Day-2 Auto-Cleared"
  };
  const beatPill = document.getElementById("collab-beat-pill");
  if (beatPill) {
    beatPill.textContent = beatLabels[beat] || beatLabels[0];
    beatPill.className = st.done ? "badge badge-green" : "badge badge-purple";
  }

  const fin = st.financial_summary || {};
  const finLoan = document.getElementById("collab-fin-loan");
  if (finLoan && fin.consolidated_loan_amount) finLoan.textContent = fin.consolidated_loan_amount;
  const finApp = document.getElementById("collab-fin-appraisal");
  if (finApp && fin.appraised_collateral_value) finApp.textContent = fin.appraised_collateral_value;
  const finLtv = document.getElementById("collab-fin-ltv");
  if (finLtv && fin.ltv_formatted) finLtv.textContent = fin.ltv_formatted;
  const finDscr = document.getElementById("collab-fin-dscr");
  if (finDscr && fin.dscr_formatted) finDscr.textContent = fin.dscr_formatted;

  const progBadge = document.getElementById("collab-borrower-progress-badge");
  if (progBadge) {
    const resolvedCount = (counts.satisfied || 0) + (counts.waived || 0);
    if (st.done) {
      progBadge.className = "badge badge-green";
      progBadge.textContent = `✓ ${resolvedCount} / ${counts.total} Resolved (${counts.satisfied} Verified · ${counts.waived} Waived) · DONE`;
    } else {
      progBadge.className = "badge badge-amber";
      progBadge.textContent = `${resolvedCount} / ${counts.total} Resolved · ${counts.required} Required`;
    }
  }

  const bannerEl = document.getElementById("collab-borrower-banner");
  const bannerHead = document.getElementById("collab-banner-headline");
  const bannerMsg = document.getElementById("collab-banner-msg");
  if (bannerEl && st.borrower_banner) {
    const tone = st.borrower_banner.tone || "info";
    bannerEl.className = `collab-banner collab-banner-${tone}`;
    if (bannerHead) bannerHead.textContent = st.borrower_banner.headline || "";
    if (bannerMsg) {
      bannerMsg.innerHTML = `${st.borrower_banner.message || ""}${
        st.borrower_banner.agent_note
          ? `<div style="margin-top:4px;font-weight:700;font-size:11.5px;opacity:0.9;">⚡ ${st.borrower_banner.agent_note}</div>`
          : ""
      }`;
    }
  }

  const checklistEl = document.getElementById("collab-borrower-checklist");
  if (checklistEl && Array.isArray(st.borrower_checklist)) {
    checklistEl.innerHTML = st.borrower_checklist.map((item, idx) => {
      const status = item.status || "required";
      let badgeCls = "badge-amber";
      let icon = "⏳";
      if (status === "satisfied") {
        badgeCls = "badge-green";
        icon = "✓";
      } else if (status === "waived") {
        badgeCls = "badge-purple";
        icon = "✦";
      }
      const emergentPill = item.emergent
        ? `<span class="badge badge-navy" style="font-size:9.5px;padding:1px 6px;">DYNAMIC · NOTE 6</span>`
        : "";
      const citationChip = item.source_citation
        ? `<span class="anchor-chip" title="Click to open WORM PDF Source Proof">${item.source_citation}</span>`
        : "";
      return `
        <div class="collab-check-item status-${status}">
          <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;">
            <div style="font-weight:800;font-size:12.5px;color:#0f172a;">
              ${icon} ${idx + 1}. ${item.label} ${emergentPill}
            </div>
            <span class="badge ${badgeCls}">${item.badge_text || status.toUpperCase()}</span>
          </div>
          <div style="font-size:11.5px;color:#334155;margin-top:4px;display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;">
            <span>${item.reason || ""}</span>
            ${citationChip}
          </div>
        </div>
      `;
    }).join("");
  }

  const a1 = st.agent1_bridge_status || {};
  const revBadge = document.getElementById("collab-skill-rev-badge");
  if (revBadge && a1.skill_revision) revBadge.textContent = a1.skill_revision;

  const a1Box = document.getElementById("collab-agent1-box");
  if (a1Box) {
    const anchorsHtml = (a1.active_anchors || []).map(a => `
      <div style="display:flex;justify-content:space-between;align-items:center;font-size:11px;padding:4px 8px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:6px;margin-top:4px;">
        <span><strong>${a.field}:</strong> <span class="mono">${a.doc_title}</span></span>
        <span class="anchor-chip">${a.citation}</span>
      </div>
    `).join("");
    a1Box.innerHTML = `
      <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;">
        <strong style="font-size:12.5px;color:#0f172a;">⚡ ${a1.agent_name || "Agent 1 · Document Bridge"}</strong>
        <span class="badge badge-blue mono">${a1.model || "gemini-3.8-flash"} · ${a1.documents_processed || 0} Docs</span>
      </div>
      <div style="font-weight:700;font-size:12px;color:#1d4ed8;margin-top:4px;">${a1.headline || ""}</div>
      <div style="font-size:11.5px;color:#475569;margin-top:2px;">${a1.detail || ""}</div>
      ${anchorsHtml}
    `;
  }

  const a2 = st.agent2_policy_status || {};
  const a2Box = document.getElementById("collab-agent2-box");
  if (a2Box) {
    const mutHtml = (a2.checklist_mutations || []).map(m => `
      <div class="mono" style="font-size:11px;color:#0f172a;padding:3px 7px;background:#f1f5f9;border-radius:5px;margin-top:3px;">${m}</div>
    `).join("");
    a2Box.innerHTML = `
      <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;">
        <strong style="font-size:12.5px;color:#0f172a;">🧠 ${a2.agent_name || "Agent 2 · Bank Underwriting Agent"}</strong>
        <span class="badge badge-purple mono">${a2.model || "gemini-4-argon"} + Rule 0 Decimal</span>
      </div>
      <div style="font-weight:700;font-size:12px;color:#6d28d9;margin-top:4px;">${a2.headline || ""}</div>
      <div style="font-size:11.5px;color:#475569;margin-top:2px;">${a2.detail || ""}</div>
      ${mutHtml}
    `;
  }

  const owBox = document.getElementById("collab-overwatch-box");
  if (owBox) {
    if (st.agent_gym_payoff) {
      const gp = st.agent_gym_payoff;
      const casesHtml = (gp.stress_cases || []).map(c => `
        <div style="font-size:11px;color:#065f46;display:flex;justify-content:space-between;gap:6px;">
          <span>✓ <strong>${c.name}</strong></span>
          <span class="mono">${c.expected}</span>
        </div>
      `).join("");
      owBox.innerHTML = `
        <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;">
          <strong style="font-size:13px;color:#065f46;">🧬 Agent Gym Day-2 Payoff · Skill <span class="mono">${gp.skill_name}@${gp.skill_version}</span> Active</strong>
          <span class="badge badge-green">${gp.stress_cases_passed}/${gp.stress_cases_total} Stress Cases · ${gp.day2_latency_ms}ms Auto-Approve</span>
        </div>
        <div style="font-size:11.5px;color:#0f172a;margin-top:5px;">
          <strong>Day-2 Deal (<span class="mono">${gp.day2_entity_id}</span> · ${gp.day2_borrower}):</strong>
          Auto-waived EquipLease payoff letter via Bank Statement Page 2 wire in <strong>${gp.day2_latency_ms}ms</strong>
          (<strong>${gp.human_touchpoints}</strong> human touchpoints · <strong>${gp.escalation_tokens}</strong> escalation tokens).
        </div>
        <div style="margin-top:6px;padding:7px 9px;background:#ecfdf5;border:1px solid #a7f3d0;border-radius:7px;display:flex;flex-direction:column;gap:3px;">
          ${casesHtml}
        </div>
      `;
    } else if (st.underwriter_overwatch) {
      const uw = st.underwriter_overwatch;
      const isApproved = uw.status === "approved";
      owBox.innerHTML = `
        <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;">
          <strong style="font-size:13px;color:${isApproved ? '#059669' : '#6d28d9'};">
            ${isApproved ? '✓ ' : '🛡️ '}${uw.headline}
          </strong>
          <span class="badge ${isApproved ? 'badge-green' : 'badge-amber'}">
            ${isApproved ? 'APPROVED · RSR ' + uw.oversight_rsr_score : 'AWAITING 1-CLICK WAIVER'}
          </span>
        </div>
        <div style="font-size:11.5px;color:#334155;margin-top:4px;">${uw.recommendation}</div>
        <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;margin-top:7px;flex-wrap:wrap;">
          <span class="anchor-chip">${uw.citation_label || "Page 2 · Region [0.16, 0.54]"}</span>
          ${
            !isApproved
              ? `<button class="btn btn-success" style="padding:5px 12px;font-size:11.5px;" onclick="window.advanceConsolidationCollab(3)">✓ Approve Cross-Doc Waiver (Beat 3)</button>`
              : `<button class="btn btn-primary" style="padding:5px 12px;font-size:11.5px;" onclick="window.advanceConsolidationCollab(4)">🧬 Distill to Agent Gym &amp; Verify Day-2 (Beat 4)</button>`
          }
        </div>
      `;
    } else {
      owBox.innerHTML = `
        <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;">
          <strong style="font-size:12.5px;color:#475569;">🛡️ Underwriter Overwatch &amp; Agent Gym Continual Learning</strong>
          <span class="badge badge-navy">STANDBY · ZERO ESCALATIONS</span>
        </div>
        <div style="font-size:11.5px;color:#64748b;margin-top:4px;">
          Overwatch only interrupts the human underwriter when Agent 1 &amp; Agent 2 discover a cross-document exception or payoff proof requiring policy sign-off.
        </div>
      `;
    }
  }
}

export async function refreshConsolidationCollab() {
  try {
    const resp = await apiFetch("/v1/demo/consolidation-collab/state");
    if (resp.ok) {
      const state = await resp.json();
      renderConsolidationCollab(state);
      return state;
    }
  } catch (_) {}
  renderConsolidationCollab(FALLBACK_COLLAB_STATE);
  return FALLBACK_COLLAB_STATE;
}

export async function advanceConsolidationCollab(beat) {
  try {
    const resp = await apiFetch("/v1/demo/consolidation-collab/advance", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ beat: Number(beat) })
    });
    if (resp.ok) {
      const state = await resp.json();
      renderConsolidationCollab(state);
      setCaption(
        `COLLABORATIVE CONSOLIDATION · BEAT ${state.beat} OF 4`,
        state.borrower_banner ? state.borrower_banner.headline : "Collaborative checklist updated.",
        `POST /v1/demo/consolidation-collab/advance (beat=${state.beat}) -> satisfied=${state.counts.satisfied}, waived=${state.counts.waived}, required=${state.counts.required}, done=${state.done}`
      );
      return state;
    }
  } catch (_) {}
  return null;
}

export async function resetConsolidationCollab() {
  try {
    const resp = await apiFetch("/v1/demo/consolidation-collab/reset", {
      method: "POST"
    });
    if (resp.ok) {
      const state = await resp.json();
      renderConsolidationCollab(state);
      setCaption(
        "COLLABORATIVE CONSOLIDATION · RESET TO BEAT 0",
        ((stageScenario("consolidation", window.currentDomain) || { display: { labels: {} } }).display.labels.reset_caption) || "Restored the consolidation stage to its initial checklist.",
        "POST /v1/demo/consolidation-collab/reset -> beat=0, satisfied=0, waived=0, required=3, done=false"
      );
      return state;
    }
  } catch (_) {}
  renderConsolidationCollab(FALLBACK_COLLAB_STATE);
  return FALLBACK_COLLAB_STATE;
}
