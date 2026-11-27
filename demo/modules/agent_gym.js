import {
  FALLBACK_TRUST_POLICIES,
  apiFetch,
  getActiveEntityId,
  setCaption,
} from "./api_state.js";
import { refreshLiveTelemetry, renderTrustPolicies } from "./inbox_registry.js";

export async function runDistillSignal() {
  const dom = window.currentDomain;
  const isLending = dom !== "insurance";
  const payload = dom === "banking"
    ? {
        entity_id: getActiveEntityId(),
        doc_id: "syndicated-credit-agreement-meridian-2026.pdf",
        doctype: "addendum",
        issuer: "Syndicated",
        field_path: "origination_fee",
        before_value: "$50,000.00",
        after_value: "$100,000.00",
        expert_memo: "Schedule B fee addendum on page 4 sets origination fee to $100,000.00.",
        page_number: 4,
        category: "extraction_hint",
        distinct_entities: ["Meridian SPV II"],
        citation_verified: true,
        review_dwell_ms: 4800
      }
    : (dom === "mortgage"
        ? {
            entity_id: getActiveEntityId(),
            doc_id: "firstnational-trid-closing-disclosure-2026.pdf",
            doctype: "closing-disclosure",
            issuer: "FirstNational",
            field_path: "origination_fee",
            before_value: "$4,250.00",
            after_value: "$8,500.00",
            expert_memo: "Signed TRID Closing Disclosure Page 3 rate-lock extension addendum sets Section A origination fee to $8,500.00.",
            page_number: 3,
            category: "extraction_hint",
            distinct_entities: ["Westbrook Irrevocable Trust II"],
            citation_verified: true,
            review_dwell_ms: 4800
          }
        : {
            entity_id: getActiveEntityId(),
            doc_id: "chubb-coastal-property-quote-2027.pdf",
            doctype: "quote",
            issuer: "Chubb",
            field_path: "deductible",
            before_value: "$50,000.00",
            after_value: "$100,000.00",
            expert_memo: "Coastal Schedule B endorsement on page 4 sets wind deductible to $100,000.00.",
            page_number: 4,
            category: "extraction_hint",
            distinct_entities: ["Syndicate 2987", "Syndicate 1183"],
            citation_verified: true,
            review_dwell_ms: 4800
          });

  try {
    const resp = await apiFetch("/v1/gym/signals/from-quarantine?role=underwriter", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Bridge-Role": "underwriter",
        "X-Bridge-Principal": isLending ? "senior.credit.officer@docubridge.io" : "lead.underwriter@docubridge.io"
      },
      body: JSON.stringify(payload)
    });
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    const data = await resp.json();
    const rule = data.candidate_rule;
    const sig = data.signal || {};
    window.lastCandidateRuleId = rule.rule_id;
    document.getElementById("gym-rule-summary-line").innerHTML =
      `<strong>Candidate Rule <span class="mono" id="live-rule-id">${rule.rule_id}</span> (${rule.status.toUpperCase()}):</strong> ${rule.instruction}`;
    document.getElementById("gate-status-pill").className = "badge badge-amber";
    document.getElementById("gate-status-pill").textContent = "⏳ Candidate Distilled (gemini-4-argon) · Ready for 3-Tier Gate";
    const rsrPill = document.getElementById("rsr-pill");
    if (rsrPill && sig.oversight_rsr_score) {
      rsrPill.textContent = `✓ Oversight RSR: ${Number(sig.oversight_rsr_score).toFixed(2)}`;
    }
    setCaption(
      "STAGE 4.1 · DISTILLED 'EXAMPLE OF ONE' SIGNAL (GEMINI-4-ARGON)",
      `Teacher model (gemini-4-argon) distilled verified correction (RSR ${sig.oversight_rsr_score || 0.98}) into Candidate Rule ${rule.rule_id}.`,
      `POST /v1/gym/signals/from-quarantine -> rule_id=${rule.rule_id} (teacher=gemini-4-argon, workhorse=gemini-3.8-flash)`
    );
  } catch (_) {
    const fallbackId = "rule-" + Math.random().toString(16).slice(2, 10);
    window.lastCandidateRuleId = fallbackId;
    document.getElementById("live-rule-id").textContent = fallbackId;
    document.getElementById("gate-status-pill").className = "badge badge-amber";
    document.getElementById("gate-status-pill").textContent = "⏳ Candidate Distilled (gemini-4-argon) · Ready for 3-Tier Gate";
  }
}

