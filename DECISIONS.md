# Architecture Decision Records (ADR)

This document captures the foundational architectural decisions and milestone records (`S0-arch-*`, `P7-*`, and `ADR-01` through `ADR-14`) that shape **DocuBridge**, an enterprise Agent-to-Agent (A2A) document collection, verification, consolidation, and governance reference architecture built on Google ADK (`google-adk >= 2.11.0, < 3`), the A2A wire protocol (`a2a-sdk`), and the **Gemini platform** (`gemini-3.8-flash` runtime workhorse and `gemini-4-argon` teacher/judge/escalation model).

---

## ADR-01: Hexagonal Ports & Adapters Across All 13 Boundaries (`local` vs. `gcp`) (`S0-arch-13`, `P7-2`–`P7-5`)

- **Context:** Enterprise document workflows require managed cloud durability and zero-trust security in production (Cloud Run v2, Agent Engine, Cloud SQL for PostgreSQL 16 / AlloyDB, Cloud Storage WORM, Document AI, and Cloud Tasks), while local development and CI require fast, credential-free execution.
- **Decision:** Factor all 13 external infrastructure boundaries behind explicit Hexagonal **Ports & Adapters** in `bridge/ports/`: the 9 core boundaries (`sessions`, `task_store`, `exchange_store`, `skill_registry`, `memory_bank`, `scheduler`, `a2a_client`, `extraction`, `email_ingress`) plus the 4 Zone 3–6 Enterprise Data Architecture stores (`audit_vault`, `operational_store`, `action_inbox`, `agent_gym`). Each port provides a deterministic `local` adapter (`bridge/ports/local/`) and a production `gcp` adapter (`bridge/ports/gcp/`) resolved via `BRIDGE_PORT_<NAME>` / `BRIDGE_PORT_MODE` that fails fast with an actionable `RuntimeError` if required environment variables (`DATABASE_URL`, `BRIDGE_SKILLS_BUCKET`, `BRIDGE_VAULT_BUCKET`) are missing.
- **Consequence:** Every scenario in `tests/` runs against `local` adapters in milliseconds and against `gcp` adapters via `@pytest.mark.port(...)` without code changes, and hybrid local+cloud debugging can flip any individual boundary (`BRIDGE_PORT_EXTRACTION=gcp`, `BRIDGE_PORT_OPERATIONAL_STORE=local`).


---

## ADR-02: Hybrid Deterministic 4-Signal Gate + ADK `LlmAgent` (`2026-08-24`, `S0-arch-17`)

- **Context:** Pure LLM judgment on regulatory document acceptability introduces non-determinism on compliance thresholds (document age, optical legibility, required schema fields, or corroborating issuers), whereas rigid rule engines cannot handle off-script customer questions or multi-turn negotiation.
- **Decision:** Implement a two-stage disposition architecture (`bridge/signals.py`, `bridge/collect.py`, `bridge/agent.py`):
  1. **Deterministic 4-Signal Disposition Gate (`dispose_signals`):** Compute objective quality, freshness, completeness, and corroboration signals against declarative `SKILL.md` frontmatter thresholds (`quality_threshold`, `max_age_days`, `corroboration`). A model can never mint a compliance `accepted` disposition on its own (`tests/test_edge_gate_inversion.py`).
  2. **ADK `LlmAgent` Orchestration (`gemini-3.8-flash`):** Route conversational follow-ups, park borderline items via `LongRunningFunctionTool` (`request_human_review`), and escalate complex cross-document conflicts to `gemini-4-argon`.
- **Consequence:** Regulatory hard stops are deterministic and auditable (`Rule 0`), while user-facing guidance and exception handling remain conversational.

---

## ADR-03: Strict Two-Way Package Isolation (`bridge/` ↔ `contract/` ↔ `agents/`)

- **Context:** Co-mingling the reusable document bridge (`bridge/`) with sample caller or carrier agents (`agents/`) causes leaky abstractions and hidden state coupling.
- **Decision:** Enforce an AST-verified two-way import firewall (`tests/test_isolation_guard.py`): `bridge/` and `agents/` share types exclusively through `contract/` (`contract/wire.py`, `contract/extraction.py`, `contract/rfp_quote.py`, `contract/checklist.py`, `contract/enums.py`). Neither `bridge/` nor `agents/` may import from the other.
- **Consequence:** Counterparty agents integrate strictly over the canonical A2A protocol and published wire schemas.

