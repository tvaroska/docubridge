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

  project_id           = var.project_id
  region               = var.region
  vpc_network          = module.network.network_id
  db_tier              = var.db_tier
  db_name              = var.db_name
  db_user              = var.db_user
  db_password          = var.db_password
  db_activation_policy = var.db_activation_policy
  deletion_protection  = var.deletion_protection
  labels               = var.labels
}

# secrets — the Secret Manager containers + single-apply secret versions for the DB DSN
# and SPIFFE SVID signing key, plus the runtime's least-privilege read grants (P7-2).
# When db_password is supplied (TF_VAR_db_password), Terraform composes the private-IP
# Cloud SQL DSN and creates the secret versions before module.runtime starts Cloud Run.
module "secrets" {
  source = "./modules/secrets"

  project_id                 = var.project_id
  service_account_member     = module.iam.bridge_runtime_member
  labels                     = var.labels
  manage_db_url_version      = nonsensitive(var.db_password != "")
  db_dsn                     = nonsensitive(var.db_password != "") ? "postgresql+asyncpg://${module.database.db_user}:${urlencode(var.db_password)}@${module.database.private_ip_address}:5432/${module.database.database_name}" : ""
  manage_spiffe_svid_version   = nonsensitive(var.db_password != "" || var.spiffe_svid_secret_data != "")
  spiffe_svid_secret_data      = nonsensitive(var.spiffe_svid_secret_data != "") ? var.spiffe_svid_secret_data : (nonsensitive(var.db_password != "") ? sha256("${var.project_id}:${var.service_name}:${var.db_password}:spiffe-svid") : "")
  manage_user_jwt_version      = nonsensitive(var.db_password != "" || var.user_jwt_secret_data != "")
  user_jwt_secret_data         = nonsensitive(var.user_jwt_secret_data != "") ? var.user_jwt_secret_data : (nonsensitive(var.db_password != "") ? sha256("${var.project_id}:${var.service_name}:${var.db_password}:user-jwt") : "")
  enable_demo_passcode         = var.enable_demo_passcode || nonsensitive(var.demo_passcode != "")
  manage_demo_passcode_version = nonsensitive(var.demo_passcode != "")
  demo_passcode_data           = var.demo_passcode
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

  project_id                          = var.project_id
  name_prefix                         = var.service_name
  location                            = var.region
  service_account_member              = module.iam.bridge_runtime_member
  retention_days                      = var.artifact_retention_days
  payload_retention_days              = var.payload_retention_days
  seed_skills                         = var.seed_skills
  single_bucket_mode                  = var.single_bucket_mode
  create_shared_bucket                = var.create_shared_bucket
  shared_bucket_name                  = var.shared_bucket_name
  force_destroy_shared_bucket         = !var.deletion_protection
  labels                              = var.labels
  enable_email_ingress                = var.enable_email_ingress
  email_webhook_member                = module.iam.email_webhook_member
  email_retention_days                = var.email_retention_days
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
  queue_name = var.tasks_queue_name
  labels     = var.labels
}

module "telemetry" {
  source = "./modules/telemetry"

  project_id                              = var.project_id
  region                                  = var.region
  enable_telemetry_bq                     = var.enable_telemetry_bq
  single_bucket_mode                      = var.single_bucket_mode
  shared_bucket_name                      = module.gcs.shared_bucket_name
  payload_retention_days                  = var.payload_retention_days
  create_eval_dataset                     = var.create_eval_dataset
  eval_dataset_id                         = var.eval_dataset_id
  create_vertex_connection                = var.create_vertex_connection
  vertex_connection_id                    = var.vertex_connection_id
  vertex_connection_service_account_email = var.vertex_connection_service_account_email
  create_shared_bucket_lister_role        = false
  shared_bucket_lister_role_name          = module.gcs.shared_bucket_lister_role_name
  service_account_member                  = module.iam.bridge_runtime_member
  deletion_protection                     = var.deletion_protection
  labels                                  = var.labels
}

module "runtime" {
  source = "./modules/runtime"