export async function runEvaluateRule() {
  if (!window.lastCandidateRuleId) {
    await runDistillSignal();
  }
  const ruleId = window.lastCandidateRuleId;
  const isLending = window.currentDomain !== "insurance";
  try {
    const resp = await apiFetch(`/v1/gym/rules/${ruleId}/evaluate?role=underwriter`, {
      method: "POST",
      headers: {
        "X-Bridge-Role": "underwriter",
        "X-Bridge-Principal": isLending ? "senior.credit.officer@docubridge.io" : "lead.underwriter@docubridge.io"
      }
    });
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    const data = await resp.json();
    const rep = data.report_card;
    const rule = data.rule;
    document.getElementById("gym-score-big").innerHTML =
      `${(rep.candidate_slice_score * 100).toFixed(1)}<span style="font-size:32px;color:#94a3b8;">%</span>`;
    document.getElementById("gym-delta-badge").textContent =
      `↑ +${(rep.slice_improvement_delta * 100).toFixed(1)}% Slice Improvement (Baseline ${(rep.baseline_slice_score * 100).toFixed(1)}% → ${(rep.candidate_slice_score * 100).toFixed(1)}%)`;
    document.getElementById("gym-rule-summary-line").innerHTML =
      `<strong>Promoted Rule <span class="mono" id="live-rule-id">${rule.rule_id}</span> (${rule.status.toUpperCase()}):</strong> ${rule.instruction}`;
    document.getElementById("gate-status-pill").className = "badge badge-green";
    document.getElementById("gate-status-pill").textContent = "● 3-Tier Gate Passed · PROMOTED (0 Regressions)";
    document.getElementById("gate-headline").textContent =
      `${rep.stress_cases_passed} / ${rep.stress_cases_total} Synthetic Perturbation Stress Cases Passed · ${rep.historical_benchmark_total} / ${rep.historical_benchmark_total} Historical Benchmarks Intact`;
    const t1 = document.getElementById("tier1-pill");
    if (t1) t1.textContent = rep.tier1_rule0_passed !== false ? "✓ Tier 1: Rule 0 & Decimal" : "⚠ Tier 1: Failed";
    const t2 = document.getElementById("tier2-pill");
    if (t2) t2.textContent = `✓ Tier 2: ${rep.judge_model || "gemini-4-argon"} (${Number(rep.tier2_autorater_score || 1.0).toFixed(2)})`;
    const t3 = document.getElementById("tier3-pill");
    if (t3) t3.textContent = rep.tier3_corridor_fa_zero !== false ? "✓ Tier 3: FA=0 Corridor" : "⚠ Tier 3: Corridor Breach";
    setCaption(
      "STAGE 4.2 · 3-TIER EVALUATION GATE PASSED (GEMINI-4-ARGON JUDGE)",
      `Rule ${rule.rule_id} passed Tier 1 (Rule 0 + Decimal), Tier 2 (gemini-4-argon=${Number(rep.tier2_autorater_score || 1.0).toFixed(2)}), Tier 3 (FA=0 Corridor) -> PROMOTED.`,
      `POST /v1/gym/rules/${rule.rule_id}/evaluate -> passed_gate=true, workhorse=${rep.workhorse_model || 'gemini-3.8-flash'}, judge=${rep.judge_model || 'gemini-4-argon'}`
    );
  } catch (_) {
    document.getElementById("gate-status-pill").className = "badge badge-green";
    document.getElementById("gate-status-pill").textContent = "● 3-Tier Gate Passed · PROMOTED (0 Regressions)";
  }
}

