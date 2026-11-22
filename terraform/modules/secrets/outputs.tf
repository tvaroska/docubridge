output "secret_id" {
  description = "The DSN secret id — the runtime references this for the DATABASE_URL secret env."
  value       = google_secret_manager_secret.db_url.secret_id
  depends_on = [
    google_secret_manager_secret_iam_member.runtime_accessor,
    google_secret_manager_secret_version.db_url,
  ]
}

output "secret_name" {
  description = "The fully-qualified secret resource name."
  value       = google_secret_manager_secret.db_url.name
}

output "email_graph_client_secret_id" {
  description = "The Graph client-secret secret id (\"\" when email intake is disabled)."
  value       = var.enable_email_ingress ? google_secret_manager_secret.email_webhook["graph_client_secret"].secret_id : ""
}

output "email_client_state_secret_id" {
  description = "The Graph clientState secret id (\"\" when email intake is disabled)."
  value       = var.enable_email_ingress ? google_secret_manager_secret.email_webhook["client_state"].secret_id : ""
}

output "email_token_secret_id" {
  description = "The intake-token HMAC key secret id (\"\" when email intake is disabled)."
  value       = var.enable_email_ingress ? google_secret_manager_secret.email_token[0].secret_id : ""
}

output "spiffe_svid_secret_id" {
  description = "The SPIFFE JWT-SVID signing key secret id (injected as BRIDGE_SPIFFE_SVID_SECRET)."
  value       = google_secret_manager_secret.spiffe_svid.secret_id
  depends_on = [
    google_secret_manager_secret_iam_member.spiffe_svid_accessor,
    google_secret_manager_secret_version.spiffe_svid,
  ]
}

output "user_jwt_secret_id" {
  description = "The human session JWT signing key secret id (injected as BRIDGE_USER_JWT_SECRET)."
  value       = google_secret_manager_secret.user_jwt.secret_id
  depends_on = [
    google_secret_manager_secret_iam_member.user_jwt_accessor,
    google_secret_manager_secret_version.user_jwt,
  ]
}

output "demo_passcode_secret_id" {
  description = "The interactive demo passcode secret id (injected as BRIDGE_DEMO_PASSCODE; \"\" when disabled)."
  value       = var.enable_demo_passcode ? google_secret_manager_secret.demo_passcode[0].secret_id : ""
  depends_on = [
    google_secret_manager_secret_iam_member.demo_passcode_accessor,
    google_secret_manager_secret_version.demo_passcode,
  ]
}
