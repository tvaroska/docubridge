# Financial Services & Regulated Industry Showcase Guide

**DocuBridge** ships with three pre-seeded, multi-document enterprise workflows spanning **Commercial Banking & Real Estate Lending**, **Residential Mortgage Origination (TRID / Fannie Mae)**, and **Commercial & Sovereign Property Insurance Placement**.

Each domain exercises the full 6-zone architecture and the 4 Gemini platform pillars (**Build, Govern, Scale, Optimize**):
1. **Zone 1 — Dual-Path Intake & A2A Edge:** Path A structured JSON/Excel schema validation (`\$0` OCR/LLM cost) and Path B multi-page PDF ingestion via Document AI OCR + `gemini-3.8-flash` extraction (`bridge/collect.py`, `bridge/extraction/`).
2. **Zone 2 — Edge PII Redaction & Draft Quarantine:** Inline Cloud DLP / regex PII scrubbing (`redact_personal_data`) and quarantine staging (`GcsArtifactService`) with a deterministic 4-Signal Disposition Gate (`bridge/signals.py`).
3. **Zone 3 — 7-Year WORM Audit Vault & Bi-Temporal Operational Store:** Immutable SHA-256 evidence locking in Cloud Storage (`bridge/vault.py`) paired with append-only bi-temporal `Decimal` consolidation in Cloud SQL for PostgreSQL 16 / AlloyDB (`bridge/operational_store/`) and watermarked BigQuery Open Iceberg CDC sync.
4. **Zone 4 — Role-Based Views & Multi-Persona Projections:** Deterministic source-priority resolution (`endorsement`/`addendum`/`closing-disclosure` `40` > `binder`/`credit-agreement` `30` > `quote`/`term-sheet`/`appraisal` `20` > `submission`/`loan-application` `10`) serving Broker, Claims, and Underwriter views with click-to-source-page bounding boxes.
5. **Zone 5 — Declarative A2UI v0.9 & Human-in-the-Loop Action Inbox:** Security-zone-isolated surfaces (`gemini_app` internal vs. `custom_web` external) with compound SPIFFE JWT-SVID + OBO principal authorization (`bridge/identity.py`) and RFC 6902 JSON Patch audit trails (`bridge/a2ui_inbox/`).
6. **Zone 6 — Knowledge Catalog, Agent Registry, Agent Gateway & Agent Gym:** 6-hop W3C PROV-O lineage (`bridge/knowledge_catalog.py`), Gemini platform Agent & Skill Registry (`agentregistry.googleapis.com`), Agent Gateway (`CLIENT_TO_AGENT` Model Armor/DLP + `AGENT_TO_ANYWHERE` IAM v3 UAP CEL), and closed-loop **Agent Gym** (`bridge/agent_gym/`) with Two-Sided Oversight (`RSR`), 3-Tier evaluation (`gemini-4-argon` judge), and 1-click `SKILL.md` compilation.

---

## 1. Commercial Banking — CRE Term Loan Underwriting (`banking`)

### Regulatory Context
- **Frameworks:** OCC Comptroller's Handbook (*Commercial Real Estate Lending*), Interagency Appraisal and Evaluation Guidelines (12 CFR Part 34), and FinCEN CDD Rule (31 CFR 1010.230).
- **Consolidation Hierarchy:** Negotiated Fee Addendum (`40`) > Credit Agreement / Commitment Letter (`30`) > Term Sheet / MAI Appraisal (`20`) > Initial Loan Application (`10`).

### Seeded Showcase Entity (`bridge/user_interaction/demo_seed.py`)
| Entity ID | Borrower / Facility | Facility Size | Scenario Highlights |
| :--- | :--- | :--- | :--- |
| `LOAN-MERIDIAN-001` | **Meridian Logistics Center LLC** | `\$45,000,000` | Multi-document CRE Term Loan consolidating Executed Term Sheet & MAI Appraisal (`doc-meridian-ts-v1` at `\$62,500,000` appraisal / `72.00% LTV` and `\$50,000.00` base origination fee) and Schedule B Closing Fee Addendum (`doc-meridian-addendum-v2` overriding `origination_fee` from `\$50,000.00` to `\$100,000.00` on Page 4). |

