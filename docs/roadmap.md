# Roadmap

The high-level milestone view of the build. Each milestone is defined by **what it proves on stage** (the demo beat / [CUJ](../spec/cujs.md) it unlocks), with the per-feature breakdown in [`features.md`](features.md) and historical decisions in [`DECISIONS.md`](../DECISIONS.md) (an ephemeral root `TODO.md` can be created when an active sprint requires granular task tracking).

The core commitment: **one deployed core on the Gemini platform running Address, Employee Benefits, Emergent RFP, and the 6-Zone Financial Services & Regulated Industry Enterprise Showcase (`LOAN-MERIDIAN-001`, `MORTGAGE-WESTBROOK-2026`, `terrepower-coastal-2027`) end-to-end across all 13 Hexagonal `Port` boundaries (`sessions`, `task_store`, `exchange_store`, `skill_registry`, `memory_bank`, `scheduler`, `a2a_client`, `extraction`, `email_ingress`, `audit_vault`, `operational_store`, `action_inbox`, `agent_gym`).**

> _Current status:_ Cycle 1 milestones **M0–M7 are complete** — M7 ("It's deployed") closed 2026-10-01 — and the Benefits cycle (CUJ‑3/11/12), Emergent RFP cycle (CUJ‑9/13/15, including variable-packaging `.zip`/`.msg`/`.eml` container unpacking in `bridge/extraction/containers.py` and resilient `.docx`/`.rtf`/`.xlsx` prepass + `1 -> N` bundled sub-entity fan-out in `bridge/extraction/office_prepass.py`), Hybrid `document_bridge` LlmAgent edge (off-script routing, real-erase deletion, HITL park/resume), the 6-Zone Enterprise Data Architecture + Agent Gym + Agent Gateway + Consolidation Collaboration Engine + Live Multimodal PDF `/ebc` showcase, and **Phase 8 — Schema Refinery & Insurance Showcase (`P8-1`–`P8-6`)** are all built, merged, and verified on `main`. Next up are **Phase 9 — Showcase Generalization (`P9-1`–`P9-3`)**, then **`P8-7`–`P8-8`** (`2` open items) and **`FX-1`**.

## Cycle 1 milestones — Core + Address

| # | Milestone | Proves | Unlocks (CUJ) | Phase |
|---|---|---|---|---|
| **M0** | Skeleton stands up | Repo, contracts, and the shared port harness exist; `bridge/` can't import `agents/` | — | P0 |
| **M1** | The Bridge judges one document | Sense-A disposition is deterministic and auditable; the LLM can't mint an acceptance | 1, 2 (core) | P1 |
| **M2** | Agents can talk to the Bridge | Canonical A2A wire + a stable mock double; domain rides inside A2A parts | 1, 2 (wire) | P2 |
| **M3** | Address collects, end-to-end | The reusable `Workflow` shape works; **code, not a model, mints "done"** | 1, 4, 10 | P3 |
| **M4** | It survives time | Park → restart → resume with zero compute; human-in-the-loop pause | 5, 7 | P4 |
| **M5** | It chases | The clock-driven follow-up ladder fires a **real** nudge — "mediation, not extraction" | 6 | P5 |
| **M6** | It's visible & human-usable | The three surfaces make the process legible; a human uploads via the portal; a doctype is added live | 2, 8 | P6 |
| **M7** | It's deployed | The same golden runs reproduce on GCP; the platform showcase is real, not a toy | 1, 4, 6, 7, 10 (deployed) | P7 |

### The headline demo beat (M3–M5)
Jordan Lee is asked for proof of address, submits two bills from the **same** company → the *agent* (not the Bridge) rejects the set and asks for one more; a second **distinct-issuer** bill arrives → satisfied → one clean normalized artifact is delivered. A blurry bill triggers resubmission; an expired ID routes to a human; a silent party gets chased on the SLA clock and escalates. All from a single "asks once" request.

## Definition of done — Cycle 1

