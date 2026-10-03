# network — the two-zone trust boundary at the network layer (design/zones.md).
#
# A custom-mode VPC with two subnets modelling the two zones (external/DMZ
# ingress ↔ internal/trusted) plus a firewall rule isolating the internal zone
# from the external zone. Isolation is enforced at the network layer only, not by
# application-level tenant scoping (design/zones.md). This is a "substrate
# primitive" module (design/gcp-substrate.md §"Terraform: substrate primitives")
# — generic VPC + subnets, not a per-platform-service module.

resource "google_compute_network" "vpc" {
  project                 = var.project_id
  name                    = var.network_name
  auto_create_subnetworks = false # custom mode: subnets declared explicitly below
}

# External (untrusted) zone — where external party/provider agents + host portals
# enter through the Gateway (design/zones.md). private_ip_google_access lets
# workloads reach Google APIs without external IPs.
resource "google_compute_subnetwork" "external" {
  project                  = var.project_id
  name                     = "${var.network_name}-external"
  region                   = var.region
  network                  = google_compute_network.vpc.id
  ip_cidr_range            = var.external_subnet_cidr
  private_ip_google_access = true
}

# Internal (trusted) zone — the backend/servicer side. The private-IP path to
# Cloud SQL (P7-2, via Private Services Access) attaches here; the runtime's
# Direct VPC egress lands here so datastore traffic stays private-IP and never
# rides an HTTP hop (DECISIONS.md 2026-08-24 vibeflix-audit).
resource "google_compute_subnetwork" "internal" {
  project                  = var.project_id
  name                     = "${var.network_name}-internal"
  region                   = var.region
  network                  = google_compute_network.vpc.id
  ip_cidr_range            = var.internal_subnet_cidr
  private_ip_google_access = true
}

# Zone isolation (deny): the external zone must NOT be able to open connections
# into the internal (trusted) zone directly — that is the trust boundary. A
# higher priority than the allow rules below so it wins.
resource "google_compute_firewall" "isolate_internal" {
  project   = var.project_id
  name      = "${var.network_name}-deny-external-to-internal"
  network   = google_compute_network.vpc.id
  direction = "INGRESS"
  priority  = 900

  deny {
    protocol = "all"
  }

  source_ranges      = [var.external_subnet_cidr]
  destination_ranges = [var.internal_subnet_cidr]
}

# Allow internal-to-internal traffic so trusted-zone workloads (and the runtime's
# private egress) can reach the private-IP datastore path.
resource "google_compute_firewall" "allow_internal" {
  project   = var.project_id
  name      = "${var.network_name}-allow-internal"
  network   = google_compute_network.vpc.id
  direction = "INGRESS"
  priority  = 1000

  allow {
    protocol = "all"
  }

  source_ranges      = [var.internal_subnet_cidr]
  destination_ranges = [var.internal_subnet_cidr]
}