### Key Canonical Facts, Glossary Terms & Trust Slices
- **Canonical Facts:** `loan_amount` (`45000000.00`), `appraised_value` (`62500000.00`), `net_operating_income` (`4185000.00`), `origination_fee` (`50000.00` → `100000.00`).
- **Glossary Terms:** `term.loan_to_value_ratio` (Loan-to-Value Ratio), `term.addendum_priority_hierarchy` (Banking & Insurance Document Priority Hierarchy), `term.cross_document_conflict`.
- **Programmable Trust Slices:** `slice:closing-disclosure:fee-schedule:LOW` (`trusted_auto_approve`), `slice:credit-agreement:addendum:MEDIUM` (`guided_review`), `slice:appraisal-report:commercial:HIGH` (`shadow_mode`).

---

## 2. Residential Mortgage — TRID Closing & Jumbo Underwriting (`mortgage`)

### Regulatory Context
- **Frameworks:** CFPB TILA-RESPA Integrated Disclosure (TRID / Regulation Z, 12 CFR 1026.19(e)–(f)) and Fannie Mae Selling Guide B3-3.1.
- **Consolidation Hierarchy:** TRID Closing Disclosure Addendum (`40`) > Commitment Letter (`30`) > Form 1004 Appraisal (`20`) > Form 1003 URLA Application (`10`).

### Seeded Showcase Entity (`bridge/user_interaction/demo_seed.py`)
| Entity ID | Borrower / File Name | Loan Amount | Scenario Highlights |
| :--- | :--- | :--- | :--- |
| `MORTGAGE-WESTBROOK-2026` | **Westbrook Family Trust (30-Year Jumbo)** | `\$1,850,000` | Combines Path A structured Form 1003 URLA + Form 1004 Appraisal (`doc-westbrook-le-v1` at `\$2,400,000` / `77.08% LTV` and `\$4,250.00` initial origination fee) and Path B TRID Closing Disclosure Page 3 Section A rate-lock extension addendum (`doc-westbrook-cd-v2`, updating `origination_fee` from `\$4,250.00` to `\$8,500.00`). |

### Key Canonical Facts, Glossary Terms & Trust Slices
- **Canonical Facts:** `loan_amount` (`1850000.00`), `appraised_value` (`2400000.00`), `origination_fee` (`4250.00` → `8500.00`).
- **Glossary Terms:** `term.loan_to_value_ratio`, `term.addendum_priority_hierarchy`, `term.cross_document_conflict`.
- **Programmable Trust Slice:** `slice:mortgage-trid:closing-disclosure:MEDIUM` (`guided_review`).

---

## 3. Commercial Insurance — Sovereign & Catastrophe Property Placement (`insurance`)

### Regulatory Context
- **Frameworks:** NAIC Model Laws, Surplus Lines Stamping Office filings, and Sovereign / Catastrophe Deductible Endorsement schedules.
- **Consolidation Hierarchy:** Signed Endorsement (`40`) > Bound Policy / Binder (`30`) > Carrier Quote (`20`) > Schedule of Values (SOV) Submission (`10`).

### Seeded Showcase Entity (`bridge/user_interaction/demo_seed.py`)
| Entity ID | Account / Placement Name | Total Insured Value | Scenario Highlights |
| :--- | :--- | :--- | :--- |
| `terrepower-coastal-2027` | **Terrepower Coastal LLC** | `\$142,500,000` | Consolidates Carrier Property Quote (`doc-terrepower-quote-v1` with `\$50,000,000` coverage limit, `\$418,500.00` annual premium, and `\$50,000.00` base deductible) and Sovereign Wind/Hail Deductible Endorsement (`doc-terrepower-endorsement-v2` overriding `deductible` from `\$50,000.00` to `\$100,000.00` on Page 4). |

