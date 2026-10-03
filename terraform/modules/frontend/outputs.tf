# frontend outputs — the surfaces the deploy step (P7-9) reads. Bucket names feed
# the `gsutil rsync dist/ gs://<bucket>` upload of each app's built assets; the
# portal IP/URL are the external serving surface. All are "" when the frontend is
# disabled (count-gated to zero), so a plan-time test can assert the disabled shape.

output "portal_bucket_name" {
  description = "The provider-portal GCS bucket name — the deploy step (P7-9) rsyncs the built dist/ into it (\"\" when disabled)."
  value       = length(google_storage_bucket.portal) > 0 ? google_storage_bucket.portal[0].name : ""
}

output "console_bucket_name" {
  description = "The agent-console GCS bucket name (private staging) — the deploy step (P7-9) rsyncs the built dist/ into it (\"\" when disabled)."
  value       = length(google_storage_bucket.console) > 0 ? google_storage_bucket.console[0].name : ""
}

output "dashboard_bucket_name" {
  description = "The ops-dashboard GCS bucket name (private staging) — the deploy step (P7-9) rsyncs the built dist/ into it (\"\" when disabled)."
  value       = length(google_storage_bucket.dashboard) > 0 ? google_storage_bucket.dashboard[0].name : ""
}

output "portal_ip" {
  description = "The portal external HTTPS LB anycast IP (\"\" when the frontend is disabled)."
  value       = length(google_compute_global_address.portal) > 0 ? google_compute_global_address.portal[0].address : ""
}

output "portal_url" {
  description = <<-EOT
    The portal public HTTPS URL, derived from the first ssl_domains entry ("" when
    disabled or when no domain is set at plan time). Feeds the provider-portal
    VITE_PORTAL_URL build env at deploy (P7-9), mirroring the gateway module's
    gateway_url.
  EOT
  value       = length(var.ssl_domains) > 0 ? "https://${var.ssl_domains[0]}/" : ""
}
