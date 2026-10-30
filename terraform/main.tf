# Root module — wires the three P7-1 substrate primitives (network → iam →
# runtime) and declares the extension points the later P7 primitives attach to.
#
# Provider CONFIGURATION lives here only; child modules inherit it. Under
# `mock_provider` (terraform test) this block is never authenticated, so the
# plan-time test suite runs credential-free (the P7-1 exit criterion).

provider "google" {
  project = var.project_id
  region  = var.region
}

provider "google-beta" {
  project = var.project_id
  region  = var.region
}

# ---------------------------------------------------------------------------
# Extension ports — declared here, built by later P7 tasks as small deltas.
# The substrate-by-primitive module set is the STABLE BASE these attach to; do
# NOT restructure it (design/gcp-substrate.md §"substrate primitives"). Each
# entry names the root wiring point the later primitive consumes:
#
#   P7-3 GCS registry/artifacts (Skill Registry bucket + signed URLs + retention):
#           BUILT (module.gcs) — reads module.iam.bridge_runtime_member for
#           per-bucket object access; feeds bucket NAMES to the runtime as plain
#           envs. Signing needs no new IAM (rides the SA's P7-1 token-creator).
#   P7-4 tasks (the bridge-followups Cloud Tasks queue + OIDC callback):
#           BUILT (module.tasks) — the queue container only; the token-creator
#           binding it consumes is already on the SA here
#           (module.iam.tasks_token_creator), so P7-4 adds NO new IAM. Feeds the
#           queue PATH + SA email to the runtime as plain envs. The follow-up
#           callback URL is the runtime's OWN service_uri (a Terraform self-ref
#           cycle) — injected out-of-band at deploy (P7-9) via followup_callback_url,
#           exactly as the DB DSN VALUE is (never wired module.runtime → module.runtime).
#   P7-5 gateway (external HTTPS LB → Serverless NEG, toggled by enable_gateway):
#           BUILT (module.gateway) — the external HTTPS LB → Serverless NEG on
#           module.runtime.service_name plus the per-party roles/run.invoker
#           scoping. INGRESS ONLY: it creates NO egress/firewall/VPC/datastore
#           resource — the Bridge→Cloud SQL path stays the runtime module's
#           private-IP Direct VPC egress, NEVER an HTTP hop (DECISIONS.md
#           2026-08-24). The runtime's ingress is computed from enable_gateway.
#           gateway_url feeds BRIDGE_PUBLIC_URL (served-card same-origin, adk >=2.7);
#           since the LB URL may not be known at plan time it is injected out-of-band
#           at deploy (P7-9) via var.public_url, exactly as followup_callback_url is.
#   P7-7 frontend (deployed SPA surfaces): BUILT (module.frontend) — serves the
#           three built Vite apps from GCS. The external-zone provider-portal is a
#           PUBLIC bucket behind an external HTTPS LB + Cloud CDN; the internal-zone
#           agent-console + ops-dashboard are PRIVATE staging buckets (live internal
#           serving is a follow-up, design/zones.md). Toggled by enable_frontend; the
#           default (false) creates nothing. Wiring "to the deployed edge/dashboard
#           endpoints" is BUILD-TIME: each app's VITE_*_URL is injected at deploy
#           (P7-9) from the resolved deployed endpoints. Terraform provisions the
#           buckets + LB; the built dist/ assets are uploaded at deploy via `gsutil
#           rsync` (No OBJECTS in HCL, CRITICAL.md), never Terraform-managed.
#
# The platform-native services (Agent Runtime, Sessions/Memory Bank, Skill
# Registry, Gateway, Agent Identity, Extraction) deliberately do NOT appear as
# same-named modules — they are realized through the ports' GCP adapters
# (config + IAM + client code). Terraform provisions substrate; the ports
# provision services (design/gcp-substrate.md).
# ---------------------------------------------------------------------------

module "network" {
  source = "./modules/network"

