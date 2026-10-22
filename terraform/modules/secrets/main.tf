# secrets — the Secret Manager CONTAINER for the DB DSN + the runtime's read grant.
#
# CRITICAL — No secrets in HCL (CRITICAL.md, P7-2 plan §6). This module creates
# the secret CONTAINER only. There is deliberately NO
# `google_secret_manager_secret_version` here: the version carries the actual DSN
# value (which embeds the DB credential), so it is injected OUT-OF-BAND by the
# deploy step (P7-9) — never a plaintext DSN in any .tf/.tfvars. The runtime reads
# the latest version as the DATABASE_URL secret env (the runtime module's dynamic
# env block), and connects over private IP (DECISIONS.md 2026-08-24).

resource "google_secret_manager_secret" "db_url" {
  project   = var.project_id
  secret_id = var.secret_id
  labels    = var.labels

  replication {
    auto {}
  }
}

# Least-privilege: secretAccessor on THIS secret only, to the bridge-runtime SA.
# Not a project-wide secretAccessor; roles/cloudsql.client is already on the SA
# (iam module base_roles) — this is the single new grant P7-2 adds.
resource "google_secret_manager_secret_iam_member" "runtime_accessor" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.db_url.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = var.service_account_member
}

# Email intake secrets (S0-arch-13, design/email-intake.md) — CONTAINERS ONLY, gated.
# Same rule as the DSN: no google_secret_manager_secret_version anywhere; the values
# (the Graph client secret, the webhook clientState, the intake-token HMAC key) are
# added out-of-band (`gcloud secrets versions add … --data-file=-`). Each accessor is
# per-secret and to exactly one identity: the two Graph secrets to the public webhook
# SA only, the token key to the bridge-runtime SA only (the webhook never mints or
# checks tokens; the runtime never talks to Graph).
locals {
  email_webhook_secrets = var.enable_email_ingress ? {
    graph_client_secret = var.email_graph_client_secret_id
    client_state        = var.email_client_state_secret_id
  } : {}
}

resource "google_secret_manager_secret" "email_webhook" {
  for_each = local.email_webhook_secrets

  project   = var.project_id
  secret_id = each.value
  labels    = var.labels

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_iam_member" "email_webhook_accessor" {
  for_each = local.email_webhook_secrets

  project   = var.project_id
  secret_id = google_secret_manager_secret.email_webhook[each.key].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = var.email_webhook_member
}

resource "google_secret_manager_secret" "email_token" {
  count = var.enable_email_ingress ? 1 : 0

  project   = var.project_id
  secret_id = var.email_token_secret_id
  labels    = var.labels

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_iam_member" "email_token_accessor" {
  count = var.enable_email_ingress ? 1 : 0

  project   = var.project_id
  secret_id = google_secret_manager_secret.email_token[0].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = var.service_account_member
}

# SPIFFE JWT-SVID signing key/secret container (Zone 6 Identity Governance).
# As with db_url, only the container + per-secret secretAccessor grant are
# created in HCL; the key material is added out-of-band at deploy time.
resource "google_secret_manager_secret" "spiffe_svid" {
  project   = var.project_id
  secret_id = var.spiffe_svid_secret_id
  labels    = var.labels

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_iam_member" "spiffe_svid_accessor" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.spiffe_svid.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = var.service_account_member
}
