output "shared_bucket_name" {
  description = "Shared single-bucket GCS name (gs://<project_id>) when single_bucket_mode = true (\"\" in multi-bucket mode)."
  value       = local.shared_bucket_name
}

output "shared_bucket_lister_role_name" {
  description = "Custom list-only IAM role resource name (storage.objects.list) created in single_bucket_mode (\"\" in multi-bucket mode)."
  value       = var.single_bucket_mode ? google_project_iam_custom_role.shared_bucket_object_lister[0].name : ""
}

output "skills_bucket_name" {
  description = "The Skill Registry bucket name — injected as the BRIDGE_SKILLS_BUCKET runtime env."
  value       = local.skills_bucket_name
}

output "skills_prefix" {
  description = "Object prefix inside the Skill Registry bucket (\"skills\" in single_bucket_mode, \"\" in multi-bucket mode)."
  value       = var.single_bucket_mode ? "skills" : ""
}

output "artifacts_bucket_name" {
  description = "The Artifacts bucket name — injected as the BRIDGE_ARTIFACT_BUCKET runtime env (GcsArtifactService + signed URLs)."
  value       = local.artifacts_bucket_name
}

output "artifacts_prefix" {
  description = "Object prefix for ADK GcsArtifactService (\"document_bridge\" in single_bucket_mode, \"\" in multi-bucket mode)."
  value       = var.single_bucket_mode ? "document_bridge" : ""
}

output "email_bucket_name" {
  description = "The email landing bucket name — injected as BRIDGE_EMAIL_BUCKET on the runtime + the webhook (\"\" when disabled)."
  value       = local.email_bucket_name
}

output "email_prefix" {
  description = "Object prefix inside the email landing bucket (\"email\" in single_bucket_mode when enabled, \"\" otherwise)."
  value       = var.single_bucket_mode && var.enable_email_ingress ? "email" : ""
}

output "audit_vault_bucket_name" {
  description = "The immutable WORM Audit Vault bucket name — injected as BRIDGE_VAULT_BUCKET on the runtime (\"\" when disabled)."
  value       = local.audit_vault_bucket_name
}

output "audit_vault_prefix" {
  description = "Object prefix inside the WORM Audit Vault bucket (\"audit-vault\" in single_bucket_mode when enabled, \"\" otherwise)."
  value       = var.single_bucket_mode && var.enable_audit_vault ? "audit-vault" : ""
}

output "scenarios_bucket_name" {
  description = "The Cloud Storage Scenarios bucket name — injected as BRIDGE_SCENARIOS_DIR on runtime services."
  value       = local.scenarios_bucket_name
}

output "scenarios_prefix" {
  description = "Object prefix inside the Scenarios bucket (\"scenarios\" in single_bucket_mode, \"\" in multi-bucket mode)."
  value       = var.single_bucket_mode ? "scenarios" : ""
}
