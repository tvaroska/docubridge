# Root inputs. Non-secret only — the DB DSN and any credentials ride a Secret
# Manager secret env (P7-2), never a Terraform variable or tfvars (see CRITICAL
# care in the P7-1 plan: "No secrets in HCL").

variable "project_id" {
  type        = string
  description = "GCP project id the substrate is provisioned into (must be an explicit Demos or Experiments project, never a project listed in blocked_project_ids)."

  validation {
    condition     = length(trimspace(var.project_id)) > 0
    error_message = "project_id must not be empty."
  }

  validation {
    condition     = !contains(var.blocked_project_ids, trimspace(var.project_id))
    error_message = "project_id is listed in blocked_project_ids — deploy the demo stack only into a dedicated Demos or Experiments project."
  }
}

# Operator-supplied deny-list (deploy.sh passes BLOCKED_PROJECT_IDS as
# TF_VAR_blocked_project_ids). Kept out of the repo so no real project ids are
# committed; cross-variable validation needs Terraform >= 1.9.
variable "blocked_project_ids" {
  type        = list(string)
  description = "Project ids the demo stack must never be deployed into (shared/Core projects). Empty by default."
  default     = []
}

variable "region" {
  type        = string
  description = "GCP region for regional resources (Cloud Run, subnets)."
  default     = "us-central1"
}

variable "service_name" {
  type        = string
  description = "Name prefix and bridge-runtime workload SA account_id."
  default     = "bridge-runtime"
}

variable "kyc_service_name" {
  type        = string
  description = "Cloud Run service name for the KYC Address Verification app (kyc-address-verification)."
  default     = "bridge-kyc"
}

variable "rfp_service_name" {
  type        = string
  description = "Cloud Run service name for the Benefits RFP Procurement app (benefits-rfp-procurement)."
  default     = "bridge-rfp"
}

variable "meridian_service_name" {
  type        = string
  description = "Cloud Run service name for the Meridian Loan Consolidation app (meridian-loan-consolidation)."
  default     = "bridge-meridian"
}

variable "container_image" {
  type        = string
  description = <<-EOT
    Container image the Cloud Run runtime serves. Defaults to Google's public
    hello image so plans/validate resolve without a built image; the real
    Bridge App image is wired at deploy time (P7-9).
  EOT
  default     = "us-docker.pkg.dev/cloudrun/container/hello"
}

variable "enable_gateway" {
  type        = bool
  description = <<-EOT
    Whether the external HTTPS LB / Agent Gateway fronts the runtime (the
    gateway live module is P7-5). When true the runtime accepts ingress only
    from the internal load balancer; when false (local/dev default) it accepts
    all ingress. This is the network trust-boundary toggle (design/zones.md).
  EOT
  default     = false
}

# Cloud SQL (P7-2) — non-secret shape only. The DB DSN + password are NEVER root
# variables: they ride the Secret Manager secret env (module.secrets), composed at
# deploy (P7-9). See CRITICAL.md "No secrets in HCL".
variable "db_tier" {
  type        = string
  description = "Cloud SQL machine tier."
  default     = "db-custom-1-3840"
}

variable "db_name" {
  type        = string
  description = "The application database created on the Cloud SQL instance."
  default     = "bridge"
}

variable "db_user" {
  type        = string
  description = "The application SQL user created on the Cloud SQL instance."
  default     = "bridge"
}

variable "db_password" {
  type        = string
  description = "SQL user password — supplied at deploy via TF_VAR_db_password / -var (never committed to HCL/tfvars); when non-empty, Terraform composes the private-IP DATABASE_URL secret version directly in a single apply."
  sensitive   = true
  default     = ""
}

variable "spiffe_svid_secret_data" {
  type        = string
  description = "Optional signing key material for BRIDGE_SPIFFE_SVID_SECRET. When empty and db_password is set, Terraform derives a deterministic project-scoped HMAC secret in a single apply."
  sensitive   = true
  default     = ""
}

variable "seed_skills" {
  type        = bool
  description = "Upload repository skills/ files into the Skill Registry GCS bucket during terraform apply (default false so DocuBridge starts empty on its own)."
  default     = false
}

variable "empty_catalog_on_start" {
  type        = bool
  description = "Start Zone 6 Knowledge Catalog with an empty ontology revision (BRIDGE_EMPTY_CATALOG_ON_START=1) so use-case agents onboard their own doctypes from sample documents."
  default     = true
}

