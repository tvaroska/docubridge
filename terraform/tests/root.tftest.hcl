# Credential-free integration plan of the ROOT module — proves the three
# substrate primitives wire together (network -> iam -> runtime) and the root
# outputs resolve, all with no credentials (mock_provider) and no apply.

mock_provider "google" {}
mock_provider "google-beta" {}

run "root_wires_three_modules" {
  command = plan

  variables {
    project_id = "test-project"
    region     = "us-central1"
  }

  assert {
    condition     = output.runtime_service_name == "bridge-runtime"
    error_message = "root must expose the runtime service name"
  }

  # Default (enable_gateway = false) is the local/dev ingress posture.
  assert {
    condition     = output.runtime_ingress == "INGRESS_TRAFFIC_ALL"
    error_message = "default enable_gateway = false must yield INGRESS_TRAFFIC_ALL"
  }

  # The runtime runs as the SA the iam module minted (the wiring port). Assert
  # on the input-derived service_name rather than the provider-computed SA email
  # (opaque at plan time under mock_provider, P7-1 plan §2b).
  assert {
    condition     = module.runtime.service_name == var.service_name
    error_message = "runtime service name must track service_name"
  }
}

run "root_gateway_enabled_locks_ingress" {
  command = plan

  variables {
    project_id     = "test-project"
    region         = "us-central1"
    enable_gateway = true
  }

  assert {
    condition     = output.runtime_ingress == "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"
    error_message = "enable_gateway = true must lock root ingress to the internal load balancer"
  }
}

run "root_wires_frontend_when_enabled" {
  command = plan

  variables {
    project_id           = "test-project"
    region               = "us-central1"
    enable_frontend      = true
    frontend_ssl_domains = ["portal.example.com"]
  }

  # The root wires module.frontend with the service_name as bucket name_prefix, so
  # the portal bucket name resolves off the root output (statically known at plan).
  assert {
    condition     = output.portal_bucket_name == "bridge-runtime-portal"
    error_message = "root must wire module.frontend and expose the portal bucket name"
  }
  assert {
    condition     = output.console_bucket_name == "bridge-runtime-console"
    error_message = "root must expose the (private) console bucket name"
  }
  assert {
    condition     = output.dashboard_bucket_name == "bridge-runtime-dashboard"
    error_message = "root must expose the (private) dashboard bucket name"
  }
  assert {
    condition     = output.portal_url == "https://portal.example.com/"
    error_message = "root must expose the portal URL from frontend_ssl_domains"
  }
}
