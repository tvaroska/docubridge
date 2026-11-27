# Architecture Decision Records (ADR)

Foundational architectural decisions (`S0-arch-*`, `P7-*`, and `ADR-01` through `ADR-16`) shaping **DocuBridge** (`google-adk >= 2.11.0, < 3`, `a2a-sdk`, `gemini-3.8-flash` workhorse, and `gemini-4-argon` teacher/judge/escalation model). Full historical prose for `ADR-01`–`ADR-12` lives in [docs/archive/decisions-history.md](docs/archive/decisions-history.md).

---

## ADR-01: Hexagonal Ports & Adapters Across All 14 Boundaries (`local` vs. `gcp`) (`S0-arch-13`, `P7-2`–`P7-5`, `P9-4`)

- **Decision:** Factor all 14 external boundaries (`sessions`, `task_store`, `exchange_store`, `skill_registry`, `memory_bank`, `scheduler`, `a2a_client`, `extraction`, `email_ingress`, `audit_vault`, `operational_store`, `action_inbox`, `agent_gym`, `knowledge_catalog`) behind **Ports, Adapters and Contracts** in `bridge/ports/{local,gcp}/`, failing fast when `DATABASE_URL` / bucket env vars are unset in `gcp` mode — [archive](docs/archive/decisions-history.md#adr-01---hexagonal-ports-adapters-and-contracts-across-all-14-boundaries-local-vs-gcp-s0-arch-13-p7-2p7-5-p9-4).

---

## ADR-02: Hybrid Deterministic 4-Signal Gate + ADK `LlmAgent` (`2026-08-24`, `S0-arch-17`)

- **Decision:** Enforce objective quality, freshness, completeness, and corroboration thresholds in deterministic Python (`dispose_signals`, `Rule 0`) before any ADK `LlmAgent` (`gemini-3.8-flash` / `gemini-4-argon`) follow-up or `request_human_review` park; models never mint `accepted` dispositions (`tests/test_edge_gate_inversion.py`) — [archive](docs/archive/decisions-history.md#adr-02---hybrid-deterministic-4-signal-gate--adk-llmagent-2026-08-24-s0-arch-17).

---

## ADR-03: Strict Two-Way Package Isolation (`bridge/` ↔ `contract/` ↔ `agents/`)

- **Decision:** Enforce an AST-verified import firewall (`specs/ast.rules.yaml`, `tests/test_isolation_guard.py`) so `bridge/` and consumer/carrier `agents/` share types exclusively through `contract/` over canonical A2A wire messages — [archive](docs/archive/decisions-history.md#adr-03---strict-two-way-package-isolation-bridge--contract--agents).

---

## ADR-04: Declarative Skill Packages & Zero Hardcoded Skill Names (`test_core_names_no_skill`)

- **Decision:** Package doctypes and processes as runtime folders under `skills/` (`SKILL.md`, `assets/schema.json`, `assets/policy.yaml`, `assets/validation.yaml`), guarded by `test_core_names_no_skill` so `bridge/` core contains zero hardcoded skill names — [archive](docs/archive/decisions-history.md#adr-04---declarative-skill-packages--zero-hardcoded-skill-names-test_core_names_no_skill).

---

## ADR-05: Dual-Path Intake & Document AI + Gemini Extraction with `SourceAnchor` Provenance (`2026-08-24`)

- **Decision:** Validate structured Path-A assertions against JSON Schema at `\$0` LLM cost (`bridge/patha.py`), and route Path-B documents through optional Document AI OCR (`bridge/extraction/docai.py`) + `gemini-3.8-flash` (`bridge/extraction/gemini.py`) with page/polygon `SourceAnchor` provenance — [archive](docs/archive/decisions-history.md#adr-05---dual-path-intake--document-ai--gemini-extraction-with-sourceanchor-provenance-2026-08-24).

---

## ADR-06: 7-Year WORM Cloud Storage Audit Vault & Bi-Temporal SQL Operational Store (`S0-arch-14`, `P7-2`)

- **Decision:** Redact edge PII and lock submitted bytes in 7-year WORM Cloud Storage (`bridge/vault.py`), paired with append-only `Decimal` bi-temporal facts (`recorded_at`, `effective_from`) in PostgreSQL 16 / AlloyDB (`SqlOperationalStore`) and BigQuery Iceberg CDC export — [archive](docs/archive/decisions-history.md#adr-06---7-year-worm-cloud-storage-audit-vault--bi-temporal-sql-operational-store-s0-arch-14-p7-2).

---

## ADR-07: Zone 6 Knowledge Catalog & 6-Hop W3C PROV-O Lineage

- **Decision:** Govern glossary terms, zone assets, SPIFFE workload policies, and stateless 6-hop PROV-O lineage (`VaultObject` → `ExtractionRun` → `QuarantineGate` → `ConsolidationRule` → `OperationalFact` → `A2UISurface`) in `bridge/knowledge_catalog/` — [archive](docs/archive/decisions-history.md#adr-07---zone-6-knowledge-catalog--6-hop-w3c-prov-o-lineage).

---

## ADR-08: SPIFFE JWT-SVID & OBO Compound Identity (`Agent ∩ User`) (`P7-5`)

- **Decision:** Compute effective authority as `Agent(SPIFFE) ∩ User(Principal)` (`bridge/identity.py`, `bridge/user_interaction/auth.py`), enforcing Shadow AI blocking, exact tenant isolation, and `Decimal` per-role approval ceilings — [archive](docs/archive/decisions-history.md#adr-08---spiffe-jwt-svid--obo-compound-identity-agent--user-p7-5).

---

## ADR-09: Declarative A2UI v0.9 Surfaces & Security-Zone-Isolated Action Inbox

- **Decision:** Isolate internal (`gemini_app`) vs. external (`custom_web`) A2UI v0.9 widget catalogs in `bridge/a2ui_inbox/` (`SqlActionInboxStore`) with ADK `request_confirmation` pause/resume and immutable RFC 6902 JSON Patch audit records — [archive](docs/archive/decisions-history.md#adr-09---declarative-a2ui-v09-surfaces--security-zone-isolated-action-inbox).

---

## ADR-10: Closed-Loop Agent Gym, Two-Sided Oversight (`RSR`), & Gemini Platform Registry/Gateway

- **Decision:** Gate SME correction distillation on Two-Sided Oversight (`oversight_rsr_score >= 0.80`) and a 3-Tier held-out evaluation gate before compiling `SkillRevisionRecord`s via `gemini-4-argon`, governed alongside Agent Registry & Gateway policies (`bridge/agent_gym/`) — [archive](docs/archive/decisions-history.md#adr-10---closed-loop-agent-gym-two-sided-oversight-rsr--gemini-platform-registrygateway).

---

## ADR-11: AST Service-Wiring Guard & Shared DSN SQL Runtime (`2026-09-07`, `P7-2`, `P7-4`, `P7-9`)

- **Decision:** Guard all `InMemory*()` constructors via `tests/test_service_wiring_guard.py` (`2026-09-07`), multiplex all SQL stores over one refcounted `DsnSqlRuntime` (`bridge/sql_runtime.py`), and pipe `DATABASE_URL` directly to Secret Manager in `scripts/deploy.sh` (`P7-4`/`P7-5`/`P7-9`) — [archive](docs/archive/decisions-history.md#adr-11---ast-service-wiring-guard--shared-dsn-sql-runtime-2026-09-07-p7-2-p7-4-p7-9).

---

## ADR-12: 22-Step OpenTelemetry `gen_ai.*` Tracing & BigQuery `Rule 0` SQL Evaluation

- **Decision:** Stream W3C OpenTelemetry spans to Cloud Trace v2 and BigQuery (`bridge/telemetry/`), evaluating 100% of traces with `$0` `Rule 0` SQL gates, sampling ~5% anomalies with `gemini-4-argon` `AI.GENERATE_TABLE`, and debiasing pass rates via Rogan-Gladen — [archive](docs/archive/decisions-history.md#adr-12---22-step-opentelemetry-gen_ai-tracing--bigquery-rule-0-sql-evaluation).

---

## ADR-13: Schema Refinery, OKF Doctype Bundles & Bi-Temporal Ontology Graph (`P8-1`–`P8-4`)

- **Context:** Overloaded monolithic document schemas (e.g., forcing 1-page Schedule B endorsements or fee addenda into full multi-page quotes/disclosures) create bimodal field sparsity ($J(A,B) \le 0.10$) and equal-priority consolidation ties (`Prio 20 == Prio 20`). Bi-temporal ontology revisions (`effective_from` / `effective_to` + `[:SPLIT_FROM]` lineage) allow the taxonomy to split and evolve safely without breaking historical audit reproducibility or violating the `bridge/` ↔ `contract/` ↔ `agents/` isolation firewall.
- **Decision:** Package every document type as a declarative Open Knowledge Format (`catalog/doctypes/*.okf.md`) bundle carrying top-level entity scope, contrastive field triples (`what_it_is`, `where_to_read`, `distinguish_from`), and typed ontology edges (`[:SUPERSEDES]`, `[:FEEDS_METRIC]`, `[:SPLIT_FROM]`, `[:MERGED_INTO]`). Drive Zone 3 (`bridge/operational_store/consolidation_rules.py`) priority resolution and metric rollups directly from the active `OntologyRevision` graph in Zone 6 (`KnowledgeCatalog`), and gate all ontology promotions (`SchemaRefineryEngine`) behind a 3-Tier Counterfactual Assay enforcing $\text{NFR} = 0.0\%$ and, only when perturbation stability is evaluated against a live extractor (raters = catalog permutations, items = document × field), Krippendorff's $\alpha \ge 0.80$ and Gwet's $\text{AC}_2 \ge 0.80$; offline the report states "not evaluated" rather than a figure.
- **Consequence:** New document subtypes (`wind-deductible-endorsement`, `fee-schedule-addendum`) are discovered from production sparsity clusters, verified with zero negative flips before promotion, and deployed on an isolated insurance-first showcase project (`BRIDGE_DEFAULT_DEMO_DOMAIN=insurance`) while the primary banking showcase deployment stays frozen.

---

## ADR-14: Decision Authority Between DocuBridge and the Calling Agent — Evidence Left, Judgment Right

- **Context:** Five decisions recur in every exchange: (D1) is this document a valid instance of its type; (D2) does the set on hand satisfy the declared requirement; (D3) what to ask for next, in what order, under which cross-document constraints; (D4) may a required document be waived for this case; (D5) the business judgment itself (approve the loan, bind the quote). D1 has always been DocuBridge's and D5 the calling agent's. D2 and D3 lived in calling-agent code with DocuBridge offering only a prose-derived proposal, which meant every new document set required a new agent and nothing about sufficiency could be learned. D4 had drifted into DocuBridge without a decision (reviewer waivers in the action inbox; a promoted rule auto-waiving on the next case).
- **Decision:** The dividing line is **evidence versus judgment**. DocuBridge decides everything derivable from *the documents plus a declared requirement*; the calling agent decides everything needing *context the documents do not contain*.
  1. **D2 — sufficiency is a DocuBridge verdict, not a proposal.** DocuBridge evaluates a **declared strategy** deterministically and returns `sufficiency = {satisfied, strategy_revision, evidence[], outstanding[]}` on every turn. No model participates in the verdict. The calling agent **still mints "done"**: `done` never originates in DocuBridge.
  2. **D3 — the strategy is authored in the process skill, overridable per request.** Ordering, counts, cross-document constraints (distinct issuer, recency, same borrower) and alternatives are declared as data in `skills/<process>/assets/strategy.yaml`. The calling agent may add or waive requirements per request through `CollectRequest.requirements`; a request-level requirement always wins over the skill's default.
  3. **D4 — waivers belong to the calling agent.** DocuBridge never changes a requirement's state to `waived` on its own authority. A reviewer in the action inbox may **recommend** a waiver with citation and reasoning; the recommendation travels in `sufficiency.evidence[]` and `outstanding[]` to the calling agent, which alone waives.
  4. **D5 — unchanged.** Business judgment never enters DocuBridge; no strategy rule may encode a product or risk policy (an LTV ceiling, an approval), only document-set sufficiency.
  5. **Scope of Calibration autonomy — resolved by `ADR-15`.** DocuBridge never learns which documents are needed, acceptable, or waivable; it learns only how to extract, derive, and reconcile facts from documents.
- **Consequence:** DocuBridge asserts facts (Sense A), concludes sufficiency against a declared static strategy (Sense A′), and the calling agent decides (Sense B).

---

## ADR-15: Calibration Scope = Fact Extraction Only + `Port.knowledge_catalog` TBox/ABox Graph Split (`P9-4`)

- **Context:** `ADR-14` left open whether Calibration (`SchemaRefineryEngine` + `AgentGymEngine`) may learn document-collection sufficiency rules, while the TBox ontology graph lived only in `InMemoryKnowledgeCatalog`.
- **Decision:**
  1. **Calibration Scope — Fact Extraction Only (Supersedes sufficiency-learning clause of `ADR-14`):** DocuBridge **does not learn anything about which documents are needed, acceptable, or waivable** (`strategy.yaml` and `CollectRequest.requirements` are static/caller-supplied). `SchemaRefineryEngine` + `AgentGymEngine` learn **strictly how to extract, normalize, derive, and reconcile facts from documents**: contrastive field & layout extraction (`EXTRACTION_HINT`), entity granularity & alias normalization (`ExtractedRecord`, `ENTITY_ALIAS`, `[:SPLIT_FROM]`), deterministic Stage-B field derivations (`bridge/extraction/derivations.py` `FieldDerivation`), and cross-document fact precedence (`[:SUPERSEDES]`, `[:REQUIRES_CORROBORATION]`).
  2. **Widened `ExtractionPort` (`bridge/ports/protocols.py`):** `ExtractionPort.extract()` accepts optional keyword-only scope parameters (`target_doctype`, `candidate_doctypes`, `requested_fields`, `context_set`, `ontology_revision`) and returns `ExtractionResult` carrying `fields.records`, `fields.derivations`, `ontology_revision_id`, and `applied_rule_ids`.
  3. **Promote `KnowledgeCatalog` to Port #14 (`Port.knowledge_catalog`) & Two-Tier Graph Storage:** `SqlKnowledgeCatalog` (`bridge/knowledge_catalog/sql_store.py`) persists the TBox ontology graph (`catalog_ontology_revisions`, `catalog_ontology_edges`), catalog assets (`catalog_assets`), and PROV-O lineage (`catalog_lineage_traces`) on `DsnSqlRuntime`, mirrored analytically to BigQuery (`bq_operational_facts_iceberg`, `bq_ontology_revisions`, `bq_ontology_edges`).
- **Consequence:** Calling agents retain 100% ownership of checklist mutations and waivers, while DocuBridge improves fact extraction, deterministic derivations, and multi-replica ontology consistency.

---

## ADR-16: Bi-Temporal Consolidation Precedence (`P13-3`)

- **Context:** Ordering by `(source_priority, effective_from, ...)` let an older endorsement (2024, priority 40) override a newer renewal (2026, priority 30), conflicting with `source.effective_from >= target.effective_from` on ontology `[:SUPERSEDES]` edges.
- **Decision:** Order winners by **`(effective_from, source_priority, recorded_at, version)`** descending in `_winning_key` and SQL views (`ORDER BY effective_from DESC, source_priority DESC, recorded_at DESC, version DESC`). Higher `source_priority` breaks equal-`effective_from` ties. `resolve_active_facts` replaces multi-row tables (`fields.tables.<name>[...]`) per winning document.
- **Consequence:** Renewals beat older endorsements, same-date endorsements still beat base policies, and scalar SQL view precedence matches in-memory consolidation.
