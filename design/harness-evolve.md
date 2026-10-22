---
type: atom
up: "bridge.md"
related:
  - "agent-gym.md"
  - "skills.md"
  - "docai-prepass.md"
  - "seams.md"
  - "disposition.md"
tags: [bridge, agent-gym, harness-evolve, extraction, docai, optimization]
---

# Agent Gym × Harness Evolve — Continual Learning & Multi-Tier Extraction Evolution

> **Architecture & As-Built Status (`v1.14.0-agent-gateway`):** Unifies **OCTO Agent Gym** (online, SME-in-the-loop "Example of One" tacit knowledge capture with Two-Sided Oversight `RSR`) with **OCTO Harness Evolve (`HaE`)** (`compile_rules_to_skill()` / `POST /v1/gym/compile-skill` folding promoted rules into immutable `SkillRevision` packages in the Gemini platform Skill Registry with 1-click rollback and Bi-Temporal Process-Instance Skill Lineage) across all three extraction tiers: **Gemini (`gemini-3.8-flash` workhorse + `gemini-4-argon` teacher/judge)**, **Document AI Standard Extractors**, and **Document AI Custom Extractors**.

---

## 1. How OCTO Agent Gym and Harness Evolve Relate

Both initiatives share the same Day-2 autonomy thesis—*training models is not training agents; enterprise agents win by evolving on the job*—but solve **two opposite, complementary halves** of the evolution loop:

| Dimension | OCTO Agent Gym (`bridge/agent_gym.py`) | OCTO Harness Evolve (`HaE` / `AgentEvolve`) |
| :--- | :--- | :--- |
| **Core Problem** | Agents fail on unwritten enterprise exceptions ("tribal knowledge") | Agents accumulate prompt bloat, waste tool calls, or over-consume tokens |
| **Primary Loop** | **Online / Daytime**, interactive **SME-in-the-loop** capture | **Offline / Nightly**, automated **evolutionary sweep & compilation** |
| **Who Is in the Loop** | **Domain SMEs** (brokers, underwriters, credit officers); **AI devs out of the loop** | **Automated Meta-Optimizer** + platform/FDE builders |
| **Input Signal** | **"Example of One"** (single Quarantine fix, RFC 6902 A2UI patch, or shadow diff) | **Golden Dataset (50/50 Train/Holdout split)** + BigQuery OTLP spans + cost function |
| **Optimization Bias** | **Precision over recall** (high-confidence rules for specific carrier/issuer slices) | **Pareto frontier** across extraction F1, tool calls, p95 latency, and token cost |
| **Core Sub-Engines** | `TribalKnowledgeRule` distiller, `SyntheticStressCase`, `GymReportCard`, `ProgrammableTrustPolicy` | **Stage 1:** `WikiSkill`, `SkillOpt` (`Fueki`), `Katachi`, `Koku`, `OctoMem`<br>**Stage 2:** DeepMind `APO` token & latency burn-down |
| **Artifact Produced** | Tactical `TribalKnowledgeRule` rows in Cloud SQL + verified labeled examples | Evolved, compressed `skills/<doctype>/` packages (`SKILL.md`, `schema.json`, `policy.yaml`, `docai-entity-map.yaml`) |
| **Safety / Promotion Gate** | `evaluate_candidate_rule` (`_FORBIDDEN_MATH_TOKENS` firewall + zero slice regression) + progressive trust dial | Strict **50% Holdout validation** + `bridge.calibration.threshold_corridor()` feasibility + Pareto economic gate |

---

## 2. How DocuBridge Benefits from Each & Their Combined Flywheel

### 2.1 Closing the Daytime Loop with Agent Gym (Next-Gen Upgrades)

DocuBridge already runs the 5-pillar Agent Gym v1 foundation (`bridge/agent_gym.py`). Three targeted wiring upgrades complete the online loop:

1. **Inject Promoted Extraction Hints into Live Extraction (`Stage 1 Feedback Loop`):**
   - **Current state:** `AgentGymEngine.get_extraction_hints()` (`bridge/agent_gym.py:1060-1069`) queries promoted `EXTRACTION_HINT` rules from Cloud SQL (`gym_tribal_rules`), and `normalize_entity_or_issuer()` (`bridge/agent_gym.py:1071-1092`) applies `ENTITY_ALIAS` rules in Stage 2 consolidation. However, `GeminiExtractor._catalog()` (`bridge/extraction/gemini.py:686-702`) only reads static `doctype_schemas()`.
   - **Next step:** Feed promoted `(issuer_pattern, doctype)` extraction hints into `build_extraction_prompt()` (or compile them into `skills/<doctype>/references/`) so the very next document upload benefits on the first pass.