- Every [`features.md`](features.md) item marked `C1` behaves as its acceptance line states.
- The Address eval corpus (`wiki/evals/address/expected.json` + `timeline.json`) reproduces on **fixture and Gemini** extraction adapters, against **mock and real** Bridge, **local and deployed**.
- CUJ‑1, 2, 4, 5, 6, 7, 8, 10 pass. Park/resume survives a real process restart. The proactive chase fires a real send.
- The port suite passes with `BRIDGE_TEST_GCP=1`; plan-time Terraform tests pass with no credentials.

## Beyond Cycle 1

| Cycle | Adds | CUJ |
|---|---|---|
| **Benefits** | Negotiate over a program, FX normalization, ADK↔LangGraph A2A interop, Bridge-as-client dial-out + Deliver | 3, 11, 12 |
| **RFP** | Emergent Collect (agent-mutated requirements, N suppliers), recursive `.zip`/`.msg`/`.eml` container unpacking + HTML reply-table advisories (`bridge/extraction/containers.py`), resilient `.docx` redline / `.rtf` / `.xlsx` prepass + issuer-mismatch gate + `1 -> N` bundled fan-out (`bridge/extraction/office_prepass.py`), email reply intake, live use-case-add | 9, 13, 15 |
| **Later swaps** | Cold unsolicited inbound push (the Extract edge). Both Gemini platform Agent Engine migration steps are built: Option A (Agent Engine Sessions, Memory Bank, and ADK `App` runtime) and Option B (`BridgeA2aAgentEngine` hosting the A2A JSON-RPC server, `CollectExecutor`, and dynamic Agent Card on Agent Engine behind `BRIDGE_AGENT_ENGINE_A2A=1`) | — |

The architecture is whole from day one — outbound port, trust boundary, contracts — so these cycles add demand-driven capability, not new machinery. See [layout & scope](../design/architecture.md) and [open questions](../spec/open-questions.md).

## Phase 8 — Schema Refinery & Ontology-Driven Document Types (`P8-1`–`P8-8`)

<!-- Counters: p0=18 p1=10 p2=8 p3=9 p4=6 p5=6 p6=9 p7=12 p8=8 p9=4 -->

> Full technical specification: [`design/schema-refinery.md`](../design/schema-refinery.md). `P8-1` through `P8-6` are complete and merged on `main`; `P8-7` through `P8-8` track the remaining ontology governance milestones.

- [x] **P8-1: Contrastive Descriptions & OKF Schema Package Loader (`bridge/doctypes.py`, `bridge/extraction/gemini_schema.py`, `catalog/doctypes/*.okf.md`)**
  - Extend `DoctypeConfig` and `ContractSchema` to load top-level entity descriptions, contrastive field metadata (`what_it_is` / `where_to_read` / `distinguish_from`), and `OntologyEdge` declarations from `catalog/doctypes/*.okf.md` (alongside existing `skills/*/SKILL.md` + `assets/schema.json` across `commercial-property-quote`, `benefits-quote`, `trid-closing-disclosure`, `cre-credit-agreement`, and `mai-appraisal`).
  - Wire root entity `description` (`genai_types.Schema(type=OBJECT, description=...)`) and synthesized contrastive field descriptions into `build_response_schema()` and `build_extraction_prompt()`.
  - **Verification Gate (T1/T2):** All existing unit tests pass + new `tests/test_schema_refinery_okf.py` verifies non-empty root/field `Schema.description` and layout-perturbation Krippendorff's $\alpha \ge 0.80$.

- [x] **P8-2: Graph-Driven Consolidation in Zone 3 & Zone 6 (`contract/refinery.py`, `bridge/operational_store/consolidation_rules.py`, `bridge/knowledge_catalog.py`)**
  - Define shared Pydantic wire models (`ContrastiveDescription`, `OntologyEdge`, `RefineryProposalKind`, `RefineryAssayReport`, `OntologyRevision`) in `contract/refinery.py` (preserving the `bridge/` ↔ `contract/` ↔ `agents/` isolation firewall).
  - Replace hardcoded `if/elif` doctype priority chains and `_MONEY_KEYS` in `bridge/operational_store/consolidation_rules.py` with declarative graph lookups (`[:SUPERSEDES]` and `[:FEEDS_METRIC]`) loaded from the active `OntologyRevision` in Zone 6 `KnowledgeCatalog`.
  - **Verification Gate (T1/T2):** `tests/test_enterprise_data_architecture.py` and `tests/test_documentation_contract.py` pass with 100% identical financial rollups across `terrepower-coastal-2027`, `LOAN-MERIDIAN-001`, and `MORTGAGE-WESTBROOK-2026`.