### Key Canonical Facts, Glossary Terms & Trust Slices
- **Canonical Facts:** `total_insured_value` (`142500000.00`), `coverage_limit` (`50000000.00`), `premium` (`418500.00`), `deductible` (`50000.00` → `100000.00`).
- **Glossary Terms:** `term.premium`, `term.deductible_to_limit_ratio`, `term.active_coverage_as_of_loss`.
- **Programmable Trust Slices:** `slice:chubb:quote:LOW` (`trusted_auto_approve`), `slice:aig:endorsement:MEDIUM` (`guided_review`), `slice:lloyds:quote:HIGH` (`shadow_mode`).

---

## Running the Interactive 3-Domain Showcase

```bash
# Start the unified dashboard & 6-Zone Enterprise Console locally
.venv/bin/uvicorn bridge.dashboard.server:build_dashboard_app --factory --port 8080

# Seed the 3 showcase domains (Banking, Mortgage, Insurance)
curl -s -X POST http://localhost:8080/v1/demo/seed | jq .

# Open the interactive 5-Scene Enterprise Console in your browser
open http://localhost:8080/demo
```

### API Endpoints for Domain Exploration (`bridge/user_interaction/routes.py`)
- `POST /v1/demo/seed` — Seed baseline bi-temporal facts, WORM vault records, and 6-hop lineage across `LOAN-MERIDIAN-001`, `MORTGAGE-WESTBROOK-2026`, and `terrepower-coastal-2027`.
- `POST /v1/intake/ingest` — Ingest a Path A structured payload or Path B document with `SourceAnchor` page/polygon provenance.
- `GET /v1/a2ui/gemini_app/LOAN-MERIDIAN-001?role=underwriter` — Render the declarative A2UI v0.9 surface with bi-temporal time-travel (`?as_of=...`) and click-to-source-page proof.
- `GET /v1/a2ui/inbox/items` — Retrieve pending human-in-the-loop A2UI v0.9 action items and cryptographic RFC 6902 JSON Patch audit records.
- `GET /v1/catalog/glossary` & `GET /v1/catalog/lineage/LOAN-MERIDIAN-001/origination_fee` — Inspect business glossary terms, SPIFFE agents, and 6-hop W3C PROV-O field lineage.
- `GET /v1/registry/catalog`, `GET /v1/registry/lineage/LOAN-MERIDIAN-001`, & `GET /v1/gateway/status` — Inspect the Gemini platform Agent & Skill Registry, process-instance skill lineage, and Agent Gateway (`CLIENT_TO_AGENT` + `AGENT_TO_ANYWHERE` UAP CEL).
- `GET /v1/gym/rules` & `GET /v1/gym/trust-policies` — View distilled Tribal Knowledge Rules, 3-Tier evaluation report cards, and Programmable Trust autonomy slices.
- `GET /v1/gym/export/docai-dataset` — Export verified SME feedback signals (`ExpertFeedbackSignal` + `SourceAnchor` bounding polygons) as a ready-to-import Document AI `ImportDocuments` Custom Extractor uptraining dataset.
- `GET /v1/telemetry/analytics` & `GET /v1/telemetry/sql-eval` — Query 22-step OpenTelemetry `gen_ai.*` trace analytics and the 5-gate BigQuery `Rule 0` SQL evaluation + Rogan-Gladen debiased pass rate.

---

## 4. FSI Tech Risk & CISO Governance Matrix (GA Terraform vs. Control-Plane Emulators)

For regulated financial institutions (e.g., Tier-1 banks with strict **GA-only** production gates under SR 11-7 / OCC Model Risk Management), the table below distinguishes between **GA GCP Infrastructure Provisioned via Terraform** (`terraform/modules/*`) and **Application-Enforced Gemini Platform Control Planes** (`bridge/*`):

