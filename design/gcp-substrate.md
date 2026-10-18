---
type: concept
down:
  - "seams.md"
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

- **[Managed-service seams](seams.md)** — every managed boundary has a **local adapter** (fast dev/test) and a **GCP adapter** (deployed), designed together and tested against the same suite — develop locally at speed, run deployed on managed services unchanged.
- **[Two-zone network model](zones.md)** — external provider agents ↔ Bridge (DMZ) ↔ internal backend agents, isolated network-only via Agent Gateway + Agent Identity. A production-grade trust boundary, shown end to end.
- **[Proactive follow-up](proactive.md)** — a durable Scheduler/Timer seam (Cloud Tasks) drives reminders / SLA / escalation, the concrete proof of "mediation, not extraction."
- **[Party memory](party-memory.md)** — a reserved seam for cross-exchange counterparty memory (advisory-only); it declares no methods, so nothing reads or writes party memory.

**Deploy.** A single deploy step packages the Bridge, provisions the runtime, and builds validated Gateway/Identity deploy specs. Every demo — including the Address warm-up — runs on this deployed path.

## Terraform: substrate primitives, not one-module-per-service

Terraform is organized **by GCP substrate primitive — what Terraform actually provisions — never one-module-per-platform-service.** The platform-native services are consumed through the seams (their GCP adapters are config + IAM + client code), which is exactly why none of them appears as a same-named Terraform module. The module set reads as generic Cloud Run + VPC + Cloud SQL + Cloud Tasks + Secret Manager + LB on purpose: **Terraform provisions substrate; the seams provision services.**

The module set: `network` (two subnets modelling the two trust zones + firewall isolation), `runtime` (Cloud Run v2, VPC egress, ingress computed from `enable_gateway`), `gateway` (toggleable external HTTPS LB → Serverless NEG), `iam` (the `bridge-runtime` workload SA + `cloudsql.client` / `cloudtasks.enqueuer` / `aiplatform.user` grants + the Cloud Tasks token-creator for OIDC callbacks), `database` (Cloud SQL Postgres 16 on private IP via Private Services Access), `tasks` (the `bridge-followups` queue), `secrets` (Secret Manager; the DB DSN rides a `DATABASE_URL` secret env, never plaintext), and `frontend` (the deployed surfaces). The `gcs` module homes the Skill Registry bucket and the Artifacts bucket. [Email intake](email-intake.md) is a delta across these modules, gated on `enable_email_ingress` (off by default): a webhook-only Cloud Run service in `runtime` running as its own create-only SA from `iam`, three Secret Manager containers in `secrets` (values added out-of-band), and a lifecycle-retained landing bucket in `gcs`. The landing bucket is drained by the RFP board host: a second internal-only Cloud Run service in `runtime` (gated on `enable_rfp_board`) running as `bridge-runtime`, whose `POST /email/drain` a Cloud Scheduler job calls with an OIDC token.

**`runtime` = Cloud Run v2 for the HTTP/A2A/webhook/REST edge + Vertex AI Agent Engine for managed Sessions, Memory Bank, and ADK `App` execution (Option A).** Cloud Run remains the thin HTTP edge because the Bridge serves custom HTTP routes outside the A2A JSON-RPC protocol — the A2UI portal endpoints, the scoped artifact-fetch endpoint (`/artifacts/{context_id}/{name}`), the M365 Graph email intake webhook, the RFP board REST API, and the Address wake receiver (`/wake`). Vertex AI Agent Engine (`aiplatform.googleapis.com` `ReasoningEngine`) hosts the managed ADK `VertexAiSessionService`, `VertexAiMemoryBankService`, and optional remote `document_bridge` / `address` ADK `App` execution (`BRIDGE_AGENT_ENGINE_ID`, `BRIDGE_AGENT_ENGINE_REMOTE=1`), with an optional Private Service Connect network attachment (`enable_agent_engine_psc`) on the internal subnet. Moving the A2A JSON-RPC server itself onto Agent Engine's native A2A runtime (**Option B**) is recorded in the backlog (`TODO.md` / `DECISIONS.md`) as a future architectural decision.

**Durable state = Vertex AI Agent Engine (Sessions + Memory Bank) + Cloud SQL Postgres (Task store + Exchange store).** Behind the seams sit ADK `VertexAiSessionService` when `BRIDGE_AGENT_ENGINE_ID` is set (or `DatabaseSessionService` on `DATABASE_URL` when unset), ADK `VertexAiMemoryBankService` behind `Seam.memory_bank`, a2a-sdk `DatabaseTaskStore` + push-config store (tasks), and the Bridge's `SqlExchangeStore` (exchange records) on Cloud SQL Postgres.