variable "vertex_location" {
  type        = string
  description = "Gemini platform / Vertex AI location injected as GOOGLE_CLOUD_LOCATION and BRIDGE_VERTEX_LOCATION when port_mode = gcp."
  default     = "global"
}

variable "invoker_iam_disabled" {
  type        = bool
  description = "Disable Cloud Run IAM invoker checks (--no-invoker-iam-check) on runtime and RFP board services for demo/Argolis projects where Domain-Restricted Sharing blocks allUsers."
  default     = false
}

variable "deletion_protection" {
  type        = bool
  description = "Guard the Cloud SQL instance against deletion (true in prod; dev/tests may disable)."
  default     = true
}

variable "db_activation_policy" {
  type        = string
  description = "Cloud SQL activation policy (ALWAYS when active/warm; NEVER when hibernated to stop hourly instance billing)."
  default     = "ALWAYS"

  validation {
    condition     = contains(["ALWAYS", "NEVER"], var.db_activation_policy)
    error_message = "db_activation_policy must be ALWAYS or NEVER."
  }
}

variable "cpu_idle" {
  type        = bool
  description = "Enable request-based CPU throttling (cpu_idle = true) on Cloud Run containers so min_instances = 0 truly scales to zero between requests."
  default     = true
}

# GCS artifacts retention (P7-3) — the ART-3 object-lifecycle Delete window (days)
# on the artifacts bucket. Non-secret. The Bridge keeps zero artifact-GC code; this
# lifecycle rule is the only retention mechanism (design/artifacts.md).
variable "artifact_retention_days" {
  type        = number
  description = "ART-3 retention window (days) for the artifacts bucket lifecycle Delete rule (post-exchange window; never during the exchange)."
  default     = 365
}

# Cloud Tasks follow-up callback (P7-4) — the URL the bridge-followups queue POSTs
# the OIDC-authenticated callback to. It is the runtime's OWN service_uri, which is
# only known post-apply and would be a Terraform cycle inside the runtime module, so
# it is injected out-of-band at deploy (P7-9) — exactly as the DB DSN VALUE is.
# Default "" = no env at plan time / P7-1 default (keeps runtime tests green). Set
# by the P7-9 deploy step from the resolved runtime service URL. Non-secret.
variable "followup_callback_url" {
  type        = string
  description = "URL Cloud Tasks POSTs the follow-up callback to (the runtime's own service_uri, injected at deploy/P7-9). Empty = no env at plan time."
  default     = ""
}

variable "tasks_queue_name" {
  type        = string
  description = "Cloud Tasks queue name for the follow-up ladder (default bridge-followups; override when redeploying within Cloud Tasks' 7-day queue-name tombstone window)."
  default     = "bridge-followups"
}

# Gateway ingress scoping (P7-5) — the per-party identities granted
# roles/run.invoker on the runtime ("a party can address only its own leg",
# design/zones.md) and the managed-cert domains for the external HTTPS LB. Both
# non-secret and empty at plan time; populated at deploy (P7-9). Only take effect
# when enable_gateway = true (the gateway module gates every resource on it).
variable "party_invoker_members" {
  type        = list(string)
  description = "Per-party identities granted roles/run.invoker on the runtime (Gateway ingress scoping, P7-5). Empty at plan time."
  default     = []
}

variable "ssl_domains" {
  type        = list(string)
  description = "Managed-cert domains for the external HTTPS LB (P7-5). May be empty at plan time; the LB URL is injected out-of-band at deploy via public_url."
  default     = []
}

# The Gateway-authorized public URL, injected as the runtime's BRIDGE_PUBLIC_URL env
# (P7-5). The served Agent Card advertises this origin so a consumer's RemoteA2aAgent
# passes the adk >=2.7 same-origin card validation (DECISIONS.md P7-5). It is the LB
# URL (module.gateway.gateway_url), which may not be known at plan time — a Terraform
# ordering/cycle risk like followup_callback_url — so it is injected out-of-band at
# deploy (P7-9). Default "" = no env at plan time / P7-1 default (keeps tests green).
variable "public_url" {
  type        = string
  description = "Gateway-authorized public URL, injected as BRIDGE_PUBLIC_URL (served-card same-origin, P7-5). Injected at deploy (P7-9); empty = no env at plan time."
  default     = ""
}

