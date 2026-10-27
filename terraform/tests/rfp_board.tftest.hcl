# Credential-free plan-time tests of the RFP board host (S0-arch-17,
# design/email-intake.md). mock_provider = never authenticated; command = plan =
# nothing created. Assertions are on input-derived values (the module runs pass a
# literal runtime SA email, since mock-computed SA emails are opaque at plan time).
#
# Load-bearing invariants: the DEFAULT plan creates no board service, invoker or
# Scheduler job and no A2A BRIDGE_RFP_PROCESS; the board service runs as bridge-runtime with internal-only ingress
# and NO allUsers invoker; it carries the processor envs (the A2A runtime carries
# none — it is an RFP opener only, S0-arch-19: BRIDGE_RFP_PROCESS) with every secret as a secret_key_ref; the Scheduler POSTs /email/drain on the
# board's deterministic run.app URL with an OIDC token for bridge-runtime, no retries.

mock_provider "google" {}
mock_provider "google-beta" {}

variables {
  project_id      = "test-project"
  region          = "us-central1"
  runtime_email   = "bridge-runtime@test-project.iam.gserviceaccount.com"
  container_image = "us-docker.pkg.dev/cloudrun/container/hello"
}

# ---- default: nothing ------------------------------------------------------

run "root_default_plan_has_no_board_surface" {
  command = plan

  assert {
    condition     = output.rfp_board_uri == "" && output.email_drain_job == ""
    error_message = "the default plan must create no RFP board service and no drain job"
  }
}

run "runtime_default_creates_no_board_resources" {
  command = plan

  module {
    source = "./modules/runtime"
  }

  variables {
    service_account_email = var.runtime_email
    vpc_network           = "projects/test-project/global/networks/bridge-vpc"
    egress_subnet         = "projects/test-project/regions/us-central1/subnetworks/bridge-vpc-internal"
  }

  assert {
    condition = (length(google_cloud_run_v2_service.rfp_board) == 0
      && length(google_cloud_run_v2_service_iam_member.rfp_board_invoker) == 0
    && length(google_cloud_scheduler_job.email_drain) == 0)
    error_message = "runtime default must create no board service, invoker or Scheduler job"
  }
  assert {
    condition = length([
      for e in google_cloud_run_v2_service.bridge.template[0].containers[0].env : e
      if e.name == "BRIDGE_RFP_PROCESS"
    ]) == 0
    error_message = "with no RFP board the A2A runtime carries no BRIDGE_RFP_PROCESS (no opener)"
  }
}

# ---- enabled ----------------------------------------------------------------

