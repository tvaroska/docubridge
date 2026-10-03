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