- [x] **P8-3: Schema Refinery Engine & Bi-Temporal Ontology Versioning (`bridge/refinery/`, `bridge/sql_runtime.py`)**
  - Implement Stages 1–4 of [`design/schema-refinery.md`](../design/schema-refinery.md): taxonomy assay detectors (bimodal null split detector $J(A,B) \le 0.10$, escape-slug new-entity detector, confusion merge detector), contrastive description compiler, 3-Tier Assay Gate (`RefineryAssayReport` enforcing $\text{NFR} = 0.0\%$, $\alpha \ge 0.80$, $\text{AC}_2 \ge 0.80$), and bi-temporal SQL tables (`refinery_ontology_revisions`, `refinery_proposals`, `refinery_assay_reports`) multiplexed on `DsnSqlRuntime` with `[:SPLIT_FROM]` / `[:MERGED_INTO]` lineage edges.
  - **Verification Gate (T1/T2):** End-to-end test in `tests/test_schema_refinery_engine.py` proving both Insurance (`commercial-property-quote` endorsement vs. base quote refinement on `terrepower-coastal-2027`) and CRE/Mortgage (`trid-closing-disclosure` → `fee-schedule-addendum` split) evolve `ont-rev-v1-1` → `ont-rev-v1-2` while preserving historical `v1.1` lineage.

- [x] **P8-4: Interactive Schema Refinery Tab & Insurance-First Showcase Mode (`demo/enterprise-demo.html`, `demo/enterprise-demo.js`, `bridge/user_interaction/`)**
  - Mount `/v1/refinery/*` FastAPI endpoints (`GET /v1/refinery/revisions`, `POST /v1/refinery/assay`, `POST /v1/refinery/promote`) and add the interactive **Schema Refinery** workflow to `/demo` (`demo/enterprise-demo.html` + `demo/enterprise-demo.js`), demonstrating bimodal field sparsity detection, contrastive description diffs, the 3-Tier BigQuery counterfactual assay, and live ontology promotion with `[:SPLIT_FROM]` lineage.
  - Support `?domain=insurance` URL parameter and `BRIDGE_DEFAULT_DEMO_DOMAIN=insurance` environment configuration so `/demo` on an insurance-first deployment opens directly into the **Insurance (Commercial Property — `terrepower-coastal-2027`)** domain while keeping default `banking` startup when unset (preserving all `test_documentation_contract.py` and Playwright invariants).

- [x] **P8-5: Example-Driven Doctype Onboarding (`bridge/refinery/onboarding.py`, `bridge/user_interaction/refinery_routes.py`)**
  - Because DocuBridge operates as a document-mediation executor for calling agents rather than a standalone warehouse crawler, implement an example-driven onboarding workflow (`onboard_doctype_from_examples` and `POST /v1/refinery/onboard`): given 1–5 sample documents or structured examples plus optional `paired_target_examples` and `spine_entity_id` / `context_set`, `gemini-4-argon` (or deterministic structural induction offline) synthesizes a candidate `.okf.md` semantic-entity bundle (`ContrastiveDescription`, `synonyms`, `[:FEEDS_METRIC]`, `[:SUPERSEDES]`, `target_projection_hints`) in `DRAFT` state and runs the 3-Tier Assay Gate (`NFR == 0.0%`) before promotion.
  - **Verification Gate (T1/T2):** Unit and assay tests in `tests/test_refinery_onboarding_and_slicing.py` verify draft `.okf.md` synthesis from sample fixtures and full 3-Tier Assay Gate evaluation.

