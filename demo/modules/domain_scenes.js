import {
  apiFetch,
  applyScenarioLabels,
  getActiveEntityId,
  scenarioLabel,
  setCaption,
  stageScenario,
  switcherDomains,
} from "./api_state.js";

export function switchScene(sceneId, stepIndex, isExternalPortal) {
  document.getElementById("portal-header").style.display = isExternalPortal ? "flex" : "none";
  document.getElementById("internal-header").style.display = isExternalPortal ? "none" : "block";

  document.querySelectorAll(".scene").forEach(el => el.classList.remove("active"));
  const target = document.getElementById(sceneId);
  if (target) target.classList.add("active");

  const jIntake = document.getElementById("journey-intake");
  const jGym = document.getElementById("journey-gym");
  if (stepIndex <= 2) {
    jIntake.classList.add("active");
    jGym.classList.remove("active");
  } else {
    jIntake.classList.remove("active");
    jGym.classList.add("active");
  }

  for (let i = 1; i <= 5; i++) {
    const pill = document.getElementById("step-pill-" + i);
    const num = document.getElementById("step-num-" + i);
    if (!pill || !num) continue;
    pill.classList.remove("active", "done");
    if (i < stepIndex) {
      pill.classList.add("done");
      num.textContent = "✓";
    } else if (i === stepIndex) {
      pill.classList.add("active");
      num.textContent = String(i);
    } else {
      num.textContent = String(i);
    }
  }

  const domLabel = scenarioLabel(window.currentDomain, "domain_label", String(window.currentDomain || "").toUpperCase());
  if (sceneId === "scene-intake") {
    setCaption(
      `STAGE 1 · DUAL-PATH INTAKE & WORM VAULT (${domLabel})`,
      scenarioLabel(window.currentDomain, "intake_caption"),
      scenarioLabel(window.currentDomain, "intake_caption_sub")
    );
  } else if (sceneId === "scene-consolidation") {
    refreshScene2LineageAndSync();
    setCaption(
      `STAGE 2 · BI-TEMPORAL CONSOLIDATION & 6-HOP LINEAGE (${domLabel})`,
      scenarioLabel(window.currentDomain, "consolidation_caption"),
      "GET /v1/catalog/lineage -> INBOUND -> AUDIT_VAULT -> OPERATIONAL_STORE -> BUSINESS_VIEW -> BIGQUERY_ICEBERG -> USER_INTERACTION_A2UI"
    );
  } else if (sceneId === "scene-inbox") {
    setCaption(
      `STAGE 3 · SPIFFE OBO AUTHORITY GATE & A2UI v0.9 INBOX (${domLabel})`,
      "Compound Identity enforces Agent(SPIFFE) ∩ User(Principal) authority limits before committing RFC 6902 JSON Patch overrides.",
      "spiffe://agents.docubridge.io/internal/credit-officer-agent | Catalog: https://docubridge.io/a2ui/v0_9/catalogs/enterprise_inbox_catalog.json"
    );
  } else if (sceneId === "scene-gym") {
    setCaption(
      `STAGE 4 · AGENT GYM & PROGRAMMABLE TRUST DIAL (${domLabel})`,
      "Distills daytime expert corrections into Tribal Knowledge Rules, verifies 4 synthetic stress cases + Decimal firewall, and proves Day-2 Auto-Approve.",
      "POST /v1/gym/signals/from-quarantine -> POST /v1/gym/rules/{id}/evaluate -> POST /v1/intake/ingest (Day-2 Payoff)"
    );
  } else if (sceneId === "scene-telemetry") {
    if (typeof window.refreshOtelWaterfall === "function") {
      window.refreshOtelWaterfall();
    }
    setCaption(
      `STAGE 5 · W3C OPENTELEMETRY TRACE WATERFALL & BIGQUERY X-RAY (${domLabel})`,
      "Live sub-millisecond W3C traceparent spans across all 6 architecture zones & Agent Gym streaming to BigQuery (docubridge-showcase.docubridge_telemetry.otel_spans).",
      "GET /v1/telemetry/analytics -> v_trace_waterfall + v_step_latency_analytics + v_entity_pipeline_summary"
    );
  }
}

