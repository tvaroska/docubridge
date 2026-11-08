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
    condition     = output.runtime_service_name == "bridge-kyc" && output.kyc_service_name == "bridge-kyc" && output.meridian_service_name == "bridge-meridian"
    error_message = "root must expose the per-app Cloud Run service names (bridge-kyc and bridge-meridian)"
  }

  # Default (enable_gateway = false) is the local/dev ingress posture.
  assert {
    condition     = output.runtime_ingress == "INGRESS_TRAFFIC_ALL"
    error_message = "default enable_gateway = false must yield INGRESS_TRAFFIC_ALL"
  }

  assert {
    condition     = module.runtime.service_name == var.kyc_service_name
    error_message = "primary runtime service name must track kyc_service_name (bridge-kyc)"
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

# The deny-list is operator-supplied (deploy.sh: BLOCKED_PROJECT_IDS ->
# TF_VAR_blocked_project_ids); no real project id is committed here.
run "root_refuses_blocked_project_id" {
  command = plan

  variables {
    project_id          = "blocked-core-project"
    region              = "us-central1"
    blocked_project_ids = ["blocked-core-project", "another-blocked-project"]
  }

  expect_failures = [var.project_id]
}

run "root_allows_project_outside_block_list" {
  command = plan

  variables {
    project_id          = "test-project"
    region              = "us-central1"
    blocked_project_ids = ["blocked-core-project", "another-blocked-project"]
  }

  assert {
    condition     = output.runtime_service_name == "bridge-kyc"
    error_message = "a project outside blocked_project_ids must plan normally"
  }
}

run "root_single_apply_wires_secrets_skills_and_gateway_origin" {
  command = plan

  variables {
    project_id       = "test-project"
    region           = "us-central1"
    db_password      = "test-db-password"
    enable_gateway   = true
    ssl_domains      = ["bridge.example.com"]
    port_mode        = "gcp"
    enable_rfp_board = true
    rfp_process      = "benefits-rfp"
  }

  assert {
    condition     = output.gateway_url == "https://bridge.example.com/"
    error_message = "single-pass root plan must resolve gateway_url from ssl_domains[0]"
  }

  assert {
    condition     = output.db_secret_id == "bridge-database-url" && output.spiffe_svid_secret_id == "bridge-spiffe-svid-secret"
    error_message = "single-pass root plan must wire both secret containers and versions"
  }
}