- [x] **P8-6: Target-Scoped Ontology Slicing per Extraction Call (`bridge/doctypes.py`, `bridge/extraction/gemini_schema.py`, `bridge/extraction/gemini.py`)**
  - Scope `slice_ontology_for_extraction()`, `build_extraction_prompt()`, `build_response_schema()`, and `GeminiExtractor.extract()` to the known target `doctype` or active process `candidate_doctypes` (`context_set`) plus their shared spine and 1-hop ontology neighborhood (`[:SUPERSEDES]`, `[:SPLIT_FROM]`, `[:DISTINCT_FROM]`), instead of injecting the union of every installed doctype on every call. Keep `fields.doctype` as a free string so out-of-label-space documents still report their observed type cleanly.
  - **Verification Gate (T1/T2):** Unit tests in `tests/test_refinery_onboarding_and_slicing.py` verify target-scoped prompt/schema generation, reduced token footprint per call, and preserved `unsupported` disposition on out-of-label-space uploads.

- [ ] **P8-7: Read-Only Allow-Listed Ontology MCP Surface over Zone 6 (`bridge/knowledge_catalog/`)**
  - Expose an allow-listed, strictly read-only MCP tool surface over Zone 6 `KnowledgeCatalog` (`get_entity_ontology`, `explain_metric_formula`, `check_doctype_precedence`, `trace_field_lineage`) for calling A2A/MCP agents, keeping all state-changing ontology mutations (`onboard_doctype_from_examples`, `register_proposal`, `promote_proposal`) isolated to the Assay-Gated admin control plane.
  - **Verification Gate (T1/T2):** Unit tests verify read-only MCP tool schemas, deterministic graph lookups, and zero mutation endpoints on the MCP surface.

- [ ] **P8-8: Pre-Execution Query & Metric Formula Validator (`bridge/knowledge_catalog/`, `bridge/operational_store/`)**
  - Add deterministic pre-execution validation (`validate_metric_query` / `validate_consolidation_view`) in Zone 6 so Zone 5 Domain Agent queries and analytical rollups over `OperationalStore` and BigQuery (`bq_operational_facts_iceberg`) are checked against certified `[:FEEDS_METRIC]` edges, bi-temporal watermarks (`learned_as_of`, `effective_on`), and `[:SUPERSEDES]` active-fact precedence before execution.
  - **Verification Gate (T1/T2):** Unit tests verify that queries bypassing certified `[:FEEDS_METRIC]` formulas or omitting bi-temporal/precedence predicates are deterministically rejected.

## Phase 9 — Showcase Generalization (`P9-1`–`P9-3`)

> DocuBridge is the base for broader industry showcases that call it **over A2A as an agent** (not as a Python dependency). Phase 9 removes the per-domain hand-written scenario code and doctype duplication so a new showcase is a `scenarios/<name>/` folder plus `catalog/` and `skills/` bundles, with zero changes to `bridge/`. Ordered ahead of `P8-7`/`P8-8`.

- [x] **P9-1: Generic Scenario Contract & Loader (`contract/scenario.py`, `bridge/showcase/`, `scenarios/<name>/`)**
  - Scenario data lives in `scenarios/<id>/scenario.yaml` (`ScenarioManifest`: display copy, entity ids, agents/principals, seed facts, optional `stage` with setup, beats as ordered named operations, declarative PDF assets under `pdf/*.yaml`, live-extraction bindings), discovered from `BRIDGE_SCENARIOS_DIR` (default `<repo>/scenarios`). `bridge/showcase/` holds the generic `ScenarioEngine` (per-instance state and PDF caches, no module globals), the operation registry (`financial_summary`, `ingest_documents`, `patch_state`, `inbox_waiver`, `gym_distill_and_compile`, `refinery_reset`, `refinery_run_assay`, `refinery_promote`), the declarative PDF renderer, live Gemini PDF extraction, and routes `GET /v1/demo/scenarios`, `/v1/demo/scenarios/{id}/{state,advance,reset}`, `/v1/demo/scenarios/seed`; the legacy `/v1/demo/consolidation-collab/*`, `/v1/demo/schema-evolution/*`, `/v1/demo/seed`, and `/ebc*` paths are thin aliases. The seven hand-written scenario modules (`consolidation_*`, `schema_evolution_*`, `demo_seed` logic; 4,102 LOC) were deleted; `demo/enterprise-demo.js` builds the domain switcher, labels, entity ids, and stage titles from the manifests.
  - **Verification Gate (T1/T2):** `tests/test_showcase_scenarios.py` (13 tests) reproduces the consolidation and schema-evolution beat outcomes and seeds of all five scenarios through the engine, and proves a fixture scenario folder appears in `GET /v1/demo/scenarios` with no Python change; `tests/test_isolation_guard.py::test_showcase_names_no_scenario_literals` fails on any scenario entity/client/program literal inside `bridge/` (allow-listed with a stale check: `bridge/refinery/assay.py`, `bridge/refinery/benefits_assay.py` replay corpora — open follow-up to move those deal ids into the manifests). Remaining literals in `demo/enterprise-demo.js` (23, down from 71) are Scene 1/2 storyboard HTML outside the seed/beat contract.