---

## ADR-04: Declarative Skill Packages & Zero Hardcoded Skill Names (`test_core_names_no_skill`)

- **Context:** Onboarding a new document type (`gov-id`, `utility-bill`, `benefits-quote`) or multi-slot process (`address-proof`, `benefits-procurement`, `benefits-rfp`) must not require editing Python code in `bridge/`.
- **Decision:** Package domain knowledge as self-contained skill folders under `skills/` (or `gs://<project>-bridge-skills/` in GCP) with `SKILL.md` frontmatter, `assets/schema.json`, `assets/policy.yaml`, and optional `assets/docai-entity-map.yaml`. Enforce an AST literal guard (`tests/test_isolation_guard.py::test_core_names_no_skill`) that fails if any non-docstring string literal in `bridge/` names an installed skill directory.
- **Consequence:** New document types and processes are hot-loaded or evolved at runtime without redeploying `bridge/`.

---

## ADR-05: Dual-Path Intake & Document AI + Gemini Extraction with `SourceAnchor` Provenance (`2026-08-24`)

- **Context:** Enterprise intake mixes structured feeds (Path A JSON/Excel) with scanned/native PDFs (Path B) that require optical layout grounding and page-level citations.
- **Decision:** Validate Path A payloads directly against JSON Schema at `\$0` OCR/LLM cost (`bridge/patha.py`). Route Path B documents through an optional Document AI OCR/layout pre-pass (`bridge/extraction/docai.py`) followed by `gemini-3.8-flash` structured extraction (`bridge/extraction/gemini.py`) with multi-window page chunking (`bridge/extraction/chunking.py`), attaching `SourceAnchor` (`page_number`, normalized bounding polygon `vertices`, `verbatim_quote`, `confidence`) to every extracted field.
- **Consequence:** Every consolidated fact links directly to its exact source PDF page and bounding polygon in the UI proof modal.

---

## ADR-06: 7-Year WORM Cloud Storage Audit Vault & Bi-Temporal SQL Operational Store (`S0-arch-14`, `P7-2`)

- **Context:** Regulated banking, mortgage, and insurance workflows require tamper-proof binary retention alongside bi-temporal relational fact history that never overwrites prior values.
- **Decision:** Pair two durable stores on every accepted submission:
  1. **Cloud Storage WORM Audit Vault (`bridge/vault.py`):** Scrub edge PII (`redact_personal_data`) and lock raw artifacts in Cloud Storage (`GcpAuditVaultStore`, `BRIDGE_VAULT_BUCKET`) with SHA-256 content hashes and 7-year retention metadata (`RetentionPolicy.SEVEN_YEARS`).
  2. **Bi-Temporal SQL Operational Store (`bridge/operational_store/`):** Persist append-only `ConsolidatedFact` rows (`field_path`, `field_value`, `numeric_value`, `currency`, `source_priority`, `recorded_at`, `effective_from`, `version`, `source_anchor_json`, `vault_uri`, `skill_revision_id`) in Cloud SQL for PostgreSQL 16 / AlloyDB (`SqlOperationalStore`) using exact `Decimal` math, composite index `ix_operational_facts_bitemporal`, and watermarked BigQuery Open Iceberg CDC export (`BIGQUERY_ICEBERG_DDL`).
- **Consequence:** Any balance, fee, or deductible can be queried both *as known at time $T_1$* (`recorded_at`) and *as effective on date $T_2$* (`effective_from`) with zero floating-point drift.

---

## ADR-07: Zone 6 Knowledge Catalog & 6-Hop W3C PROV-O Lineage

- **Context:** Auditors and data stewards need to trace any reported figure back through its consolidation rule, extraction model, skill revision, and WORM vault object.
- **Decision:** Maintain a Zone 6 `KnowledgeCatalog` (`bridge/knowledge_catalog/`) governing business glossary terms, asset locations across all 6 zones, SPIFFE workload registrations, and stateless 6-hop End-to-End Lineage (`LineageTrace`: `VaultObject` → `ExtractionRun` → `QuarantineGate` → `ConsolidationRule` → `OperationalFact` → `A2UISurface`) hydrated automatically from `OperationalStore` on cache miss.
- **Consequence:** Lineage queries succeed across stateless Cloud Run replicas even after cold starts.

