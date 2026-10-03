output "bridge_runtime_email" {
  description = "Email of the bridge-runtime workload SA (the runtime runs as this identity)."
  value       = google_service_account.bridge_runtime.email
}

output "bridge_runtime_id" {
  description = "Fully-qualified id of the bridge-runtime SA."
  value       = google_service_account.bridge_runtime.id
}

output "bridge_runtime_member" {
  description = "IAM member string for the SA — reused by tasks/database modules (P7-2/P7-4)."
  value       = "serviceAccount:${google_service_account.bridge_runtime.email}"
}

output "granted_roles" {
  description = "The set of project roles granted to the bridge-runtime SA."
  value       = setunion(var.base_roles, var.extra_roles)
}

output "build_sa_member" {
  description = "IAM member string for the Cloud Build (compute default) SA."
  value       = local.build_sa_member
}

output "build_sa_roles" {
  description = "Roles granted to the Cloud Build SA (empty when grants are disabled)."
  value       = var.enable_build_sa_grants ? var.build_sa_roles : toset([])
}

output "docai_grant" {
  description = "The cross-project Document AI grant { project, role, member } the runtime SA needs on the processor's project (null without a processor). Hand it to the client when manage_docai_grant = false."
  value       = local.docai_grant
}

output "email_webhook_email" {
  description = "Email of the email intake webhook SA (\"\" when enable_email_ingress = false)."
  value       = var.enable_email_ingress ? one(google_service_account.email_webhook[*].email) : ""
}

output "email_webhook_member" {
  description = "IAM member string for the email intake webhook SA (\"\" when disabled)."
  value       = var.enable_email_ingress ? "serviceAccount:${one(google_service_account.email_webhook[*].email)}" : ""
}