2. **Replace Simulated Stress Metadata with Live Extractor Replay:**
   - **Current state:** `generate_synthetic_stress_suite()` (`bridge/agent_gym.py:392-467`) synthesizes 4 corner-case descriptors (`carrier_layout_shift`, `legal_entity_suffix_variant`, `distinct_syndicate_guard`, `model_upgrade_parity`) with static `candidate_passed=True`.
   - **Next step:** Have `evaluate_candidate_rule()` (`bridge/agent_gym.py:905-1025`) replay the candidate rule against live fixture/sandbox documents in `wiki/evals/` and historical `gym_feedback_signals` before setting `passed_gate=True`.
3. **Wire Two-Sided Human-Oversight Calibration (`design/agent-gym.md` §2.3):**
   - Enforce **Blind Pre-Commitment** (`selectedValue: ""` and hidden `confidenceScore` on `HIGH`/`CRITICAL` risk items until a `SourceCitationPill` is opened), **Inline Reasoning Probes** when `/review/memo` is blank (`bridge/agent_gym.py:783`), **verified `consecutive_matches`** (`RAIR`/`RSR` gating), and **5% stratified spot-checks** on `trusted_auto_approve` slices.

### 2.2 Offline Algorithmic & Cost Optimization with Harness Evolve (`HaE`)

DocuBridge's declarative `skills/` architecture (`design/skills.md`), BigQuery OTLP span analytics (`bridge/telemetry.py` → `v_step_latency_analytics`), and corridor oracle (`bridge/calibration.py`) map directly to Harness Evolve's 3-layer stack:

1. **Stage 1 — `WikiSkill` & `Koku` on Doctype `SKILL.md` and `assets/schema.json`:**
   - **Current bottleneck:** `build_response_schema()` (`bridge/extraction/gemini.py:310-423`) pins the **union of every installed doctype's `key_fields` and `tables`** onto every extraction request. As Banking (`closing-disclosure`, `credit-agreement`, `appraisal`) and Insurance (`benefits-quote`, `chubb-quote`, `lloyds-quote`) doctypes grow, schema bloat increases token consumption, `MAX_TOKENS` page-window bisections (`chunking.py`), and schema-retry turns (`RETRY_CORRECTION`, echoing up to 4,000 chars).
   - **Harness Evolve benefit:** Evolves `SKILL.md` instructions and `assets/schema.json` field descriptions against a **50/50 Train/Holdout split** to maximize first-attempt extraction F1 and eliminate schema validation retries.
2. **Stage 1 — `SkillOpt` (`Fueki`) Tool-Boundary Discipline on `document_bridge` Root `LlmAgent`:**
   - **Current state:** `build_document_bridge()` (`bridge/agent.py:31-66`) routes messy edge submissions across `extraction_agent` (`AgentTool`), `run_disposition_gate`, `handle_deletion_request`, `reject_submission`, `escalate_submission`, and `request_human_review` (`LongRunningFunctionTool`).
   - **Harness Evolve benefit:** Sharpens `INSTRUCTION` (`bridge/agent.py:22-28`) and tool docstrings against off-script intent traces so the root mediator never misroutes ambiguous counterparty messages or calls tools out of turn.
3. **Stage 2 — DeepMind `APO` Token & Latency Burn-Down:**
   - Compresses `PROMPT_HEADER` / `PROMPT_FOOTER` (`bridge/extraction/gemini.py:157-167`), `PAGE_WINDOW_INSTRUCTION` (`bridge/extraction/gemini.py:210`), and `ASSESSOR_INSTRUCTION` (`bridge/satisfaction_llm.py:31-41`) after Stage 1 accuracy saturates—targeting **38%–60% token reduction** and lower p95 multi-window latency with **0% accuracy loss** on the Holdout split.
4. **Layer 3 — Meta-Optimizer & Per-Slice Corridor Calibration (`bridge/calibration.py`):**
   - **Current state:** `bridge/calibration.py:169-180` enforces that every process skill's `assets/policy.yaml` uses identical `auto_approve_at=0.85, resubmit_below=0.55` thresholds, and every slice uses the same `BRIDGE_MODEL`.
   - **Harness Evolve benefit:** Uses `threshold_corridor()` as a hard safety constraint to optimize **per-process `(auto_approve_at, resubmit_below)` thresholds** and **model down-tiering**—proving where `Gemini 3.5 Flash-Lite` (with an evolved `SKILL.md`) matches `Gemini 3.8 Flash` on `LOW`-risk slices (`slice:closing-disclosure:fee-schedule:LOW`, `slice:chubb:quote:LOW`).