  project_id              = var.project_id
  region                  = var.region
  enable_agent_engine_psc = var.enable_agent_engine_psc
  labels                  = var.labels
}

module "iam" {
  source = "./modules/iam"

  project_id   = var.project_id
  service_name = var.service_name
  extra_roles  = var.extra_roles
  # The docai extraction engine's cross-project grant: roles/documentai.apiUser
  # on the CLIENT's processor project (design/docai-prepass.md). Empty = no grant.
  docai_processor    = var.docai_processor
  manage_docai_grant = var.manage_docai_grant
  # Email intake (S0-arch-13): the webhook-only SA, gated; no project roles.
  enable_email_ingress = var.enable_email_ingress
}

# database — Cloud SQL PG16 on private IP via PSA (P7-2). The shared relational
# store behind the sessions/task_store/exchange_store GCP port adapters (never
# per-replica in-memory — the session-affinity trap, design/ports.md). Peers into
# the VPC over Private Services Access; ipv4_enabled = false (no public IP).
module "database" {
  source = "./modules/database"

  project_id          = var.project_id
  region              = var.region
  vpc_network         = module.network.network_id
  db_tier             = var.db_tier
  db_name             = var.db_name
  db_user             = var.db_user
  db_password         = var.db_password
  deletion_protection = var.deletion_protection
  labels              = var.labels
}

# secrets — the Secret Manager container for the DB DSN + the runtime's read grant
# (P7-2). The DSN VALUE (which embeds the DB credential) is injected out-of-band at
# deploy (P7-9); this module creates only the container + a secretAccessor binding
# to the bridge-runtime SA. No plaintext DSN in HCL (CRITICAL.md).
module "secrets" {
  source = "./modules/secrets"

  project_id             = var.project_id
  service_account_member = module.iam.bridge_runtime_member
  labels                 = var.labels
  # Email intake (S0-arch-13): three gated CONTAINERS (no versions) — the Graph client
  # secret + clientState read by the webhook SA only, the token key by the runtime only.
  enable_email_ingress = var.enable_email_ingress
  email_webhook_member = module.iam.email_webhook_member
}

# gcs — the Skill Registry bucket + the Artifacts bucket (P7-3). Never public
# (UBLA + public_access_prevention ENFORCED); signed V4 URLs are the only external
# byte path. The ONLY new grant is per-bucket objectAdmin to the bridge-runtime SA
# (signing rides the SA's existing token-creator self-grant, P7-1). ART-1 versioning
# + ART-3 lifecycle Delete retention live on the artifacts bucket.
module "gcs" {
  source = "./modules/gcs"

  project_id             = var.project_id
  name_prefix            = var.service_name
  location               = var.region
  service_account_member = module.iam.bridge_runtime_member
  retention_days         = var.artifact_retention_days
  labels                 = var.labels
  # Email intake (S0-arch-13): the gated landing bucket — webhook objectCreator only,
  # runtime objectViewer + objectCreator, lifecycle Delete after email_retention_days.
  enable_email_ingress = var.enable_email_ingress
  email_webhook_member = module.iam.email_webhook_member
  email_retention_days = var.email_retention_days
  # Enterprise Data Architecture — Cloud Storage Audit Vault (Zone 3 WORM store).
  enable_audit_vault                  = var.enable_audit_vault
  vault_retention_days                = var.vault_retention_days
  lock_audit_vault_retention          = var.lock_audit_vault_retention
  agent_registry_service_agent_member = var.agent_registry_service_agent_member
}

# tasks — the bridge-followups Cloud Tasks queue (P7-4). The substrate behind the
# scheduler port's GCP adapter (GcpScheduler): the follow-up ladder arms one Cloud
# Task per exchange, fired by the queue POSTing an OIDC-authenticated callback at
# schedule_time. NO new IAM — the enqueuer + SA-scoped token-creator grants are
# already on the SA (module.iam); this module provisions only the queue container.
module "tasks" {
  source = "./modules/tasks"

