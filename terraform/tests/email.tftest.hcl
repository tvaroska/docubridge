# Credential-free plan-time tests of email intake (S0-arch-13, design/email-intake.md).
# mock_provider = never authenticated; command = plan = nothing created. Assertions
# are on input-derived values (the module runs pass literal SA members/emails, since
# mock-computed SA emails are opaque at plan time).
#
# Load-bearing invariants: the DEFAULT plan creates zero email resources; the webhook
# service is the only allUsers-invokable surface and runs as a create-only SA; every
# email secret rides a secret_key_ref (no plaintext value, no secret version in HCL);
# the landing bucket is never public, lifecycle-retained, and has no objectAdmin.

mock_provider "google" {}
mock_provider "google-beta" {}

variables {
  project_id      = "test-project"
  region          = "us-central1"
  runtime_member  = "serviceAccount:bridge-runtime@test-project.iam.gserviceaccount.com"
  webhook_email   = "bridge-email-webhook@test-project.iam.gserviceaccount.com"
  webhook_member  = "serviceAccount:bridge-email-webhook@test-project.iam.gserviceaccount.com"
  container_image = "us-docker.pkg.dev/cloudrun/container/hello"
}

# ---- default: nothing ------------------------------------------------------

run "root_default_plan_has_no_email_surface" {
  command = plan

  assert {
    condition     = output.email_bucket_name == "" && output.email_webhook_email == "" && output.email_webhook_uri == ""
    error_message = "the default plan must create no email bucket, webhook SA, or webhook service"
  }
}

run "modules_default_create_zero_email_resources" {
  command = plan

  module {
    source = "./modules/gcs"
  }

  variables {
    service_account_member = var.runtime_member
  }

  assert {
    condition     = length(google_storage_bucket.email) == 0 && length(google_storage_bucket_iam_member.email) == 0
    error_message = "gcs default must create no email bucket and no email grants"
  }
}

run "secrets_default_creates_zero_email_secrets" {
  command = plan

  module {
    source = "./modules/secrets"
  }

  variables {
    service_account_member = var.runtime_member
  }

  assert {
    condition = (length(google_secret_manager_secret.email_webhook) == 0
      && length(google_secret_manager_secret.email_token) == 0
      && length(google_secret_manager_secret_iam_member.email_webhook_accessor) == 0
    && length(google_secret_manager_secret_iam_member.email_token_accessor) == 0)
    error_message = "secrets default must create no email secret containers or accessors"
  }
}

run "iam_default_creates_no_webhook_sa" {
  command = plan

  module {
    source = "./modules/iam"
  }

  assert {
    condition     = length(google_service_account.email_webhook) == 0 && output.email_webhook_member == ""
    error_message = "iam default must create no webhook SA"
  }
}

run "runtime_default_has_no_webhook_and_no_email_env" {
  command = plan

  module {
    source = "./modules/runtime"
  }

  variables {
    service_account_email = "bridge-runtime@test-project.iam.gserviceaccount.com"
    vpc_network           = "projects/test-project/global/networks/bridge-vpc"
    egress_subnet         = "projects/test-project/regions/us-central1/subnetworks/bridge-vpc-internal"
  }

  assert {
    condition = (length(google_cloud_run_v2_service.email_webhook) == 0
    && length(google_cloud_run_v2_service_iam_member.email_webhook_public) == 0)
    error_message = "runtime default must create no webhook service and no public invoker"
  }
  assert {
    condition = length([
      for e in google_cloud_run_v2_service.bridge.template[0].containers[0].env
      : e if startswith(e.name, "BRIDGE_EMAIL_")
    ]) == 0
    error_message = "runtime default must carry no BRIDGE_EMAIL_* env"
  }
}

# ---- enabled ----------------------------------------------------------------

run "iam_enabled_webhook_sa_has_no_project_roles" {
  command = plan

  module {
    source = "./modules/iam"
  }

  variables {
    enable_email_ingress = true
  }

  assert {
    condition     = google_service_account.email_webhook[0].account_id == "bridge-email-webhook"
    error_message = "the webhook SA must be bridge-email-webhook"
  }
  # Project grants (base) go only to the runtime SA — the webhook SA appears in none.
  assert {
    condition     = length(google_project_iam_member.base) == length(output.granted_roles)
    error_message = "enabling email intake must add no project-level role"
  }
}