---

## ADR-08: SPIFFE JWT-SVID & OBO Compound Identity (`Agent ∩ User`) (`P7-5`)

- **Context:** An autonomous or assistive agent acting on behalf of a human operator must never exceed either the workload's permissions or the human user's tenant scope and financial approval limit.
- **Decision:** Enforce cryptographic SPIFFE JWT-SVID verification (`mint_jwt_svid`, `verify_jwt_svid` in `bridge/identity.py`) combined with On-Behalf-Of (OBO) human principal resolution (`bridge/user_interaction/auth.py`). Effective permissions are computed as the strict intersection `Agent(SPIFFE) ∩ User(Principal)`, enforcing Shadow AI blocking (`ShadowAgentError`), row-level client/carrier isolation, and per-role approval caps across `CatalogRole` (`broker`, `claims`, `underwriter`, `executive`, `client`, `carrier`).
- **Consequence:** Unregistered agents or unauthorized role escalations fail closed before touching operational data.

---

## ADR-09: Declarative A2UI v0.9 Surfaces & Security-Zone-Isolated Action Inbox

- **Context:** Borderline extractions and high-value overrides (`addendum`, `closing-disclosure`, `endorsement`) must park cleanly for human review and stream zero-flicker progress updates to both internal and external channels.
- **Decision:** Implement A2UI v0.9 surface rendering and the Action Inbox (`bridge/a2ui_inbox/`, `SqlActionInboxStore`) with strict widget-catalog isolation between internal (`gemini_app` → `enterprise_inbox_catalog.json`) and external (`custom_web` → `portal_customer_catalog.json`) channels, native ADK `ToolContext.request_confirmation` pause/resume, the 4+1 governance verbs (`accept`, `edit_and_approve`, `respond`, `reject`, `escalate`), and immutable RFC 6902 JSON Patch audit records (`InboxAuditRecord`).
- **Consequence:** Human reviewers inspect side-by-side PDF bounding-box proofs and resolve exceptions with a tamper-evident RFC 6902 diff trail.

---

## ADR-10: Closed-Loop Agent Gym, Two-Sided Oversight (`RSR`), & Gemini Platform Registry/Gateway

- **Context:** Static extraction prompts degrade on novel carrier/lender layouts, while uncalibrated human reviewers risk rubber-stamping automated suggestions.
- **Decision:**
  1. **Agent Gym (`bridge/agent_gym/`):** Capture daytime "Example of One" SME corrections from Quarantine and Inbox RFC 6902 patches, enforce **Two-Sided Oversight** (`oversight_rsr_score >= 0.50` based on citation verification and review dwell time), evaluate candidate `TribalKnowledgeRule`s through a 3-Tier gate (Tier 1 deterministic `Rule 0` + `Decimal` firewall, Tier 2 `gemini-4-argon` autorater, Tier 3 `threshold_corridor()` `FA=0` check), and compile promoted rules into content-addressed `SkillRevisionRecord`s (`POST /v1/gym/compile-skill`).
  2. **Gemini Platform Agent Registry & Agent Gateway (`bridge/agent_gym/registry_gateway.py`):** Govern all 4 registry component types (`Agents`, `MCP Servers`, `Endpoints`, `Skills` + `Bindings`) and enforce `CLIENT_TO_AGENT` (Model Armor + Cloud DLP) and `AGENT_TO_ANYWHERE` (IAM v3 Unified Access Policy CEL) gateway policies.
- **Consequence:** Production runtime inference stays fast and economical on `gemini-3.8-flash`, while `gemini-4-argon` acts as teacher, judge, and skill compiler.

---

## ADR-11: AST Service-Wiring Guard & Shared DSN SQL Runtime (`2026-09-07`, `P7-2`, `P7-4`, `P7-9`)