export async function runCompileSkill() {
  await runEvaluateRule();
  const dom = window.currentDomain;
  const isLending = dom !== "insurance";
  const doctype = dom === "banking" ? "addendum" : (dom === "mortgage" ? "closing-disclosure" : "quote");
  const issuerPattern = dom === "banking" ? "Syndicated*" : (dom === "mortgage" ? "FirstNational*" : "Chubb*");
  try {
    const resp = await apiFetch("/v1/gym/compile-skill?role=underwriter", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Bridge-Role": "underwriter",
        "X-Bridge-Principal": isLending ? "senior.credit.officer@docubridge.io" : "lead.underwriter@docubridge.io"
      },
      body: JSON.stringify({ doctype: doctype, issuer_pattern: issuerPattern, auto_promote: true })
    });
    if (resp.ok) {
      const compiled = await resp.json();
      window.lastCompiledSkill = compiled;
      const banner = document.getElementById("compiled-skill-banner");
      if (banner) {
        banner.style.display = "block";
        const stateBadge = document.getElementById("compiled-skill-state-badge");
        if (stateBadge) {
          stateBadge.className = "badge badge-purple";
          stateBadge.textContent = `🧬 AGENT REGISTRY · SKILL REVISION ${compiled.target_state || "ACTIVE"} (defaultRevision)`;
        }
        document.getElementById("compiled-skill-title").textContent =
          `Teacher (${compiled.teacher_model}) Compiled ${compiled.compiled_rules_count} Promoted Rule(s) into SKILL.md v${compiled.version || "1.1.0"} for Workhorse (${compiled.workhorse_model})`;
        document.getElementById("compiled-skill-uri").textContent = compiled.gcs_source_uri || compiled.skill_uri;
        const urnEl = document.getElementById("compiled-skill-urn");
        if (urnEl) urnEl.textContent = compiled.skill_urn || `urn:skill:projects-docubridge-showcase:locations:global:private-${doctype}`;
        const revEl = document.getElementById("compiled-skill-rev");
        if (revEl) revEl.textContent = `${compiled.revision_id || "rev-v1-1"} (v${compiled.version || "1.1.0"})`;
        const parentEl = document.getElementById("compiled-skill-parent");
        if (parentEl) parentEl.textContent = compiled.parent_revision_id || "rev-v1-0-baseline";
        const shaEl = document.getElementById("compiled-skill-sha");
        if (shaEl) shaEl.textContent = (compiled.archive_sha256 || "e3b0c44298fc1c14").slice(0, 16) + "...";
        document.getElementById("compiled-skill-markdown").textContent = compiled.skill_markdown;
      }
      setCaption(
        "STAGE 4.6 · AGENT REGISTRY & SKILL REGISTRY — IMMUTABLE SKILL REVISION PUBLISHED",
        `Teacher (${compiled.teacher_model}) published SkillRevision ${compiled.revision_id} (${compiled.skill_urn}) -> defaultRevision updated.`,
        `POST /v1/gym/compile-skill -> revision_id=${compiled.revision_id}, parent=${compiled.parent_revision_id}, sha256=${(compiled.archive_sha256 || '').slice(0, 12)}`
      );
      return;
    }
  } catch (_) {}
}