run "secrets_enabled_containers_and_single_identity_accessors" {
  command = plan

  module {
    source = "./modules/secrets"
  }

  variables {
    service_account_member = var.runtime_member
    enable_email_ingress   = true
    email_webhook_member   = var.webhook_member
  }

  assert {
    condition = (google_secret_manager_secret.email_webhook["graph_client_secret"].secret_id == "bridge-email-graph-client-secret"
      && google_secret_manager_secret.email_webhook["client_state"].secret_id == "bridge-email-client-state"
    && google_secret_manager_secret.email_token[0].secret_id == "bridge-email-token-secret")
    error_message = "the three email secret containers must use the documented ids"
  }
  assert {
    condition = alltrue([
      for k, m in google_secret_manager_secret_iam_member.email_webhook_accessor :
      m.member == var.webhook_member && m.role == "roles/secretmanager.secretAccessor"
    ]) && length(google_secret_manager_secret_iam_member.email_webhook_accessor) == 2
    error_message = "the Graph client secret + clientState must be readable by the webhook SA only"
  }
  assert {
    condition     = google_secret_manager_secret_iam_member.email_token_accessor[0].member == var.runtime_member
    error_message = "the token key must be readable by the runtime SA only"
  }
  assert {
    condition     = output.email_token_secret_id == "bridge-email-token-secret"
    error_message = "the token secret id must be exposed for the runtime env"
  }
}

run "gcs_enabled_bucket_private_retained_create_only" {
  command = plan

  module {
    source = "./modules/gcs"
  }

  variables {
    name_prefix            = "bridge-runtime"
    service_account_member = var.runtime_member
    enable_email_ingress   = true
    email_webhook_member   = var.webhook_member
    email_retention_days   = 14
  }

  assert {
    condition     = google_storage_bucket.email[0].name == "bridge-runtime-email" && output.email_bucket_name == "bridge-runtime-email"
    error_message = "email bucket must be <name_prefix>-email"
  }
  assert {
    condition = (google_storage_bucket.email[0].uniform_bucket_level_access == true
    && google_storage_bucket.email[0].public_access_prevention == "enforced")
    error_message = "email bucket must be UBLA + public access prevention enforced"
  }
  assert {
    condition = length([
      for r in google_storage_bucket.email[0].lifecycle_rule : r
      if one([for a in r.action : a.type]) == "Delete" && one([for c in r.condition : c.age]) == 14
    ]) == 1
    error_message = "email bucket lifecycle must Delete at email_retention_days"
  }
  assert {
    condition = toset([
      for g in google_storage_bucket_iam_member.email : g.role if g.member == var.webhook_member
    ]) == toset(["roles/storage.objectCreator"])
    error_message = "the webhook SA must hold objectCreator ONLY on the email bucket"
  }
  assert {
    condition = toset([
      for g in google_storage_bucket_iam_member.email : g.role if g.member == var.runtime_member
    ]) == toset(["roles/storage.objectViewer", "roles/storage.objectCreator"])
    error_message = "the runtime SA must hold objectViewer + objectCreator on the email bucket"
  }
  assert {
    condition = length([
      for g in google_storage_bucket_iam_member.email : g if g.role == "roles/storage.objectAdmin"
    ]) == 0
    error_message = "nobody holds objectAdmin on the email bucket"
  }
}