  project_id = var.project_id
  region     = var.region
  labels     = var.labels
}

module "telemetry" {
  source = "./modules/telemetry"

  project_id             = var.project_id
  region                 = var.region
  enable_telemetry_bq    = var.enable_telemetry_bq
  dataset_id             = var.telemetry_bq_dataset_id
  service_account_member = module.iam.bridge_runtime_member
  deletion_protection    = var.deletion_protection
  labels                 = var.labels
}

module "runtime" {
  source = "./modules/runtime"

  project_id            = var.project_id
  region                = var.region
  service_name          = var.service_name
  container_image       = var.container_image
  enable_gateway        = var.enable_gateway
  service_account_email = module.iam.bridge_runtime_email
  vpc_network           = module.network.network_id
  egress_subnet         = module.network.internal_subnet_id
  # Inject the DB DSN as the DATABASE_URL secret env (P7-2). The Bridge connects to
  # Cloud SQL over the private-IP VPC path, never an HTTP hop (DECISIONS.md 2026-08-24).
  db_url_secret_id      = module.secrets.secret_id
  spiffe_svid_secret_id = module.secrets.spiffe_svid_secret_id
  # GCS bucket NAMES as plain envs (P7-3) — not secrets, so plain env not
  # secret_key_ref. Select the GCS skill registry + GcsArtifactService/signed-URL path.
  skills_bucket    = module.gcs.skills_bucket_name
  artifacts_bucket = module.gcs.artifacts_bucket_name
  vault_bucket     = module.gcs.audit_vault_bucket_name
  # Cloud Tasks scheduler wiring (P7-4) as plain envs (a queue path / SA email are
  # not secrets). The follow-up callback URL is the runtime's OWN service_uri — a
  # Terraform self-reference cycle, so it is injected out-of-band at deploy (P7-9)
  # via var.followup_callback_url (default "" = no env at plan time), exactly as the
  # DB DSN VALUE is. Do NOT wire module.runtime.service_uri back into module.runtime.
  tasks_queue_path      = module.tasks.queue_path
  runtime_sa_email      = module.iam.bridge_runtime_email
  followup_callback_url = var.followup_callback_url
  # The Gateway-authorized public URL as the BRIDGE_PUBLIC_URL plain env (P7-5) —
  # the served Agent Card advertises this origin so a consumer's RemoteA2aAgent
  # passes the adk >=2.7 same-origin card validation (DECISIONS.md P7-5). The LB URL
  # may not be known at plan time (like followup_callback_url), so it is injected
  # out-of-band at deploy (P7-9) via var.public_url; default "" = no env at plan time.
  public_url = var.public_url
  # Email intake (S0-arch-13, design/email-intake.md): the processor envs (bucket,
  # mailbox, token-key secret_key_ref — on the RFP board service) + the gated
  # webhook-only service and its allUsers invoker (a deliberate trust-boundary
  # exception — see modules/runtime). Every value is "" / false by default, so the
  # default plan is unchanged.
  enable_email_ingress         = var.enable_email_ingress
  email_bucket                 = module.gcs.email_bucket_name
  email_mailbox                = var.email_mailbox
  email_token_secret_id        = module.secrets.email_token_secret_id
  email_webhook_sa_email       = module.iam.email_webhook_email
  email_graph_tenant_id        = var.email_graph_tenant_id
  email_graph_client_id        = var.email_graph_client_id
  email_graph_client_secret_id = module.secrets.email_graph_client_secret_id
  email_client_state_secret_id = module.secrets.email_client_state_secret_id
  # The RFP board host (S0-arch-17): the internal service that owns the RFP board over
  # the durable exchange store and binds the email processor (it — not the A2A runtime —
  # carries the BRIDGE_EMAIL_* envs), plus the Cloud Scheduler POST /email/drain job.
  # Runs as bridge-runtime: no new grant. Off by default (creates nothing).
  enable_rfp_board        = var.enable_rfp_board
  rfp_process             = var.rfp_process
  rfp_board_ingress       = var.rfp_board_ingress
  rfp_board_max_instances = var.rfp_board_max_instances
  min_instances           = var.min_instances
  # S0-arch-19: with enable_rfp_board the A2A runtime also gets BRIDGE_RFP_PROCESS (it
  # OPENS RFPs onto the shared store); the board service gets the a2a_client gcp env
  # (BRIDGE_RUNTIME_SA_EMAIL + BRIDGE_GATEWAY_HOST) for its chase route.
  gateway_host         = var.gateway_host
  email_drain_schedule = var.email_drain_schedule
  # The port master switch + extraction engine (P7-9/P7-6) — plain envs routing every
  # port through its Gcp adapter (BRIDGE_PORT_MODE=gcp) and selecting the Gemini
  # extraction engine (BRIDGE_EXTRACTION_ENGINE=gemini). Default "" = no env at plan
  # time / P7-1 default (keeps runtime tests green); set at deploy (P7-9).
  port_mode         = var.port_mode
  extraction_engine = var.extraction_engine
  # The client's Document AI processor for the docai engine (BRIDGE_DOCAI_PROCESSOR
  # plain env); default "" = no env.
  docai_processor     = var.docai_processor
  agent_engine_id     = var.agent_engine_id
  agent_engine_remote = var.agent_engine_remote
  agent_engine_a2a    = var.agent_engine_a2a
  telemetry_bq_table  = module.telemetry.spans_table_fqn
  default_demo_domain = var.default_demo_domain
  deletion_protection = var.deletion_protection
  labels              = var.labels
}