### 2.3 The Combined Two-Speed Flywheel: Solving "Rule Bloat" with Zero Redeploy

Used alone, Agent Gym eventually accumulates hundreds of granular `TribalKnowledgeRule` rows in Cloud SQL (`gym_tribal_rules`). Injecting 200 raw one-off hints into every extraction prompt causes context bloat and rule collisions.

Combining **Agent Gym (Daytime Capture)** with **Harness Evolve (Nightly Skill Compiler)** resolves this through DocuBridge's existing **`skill_registry` seam** (`design/skills.md:142-148`):

```
DAYTIME (Online — bridge/agent_gym.py + bridge/a2ui_inbox.py)
1. Broker / Underwriter / Credit Officer fixes a field in Zone 2 Quarantine or Zone 5 A2UI Inbox.
2. Agent Gym distills an immediate, high-precision TribalKnowledgeRule ("Example of One")
   for tactical patching AND logs the verified (doc, SourceAnchor, patch, OTel trace) as a
   new golden evaluation record in Cloud SQL / BigQuery.
         │
         ▼
NIGHTLY / BATCH (Offline — Harness Evolve Compiler over 50/50 Train/Holdout Split)
3. WikiSkill + Koku folds accumulated TribalKnowledgeRules into the doctype skill folder:
   • Rewrites skills/<doctype>/SKILL.md & references/issuer-canonicalization.md
   • Refines field descriptions in skills/<doctype>/assets/schema.json
   • Updates entity mappings in skills/<doctype>/assets/docai-entity-map.yaml
4. DeepMind APO compresses the resulting prompt & schema descriptions (-38%..-60% tokens).
5. Dual Safety Gate:
   • Agent Gym Gate: math_firewall_intact (_FORBIDDEN_MATH_TOKENS) + 0 slice regressions
   • Bridge Calibration Gate: bridge.calibration.threshold_corridor() feasible on 50% Holdout
         │
         ▼
PROMOTION (Zero Redeploy — skill_registry.install_skill)
6. Winning skill folder is published to gs://docubridge-showcase-bridge-skills/<doctype>/ (version++),
   and consolidated one-off SQL rules are marked TribalRuleStatus.RETIRED.
7. GeminiExtractor._catalog() and the Agent Card detect the updated skill on the next read
   and hot-reload the evolved prompt, schema, and processor binding — zero container redeploy.
```

---

## 3. Multi-Tier Extraction Evolution: Adding Document AI Standard & Custom Extractors

Today, `docubridge` uses `GeminiExtractor` (with an optional deployment-wide `DocAIPrepass` OCR text layer in `bridge/extraction/docai.py`) as a clean baseline. As doctypes graduate to **Document AI Standard Extractors** (e.g., Identity/Driver License, Utility Bill, Closing Disclosure) and **Document AI Custom Extractors** (Foundation-Model Custom Extractor with uptraining for carrier quotes, credit agreements, and appraisals), the architecture preserves **100% downstream compatibility** while adapting what Agent Gym and Harness Evolve optimize inside `Seam.extraction`.

### 3.1 What Stays 100% Unchanged

Because `Seam.extraction` returns the canonical `ExtractionResult` wire contract (`contract/extraction.py`), **every layer downstream of extraction is completely invariant to the extractor engine**:

- **Disposition & Calibration:** `run_disposition_gate` (`bridge/disposition.py`) and `threshold_corridor` (`bridge/calibration.py`) consume `ExtractionResult` signals identically.
- **Consolidation & Deterministic Math:** Stage 2 `OperationalStore` promotion, `Decimal` financial math, and bi-temporal AlloyDB views are untouched.
- **A2UI Inbox & Programmable Trust:** Zone 5 A2UI cards (`bridge/a2ui_inbox.py`), `ExpertFeedbackSignal` capture, and `ProgrammableTrustPolicy` autonomy dials (`shadow_mode` → `guided_review` → `trusted_auto_approve`) work without change.
- **Skill Packaging Contract:** `design/skills.md:45-58` and `design/seams.md:42` **already reserved** `metadata.bridge-docai-processor` in `SKILL.md` plus `assets/docai-entity-map.yaml` inside each doctype skill folder.