- **Context:** Two recurring cloud failure modes are (a) accidentally constructing an `InMemory*` store unconditionally on a multi-replica Cloud Run path (the session-affinity trap), and (b) opening redundant per-store event-loop threads and connection pools against the same Cloud SQL instance.
- **Decision:**
  1. Enforce `tests/test_service_wiring_guard.py` (`2026-09-07`), an AST check guaranteeing that every `InMemory*()` construction in `bridge/` sits behind an explicit parameter or environment guard, paired with `BRIDGE_PORT_MODE=gcp` fail-fast checks across all SQL stores.
  2. Multiplex synchronous-facade SQL stores (`SqlExchangeStore`, `SqlOperationalStore`, `SqlAgentGymEngine`, `SqlActionInboxStore`) over a refcounted per-DSN async SQLAlchemy runtime (`bridge/sql_runtime.py`) so stores sharing `DATABASE_URL` share one event-loop thread and one bounded `AsyncEngine` connection pool, with opt-in zero-secret Cloud SQL IAM token injection (`BRIDGE_DB_IAM_AUTH=1` via `enable_iam_auth` + `roles/cloudsql.instanceUser` + `CLOUD_IAM_SERVICE_ACCOUNT`).

  3. Deploy via a two-phase Terraform apply (`scripts/deploy.sh`, `P7-4`/`P7-5`/`P7-9`) that resolves Cloud Run self-referential URLs cleanly and pipes `DATABASE_URL` directly to Secret Manager via `stdin` (never written to disk or Terraform state).
- **Consequence:** Multi-replica Cloud Run and Agent Engine workers share durable state safely without connection pool exhaustion or secret leakage.

---

## ADR-12: 22-Step OpenTelemetry `gen_ai.*` Tracing & BigQuery `Rule 0` SQL Evaluation

- **Context:** Running an LLM judge over 100% of production traces is prohibitively expensive and subject to autorater sensitivity/specificity bias ($q_0, q_1 < 1$).
- **Decision:** Emit W3C-correlated OpenTelemetry spans (`bridge/telemetry/`) with `BatchSpanProcessor` to Cloud Trace v2 and BigQuery (`otel_spans`), and evaluate traces in three layers (`terraform/modules/telemetry/main.tf`, `GET /v1/telemetry/sql-eval`):
  1. **Layer 1 (`v_trace_sql_evaluation`):** Check 5 deterministic `Rule 0` trajectory & governance gates in BigQuery Standard SQL at `\$0` LLM token cost across 100% of traces.
  2. **Layer 2 (`AI.GENERATE_TABLE` with `gemini-4-argon`):** Trigger LLM-as-a-Judge in SQL only on traces where `needs_llm_judge_sampling = TRUE` (~5% of traffic, cutting judge token spend by ~95%).
  3. **Layer 3 (Rogan-Gladen Debiasing):** Correct raw autorater pass rates ($p_{\text{obs}}$) against gold SME calibration labels in SQL via $\hat{\theta} = \text{clip}((p_{\text{obs}} + q_0 - 1) / (q_1 + q_0 - 1), 0, 1)$.
- **Consequence:** Full-population compliance verification runs at SQL speed and zero token cost, while `gemini-4-argon` evaluation is reserved for anomalies and mathematically debiased.

---

## ADR-13: Schema Refinery, OKF Doctype Bundles & Bi-Temporal Ontology Graph (`P8-1`–`P8-4`)

- **Context:** Overloaded monolithic document schemas (e.g., forcing 1-page Schedule B endorsements or fee addenda into full multi-page quotes/disclosures) create bimodal field sparsity ($J(A,B) \le 0.10$) and equal-priority consolidation ties (`Prio 20 == Prio 20`). Bi-temporal ontology revisions (`effective_from` / `effective_to` + `[:SPLIT_FROM]` lineage) allow the taxonomy to split and evolve safely without breaking historical audit reproducibility or violating the `bridge/` ↔ `contract/` ↔ `agents/` isolation firewall.
- **Decision:** Package every document type as a declarative Open Knowledge Format (`catalog/doctypes/*.okf.md`) bundle carrying top-level entity scope, contrastive field triples (`what_it_is`, `where_to_read`, `distinguish_from`), and typed ontology edges (`[:SUPERSEDES]`, `[:FEEDS_METRIC]`, `[:SPLIT_FROM]`, `[:MERGED_INTO]`). Drive Zone 3 (`bridge/operational_store/consolidation_rules.py`) priority resolution and metric rollups directly from the active `OntologyRevision` graph in Zone 6 (`KnowledgeCatalog`), and gate all ontology promotions (`SchemaRefineryEngine`) behind a 3-Tier Counterfactual Assay enforcing $\text{NFR} = 0.0\%$ and, only when perturbation stability is evaluated against a live extractor (raters = catalog permutations, items = document × field), Krippendorff's $\alpha \ge 0.80$ and Gwet's $\text{AC}_2 \ge 0.80$; offline the report states "not evaluated" rather than a figure.
- **Consequence:** New document subtypes (`wind-deductible-endorsement`, `fee-schedule-addendum`) are discovered from production sparsity clusters, verified with zero negative flips before promotion, and deployed on an isolated insurance-first showcase project (`BRIDGE_DEFAULT_DEMO_DOMAIN=insurance`) while the primary banking showcase deployment stays frozen.


