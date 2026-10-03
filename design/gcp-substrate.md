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

**`runtime` = Cloud Run v2 hosting the ADK `App`.** Cloud Run is the compute host because the Bridge serves custom HTTP that Agent Engine's managed runtime cannot host — the A2UI endpoints, the scoped artifact-fetch endpoint, the push/wake receiver. Vertex Agent Engine is not part of the deployment.

**Durable state = Cloud SQL Postgres.** Behind the seams sit ADK `DatabaseSessionService` (sessions), a2a-sdk `DatabaseTaskStore` + push-config store (tasks) and the Bridge's `SqlExchangeStore` (exchange records), all on the one `DATABASE_URL` DSN and the same classes the local adapters build over sqlite. A managed session service would be a one-adapter swap behind the Sessions seam. The Memory Bank seam declares no methods ([party memory](party-memory.md)).

Platform service → realization → Terraform home:

| Platform service | Realized as | Terraform home |
|---|---|---|
| **Agent Runtime** | the deployed ADK `App` (FastAPI + `a2a-sdk` A2A edge + A2UI portal) | `runtime` (Cloud Run v2) |
| **Sessions / task store / exchange store** | ADK `DatabaseSessionService`, a2a-sdk `DatabaseTaskStore`, Bridge `SqlExchangeStore` on Cloud SQL Postgres via the gcp adapters (`GcpSessions` / `GcpTaskStore` / `GcpExchangeStore`; asyncpg DSN from the `DATABASE_URL` secret; in-memory refused) | `database` + `secrets` + `iam` (`cloudsql.client`) |
| **Memory Bank** | a named seam with no methods: the local adapter is empty, there is no GCP adapter (the registry raises) | none |
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
