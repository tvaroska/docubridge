---
type: concept
down:
  - "ports.md"
  - "zones.md"
  - "proactive.md"
  - "party-memory.md"
related:
  - "bridge.md"
tags: [bridge]
---

# GCP Substrate

> The half that makes the Bridge a **showcase of the Gemini Enterprise Agent Platform**, not a local prototype: the managed services, the trust boundary, and the deploy path are part of what's demonstrated — and part of what a servicer would actually run in production.

The Bridge showcases the Gemini Enterprise Agent Platform — Agent Runtime, Skill Registry, Agent Gateway, Agent Identity, A2A, A2UI — as its substrate. These ideas keep development fast while everything still runs on the real managed platform it showcases:

- **[Managed-service ports](ports.md)** — every managed boundary has a **local adapter** (fast dev/test) and a **GCP adapter** (deployed), designed together and tested against the same suite — develop locally at speed, run deployed on managed services unchanged.
- **[Two-zone network model](zones.md)** — external provider agents ↔ Bridge (DMZ) ↔ internal backend agents, isolated network-only via Agent Gateway + Agent Identity. A production-grade trust boundary, shown end to end.
- **[Proactive follow-up](proactive.md)** — a durable Scheduler/Timer port (Cloud Tasks) drives reminders / SLA / escalation, the concrete proof of "mediation, not extraction."
- **[Party memory](party-memory.md)** — a reserved port for cross-exchange counterparty memory (advisory-only); it declares no methods, so nothing reads or writes party memory.

**Deploy.** A single deploy step packages the Bridge, provisions the runtime, and builds validated Gateway/Identity deploy specs. Every demo — including the Address warm-up — runs on this deployed path.

## Terraform: substrate primitives, not one-module-per-service

Terraform is organized **by GCP substrate primitive — what Terraform actually provisions — never one-module-per-platform-service.** The platform-native services are consumed through the ports (their GCP adapters are config + IAM + client code), which is exactly why none of them appears as a same-named Terraform module. The module set reads as generic Cloud Run + VPC + Cloud SQL + Cloud Tasks + Secret Manager + LB on purpose: **Terraform provisions substrate; the ports provision services.**

The module set: `network` (two subnets modelling the two trust zones + firewall isolation), `runtime` (Cloud Run v2, VPC egress, ingress computed from `enable_gateway`), `gateway` (toggleable external HTTPS LB → Serverless NEG), `iam` (the `bridge-runtime` workload SA + `cloudsql.client` / `cloudtasks.enqueuer` / `aiplatform.user` grants + the Cloud Tasks token-creator for OIDC callbacks), `database` (Cloud SQL Postgres 16 on private IP via Private Services Access), `tasks` (the `bridge-followups` queue), `secrets` (Secret Manager; the DB DSN rides a `DATABASE_URL` secret env, never plaintext), and `frontend` (the deployed surfaces). The `gcs` module homes the Skill Registry bucket and the Artifacts bucket. [Email intake](email-intake.md) is a delta across these modules, gated on `enable_email_ingress` (off by default): a webhook-only Cloud Run service in `runtime` running as its own create-only SA from `iam`, three Secret Manager containers in `secrets` (values added out-of-band), and a lifecycle-retained landing bucket in `gcs`. The landing bucket is drained by the RFP board host: a second internal-only Cloud Run service in `runtime` (gated on `enable_rfp_board`) running as `bridge-runtime`, whose `POST /email/drain` a Cloud Scheduler job calls with an OIDC token.