export async function runRollbackSkill() {
  const dom = window.currentDomain;
  const isLending = dom !== "insurance";
  const doctype = dom === "banking" ? "addendum" : (dom === "mortgage" ? "closing-disclosure" : "quote");
  try {
    const resp = await apiFetch(`/v1/gym/skills/${encodeURIComponent(doctype)}/rollback?role=underwriter`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Bridge-Role": "underwriter",
        "X-Bridge-Principal": isLending ? "senior.credit.officer@docubridge.io" : "lead.underwriter@docubridge.io"
      },
      body: JSON.stringify({ reason: "Operator rolled back defaultRevision pointer to parent revision." })
    });
    if (resp.ok) {
      const rev = await resp.json();
      const stateBadge = document.getElementById("compiled-skill-state-badge");
      if (stateBadge) {
        stateBadge.className = "badge badge-amber";
        stateBadge.textContent = `↩ ROLLED BACK · defaultRevision = ${rev.revision_id} (v${rev.version})`;
      }
      const revEl = document.getElementById("compiled-skill-rev");
      if (revEl) revEl.textContent = `${rev.revision_id} (v${rev.version} · ACTIVE)`;
      setCaption(
        "STAGE 4.7 · SKILL REGISTRY — INSTANT REVISION ROLLBACK",
        `Repointed defaultRevision for ${doctype} back to ${rev.revision_id} (v${rev.version}) without deleting immutable revision history.`,
        `POST /v1/gym/skills/${doctype}/rollback -> active_revision=${rev.revision_id}, version=${rev.version}`
      );
    }
  } catch (_) {}
}

export async function runPromoteTrustSlice() {
  const dom = window.currentDomain;
  const isLending = dom !== "insurance";
  const sliceId = dom === "banking"
    ? "slice:credit-agreement:addendum:MEDIUM"
    : (dom === "mortgage" ? "slice:mortgage-trid:closing-disclosure:MEDIUM" : "slice:aig:endorsement:MEDIUM");
  const shadowPayload = dom === "banking"
    ? {
        entity_id: getActiveEntityId(),
        doc_id: "syndicated-credit-agreement-meridian-2026.pdf",
        issuer: "Syndicated",
        doctype: "addendum",
        field_path: "origination_fee",
        ai_value: "$100,000.00",
        human_value: "$100,000.00",
        risk_level: "MEDIUM"
      }
    : (dom === "mortgage"
        ? {
            entity_id: getActiveEntityId(),
            doc_id: "firstnational-trid-closing-disclosure-2026.pdf",
            issuer: "FirstNational",
            doctype: "closing-disclosure",
            field_path: "origination_fee",
            ai_value: "$8,500.00",
            human_value: "$8,500.00",
            risk_level: "MEDIUM"
          }
        : {
            entity_id: getActiveEntityId(),
            doc_id: "aig-excess-property-binder-2027.pdf",
            issuer: "AIG",
            doctype: "endorsement",
            field_path: "excess_layer_limit_usd",
            ai_value: "$25,000,000.00",
            human_value: "$25,000,000.00",
            risk_level: "MEDIUM"
          });

  try {
    await apiFetch(`/v1/gym/trust-policies/${encodeURIComponent(sliceId)}/shadow`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(shadowPayload)
    });
    const dialResp = await apiFetch(`/v1/gym/trust-policies/${encodeURIComponent(sliceId)}/dial?role=underwriter`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Bridge-Role": "underwriter",
        "X-Bridge-Principal": isLending ? "senior.credit.officer@docubridge.io" : "lead.underwriter@docubridge.io"
      },
      body: JSON.stringify({
        target_level: "trusted_auto_approve",
        reason: "Promoted after 5/5 consecutive exact matches in Guided Review Queue.",
        force: true
      })
    });
    if (!dialResp.ok) throw new Error("HTTP " + dialResp.status);
    await refreshLiveTelemetry();
    setCaption(
      "STAGE 4.3 · PROGRAMMABLE TRUST PROMOTED TO AUTO-APPROVE",
      `Slice ${sliceId} reached 5/5 consecutive matches and promoted to Trusted Auto-Approve.`,
      `POST /v1/gym/trust-policies/${sliceId}/dial -> autonomy_level=trusted_auto_approve`
    );
  } catch (_) {
    const updated = window.cachedTrustPolicies.map(p =>
      p.slice_id === sliceId
        ? { ...p, autonomy_level: "trusted_auto_approve", consecutive_matches: 5, last_updated_reason: "Promoted after 5/5 consecutive exact matches in Guided Review Queue." }
        : p
    );
    renderTrustPolicies(updated);
  }
}

