output "service_name" {
  description = "The Cloud Run service name."
  value       = google_cloud_run_v2_service.bridge.name
}

output "service_uri" {
  description = "The Cloud Run service URI."
  value       = google_cloud_run_v2_service.bridge.uri
}

# Echoes the computed-ingress ternary so a plan-time test can assert both
# enable_gateway branches without an apply (see runtime.tftest.hcl).
output "computed_ingress" {
  description = "The ingress mode computed from enable_gateway."
  value       = local.ingress
}

output "email_webhook_uri" {
  description = "The email intake webhook service URI (\"\" when enable_email_ingress = false); the Graph subscription notificationUrl is <uri>/email/graph/notifications."
  value       = var.enable_email_ingress ? google_cloud_run_v2_service.email_webhook[0].uri : ""
}

output "rfp_board_uri" {
  description = "The internal RFP board host service URI (\"\" when enable_rfp_board = false)."
  value       = var.enable_rfp_board ? google_cloud_run_v2_service.rfp_board[0].uri : ""
}

output "email_drain_job" {
  description = "The Cloud Scheduler job driving POST /email/drain on the RFP board (\"\" unless enable_rfp_board && enable_email_ingress)."
  value       = var.enable_rfp_board && var.enable_email_ingress ? google_cloud_scheduler_job.email_drain[0].name : ""
}
