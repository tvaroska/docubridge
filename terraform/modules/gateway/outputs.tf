# gateway outputs — the ingress surface the deploy step (P7-9) reads. Both are ""
# when the gateway is disabled (count-gated to zero), so a plan-time test can
# assert the disabled shape statically.

output "gateway_ip" {
  description = "The external HTTPS LB anycast IP (\"\" when the gateway is disabled)."
  value       = length(google_compute_global_address.runtime) > 0 ? google_compute_global_address.runtime[0].address : ""
}

output "gateway_url" {
  description = <<-EOT
    The Gateway-authorized public HTTPS URL, derived from the first ssl_domains
    entry ("" when disabled or when no domain is set at plan time). This is the
    origin consumers must fetch the Bridge Agent Card from (adk >=2.7 same-origin
    validation) and the value that feeds BRIDGE_PUBLIC_URL (Deliverable D).
  EOT
  value       = length(var.ssl_domains) > 0 ? "https://${var.ssl_domains[0]}/" : ""
}
