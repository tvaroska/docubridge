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

> Continuous learning from single-example expert fixes ("Example of One") and graduated autonomy across the 6-zone Enterprise Data Architecture (`bridge/agent_gym/`, `bridge/a2ui_inbox/`, `bridge/user_interaction/`, `bridge/knowledge_catalog.py`).

## 1. As-Built Architecture (5 Pillars)

1. **Daytime Expert Signals ("Example of One"):** Captures single-example human corrections from Zone 2 (`Review Quarantine` via `distill_from_quarantine_correction`) and Zone 5 (`A2UI Agent Inbox` via RFC 6902 JSON Patch diffs and reviewer memos in `distill_from_inbox_audit`), alongside background `Shadow Mode Comparisons` (`record_shadow_comparison`).
2. **Offline Agent Gym (Safe Evolution Loop):**
   - **Tribal Knowledge Distiller:** Converts a single expert fix into a versioned `TribalKnowledgeRule` (`extraction_hint`, `entity_alias`, `consolidation_priority`, or `presentation_preference`) without touching deterministic financial math code.
   - **Synthetic Stress Testing (`generate_synthetic_stress_suite`):** Synthesizes and deterministically executes 4 corner-case perturbations per rule (`carrier_layout_shift`, `legal_entity_suffix_variant`, `distinct_syndicate_guard`, `model_upgrade_parity`) via `_execute_rule_on_case`.
   - **Quality Report Card Gate (`evaluate_candidate_rule`):** Enforces positive target-slice gain, zero historical regression, and an intact deterministic `Decimal` math firewall (`_FORBIDDEN_MATH_TOKENS`) before promoting a rule.
3. **Production Layer Application & Day-2 Continuous Learning Payoff:** Applies promoted rules across:
   - **Stage 1 Extraction (`get_extraction_hints` → `GeminiExtractor` & `ingest_accepted_submission`):** Promoted `EXTRACTION_HINT` rules are dynamically injected into `build_extraction_prompt(schemas, extraction_hints=...)` (`bridge/extraction/gemini.py`) whenever the promoted rule set changes, and are deterministically applied in `ingest_accepted_submission()` (`bridge/user_interaction/surfaces.py`) to map overridden fields (`canonical_target`), automatically promoting parked/pending Day-2 submissions (`Disposition.pending` → `Disposition.accepted` with `Gate.auto_approve`) and recording `docubridge.applied_extraction_hints` on the OpenTelemetry span.
   - **Stage 2 Consolidation (`normalize_entity_or_issuer`):** Normalizes legal entity suffixes (e.g., `"Meridian Logistics Holdings Ltd."` → `"Meridian Logistics"`, `"Campala Ltd."` → `"Campala"`) while preserving distinct syndicates/SPVs (`distinct_entities`).
   - **Stage 3 A2UI Presentation (`get_presentation_preferences`):** Customizes governed A2UI v0.9 surface layouts per role and domain.
4. **Programmable Trust (`ProgrammableTrustPolicy`):** Per-slice `(issuer_pattern, doctype, risk_level)` autonomy dial progressing through `shadow_mode` → `guided_review` → `trusted_auto_approve` once verified `consecutive_matches >= required_matches_for_promotion`, with interactive A2UI v0.9 controls (`build_a2ui_trust_readiness_surface`), automatic dial-back to `guided_review` on format drift, stratified `spot_check_sample_rate` sampling on `trusted_auto_approve` slices, and 1-click rehearsal reset (`reset_trust_policies(clear_rules=True)`). Pre-seeded with **7 default slices** across **Commercial Banking** (`slice:closing-disclosure:fee-schedule:LOW`, `slice:credit-agreement:addendum:MEDIUM`, `slice:appraisal-report:commercial:HIGH`), **Residential Mortgage TRID Closing** (`slice:mortgage-trid:closing-disclosure:MEDIUM`), and **Commercial Insurance** (`slice:chubb:quote:LOW`, `slice:aig:endorsement:MEDIUM`, `slice:lloyds:quote:HIGH`).
5. **Durable Multi-Replica Store (`SqlAgentGymEngine`):** Persists signals, rules, report cards, trust policies, and shadow records in shared Cloud SQL / AlloyDB / SQLite tables (`gym_feedback_signals`, `gym_tribal_rules`, `gym_report_cards`, `gym_trust_policies`, `gym_shadow_comparisons`), refusing in-memory fallback when `BRIDGE_SEAM_MODE=gcp`.

---

## 2. Two-Sided Calibration & Human-Oversight Architecture

