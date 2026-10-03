variable "project_id" {
  type        = string
  description = "GCP project id the roles are granted in."
}

variable "service_name" {
  type        = string
  description = "account_id for the bridge-runtime workload service account."
  default     = "bridge-runtime"
}

# Base grants come straight from design/gcp-substrate.md §30 + TODO.md P7 "Work".
# Least-privilege: only the roles the design names. Do NOT add editor/owner or a
# project-wide token-creator (see the P7-1 plan §6 CRITICAL care).
variable "base_roles" {
  type        = set(string)
  description = "Base project roles granted to the bridge-runtime SA."
  default = [
    "roles/cloudsql.client",
    "roles/cloudtasks.enqueuer",
    "roles/aiplatform.user",
  ]
}

# Extension seam: later primitives (tasks P7-4, etc.) can add grants without
# editing this module. Empty by default.
variable "extra_roles" {
  type        = set(string)
  description = "Additional project roles for the bridge-runtime SA (later primitives extend here)."
  default     = []
}

# Cloud Build identity grants — the compute default SA that `gcloud builds submit`
# runs as. On by default so the image build (source read + AR push + logs) works
# on a fresh project without a manual gcloud grant; disable if the build SA is
# provisioned out-of-band.
variable "enable_build_sa_grants" {
  type        = bool
  description = "Grant the compute default SA the roles Cloud Build needs to build+push the runtime image."
  default     = true
}

variable "build_sa_roles" {
  type        = set(string)
  description = "Roles granted to the Cloud Build (compute default) SA. The purpose-built builds.builder covers source read, AR push, and log writing."
  default     = ["roles/cloudbuild.builds.builder"]
}

# Document AI pre-pass (the docai extraction engine). The processor is the CLIENT's
# existing one, referenced by full resource name — usually in the client's own
# project, so the grant below is cross-project. Empty = no docai engine, no grant.
variable "docai_processor" {
  type        = string
  description = "Full resource name of the client's Document AI processor (projects/{project}/locations/{location}/processors/{id}[/processorVersions/{v}]); empty = no Document AI grant."
  default     = ""

  validation {
    condition     = var.docai_processor == "" || can(regex("^projects/[^/]+/locations/[a-z0-9-]+/processors/[^/]+(/processorVersions/[^/]+)?$", var.docai_processor))
    error_message = "docai_processor must be empty or projects/{project}/locations/{location}/processors/{id}[/processorVersions/{version}]."
  }
}

# False when the applying identity has no setIamPolicy on the client's project: the
# client then applies the `docai_grant` output themselves (out-of-band).
variable "manage_docai_grant" {
  type        = bool
  description = "Apply the cross-project roles/documentai.apiUser grant on the processor's project (false = the client grants it; see the docai_grant output)."
  default     = true
}

# Email intake (S0-arch-13). Gates the webhook-only SA; false = no SA.
variable "enable_email_ingress" {
  type        = bool
  description = "Create the email intake webhook SA (bridge-email-webhook); false = nothing."
  default     = false
}

variable "email_webhook_account_id" {
  type        = string
  description = "account_id for the email intake webhook service account."
  default     = "bridge-email-webhook"
}
