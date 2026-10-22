---
type: atom
up: "bridge.md"
related:
  - "a2ui-edge.md"
  - "disposition.md"
  - "architecture.md"
  - "harness-evolve.md"
tags: [bridge, agent-gym, hitl, governance]
---

# Agent Gym & Programmable Trust

> Continuous learning from single-example expert fixes ("Example of One") and graduated autonomy across the 6-zone Enterprise Data Architecture (`bridge/agent_gym.py`, `bridge/a2ui_inbox.py`, `bridge/user_interaction.py`, `bridge/knowledge_catalog.py`).

## 1. As-Built Architecture (5 Pillars)

1. **Daytime Expert Signals ("Example of One"):** Captures single-example human corrections from Zone 2 (`Review Quarantine` via `distill_from_quarantine_correction`) and Zone 5 (`A2UI Agent Inbox` via RFC 6902 JSON Patch diffs and reviewer memos in `distill_from_inbox_audit`), alongside background `Shadow Mode Comparisons` (`record_shadow_comparison`).
2. **Offline Agent Gym (Safe Evolution Loop):**
   - **Tribal Knowledge Distiller:** Converts a single expert fix into a versioned `TribalKnowledgeRule` (`extraction_hint`, `entity_alias`, `consolidation_priority`, or `presentation_preference`) without touching deterministic financial math code.
   - **Synthetic Stress Testing (`generate_synthetic_stress_suite`):** Synthesizes 4 corner-case perturbations per rule (`carrier_layout_shift`, `legal_entity_suffix_variant`, `distinct_syndicate_guard`, `model_upgrade_parity`).
   - **Quality Report Card Gate (`evaluate_candidate_rule`):** Enforces positive target-slice gain, zero historical regression, and an intact deterministic `Decimal` math firewall (`_FORBIDDEN_MATH_TOKENS`) before promoting a rule.
3. **Production Layer Application & Day-2 Continuous Learning Payoff:** Applies promoted rules across:
   - **Stage 1 Extraction (`get_extraction_hints` → `GeminiExtractor` & `ingest_accepted_submission`):** Promoted `EXTRACTION_HINT` rules are dynamically injected into `build_extraction_prompt(schemas, extraction_hints=...)` (`bridge/extraction/gemini.py`) whenever the promoted rule set changes, and are deterministically applied in `ingest_accepted_submission()` (`bridge/user_interaction.py`) to map overridden fields (`canonical_target`), automatically promoting parked/pending Day-2 submissions (`Disposition.pending` → `Disposition.accepted` with `Gate.auto_approve`) and recording `docubridge.applied_extraction_hints` on the OpenTelemetry span.
   - **Stage 2 Consolidation (`normalize_entity_or_issuer`):** Normalizes legal entity suffixes (e.g., `"Meridian Logistics Holdings Ltd."` → `"Meridian Logistics"`, `"Campala Ltd."` → `"Campala"`) while preserving distinct syndicates/SPVs (`distinct_entities`).
   - **Stage 3 A2UI Presentation (`get_presentation_preferences`):** Customizes governed A2UI v0.9 surface layouts per role and domain.
4. **Programmable Trust (`ProgrammableTrustPolicy`):** Per-slice `(issuer_pattern, doctype, risk_level)` autonomy dial progressing through `shadow_mode` → `guided_review` → `trusted_auto_approve` once `consecutive_matches >= required_matches_for_promotion`, with interactive A2UI v0.9 controls (`build_a2ui_trust_readiness_surface`), automatic dial-back to `guided_review` on format drift, and 1-click rehearsal reset (`reset_trust_policies(clear_rules=True)`). Pre-seeded with **7 default slices** across **Commercial Banking** (`slice:closing-disclosure:fee-schedule:LOW`, `slice:credit-agreement:addendum:MEDIUM`, `slice:appraisal-report:commercial:HIGH`), **Residential Mortgage TRID Closing** (`slice:mortgage-trid:closing-disclosure:MEDIUM`), and **Commercial Insurance** (`slice:chubb:quote:LOW`, `slice:aig:endorsement:MEDIUM`, `slice:lloyds:quote:HIGH`).
5. **Durable Multi-Replica Store (`SqlAgentGymEngine`):** Persists signals, rules, report cards, trust policies, and shadow records in shared Cloud SQL / AlloyDB / SQLite tables (`gym_feedback_signals`, `gym_tribal_rules`, `gym_report_cards`, `gym_trust_policies`, `gym_shadow_comparisons`), refusing in-memory fallback when `BRIDGE_SEAM_MODE=gcp`.