- [x] **P9-2: Single Doctype Source of Truth (`catalog/doctypes/*.okf.md` → `skills/<doctype>/assets/schema.json`)**
  - The OKF bundle is canonical. `bridge/doctypes.py::doctype_schemas()` renders the extraction schema from `catalog/doctypes/<name>.okf.md` whenever a bundle exists; `assets/schema.json` is a generated artifact kept in sync by `python -m bridge.refinery.okf_loader --write-schemas` (`--check` exits non-zero when stale). OKF frontmatter gained `fields.<name>.enum`, top-level `tables`, and `schema_version` so closed vocabularies and table schemas no longer need a hand-written JSON Schema. Drift resolved: `trid-closing-disclosure` (`annual_debt_service`, `interest_rate`, `issuer` added to schema; `appraised_value` added to OKF as optional; `effective_date` required), `benefits-quote` (`employer_account_seed`, `plan_type`, `waiting_period_days` added to schema), `commercial-property-quote` (`effective_date` required, `total_insured_value` optional).
  - **Verification Gate (T1/T2):** `tests/test_doctype_consistency.py` (12 tests) fails on any field/required/type/enum/table mismatch between OKF and on-disk `schema.json`, requires every doctype skill with a schema to have a bundle, and asserts the generator is a no-op on the committed tree; all existing extraction and conformance tests pass unchanged.

- [x] **P9-3: Downstream Showcase Integration over A2A (`docs/consume-over-a2a.md`, `examples/a2a_consumer/`, `/.well-known/agent-card.json`)**
  - `docs/consume-over-a2a.md` documents what a downstream showcase supplies (`skills/`, `catalog/doctypes/`, `scenarios/`, optional console files), the content-root knobs (`BRIDGE_SKILLS_DIR` / `BRIDGE_SKILLS_BUCKET`, `BRIDGE_OKF_DOCTYPES_DIR`, `BRIDGE_SCENARIOS_DIR`, `BRIDGE_DEMO_STATIC_DIR`, `BRIDGE_DEFAULT_DEMO_DOMAIN`, `BRIDGE_PUBLIC_URL`), the Agent Card fields a caller may rely on, and the Collect round-trip wire (`CollectRequest` data part → task state → `exchange_turn` artifact → `LedgerEntry`). `examples/a2a_consumer/consume_bridge.py` is a standalone `a2a-sdk` driver (imports only `a2a`, `httpx`, `contract`, stdlib) that resolves the card, refuses unadvertised processes, sends one Collect, and prints the disposition. Console files (`/demo`, `/console`, `/ebc*`) are served from `demo_static_root()` (`BRIDGE_DEMO_STATIC_DIR`, default `<repo>/demo`) instead of `Path(__file__).parents[2]`.
  - **Verification Gate (T1/T2):** `tests/test_a2a_consumer_contract.py` (6 tests) boots the A2A edge and dashboard from a tmp tree holding one scenario folder plus its skills, OKF bundles, and console files, and verifies the Agent Card advertises exactly the installed process skill, one Collect reaches a terminal accepted disposition, `GET /v1/demo/scenarios` lists exactly that scenario, `/demo` and `/ebc` return 200 (404 once the static root loses the file), the example consumer runs in-process and prints the disposition, and the doc names every knob with each present in `.env.example`; `tests/test_isolation_guard.py::test_examples_never_import_bridge_or_agents` keeps `examples/` on wire + `contract/` only.

