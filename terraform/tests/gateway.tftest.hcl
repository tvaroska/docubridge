# Credential-free plan-time test of the gateway module (external HTTPS LB →
# Serverless NEG + per-party run.invoker scoping, P7-5). mock_provider = never
# authenticated; command = plan = nothing created. Assertions are on input-derived /
# statically-known values (NEG service/region, forwarding-rule shape, invoker
# role/count, count-gated outputs), never mock-computed ids.
#
# CRITICAL invariant (CRITICAL.md, DECISIONS.md 2026-08-24): this module is INGRESS
# ONLY — it declares NO google_compute_firewall, NO egress rule, NO VPC connector/
# access, and NO datastore path. The Bridge→Cloud SQL connection stays the runtime
# module's private-IP Direct VPC egress, never an HTTP hop. Terraform tests can only
# assert on resources that EXIST, so we assert the positive ingress resources and
# their count-gating; the absence of any egress/firewall/datastore resource is
# enforced by the module header + the T2.8 grep + review.

mock_provider "google" {}
mock_provider "google-beta" {}

run "disabled_creates_nothing" {
  command = plan

  module {
    source = "./modules/gateway"
  }

  variables {
    project_id     = "test-project"
    region         = "us-central1"
    service_name   = "bridge-runtime"
    enable_gateway = false
  }

  # Every resource is count/for_each-gated on enable_gateway → zero when disabled.
  assert {
    condition     = length(google_compute_region_network_endpoint_group.runtime) == 0
    error_message = "disabled gateway must create no Serverless NEG"
  }
  assert {
    condition     = length(google_compute_global_forwarding_rule.runtime) == 0
    error_message = "disabled gateway must create no forwarding rule"
  }
  assert {
    condition     = length(google_compute_backend_service.runtime) == 0
    error_message = "disabled gateway must create no backend service"
  }
  assert {
    condition     = length(google_cloud_run_v2_service_iam_member.party_invoker) == 0
    error_message = "disabled gateway must create no per-party invoker grants"
  }

  # Outputs are "" when disabled (count-gated to zero — statically known at plan).
  assert {
    condition     = output.gateway_ip == ""
    error_message = "gateway_ip must be empty when the gateway is disabled"
  }
  assert {
    condition     = output.gateway_url == ""
    error_message = "gateway_url must be empty when the gateway is disabled"
  }
}

run "enabled_builds_ingress_lb" {
  command = plan

  module {
    source = "./modules/gateway"
  }

  variables {
    project_id     = "test-project"
    region         = "us-central1"
    service_name   = "bridge-runtime"
    enable_gateway = true
  }

  # Serverless NEG routes to the runtime Cloud Run service in-region.
  assert {
    condition     = google_compute_region_network_endpoint_group.runtime[0].cloud_run[0].service == "bridge-runtime"
    error_message = "the Serverless NEG must front the runtime Cloud Run service"
  }
  assert {
    condition     = google_compute_region_network_endpoint_group.runtime[0].region == "us-central1"
    error_message = "the Serverless NEG must be in the runtime region"
  }
  assert {
    condition     = google_compute_region_network_endpoint_group.runtime[0].network_endpoint_type == "SERVERLESS"
    error_message = "the NEG must be a SERVERLESS endpoint group (Cloud Run backend)"
  }

  # External HTTPS forwarding rule — the ingress entry point (port 443, external managed).
  assert {
    condition     = google_compute_global_forwarding_rule.runtime[0].port_range == "443"
    error_message = "the ingress forwarding rule must listen on 443 (HTTPS)"
  }
  assert {
    condition     = google_compute_global_forwarding_rule.runtime[0].load_balancing_scheme == "EXTERNAL_MANAGED"
    error_message = "the forwarding rule must be an external managed HTTPS LB"
  }
  assert {
    condition     = google_compute_backend_service.runtime[0].load_balancing_scheme == "EXTERNAL_MANAGED"
    error_message = "the backend service must be an external managed HTTPS LB"
  }
}

run "per_party_invoker_scoping" {
  command = plan

  module {
    source = "./modules/gateway"
  }

  variables {
    project_id     = "test-project"
    region         = "us-central1"
    service_name   = "bridge-runtime"
    enable_gateway = true
    party_invoker_members = [
      "serviceAccount:carrier-a@x.iam",
      "serviceAccount:carrier-b@x.iam",
    ]
  }

  # Two per-party invoker grants, each roles/run.invoker on the runtime service
  # (a party can address only its own leg — design/zones.md).
  assert {
    condition     = length(google_cloud_run_v2_service_iam_member.party_invoker) == 2
    error_message = "each party_invoker_members entry must get one run.invoker grant"
  }
  assert {
    condition     = google_cloud_run_v2_service_iam_member.party_invoker["serviceAccount:carrier-a@x.iam"].role == "roles/run.invoker"
    error_message = "per-party grant must be roles/run.invoker"
  }
  assert {
    condition     = google_cloud_run_v2_service_iam_member.party_invoker["serviceAccount:carrier-a@x.iam"].name == "bridge-runtime"
    error_message = "per-party grant must scope to the runtime service"
  }
}
