output "skills_bucket_name" {
  description = "The Skill Registry bucket name — injected as the BRIDGE_SKILLS_BUCKET runtime env."
  value       = google_storage_bucket.skills.name
}

output "artifacts_bucket_name" {
  description = "The Artifacts bucket name — injected as the BRIDGE_ARTIFACT_BUCKET runtime env (GcsArtifactService + signed URLs)."
  value       = google_storage_bucket.artifacts.name
}

output "email_bucket_name" {
  description = "The email landing bucket name — injected as BRIDGE_EMAIL_BUCKET on the runtime + the webhook (\"\" when disabled)."
  value       = var.enable_email_ingress ? google_storage_bucket.email[0].name : ""
}

output "audit_vault_bucket_name" {
  description = "The immutable WORM Audit Vault bucket name — injected as BRIDGE_VAULT_BUCKET on the runtime (\"\" when disabled)."
  value       = var.enable_audit_vault ? google_storage_bucket.audit_vault[0].name : ""
}

output "scenarios_bucket_name" {
  description = "The Cloud Storage Scenarios bucket name — injected as BRIDGE_SCENARIOS_DIR=gs://<bucket>/ on runtime services."
  value       = google_storage_bucket.scenarios.name
}
