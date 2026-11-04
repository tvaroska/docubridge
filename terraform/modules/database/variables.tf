variable "project_id" {
  type        = string
  description = "GCP project id the Cloud SQL instance is provisioned into."
}

variable "region" {
  type        = string
  description = "Region for the Cloud SQL instance."
}

# The VPC the instance peers into over Private Services Access. Cloud SQL is
# private-IP ONLY (see main.tf ip_configuration) — the Bridge reaches it over the
# VPC, never an HTTP hop (DECISIONS.md 2026-08-24). Pass module.network.network_id.
variable "vpc_network" {
  type        = string
  description = "VPC network id/self_link the private-IP instance peers into (PSA)."
}

variable "instance_name" {
  type        = string
  description = "Cloud SQL instance name."
  default     = "bridge-sql"
}

variable "db_tier" {
  type        = string
  description = "Cloud SQL machine tier."
  default     = "db-custom-1-3840"
}

variable "db_edition" {
  type        = string
  description = "Cloud SQL edition. ENTERPRISE supports the db-custom-* tiers; ENTERPRISE_PLUS requires db-perf-optimized-N-* tiers."
  default     = "ENTERPRISE"
}

variable "db_name" {
  type        = string
  description = "The application database created on the instance."
  default     = "bridge"
}

variable "db_user" {
  type        = string
  description = "The application SQL user created on the instance."
  default     = "bridge"
}

# The SQL user password is NEVER committed. It has no default here: the real
# credential is supplied out-of-band at deploy (P7-9) — the composed DSN then
# rides the Secret Manager secret env (the secrets module), never plaintext HCL.
# IAM database authentication is the preferred alternative (no password at all);
# this var is the plan-time port so `terraform plan/validate` resolves.
variable "db_password" {
  type        = string
  description = "SQL user password — supplied at deploy (P7-9), never committed. Prefer IAM auth."
  sensitive   = true
  default     = ""
}

variable "psa_prefix_length" {
  type        = number
  description = "Prefix length for the PSA reserved range (an INTERNAL VPC_PEERING range)."
  default     = 16
}

variable "deletion_protection" {
  type        = bool
  description = "Guard the instance against deletion (true in prod; dev/tests may disable)."
  default     = true
}

variable "db_activation_policy" {
  type        = string
  description = "Cloud SQL activation policy (ALWAYS or NEVER for hibernated projects)."
  default     = "ALWAYS"

  validation {
    condition     = contains(["ALWAYS", "NEVER"], var.db_activation_policy)
    error_message = "db_activation_policy must be ALWAYS or NEVER."
  }
}

variable "labels" {
  type        = map(string)
  description = "Resource labels."
  default     = {}
}

variable "iam_service_account_email" {
  type        = string
  description = "Optional runtime service account email to provision as a passwordless CLOUD_IAM_SERVICE_ACCOUNT PostgreSQL user."
  default     = ""
}

