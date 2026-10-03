output "secret_id" {
  description = "The DSN secret id — the runtime references this for the DATABASE_URL secret env."
  value       = google_secret_manager_secret.db_url.secret_id
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