> **Canonical Literature & Math Source:** General HCI & MRM literature on Why Passive HITL Fails (`Vaccaro et al. 2024`, $\text{Hedges' } g = -0.23$; `Mitchell et al. arXiv:2608.23642`; `Bainbridge 1983`; `Endsley 2023`), Cognitive Forcing Functions (`Buçinca et al. 2021`), Evaluative AI (`Miller 2023`), Appropriate Reliance (`RAIR` & `RSR`, `Schemmer et al. 2023`), and Dawid-Skene honeypots has been consolidated into [`~/projects/agent-evals/docs/playbooks/two-sided-hitl-and-continual-learning.md`](file:///usr/local/google/home/tvaroska/projects/agent-evals/docs/playbooks/two-sided-hitl-and-continual-learning.md) and [`~/projects/agent-evals/agent_evals/calibration.py`](file:///usr/local/google/home/tvaroska/projects/agent-evals/agent_evals/calibration.py).
>
> **DocuBridge Implementation (`v1.14.0-agent-gateway`):** Two-Sided Oversight (`oversight_rsr_score` computed from `citation_verified`, `review_dwell_ms`, `pre_committed`, and historical reviewer reliance, rejecting rubber-stamps with `RSR < 0.50`), the 3-Tier Evaluation Gate (Tier 1 `Rule 0` deterministic `dispose` + `Decimal` math firewall, Tier 2 `gemini-4-argon` pointwise judge, and Tier 3 `threshold_corridor()` `FA=0` check), `compile_rules_to_skill()` (`SkillRevision` `v1.0.0 -> v1.1.0` + 1-click rollback), and Bi-Temporal Process-Instance Skill Lineage (`ProcessSkillLineageRecord`) are implemented in `bridge/agent_gym/` and `bridge/user_interaction/`.

---

### 2.1 DocuBridge Human-Oversight Controls (`bridge/agent_gym/` & `bridge/a2ui_inbox/`)

| Mechanism | Literature Anchor | Implementation in `bridge/agent_gym/` & `bridge/a2ui_inbox/` | Operational Behavior |
| :--- | :--- | :--- | :--- |
| **1. Pre-Commitment & Evaluative AI (Anti-Anchoring)** | Mitchell et al. (2026); Buçinca et al. (2021); Miller (2023, *Evaluative AI*) | `InMemoryActionInboxStore.create_action` (`bridge/a2ui_inbox/memory_store.py`) and `InboxAuditRecord.pre_committed` (`bridge/a2ui_inbox/models.py`). | Supports **Blind Pre-Commitment / Evaluative Mode** (`blind_pre_commitment=True` or `HIGH`/`CRITICAL` calibration items): omits the pre-selected winner (`selectedValue: ""`) and records `pre_committed: bool` on `InboxAuditRecord`. |
| **2. Reasoning Probes on Overrides & High-Risk Gates** | Mitchell et al. (2026); Vasconcelos et al. (2023) | `distill_from_inbox_audit` (`bridge/agent_gym/engine.py`) and `resolve_action` (`bridge/a2ui_inbox/memory_store.py`). | Evaluates reviewer rationale (`expert_memo`), dwell time (`review_dwell_ms`), and citation inspection (`citation_opened`); unverified rubber-stamp overrides with `oversight_rsr_score < 0.50` are marked `TribalRuleStatus.REJECTED`. |
| **3. Behavioral Oversight Telemetry & `RAIR`/`RSR` Gating** | Mitchell et al. (2026); Schemmer et al. (2023) | `InboxAuditRecord` (`bridge/a2ui_inbox/models.py`) and `record_shadow_comparison` (`bridge/agent_gym/engine.py`). | Records `review_dwell_ms: int`, `citation_opened: bool`, and `pre_committed: bool` on `InboxAuditRecord`, and requires verified oversight (`citation_verified=True` and `oversight_rsr_score >= 0.80`) before incrementing `ProgrammableTrustPolicy.consecutive_matches`. |
| **4. Continuous Spot-Checks & Seeded Honeypots** | Bainbridge (1983); Endsley (2023); Dawid & Skene (1979) | `ProgrammableTrustPolicy.spot_check_sample_rate` (`bridge/agent_gym/models.py`) and `generate_synthetic_stress_suite`. | Routes a configurable `spot_check_sample_rate` (default `0.05` / 5% of `trusted_auto_approve` traffic) plus periodic seeded perturbations from `generate_synthetic_stress_suite` into `guided_review` to prevent skill atrophy and continuously calibrate reviewer $\text{RSR}$. |

## Related
- [Agent Gym × Harness Evolve & Multi-Tier Extraction](harness-evolve.md) · [the Bridge](bridge.md) · [A2UI edge](a2ui-edge.md) · [disposition](disposition.md) · [project layout](architecture.md)