**`runtime` = Cloud Run v2 for the HTTP/webhook/REST edge + Gemini platform Agent Engine for managed Sessions, Memory Bank, ADK `App` execution (Option A), and the A2A Server (Option B).** Cloud Run serves the thin HTTP edge — custom HTTP routes outside the A2A JSON-RPC protocol (the A2UI portal endpoints, the scoped artifact-fetch endpoint `/artifacts/{context_id}/{name}`, the M365 Graph email intake webhook, the RFP board REST API, and the Address wake receiver `/wake`) and, when `BRIDGE_AGENT_ENGINE_A2A=1`, proxies `GET /.well-known/agent-card.json` and `POST /` (unary and SSE streaming A2A JSON-RPC 2.0) to `BridgeA2aAgentEngine` (`bridge/edge/agent_engine_a2a.py`) on Gemini platform Agent Engine. Gemini platform Agent Engine (`aiplatform.googleapis.com` `ReasoningEngine`) hosts the managed ADK `VertexAiSessionService`, `VertexAiMemoryBankService`, remote `document_bridge` / `address` ADK `App` execution (`BRIDGE_AGENT_ENGINE_ID`, `BRIDGE_AGENT_ENGINE_REMOTE=1`), and the A2A server runtime (`CollectExecutor` + `EdgeRequestHandler` + dynamic `AgentCard` via `BRIDGE_AGENT_ENGINE_A2A=1`), with a Private Service Connect network attachment (`enable_agent_engine_psc`) on the internal subnet.

**Durable state = Gemini platform Agent Engine (Sessions + Memory Bank) + Cloud SQL Postgres (Task store + Exchange store).** Behind the ports sit ADK `VertexAiSessionService` when `BRIDGE_AGENT_ENGINE_ID` is set (or `DatabaseSessionService` on `DATABASE_URL` when unset), ADK `VertexAiMemoryBankService` behind `Port.memory_bank`, a2a-sdk `DatabaseTaskStore` + push-config store (tasks), and the Bridge's `SqlExchangeStore` (exchange records) on Cloud SQL Postgres.

Platform service → realization → Terraform home:

| Platform service | Realized as | Terraform home |
|---|---|---|
| **Agent Runtime** | ADK `App` (`document_bridge` / `address`) and `BridgeA2aAgentEngine` (`CollectExecutor` + `EdgeRequestHandler` + dynamic `AgentCard`) on Gemini platform Agent Engine (`BRIDGE_AGENT_ENGINE_ID`, `BRIDGE_AGENT_ENGINE_REMOTE=1`, `BRIDGE_AGENT_ENGINE_A2A=1`) fronted by the Cloud Run v2 HTTP/A2A/A2UI/webhook edge | `runtime` (Cloud Run v2) + `network` (`enable_agent_engine_psc` PSC network attachment) |
| **Sessions / task store / exchange store** | ADK `VertexAiSessionService` when `BRIDGE_AGENT_ENGINE_ID` is set (or `DatabaseSessionService` on Cloud SQL Postgres when unset); a2a-sdk `DatabaseTaskStore` and Bridge `SqlExchangeStore` on Cloud SQL Postgres (`GcpSessions` / `GcpTaskStore` / `GcpExchangeStore`; asyncpg DSN from the `DATABASE_URL` secret; in-memory refused) | `database` + `secrets` + `iam` (`cloudsql.client`, `aiplatform.user`) |
| **Memory Bank** | ADK `BaseMemoryService` via `Port.memory_bank`: `InMemoryMemoryService` / `SqliteMemoryService` (`BRIDGE_MEMORY_DB_PATH`) locally; `VertexAiMemoryBankService` (`BRIDGE_AGENT_ENGINE_ID`) on GCP (`GcpMemoryBank`) | `iam` (`aiplatform.user`) + config (`BRIDGE_AGENT_ENGINE_ID`) |
| **Skill Registry** | GCS bucket + the Skill-registry port GCP adapter | `gcs` (versioned bucket) |
| **Artifacts** (documents) | GCS bucket `<prefix>-artifacts` behind ADK `GcsArtifactService`, selected by `BRIDGE_ARTIFACT_BUCKET` (a native backend swap, not a port); versioned — a resubmission is a new version; lifecycle Delete after `artifact_retention_days` (default 365), archived versions included; never public — bytes leave only by a signed URL minted after the leg-scope check ([artifacts](artifacts.md)) | `gcs` (per-bucket `storage.objectAdmin` to `bridge-runtime`) |
| **Agent Gateway** | external HTTPS LB → Serverless NEG + Cloud Armor edge WAF/rate-limiting (`edge_waf`) + per-party scoping | `gateway` (toggleable via `enable_gateway`) |
| **Agent Identity & SPIFFE SVIDs** | `bridge-runtime` workload SA + OIDC + per-party `run.invoker` + Secret Manager `bridge-spiffe-svid-secret` (`BRIDGE_SPIFFE_SVID_SECRET`) for asymmetric `RS256`/`ES256` or `HS256` SPIFFE JWT-SVIDs | `iam` + `secrets` (`spiffe_svid`) + `runtime` |
| **Zone 3 Audit Vault** (`audit_vault`) | 7-year retention-locked Cloud Storage WORM bucket (`GcpAuditVaultStore`, `BRIDGE_VAULT_BUCKET`) | `gcs` (`enable_audit_vault`, `vault_retention_days`, `lock_audit_vault_retention`) |
| **Zone 4–6 Enterprise Stores** (`operational_store`, `action_inbox`, `agent_gym`) | `SqlOperationalStore`, `SqlActionInboxStore`, and `SqlAgentGymEngine` multiplexing over the shared `DsnSqlRuntime` pool on Cloud SQL Postgres (`DATABASE_URL` + optional IAM auth `BRIDGE_DB_IAM_AUTH=1`), plus BigQuery Lakehouse table `docubridge_lakehouse.bq_operational_facts_iceberg` | `database` + `secrets` + `telemetry` (`lakehouse` dataset + `operational_facts_iceberg` table) |
| **Extraction** (Gemini / Document AI) | Gemini platform, plus the client's own Document AI processor as an OCR [pre-pass](docai-prepass.md), via the Extraction port | `iam` (`aiplatform.user`; `documentai.apiUser` on the processor's project, cross-project) + config (`BRIDGE_EXTRACTION_ENGINE`, `BRIDGE_DOCAI_PROCESSOR`) |
| **Email intake** (M365 Graph) | a public, webhook-only Cloud Run service (clientState-authenticated, the one `allUsers` invoker) feeding the Email-ingress port's create-only GCS landing bucket, drained by the internal RFP board host (Cloud Scheduler → `POST /email/drain`, OIDC); the app reads one dedicated mailbox | `runtime` + `iam` (`bridge-email-webhook`, no project roles) + `secrets` + `gcs`, gated on `enable_email_ingress`; the board host + Scheduler job in `runtime`, gated on `enable_rfp_board` |
| **Observability & Trace Analytics** (Cloud Trace, Cloud Logging, BigQuery) | W3C `traceparent` + `X-Cloud-Trace-Context` propagation (`OtelAsgiMiddleware`), 22 pipeline `trace_step` spans + `gen_ai.*` attributes (`bridge/telemetry.py`), exported simultaneously to in-memory ring buffer (`/v1/telemetry/spans`, `/v1/telemetry/analytics`), Cloud Trace v2 (`traces:batchWrite`), structured Cloud Logging JSON (`logging.googleapis.com/trace`), and BigQuery streaming (`tabledata.insertAll` into `docubridge_telemetry.otel_spans` + SQL views `v_step_latency_analytics`, `v_trace_waterfall`, `v_entity_pipeline_summary`) | `telemetry` (`enable_telemetry_bq`, dataset + partitioned/clustered table + 3 SQL views + Cloud Logging sink + dataset `bigquery.dataEditor`) + `runtime` (`BRIDGE_TELEMETRY_BQ_TABLE`) + `iam` (`extra_roles`: `roles/cloudtrace.agent`, `roles/bigquery.jobUser`) |

**Deletion protection.** The root `deletion_protection` variable (default `true`) guards the Cloud SQL instance and the runtime and RFP-board Cloud Run services; the demo deploy script sets it `false`. The buckets carry none — their retention is the lifecycle rules.

Adding a platform service in a later release grows a port adapter + a small Terraform delta, not a restructured module set — substrate-oriented modules are the stable base those deltas attach to. Validation is credential-free plan-time tests (`terraform test` + `mock_provider`); the deployed-path parity run is separate, against a provisioned project.

## Read next
- [ports](ports.md) — the local + GCP adapter table
- [network zones](zones.md) — Gateway ingress + Agent Identity
- [proactive follow-up](proactive.md) — the Scheduler/Timer pillar
- [party memory](party-memory.md) — the reserved Memory Bank port (interface intent)

## Related
- [the Bridge](bridge.md)
- [deploying the resumable Collect loop](deploy-resume.md) — restore vs. wake on Cloud Run / Agent Engine