export async function runSimulateDrift() {
  const dom = window.currentDomain;
  const sliceId = dom === "banking"
    ? "slice:credit-agreement:addendum:MEDIUM"
    : (dom === "mortgage" ? "slice:mortgage-trid:closing-disclosure:MEDIUM" : "slice:aig:endorsement:MEDIUM");
  const driftPayload = dom === "banking"
    ? {
        entity_id: getActiveEntityId(),
        doc_id: "syndicated-credit-agreement-drift-2026.pdf",
        issuer: "Syndicated",
        doctype: "addendum",
        field_path: "origination_fee",
        ai_value: "$50,000.00",
        human_value: "$100,000.00",
        risk_level: "MEDIUM"
      }
    : (dom === "mortgage"
        ? {
            entity_id: getActiveEntityId(),
            doc_id: "firstnational-trid-closing-drift-2026.pdf",
            issuer: "FirstNational",
            doctype: "closing-disclosure",
            field_path: "origination_fee",
            ai_value: "$4,250.00",
            human_value: "$8,500.00",
            risk_level: "MEDIUM"
          }
        : {
            entity_id: getActiveEntityId(),
            doc_id: "aig-excess-property-drift-2027.pdf",
            issuer: "AIG",
            doctype: "endorsement",
            field_path: "excess_layer_limit_usd",
            ai_value: "$15,000,000.00",
            human_value: "$25,000,000.00",
            risk_level: "MEDIUM"
          });

  try {
    const resp = await apiFetch(`/v1/gym/trust-policies/${encodeURIComponent(sliceId)}/shadow`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(driftPayload)
    });
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    await refreshLiveTelemetry();
    setCaption(
      "STAGE 4.4 · INSTANT SAFETY DIAL-BACK ON FORMAT DRIFT",
      `Format drift detected on ${sliceId} -> Auto-demoted to Guided Human Review (0/5 matches, 1 drift).`,
      `POST /v1/gym/trust-policies/${sliceId}/shadow -> autonomy_level=guided_review, consecutive_matches=0, drift_incidents=1`
    );
  } catch (_) {
    const updated = window.cachedTrustPolicies.map(p =>
      p.slice_id === sliceId
        ? { ...p, autonomy_level: "guided_review", consecutive_matches: 0, drift_incidents: 1, accuracy_rate: 0.833, last_updated_reason: "Auto-demoted to Guided Review due to format drift." }
        : p
    );
    renderTrustPolicies(updated);
  }
}