# gateway — external HTTPS LB → Serverless NEG on the runtime + per-party
# roles/run.invoker scoping (P7-5). INGRESS ONLY: it creates NO egress/firewall/VPC/
# datastore resource — the Bridge→Cloud SQL path stays the runtime module's
# private-IP Direct VPC egress, never an HTTP hop (DECISIONS.md 2026-08-24). All
# resources are gated on enable_gateway, so the P7-1 default (false) creates nothing.
module "gateway" {
  source = "./modules/gateway"

  project_id            = var.project_id
  region                = var.region
  service_name          = module.runtime.service_name
  enable_gateway        = var.enable_gateway
  party_invoker_members = var.party_invoker_members
  ssl_domains           = var.ssl_domains
  labels                = var.labels
}

# frontend — serves the three built Vite apps from GCS (P7-7). The external-zone
# provider-portal is PUBLIC (external HTTPS LB + Cloud CDN over a public bucket);
# the internal-zone agent-console + ops-dashboard are PRIVATE staging buckets (live
# internal serving is a follow-up, design/zones.md). All resources are gated on
# enable_frontend, so the default (false) creates nothing. The built dist/ assets
# are uploaded at deploy (P7-9) via `gsutil rsync` — never Terraform-managed (No
# OBJECTS in HCL, CRITICAL.md); each app's VITE_*_URL is a build-time env injected
# then from the resolved deployed endpoints. frontend_ssl_domains fronts the portal
# host (kept separate from the gateway's ssl_domains, which fronts the runtime/edge).
module "frontend" {
  source = "./modules/frontend"

  project_id      = var.project_id
  name_prefix     = var.service_name
  location        = var.region
  region          = var.region
  enable_frontend = var.enable_frontend
  ssl_domains     = var.frontend_ssl_domains
  labels          = var.labels
}

# Landing mail with no deployed drain would fill the bucket until lifecycle deletes it
# (S0-arch-17). A check is a plan-time WARNING, not a failure.
check "email_ingress_has_a_drain" {
  assert {
    condition     = !var.enable_email_ingress || var.enable_rfp_board
    error_message = "enable_email_ingress without enable_rfp_board lands mail that no deployed service drains."
  }
}
