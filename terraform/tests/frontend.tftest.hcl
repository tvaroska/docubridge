# Credential-free plan-time test of the frontend module (deployed SPA surfaces,
# P7-7). mock_provider = never authenticated; command = plan = nothing created.
# Assertions are on input-derived / statically-known values (bucket names, config
# booleans, IAM role/member, forwarding-rule count), never mock-computed ids.
#
# Load-bearing ZONE boundary (design/zones.md): the external-zone provider-portal is
# PUBLIC by design (allUsers:objectViewer, public access prevention NOT enforced)
# behind a CDN-enabled backend bucket + external HTTPS forwarding rule; the
# internal-zone agent-console + ops-dashboard buckets are PRIVATE
# (public_access_prevention = "enforced", no public grant). An implementer who
# accidentally makes the internal buckets public — or the portal bucket private —
# fails this test.

mock_provider "google" {}
mock_provider "google-beta" {}

run "disabled_creates_nothing" {
  command = plan

  module {
    source = "./modules/frontend"
  }

  variables {
    project_id      = "test-project"
    enable_frontend = false
  }

  # Every resource is count/for_each-gated on enable_frontend → zero when disabled.
  assert {
    condition     = length(google_storage_bucket.portal) == 0
    error_message = "disabled frontend must create no portal bucket"
  }
  assert {
    condition     = length(google_storage_bucket.console) == 0
    error_message = "disabled frontend must create no console bucket"
  }
  assert {
    condition     = length(google_storage_bucket.dashboard) == 0
    error_message = "disabled frontend must create no dashboard bucket"
  }
  assert {
    condition     = length(google_storage_bucket_iam_member.portal_public_read) == 0
    error_message = "disabled frontend must create no public-read grant"
  }
  assert {
    condition     = length(google_compute_global_forwarding_rule.portal) == 0
    error_message = "disabled frontend must create no forwarding rule"
  }

  # Outputs are "" when disabled (count-gated to zero — statically known at plan).
  assert {
    condition     = output.portal_bucket_name == ""
    error_message = "portal_bucket_name must be empty when the frontend is disabled"
  }
  assert {
    condition     = output.portal_ip == ""
    error_message = "portal_ip must be empty when the frontend is disabled"
  }
}

run "enabled_serves_portal_public_and_internal_private" {
  command = plan

  module {
    source = "./modules/frontend"
  }

  variables {
    project_id      = "test-project"
    enable_frontend = true
    name_prefix     = "bridge-runtime"
    ssl_domains     = ["portal.example.com"]
  }

  # Bucket names track the prefix (input-derived, statically known).
  assert {
    condition     = google_storage_bucket.portal[0].name == "bridge-runtime-portal"
    error_message = "portal bucket name must be <name_prefix>-portal"
  }
  assert {
    condition     = google_storage_bucket.console[0].name == "bridge-runtime-console"
    error_message = "console bucket name must be <name_prefix>-console"
  }
  assert {
    condition     = google_storage_bucket.dashboard[0].name == "bridge-runtime-dashboard"
    error_message = "dashboard bucket name must be <name_prefix>-dashboard"
  }

  # EXTERNAL zone: the portal bucket grants allUsers:objectViewer (public read).
  # NOTE: the portal bucket deliberately leaves public_access_prevention UNSET (a
  # public web asset, unlike the gcs artifact/skill buckets). That attribute is then
  # provider-computed and unknown at plan time, so it cannot be asserted here; the
  # public posture is proven by this allUsers grant (a PAP-enforced bucket cannot
  # carry an allUsers IAM grant) contrasted with the console/dashboard enforced-PAP
  # assertions below. See the module header + the T2 zone-boundary criterion.
  assert {
    condition     = google_storage_bucket_iam_member.portal_public_read[0].member == "allUsers"
    error_message = "the portal bucket must grant read to allUsers (external-zone public serving)"
  }
  assert {
    condition     = google_storage_bucket_iam_member.portal_public_read[0].role == "roles/storage.objectViewer"
    error_message = "the portal public grant must be roles/storage.objectViewer"
  }

  # INTERNAL zone: console + dashboard buckets DO enforce public access prevention
  # (the internal zone never accepts unauthenticated calls — design/zones.md).
  assert {
    condition     = google_storage_bucket.console[0].public_access_prevention == "enforced"
    error_message = "the internal-zone console bucket must enforce public access prevention (never public)"
  }
  assert {
    condition     = google_storage_bucket.dashboard[0].public_access_prevention == "enforced"
    error_message = "the internal-zone dashboard bucket must enforce public access prevention (never public)"
  }

  # UBLA on all three buckets (no per-object ACLs).
  assert {
    condition     = google_storage_bucket.portal[0].uniform_bucket_level_access == true
    error_message = "portal bucket must enable uniform bucket-level access"
  }
  assert {
    condition     = google_storage_bucket.console[0].uniform_bucket_level_access == true
    error_message = "console bucket must enable uniform bucket-level access"
  }
  assert {
    condition     = google_storage_bucket.dashboard[0].uniform_bucket_level_access == true
    error_message = "dashboard bucket must enable uniform bucket-level access"
  }

  # The portal is fronted by a CDN-enabled backend bucket.
  assert {
    condition     = google_compute_backend_bucket.portal[0].enable_cdn == true
    error_message = "the portal backend bucket must enable Cloud CDN"
  }
  assert {
    condition     = google_compute_backend_bucket.portal[0].bucket_name == "bridge-runtime-portal"
    error_message = "the portal backend bucket must front the portal bucket"
  }

  # An external HTTPS forwarding rule exists (the public ingress entry point).
  assert {
    condition     = length(google_compute_global_forwarding_rule.portal) == 1
    error_message = "the enabled portal must create one external HTTPS forwarding rule"
  }
  assert {
    condition     = google_compute_global_forwarding_rule.portal[0].port_range == "443"
    error_message = "the portal forwarding rule must listen on 443 (HTTPS)"
  }
  assert {
    condition     = google_compute_global_forwarding_rule.portal[0].load_balancing_scheme == "EXTERNAL_MANAGED"
    error_message = "the portal forwarding rule must be an external managed HTTPS LB"
  }

  # The managed cert is provisioned when ssl_domains is non-empty.
  assert {
    condition     = length(google_compute_managed_ssl_certificate.portal) == 1
    error_message = "a managed cert must be provisioned when ssl_domains is set"
  }

  # portal_url derives from the first ssl_domains entry.
  assert {
    condition     = output.portal_url == "https://portal.example.com/"
    error_message = "portal_url must derive from the first ssl_domains entry"
  }
}