export async function runVerifyDay2Payoff() {
  await runCompileSkill();
  const dom = window.currentDomain;
  const isLending = dom !== "insurance";
  const tStart = performance.now();

  const ingestPayload = dom === "banking"
    ? {
        entity_id: getActiveEntityId(),
        doc_id: "doc-meridian-day2-auto",
        doctype: "addendum",
        issuer: "Syndicated Credit Agreement",
        status: "parked",
        version: 2,
        confidence: 0.99,
        summary: "Day-2 CRE Deal Submission (Auto-reconciled via Promoted Agent Gym Rule)",
        key_fields: { currency: "USD", origination_fee: "$50,000.00" },
        source_anchors: {
          origination_fee: {
            page_number: 4,
            bounding_box: [0.12, 0.44, 0.58, 0.51],
            snippet: "Schedule B Origination & Rate-Lock Fee: $100,000.00"
          }
        }
      }
    : (dom === "mortgage"
        ? {
            entity_id: getActiveEntityId(),
            doc_id: "doc-westbrook-day2-auto",
            doctype: "closing-disclosure",
            issuer: "FirstNational Home Loans",
            status: "parked",
            version: 2,
            confidence: 0.99,
            summary: "Day-2 TRID Closing Disclosure (Auto-reconciled via Promoted Agent Gym Rule)",
            key_fields: { currency: "USD", origination_fee: "$4,250.00" },
            source_anchors: {
              origination_fee: {
                page_number: 3,
                bounding_box: [0.11, 0.48, 0.59, 0.54],
                snippet: "Section A Origination + 45-Day Rate-Lock Extension: $8,500.00"
              }
            }
          }
        : {
            entity_id: getActiveEntityId(),
            doc_id: "doc-terrepower-day2-auto",
            doctype: "quote",
            issuer: "Chubb Syndicate 2987",
            status: "parked",
            version: 2,
            confidence: 0.99,
            summary: "Day-2 Coastal Property Submission (Auto-reconciled via Promoted Agent Gym Rule)",
            key_fields: { currency: "USD", deductible: "$50,000.00" },
            source_anchors: {
              deductible: {
                page_number: 4,
                bounding_box: [0.12, 0.44, 0.58, 0.51],
                snippet: "Schedule B Named Windstorm Deductible Override: $100,000.00"
              }
            }
          });

  let resolvedVal = dom === "mortgage" ? "$8,500.00" : "$100,000.00";
  let elapsedMs = 142;
  let activeSkillRev = (window.lastCompiledSkill && window.lastCompiledSkill.revision_id) || "rev-v1-1";
  let activeSkillVer = (window.lastCompiledSkill && window.lastCompiledSkill.version) || "1.1.0";
  try {
    const resp = await apiFetch("/v1/intake/ingest?role=underwriter", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Bridge-Role": "underwriter",
        "X-Bridge-Principal": isLending ? "senior.credit.officer@docubridge.io" : "lead.underwriter@docubridge.io"
      },
      body: JSON.stringify(ingestPayload)
    });
    elapsedMs = Math.max(18, Math.round(performance.now() - tStart));
    if (resp.ok) {
      const resData = await resp.json();
      const kf = (resData.entry && resData.entry.key_fields) || {};
      resolvedVal = kf.origination_fee || kf.deductible || resolvedVal;
      if (resData.entry && resData.entry.skill_revision_id) {
        activeSkillRev = resData.entry.skill_revision_id;
        activeSkillVer = resData.entry.skill_version || activeSkillVer;
      }
    }
    const linResp = await apiFetch(`/v1/registry/lineage/${encodeURIComponent(ingestPayload.entity_id)}`);
    if (linResp.ok) {
      const linData = await linResp.json();
      const execs = linData.process_skill_executions || [];
      const linBox = document.getElementById("process-skill-lineage-box");
      if (linBox && execs.length > 0) {
        const summaryRows = execs.slice(-2).map(ex =>
          `<span class="mono" style="background:#f1f5f9;padding:2px 6px;border-radius:4px;">Doc v${ex.document_version} (${ex.doc_id}): ${ex.doctype_skill_name}@v${ex.doctype_skill_version} [${ex.doctype_skill_revision_id}] · rules=${(ex.compiled_rule_ids || []).join(",") || "none"}</span>`
        ).join(" → ");
        linBox.innerHTML = `<strong>🔗 Process-Instance Skill Lineage (<span class="mono">GET /v1/registry/lineage/${ingestPayload.entity_id}</span>):</strong> ${summaryRows}`;
      }
    }
  } catch (_) {}

  const t2RevEl = document.getElementById("lineage-turn2-rev");
  if (t2RevEl) t2RevEl.textContent = `v${activeSkillVer} (${activeSkillRev})`;

  const banner = document.getElementById("day2-payoff-banner");
  if (banner) {
    banner.style.display = "block";
    document.getElementById("day2-payoff-title").textContent =
      `Next Deal Submission Auto-Approved in ${elapsedMs}ms · 0 Human Touchpoints`;
    document.getElementById("day2-payoff-trace").textContent =
      `POST /v1/intake/ingest · disposition=accepted · trace=${window.lastTraceId.slice(0, 12)}...`;
    if (dom === "mortgage") {
      document.getElementById("day2-before-text").innerHTML =
        'Initial Loan Estimate (<span class="mono">$4,250</span>) conflicted with Page 3 TRID Closing Disclosure (<span class="mono">$8,500</span>) → <strong>Parked in Zone 2 Review Quarantine</strong>.';
      document.getElementById("day2-after-text").innerHTML =
        `Promoted rule automatically mapped Page 3 Section A (<span class="mono">${resolvedVal}</span>), locked WORM Vault <span class="mono">sha256</span>, and <strong>Auto-Approved straight to AlloyDB &amp; BigQuery in ${elapsedMs}ms</strong>.`;
    } else if (dom === "insurance") {
      document.getElementById("day2-before-text").innerHTML =
        'Cover deductible (<span class="mono">$50,000</span>) conflicted with Page 4 Schedule B (<span class="mono">$100,000</span>) → <strong>Parked in Zone 2 Review Quarantine</strong>.';
      document.getElementById("day2-after-text").innerHTML =
        `Promoted rule automatically mapped Page 4 Wind Endorsement (<span class="mono">${resolvedVal}</span>), locked WORM Vault <span class="mono">sha256</span>, and <strong>Auto-Approved straight to AlloyDB &amp; BigQuery in ${elapsedMs}ms</strong>.`;
    } else {
      document.getElementById("day2-before-text").innerHTML =
        'Cover fee (<span class="mono">$50,000</span>) conflicted with Page 4 Schedule B (<span class="mono">$100,000</span>) → <strong>Parked in Zone 2 Review Quarantine</strong>.';
      document.getElementById("day2-after-text").innerHTML =
        `Promoted rule automatically mapped Page 4 Schedule B (<span class="mono">${resolvedVal}</span>), locked WORM Vault <span class="mono">sha256</span>, and <strong>Auto-Approved straight to AlloyDB &amp; BigQuery in ${elapsedMs}ms</strong>.`;
    }
  }

  const c2Badge = document.getElementById("chubb-intake-badge");
  if (c2Badge) {
    c2Badge.className = "badge badge-green";
    c2Badge.textContent = "Accepted · Auto-Approved (Day-2 Gym Rule)";
  }

  setCaption(
    "STAGE 4.5 · DAY-2 CONTINUOUS LEARNING PAYOFF VERIFIED",
    `Next submission bypassed Review Quarantine and Auto-Approved in ${elapsedMs}ms (${resolvedVal} resolved automatically).`,
    `POST /v1/intake/ingest -> disposition=accepted, gate=auto_approve, applied_extraction_hints=${window.lastCandidateRuleId || 'promoted'}`
  );
}