run "runtime_enabled_board_internal_runtime_sa_scheduler_drain" {
  command = plan

  module {
    source = "./modules/runtime"
  }

  variables {
    service_account_email        = var.runtime_email
    vpc_network                  = "projects/test-project/global/networks/bridge-vpc"
    egress_subnet                = "projects/test-project/regions/us-central1/subnetworks/bridge-vpc-internal"
    port_mode                    = "gcp"
    db_url_secret_id             = "bridge-db-url"
    enable_rfp_board             = true
    rfp_process                  = "benefits-rfp"
    enable_email_ingress         = true
    email_bucket                 = "bridge-runtime-email"
    email_mailbox                = "rfp-quotes@broker.example"
    email_token_secret_id        = "bridge-email-token-secret"
    email_webhook_sa_email       = "bridge-email-webhook@test-project.iam.gserviceaccount.com"
    email_graph_tenant_id        = "00000000-0000-0000-0000-000000000000"
    email_graph_client_id        = "11111111-1111-1111-1111-111111111111"
    email_graph_client_secret_id = "bridge-email-graph-client-secret"
    email_client_state_secret_id = "bridge-email-client-state"
    runtime_sa_email             = "bridge-runtime@test-project.iam.gserviceaccount.com"
    gateway_host                 = "bridge.example.com"
  }

  assert {
    condition     = google_cloud_run_v2_service.rfp_board[0].name == "bridge-runtime-rfp-board"
    error_message = "the board service must be <service_name>-rfp-board"
  }
  assert {
    condition     = google_cloud_run_v2_service.rfp_board[0].template[0].service_account == var.runtime_email
    error_message = "the board service must run as bridge-runtime (no new SA, no new grant)"
  }
  assert {
    condition     = google_cloud_run_v2_service.rfp_board[0].ingress == "INGRESS_TRAFFIC_INTERNAL_ONLY"
    error_message = "the board service must default to internal-only ingress"
  }
  assert {
    condition     = strcontains(google_cloud_run_v2_service.rfp_board[0].template[0].containers[0].args[1], "serve_rfp_board_container")
    error_message = "the board service must run the board-host entrypoint"
  }
  assert {
    condition     = google_cloud_run_v2_service.rfp_board[0].template[0].scaling[0].max_instance_count == 1
    error_message = "the board service defaults to one instance (drains serialize on the in-process lock)"
  }
  assert {
    condition = {
      for e in google_cloud_run_v2_service.rfp_board[0].template[0].containers[0].env :
      e.name => e.value if contains(["BRIDGE_RFP_PROCESS", "BRIDGE_EMAIL_DRAIN_INTERVAL", "BRIDGE_PORT_MODE", "BRIDGE_EMAIL_BUCKET", "BRIDGE_EMAIL_MAILBOX"], e.name)
      } == {
      BRIDGE_RFP_PROCESS          = "benefits-rfp"
      BRIDGE_EMAIL_DRAIN_INTERVAL = "0"
      BRIDGE_PORT_MODE            = "gcp"
      BRIDGE_EMAIL_BUCKET         = "bridge-runtime-email"
      BRIDGE_EMAIL_MAILBOX        = "rfp-quotes@broker.example"
    }
    error_message = "the board service carries the process, drain-interval 0, port mode and the processor's plain envs"
  }
  assert {
    condition = {
      for e in google_cloud_run_v2_service.rfp_board[0].template[0].containers[0].env :
      e.name => e.value_source[0].secret_key_ref[0].secret
      if contains(["DATABASE_URL", "BRIDGE_EMAIL_TOKEN_SECRET"], e.name) && (e.value == null || e.value == "")
      } == {
      DATABASE_URL              = "bridge-db-url"
      BRIDGE_EMAIL_TOKEN_SECRET = "bridge-email-token-secret"
    }
    error_message = "DATABASE_URL + BRIDGE_EMAIL_TOKEN_SECRET must be secret_key_ref envs with no plaintext value"
  }
  assert {
    condition = length([
      for e in google_cloud_run_v2_service.rfp_board[0].template[0].containers[0].env : e
      if startswith(e.name, "BRIDGE_EMAIL_GRAPH_") || e.name == "BRIDGE_EMAIL_CLIENT_STATE"
    ]) == 0
    error_message = "the board service holds no Graph credential (the webhook's alone)"
  }
  assert {
    condition = length([
      for e in google_cloud_run_v2_service.bridge.template[0].containers[0].env : e
      if startswith(e.name, "BRIDGE_EMAIL_")
    ]) == 0
    error_message = "the A2A runtime is an RFP opener only (no processor), so it carries no BRIDGE_EMAIL_* env"
  }
  assert {
    condition = {
      for e in google_cloud_run_v2_service.bridge.template[0].containers[0].env :
      e.name => e.value if e.name == "BRIDGE_RFP_PROCESS"
    } == { BRIDGE_RFP_PROCESS = "benefits-rfp" }
    error_message = "S0-arch-19: the A2A runtime opens RFPs onto the shared store, so it carries the board's BRIDGE_RFP_PROCESS"
  }
  assert {
    condition = {
      for e in google_cloud_run_v2_service.rfp_board[0].template[0].containers[0].env :
      e.name => e.value if contains(["BRIDGE_RUNTIME_SA_EMAIL", "BRIDGE_GATEWAY_HOST"], e.name)
      } == {
      BRIDGE_RUNTIME_SA_EMAIL = "bridge-runtime@test-project.iam.gserviceaccount.com"
      BRIDGE_GATEWAY_HOST     = "bridge.example.com"
    }
    error_message = "S0-arch-19: the board service carries the a2a_client gcp env (runtime SA + gateway host) for its chase route"
  }
  assert {
    condition = (google_cloud_run_v2_service_iam_member.rfp_board_invoker[0].role == "roles/run.invoker"
    && google_cloud_run_v2_service_iam_member.rfp_board_invoker[0].member == "serviceAccount:${var.runtime_email}")
    error_message = "bridge-runtime (the Scheduler's OIDC identity) is the board's only invoker"
  }
  assert {
    condition = length([
      for m in google_cloud_run_v2_service_iam_member.rfp_board_invoker : m if m.member == "allUsers" || m.member == "allAuthenticatedUsers"
    ]) == 0
    error_message = "the board service must never be public"
  }
  assert {
    condition = (google_cloud_scheduler_job.email_drain[0].http_target[0].http_method == "POST"
      && endswith(google_cloud_scheduler_job.email_drain[0].http_target[0].uri, "/email/drain")
      && startswith(google_cloud_scheduler_job.email_drain[0].http_target[0].uri, "https://bridge-runtime-rfp-board-")
    && strcontains(google_cloud_scheduler_job.email_drain[0].http_target[0].uri, ".us-central1.run.app/"))
    error_message = "the Scheduler must POST /email/drain on the board's deterministic run.app URL"
  }
  assert {
    condition = (google_cloud_scheduler_job.email_drain[0].http_target[0].oidc_token[0].service_account_email == var.runtime_email
    && "${google_cloud_scheduler_job.email_drain[0].http_target[0].oidc_token[0].audience}/email/drain" == google_cloud_scheduler_job.email_drain[0].http_target[0].uri)
    error_message = "the Scheduler must present an OIDC token for bridge-runtime, audience = the board URL"
  }
  assert {
    condition = (google_cloud_scheduler_job.email_drain[0].retry_config[0].retry_count == 0
      && google_cloud_scheduler_job.email_drain[0].schedule == "* * * * *"
    && google_cloud_scheduler_job.email_drain[0].attempt_deadline == "300s")
    error_message = "the drain ticks every minute with no retries (the next tick is the retry), 300s deadline"
  }
}

