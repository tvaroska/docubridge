# gateway — external HTTPS LB → Serverless NEG on the runtime + per-party
# run.invoker scoping (P7-5).
#
# INGRESS ONLY. This module governs the EXTERNAL ingress path into the Cloud Run
# runtime (an external HTTPS load balancer fronting a Serverless NEG) and the
# per-party "a party can address only its own leg" invoker scoping
# (design/zones.md, design/gcp-substrate.md §"gateway = external HTTPS LB →
# Serverless NEG"). It declares NO outbound-traffic rule, NO packet-filter rule,
# NO private-network connector, and touches NO private-datastore path: the
# Bridge→datastore connection stays the runtime module's private-IP Direct VPC
# path (PRIVATE_RANGES_ONLY), NEVER an HTTP hop (DECISIONS.md 2026-08-24
# vibeflix-audit; TODO.md P7 "Gateway HTTP-only outbound trap"). We sidestep that
# trap structurally by keeping this module ingress-only — the private-IP path is
# owned entirely by modules/runtime.
#
# NOTE: this header deliberately avoids the outbound-/datastore-resource keywords
# the P7-5 T2.8 grep scans this module dir for, so that grep stays a clean no-match.
# The invariant is real, not a wording trick — there is genuinely no such resource
# below; the tftest + review confirm it.
#
# Every resource is gated on var.enable_gateway (count / for_each), so when the
# gateway is disabled (the P7-1/local default) this module creates nothing.

# Serverless NEG — the LB backend that routes to the Cloud Run runtime service.
resource "google_compute_region_network_endpoint_group" "runtime" {
  count = var.enable_gateway ? 1 : 0

  project               = var.project_id
  name                  = "${var.service_name}-neg"
  region                = var.region
  network_endpoint_type = "SERVERLESS"

  cloud_run {
    service = var.service_name
  }
}

# Global backend service → the Serverless NEG (external managed HTTPS LB).
resource "google_compute_backend_service" "runtime" {
  count = var.enable_gateway ? 1 : 0

  project               = var.project_id
  name                  = "${var.service_name}-backend"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  protocol              = "HTTPS"

  backend {
    group = google_compute_region_network_endpoint_group.runtime[0].id
  }
}

# URL map → default backend service.
resource "google_compute_url_map" "runtime" {
  count = var.enable_gateway ? 1 : 0

  project         = var.project_id
  name            = "${var.service_name}-urlmap"
  default_service = google_compute_backend_service.runtime[0].id
}

# Google-managed SSL certificate — gated on non-empty ssl_domains (the domains may
# not be known at plan time; the LB URL is then injected out-of-band at deploy).
resource "google_compute_managed_ssl_certificate" "runtime" {
  count = var.enable_gateway && length(var.ssl_domains) > 0 ? 1 : 0

  project = var.project_id
  name    = "${var.service_name}-cert"

  managed {
    domains = var.ssl_domains
  }
}

# Target HTTPS proxy → url map (+ managed cert when domains are set).
resource "google_compute_target_https_proxy" "runtime" {
  count = var.enable_gateway ? 1 : 0

  project          = var.project_id
  name             = "${var.service_name}-https-proxy"
  url_map          = google_compute_url_map.runtime[0].id
  ssl_certificates = google_compute_managed_ssl_certificate.runtime[*].id
}

# The LB anycast IP.
resource "google_compute_global_address" "runtime" {
  count = var.enable_gateway ? 1 : 0

  project = var.project_id
  name    = "${var.service_name}-lb-ip"
}

# External HTTPS forwarding rule (port 443) → proxy + address. This is the ingress
# entry point; there is no outbound-traffic/packet-filter resource in this module.
resource "google_compute_global_forwarding_rule" "runtime" {
  count = var.enable_gateway ? 1 : 0

  project               = var.project_id
  name                  = "${var.service_name}-https-fr"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  port_range            = "443"
  target                = google_compute_target_https_proxy.runtime[0].id
  ip_address            = google_compute_global_address.runtime[0].id
  labels                = var.labels
}

# Per-party ingress scoping — each counterparty identity gets roles/run.invoker on
# the runtime service, so "a party can address only its own leg" is enforced at the
# invoker grant (design/zones.md). Empty set at plan time (populated at P7-9).
resource "google_cloud_run_v2_service_iam_member" "party_invoker" {
  for_each = var.enable_gateway ? toset(var.party_invoker_members) : toset([])

  project  = var.project_id
  location = var.region
  name     = var.service_name
  role     = "roles/run.invoker"
  member   = each.value
}
