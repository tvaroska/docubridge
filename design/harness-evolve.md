---
type: atom
up: "knowledge-model.md"
tags: [bridge, agent-gym, harness-evolve, extraction, docai, optimization]
---

# Agent Gym × Harness Evolve — Calibration at Scale & Multi-Tier Extraction Evolution

> **Canonical Methodology Source:** General Agent Gym, Harness Evolve (`arXiv:2609.00829`), and Harness² (`arXiv:2610.14224`, 8 editable harness surfaces `c1..c8` and Cheapest-Lever Gate) patterns are maintained in `agent-evals` (`docs/playbooks/two-sided-hitl-and-continual-learning.md`). This document specifies the DocuBridge-specific implementation (`bridge/agent_gym/`, `bridge/extraction/gemini.py`, `skills/<doctype>/`).
>
> **Architecture & As-Built Status (`v1.14.0-agent-gateway`):** Unifies **OCTO Agent Gym** (online, SME-in-the-loop "Example of One" tacit knowledge capture with Two-Sided Oversight `RSR`) with **OCTO Harness Evolve (`HaE`)** (`compile_rules_to_skill()` / `POST /v1/gym/compile-skill` folding promoted rules into immutable `SkillRevision` packages in the Gemini platform Skill Registry with 1-click rollback and Bi-Temporal Process-Instance Skill Lineage) across all three extraction tiers: **Gemini (`gemini-3.8-flash` workhorse + `gemini-4-argon` teacher/judge)**, **Document AI Standard Extractors**, and **Document AI Custom Extractors**.

---

## 1. How OCTO Agent Gym and Harness Evolve Relate

Both initiatives share the same Day-2 autonomy thesis—*training models is not training agents; enterprise agents win by evolving on the job*—but solve **two opposite, complementary halves** of the evolution loop:

| Dimension | OCTO Agent Gym (`bridge/agent_gym/`) | OCTO Harness Evolve (`HaE` / `AgentEvolve`) |
| :--- | :--- | :--- |
| **Core Problem** | Agents fail on unwritten enterprise exceptions ("tribal knowledge") | Agents accumulate prompt bloat, waste tool calls, or over-consume tokens |
| **Primary Loop** | **Online / Daytime**, interactive **SME-in-the-loop** capture | **Offline / Nightly**, automated **evolutionary sweep & compilation** |
| **Who Is in the Loop** | **Domain SMEs** (brokers, underwriters, credit officers); **AI devs out of the loop** | **Automated Meta-Optimizer** + platform/FDE builders |
| **Input Signal** | **"Example of One"** (single Quarantine fix, RFC 6902 A2UI patch, or shadow diff) | **Golden Dataset (50/50 Train/Holdout split)** + BigQuery OTLP spans + cost function |
| **Optimization Bias** | **Precision over recall** (high-confidence rules for specific carrier/issuer slices) | **Pareto frontier** across extraction F1, tool calls, p95 latency, and token cost |
| **Core Sub-Engines** | `TribalKnowledgeRule` distiller, `SyntheticStressCase`, `GymReportCard`, `ProgrammableTrustPolicy` | **Stage 1:** `WikiSkill`, `SkillOpt`, `Katachi`, `OctoMem`<br>**Stage 2:** Automated Prompt Optimization (`APO`) token & latency burn-down |
| **Artifact Produced** | Tactical `TribalKnowledgeRule` rows in Cloud SQL + verified labeled examples | An immutable **`SkillRevision`** of a doctype skill — evolved `SKILL.md` prose plus `policy.yaml` thresholds (and the reserved `docai-entity-map.yaml`). Field names, types and descriptions are **not** a compile target: they live in the OKF bundle and are evolved by the [Schema Refinery](schema-refinery.md) |
| **Safety / Promotion Gate** | `evaluate_candidate_rule` (`_FORBIDDEN_MATH_TOKENS` firewall + zero slice regression) + progressive trust dial | Strict **50% Holdout validation** + `bridge.calibration.threshold_corridor()` feasibility + Pareto economic gate |

---

## 2. How DocuBridge Benefits from Each & Their Combined Flywheel

### 2.1 Closing the Daytime Loop with Agent Gym (Implemented Architecture)

DocuBridge runs the 5-pillar Agent Gym v1/v2 loop (`bridge/agent_gym/`):

