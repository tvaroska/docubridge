variable "project_id" {
  type        = string
  description = "GCP project id."
}

variable "region" {
  type        = string
  description = "Region (Cloud Run location)."
}

variable "service_name" {
  type        = string
  description = "Cloud Run service name."
  default     = "bridge-runtime"
}

variable "container_image" {
  type        = string
  description = "Container image the service serves (real Bridge App image wired at deploy/P7-9)."
}

variable "enable_gateway" {
  type        = bool
  description = <<-EOT
    Whether the external Gateway/HTTPS LB fronts this service. When true, ingress
    is restricted to the internal load balancer; when false, all ingress is
    accepted (local/dev default). The gateway live module is P7-5.
  EOT
  default     = false
}

variable "service_account_email" {
  type        = string
  description = "The bridge-runtime workload SA the service runs as (from the iam module)."
}

variable "vpc_network" {
  type        = string
  description = "VPC network id for Direct VPC egress (from the network module)."
}

variable "egress_subnet" {
  type        = string
  description = "Internal subnet id the service's Direct VPC egress lands in (from the network module)."
}

variable "db_url_secret_id" {
  type        = string
  description = <<-EOT
    Secret Manager secret id whose latest version is injected as the DATABASE_URL
    env (the DB DSN). Empty ("") = no secret env (the P7-1 default; keeps runtime
    tests with no secret green). Set to module.secrets.secret_id (P7-2). The DSN
    reaches the app ONLY via this secret env; the Bridge→Cloud SQL connection is
    private-IP over the VPC, never an HTTP hop (DECISIONS.md 2026-08-24).
  EOT
  default     = ""
}

variable "spiffe_svid_secret_id" {
  type        = string
  description = <<-EOT
    Secret Manager secret id whose latest version is injected as the
    BRIDGE_SPIFFE_SVID_SECRET env (SPIFFE JWT-SVID signing key/secret). Empty ("") =
    no secret env (keeps credential-free module tests green). Set to
    module.secrets.spiffe_svid_secret_id.
  EOT
  default     = ""
}

variable "skills_bucket" {
  type        = string
  description = <<-EOT
    Skill Registry GCS bucket name, injected as the BRIDGE_SKILLS_BUCKET plain env
    (a bucket name is NOT a secret — plain env, never secret_key_ref). Empty ("") =
    no env (the P7-1/local default; keeps runtime tests with no bucket green). Set
    to module.gcs.skills_bucket_name (P7-3).
  EOT
  default     = ""
}

variable "artifacts_bucket" {
  type        = string
  description = <<-EOT
    Artifacts GCS bucket name, injected as the BRIDGE_ARTIFACT_BUCKET plain env
    (selects GcsArtifactService + signed-URL fetch). Empty ("") = no env (the
    P7-1/local default; in-memory store + direct stream). Set to
    module.gcs.artifacts_bucket_name (P7-3).
  EOT
  default     = ""
}

variable "vault_bucket" {
  type        = string
  description = <<-EOT
    Immutable Audit Vault GCS bucket name, injected as the BRIDGE_VAULT_BUCKET
    plain env (selects GcpAuditVaultStore for Zone 3 WORM storage). Empty ("") =
    no env (local/default). Set to module.gcs.audit_vault_bucket_name.
  EOT
  default     = ""
}

variable "tasks_queue_path" {
  type        = string
  description = <<-EOT
    Cloud Tasks queue resource path, injected as the BRIDGE_TASKS_QUEUE plain env
    (a queue path is NOT a secret — plain env, never secret_key_ref). Empty ("") =
    no env (the P7-1/local default; keeps runtime tests with no queue green). Set
    to module.tasks.queue_path (P7-4).
  EOT
  default     = ""
}

variable "runtime_sa_email" {
  type        = string
  description = <<-EOT
    The bridge-runtime SA email, injected as the BRIDGE_RUNTIME_SA_EMAIL plain env —
    the identity Cloud Tasks mints the OIDC callback token FOR (P7-4). Empty ("") =
    no env (the P7-1/local default). Set to module.iam.bridge_runtime_email.
  EOT
  default     = ""
}

