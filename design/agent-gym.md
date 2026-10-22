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

## 2. Two-Sided Calibration & Human-Oversight Architecture

> **Canonical Literature & Math Source:** General HCI & MRM literature on Why Passive HITL Fails (`Vaccaro et al. 2024`, $\text{Hedges' } g = -0.23$; `Mitchell et al. arXiv:2608.23642`; `Bainbridge 1983`; `Endsley 2023`), Cognitive Forcing Functions (`Buçinca et al. 2021`), Evaluative AI (`Miller 2023`), Appropriate Reliance (`RAIR` & `RSR`, `Schemmer et al. 2023`), and Dawid-Skene honeypots has been consolidated into [`~/projects/agent-evals/docs/playbooks/two-sided-hitl-and-continual-learning.md`](file:///usr/local/google/home/tvaroska/projects/agent-evals/docs/playbooks/two-sided-hitl-and-continual-learning.md) and [`~/projects/agent-evals/agent_evals/calibration.py`](file:///usr/local/google/home/tvaroska/projects/agent-evals/agent_evals/calibration.py).
>
> **DocuBridge Implementation Status (`v1.14.0-agent-gateway`):** Two-Sided Oversight (`oversight_rsr_score` computed from `citation_verified` and `review_dwell_ms`, rejecting rubber-stamps with `RSR < 0.50`), the 3-Tier Evaluation Gate (Tier 1 `Rule 0` deterministic `dispose` + `Decimal` math firewall, Tier 2 `gemini-4-argon` pointwise judge, and Tier 3 `threshold_corridor()` `FA=0` check), `compile_rules_to_skill()` (`SkillRevision` `v1.0.0 -> v1.1.0` + 1-click rollback), and Bi-Temporal Process-Instance Skill Lineage (`ProcessSkillLineageRecord`) are implemented in `bridge/agent_gym.py` and `bridge/user_interaction.py`.

---

### 2.1 DocuBridge-Specific Roadmap & Code Mapping (`bridge/agent_gym.py` & `bridge/a2ui_inbox.py`)

| Mechanism | Literature Anchor | Current Gap in `bridge/agent_gym.py` & `bridge/a2ui_inbox.py` | Candidate Design Pattern |
| :--- | :--- | :--- | :--- |
| **1. Pre-Commitment & Evaluative AI (Anti-Anchoring)** | Mitchell et al. (2026); Buçinca et al. (2021); Miller (2023, *Evaluative AI*) | `InMemoryActionInboxStore.create_action` (`bridge/a2ui_inbox.py:1028-1029`) pre-selects `options[0]["value"]` in `/resolution/selectedValue` and renders `confidenceScore` upfront on `ApprovalCard` (`bridge/a2ui_inbox.py:137-160`). | Add a **Blind Pre-Commitment / Evaluative Mode** for `HIGH`/`CRITICAL` risk items and calibration samples: omit the pre-selected winner (`selectedValue: ""`) and hide `confidenceScore` until the reviewer clicks a `SourceCitationPill` or commits an independent choice (`pre_committed: bool`). |
| **2. Reasoning Probes on Overrides & High-Risk Gates** | Mitchell et al. (2026); Vasconcelos et al. (2023) | `distill_from_inbox_audit` (`bridge/agent_gym.py:711-714`) falls back to `"Expert edit override on ..."` when `/review/memo` is blank, allowing empty memos to mint candidate `TribalKnowledgeRule`s in `_distill_rule_from_signal`. | Trigger an inline **Reasoning Probe** on `HIGH`/`CRITICAL` approvals and edits: require a structured rationale code or cited page/clause before `edit_and_approve` / `accept` succeeds, and refuse to distill a candidate `TribalKnowledgeRule` when `expert_memo` is empty or generic. |
| **3. Behavioral Oversight Telemetry & `RAIR`/`RSR` Gating** | Mitchell et al. (2026); Schemmer et al. (2023) | `record_shadow_comparison` (`bridge/agent_gym.py:1037-1104`) increments `consecutive_matches` on any `ai_value == human_value`, even a sub-second rubber-stamp with zero PDF inspection. | Record `review_dwell_ms: int`, `citation_opened: bool`, and `pre_committed: bool` on `InboxAuditRecord` (`bridge/a2ui_inbox.py:269-290`). Discount unverified/sub-threshold clicks so only *verified* matches increment `ProgrammableTrustPolicy.consecutive_matches`, and track reviewer $\text{RSR}$ alongside `accuracy_rate`. |
| **4. Continuous Spot-Checks & Seeded Honeypots** | Bainbridge (1983); Endsley (2023); Dawid & Skene (1979) | Once a slice reaches `TRUSTED_AUTO_APPROVE` (`bridge/agent_gym.py:98`), humans see 0% of traffic until `drift_incidents` (`bridge/agent_gym.py:1079-1083`) forces an abrupt emergency dial-back. | Route a configurable `spot_check_sample_rate` (e.g., 5% of `trusted_auto_approve` items) plus periodic seeded perturbations from `generate_synthetic_stress_suite` (`bridge/agent_gym.py:352-427`) into `guided_review` to prevent skill atrophy and continuously calibrate reviewer $\text{RSR}$. |

## Related
- [Agent Gym × Harness Evolve & Multi-Tier Extraction](harness-evolve.md) · [the Bridge](bridge.md) · [A2UI edge](a2ui-edge.md) · [disposition](disposition.md) · [project layout](architecture.md)
