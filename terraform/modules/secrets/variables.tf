variable "project_id" {
  type        = string
  description = "GCP project id the secret container lives in."
}

variable "secret_id" {
  type        = string
  description = "Secret Manager secret id for the DB DSN (injected as DATABASE_URL)."
  default     = "bridge-database-url"
}

# The IAM member string (serviceAccount:…) granted read access to the DSN secret —
# pass module.iam.bridge_runtime_member. This is the ONLY new grant P7-2 adds;
# roles/cloudsql.client is already granted by the iam module (do NOT re-grant it).
variable "service_account_member" {
  type        = string
  description = "IAM member (serviceAccount:…) granted secretAccessor on the DSN secret."
}

variable "labels" {
  type        = map(string)
  description = "Resource labels."
  default     = {}
}

# Email intake (S0-arch-13). false = none of the three containers exist.
variable "enable_email_ingress" {
  type        = bool
  description = "Create the three email intake secret containers + their accessors."
  default     = false
}

# The webhook SA member (module.iam.email_webhook_member) — granted secretAccessor on
# the Graph client secret + the clientState ONLY.
variable "email_webhook_member" {
  type        = string
  description = "IAM member (serviceAccount:…) of the email intake webhook SA; \"\" when disabled."
  default     = ""

  validation {
    condition     = var.email_webhook_member == "" || startswith(var.email_webhook_member, "serviceAccount:")
    error_message = "email_webhook_member must be empty or a serviceAccount: member."
  }
}

variable "email_graph_client_secret_id" {
  type        = string
  description = "Secret id of the Graph app-registration client secret (BRIDGE_EMAIL_GRAPH_CLIENT_SECRET on the webhook)."
  default     = "bridge-email-graph-client-secret"
}

variable "email_client_state_secret_id" {
  type        = string
  description = "Secret id of the Graph subscription clientState (BRIDGE_EMAIL_CLIENT_STATE on the webhook)."
  default     = "bridge-email-client-state"
}

variable "email_token_secret_id" {
  type        = string
  description = "Secret id of the intake-token HMAC key (BRIDGE_EMAIL_TOKEN_SECRET on the runtime)."
  default     = "bridge-email-token-secret"
}

variable "spiffe_svid_secret_id" {
  type        = string
  description = "Secret Manager secret id for the SPIFFE JWT-SVID signing key/secret (injected as BRIDGE_SPIFFE_SVID_SECRET)."
  default     = "bridge-spiffe-svid-secret"
}

variable "manage_db_url_version" {
  type        = bool
  description = "Create the Secret Manager secret version for DATABASE_URL directly in Terraform when true (single-pass apply)."
  default     = false
}

variable "db_dsn" {
  type        = string
  description = "Composed PostgreSQL asyncpg DSN stored in the DATABASE_URL secret version when manage_db_url_version = true."
  sensitive   = true
  default     = ""
}

variable "manage_spiffe_svid_version" {
  type        = bool
  description = "Create the Secret Manager secret version for BRIDGE_SPIFFE_SVID_SECRET directly in Terraform when true (single-pass apply)."
  default     = false
}

variable "spiffe_svid_secret_data" {
  type        = string
  description = "Signing key material stored in the BRIDGE_SPIFFE_SVID_SECRET secret version when manage_spiffe_svid_version = true."
  sensitive   = true
  default     = ""
}