variable "followup_callback_url" {
  type        = string
  description = <<-EOT
    URL the bridge-followups queue POSTs the OIDC-authenticated follow-up callback
    to (the OIDC audience + HTTP target), injected as the BRIDGE_FOLLOWUP_CALLBACK_URL
    plain env (P7-4). GOTCHA: this is the runtime's OWN service_uri — a Terraform
    self-reference cycle if wired from module.runtime.service_uri — so it is injected
    out-of-band at deploy (P7-9), exactly as the DB DSN VALUE is. Empty ("") = no env
    at plan time / P7-1 default (keeps runtime tests green).
  EOT
  default     = ""
}

variable "public_url" {
  type        = string
  description = <<-EOT
    The Gateway-authorized public URL, injected as the BRIDGE_PUBLIC_URL plain env
    (P7-5). The served Agent Card advertises this origin so a consumer's
    RemoteA2aAgent passes the adk >=2.7 same-origin card validation. GOTCHA: this is
    the external LB URL (module.gateway.gateway_url), which may not be known at plan
    time (an ordering/cycle risk like followup_callback_url), so it is injected
    out-of-band at deploy (P7-9). Empty ("") = no env at plan time / P7-1 default.
  EOT
  default     = ""
}

# Email intake (S0-arch-13, design/email-intake.md). enable_email_ingress gates the
# webhook-only service + its public invoker; the processor envs (bucket, mailbox, token
# key) ride the RFP board service (S0-arch-17), each gated on its own value.
variable "enable_email_ingress" {
  type        = bool
  description = "Create the webhook-only email intake Cloud Run service (+ its allUsers invoker)."
  default     = false
}

variable "email_bucket" {
  type        = string
  description = "The email landing bucket name — BRIDGE_EMAIL_BUCKET plain env on the RFP board service + the webhook; empty = no env."
  default     = ""
}

variable "email_mailbox" {
  type        = string
  description = "The dedicated intake mailbox address — BRIDGE_EMAIL_MAILBOX plain env; empty = no env."
  default     = ""
}

variable "email_token_secret_id" {
  type        = string
  description = "Secret id of the intake-token HMAC key — BRIDGE_EMAIL_TOKEN_SECRET secret env on the RFP board service; empty = no env."
  default     = ""
}

variable "email_webhook_sa_email" {
  type        = string
  description = "The email intake webhook SA email (the webhook service runs as it)."
  default     = ""
}

variable "email_graph_tenant_id" {
  type        = string
  description = "Entra tenant id (GUID or domain) — BRIDGE_EMAIL_GRAPH_TENANT plain env on the webhook (not a secret)."
  default     = ""
}

variable "email_graph_client_id" {
  type        = string
  description = "App-registration client id — BRIDGE_EMAIL_GRAPH_CLIENT_ID plain env on the webhook (not a secret)."
  default     = ""
}

variable "email_graph_client_secret_id" {
  type        = string
  description = "Secret id of the Graph client secret — BRIDGE_EMAIL_GRAPH_CLIENT_SECRET secret env on the webhook."
  default     = ""
}

variable "email_client_state_secret_id" {
  type        = string
  description = "Secret id of the Graph subscription clientState — BRIDGE_EMAIL_CLIENT_STATE secret env on the webhook."
  default     = ""
}

# The RFP board host (S0-arch-17, design/email-intake.md) — a THIRD Cloud Run service on
# the same image, internal-only, running as bridge-runtime: it owns the RFP board over the
# durable exchange store and binds the email processor; Cloud Scheduler drives its
# POST /email/drain. enable_rfp_board gates the service + its invoker; the drain job also
# needs enable_email_ingress.
variable "enable_rfp_board" {
  type        = bool
  description = "Create the internal RFP board host service (+ its bridge-runtime invoker, + the Scheduler drain job when email intake is on)."
  default     = false
}

variable "rfp_process" {
  type        = string
  description = "The emergent-Collect process skill the board runs — BRIDGE_RFP_PROCESS plain env on the board service. Required when enable_rfp_board."
  default     = ""
}

variable "gateway_host" {
  type        = string
  description = "The Gateway-authorized host the a2a_client gcp adapter sends through (BRIDGE_GATEWAY_HOST) — plain env on the RFP board service for its chase route; empty = no env (chase 503s)."
  default     = ""
}

