output "network_id" {
  description = "The VPC self_link/id (consumed by the runtime module's VPC egress)."
  value       = google_compute_network.vpc.id
}

output "network_name" {
  description = "The VPC name."
  value       = google_compute_network.vpc.name
}

output "network_self_link" {
  description = "The VPC self_link."
  value       = google_compute_network.vpc.self_link
}

output "external_subnet_id" {
  description = "The external (DMZ/ingress) subnet id."
  value       = google_compute_subnetwork.external.id
}

output "internal_subnet_id" {
  description = "The internal (trusted) subnet id — the runtime's Direct VPC egress target."
  value       = google_compute_subnetwork.internal.id
}

output "internal_subnet_self_link" {
  description = "The internal subnet self_link — the private-IP / PSA anchor Cloud SQL attaches to (P7-2)."
  value       = google_compute_subnetwork.internal.self_link
}

output "external_subnet_cidr" {
  description = "The external subnet CIDR."
  value       = google_compute_subnetwork.external.ip_cidr_range
}

output "internal_subnet_cidr" {
  description = "The internal subnet CIDR."
  value       = google_compute_subnetwork.internal.ip_cidr_range
}

output "agent_engine_network_attachment_id" {
  description = "The PSC network attachment id for Vertex AI Agent Engine private VPC egress (\"\" when enable_agent_engine_psc = false)."
  value       = var.enable_agent_engine_psc ? google_compute_network_attachment.agent_engine_psc[0].id : ""
}