1. **Inject Promoted Extraction Hints into Live Extraction (`Stage 1 Feedback Loop`):**
   - `AgentGymEngine.get_extraction_hints()` (`bridge/agent_gym/engine.py`) queries promoted `EXTRACTION_HINT` rules from Cloud SQL (`gym_tribal_rules`), `GeminiExtractor._catalog()` (`bridge/extraction/gemini.py`) dynamically rebuilds `build_extraction_prompt(schemas, extraction_hints=...)` whenever promoted rule IDs change, and `ingest_accepted_submission()` (`bridge/user_interaction/surfaces.py`) applies matching hints on Day-2 submissions.
2. **Deterministic Rule Execution on Synthetic Stress Suites:**
   - `generate_synthetic_stress_suite()` (`bridge/agent_gym/models.py`) synthesizes and executes 4 corner-case perturbations (`carrier_layout_shift`, `legal_entity_suffix_variant`, `distinct_syndicate_guard`, `model_upgrade_parity`) via `_execute_rule_on_case()`, and `evaluate_candidate_rule()` (`bridge/agent_gym/engine.py`) verifies positive slice gain, zero historical regression, and an intact `_FORBIDDEN_MATH_TOKENS` firewall before setting `passed_gate=True`.
3. **Two-Sided Human-Oversight Calibration ([Agent Gym](agent-gym.md) §2.1):**
   - Enforces **Blind Pre-Commitment** (`selectedValue: ""` on `HIGH`/`CRITICAL` or evaluative items), **Inline Reasoning Probes** (`oversight_rsr_score` rejecting rubber-stamps with `RSR < 0.50`), **verified `consecutive_matches`** (`RAIR`/`RSR` gating), and **5% stratified spot-checks** on `trusted_auto_approve` slices.

### 2.2 Offline Algorithmic & Cost Optimization with Harness Evolve (`HaE`)

DocuBridge's declarative `skills/` architecture ([skills](skills.md)), BigQuery OTLP span analytics (`bridge/telemetry.py` → `v_step_latency_analytics`), and corridor oracle (`bridge/calibration.py`) map directly to Harness Evolve's 3-layer stack:

1. **Stage 1 — `WikiSkill` on the Doctype `SKILL.md`:**
   - **Where the tokens go:** `build_response_schema()` (`bridge/extraction/gemini_schema.py`) pins the field union of the extraction's ontology slice (`slice_ontology_for_extraction`, [extraction](extraction.md)) onto the request; the prompt carries each doctype's contrastive descriptions, promoted hints and the `SKILL.md` notes. Prompt bloat shows up as `MAX_TOKENS` page-window bisections (`chunking.py`) and schema-retry turns (`RETRY_CORRECTION`, echoing up to 4,000 chars).
   - **Harness Evolve benefit:** Evolves the `SKILL.md` instructions against a **50/50 Train/Holdout split** to maximize first-attempt extraction F1 and eliminate schema validation retries. Field descriptions are a layer-1 concern: a description change is a Schema Refinery `REFINE_DESCRIPTION` proposal on the OKF bundle, never a compiler edit to `assets/schema.json` (a generated file).
2. **Stage 1 — `SkillOpt` Tool-Boundary Discipline on `document_bridge` Root `LlmAgent`:**
   - **Current state:** `build_document_bridge()` (`bridge/agent.py:31-66`) routes messy edge submissions across `extraction_agent` (`AgentTool`), `run_disposition_gate`, `handle_deletion_request`, `reject_submission`, `escalate_submission`, and `request_human_review` (`LongRunningFunctionTool`).
   - **Harness Evolve benefit:** Sharpens `INSTRUCTION` (`bridge/agent.py:22-28`) and tool docstrings against off-script intent traces so the root mediator never misroutes ambiguous counterparty messages or calls tools out of turn.
3. **Stage 2 — DeepMind `APO` Token & Latency Burn-Down:**
   - Compresses `PROMPT_HEADER` / `PROMPT_FOOTER` (`bridge/extraction/gemini.py:157-167`), `PAGE_WINDOW_INSTRUCTION` (`bridge/extraction/gemini.py:210`), and `ASSESSOR_INSTRUCTION` (`bridge/satisfaction_llm.py:31-41`) after Stage 1 accuracy saturates—targeting **38%–60% token reduction** and lower p95 multi-window latency with **0% accuracy loss** on the Holdout split.
