variable "project_id" {
  type        = string
  description = "GCP project id the buckets live in."
}

variable "name_prefix" {
  type        = string
  description = "Prefix for the bucket names (<prefix>-skills, <prefix>-artifacts). Bucket names are global; the deploy passes a project-unique prefix."
  default     = "bridge"
}

variable "location" {
  type        = string
  description = "Bucket location (region or multi-region)."
  default     = "US"
}

# The IAM member string (serviceAccount:…) granted object access on BOTH buckets —
# pass module.iam.bridge_runtime_member. This is the ONLY new grant P7-3 adds:
# signing rides the SA's existing self-scoped serviceAccountTokenCreator (P7-1),
# so no new IAM is needed for signed URLs — only bucket object access.
variable "service_account_member" {
  type        = string
  description = "IAM member (serviceAccount:…) granted roles/storage.objectAdmin per-bucket (write for install_skill/save_artifact, read for fetch)."
}

variable "retention_days" {
  type        = number
  description = <<-EOT
    ART-3 retention window (days) for the artifacts bucket lifecycle Delete rule.
    Artifacts are retained during the exchange and its post-exchange window; the
    lifecycle rule deletes objects only AFTER this age (never during the exchange).
    The Bridge keeps ZERO artifact-GC/TTL code — this object-lifecycle rule is the
    only retention mechanism (design/artifacts.md, bridge/edge/artifacts.py ART-3).
  EOT
  default     = 365
}

variable "labels" {
  type        = map(string)
  description = "Resource labels."
  default     = {}
}

# Email intake (S0-arch-13). false = no email bucket, no grants.
variable "enable_email_ingress" {
  type        = bool
  description = "Create the email landing bucket (<prefix>-email) + its create-only grants."
  default     = false
}

variable "email_webhook_member" {
  type        = string
  description = "IAM member (serviceAccount:…) of the email intake webhook SA — granted roles/storage.objectCreator ONLY; \"\" when disabled."
  default     = ""

  validation {
    condition     = var.email_webhook_member == "" || startswith(var.email_webhook_member, "serviceAccount:")
    error_message = "email_webhook_member must be empty or a serviceAccount: member."
  }
}

variable "email_retention_days" {
  type        = number
  description = "Lifecycle Delete age (days) for landed mail, attachments, and outcome markers in the email bucket."
  default     = 30

  validation {
    condition     = var.email_retention_days >= 1
    error_message = "email_retention_days must be at least 1."
  }
}

# Enterprise Data Architecture — Cloud Storage Audit Vault (Zone 3 WORM store).
variable "enable_audit_vault" {
  type        = bool
  description = "Create the immutable WORM Audit Vault bucket (<prefix>-audit-vault) + its create-only/viewer grants."
  default     = false
}

variable "vault_retention_days" {
  type        = number
  description = "WORM retention policy duration (days) for the Cloud Storage Audit Vault bucket."
  default     = 2555 # 7 years regulatory compliance retention

  validation {
    condition     = var.vault_retention_days >= 1
    error_message = "vault_retention_days must be at least 1."
  }
}

variable "lock_audit_vault_retention" {
  type        = bool
  description = "Permanently lock the Audit Vault bucket retention policy (true in prod; false in dev/staging so test buckets can be torn down)."
  default     = false
}

# Gemini Platform Agent Registry Service Agent (agentregistry.googleapis.com):
# service-<PROJECT_NUMBER>@gcp-sa-agentregistry.iam.gserviceaccount.com requires
# roles/storage.objectViewer on the Skill Registry bucket so `gcloud alpha agent-registry
# skills create --source=gs://...` can inspect and version skill .zip archives.
variable "agent_registry_service_agent_member" {
  type        = string
  description = "IAM member (serviceAccount:service-<PROJECT_NUMBER>@gcp-sa-agentregistry.iam.gserviceaccount.com) granted roles/storage.objectViewer on the Skill Registry bucket; \"\" when unset."
  default     = ""

  validation {
    condition     = var.agent_registry_service_agent_member == "" || startswith(var.agent_registry_service_agent_member, "serviceAccount:")
    error_message = "agent_registry_service_agent_member must be empty or a serviceAccount: member."
  }
}

variable "seed_skills" {
  type        = bool
  description = "Deprecated no-op in HCL (skill files are synced via gcloud storage rsync in scripts/deploy.sh, keeping zero bucket_object resources in Terraform)."
  default     = false
}

variable "single_bucket_mode" {
  type        = bool
  description = "Consolidate skills/, document_bridge/, scenarios/, email/, audit-vault/, and genai-payloads/ into a single shared bucket (gs://<project_id>) with per-object retention, prefix-scoped lifecycle Delete rules, and CEL IAM prefix conditions."
  default     = false
}

variable "create_shared_bucket" {
  type        = bool
  description = "When single_bucket_mode = true: true creates gs://<project_id> (standalone install on a cold GCP project); false adopts an existing shared bucket via data.google_storage_bucket (co-located install)."
  default     = true
}

variable "shared_bucket_name" {
  type        = string
  description = "Optional override for the shared bucket name in single_bucket_mode (defaults to var.project_id)."
  default     = ""
}

variable "payload_retention_days" {
  type        = number
  description = "Lifecycle Delete age (days) for the genai-payloads/ prefix on the shared bucket in single_bucket_mode."
  default     = 90

  validation {
    condition     = var.payload_retention_days >= 1
    error_message = "payload_retention_days must be at least 1."
  }
}

variable "force_destroy_shared_bucket" {
  type        = bool
  description = "Allow Terraform destroy of the shared bucket when non-empty (false in prod; may be disabled in dev/staging)."
  default     = false
}