run "runtime_enabled_webhook_public_secret_refs_and_board_token_env" {
  command = plan

  module {
    source = "./modules/runtime"
  }

  variables {
    service_account_email        = "bridge-runtime@test-project.iam.gserviceaccount.com"
    vpc_network                  = "projects/test-project/global/networks/bridge-vpc"
    egress_subnet                = "projects/test-project/regions/us-central1/subnetworks/bridge-vpc-internal"
    enable_email_ingress         = true
    email_bucket                 = "bridge-runtime-email"
    email_mailbox                = "rfp-quotes@broker.example"
    email_token_secret_id        = "bridge-email-token-secret"
    email_webhook_sa_email       = var.webhook_email
    email_graph_tenant_id        = "00000000-0000-0000-0000-000000000000"
    email_graph_client_id        = "11111111-1111-1111-1111-111111111111"
    email_graph_client_secret_id = "bridge-email-graph-client-secret"
    email_client_state_secret_id = "bridge-email-client-state"
    # The processor envs ride the RFP board host (S0-arch-17), not the A2A runtime.
    enable_rfp_board = true
    rfp_process      = "benefits-rfp"
  }

  assert {
    condition     = google_cloud_run_v2_service.email_webhook[0].ingress == "INGRESS_TRAFFIC_ALL"
    error_message = "the webhook service must accept Graph's traffic (INGRESS_TRAFFIC_ALL)"
  }
  assert {
    condition     = google_cloud_run_v2_service.email_webhook[0].template[0].service_account == var.webhook_email
    error_message = "the webhook service must run as the webhook SA, never the runtime SA"
  }
  assert {
    condition     = google_cloud_run_v2_service.email_webhook[0].template[0].containers[0].args[1] == "from bridge.edge.email_ingress import serve_email_webhook_container; serve_email_webhook_container()"
    error_message = "the webhook service must run the webhook-only entrypoint"
  }
  assert {
    condition     = google_cloud_run_v2_service.email_webhook[0].template[0].scaling[0].max_instance_count == 3
    error_message = "the webhook service must be capped at a small instance count"
  }
  assert {
    condition = alltrue([
      for e in google_cloud_run_v2_service.email_webhook[0].template[0].containers[0].env :
      e.value_source[0].secret_key_ref[0].secret != "" && (e.value == null || e.value == "")
      if contains(["BRIDGE_EMAIL_GRAPH_CLIENT_SECRET", "BRIDGE_EMAIL_CLIENT_STATE"], e.name)
      ]) && length([
      for e in google_cloud_run_v2_service.email_webhook[0].template[0].containers[0].env : e
      if contains(["BRIDGE_EMAIL_GRAPH_CLIENT_SECRET", "BRIDGE_EMAIL_CLIENT_STATE"], e.name)
    ]) == 2
    error_message = "the Graph client secret + clientState must be secret_key_ref envs with no plaintext value"
  }
  assert {
    condition = one([
      for e in google_cloud_run_v2_service.email_webhook[0].template[0].containers[0].env : e.value
      if e.name == "BRIDGE_PORT_EMAIL_INGRESS"
    ]) == "gcp"
    error_message = "the webhook must route the email_ingress port to the GCS adapter"
  }
  assert {
    condition = (google_cloud_run_v2_service_iam_member.email_webhook_public[0].member == "allUsers"
    && google_cloud_run_v2_service_iam_member.email_webhook_public[0].role == "roles/run.invoker")
    error_message = "the webhook service is the deliberate allUsers run.invoker exception"
  }
  assert {
    condition = one([
      for e in google_cloud_run_v2_service.rfp_board[0].template[0].containers[0].env :
      e.value_source[0].secret_key_ref[0].secret if e.name == "BRIDGE_EMAIL_TOKEN_SECRET"
    ]) == "bridge-email-token-secret"
    error_message = "the RFP board host must read the token key via secret_key_ref"
  }
  assert {
    condition = toset([
      for e in google_cloud_run_v2_service.rfp_board[0].template[0].containers[0].env : e.name
      if startswith(e.name, "BRIDGE_EMAIL_")
    ]) == toset(["BRIDGE_EMAIL_BUCKET", "BRIDGE_EMAIL_MAILBOX", "BRIDGE_EMAIL_TOKEN_SECRET", "BRIDGE_EMAIL_DRAIN_INTERVAL"])
    error_message = "the RFP board host carries the bucket + mailbox + token + drain-interval envs and NO Graph credential"
  }
  assert {
    condition = length([
      for e in google_cloud_run_v2_service.bridge.template[0].containers[0].env : e
      if startswith(e.name, "BRIDGE_EMAIL_")
    ]) == 0
    error_message = "the A2A runtime holds no RFP board, so it carries no BRIDGE_EMAIL_* env"
  }
}

run "root_enabled_wires_bucket_and_token_secret" {
  command = plan

  variables {
    enable_email_ingress  = true
    email_mailbox         = "rfp-quotes@broker.example"
    email_graph_tenant_id = "00000000-0000-0000-0000-000000000000"
    email_graph_client_id = "11111111-1111-1111-1111-111111111111"
    # Landed mail needs a drain: the RFP board host (S0-arch-17, check email_ingress_has_a_drain).
    enable_rfp_board = true
    rfp_process      = "benefits-rfp"
  }

  assert {
    condition     = output.email_bucket_name == "bridge-runtime-email"
    error_message = "root must wire module.gcs and expose the email bucket name"
  }
  assert {
    condition = (module.secrets.email_token_secret_id == "bridge-email-token-secret"
    && module.secrets.email_client_state_secret_id == "bridge-email-client-state")
    error_message = "root must wire module.secrets' email containers (the runtime/webhook secret envs read them)"
  }
}