export function switchDomain(domain) {
  const known = switcherDomains();
  window.currentDomain = (known.length === 0 || known.includes(domain)) ? (domain || "banking") : known[0];
  document.querySelectorAll(".domain-btn").forEach(btn => {
    btn.classList.toggle("active", btn.id === "domain-btn-" + window.currentDomain);
  });

  const day2Banner = document.getElementById("day2-payoff-banner");
  if (day2Banner) day2Banner.style.display = "none";
  const c2Badge = document.getElementById("chubb-intake-badge");
  if (c2Badge) {
    c2Badge.className = "badge badge-amber";
    c2Badge.textContent = "Quarantined · Review Needed";
  }
  const activeEntityEl = document.getElementById("otel-active-entity");
  if (activeEntityEl) activeEntityEl.textContent = getActiveEntityId();
  const collabStage = document.getElementById("banking-collab-stage");
  if (collabStage) {
    collabStage.style.display = stageScenario("consolidation", window.currentDomain) ? "block" : "none";
  }
  const schemaEvolStage = document.getElementById("schema-evolution-stage");
  if (schemaEvolStage) {
    schemaEvolStage.style.display = stageScenario("schema_evolution", window.currentDomain) ? "block" : "none";
  }

  if (window.currentDomain === "banking") {
    document.getElementById("portal-path-a-file").textContent = "📄 meridian-rent-roll-dscr-2026.xlsx";
    document.getElementById("portal-path-a-meta").textContent = "38 Commercial Tenants · Net Operating Income $4,185,000.00 · DSCR 1.42x";
    document.getElementById("portal-path-b-file").textContent = "📑 syndicated-credit-agreement-meridian-2026.pdf";
    document.getElementById("portal-path-b-meta").textContent = "Cover Term Sheet ($50,000 Origination Fee) + Page 4 Schedule B Fee Addendum ($100,000)";

    document.getElementById("intake-page-title").textContent = "Dual-Path Intake & Two-Zone Cloud Storage (Banking · Loan Underwriting)";
    document.getElementById("intake-page-subtitle").textContent =
      "Path A validates structured rent rolls & schedules in code (0 AI tokens); Path B extracts layout-anchored credit agreements & appraisals, masks guarantor SSNs at the edge, and gates conflicts into Review Quarantine.";
    document.getElementById("intake-c1-title").textContent = "Meridian Commercial Rent Roll & DSCR";
    document.getElementById("intake-c1-file").textContent = "meridian-rent-roll-dscr-2026.xlsx";
    document.getElementById("intake-c1-body").innerHTML = `
      <div><strong>Net Operating Income (NOI):</strong> <span class="mono">$4,185,000.00</span> (38 commercial tenants · DSCR <span class="mono">1.42x</span>)</div>
      <div><strong>Edge PII Redaction:</strong> <span class="mono">2 guarantor SSNs masked ([REDACTED-SSN])</span></div>
      <div><strong>Zone 3 Audit Vault:</strong> <span class="mono" style="color:#059669;">gs://docubridge-audit-vault/vault/meridian/sha256:9f84...e21c</span></div>
      <div><strong>Retention Lock:</strong> Locked 7 Years (2,555 days · Write-Once WORM)</div>
    `;
    document.getElementById("intake-c2-title").textContent = "Syndicated Credit Agreement & Addendum";
    document.getElementById("intake-c2-file").textContent = "syndicated-credit-agreement-meridian-2026.pdf";
    document.getElementById("intake-c2-body").innerHTML = `
      <div><strong>Cover Term Sheet Fee:</strong> <span class="mono">$50,000</span> <span class="anchor-chip" title="Click to inspect source PDF bounding box">Page 1 · Region [0.14, 0.31]</span></div>
      <div><strong>Schedule B Fee Addendum:</strong> <span class="mono" style="color:#b45309;font-weight:700;">$100,000 (Origination &amp; Rate-Lock)</span> <span class="anchor-chip" title="Click to inspect source PDF bounding box">Page 4 · Region [0.12, 0.44]</span></div>
      <div><strong>Zone 2 Quarantine URI:</strong> <span class="mono">gs://docubridge-artifacts/quarantine/doc_meridian_credit_001</span></div>
      <div><strong>4-Signal Gate:</strong> <span class="mono" style="color:#b45309;">awaiting_human_review (Schedule B fee override conflict)</span></div>
    `;
    document.getElementById("intake-c3-title").textContent = "CBRE · MAI Commercial Appraisal Report";
    document.getElementById("intake-c3-file").textContent = "cbre-mai-commercial-appraisal-2026.pdf";
    document.getElementById("intake-c3-chip").textContent = "⚡ gemini-3.8-flash · Multi-Window Table Merging (Pages 1–24)";
    document.getElementById("intake-c3-body").innerHTML = `
      <div><strong>Appraised Collateral Value:</strong> <span class="mono">$62,500,000.00</span> <span class="anchor-chip" title="Click to inspect source PDF bounding box">Page 11 · Region [0.18, 0.52]</span></div>
      <div><strong>Implied Loan-to-Value (LTV):</strong> <span class="mono">72.00% ($45.0M / $62.5M)</span> <span class="anchor-chip" title="Click to inspect source PDF bounding box">Page 14 · Region [0.22, 0.61]</span></div>
      <div><strong>Zone 3 Audit Vault:</strong> <span class="mono" style="color:#059669;">gs://docubridge-audit-vault/vault/meridian/sha256:4a19...c802</span></div>
      <div><strong>4-Signal Gate:</strong> <span class="mono" style="color:#059669;">auto_approved (Confidence 99% · 0 anomalies)</span></div>
    `;

    document.getElementById("view-btn-broker").textContent = "Credit Officer View (Deal & Conflicts)";
    document.getElementById("view-btn-underwriter").textContent = "Underwriter View (LTV, DSCR & Fee Exposure)";
    document.getElementById("view-btn-claims").textContent = "Covenant / Audit View (Effective-Date Terms)";
    document.getElementById("active-view-title").textContent = "Role-Based Projection: Credit Officer View (Deal & Conflicts)";
    document.getElementById("consolidation-tbody").innerHTML = `
      <tr>
        <td><strong>Appraised Collateral Value</strong></td>
        <td class="mono" style="font-weight:700;">$62,500,000.00</td>
        <td>CBRE MAI Appraisal <span class="badge badge-navy">Prio 20</span></td>
        <td class="mono" style="font-size:11.5px;">Learned: 2026-10-19 20:54Z<br/>Effective: 2026-11-01</td>
        <td><span class="anchor-chip">Page 11 · Region [0.18, 0.52]</span></td>
        <td><span class="badge badge-green">✓ Reconciled</span></td>
      </tr>
      <tr>
        <td><strong>Senior Term Loan Principal</strong></td>
        <td class="mono" style="font-weight:700;">$45,000,000.00 <span class="badge badge-blue">LTV 72.00%</span></td>
        <td>Syndicated Credit Agreement <span class="badge badge-blue">Prio 30</span></td>
        <td class="mono" style="font-size:11.5px;">Learned: 2026-10-19 20:55Z<br/>Effective: 2026-11-01</td>
        <td><span class="anchor-chip">Page 2 · Region [0.15, 0.28]</span></td>
        <td><span class="badge badge-green">✓ Verified</span></td>
      </tr>
      <tr class="highlight-row">
        <td><strong>Origination &amp; Rate-Lock Fee</strong></td>
        <td class="mono" style="font-weight:800;color:#b45309;">$100,000.00</td>
        <td>Schedule B Fee Addendum <span class="badge badge-purple">Prio 40</span></td>
        <td class="mono" style="font-size:11.5px;">Learned: 2026-10-19 20:57Z<br/>Effective: 2026-11-01</td>
        <td><span class="anchor-chip">Page 4 · Region [0.12, 0.44]</span></td>
        <td><span class="badge badge-amber">⚠ Conflict vs Cover Term Sheet ($50,000)</span></td>
      </tr>
      <tr>
        <td><strong>Net Operating Income (NOI)</strong></td>
        <td class="mono" style="font-weight:700;">$4,185,000.00 <span class="badge badge-green">DSCR 1.42x</span></td>
        <td>Validated Rent Roll Excel <span class="badge badge-green">Prio 20</span></td>
        <td class="mono" style="font-size:11.5px;">Learned: 2026-10-19 20:56Z<br/>Effective: 2026-11-01</td>
        <td><span class="anchor-chip">Rent Roll Tab 1 · Row 38</span></td>
        <td><span class="badge badge-green">✓ Verified</span></td>
      </tr>
      <tr class="success-row">
        <td><strong>Borrower Entity Resolution</strong></td>
        <td class="mono" style="font-weight:700;">Meridian Logistics (Canonical)</td>
        <td>Agent Gym Rule <span class="mono">rule-entity-meridian</span></td>
        <td class="mono" style="font-size:11.5px;">Mapped "Meridian Logistics Holdings Ltd." → "Meridian Logistics"<br/>"Meridian SPV II" kept distinct</td>
        <td><span class="anchor-chip">Page 1 · Preamble</span></td>
        <td><span class="badge badge-green">✓ Canonicalized</span></td>
      </tr>
    `;
    document.getElementById("lineage-hops-box").innerHTML = `
      <div style="padding:7px 12px;background:#f8fafc;border-left:3px solid var(--brand-slate);border-radius:6px;">
        <strong>1. Intake Document:</strong> <span class="mono">syndicated-credit-agreement-meridian-2026.pdf (Page 4)</span>
      </div>
      <div style="padding:7px 12px;background:#f8fafc;border-left:3px solid #6d28d9;border-radius:6px;">
        <strong>2. Extraction Engine:</strong> <span class="mono">Document AI Layout + Agent Gym Hint</span>
      </div>
      <div style="padding:7px 12px;background:#f8fafc;border-left:3px solid #059669;border-radius:6px;">
        <strong>3. Audit Vault:</strong> <span class="mono">gs://docubridge-audit-vault/vault/LOAN-MERIDIAN-001/doc-meridian-addendum-v2</span>
      </div>
      <div style="padding:7px 12px;background:#f8fafc;border-left:3px solid #d97706;border-radius:6px;">
        <strong>4. Consolidation Rule:</strong> <span class="mono">Addendum Overrides Term Sheet (Prio 40 &gt; Prio 20)</span>
      </div>
      <div style="padding:7px 12px;background:#f8fafc;border-left:3px solid #1d4ed8;border-radius:6px;">
        <strong>5. Operational Store:</strong> <span class="mono">alloydb://docubridge-enterprise-data/operational/bridge_operational_facts</span>
      </div>
      <div style="padding:7px 12px;background:#f8fafc;border-left:3px solid var(--brand-primary);border-radius:6px;">
        <strong>6. User Interface Card:</strong> <span class="mono">a2ui://citation/LOAN-MERIDIAN-001/origination_fee#page=4</span>
      </div>
    `;

    document.getElementById("inbox-card-sub").innerHTML = 'Proposed by Agent: <span class="mono">$50,000</span> (Cover Term Sheet) vs. Schedule B Fee Addendum: <span class="mono">$100,000</span> (<span class="anchor-chip">Page 4 · Region [0.12, 0.44]</span>)';
    document.getElementById("inbox-field-label").textContent = "VERIFIED ORIGINATION FEE VALUE";
    document.getElementById("input-verified-deductible").value = "$100,000";
    document.getElementById("input-expert-memo").value = "Executed Schedule B fee addendum on page 4 supersedes cover term sheet origination fee ($50,000 -> $100,000).";
    document.getElementById("rfc6902-patch-label").textContent = 'Audit Diff: [{"op":"replace","path":"/origination_fee_usd","value":"$100,000"}]';

    document.getElementById("gym-slice-header").textContent = "TARGET SLICE ACCURACY (SYNDICATED* · ADDENDUM · ORIGINATION_FEE) · WORKHORSE: GEMINI-3.8-FLASH";
    document.getElementById("gym-rule-summary-line").innerHTML = '<strong>Promoted Rule <span class="mono" id="live-rule-id">rule-banking-01</span>:</strong> When extracting <span class="mono">origination_fee</span> from <span class="mono">Syndicated*</span> addendum documents (see page 4), map <span class="mono">$50,000 → $100,000</span> (Schedule B fee addendum overrides cover term sheet).';
    document.getElementById("gate-detail").textContent =
      "Verified against layout shifts (cover term sheet → Schedule B fee addendum), legal suffix variants (Meridian Logistics Holdings Ltd. → Meridian Logistics), distinct SPV collision guard (Meridian SPV II kept separate), and deterministic Decimal math firewall.";
    document.getElementById("btn-promote-trust").textContent = "3. Promote Credit Addendum Slice to Trusted Auto-Approve";
  } else if (window.currentDomain === "mortgage") {
    document.getElementById("portal-path-a-file").textContent = "📄 westbrook-1003-urla-4506c-2026.json";
    document.getElementById("portal-path-a-meta").textContent = "MISMO 3.4 URLA (Form 1003) + IRS 4506-C Transcripts · DTI 31.4% · 18 Mos Reserves";
    document.getElementById("portal-path-b-file").textContent = "📑 firstnational-trid-closing-disclosure-2026.pdf";
    document.getElementById("portal-path-b-meta").textContent = "Initial Loan Estimate ($4,250 Origination) + Page 3 TRID Closing Disclosure Rate-Lock Extension ($8,500)";

    document.getElementById("intake-page-title").textContent = "Dual-Path Intake & Two-Zone Cloud Storage (Mortgage · TRID Closing & Underwriting)";
    document.getElementById("intake-page-subtitle").textContent =
      "Path A validates MISMO 3.4 Form 1003 URLA & IRS 4506-C tax transcripts in code (0 AI tokens); Path B extracts layout-anchored TRID Closing Disclosures & Form 1004 appraisals, masks borrower SSNs at the edge, and gates TRID tolerance conflicts into Review Quarantine.";
    document.getElementById("intake-c1-title").textContent = "Form 1003 URLA & IRS 4506-C Income";
    document.getElementById("intake-c1-file").textContent = "westbrook-1003-urla-4506c-2026.json";
    document.getElementById("intake-c1-body").innerHTML = `
      <div><strong>Qualifying Income &amp; DTI:</strong> <span class="mono">$442,000.00/yr</span> (Back-End DTI <span class="mono">31.4%</span> · 18 mos post-close reserves)</div>
      <div><strong>Edge PII Redaction:</strong> <span class="mono">2 borrower SSNs &amp; bank account #s masked ([REDACTED-SSN])</span></div>
      <div><strong>Zone 3 Audit Vault:</strong> <span class="mono" style="color:#059669;">gs://docubridge-audit-vault/vault/westbrook/sha256:7c31...a904</span></div>
      <div><strong>Retention Lock:</strong> Locked 7 Years (2,555 days · Write-Once WORM)</div>
    `;
    document.getElementById("intake-c2-title").textContent = "TRID Closing Disclosure & Rate-Lock Addendum";
    document.getElementById("intake-c2-file").textContent = "firstnational-trid-closing-disclosure-2026.pdf";
    document.getElementById("intake-c2-body").innerHTML = `
      <div><strong>Initial Loan Estimate (LE) Fee:</strong> <span class="mono">$4,250</span> <span class="anchor-chip" title="Click to inspect source PDF bounding box">Page 2 · Region [0.11, 0.24]</span></div>
      <div><strong>Final TRID Closing Disclosure:</strong> <span class="mono" style="color:#b45309;font-weight:700;">$8,500 (Section A + 45-Day Rate-Lock Extension)</span> <span class="anchor-chip" title="Click to inspect source PDF bounding box">Page 3 · Region [0.11, 0.48]</span></div>
      <div><strong>Zone 2 Quarantine URI:</strong> <span class="mono">gs://docubridge-artifacts/quarantine/doc_westbrook_cd_001</span></div>
      <div><strong>4-Signal Gate:</strong> <span class="mono" style="color:#b45309;">awaiting_human_review (TRID Section A rate-lock fee variance)</span></div>
    `;
    document.getElementById("intake-c3-title").textContent = "Form 1004 · Uniform Residential Appraisal";
    document.getElementById("intake-c3-file").textContent = "form-1004-residential-appraisal-westbrook.pdf";
    document.getElementById("intake-c3-chip").textContent = "⚡ gemini-3.8-flash · Comp Sales Grid & LTV Verification (Pages 1–16)";
    document.getElementById("intake-c3-body").innerHTML = `
      <div><strong>Appraised Market Value:</strong> <span class="mono">$2,400,000.00</span> <span class="anchor-chip" title="Click to inspect source PDF bounding box">Page 6 · Region [0.16, 0.61]</span></div>
      <div><strong>Implied Loan-to-Value (LTV):</strong> <span class="mono">77.08% ($1.85M / $2.40M)</span> <span class="anchor-chip" title="Click to inspect source PDF bounding box">Page 9 · Region [0.20, 0.54]</span></div>
      <div><strong>Zone 3 Audit Vault:</strong> <span class="mono" style="color:#059669;">gs://docubridge-audit-vault/vault/westbrook/sha256:8e12...f441</span></div>
      <div><strong>4-Signal Gate:</strong> <span class="mono" style="color:#059669;">auto_approved (Confidence 99% · 5 comparable sales verified)</span></div>
    `;

    document.getElementById("view-btn-broker").textContent = "Mortgage Underwriter View (TRID & LTV)";
    document.getElementById("view-btn-underwriter").textContent = "Closing Desk View (Cash-to-Close & Fees)";
    document.getElementById("view-btn-claims").textContent = "Compliance / QC View (TRID Tolerance Audit)";
    document.getElementById("active-view-title").textContent = "Role-Based Projection: Mortgage Underwriter View (TRID & LTV)";
    document.getElementById("consolidation-tbody").innerHTML = `
      <tr>
        <td><strong>Form 1004 Appraised Value</strong></td>
        <td class="mono" style="font-weight:700;">$2,400,000.00</td>
        <td>Form 1004 Appraisal <span class="badge badge-navy">Prio 20</span></td>
        <td class="mono" style="font-size:11.5px;">Learned: 2026-10-19 18:00Z<br/>Effective: 2026-11-01</td>
        <td><span class="anchor-chip">Page 6 · Region [0.16, 0.61]</span></td>
        <td><span class="badge badge-green">✓ Reconciled</span></td>
      </tr>
      <tr>
        <td><strong>First-Lien Jumbo Loan Amount</strong></td>
        <td class="mono" style="font-weight:700;">$1,850,000.00 <span class="badge badge-blue">LTV 77.08%</span></td>
        <td>TRID Closing Disclosure <span class="badge badge-blue">Prio 40</span></td>
        <td class="mono" style="font-size:11.5px;">Learned: 2026-10-19 20:00Z<br/>Effective: 2026-11-01</td>
        <td><span class="anchor-chip">Page 1 · Region [0.14, 0.19]</span></td>
        <td><span class="badge badge-green">✓ Verified</span></td>
      </tr>
      <tr class="highlight-row">
        <td><strong>Section A Origination &amp; Rate-Lock Fee</strong></td>
        <td class="mono" style="font-weight:800;color:#b45309;">$8,500.00</td>
        <td>Final TRID Closing Disclosure <span class="badge badge-purple">Prio 40</span></td>
        <td class="mono" style="font-size:11.5px;">Learned: 2026-10-19 20:00Z<br/>Effective: 2026-11-01</td>
        <td><span class="anchor-chip">Page 3 · Region [0.11, 0.48]</span></td>
        <td><span class="badge badge-amber">⚠ Conflict vs Initial Loan Estimate ($4,250)</span></td>
      </tr>
      <tr>
        <td><strong>Annual Housing Debt Service</strong></td>
        <td class="mono" style="font-weight:700;">$138,400.00 <span class="badge badge-green">DTI 31.4%</span></td>
        <td>Validated URLA 1003 + 4506-C <span class="badge badge-green">Prio 20</span></td>
        <td class="mono" style="font-size:11.5px;">Learned: 2026-10-19 18:00Z<br/>Effective: 2026-11-01</td>
        <td><span class="anchor-chip">URLA Section 4 · Line 4a</span></td>
        <td><span class="badge badge-green">✓ Verified</span></td>
      </tr>
      <tr class="success-row">
        <td><strong>Vesting Trust Entity Resolution</strong></td>
        <td class="mono" style="font-weight:700;">Westbrook Family Trust (Canonical)</td>
        <td>Agent Gym Rule <span class="mono">rule-entity-westbrook</span></td>
        <td class="mono" style="font-size:11.5px;">Mapped "Westbrook Revocable Living Trust DTD 2019" → "Westbrook Family Trust"</td>
        <td><span class="anchor-chip">Page 1 · Vesting Addendum</span></td>
        <td><span class="badge badge-green">✓ Canonicalized</span></td>
      </tr>
    `;
    document.getElementById("lineage-hops-box").innerHTML = `
      <div style="padding:7px 12px;background:#f8fafc;border-left:3px solid var(--brand-slate);border-radius:6px;">
        <strong>1. Intake Document:</strong> <span class="mono">firstnational-trid-closing-disclosure-2026.pdf (Page 3)</span>
      </div>
      <div style="padding:7px 12px;background:#f8fafc;border-left:3px solid #6d28d9;border-radius:6px;">
        <strong>2. Extraction Engine:</strong> <span class="mono">Document AI Layout + Agent Gym TRID Hint</span>
      </div>
      <div style="padding:7px 12px;background:#f8fafc;border-left:3px solid #059669;border-radius:6px;">
        <strong>3. Audit Vault:</strong> <span class="mono">gs://docubridge-audit-vault/vault/MORTGAGE-WESTBROOK-2026/doc-westbrook-cd-v2</span>
      </div>
      <div style="padding:7px 12px;background:#f8fafc;border-left:3px solid #d97706;border-radius:6px;">
        <strong>4. Consolidation Rule:</strong> <span class="mono">Closing Disclosure Overrides Loan Estimate (Prio 40 &gt; Prio 20)</span>
      </div>
      <div style="padding:7px 12px;background:#f8fafc;border-left:3px solid #1d4ed8;border-radius:6px;">
        <strong>5. Operational Store:</strong> <span class="mono">alloydb://docubridge-enterprise-data/operational/bridge_operational_facts</span>
      </div>
      <div style="padding:7px 12px;background:#f8fafc;border-left:3px solid var(--brand-primary);border-radius:6px;">
        <strong>6. User Interface Card:</strong> <span class="mono">a2ui://citation/MORTGAGE-WESTBROOK-2026/origination_fee#page=3</span>
      </div>
    `;

    document.getElementById("inbox-card-sub").innerHTML = 'Proposed by Agent: <span class="mono">$4,250</span> (Initial Loan Estimate) vs. Signed TRID Closing Disclosure: <span class="mono">$8,500</span> (<span class="anchor-chip">Page 3 · Region [0.11, 0.48]</span>)';
    document.getElementById("inbox-field-label").textContent = "VERIFIED TRID SECTION A ORIGINATION FEE";
    document.getElementById("input-verified-deductible").value = "$8,500";
    document.getElementById("input-expert-memo").value = "Borrower-signed 45-day rate-lock extension addendum on TRID Closing Disclosure Page 3 updates Section A origination fee ($4,250 -> $8,500) with valid Changed Circumstance.";
    document.getElementById("rfc6902-patch-label").textContent = 'Audit Diff: [{"op":"replace","path":"/origination_fee","value":"$8,500"}]';

    document.getElementById("gym-slice-header").textContent = "TARGET SLICE ACCURACY (FIRSTNATIONAL* · CLOSING-DISCLOSURE · ORIGINATION_FEE) · WORKHORSE: GEMINI-3.8-FLASH";
    document.getElementById("gym-rule-summary-line").innerHTML = '<strong>Promoted Rule <span class="mono" id="live-rule-id">rule-mortgage-01</span>:</strong> When extracting <span class="mono">origination_fee</span> from <span class="mono">FirstNational*</span> closing-disclosure documents (see page 3), map <span class="mono">$4,250 → $8,500</span> (TRID Section A rate-lock extension addendum).';
    document.getElementById("gate-detail").textContent =
      "Verified against TRID layout shifts (Loan Estimate Page 2 → Closing Disclosure Page 3 Section A), vesting trust suffix variants (Westbrook Revocable Living Trust → Westbrook Family Trust), co-borrower entity guard, and deterministic Decimal LTV/DTI firewall.";
    document.getElementById("btn-promote-trust").textContent = "3. Promote Mortgage TRID Closing Slice to Trusted Auto-Approve";
  } else {
    document.getElementById("portal-path-a-file").textContent = "📄 terrepower-coastal-sov-2027.xlsx";
    document.getElementById("portal-path-a-meta").textContent = "14 Coastal Locations · Total Insured Value $142,500,000 · Exact Decimal schema check";
    document.getElementById("portal-path-b-file").textContent = "📑 chubb-coastal-property-quote-2027.pdf";
    document.getElementById("portal-path-b-meta").textContent = "Cover Page ($50,000 Base Deductible) + Page 4 Schedule B Coastal Wind Endorsement ($100,000)";

    document.getElementById("intake-page-title").textContent = "Dual-Path Intake & Two-Zone Cloud Storage (Insurance · Commercial Placement)";
    document.getElementById("intake-page-subtitle").textContent =
      "Path A validates structured property schedules in code (0 AI tokens); Path B extracts layout-anchored carrier quotes & binders, masks claimant SSNs at the edge, and gates conflicts into Review Quarantine.";
    document.getElementById("intake-c1-title").textContent = "Terrepower Property Schedule (SOV)";
    document.getElementById("intake-c1-file").textContent = "terrepower-coastal-sov-2027.xlsx";
    document.getElementById("intake-c1-body").innerHTML = `
      <div><strong>Total Insured Value:</strong> <span class="mono">$142,500,000.00</span> (14 coastal locations)</div>
      <div><strong>Edge PII Redaction:</strong> <span class="mono">2 claimant SSNs masked ([REDACTED-SSN])</span></div>
      <div><strong>Zone 3 Audit Vault:</strong> <span class="mono" style="color:#059669;">gs://docubridge-audit-vault/vault/terrepower/sha256:9f84...e21c</span></div>
      <div><strong>Retention Lock:</strong> Locked 7 Years (2,555 days · Write-Once WORM)</div>
    `;
    document.getElementById("intake-c2-title").textContent = "Chubb · Commercial Property Quote";
    document.getElementById("intake-c2-file").textContent = "chubb-coastal-property-quote-2027.pdf";
    document.getElementById("intake-c2-body").innerHTML = `
      <div><strong>Cover Page Deductible:</strong> <span class="mono">$50,000</span> <span class="anchor-chip">Page 1 · Region [0.14, 0.31]</span></div>
      <div><strong>Schedule B Endorsement:</strong> <span class="mono" style="color:#b45309;font-weight:700;">$100,000 (Coastal Wind)</span> <span class="anchor-chip">Page 4 · Region [0.12, 0.44]</span></div>
      <div><strong>Zone 2 Quarantine URI:</strong> <span class="mono">gs://docubridge-artifacts/quarantine/doc_chubb_001</span></div>
      <div><strong>4-Signal Gate:</strong> <span class="mono" style="color:#b45309;">awaiting_human_review (Schedule B override conflict)</span></div>
    `;
    document.getElementById("intake-c3-title").textContent = "AIG · Signed Binder & Endorsement";
    document.getElementById("intake-c3-file").textContent = "aig-excess-property-binder-2027.pdf";
    document.getElementById("intake-c3-chip").textContent = "⚡ gemini-3.8-flash · Multi-Window Table Merging (Pages 1–18)";
    document.getElementById("intake-c3-body").innerHTML = `
      <div><strong>Layer Limit:</strong> <span class="mono">$25,000,000 xs $50,000,000</span> <span class="anchor-chip">Page 11 · Region [0.18, 0.52]</span></div>
      <div><strong>Annual Premium:</strong> <span class="mono">$412,000.00</span> <span class="anchor-chip">Page 14 · Region [0.22, 0.61]</span></div>
      <div><strong>Zone 3 Audit Vault:</strong> <span class="mono" style="color:#059669;">gs://docubridge-audit-vault/vault/terrepower/sha256:4a19...c802</span></div>
      <div><strong>4-Signal Gate:</strong> <span class="mono" style="color:#059669;">auto_approved (Confidence 99% · 0 anomalies)</span></div>
    `;

    document.getElementById("view-btn-broker").textContent = "Broker View (Market & Conflicts)";
    document.getElementById("view-btn-underwriter").textContent = "Underwriter View (Risk & Deductibles)";
    document.getElementById("view-btn-claims").textContent = "Claims View (Effective-Date Coverage)";
    document.getElementById("active-view-title").textContent = "Role-Based Projection: Broker View (Market & Conflicts)";
    document.getElementById("consolidation-tbody").innerHTML = `
      <tr>
        <td><strong>Total Insured Value</strong></td>
        <td class="mono" style="font-weight:700;">$142,500,000.00</td>
        <td>Signed Property Schedule <span class="badge badge-navy">Prio 30</span></td>
        <td class="mono" style="font-size:11.5px;">Learned: 2026-10-17 18:54Z<br/>Effective: 2027-01-01</td>
        <td><span class="anchor-chip">Schedule Tab 1 · Row 14</span></td>
        <td><span class="badge badge-green">✓ Reconciled</span></td>
      </tr>
      <tr class="highlight-row">
        <td><strong>Coastal Wind Deductible</strong></td>
        <td class="mono" style="font-weight:800;color:#b45309;">$100,000.00</td>
        <td>Schedule B Endorsement <span class="badge badge-purple">Prio 40</span></td>
        <td class="mono" style="font-size:11.5px;">Learned: 2026-10-17 18:57Z<br/>Effective: 2027-01-01</td>
        <td><span class="anchor-chip">Page 4 · Region [0.12, 0.44]</span></td>
        <td><span class="badge badge-amber">⚠ Conflict vs Cover Page ($50,000)</span></td>
      </tr>
      <tr>
        <td><strong>Primary Property Limit</strong></td>
        <td class="mono" style="font-weight:700;">$50,000,000.00</td>
        <td>Chubb Primary Quote <span class="badge badge-blue">Prio 20</span></td>
        <td class="mono" style="font-size:11.5px;">Learned: 2026-10-17 18:55Z<br/>Effective: 2027-01-01</td>
        <td><span class="anchor-chip">Page 2 · Region [0.15, 0.28]</span></td>
        <td><span class="badge badge-green">✓ Verified</span></td>
      </tr>
      <tr>
        <td><strong>Excess Layer 1 Limit</strong></td>
        <td class="mono" style="font-weight:700;">$25,000,000.00</td>
        <td>AIG Signed Binder <span class="badge badge-green">Prio 30</span></td>
        <td class="mono" style="font-size:11.5px;">Learned: 2026-10-17 18:56Z<br/>Effective: 2027-01-01</td>
        <td><span class="anchor-chip">Page 11 · Region [0.18, 0.52]</span></td>
        <td><span class="badge badge-green">✓ Verified</span></td>
      </tr>
      <tr class="success-row">
        <td><strong>Carrier Entity Resolution</strong></td>
        <td class="mono" style="font-weight:700;">Campala (Canonical)</td>
        <td>Agent Gym Rule <span class="mono">rule-entity-01</span></td>
        <td class="mono" style="font-size:11.5px;">Mapped "Campala Ltd." → "Campala"<br/>Syndicate 2987 ≠ 1183 preserved</td>
        <td><span class="anchor-chip">Page 1 · Header</span></td>
        <td><span class="badge badge-green">✓ Canonicalized</span></td>
      </tr>
    `;
    document.getElementById("lineage-hops-box").innerHTML = `
      <div style="padding:7px 12px;background:#f8fafc;border-left:3px solid var(--brand-slate);border-radius:6px;">
        <strong>1. Intake Document:</strong> <span class="mono">chubb-coastal-property-quote-2027.pdf (Page 4)</span>
      </div>
      <div style="padding:7px 12px;background:#f8fafc;border-left:3px solid #6d28d9;border-radius:6px;">
        <strong>2. Extraction Engine:</strong> <span class="mono">Document AI Layout + Agent Gym Hint</span>
      </div>
      <div style="padding:7px 12px;background:#f8fafc;border-left:3px solid #059669;border-radius:6px;">
        <strong>3. Audit Vault:</strong> <span class="mono">gs://docubridge-audit-vault/vault/terrepower/chubb#v1</span>
      </div>
      <div style="padding:7px 12px;background:#f8fafc;border-left:3px solid #d97706;border-radius:6px;">
        <strong>4. Consolidation Rule:</strong> <span class="mono">Endorsement Overrides Quote (Prio 40 &gt; Prio 20)</span>
      </div>
      <div style="padding:7px 12px;background:#f8fafc;border-left:3px solid #1d4ed8;border-radius:6px;">
        <strong>5. Operational Store:</strong> <span class="mono">alloydb://docubridge-enterprise-data/operational/bridge_operational_facts</span>
      </div>
      <div style="padding:7px 12px;background:#f8fafc;border-left:3px solid var(--brand-primary);border-radius:6px;">
        <strong>6. User Interface Card:</strong> <span class="mono">a2ui://citation/terrepower-2027/deductible#page=4</span>
      </div>
    `;

    document.getElementById("inbox-card-sub").innerHTML = 'Proposed by Agent: <span class="mono">$50,000</span> (Cover Page) vs. Schedule B Endorsement: <span class="mono">$100,000</span> (<span class="anchor-chip">Page 4 · Region [0.12, 0.44]</span>)';
    document.getElementById("inbox-field-label").textContent = "VERIFIED DEDUCTIBLE VALUE";
    document.getElementById("input-expert-memo").value = "Coastal Schedule B endorsement on page 4 overrides base cover page deductible to $100,000.";
    document.getElementById("rfc6902-patch-label").textContent = 'Audit Diff: [{"op":"replace","path":"/deductible_usd","value":"$100,000"}]';

    document.getElementById("gym-slice-header").textContent = "TARGET SLICE ACCURACY (CHUBB* · QUOTE · DEDUCTIBLE_USD) · WORKHORSE: GEMINI-3.8-FLASH";
    document.getElementById("gym-rule-summary-line").innerHTML = '<strong>Promoted Rule <span class="mono" id="live-rule-id">rule-insurance-01</span>:</strong> When extracting <span class="mono">deductible_usd</span> from <span class="mono">Chubb*</span> quote documents (see page 4), map <span class="mono">$50,000 → $100,000</span> (Coastal Schedule B endorsement).';
    document.getElementById("gate-detail").textContent =
      "Verified against carrier layout shifts (cover page → Schedule B addendum), legal suffix variants (Campala Ltd. → Campala), distinct Lloyd's syndicate collision guard (Syndicate 2987 ≠ Syndicate 1183), and deterministic Decimal math firewall.";
    document.getElementById("btn-promote-trust").textContent = "3. Promote AIG* Endorsement Slice to Trusted Auto-Approve";
  }

  if (typeof window.setPersonaMode === "function") {
    window.setPersonaMode(window.currentPersonaMode);
  }
  applyScenarioLabels();
  const activeScene = document.querySelector(".scene.active");
  if (activeScene) {
    const id = activeScene.id;
    const stepMap = { "scene-intake": 1, "scene-consolidation": 2, "scene-inbox": 3, "scene-gym": 4, "scene-telemetry": 5, "scene-portal": 1 };
    switchScene(id, stepMap[id] || 1, id === "scene-portal");
  }
}