---

## 2. Two-Sided Calibration & Human-Oversight Literature Note

> **Status (`v1.14.0-agent-gateway`):** Two-Sided Oversight (`oversight_rsr_score` computed from `citation_verified` and `review_dwell_ms`, rejecting rubber-stamps with `RSR < 0.50`), the 3-Tier Evaluation Gate (Tier 1 `Rule 0` deterministic `dispose` + `Decimal` math firewall, Tier 2 `gemini-4-argon` pointwise judge, and Tier 3 `threshold_corridor()` `FA=0` check), `compile_rules_to_skill()` (`SkillRevision` `v1.0.0 -> v1.1.0` + 1-click rollback), and Bi-Temporal Process-Instance Skill Lineage (`ProcessSkillLineageRecord`) are now implemented in `bridge/agent_gym.py` and `bridge/user_interaction.py`. The academic literature below grounds why naive human-in-the-loop (HITL) designs degrade human judgment over time without two-sided verification.

### 2.1 The Empirical Consensus: Why Naive HITL Fails

1. **Structural Erosion of Human Oversight in Agent Workflows** — Mitchell, Ghosh, & Passi (2026), [*AI Agents Push Humans Out of the Loop*](https://arxiv.org/pdf/2608.23642) (`arXiv:2608.23642`):
   - **Section 3 ("The oversight oversight"):** Current agent oversight discussions treat "human-in-the-loop" as a static, plug-and-play safety alibi while ignoring how agent architectures actively impede situational awareness.
   - **Section 4 ("The irony of automation"):** Extended reliance on autonomous agents degrades critical thinking and induces skill atrophy, creating a feedback loop that passively incentivizes rubber-stamping.
   - **Section 5 / Table 1 ("Organizing device behind solutions inventory"):** Maps interventions across two implementation stages (**Development / design-level affordances** vs. **Deployment / organizational protocols**) and two cognitive targets:
     - *Engaging oversight in the moment:* Pre-commitment mechanisms, delay-and-choice mechanisms, and inline reasoning probes.
     - *Maintaining skills over long-term capacity:* Behavioral monitoring, workload/scheduling pacing, active training/simulations, and role/audit redesign.
2. **Meta-Analysis of Human-AI Complementarity** — Vaccaro, Almaatouq, & Malone (2024), *When combinations of humans and AI are useful: A systematic review and meta-analysis* (*Nature Human Behaviour*, 106 experimental studies / 370 effect sizes):
   - Shows that human–AI combinations perform **significantly worse** on average than the single strongest partner alone ($\text{Hedges' } g = -0.23$) on **decision-making tasks** (whereas open-ended *content creation* sees gains).
   - When the AI baseline outperforms the human baseline, combining them yields net losses because humans either anchor on wrong AI outputs (over-reliance) or erratically override right ones (under-reliance).
3. **Ironies of Automation & Out-of-the-Loop (OOTL) Syndrome** — Bainbridge (1983), *Ironies of Automation* (*Automatica*); Endsley (2023), *Ironies of Artificial Intelligence* (*Ergonomics*):
   - **The Residue & Cold-Handback Problem:** Automating the routine 90%–95% of cases (`trusted_auto_approve`) starves human operators of daily practice (*skill atrophy*) and strips away situational context (*context blindness*). When the agent hits a complex failure or carrier format drift and abruptly dials back to `guided_review`, it drops the hardest 5%–10% of edge cases onto a deskilled, cold-started human operator.
4. **Failure of Passive XAI & Confidence Badges** — Bansal et al. (2021), *Does the Whole Exceed its Parts? The Effect of AI Explanations on Complementary Team Performance* (*CHI*); Green & Chen (2019), *The Principles and Limits of Algorithm-in-the-Loop Decision Making* (*CSCW*):
   - Displaying high confidence scores (`confidenceScore: 0.95` on `ApprovalCard`) and static explanations alongside a pre-selected recommendation increases human agreement with the AI **even when the AI is wrong**, functioning as a persuasive anchor rather than a verification aid.
5. **Surface Fluency ("Epistemia") & Multi-Layer Trust Calibration** — 2024–2026 Systematic Reviews (*Collaboration Convergence Framework*; *Social Trust Calibration Framework [STCF]*):
   - Fluent, structured agent outputs induce *epistemia* (false attribution of deep verification to clean presentation). Moreover, attitudinal trust diverges from behavioral reliance over time: fatigued operators can intellectually distrust an agent while still rubber-stamping its queue.
   - STCF separates calibration across four layers: **Capability trust** (slice accuracy), **Process trust** (transparent step lineage), **Institutional trust** (WORM vault + math firewall), and **Identity trust** (cryptographic SPIFFE SVID + OBO role bounds).
6. **Regulatory Mandate for Anti-Automation-Bias Controls** — *EU AI Act Article 14 (Human Oversight)*:
   - Explicitly requires high-risk human-machine interfaces (`InboxAuditRecord` in `bridge/a2ui_inbox.py`) to equip human overseers to remain aware of and counteract **automation bias** (over-reliance), interpret outputs against source evidence, and intervene or halt execution (`dial_trust_policy`).

---

### 2.2 Recommended Approaches from the Literature

1. **Cognitive Forcing Functions (CFFs) & Selective Friction** — Buçinca, Malaya, & Gajos (2021), *To Trust or to Think: Cognitive Forcing Functions Can Reduce Overreliance on AI* (*CSCW*); Vasconcelos et al. (2023), *Explanations Can Reduce Overreliance on AI Systems During Decision-Making* (*CHI*):
   - **Dual-Process Theory (System 1 vs. System 2):** Reviewers default to fast, lazy System 1 heuristics unless the interface alters the **cost-benefit equation** (Vasconcelos et al.)—either by lowering the cognitive cost of inspecting the source PDF (`SourceCitationPill` bounding-box deep links) or raising the friction of blind acceptance.
   - **Three CFF Archetypes (Buçinca et al.):**
     1. *Update / Pre-Commitment ("Don't label the labels"):* The human records an independent judgment *before* the AI's recommendation is revealed.
     2. *On-Demand / Delay-and-Choice:* The AI's extracted value or recommendation is hidden until the reviewer explicitly clicks to request it or opens the source document.
     3. *Wait / Deliberation Pause:* A brief mandatory inspection window before approval controls unlock on high-stakes items.
   - **Selective / Adaptive Friction (2024–2026 CSCW/CHI):** Because universal CFFs lower subjective user satisfaction and cause fatigue, friction must be triggered **selectively** (only on `HIGH`/`CRITICAL` risk tiers, conflicting signals, or when behavioral monitoring detects rapid clicking) and account for individual variance (*Need for Cognition*).
2. **Evaluative AI (Hypothesis-Driven vs. Recommendation-Driven Support)** — Tim Miller (2023), *Explainable AI is dead, long live explainable AI! Hypothesis-driven decision support using evaluative AI* (*FAccT*); Le, Miller, Singh, & Sonenberg (2024, *ECAI* — Weight of Evidence):
   - Rather than recommending a single pre-selected winner (`initial_model["resolution"]["selectedValue"] = options[0]["value"]`), the UI should present **competing hypotheses with pro and con evidence** side by side (`FactConflictTable` + `SourceCitationPill`), preserving human cognitive agency to weigh the evidence and choose.
3. **Two-Dimensional Appropriate Reliance Metrics (`RAIR` & `RSR`)** — Schemmer et al. (2023), *Appropriate Reliance on AI Advice: Conceptualization and the Effect of Explanations* (*CHI*):
   - Raw `% agreement` (`ProgrammableTrustPolicy.accuracy_rate = total_exact_matches / total_evaluated`) cannot distinguish a vigilant expert verifying a strong model from a fatigued reviewer rubber-stamping everything.
   - Appropriate Reliance decomposes human-AI transitions into two conditional metrics:
     $$\text{RAIR (Relative AI Reliance)} = P(\text{Human final correct} \mid \text{Human initial wrong}, \text{AI correct})$$
     $$\text{RSR (Relative Self-Reliance)} = P(\text{Human final correct} \mid \text{Human initial correct or unassisted}, \text{AI wrong})$$
   - Promoting a policy slice to `trusted_auto_approve` requires verifying both high model accuracy and high reviewer **$\text{RSR}$** (the reviewer actually overrides the AI when the AI is wrong).
4. **Gold-Standard Injection ("Honeypots"), Dawid-Skene Calibration & SME Agreement** — Dawid & Skene (1979, *Applied Statistics*); Enterprise Autorater Calibration (`Krippendorff's` $\alpha \ge 0.80$, `Gwet's AC2`, Human/Agent Swap):
   - **Seeded Honeypots & Attention Checks:** Periodically inject known-discrepancy items (e.g., synthetic perturbations from `generate_synthetic_stress_suite`) into the live review queue to measure each reviewer's live confusion matrix and $\text{RSR}$.
   - **Continuous Stratified Spot-Checks:** Never drop human review to 0% on `trusted_auto_approve` slices; route a 2%–5% stratified sample to `guided_review` so reviewers retain domain familiarity and situational awareness.
   - **Blind Multi-Rater & Dawid-Skene Weighting:** Weight expert feedback signals by reviewer reliability and require blind agreement ($\alpha \ge 0.80$, Gwet's AC2 on skewed distributions) rather than raw `% agreement` before auto-promoting rules across tenants.

---

### 2.3 Architectural Roadmap for Agent Gym v2 (Design Candidates mapped to Code)

| Mechanism | Literature Anchor | Current Gap in `bridge/agent_gym.py` & `bridge/a2ui_inbox.py` | Candidate Design Pattern |
| :--- | :--- | :--- | :--- |
| **1. Pre-Commitment & Evaluative AI (Anti-Anchoring)** | Mitchell et al. (2026); Buçinca et al. (2021); Miller (2023, *Evaluative AI*) | `InMemoryActionInboxStore.create_action` (`bridge/a2ui_inbox.py:1028-1029`) pre-selects `options[0]["value"]` in `/resolution/selectedValue` and renders `confidenceScore` upfront on `ApprovalCard` (`bridge/a2ui_inbox.py:137-160`). | Add a **Blind Pre-Commitment / Evaluative Mode** for `HIGH`/`CRITICAL` risk items and calibration samples: omit the pre-selected winner (`selectedValue: ""`) and hide `confidenceScore` until the reviewer clicks a `SourceCitationPill` or commits an independent choice (`pre_committed: bool`). |
| **2. Reasoning Probes on Overrides & High-Risk Gates** | Mitchell et al. (2026); Vasconcelos et al. (2023) | `distill_from_inbox_audit` (`bridge/agent_gym.py:711-714`) falls back to `"Expert edit override on ..."` when `/review/memo` is blank, allowing empty memos to mint candidate `TribalKnowledgeRule`s in `_distill_rule_from_signal`. | Trigger an inline **Reasoning Probe** on `HIGH`/`CRITICAL` approvals and edits: require a structured rationale code or cited page/clause before `edit_and_approve` / `accept` succeeds, and refuse to distill a candidate `TribalKnowledgeRule` when `expert_memo` is empty or generic. |
| **3. Behavioral Oversight Telemetry & `RAIR`/`RSR` Gating** | Mitchell et al. (2026); Schemmer et al. (2023) | `record_shadow_comparison` (`bridge/agent_gym.py:1037-1104`) increments `consecutive_matches` on any `ai_value == human_value`, even a sub-second rubber-stamp with zero PDF inspection. | Record `review_dwell_ms: int`, `citation_opened: bool`, and `pre_committed: bool` on `InboxAuditRecord` (`bridge/a2ui_inbox.py:269-290`). Discount unverified/sub-threshold clicks so only *verified* matches increment `ProgrammableTrustPolicy.consecutive_matches`, and track reviewer $\text{RSR}$ alongside `accuracy_rate`. |
| **4. Continuous Spot-Checks & Seeded Honeypots** | Bainbridge (1983); Endsley (2023); Dawid & Skene (1979) | Once a slice reaches `TRUSTED_AUTO_APPROVE` (`bridge/agent_gym.py:98`), humans see 0% of traffic until `drift_incidents` (`bridge/agent_gym.py:1079-1083`) forces an abrupt emergency dial-back. | Route a configurable `spot_check_sample_rate` (e.g., 5% of `trusted_auto_approve` items) plus periodic seeded perturbations from `generate_synthetic_stress_suite` (`bridge/agent_gym.py:352-427`) into `guided_review` to prevent skill atrophy and continuously calibrate reviewer $\text{RSR}$. |

## Related
- [Agent Gym × Harness Evolve & Multi-Tier Extraction](harness-evolve.md) · [the Bridge](bridge.md) · [A2UI edge](a2ui-edge.md) · [disposition](disposition.md) · [project layout](architecture.md)