- [ ] **P9-4: Declared Collection Strategy & Sufficiency Verdict (ADR-14) (`contract/wire.py`, `skills/<process>/assets/strategy.yaml`, `bridge/strategy/`, `bridge/satisfaction.py`, `bridge/a2ui_inbox/`, `agents/address/gate.py`)**
  - Add `assets/strategy.yaml` to process skills (ordered steps and alternatives, per-doctype counts, cross-document constraints referencing ontology fields — `distinct`, `same`, `max_age_days`, `unless` — validated against the active `OntologyRevision`; no business-policy predicates). Implement a deterministic evaluator in `bridge/strategy/` that produces `sufficiency = {satisfied, strategy_revision, evidence[], outstanding[]}` on every turn, with request-level `CollectRequest.requirements` overriding the skill default; carry it on `ExchangeTurn` (additive, contract minor bump). `bridge/satisfaction.py` uses the evaluator when a strategy exists and keeps the prose/LLM path as fallback; `bridge-satisfaction` prose is generated from the strategy. Chase messages and `outstanding[]` follow the declared order ("ask for the ID first").
  - Demote waivers (ADR-14 D4): the action-inbox waiver verb and the Day-2 auto-waiver produce a **recommendation** in `sufficiency.evidence[]` and leave the requirement `required`; only the calling agent sets `waived` by returning the item. Rewire the consolidation scenario's Beat 3/4 and the demo copy accordingly (`scenarios/loan-meridian-consol-2026/`). Calibration compiles promoted sufficiency rules into strategy diffs (new skill revision with lineage) rather than prose; autonomy scope for sufficiency rules stays `guided_review` until the deferred decision in ADR-14 §5.
  - Migrate `address-proof` (ID first, else two bills with distinct issuer within 90 days) and `commercial-loan-underwriting` to `strategy.yaml`; reduce `agents/address/gate.py::is_satisfied` to accept-or-override of the verdict.
  - **Verification Gate (T1/T2):** CUJ-1/4/10 golden runs and `tests/test_collect.py` produce identical terminal outcomes with the strategy evaluator; new `tests/test_strategy.py` covers ordering, counts, distinct-issuer, recency, request-level override, and that no rule may reference a non-ontology field or a monetary threshold; `tests/test_enterprise_data_architecture.py` consolidation test asserts the waiver is a recommendation and the item stays `required` until the calling agent waives; `tests/test_documentation_contract.py` asserts `design/collect.md` and `design/actors.md` carry the three-row decision table.

## Future Experiments

- [ ] **FX-1: 3-Arm Ontology Ablation Benchmark in `RefineryAssayReport` (`bridge/refinery/assay.py`)**
  - Evaluate candidate ontology revisions across three explicit ablation arms—**Arm 0 (Schema-Only Control:** flat JSON `field: type` with zero contrastive descriptions or graph edges), **Arm 1 (+ Contrastive Descriptions:** adding `what_it_is` / `where_to_read` / `distinguish_from` to isolate Stage-1 `gemini-3.8-flash` extraction disambiguation lift), and **Arm 2 (+ Full Ontology Graph:** adding `[:SUPERSEDES]`, `[:INCLUDES]` / `[:EXCLUDES]`, and `[:SPLIT_FROM]` edges to isolate Stage-2 consolidation conflict-resolution lift and quarantine reduction).

## See also
- [`DECISIONS.md`](../DECISIONS.md) — architectural decision log · [`features.md`](features.md) — the feature catalog · [the Bridge](../design/bridge.md) — design source of truth · [Schema Refinery](../design/schema-refinery.md) — self-evolving ontology & taxonomy engine