export function selectRoleView(viewKey) {
  const b1 = document.getElementById("view-btn-broker");
  const b2 = document.getElementById("view-btn-underwriter");
  const b3 = document.getElementById("view-btn-claims");
  b1.className = viewKey === "primary" ? "btn btn-navy" : "btn btn-outline";
  b2.className = viewKey === "underwriter" ? "btn btn-navy" : "btn btn-outline";
  b3.className = viewKey === "audit" ? "btn btn-navy" : "btn btn-outline";

  const titleEl = document.getElementById("active-view-title");
  if (window.currentDomain === "banking") {
    if (viewKey === "primary") {
      titleEl.textContent = "Role-Based Projection: Credit Officer View (Deal & Conflicts)";
    } else if (viewKey === "underwriter") {
      titleEl.textContent = "Role-Based Projection: Underwriter View (LTV 72.00%, DSCR 1.42x & Fee Exposure)";
    } else {
      titleEl.textContent = "Role-Based Projection: Covenant / Audit View (Bi-Temporal Effective-Date Terms)";
    }
  } else if (window.currentDomain === "mortgage") {
    if (viewKey === "primary") {
      titleEl.textContent = "Role-Based Projection: Mortgage Underwriter View (TRID & LTV 77.08%)";
    } else if (viewKey === "underwriter") {
      titleEl.textContent = "Role-Based Projection: Closing Desk View (Cash-to-Close & Section A Fees)";
    } else {
      titleEl.textContent = "Role-Based Projection: Compliance / QC View (TRID Tolerance Audit)";
    }
  } else {
    if (viewKey === "primary") {
      titleEl.textContent = "Role-Based Projection: Broker View (Market & Conflicts)";
    } else if (viewKey === "underwriter") {
      titleEl.textContent = "Role-Based Projection: Underwriter View (Risk & Deductible-to-Limit Ratio)";
    } else {
      titleEl.textContent = "Role-Based Projection: Claims View (Active Coverage as of Loss Date)";
    }
  }
}

