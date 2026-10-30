# Roadmap

The high-level milestone view of the build. Each milestone is defined by **what it proves on stage** (the demo beat / [CUJ](../spec/cujs.md) it unlocks), with the per-feature breakdown in [`features.md`](features.md) and historical decisions in [`DECISIONS.md`](../DECISIONS.md) (an ephemeral root `TODO.md` can be created when an active sprint requires granular task tracking).

The core commitment: **one deployed core on the Gemini platform running Address, Employee Benefits, Emergent RFP, and the 6-Zone Financial Services & Regulated Industry Enterprise Showcase (`LOAN-MERIDIAN-001`, `MORTGAGE-WESTBROOK-2026`, `terrepower-coastal-2027`) end-to-end across all 13 Hexagonal `Port` boundaries (`sessions`, `task_store`, `exchange_store`, `skill_registry`, `memory_bank`, `scheduler`, `a2a_client`, `extraction`, `email_ingress`, `audit_vault`, `operational_store`, `action_inbox`, `agent_gym`).**

> _Current status:_ Cycle 1 milestones **M0–M7 are complete** — M7 ("It's deployed") closed 2026-10-01 — and the Benefits cycle (CUJ‑3/11/12), Emergent RFP cycle (CUJ‑9/13), Hybrid `document_bridge` LlmAgent edge (off-script routing, real-erase deletion, HITL park/resume), and the 6-Zone Enterprise Data Architecture + Agent Gym + Agent Gateway + Consolidation Collaboration Engine showcase (`v1.14.0-agent-gateway`) are all built and verified on `main` (`documents001`). **Phase 8 — Schema Refinery & Insurance Showcase (`P8-1`–`P8-4`)** is complete and deployed on the isolated `feat/schema-refinery` branch targeting GCP project `documents002` (`0` open items).

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
| **RFP** | Emergent Collect (agent-mutated requirements, N suppliers), email reply intake, live use-case-add | 9, 13, 15 |
| **Later swaps** | Cold unsolicited inbound push (the Extract edge). Both Gemini platform Agent Engine migration steps are built: Option A (Agent Engine Sessions, Memory Bank, and ADK `App` runtime) and Option B (`BridgeA2aAgentEngine` hosting the A2A JSON-RPC server, `CollectExecutor`, and dynamic Agent Card on Agent Engine behind `BRIDGE_AGENT_ENGINE_A2A=1`) | — |

The architecture is whole from day one — outbound port, trust boundary, contracts — so these cycles add demand-driven capability, not new machinery. See [layout & scope](../design/architecture.md) and [open questions](../spec/open-questions.md).

## Phase 8 — Schema Refinery & Insurance Showcase (`feat/schema-refinery` → `documents002`)

<!-- Counters: p0=18 p1=10 p2=8 p3=9 p4=6 p5=6 p6=9 p7=12 p8=4 -->

> **Branch & Environment Isolation Rule:** Keep `main` frozen on GCP project `documents001` (`#321488252989`, `default` Terraform workspace) for the Banking / Mortgage EBC showcase. Execute all `P8-*` tasks below on the **`feat/schema-refinery`** branch and deploy exclusively to GCP project **`documents002`** (`#816370755208`) using `terraform -chdir=terraform workspace select -or-create documents002` and `./scripts/deploy.sh --project documents002`. Full technical specification: [`design/schema-refinery.md`](../design/schema-refinery.md).

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
  - Support `?domain=insurance` URL parameter and `BRIDGE_DEFAULT_DEMO_DOMAIN=insurance` environment configuration so `/demo` on `documents002` opens directly into the **Insurance (Commercial Property — `terrepower-coastal-2027`)** domain while keeping default `banking` startup when unset (preserving all `test_documentation_contract.py` and Playwright invariants).

## See also
- [`DECISIONS.md`](../DECISIONS.md) — architectural decision log · [`features.md`](features.md) — the feature catalog · [the Bridge](../design/bridge.md) — design source of truth · [Schema Refinery](../design/schema-refinery.md) — self-evolving ontology & taxonomy engine