run "runtime_board_without_email_has_no_drain_job" {
  command = plan

  module {
    source = "./modules/runtime"
  }

  variables {
    service_account_email = var.runtime_email
    vpc_network           = "projects/test-project/global/networks/bridge-vpc"
    egress_subnet         = "projects/test-project/regions/us-central1/subnetworks/bridge-vpc-internal"
    enable_rfp_board      = true
    rfp_process           = "benefits-rfp"
  }

  assert {
    condition = (length(google_cloud_run_v2_service.rfp_board) == 1
    && length(google_cloud_scheduler_job.email_drain) == 0)
    error_message = "the board without email intake serves the read API but schedules no drain"
  }
  assert {
    condition = length([
      for e in google_cloud_run_v2_service.rfp_board[0].template[0].containers[0].env : e
      if e.name == "BRIDGE_EMAIL_BUCKET" || e.name == "BRIDGE_EMAIL_MAILBOX" || e.name == "BRIDGE_EMAIL_TOKEN_SECRET"
    ]) == 0
    error_message = "with no email intake the board carries no processor env"
  }
  assert {
    condition = length([
      for e in google_cloud_run_v2_service.rfp_board[0].template[0].containers[0].env : e
      if e.name == "BRIDGE_GATEWAY_HOST"
    ]) == 0
    error_message = "an unset gateway_host emits no BRIDGE_GATEWAY_HOST env on the board"
  }
  assert {
    condition = {
      for e in google_cloud_run_v2_service.bridge.template[0].containers[0].env :
      e.name => e.value if e.name == "BRIDGE_RFP_PROCESS"
    } == { BRIDGE_RFP_PROCESS = "benefits-rfp" }
    error_message = "the A2A runtime opens RFPs whenever the board is enabled, email or not"
  }
}

run "runtime_board_requires_a_process" {
  command = plan

  module {
    source = "./modules/runtime"
  }

  variables {
    service_account_email = var.runtime_email
    vpc_network           = "projects/test-project/global/networks/bridge-vpc"
    egress_subnet         = "projects/test-project/regions/us-central1/subnetworks/bridge-vpc-internal"
    enable_rfp_board      = true
  }

  expect_failures = [google_cloud_run_v2_service.rfp_board]
}

run "runtime_board_ingress_is_validated" {
  command = plan

  module {
    source = "./modules/runtime"
  }

  variables {
    service_account_email = var.runtime_email
    vpc_network           = "projects/test-project/global/networks/bridge-vpc"
    egress_subnet         = "projects/test-project/regions/us-central1/subnetworks/bridge-vpc-internal"
    rfp_board_ingress     = "bogus"
  }

  expect_failures = [var.rfp_board_ingress]
}

run "root_email_without_board_warns_no_drain" {
  command = plan

  variables {
    enable_email_ingress  = true
    email_mailbox         = "rfp-quotes@broker.example"
    email_graph_tenant_id = "00000000-0000-0000-0000-000000000000"
    email_graph_client_id = "11111111-1111-1111-1111-111111111111"
  }

  expect_failures = [check.email_ingress_has_a_drain]
}

run "root_enabled_wires_board_and_drain_job" {
  command = plan

  variables {
    enable_email_ingress  = true
    email_mailbox         = "rfp-quotes@broker.example"
    email_graph_tenant_id = "00000000-0000-0000-0000-000000000000"
    email_graph_client_id = "11111111-1111-1111-1111-111111111111"
    enable_rfp_board      = true
    rfp_process           = "benefits-rfp"
  }

  assert {
    condition     = output.email_drain_job == "bridge-runtime-email-drain"
    error_message = "root must wire the board + drain job through module.runtime"
  }
}
