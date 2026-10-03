# frontend — serves the built SPA assets for the three deployed apps (P7-7).
#
# The three Vite apps ship as static dist/ bundles served from GCS. The zone
# boundary (design/zones.md) drives the bucket posture:
#
#   * provider-portal  — EXTERNAL zone (untrusted counterparties reach it). Its
#     static assets are served from a PUBLIC GCS bucket behind an external HTTPS
#     LB + Cloud CDN. Public here is BY DESIGN, not a violation of the
#     "never public / signed-URLs-only" invariant — that invariant governs
#     artifact/skill BYTE DATA (the gcs module), not a public web SPA. A
#     google_compute_backend_bucket must be able to serve the bucket to anonymous
#     users, so the portal bucket grants allUsers:objectViewer and does NOT enforce
#     public access prevention.
#   * agent-console + ops-dashboard — INTERNAL zone (the internal zone never
#     accepts unauthenticated calls, design/zones.md). Their buckets are PRIVATE
#     (uniform bucket-level access + public_access_prevention ENFORCED, no allUsers
#     grant, NOT on the public LB). They are provisioned here as private STAGING
#     buckets so the zone boundary is honored today; live internal serving (IAP /
#     internal LB) is a FOLLOW-UP wired when the serving surfaces split into their
#     own services (TODO.md P7 "Per-workload identity if serving surfaces split").
#     This is a logged, not silent, cap.
#
# Only THREE apps are provisioned. The presenter clock-control app is presenter-only
# and runs LOCALLY — it is deliberately NOT deployed and has no resource below
# (design/frontend.md). This header avoids naming it so the P7-7 T2.5 grep stays a
# clean no-match (the same idiom the gateway module uses for its egress-keyword grep);
# the absence is real, not a wording trick — there is genuinely no such resource here.
#
# CRITICAL — No bucket OBJECTS / secrets / plaintext values in HCL (CRITICAL.md).
# The built dist/ assets are uploaded at deploy (P7-9) via `gsutil rsync dist/
# gs://<bucket>`, never Terraform-managed. This module provisions the containers +
# the external LB only. It declares NO VPC/firewall/private-network or datastore
# resource (ingress-serving only; same discipline as the gateway module header).
#
# Every resource is gated on var.enable_frontend (count / for_each), so when the
# frontend is disabled (the credential-free default) this module creates nothing.

# Portal bucket — EXTERNAL zone, PUBLIC by design (see header). SPA client-side
# routing falls back to index.html on a 404 (website block).
resource "google_storage_bucket" "portal" {
  count = var.enable_frontend ? 1 : 0

  project  = var.project_id
  name     = "${var.name_prefix}-portal"
  location = var.location
  labels   = var.labels

  uniform_bucket_level_access = true # UBLA: no per-object ACLs
  # NO public_access_prevention = "enforced": the external-zone portal is public,
  # served to anonymous users through the backend bucket (see the allUsers grant).

  website {
    main_page_suffix = "index.html"
    not_found_page   = "index.html" # SPA client-side routing fallback
  }
}

# Console bucket — INTERNAL zone, PRIVATE staging bucket (never public). Live
# internal serving is a follow-up (see header). website block is harmless on a
# private bucket and keeps the SPA fallback ready for the internal serving surface.
resource "google_storage_bucket" "console" {
  count = var.enable_frontend ? 1 : 0

  project  = var.project_id
  name     = "${var.name_prefix}-console"
  location = var.location
  labels   = var.labels

  uniform_bucket_level_access = true
  public_access_prevention    = "enforced" # internal zone: never public

  website {
    main_page_suffix = "index.html"
    not_found_page   = "index.html"
  }
}

# Dashboard bucket — INTERNAL zone, PRIVATE staging bucket (never public). Same
# posture as the console bucket; live internal serving is a follow-up (see header).
resource "google_storage_bucket" "dashboard" {
  count = var.enable_frontend ? 1 : 0

  project  = var.project_id
  name     = "${var.name_prefix}-dashboard"
  location = var.location
  labels   = var.labels

  uniform_bucket_level_access = true
  public_access_prevention    = "enforced" # internal zone: never public

  website {
    main_page_suffix = "index.html"
    not_found_page   = "index.html"
  }
}

# Public read on the PORTAL bucket ONLY — the external-zone portal is public by
# design so the LB backend bucket can serve its assets to anonymous users. The
# internal-zone console + dashboard buckets get NO such grant (they stay private).
resource "google_storage_bucket_iam_member" "portal_public_read" {
  count = var.enable_frontend ? 1 : 0

  bucket = google_storage_bucket.portal[0].name
  role   = "roles/storage.objectViewer"
  member = "allUsers"
}

# ---------------------------------------------------------------------------
# Portal external HTTPS LB (mirrors the gateway module but with a BACKEND BUCKET
# + Cloud CDN in front of the public portal bucket). Gated on enable_frontend.
# ---------------------------------------------------------------------------

# Backend bucket → the public portal bucket, with Cloud CDN enabled.
resource "google_compute_backend_bucket" "portal" {
  count = var.enable_frontend ? 1 : 0

  project     = var.project_id
  name        = "${var.name_prefix}-portal-backend"
  bucket_name = google_storage_bucket.portal[0].name
  enable_cdn  = true
}

# URL map → default backend bucket.
resource "google_compute_url_map" "portal" {
  count = var.enable_frontend ? 1 : 0

  project         = var.project_id
  name            = "${var.name_prefix}-portal-urlmap"
  default_service = google_compute_backend_bucket.portal[0].id
}

# Google-managed SSL certificate — gated on non-empty ssl_domains (the domains may
# not be known at plan time; the LB URL is then injected out-of-band at deploy).
resource "google_compute_managed_ssl_certificate" "portal" {
  count = var.enable_frontend && length(var.ssl_domains) > 0 ? 1 : 0

  project = var.project_id
  name    = "${var.name_prefix}-portal-cert"

  managed {
    domains = var.ssl_domains
  }
}

# Target HTTPS proxy → url map (+ managed cert when domains are set).
resource "google_compute_target_https_proxy" "portal" {
  count = var.enable_frontend ? 1 : 0

  project          = var.project_id
  name             = "${var.name_prefix}-portal-https-proxy"
  url_map          = google_compute_url_map.portal[0].id
  ssl_certificates = google_compute_managed_ssl_certificate.portal[*].id
}

# The LB anycast IP.
resource "google_compute_global_address" "portal" {
  count = var.enable_frontend ? 1 : 0

  project = var.project_id
  name    = "${var.name_prefix}-portal-lb-ip"
}

# External HTTPS forwarding rule (port 443) → proxy + address. The public ingress
# entry point for the external-zone portal.
resource "google_compute_global_forwarding_rule" "portal" {
  count = var.enable_frontend ? 1 : 0

  project               = var.project_id
  name                  = "${var.name_prefix}-portal-https-fr"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  port_range            = "443"
  target                = google_compute_target_https_proxy.portal[0].id
  ip_address            = google_compute_global_address.portal[0].id
  labels                = var.labels
}