---

## ADR-14: Decision Authority Between DocuBridge and the Calling Agent — Evidence Left, Judgment Right

- **Context:** Five decisions recur in every exchange: (D1) is this document a valid instance of its type; (D2) does the set on hand satisfy the declared requirement; (D3) what to ask for next, in what order, under which cross-document constraints; (D4) may a required document be waived for this case; (D5) the business judgment itself (approve the loan, bind the quote). D1 has always been DocuBridge's and D5 the calling agent's. D2 and D3 lived in calling-agent code with DocuBridge offering only a prose-derived proposal, which meant every new document set required a new agent and nothing about sufficiency could be learned. D4 had drifted into DocuBridge without a decision (reviewer waivers in the action inbox; a promoted rule auto-waiving on the next case). The repeated question "how much decision can move left into DocuBridge" could not be answered as one dial.
- **Decision:** The dividing line is **evidence versus judgment**. DocuBridge decides everything that is derivable from *the documents plus a declared requirement*; the calling agent decides everything that needs *context the documents do not contain*.
  1. **D2 — sufficiency is a DocuBridge verdict, not a proposal.** DocuBridge evaluates a **declared strategy** deterministically and returns `sufficiency = {satisfied, strategy_revision, evidence[], outstanding[]}` on every turn. The verdict names the rule and the documents that satisfied or failed it. No model participates in the verdict. The calling agent **still mints "done"**: it may accept the verdict in one line or override it with its own logic; `done` never originates in DocuBridge.
  2. **D3 — the strategy is authored in the process skill, overridable per request.** Ordering ("ask for the ID first"), counts, cross-document constraints (distinct issuer, recency, same borrower) and alternatives are declared as data in `skills/<process>/assets/strategy.yaml`, referencing ontology fields by name. The calling agent may add or waive requirements per request through `CollectRequest.requirements`; a request-level requirement always wins over the skill's default. This supersedes the "never a rule language" wording in `design/skills.md` and `design/collect.md`: the strategy is declarative data evaluated by code, not a workflow DSL and not prose read by a model. The prose `bridge-satisfaction` becomes generated documentation of the strategy, not its source.
  3. **D4 — waivers belong to the calling agent.** DocuBridge never changes a requirement's state to `waived` on its own authority. A reviewer in the action inbox may **recommend** a waiver, with citation and reasoning; the recommendation travels in `sufficiency.evidence[]` and `outstanding[]` to the calling agent, which alone waives (by sending the item back as `waived`). The Day-2 "zero-touch" effect is therefore: DocuBridge presents the recommendation already formed and evidenced, and the calling agent's acceptance is the one remaining act.
  4. **D5 — unchanged.** Business judgment never enters DocuBridge; no strategy rule may encode a product or risk policy (an LTV ceiling, an approval), only document-set sufficiency.
  5. **Scope of Calibration autonomy — deferred.** Whether a promoted rule may change what counts as sufficient without a human on the next case is left open; until decided, promoted rules change sense-A thresholds, extraction hints and *recommendations* only.
- **Consequence:** Sense A / sense B stays intact and gains a middle row: DocuBridge asserts facts (A), DocuBridge concludes sufficiency against a declared strategy (A′, evidence), the calling agent decides (B, judgment). A new document set is a strategy file plus skill and ontology bundles — no new agent. Chasing is targeted from the first turn. Sufficiency becomes learnable: a correction compiles into a strategy diff that is backtested and versioned, instead of prose. The inbox waiver and the Day-2 auto-waiver beat are demoted to recommendations (`P9-4`). The reference consumer (`agents/address`) shrinks to accepting or overriding the verdict. The actors table (`design/actors.md`) records the ownership; `design/collect.md` and `design/skills.md` carry the amended wording.