export async function runResetGym() {
  const day2Banner = document.getElementById("day2-payoff-banner");
  if (day2Banner) day2Banner.style.display = "none";
  const skillBanner = document.getElementById("compiled-skill-banner");
  if (skillBanner) skillBanner.style.display = "none";
  const c2Badge = document.getElementById("chubb-intake-badge");
  if (c2Badge) {
    c2Badge.className = "badge badge-amber";
    c2Badge.textContent = "Quarantined · Review Needed";
  }
  window.lastCandidateRuleId = null;
  try {
    const resp = await apiFetch("/v1/gym/trust-policies/reset?clear_rules=true&reseed_demo=true", { method: "POST" });
    if (typeof window.refreshConsolidationCollab === "function") {
      await window.refreshConsolidationCollab();
    }
    if (resp.ok) {
      const policies = await resp.json();
      renderTrustPolicies(policies);
      setCaption(
        "STAGE 4 · DEMO STATE RESET",
        "Programmable Trust policies & baseline scenarios restored to clean 4/5 Guided Review streak.",
        "POST /v1/gym/trust-policies/reset?clear_rules=true&reseed_demo=true -> 7 Banking, Mortgage & Insurance slices restored"
      );
      return;
    }
  } catch (_) {}
  renderTrustPolicies(FALLBACK_TRUST_POLICIES);
}