Platform service → realization → Terraform home:

| Platform service | Realized as | Terraform home |
|---|---|---|
| **Agent Runtime** | ADK `App` (`document_bridge` / `address`) on Vertex AI Agent Engine (`BRIDGE_AGENT_ENGINE_ID`, `BRIDGE_AGENT_ENGINE_REMOTE=1`) fronted by the Cloud Run v2 HTTP/A2A/A2UI/webhook edge | `runtime` (Cloud Run v2) + `network` (`enable_agent_engine_psc` PSC network attachment) |
| **Sessions / task store / exchange store** | ADK `VertexAiSessionService` when `BRIDGE_AGENT_ENGINE_ID` is set (or `DatabaseSessionService` on Cloud SQL Postgres when unset); a2a-sdk `DatabaseTaskStore` and Bridge `SqlExchangeStore` on Cloud SQL Postgres (`GcpSessions` / `GcpTaskStore` / `GcpExchangeStore`; asyncpg DSN from the `DATABASE_URL` secret; in-memory refused) | `database` + `secrets` + `iam` (`cloudsql.client`, `aiplatform.user`) |
| **Memory Bank** | ADK `BaseMemoryService` via `Seam.memory_bank`: `InMemoryMemoryService` / `SqliteMemoryService` (`BRIDGE_MEMORY_DB_PATH`) locally; `VertexAiMemoryBankService` (`BRIDGE_AGENT_ENGINE_ID`) on GCP (`GcpMemoryBank`) | `iam` (`aiplatform.user`) + config (`BRIDGE_AGENT_ENGINE_ID`) |
| **Skill Registry** | GCS bucket + the Skill-registry seam GCP adapter | `gcs` (versioned bucket) |
| **Artifacts** (documents) | GCS bucket `<prefix>-artifacts` behind ADK `GcsArtifactService`, selected by `BRIDGE_ARTIFACT_BUCKET` (a native backend swap, not a seam); versioned — a resubmission is a new version; lifecycle Delete after `artifact_retention_days` (default 365), archived versions included; never public — bytes leave only by a signed URL minted after the leg-scope check ([artifacts](artifacts.md)) | `gcs` (per-bucket `storage.objectAdmin` to `bridge-runtime`) |
| **Agent Gateway** | external HTTPS LB → Serverless NEG + per-party scoping | `gateway` (toggleable via `enable_gateway`) |
| **Agent Identity** | `bridge-runtime` workload SA + OIDC + per-party `run.invoker` | `iam` |
| **Extraction** (Gemini / Document AI) | Vertex Gemini, plus the client's own Document AI processor as an OCR [pre-pass](docai-prepass.md), via the Extraction seam | `iam` (`aiplatform.user`; `documentai.apiUser` on the processor's project, cross-project) + config (`BRIDGE_EXTRACTION_ENGINE`, `BRIDGE_DOCAI_PROCESSOR`) |
| **Email intake** (M365 Graph) | a public, webhook-only Cloud Run service (clientState-authenticated, the one `allUsers` invoker) feeding the Email-ingress seam's create-only GCS landing bucket, drained by the internal RFP board host (Cloud Scheduler → `POST /email/drain`, OIDC); the app reads one dedicated mailbox | `runtime` + `iam` (`bridge-email-webhook`, no project roles) + `secrets` + `gcs`, gated on `enable_email_ingress`; the board host + Scheduler job in `runtime`, gated on `enable_rfp_board` |

**Deletion protection.** The root `deletion_protection` variable (default `true`) guards the Cloud SQL instance and the runtime and RFP-board Cloud Run services; the demo deploy script sets it `false`. The buckets carry none — their retention is the lifecycle rules.

Adding a platform service in a later release grows a seam adapter + a small Terraform delta, not a restructured module set — substrate-oriented modules are the stable base those deltas attach to. Validation is credential-free plan-time tests (`terraform test` + `mock_provider`); the deployed-path parity run is separate, against a provisioned project.

## Read next
- [seams](seams.md) — the local + GCP adapter table
- [network zones](zones.md) — Gateway ingress + Agent Identity
- [proactive follow-up](proactive.md) — the Scheduler/Timer pillar
- [party memory](party-memory.md) — the reserved Memory Bank seam (interface intent)

## Related
- [the Bridge](bridge.md)
- [deploying the resumable Collect loop](deploy-resume.md) — restore vs. wake on Cloud Run / Agent Engine