4. **Layer 3 — Meta-Optimizer & Per-Slice Corridor Calibration (`bridge/calibration.py`):**
   - **Current state:** `bridge/calibration.py:169-180` enforces that every process skill's `assets/policy.yaml` uses identical `auto_approve_at=0.85, resubmit_below=0.55` thresholds, and every slice uses the same `BRIDGE_MODEL`.
   - **Harness Evolve benefit:** Uses `threshold_corridor()` as a hard safety constraint to optimize **per-process `(auto_approve_at, resubmit_below)` thresholds** and **model down-tiering**—proving where `Gemini 3.5 Flash-Lite` (with an evolved `SKILL.md`) matches `Gemini 3.8 Flash` on `LOW`-risk slices (`slice:closing-disclosure:fee-schedule:LOW`, `slice:chubb:quote:LOW`).

### 2.3 The Combined Two-Speed Flywheel: Solving "Rule Bloat" with Zero Redeploy

Used alone, Agent Gym eventually accumulates hundreds of granular `TribalKnowledgeRule` rows in Cloud SQL (`gym_tribal_rules`). Injecting 200 raw one-off hints into every extraction prompt causes context bloat and rule collisions.

Combining **Agent Gym (Daytime Capture)** with **Harness Evolve (Nightly Skill Compiler)** resolves this through DocuBridge's existing **`skill_registry` port** ([skills](skills.md#read-time-audit--every-skill-consumer-resolves-the-registry-live)):

```
DAYTIME (Online — bridge/agent_gym/ + bridge/a2ui_inbox/)
1. Broker / Underwriter / Credit Officer fixes a field in Zone 2 Quarantine or Zone 5 A2UI Inbox.
2. Agent Gym distills an immediate, high-precision TribalKnowledgeRule ("Example of One")
   for tactical patching AND logs the verified (doc, SourceAnchor, patch, OTel trace) as a
   new golden evaluation record in Cloud SQL / BigQuery.
         │
         ▼
NIGHTLY / BATCH (Offline — Harness Evolve Compiler over 50/50 Train/Holdout Split)
3. WikiSkill compiler (compile_rules_to_skill) folds accumulated TribalKnowledgeRules into a new
   SkillRevision of the doctype skill:
   • Rewrites skills/<doctype>/SKILL.md & references/issuer-canonicalization.md
   • Updates entity mappings in skills/<doctype>/assets/docai-entity-map.yaml (reserved)
   • Leaves field names / types / descriptions alone — they come from catalog/doctypes/*.okf.md
4. DeepMind APO compresses the resulting prompt (-38%..-60% tokens).
5. Dual Safety Gate:
   • Agent Gym Gate: math_firewall_intact (_FORBIDDEN_MATH_TOKENS) + 0 slice regressions
   • Bridge Calibration Gate: bridge.calibration.threshold_corridor() feasible on 50% Holdout
         │
         ▼
PROMOTION (Zero Redeploy — skill_registry.install_skill)
6. Winning SkillRevision (rev-v1-N-<hash>, parent = the current default) is published to the
   skill-registry bucket (version++), and consolidated one-off SQL rules are marked
   TribalRuleStatus.RETIRED. Rollback = re-point the default revision.
7. GeminiExtractor._catalog() and the Agent Card detect the updated skill on the next read
   and hot-reload the evolved prompt — zero container redeploy.
```

### 2.4 Mapping `google-research/harness2` Mechanics to DocuBridge Agent Gym & Skill Compiler

While **Agent Gym** captures online SME corrections ("Example of One") and **Harness Evolve** runs nightly skill compilation, **`google-research/harness2`** (`arXiv:2610.14224`) provides four structural harness-evolution invariants that directly govern how `bridge/agent_gym/` and `skills/` evolve:

| `harness2` Mechanism | Core `harness2` Invariant | DocuBridge Mapping (`bridge/agent_gym/` & `skills/`) |
| :--- | :--- | :--- |
| **1. Orthogonal `INV` vs. `DOM` Split (`Propose–Probe–Compose`)** | Separates **Invariant Machinery (`INV`)**—tool-error recovery, deterministic parsing scripts (`c7`), and completion hooks (`c8`)—from **Practitioner Domain Knowledge (`DOM`)**—document anatomy, reconciliation rules, and on-demand skills (`c2`/`c3`). | `TribalRuleCategory` (`EXTRACTION_HINT`, `ENTITY_ALIAS`, `CONSOLIDATION_PRIORITY`, `PRESENTATION_PREFERENCE`) and `Schema Refinery` (`catalog/doctypes/*.okf.md`) own the **`DOM`** dimension, while `bridge/extraction/chunking.py` (page-window bisection), `bridge/disposition.py` (`Rule 0` gates), and `_FORBIDDEN_MATH_TOKENS` enforce the **`INV`** dimension. |
| **2. Surface Validation & Auto-Trigger Gate (`tree.validate` / `autotrigger`)** | Treats always-on prompt text as the most expensive budget; pushes situation-specific procedures into on-demand `skills/<name>/SKILL.md`, requires every skill to have valid YAML frontmatter + a narrow trigger line in always-on instructions, compiles helper scripts (`py_compile`), and treats `KEEP_BASE` as a valid outcome when candidates do not beat base. | `compile_rules_to_skill()` (`bridge/agent_gym/compiler.py`) folds accumulated SQL rules into versioned `SKILL.md` packages (`SkillRevisionRecord` `rev-v1-N-hash`) validated via `google.adk.skills.models.Frontmatter`, while `evaluate_candidate_rule()` rejects any candidate that fails to improve the target slice (`delta <= 0`, preserving the base revision) or violates the deterministic math firewall. |
| **3. Structured `EXPERIENCE.md` Distillation (`{cue, lesson, evidence}`)** | Refuses to store a lesson in the domain library unless `cue` (exact situation trigger), `lesson` (reusable procedure), and `evidence` (specific trajectory/artifact contrast) are all present. | `distill_from_inbox_audit()` and `distill_from_quarantine_correction()` (`bridge/agent_gym/engine.py`) require a verified `ExpertFeedbackSignal` (`issuer_pattern` + `doctype` cue, `instruction` lesson, and `SourceAnchor` + `oversight_rsr_score >= 0.50` evidence) before minting a promotable rule. |
| **4. Blind Anonymized Selection & Anti-Leak Tripwires (`assign_ids` + `assert_no_grader_verdicts`)** | Anonymizes and seeded-shuffles candidate outputs (`A..Z` with unmodified base runs mixed in) and blocks grader/rubric leakage from entering the selector context. | Implemented in Zone 5 A2UI Inbox via **Blind Pre-Commitment (`Evaluative AI` mode)** (`blind_pre_commitment=True` in `ActionInboxStore.create_action`), which strips pre-selected defaults (`selectedValue: ""`) and hides confidence badges until the reviewer inspects `SourceCitationPill` evidence. |

---

## 3. Multi-Tier Extraction Evolution: Adding Document AI Standard & Custom Extractors

Today, `docubridge` uses `GeminiExtractor` (with an optional deployment-wide `DocAIPrepass` OCR text layer in `bridge/extraction/docai.py`) as a clean baseline. As doctypes graduate to **Document AI Standard Extractors** (e.g., Identity/Driver License, Utility Bill, Closing Disclosure) and **Document AI Custom Extractors** (Foundation-Model Custom Extractor with uptraining for carrier quotes, credit agreements, and appraisals), the architecture preserves **100% downstream compatibility** while adapting what Agent Gym and Harness Evolve optimize inside `Port.extraction`.

### 3.1 What Stays 100% Unchanged

Because `Port.extraction` returns the canonical `ExtractionResult` wire contract (`contract/extraction.py`), **every layer downstream of extraction is completely invariant to the extractor engine**:

- **Disposition & Calibration:** `run_disposition_gate` (`bridge/disposition.py`) and `threshold_corridor` (`bridge/calibration.py`) consume `ExtractionResult` signals identically.
- **Consolidation & Deterministic Math:** Stage 2 `OperationalStore` promotion, `Decimal` financial math, and bi-temporal AlloyDB views are untouched.
- **A2UI Inbox & Programmable Trust:** Zone 5 A2UI cards (`bridge/a2ui_inbox/`), `ExpertFeedbackSignal` capture, and `ProgrammableTrustPolicy` autonomy dials (`shadow_mode` → `guided_review` → `trusted_auto_approve`) work without change.
- **Skill Packaging Contract:** [skills](skills.md) and [ports](ports.md) **already reserve** `metadata.bridge-docai-processor` in `SKILL.md` plus `assets/docai-entity-map.yaml` inside each doctype skill folder.

### 3.2 One Structural Addition Inside `Port.extraction`: Per-Doctype Processor Routing