### 3.2 One Structural Addition Inside `Seam.extraction`: Per-Doctype Processor Routing

Whereas `DocAIPrepass` runs a single deployment-wide OCR processor (`BRIDGE_DOCAI_PROCESSOR`) *before* doctype classification, **Standard and Custom Document AI Extractors are bound per doctype** (`metadata.bridge-docai-processor` in `skills/<doctype>/SKILL.md`). Because `extract(artifact_name)` receives an untyped document reference, `Seam.extraction` adds a lightweight routing step:

1. **Single-Candidate Fast Path:** When an exchange's active process skill declares a single candidate doctype carrying a `bridge-docai-processor` (e.g., `benefits-rfp` → `benefits-quote`), route directly to that processor and map `document.entities` via `assets/docai-entity-map.yaml`.
2. **Multi-Candidate Classifier Path:** When multiple candidate doctypes exist (e.g., `address-proof` accepts `gov-id` or `utility-bill`, or must reject an out-of-label-space `passport`), run a fast **Document AI Custom Classifier** (or 1-page `Gemini 3.5 Flash-Lite` classification pass) to identify the doctype → dispatch to `skills/<doctype>/SKILL.md`'s `bridge-docai-processor`.
3. **Selective Gemini Hybrid Fallback:** When a doctype declares `bridge-docai-processor: ""` (or when a Document AI Standard Processor leaves a complex multi-page table unextracted), invoke `GeminiExtractor` only for the unmapped fields, grounded by the Document AI OCR text and `SourceAnchor` bounding boxes.

### 3.3 How Agent Gym & Harness Evolve Operate Across All 3 Extractor Tiers

| Extractor Tier | Example Doctypes | What Agent Gym (Online SME Loop) Captures | What Harness Evolve (Offline Optimizer) Mutates |
| :--- | :--- | :--- | :--- |
| **Tier 1: Gemini Extractor**<br>(`bridge-docai-processor: ""`) | Newly onboarded or long-tail doctypes | Distills SME fixes into `EXTRACTION_HINT` and `ENTITY_ALIAS` rules. | • **`WikiSkill` / `APO`:** Mutates and compresses `SKILL.md` prompt instructions and `assets/schema.json` descriptions. |
| **Tier 2: DocAI Standard Extractor**<br>(Fixed Google weights & entity schema) | `gov-id` (ID Proofing / Driver License)<br>`utility-bill` (Utility Parser)<br>`closing-disclosure` | Captures **issuer-specific entity-mapping overrides** (e.g., when a utility provider prints `service_address` in a block DocAI tags as `billing_address`). | • **Cannot mutate Google-managed processor weights.**<br>• Evolves **`assets/docai-entity-map.yaml`** priority/fallback chains.<br>• **Recalibrates confidence corridors** in `bridge/calibration.py` (`auto_approve_at`, `resubmit_below`).<br>• Tunes **selective Gemini fallback rules** (saving ~80% of LLM tokens). |
| **Tier 3: DocAI Custom Extractor**<br>(Foundation-Model Custom Extractor + Uptraining) | `benefits-quote`<br>`credit-agreement`<br>`lloyds-quote`<br>`appraisal` | **Turns SME fixes into labeled DocAI training examples:** `ExpertFeedbackSignal.source_anchor` (`page_number` + `bounding_box`) + `after_value` maps 1:1 to a Document AI labeled `Document` proto. | • **Evolves Custom Extractor property descriptions** (zero-shot/few-shot schema prompts inside DocAI).<br>• **Triggers & gates DocAI Uptraining** on a **50/50 Train/Holdout split**.<br>• Pins the winning `/processorVersions/{v}` in `SKILL.md` via `install_skill`. |

### 3.4 Three High-Leverage Synergies with Document AI

1. **`SourceAnchor` + `ExpertFeedbackSignal` = Turnkey Document AI Uptraining Dataset:**
   - `extract_docai_source_anchors()` (`bridge/extraction/docai.py:245-302`) and `ExpertFeedbackSignal.source_anchor` (`bridge/agent_gym.py:134`) already capture the **exact PDF URI, 1-based page number, normalized bounding box `(x0, y0, x1, y1)`, field path, and corrected value** whenever a reviewer edits a field in Zone 2 Quarantine or Zone 5 A2UI Inbox.
   - That tuple translates directly into Document AI's `ImportDocuments` dataset format (`Document.entities[].page_anchor.page_refs[].bounding_poly`). Daytime reviews in the A2UI Inbox automatically build the labeled uptraining dataset for Document AI Custom Extractors with zero separate labeling tool.