# Frontend deploy module (P7-7) — the deployed SPA serving surfaces. Non-secret.
# Default off keeps the credential-free plan empty (the frontend module gates every
# resource on it, mirroring enable_gateway).
variable "enable_frontend" {
  type        = bool
  description = "Whether the deployed frontend buckets + external portal LB are provisioned (P7-7). Default off keeps the credential-free plan empty."
  default     = false
}

variable "frontend_ssl_domains" {
  type        = list(string)
  description = "Managed-cert domains for the external portal LB (P7-7). Kept separate from the gateway's ssl_domains (which fronts the runtime/edge). Empty at plan time; injected at deploy (P7-9)."
  default     = []
}

# Deployed-container port wiring (P7-9). Both non-secret plain envs, empty at plan
# time so the credential-free default leaves the container env untouched. The deploy
# step sets port_mode = "gcp" (route every port to its Gcp adapter — the master
# switch, DECISIONS.md 2026-08-24 session-affinity trap) and extraction_engine =
# "gemini" (the deployed extraction engine, P7-6).
variable "port_mode" {
  type        = string
  description = "BRIDGE_PORT_MODE runtime env — the port master switch. Set \"gcp\" at deploy (P7-9); empty = no env at plan time."
  default     = ""
}

variable "extraction_engine" {
  type        = string
  description = "BRIDGE_EXTRACTION_ENGINE runtime env — the deployed extraction engine (P7-6). Set \"gemini\" at deploy (P7-9); \"docai\" also needs docai_processor; empty = no env at plan time."
  default     = ""
}

# The docai extraction engine's processor: the CLIENT's existing Document AI
# processor by full resource name (design/docai-prepass.md). Drives both the
# BRIDGE_DOCAI_PROCESSOR runtime env and the cross-project roles/documentai.apiUser
# grant on the processor's project. Empty = neither.
variable "docai_processor" {
  type        = string
  description = "Full resource name of the client's Document AI processor (projects/{project}/locations/{location}/processors/{id}); empty = no docai env, no grant."
  default     = ""
}

variable "manage_docai_grant" {
  type        = bool
  description = "Apply the cross-project Document AI grant here (false = the client applies the iam docai_grant output out-of-band)."
  default     = true
}

variable "labels" {
  type        = map(string)
  description = "Resource labels applied to substrate resources."
  default     = {}
}

# Email intake (S0-arch-13, design/email-intake.md): the dedicated M365 intake mailbox
# → Graph change notification → a webhook-only Cloud Run service → a create-only GCS
# landing bucket → the EmailProcessor's grants/env for a board-owning host running as
# bridge-runtime (design/email-intake.md). Off by default (creates nothing).
# The tenant id and client id are identifiers, not secrets; the Graph client secret,
# the clientState, and the token HMAC key are Secret Manager containers whose VALUES
# are added out-of-band — never in HCL/tfvars.
variable "enable_email_ingress" {
  type        = bool
  description = "Provision email intake: webhook SA, three secret containers, the landing bucket, and the public webhook-only service."
  default     = false
}

variable "email_mailbox" {
  type        = string
  description = "The dedicated intake mailbox address (BRIDGE_EMAIL_MAILBOX), e.g. rfp-quotes@<tenant domain>."
  default     = ""

  validation {
    condition     = var.email_mailbox == "" || can(regex("^[^@\\s]+@[^@\\s]+$", var.email_mailbox))
    error_message = "email_mailbox must be empty or an email address."
  }
}

variable "email_graph_tenant_id" {
  type        = string
  description = "Entra tenant id of the intake mailbox (BRIDGE_EMAIL_GRAPH_TENANT; a GUID also enables the notification tenantId check). Not a secret."
  default     = ""
}

variable "email_graph_client_id" {
  type        = string
  description = "Client id of the Mail.Read app registration (BRIDGE_EMAIL_GRAPH_CLIENT_ID). Not a secret."
  default     = ""
}

variable "email_retention_days" {
  type        = number
  description = "Lifecycle Delete age (days) for the email landing bucket (raw mail, attachments, outcome markers)."
  default     = 30
}

# The RFP board host (S0-arch-17, design/email-intake.md): an internal Cloud Run service
# (running as bridge-runtime) that owns the RFP board over the durable exchange store,
# binds the email processor, and is driven by a Cloud Scheduler POST /email/drain. Off
# by default (creates nothing). With enable_email_ingress, mail landed by the public
# webhook is drained ONLY here.
variable "enable_rfp_board" {
  type        = bool
  description = "Provision the internal RFP board host service (+ the Scheduler email-drain job when enable_email_ingress)."
  default     = false
}