Whereas `DocAIPrepass` runs a single deployment-wide OCR processor (`BRIDGE_DOCAI_PROCESSOR`) *before* doctype classification, **Standard and Custom Document AI Extractors are bound per doctype** (`metadata.bridge-docai-processor` in `skills/<doctype>/SKILL.md`). Because `extract(artifact_name)` receives an untyped document reference, `Port.extraction` adds a lightweight routing step:

1. **Single-Candidate Fast Path:** When an exchange's active process skill declares a single candidate doctype carrying a `bridge-docai-processor` (e.g., `benefits-rfp` → `benefits-quote`), route directly to that processor and map `document.entities` via `assets/docai-entity-map.yaml`.
2. **Multi-Candidate Classifier Path:** When multiple candidate doctypes exist (e.g., `address-proof` accepts `gov-id` or `utility-bill`, or must reject an out-of-label-space `passport`), run a fast **Document AI Custom Classifier** (or 1-page `Gemini 3.5 Flash-Lite` classification pass) to identify the doctype → dispatch to `skills/<doctype>/SKILL.md`'s `bridge-docai-processor`.
3. **Selective Gemini Hybrid Fallback:** When a doctype declares `bridge-docai-processor: ""` (or when a Document AI Standard Processor leaves a complex multi-page table unextracted), invoke `GeminiExtractor` only for the unmapped fields, grounded by the Document AI OCR text and `SourceAnchor` bounding boxes.

### 3.3 How Agent Gym & Harness Evolve Operate Across All 3 Extractor Tiers

| Extractor Tier | Example Doctypes | What Agent Gym (Online SME Loop) Captures | What Harness Evolve (Offline Optimizer) Mutates |
| :--- | :--- | :--- | :--- |
| **Tier 1: Gemini Extractor**<br>(`bridge-docai-processor: ""`) | Newly onboarded or long-tail doctypes | Distills SME fixes into `EXTRACTION_HINT` and `ENTITY_ALIAS` rules. | • **`WikiSkill` / `APO`:** Mutates and compresses `SKILL.md` prompt instructions into a new `SkillRevision`; field descriptions are evolved in the OKF bundle by the Schema Refinery. |
| **Tier 2: DocAI Standard Extractor**<br>(Fixed Google weights & entity schema) | `gov-id` (ID Proofing / Driver License)<br>`utility-bill` (Utility Parser)<br>`closing-disclosure` | Captures **issuer-specific entity-mapping overrides** (e.g., when a utility provider prints `service_address` in a block DocAI tags as `billing_address`). | • **Cannot mutate Google-managed processor weights.**<br>• Evolves **`assets/docai-entity-map.yaml`** priority/fallback chains.<br>• **Recalibrates confidence corridors** in `bridge/calibration.py` (`auto_approve_at`, `resubmit_below`).<br>• Tunes **selective Gemini fallback rules** (saving ~80% of LLM tokens). |
| **Tier 3: DocAI Custom Extractor**<br>(Foundation-Model Custom Extractor + Uptraining) | `benefits-quote`<br>`credit-agreement`<br>`lloyds-quote`<br>`appraisal` | **Turns SME fixes into labeled DocAI training examples:** `ExpertFeedbackSignal.source_anchor` (`page_number` + `bounding_box`) + `after_value` maps 1:1 to a Document AI labeled `Document` proto (`export_docai_uptraining_dataset` / `GET /v1/gym/export/docai-dataset`). | • **Evolves Custom Extractor property descriptions** (zero-shot/few-shot schema prompts inside DocAI).<br>• **Triggers & gates DocAI Uptraining** on a **50/50 Train/Holdout split**.<br>• Pins the winning `/processorVersions/{v}` in `SKILL.md` via `install_skill`. |

### 3.4 Three High-Leverage Synergies with Document AI

1. **`SourceAnchor` + `ExpertFeedbackSignal` = Turnkey Document AI Uptraining Dataset:**
   - `extract_docai_source_anchors()` (`bridge/extraction/docai.py:245-302`) and `ExpertFeedbackSignal.source_anchor` (`bridge/agent_gym/models.py`) already capture the **exact PDF URI, 1-based page number, normalized bounding box `(x0, y0, x1, y1)`, field path, and corrected value** whenever a reviewer edits a field in Zone 2 Quarantine or Zone 5 A2UI Inbox.
   - `AgentGymEngine.export_docai_uptraining_dataset()` (`bridge/agent_gym/engine.py`, exposed at `GET /v1/gym/export/docai-dataset`) translates those verified tuples directly into Document AI's `ImportDocuments` dataset format (`Document.entities[].page_anchor.page_refs[].bounding_poly.normalized_vertices`). Daytime reviews in the A2UI Inbox automatically build the labeled uptraining dataset for Document AI Custom Extractors with zero separate labeling tool.