export async function refreshScene2LineageAndSync() {
  const entityId = getActiveEntityId();
  const fieldName = window.currentDomain === "insurance" ? "deductible" : "origination_fee";
  try {
    const [linResp, syncResp] = await Promise.all([
      apiFetch(`/v1/catalog/lineage/${encodeURIComponent(entityId)}/${encodeURIComponent(fieldName)}`),
      apiFetch(`/v1/bigquery/sync?entity_id=${encodeURIComponent(entityId)}`)
    ]);
    if (syncResp.ok) {
      const syncData = await syncResp.json();
      const wmEl = document.getElementById("iceberg-watermark-label");
      if (wmEl && syncData.watermark) {
        wmEl.textContent = `${syncData.watermark} (${entityId} · ${syncData.record_count} Iceberg CDC rows synced)`;
      }
    }
    if (linResp.ok) {
      const lin = await linResp.json();
      if (lin.nodes && lin.nodes.length >= 6) {
        const colors = ["var(--brand-slate)", "#6d28d9", "#059669", "#d97706", "#1d4ed8", "var(--brand-primary)"];
        document.getElementById("lineage-hops-box").innerHTML = lin.nodes.slice(0, 6).map((n, idx) => `
          <div style="padding:7px 12px;background:#f8fafc;border-left:3px solid ${colors[idx] || '#1d4ed8'};border-radius:6px;">
            <strong>${idx + 1}. ${n.zone.toUpperCase()}:</strong> <span class="mono">${n.location_uri}</span>
          </div>
        `).join("");
      }
    }
  } catch (_) {}
}