variable "rfp_board_ingress" {
  type        = string
  description = <<-EOT
    Ingress of the RFP board service. Default INGRESS_TRAFFIC_INTERNAL_ONLY (Cloud
    Scheduler in the same project counts as internal). If a live deploy shows the
    Scheduler's POST refused at ingress, set INGRESS_TRAFFIC_ALL — Cloud Run IAM
    (run.invoker for bridge-runtime only) still gates every request.
  EOT
  default     = "INGRESS_TRAFFIC_INTERNAL_ONLY"

  validation {
    condition = contains([
      "INGRESS_TRAFFIC_INTERNAL_ONLY",
      "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER",
      "INGRESS_TRAFFIC_ALL",
    ], var.rfp_board_ingress)
    error_message = "rfp_board_ingress must be INGRESS_TRAFFIC_INTERNAL_ONLY, INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER or INGRESS_TRAFFIC_ALL."
  }
}

variable "rfp_board_max_instances" {
  type        = number
  description = "Max instances of the RFP board service. 1 serializes drains on the in-process lock; more is safe (CAS retry + re-file guard) but may duplicate extraction work."
  default     = 1

  validation {
    condition     = var.rfp_board_max_instances >= 1
    error_message = "rfp_board_max_instances must be >= 1."
  }
}

variable "min_instances" {
  type        = number
  description = "Minimum warm instances for the Cloud Run runtime and RFP board services (1 eliminates cold-start latency)."
  default     = 1

  validation {
    condition     = var.min_instances >= 0
    error_message = "min_instances must be >= 0."
  }
}

variable "cpu_idle" {
  type        = bool
  description = "Enable request-based CPU throttling (cpu_idle = true) on Cloud Run containers so min_instances = 0 truly scales to zero between requests."
  default     = true
}

variable "email_drain_schedule" {
  type        = string
  description = "Cloud Scheduler cron for the POST /email/drain tick on the RFP board service (Etc/UTC)."
  default     = "* * * * *"
}

variable "port_mode" {
  type        = string
  description = <<-EOT
    The port master switch, injected as the BRIDGE_PORT_MODE plain env (P7-9). Set to
    "gcp" on the deployed container so every port routes through its Gcp adapter (Cloud
    SQL / GCS / Cloud Tasks / Agent Identity) instead of a per-replica in-memory store
    (the session-affinity trap, DECISIONS.md 2026-08-24). Empty ("") = no env (the
    P7-1/local default; keeps runtime tests green). Set to "gcp" at deploy (P7-9).
  EOT
  default     = ""
}

variable "extraction_engine" {
  type        = string
  description = <<-EOT
    The deployed extraction engine, injected as the BRIDGE_EXTRACTION_ENGINE plain env
    (P7-6: the fixture|gemini|docai capability axis). Set to "gemini" on the deployed
    container so the extraction port runs real Gemini. Empty ("") = no env (the
    P7-1/local default; GcpExtraction still defaults to gemini when routed). Set to
    "gemini" at deploy (P7-9). "docai" (Gemini with the Document AI OCR pre-pass)
    also needs docai_processor, or the engine refuses to run.
  EOT
  default     = ""
}

variable "docai_processor" {
  type        = string
  description = "Full resource name of the client's Document AI processor, injected as the BRIDGE_DOCAI_PROCESSOR plain env for the docai extraction engine. Empty = no env."
  default     = ""
}

variable "agent_engine_id" {
  type        = string
  description = "Vertex AI Agent Engine ID or full reasoningEngines/... resource name, injected as the BRIDGE_AGENT_ENGINE_ID plain env (selects VertexAiSessionService + VertexAiMemoryBankService). Empty = no env (Cloud SQL default)."
  default     = ""
}

variable "agent_engine_remote" {
  type        = string
  description = "Set to \"1\" to stream LlmLeg turns through the deployed Vertex AI Agent Engine ReasoningEngine (BRIDGE_AGENT_ENGINE_REMOTE plain env). Empty = no env."
  default     = ""
}

variable "agent_engine_a2a" {
  type        = string
  description = "Set to \"1\" to delegate A2A discovery (/.well-known/agent-card.json) and JSON-RPC POST / to BridgeA2aAgentEngine on Vertex AI Agent Engine (BRIDGE_AGENT_ENGINE_A2A plain env). Empty = no env (in-process default)."
  default     = ""
}

variable "telemetry_bq_table" {
  type        = string
  description = "Fully qualified BigQuery table (project.dataset.table) injected as BRIDGE_TELEMETRY_BQ_TABLE for OpenTelemetry span streaming and SQL analytics. Empty = no env."
  default     = ""
}

variable "deletion_protection" {
  type        = bool
  description = "Guard the service against deletion (true in prod; dev/tests may disable)."
  default     = true
}

variable "labels" {
  type        = map(string)
  description = "Resource labels."
  default     = {}
}