| Control Plane / Zone | GCP / Gemini Platform Capability | Launch Tier | Realization in DocuBridge (`terraform/` & `bridge/`) |
| :--- | :--- | :--- | :--- |
| **Zone 1 · Edge & Network Isolation** | Cloud Run v2 + External HTTPS LB + Cloud Armor WAF (`edge_waf`) + 2-Subnet VPC (`internal`/`external`) | **GA** | Provisioned in [`terraform/modules/gateway/`](terraform/modules/gateway/main.tf) and [`terraform/modules/network/`](terraform/modules/network/main.tf). |
| **Zone 2 · Extraction & OCR** | Gemini (`gemini-3.8-flash` workhorse, `gemini-4-argon` judge) + Document AI OCR / Custom Extractor | **GA** | Implemented in [`bridge/extraction/gemini.py`](bridge/extraction/gemini.py) and [`bridge/extraction/docai.py`](bridge/extraction/docai.py). |
| **Zone 3 · WORM Evidence Vault** | Cloud Storage Retention Policy Lock (`7-year WORM`) + `if_generation_match=0` | **GA** | Provisioned in [`terraform/modules/gcs/`](terraform/modules/gcs/main.tf) (`enable_audit_vault`, `lock_audit_vault_retention`) + [`bridge/vault.py`](bridge/vault.py). |
| **Zone 3/4 · Bi-Temporal Store & Lakehouse** | Cloud SQL for PostgreSQL 16 (Private IP + `cloudsql.iam_authentication = "on"`) + BigQuery Open Iceberg | **GA** | Provisioned in [`terraform/modules/database/`](terraform/modules/database/main.tf) and [`terraform/modules/telemetry/`](terraform/modules/telemetry/main.tf) + [`bridge/operational_store/`](bridge/operational_store/__init__.py). |
| **Zone 5 · HITL Feedback Persistence** | Cloud SQL Postgres (`SqlActionInboxStore`, `SqlAgentGymEngine`) vs. 1P `FeedbackService` API | **GA (Cloud SQL)** / *Preview (1P API)* | Strict-GA banks persist SME overrides & RFC 6902 patches in GA Cloud SQL + BigQuery (`gym_feedback_signals`, `action_inbox_audits`). |
| **Zone 6 · Agent Runtime & Sessions** | Agent Engine (`ReasoningEngine`, `VertexAiSessionService`, `VertexAiMemoryBankService`) with PSC | **GA** | Implemented in [`bridge/edge/agent_engine_a2a.py`](bridge/edge/agent_engine_a2a.py) and [`bridge/ports/gcp/`](bridge/ports/gcp/__init__.py). |
| **Zone 6 · Agent Identity & OBO** | SPIFFE JWT-SVID (`RS256`/`ES256`) + Google OIDC + Secret Manager (`bridge-spiffe-svid-secret`) | **GA** | Provisioned in [`terraform/modules/secrets/`](terraform/modules/secrets/main.tf) and enforced in [`bridge/identity.py`](bridge/identity.py). |
| **Zone 6 · Agent & Skill Registry + Gateway CEL** | Agent Registry (`agentregistry.googleapis.com`), Skill Registry (`SkillRevision`), Agent Gateway (`CLIENT_TO_AGENT` Model Armor + `AGENT_TO_ANYWHERE` IAM v3 UAP CEL) | **GA (Agents/MCP/Gateway/CEL)** / *Preview (Skill Registry)* | Enforced in [`bridge/agent_gym/registry_gateway.py`](bridge/agent_gym/registry_gateway.py) with optional live GCP Model Armor REST inspection (`BRIDGE_MODEL_ARMOR_TEMPLATE`) and SQL persistence (`gym_skill_revisions`, `gym_registry_services`, `gym_registry_bindings`). |
| **Observability & 3-Tier SQL Eval** | Cloud Trace v2 (`traces:batchWrite`), Cloud Logging, BigQuery `otel_spans` + `v_trace_sql_evaluation` | **GA** | Provisioned in [`terraform/modules/telemetry/`](terraform/modules/telemetry/main.tf) and [`bridge/telemetry/`](bridge/telemetry/__init__.py). |
