# Credential-free plan-time test of the network module (the two-zone trust
# boundary). mock_provider means the google provider is never authenticated;
# command = plan means nothing is created. Asserts on input-derived values, not
# opaque mocked ids (P7-1 plan §2b).

mock_provider "google" {}
mock_provider "google-beta" {}

run "two_zone_subnets" {
  command = plan

  module {
    source = "./modules/network"
  }

  variables {
    project_id = "test-project"
    region     = "us-central1"
  }

  assert {
    condition     = google_compute_network.vpc.auto_create_subnetworks == false
    error_message = "network must be custom-mode (auto_create_subnetworks = false)"
  }

  assert {
    condition     = google_compute_subnetwork.external.ip_cidr_range != google_compute_subnetwork.internal.ip_cidr_range
    error_message = "external and internal subnets must not share a CIDR"
  }

  assert {
    condition     = google_compute_subnetwork.external.private_ip_google_access == true
    error_message = "external subnet must enable private Google access"
  }

  assert {
    condition     = google_compute_subnetwork.internal.private_ip_google_access == true
    error_message = "internal subnet must enable private Google access"
  }

  # The zone-isolation firewall denies external -> internal (the trust boundary).
  assert {
    condition     = google_compute_firewall.isolate_internal.destination_ranges == toset([google_compute_subnetwork.internal.ip_cidr_range])
    error_message = "isolation rule must target the internal subnet range"
  }

  assert {
    condition     = google_compute_firewall.isolate_internal.source_ranges == toset([google_compute_subnetwork.external.ip_cidr_range])
    error_message = "isolation rule must deny traffic sourced from the external subnet"
  }
}