2. **Confidence Corridor Recalibration (`bridge/calibration.py`) Across Engines:**
   - Gemini self-reported `field_confidence` values (`0.80–0.98`) have a different calibration curve than Document AI optical/entity confidence scores (`0.62–0.99`).
   - Whenever a doctype switches from `gemini` to a Document AI Standard or Custom processor, Harness Evolve's Meta-Optimizer runs `bridge.calibration.threshold_corridor()` against the 50/50 split to compute engine-calibrated `(auto_approve_at, resubmit_below)` thresholds in `assets/policy.yaml`.
3. **Version-Pinned Processor Promotion & Instant Rollback via `SKILL.md`:**
   - `_PROCESSOR_RE` (`bridge/extraction/docai.py:81-89`) already accepts version-pinned resource names:
     `projects/{project}/locations/{location}/processors/{id}/processorVersions/{version}`
   - When an uptrained Document AI Custom Extractor version passes the **50% Holdout gate** and `GymReportCard`, Harness Evolve promotes it by updating `metadata.bridge-docai-processor` in `skills/<doctype>/SKILL.md` from `.../processorVersions/v1` to `.../processorVersions/v2` via `install_skill`.
   - If `record_shadow_comparison()` (`bridge/agent_gym/engine.py`) later detects carrier format drift, **Instant Safety Dial-Back** reverts both the `ProgrammableTrustPolicy` autonomy level *and* the pinned `/processorVersions/{v}` in `SKILL.md` with zero redeploy.

---

## 4. Implementation Status & Roadmap (`v1.14.0`)

| Step | Scope | Key Modules & Ports | Status & Verification Gate |
| :--- | :--- | :--- | :--- |
| **Phase 1: Daytime Hint Injection & Live Stress Replay** | Wire `AgentGymEngine.get_extraction_hints()` into `GeminiExtractor` prompt assembly and replay real fixture/signal documents inside `evaluate_candidate_rule()`. | `bridge/agent_gym/engine.py`<br>`bridge/extraction/gemini.py` | **Implemented (`v1.14.0`):** Unit + port tests verify a promoted `EXTRACTION_HINT` alters the extraction prompt and passes live 3-tier evaluation. |
| **Phase 2: Per-Doctype DocAI Standard & Custom Extractor Routing** | Activate `metadata.bridge-docai-processor` + `assets/docai-entity-map.yaml` per doctype skill, with `Document.entities` → `ExtractionResult` mapping and selective Gemini fallback. | `bridge/extraction/docai.py`<br>`bridge/doctypes.py`<br>`skills/*/SKILL.md` | **Design Ready:** Pre-pass OCR + `SourceAnchor` bbox extraction is active (`tests/test_docai_extraction.py`); per-doctype `docai-entity-map.yaml` routing is next. |
| **Phase 3: Skill Compiler (`compile_rules_to_skill`)** | Fold promoted `gym_tribal_rules` into content-addressed `SkillRevisionRecord`s (`POST /v1/gym/compile-skill`) with 1-click rollback (`POST /v1/gym/skills/{skill_name}/rollback`). | `bridge/agent_gym/engine.py`<br>`bridge/calibration.py`<br>`Port.skill_registry` | **Implemented (`v1.14.0`):** Verified in `tests/test_enterprise_data_architecture.py`. |
| **Phase 4: DocAI Custom Extractor Dataset Export & Two-Sided HITL Calibration** | Export verified `ExpertFeedbackSignal` + `SourceAnchor` records to Document AI `ImportDocuments` format (`GET /v1/gym/export/docai-dataset`), and enforce Pre-Commitment / `RSR` metrics in A2UI Inbox. | `bridge/agent_gym/engine.py`<br>`bridge/a2ui_inbox/` | **Implemented (`v1.14.0`):** End-to-end test exporting labeled `Document` protos and enforcing verified `consecutive_matches` before `trusted_auto_approve`. |

## Related
- [knowledge model](knowledge-model.md) · [Agent Gym & Programmable Trust](agent-gym.md) · [Schema Refinery](schema-refinery.md) · [Skills](skills.md) · [Extraction](extraction.md) (the Document AI pre-pass) · [Managed-Service Ports](ports.md) · [Disposition](disposition.md)