variable "rfp_process" {
  type        = string
  description = "The emergent-Collect process skill the RFP board runs (BRIDGE_RFP_PROCESS), e.g. benefits-rfp. Required when enable_rfp_board."
  default     = ""
}

variable "gateway_host" {
  type        = string
  description = "The Gateway-authorized host (BRIDGE_GATEWAY_HOST) the RFP board service's chase route sends A2A through (a2a_client gcp adapter). Empty = no env (the chase 503s). Injected at deploy like public_url."
  default     = ""
}

variable "rfp_board_ingress" {
  type        = string
  description = "Ingress of the RFP board service (default internal-only; INGRESS_TRAFFIC_ALL if the Scheduler is refused at ingress — IAM still gates)."
  default     = "INGRESS_TRAFFIC_INTERNAL_ONLY"
}

variable "rfp_board_max_instances" {
  type        = number
  description = "Max instances of the RFP board service (1 serializes drains; more is safe but may duplicate extraction work)."
  default     = 1
}

variable "min_instances" {
  type        = number
  description = "Minimum warm instances for the Cloud Run runtime and RFP board services (1 eliminates cold-start latency during live demos)."
  default     = 1
}

variable "email_drain_schedule" {
  type        = string
  description = "Cloud Scheduler cron (Etc/UTC) for the POST /email/drain tick on the RFP board service."
  default     = "* * * * *"
}

# Enterprise Data Architecture — Cloud Storage Audit Vault (Zone 3 WORM store).
variable "enable_audit_vault" {
  type        = bool
  description = "Provision the immutable WORM Audit Vault bucket (<service_name>-audit-vault) and inject BRIDGE_VAULT_BUCKET into runtime services."
  default     = false
}

variable "vault_retention_days" {
  type        = number
  description = "WORM retention policy duration (days) for the Cloud Storage Audit Vault bucket."
  default     = 2555
}

variable "lock_audit_vault_retention" {
  type        = bool
  description = "Permanently lock the Audit Vault bucket retention policy (true in prod; false in dev/staging)."
  default     = false
}

# Vertex AI Agent Engine (Option A: Managed Sessions, Memory Bank, and ADK App runtime).
variable "enable_agent_engine_psc" {
  type        = bool
  description = "Provision a Private Service Connect network attachment on the internal subnet for Vertex AI Agent Engine private VPC egress."
  default     = false
}

variable "agent_engine_id" {
  type        = string
  description = "Vertex AI Agent Engine ID or full reasoningEngines/... resource name, injected as BRIDGE_AGENT_ENGINE_ID into Cloud Run services. Empty = no env (Cloud SQL default)."
  default     = ""
}

variable "agent_engine_remote" {
  type        = string
  description = "Set \"1\" to stream LlmLeg turns through the deployed Vertex AI Agent Engine ReasoningEngine (BRIDGE_AGENT_ENGINE_REMOTE). Empty = no env."
  default     = ""
}

variable "agent_engine_a2a" {
  type        = string
  description = "Set \"1\" to delegate A2A discovery (/.well-known/agent-card.json) and JSON-RPC POST / to BridgeA2aAgentEngine on Vertex AI Agent Engine (BRIDGE_AGENT_ENGINE_A2A). Empty = no env (in-process default)."
  default     = ""
}

variable "extra_roles" {
  type        = set(string)
  description = "Additional project-level IAM roles to bind to the bridge runtime service account beyond the base roles (e.g. roles/cloudtrace.agent)."
  default     = []
}

# Native Cloud Observability, App Hub & Zone 4 Lakehouse Analytics.
variable "enable_telemetry_bq" {
  type        = bool
  description = "Provision native Cloud Observability linked datasets, GenAI payload bucket + BigLake external table, App Hub applications, and the Zone 4 lakehouse dataset."
  default     = false
}

variable "agent_registry_service_agent_member" {
  type        = string
  description = "IAM member (serviceAccount:service-<PROJECT_NUMBER>@gcp-sa-agentregistry.iam.gserviceaccount.com) granted roles/storage.objectViewer on the Skill Registry bucket for Gemini platform Agent Registry skill ingestion."
  default     = ""
}

variable "default_demo_domain" {
  type        = string
  description = "Optional default showcase domain for /demo and /console (banking | mortgage | insurance), injected as BRIDGE_DEFAULT_DEMO_DOMAIN. Empty = banking default."
  default     = ""
}