  project_id                   = var.project_id
  region                       = var.region
  service_name                 = var.service_name
  kyc_service_name             = var.kyc_service_name
  rfp_service_name             = var.rfp_service_name
  meridian_service_name        = var.meridian_service_name
  container_image              = var.container_image
  enable_gateway               = var.enable_gateway
  service_account_email        = module.iam.bridge_runtime_email
  vpc_network                  = module.network.network_id
  egress_subnet                = module.network.internal_subnet_id
  db_url_secret_id             = module.secrets.secret_id
  spiffe_svid_secret_id        = module.secrets.spiffe_svid_secret_id
  user_jwt_secret_id           = module.secrets.user_jwt_secret_id
  demo_passcode_secret_id      = module.secrets.demo_passcode_secret_id
  iap_audience                 = var.iap_audience
  cors_origins                 = var.cors_origins
  principal_roles_json         = var.principal_roles_json
  gs_allowlist                 = var.gs_allowlist
  single_bucket_mode           = var.single_bucket_mode
  skills_bucket                = module.gcs.skills_bucket_name
  skills_prefix                = module.gcs.skills_prefix
  artifacts_bucket             = module.gcs.artifacts_bucket_name
  vault_bucket                 = module.gcs.audit_vault_bucket_name
  vault_prefix                 = module.gcs.audit_vault_prefix
  vault_retention_days         = var.single_bucket_mode && var.enable_audit_vault ? var.vault_retention_days : 0
  vault_retention_mode         = var.single_bucket_mode && var.enable_audit_vault ? (var.lock_audit_vault_retention ? "Locked" : "Unlocked") : ""
  scenarios_bucket             = module.gcs.scenarios_bucket_name
  scenarios_prefix             = module.gcs.scenarios_prefix
  genai_upload_base_path       = var.single_bucket_mode ? "gs://${module.gcs.shared_bucket_name}/genai-payloads/" : ""
  tasks_queue_path             = module.tasks.queue_path
  runtime_sa_email             = module.iam.bridge_runtime_email
  followup_callback_url        = var.followup_callback_url
  public_url                   = var.public_url != "" ? var.public_url : (var.enable_gateway && length(var.ssl_domains) > 0 ? "https://${var.ssl_domains[0]}/" : "")
  enable_email_ingress         = var.enable_email_ingress
  email_bucket                 = module.gcs.email_bucket_name
  email_prefix                 = module.gcs.email_prefix
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
  cpu_idle                = var.cpu_idle
  # S0-arch-19: with enable_rfp_board the A2A runtime also gets BRIDGE_RFP_PROCESS (it
  # OPENS RFPs onto the shared store); the board service gets the a2a_client gcp env
  # (BRIDGE_RUNTIME_SA_EMAIL + BRIDGE_GATEWAY_HOST) for its chase route.
  gateway_host         = var.gateway_host != "" ? var.gateway_host : (var.enable_gateway && length(var.ssl_domains) > 0 ? "https://${var.ssl_domains[0]}/" : "")
  email_drain_schedule = var.email_drain_schedule
  # The port master switch + extraction engine (P7-9/P7-6) — plain envs routing every
  # port through its Gcp adapter (BRIDGE_PORT_MODE=gcp) and selecting the Gemini
  # extraction engine (BRIDGE_EXTRACTION_ENGINE=gemini). Default "" = no env at plan
  # time / P7-1 default (keeps runtime tests green); set at deploy (P7-9).
  port_mode              = var.port_mode
  extraction_engine      = var.extraction_engine
  vertex_location        = var.vertex_location
  invoker_iam_disabled   = var.invoker_iam_disabled
  empty_catalog_on_start = var.empty_catalog_on_start && !var.seed_skills
  # The client's Document AI processor for the docai engine (BRIDGE_DOCAI_PROCESSOR
  # plain env); default "" = no env.
  docai_processor     = var.docai_processor
  agent_engine_id     = var.agent_engine_id
  agent_engine_remote = var.agent_engine_remote
  agent_engine_a2a    = var.agent_engine_a2a
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

  project_id         = var.project_id
  name_prefix        = var.service_name
  location           = var.region
  region             = var.region
  single_bucket_mode = var.single_bucket_mode
  enable_frontend    = var.enable_frontend
  ssl_domains        = var.frontend_ssl_domains
  labels             = var.labels
}

# Landing mail with no deployed drain would fill the bucket until lifecycle deletes it
# (S0-arch-17). A check is a plan-time WARNING, not a failure.
check "email_ingress_has_a_drain" {
  assert {
    condition     = !var.enable_email_ingress || var.enable_rfp_board
    error_message = "enable_email_ingress without enable_rfp_board lands mail that no deployed service drains."
  }
}
