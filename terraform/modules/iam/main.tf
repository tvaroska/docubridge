# iam — the bridge-runtime workload identity + least-privilege grants.
#
# Realizes "Agent Identity" (design/gcp-substrate.md): a single bridge-runtime
# workload SA the Cloud Run runtime runs as. Grants are exactly the four the
# design names — no editor/owner, no project-wide token-creator (P7-1 plan §6) —
# plus, only when a Document AI processor is configured, roles/documentai.apiUser
# on the processor's (client's) project.

resource "google_service_account" "bridge_runtime" {
  project      = var.project_id
  account_id   = var.service_name
  display_name = "Bridge runtime workload identity"
}

# Base project grants: cloudsql.client (private-IP Cloud SQL, P7-2),
# cloudtasks.enqueuer (the bridge-followups queue, P7-4), aiplatform.user
# (Gemini extraction + Vertex Agent Engine sessions). extra_roles lets later
# primitives extend without editing the module.
resource "google_project_iam_member" "base" {
  for_each = setunion(var.base_roles, var.extra_roles)

  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.bridge_runtime.email}"
}

# Cloud Build identity grants — the SA that `gcloud builds submit` runs as needs
# to read the build source, push the runtime image to Artifact Registry, and write
# build logs. Recent Cloud Build uses the project's compute default SA (not the
# legacy cloudbuild SA), which starts with none of these, so a fresh project's
# first image build 403s without this. Granting the purpose-built
# roles/cloudbuild.builds.builder keeps the deploy repeatable (no out-of-band
# gcloud add-iam-policy-binding). Gated so plan-time/dev can opt out.
data "google_project" "this" {
  project_id = var.project_id
}

locals {
  build_sa_member = "serviceAccount:${data.google_project.this.number}-compute@developer.gserviceaccount.com"
}

resource "google_project_iam_member" "build_sa" {
  for_each = var.enable_build_sa_grants ? var.build_sa_roles : []

  project = var.project_id
  role    = each.value
  member  = local.build_sa_member
}

# Cloud Tasks OIDC token-creator — scoped to the SA resource itself (NOT a
# project-level binding), so the SA can mint OIDC tokens for its own identity
# when Cloud Tasks calls back into the runtime (TODO.md P7 "Cloud Tasks OIDC
# token-creator"; consumed by the tasks module, P7-4).
resource "google_service_account_iam_member" "tasks_token_creator" {
  service_account_id = google_service_account.bridge_runtime.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:${google_service_account.bridge_runtime.email}"
}

# Document AI pre-pass — cross-project, least privilege. The processor lives in the
# CLIENT's project, so the grant is on THAT project (not var.project_id):
# roles/documentai.apiUser (processOnline on its processors), never documentai.admin.
# The processor project may be a project number; IAM accepts either. Deliberately not
# part of base_roles/granted_roles, which are grants on the Bridge's own project.
locals {
  docai_project = var.docai_processor == "" ? "" : regex("^projects/([^/]+)/", var.docai_processor)[0]
  docai_grant = var.docai_processor == "" ? null : {
    project = local.docai_project
    role    = "roles/documentai.apiUser"
    member  = "serviceAccount:${google_service_account.bridge_runtime.email}"
  }
}

resource "google_project_iam_member" "docai_api_user" {
  count = var.docai_processor != "" && var.manage_docai_grant ? 1 : 0

  project = local.docai_project
  role    = "roles/documentai.apiUser"
  member  = "serviceAccount:${google_service_account.bridge_runtime.email}"
}

# Email intake webhook identity (S0-arch-13, design/email-intake.md) — a SEPARATE SA
# for the public, webhook-only Cloud Run service Graph notifies. It holds NO
# project-level roles: its only grants are per-resource (secretAccessor on the two
# Graph secrets, objectCreator on the email landing bucket), so the public service
# can fetch from its own mailbox and create objects — never read the board, the DB,
# Vertex, or any runtime secret. Gated: the default plan creates nothing.
resource "google_service_account" "email_webhook" {
  count = var.enable_email_ingress ? 1 : 0

  project      = var.project_id
  account_id   = var.email_webhook_account_id
  display_name = "Bridge email intake webhook (create-only)"
}