2. **Confidence Corridor Recalibration (`bridge/calibration.py`) Across Engines:**
   - Gemini self-reported `field_confidence` values (`0.80–0.98`) have a different calibration curve than Document AI optical/entity confidence scores (`0.62–0.99`).
   - Whenever a doctype switches from `gemini` to a Document AI Standard or Custom processor, Harness Evolve's Meta-Optimizer runs `bridge.calibration.threshold_corridor()` against the 50/50 split to compute engine-calibrated `(auto_approve_at, resubmit_below)` thresholds in `assets/policy.yaml`.
3. **Version-Pinned Processor Promotion & Instant Rollback via `SKILL.md`:**
   - `_PROCESSOR_RE` (`bridge/extraction/docai.py:81-89`) already accepts version-pinned resource names:
     `projects/{project}/locations/{location}/processors/{id}/processorVersions/{version}`
   - When an uptrained Document AI Custom Extractor version passes the **50% Holdout gate** and `GymReportCard`, Harness Evolve promotes it by updating `metadata.bridge-docai-processor` in `skills/<doctype>/SKILL.md` from `.../processorVersions/v1` to `.../processorVersions/v2` via `install_skill`.
   - If `record_shadow_comparison()` (`bridge/agent_gym.py:1125-1207`) later detects carrier format drift, **Instant Safety Dial-Back** reverts both the `ProgrammableTrustPolicy` autonomy level *and* the pinned `/processorVersions/{v}` in `SKILL.md` with zero redeploy.

---

## 4. Post-Demo Implementation Roadmap (Next Phase)

To preserve full stability for the current `v1.11.0-bq-telemetry` demo builds, implementation is sequenced into four self-contained post-demo steps:

| Step | Scope | Key Modules & Seams | Verification Gate |
| :--- | :--- | :--- | :--- |
| **Phase 1: Daytime Hint Injection & Live Stress Replay** | Wire `AgentGymEngine.get_extraction_hints()` into `GeminiExtractor` prompt assembly and replay real fixture/signal documents inside `evaluate_candidate_rule()`. | `bridge/agent_gym.py`<br>`bridge/extraction/gemini.py` | Unit + seam tests verifying a promoted `EXTRACTION_HINT` alters the extraction prompt and passes live replay. |
| **Phase 2: Per-Doctype DocAI Standard & Custom Extractor Routing** | Activate `metadata.bridge-docai-processor` + `assets/docai-entity-map.yaml` per doctype skill, with `Document.entities` → `ExtractionResult` mapping and selective Gemini fallback. | `bridge/extraction/docai.py`<br>`bridge/doctypes.py`<br>`skills/*/SKILL.md` | Shared seam parity tests (`tests/test_docai_extraction.py`) verifying entity mapping, `SourceAnchor` bboxes, and terminal-outcome parity. |
| **Phase 3: Harness Evolve Offline Skill Compiler (`bridge/harness_evolve.py`)** | Build offline compiler folding promoted `gym_tribal_rules` into `SKILL.md` / `schema.json` / `docai-entity-map.yaml`, running `APO` prompt compression, and gating on a 50/50 split + `threshold_corridor()`. | `bridge/harness_evolve.py`<br>`bridge/calibration.py`<br>`Seam.skill_registry` | Holdout evaluation gate (`passed_gate=True`, `threshold_corridor.feasible=True`) + hot-reload via `install_skill`. |
| **Phase 4: DocAI Custom Extractor Dataset Export & Two-Sided HITL Calibration** | Export verified `ExpertFeedbackSignal` + `SourceAnchor` records to Document AI `ImportDocuments` format, and wire Pre-Commitment / Reasoning Probes / `RAIR`+`RSR` metrics in A2UI Inbox. | `bridge/agent_gym.py`<br>`bridge/a2ui_inbox.py` | End-to-end test exporting labeled `Document` protos and enforcing verified `consecutive_matches` before `trusted_auto_approve`. |

## Related
- [Agent Gym & Programmable Trust](agent-gym.md) · [Skills](skills.md) · [Document AI pre-pass](docai-prepass.md) · [Managed-Service Seams](seams.md) · [Disposition](disposition.md)
