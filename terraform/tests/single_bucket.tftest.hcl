# Credential-free plan-time tests for standalone and co-located single-bucket mode.
# Verifies gs://<project_id> prefix isolation, per-object retention, prefix-scoped
# lifecycle Delete rules, CEL IAM prefix conditions, BigLake source_uris, co-located
# data adoption (create_* = false), runtime env wiring, and frontend bucket rejection.

mock_provider "google" {}
mock_provider "google-beta" {}

variables {
  project_id     = "test-project"
  region         = "us-central1"
  runtime_member = "serviceAccount:bridge-runtime@test-project.iam.gserviceaccount.com"
  webhook_member = "serviceAccount:bridge-email-webhook@test-project.iam.gserviceaccount.com"
}

run "gcs_single_bucket_standalone_creates_shared_bucket_only" {
  command = plan

  module {
    source = "./modules/gcs"
  }

  variables {
    project_id                          = var.project_id
    location                            = var.region
    service_account_member              = var.runtime_member
    single_bucket_mode                  = true
    create_shared_bucket                = true
    retention_days                      = 365
    payload_retention_days              = 90
    email_retention_days                = 30
    enable_email_ingress                = true
    email_webhook_member                = var.webhook_member
    enable_audit_vault                  = true
    agent_registry_service_agent_member = "serviceAccount:service-100000000000@gcp-sa-agentregistry.iam.gserviceaccount.com"
  }

  assert {
    condition     = length(google_storage_bucket.shared) == 1 && length(data.google_storage_bucket.shared) == 0
    error_message = "standalone single-bucket mode must create exactly one shared bucket and zero data lookups"
  }

  assert {
    condition = (
      length(google_storage_bucket.skills) == 0
      && length(google_storage_bucket.artifacts) == 0
      && length(google_storage_bucket.scenarios) == 0
      && length(google_storage_bucket.email) == 0
      && length(google_storage_bucket.audit_vault) == 0
    )
    error_message = "single-bucket mode must create zero legacy per-subsystem buckets"
  }

  assert {
    condition = (
      google_storage_bucket.shared[0].name == "test-project"
      && google_storage_bucket.shared[0].location == "us-central1"
      && google_storage_bucket.shared[0].uniform_bucket_level_access == true
      && google_storage_bucket.shared[0].public_access_prevention == "enforced"
      && google_storage_bucket.shared[0].enable_object_retention == true
      && length(google_storage_bucket.shared[0].retention_policy) == 0
      && google_storage_bucket.shared[0].versioning[0].enabled == true
    )
    error_message = "shared bucket must be private (UBLA+PAP), versioned, with per-object retention enabled and no bucket-wide retention_policy"
  }

  assert {
    condition = alltrue([
      for r in google_storage_bucket.shared[0].lifecycle_rule :
      alltrue([
        for c in r.condition :
        length(c.matches_prefix) > 0 && alltrue([
          for p in c.matches_prefix :
          contains(["document_bridge/", "genai-payloads/", "email/"], p)
        ])
      ])
    ])
    error_message = "every lifecycle Delete rule on the shared bucket must be strictly scoped to document_bridge/, genai-payloads/, or email/ (never audit-vault/, skills/, or scenarios/)"
  }

  assert {
    condition = toset(flatten([
      for r in google_storage_bucket.shared[0].lifecycle_rule :
      flatten([for c in r.condition : c.matches_prefix])
    ])) == toset(["document_bridge/", "genai-payloads/", "email/"])
    error_message = "shared bucket lifecycle Delete rules must cover exactly document_bridge/, genai-payloads/, and email/"
  }

  assert {
    condition = (
      google_storage_bucket_iam_member.skills_object_admin.condition[0].expression == "resource.name.startsWith(\"projects/_/buckets/test-project/objects/skills/\")"
      && google_storage_bucket_iam_member.skills_agent_registry_viewer[0].condition[0].expression == "resource.name.startsWith(\"projects/_/buckets/test-project/objects/skills/\")"
      && google_storage_bucket_iam_member.artifacts_object_admin.condition[0].expression == "resource.name.startsWith(\"projects/_/buckets/test-project/objects/document_bridge/\")"
      && google_storage_bucket_iam_member.scenarios_object_admin.condition[0].expression == "resource.name.startsWith(\"projects/_/buckets/test-project/objects/scenarios/\")"
    )
    error_message = "skills, artifacts (document_bridge), and scenarios IAM bindings must carry valid bucket-policy CEL prefix conditions (no api.getAttribute)"
  }

  assert {
    condition = (
      length(google_project_iam_custom_role.shared_bucket_object_lister) == 1
      && google_project_iam_custom_role.shared_bucket_object_lister[0].permissions == toset(["storage.objects.list"])
      && toset(keys(google_storage_bucket_iam_member.shared_bucket_lister)) == toset(["runtime", "agent_registry"])
      && !contains(values(google_storage_bucket_iam_member.shared_bucket_lister)[*].member, var.webhook_member)
    )
    error_message = "single-bucket mode must grant minimal storage.objects.list custom role only to runtime/agent_registry and NEVER to email_webhook"
  }

  assert {
    condition = (
      google_storage_bucket_iam_member.email["webhook_creator"].role == "roles/storage.objectCreator"
      && google_storage_bucket_iam_member.email["webhook_creator"].condition[0].expression == "resource.name.startsWith(\"projects/_/buckets/test-project/objects/email/\")"
      && google_storage_bucket_iam_member.email["runtime_creator"].role == "roles/storage.objectCreator"
      && google_storage_bucket_iam_member.email["runtime_creator"].condition[0].expression == "resource.name.startsWith(\"projects/_/buckets/test-project/objects/email/\")"
      && google_storage_bucket_iam_member.email["runtime_viewer"].role == "roles/storage.objectViewer"
      && google_storage_bucket_iam_member.email["runtime_viewer"].condition[0].expression == "resource.name.startsWith(\"projects/_/buckets/test-project/objects/email/\")"
      && length([for g in google_storage_bucket_iam_member.email : g if g.role == "roles/storage.objectAdmin"]) == 0
    )
    error_message = "email prefix IAM must stay least-privilege (webhook create-only, runtime viewer+creator, zero objectAdmin) with valid CEL prefix conditions"
  }

  assert {
    condition = (
      toset([for g in google_storage_bucket_iam_member.audit_vault : g.role]) == toset(["roles/storage.objectCreator", "roles/storage.objectViewer"])
      && google_storage_bucket_iam_member.audit_vault["runtime_creator"].condition[0].expression == "resource.name.startsWith(\"projects/_/buckets/test-project/objects/audit-vault/\")"
      && google_storage_bucket_iam_member.audit_vault["runtime_viewer"].condition[0].expression == "resource.name.startsWith(\"projects/_/buckets/test-project/objects/audit-vault/\")"
      && length([for g in google_storage_bucket_iam_member.audit_vault : g if g.role == "roles/storage.objectAdmin"]) == 0
      && length(google_project_iam_custom_role.audit_vault_retention_setter) == 1
      && google_project_iam_custom_role.audit_vault_retention_setter[0].permissions == toset(["storage.objects.setRetention"])
      && length(google_storage_bucket_iam_member.audit_vault_retention) == 1
      && google_storage_bucket_iam_member.audit_vault_retention[0].member == var.runtime_member
      && google_storage_bucket_iam_member.audit_vault_retention[0].condition[0].expression == "resource.name.startsWith(\"projects/_/buckets/test-project/objects/audit-vault/\")"
    )
    error_message = "audit-vault prefix IAM must grant create+viewer+setRetention (zero objectAdmin/delete) with valid CEL prefix conditions"
  }

  assert {
    condition = (
      output.shared_bucket_name == "test-project"
      && output.skills_bucket_name == "test-project"
      && output.skills_prefix == "skills"
      && output.artifacts_bucket_name == "test-project"
      && output.artifacts_prefix == "document_bridge"
      && output.scenarios_bucket_name == "test-project"
      && output.scenarios_prefix == "scenarios"
      && output.email_bucket_name == "test-project"
      && output.email_prefix == "email"
      && output.audit_vault_bucket_name == "test-project"
      && output.audit_vault_prefix == "audit-vault"
    )
    error_message = "single-bucket gcs outputs must expose the shared bucket name and all 5 subsystem prefixes"
  }
}

run "gcs_single_bucket_colocated_adopts_shared_bucket" {
  command = plan

  module {
    source = "./modules/gcs"
  }

  variables {
    project_id             = var.project_id
    location               = var.region
    service_account_member = var.runtime_member
    single_bucket_mode     = true
    create_shared_bucket   = false
    enable_email_ingress   = true
    email_webhook_member   = var.webhook_member
    enable_audit_vault     = true
  }

  assert {
    condition = (
      length(google_storage_bucket.shared) == 0
      && length(data.google_storage_bucket.shared) == 1
      && length(google_storage_bucket.skills) == 0
      && length(google_storage_bucket.artifacts) == 0
      && length(google_storage_bucket.scenarios) == 0
      && length(google_storage_bucket.email) == 0
      && length(google_storage_bucket.audit_vault) == 0
    )
    error_message = "co-located single-bucket mode (create_shared_bucket = false) must adopt the existing bucket via data source and create zero buckets"
  }

  assert {
    condition     = output.shared_bucket_name == "test-project" && google_storage_bucket_iam_member.skills_object_admin.bucket == "test-project"
    error_message = "co-located single-bucket mode must bind CEL prefix IAM onto the adopted shared bucket"
  }
}

run "telemetry_single_bucket_standalone" {
  command = plan

  module {
    source = "./modules/telemetry"
  }

  variables {
    project_id               = var.project_id
    region                   = var.region
    enable_telemetry_bq      = true
    single_bucket_mode       = true
    shared_bucket_name       = var.project_id
    create_eval_dataset      = true
    create_vertex_connection = true
    service_account_member   = var.runtime_member
  }

  assert {
    condition     = length(google_storage_bucket.genai_payloads) == 0
    error_message = "single-bucket mode must skip creating a separate genai-payloads bucket"
  }

  assert {
    condition = (
      length(google_bigquery_dataset.agent_eval) == 1
      && length(data.google_bigquery_dataset.agent_eval) == 0
      && length(google_bigquery_connection.vertex_ai) == 1
      && length(google_project_iam_member.vertex_connection_aiplatform) == 1
    )
    error_message = "standalone telemetry (create_* = true) must provision agent_eval dataset and vertex_ai_connection"
  }

  assert {
    condition = (
      google_bigquery_table.completions[0].external_data_configuration[0].source_uris == tolist(["gs://test-project/genai-payloads/*.jsonl"])
      && output.genai_payloads_bucket_name == "test-project"
      && output.genai_payloads_upload_base_path == "gs://test-project/genai-payloads/"
      && output.completions_table_fqn == "test-project.agent_eval.completions"
    )
    error_message = "single-bucket telemetry must aim BigLake completions and upload path at gs://<project_id>/genai-payloads/"
  }

  assert {
    condition = (
      google_storage_bucket_iam_member.genai_payloads_creator[0].role == "roles/storage.objectCreator"
      && google_storage_bucket_iam_member.genai_payloads_creator[0].condition[0].expression == "resource.name.startsWith(\"projects/_/buckets/test-project/objects/genai-payloads/\")"
      && google_storage_bucket_iam_member.genai_payloads_biglake_viewer[0].role == "roles/storage.objectViewer"
      && google_storage_bucket_iam_member.genai_payloads_biglake_viewer[0].condition[0].expression == "resource.name.startsWith(\"projects/_/buckets/test-project/objects/genai-payloads/\")"
      && length(google_storage_bucket_iam_member.genai_payloads_biglake_lister) == 1
      && google_project_iam_custom_role.genai_payloads_object_lister[0].permissions == toset(["storage.objects.list"])
    )
    error_message = "single-bucket telemetry IAM must scope runtime creator and BigLake viewer to genai-payloads/ with valid CEL and list-only BigLake role"
  }
}

run "telemetry_single_bucket_colocated_adopts_dataset_and_connection" {
  command = plan

  module {
    source = "./modules/telemetry"
  }

  variables {
    project_id                              = var.project_id
    region                                  = var.region
    enable_telemetry_bq                     = true
    single_bucket_mode                      = true
    shared_bucket_name                      = var.project_id
    create_eval_dataset                     = false
    create_vertex_connection                = false
    vertex_connection_service_account_email = "bqcx-test@gcp-sa-bigquery-condel.iam.gserviceaccount.com"
    service_account_member                  = var.runtime_member
  }

  assert {
    condition = (
      length(google_storage_bucket.genai_payloads) == 0
      && length(google_bigquery_dataset.agent_eval) == 0
      && length(data.google_bigquery_dataset.agent_eval) == 1
      && length(google_bigquery_connection.vertex_ai) == 0
      && length(google_project_iam_member.vertex_connection_aiplatform) == 0
    )
    error_message = "co-located telemetry (create_* = false) must adopt agent_eval via data source and skip creating vertex_ai_connection"
  }

  assert {
    condition = (
      google_bigquery_table.completions[0].external_data_configuration[0].connection_id == "projects/test-project/locations/US/connections/vertex_ai_connection"
      && google_bigquery_table.completions[0].external_data_configuration[0].source_uris == tolist(["gs://test-project/genai-payloads/*.jsonl"])
      && output.completions_table_fqn == "test-project.agent_eval.completions"
    )
    error_message = "co-located completions BigLake table must reference the existing US vertex_ai_connection and genai-payloads/*.jsonl URI"
  }

  assert {
    condition = (
      length(google_storage_bucket_iam_member.genai_payloads_biglake_viewer) == 1
      && google_storage_bucket_iam_member.genai_payloads_biglake_viewer[0].role == "roles/storage.objectViewer"
      && google_storage_bucket_iam_member.genai_payloads_biglake_viewer[0].member == "serviceAccount:bqcx-test@gcp-sa-bigquery-condel.iam.gserviceaccount.com"
      && google_storage_bucket_iam_member.genai_payloads_biglake_viewer[0].condition[0].expression == "resource.name.startsWith(\"projects/_/buckets/test-project/objects/genai-payloads/\")"
      && length(google_storage_bucket_iam_member.genai_payloads_biglake_lister) == 1
      && google_storage_bucket_iam_member.genai_payloads_biglake_lister[0].member == "serviceAccount:bqcx-test@gcp-sa-bigquery-condel.iam.gserviceaccount.com"
    )
    error_message = "co-located telemetry must grant valid prefix-scoped viewer and list-only role to the existing BigLake connection SA when supplied"
  }
}

run "frontend_rejects_extra_buckets_in_single_bucket_mode" {
  command = plan

  module {
    source = "./modules/frontend"
  }

  variables {
    project_id         = var.project_id
    single_bucket_mode = true
    enable_frontend    = true
  }

  expect_failures = [var.enable_frontend]
}

run "root_rejects_frontend_in_single_bucket_mode" {
  command = plan

  variables {
    project_id         = var.project_id
    region             = var.region
    single_bucket_mode = true
    enable_frontend    = true
  }

  expect_failures = [var.enable_frontend]
}

run "runtime_single_bucket_injects_prefix_and_worm_retention_envs" {
  command = plan

  module {
    source = "./modules/runtime"
  }

  variables {
    project_id                   = var.project_id
    region                       = var.region
    container_image              = "us-docker.pkg.dev/cloudrun/container/hello"
    service_account_email        = "bridge-runtime@test-project.iam.gserviceaccount.com"
    vpc_network                  = "projects/test-project/global/networks/bridge-vpc"
    egress_subnet                = "projects/test-project/regions/us-central1/subnetworks/bridge-vpc-internal"
    port_mode                    = "gcp"
    single_bucket_mode           = true
    skills_bucket                = "test-project"
    skills_prefix                = "skills"
    artifacts_bucket             = "test-project"
    vault_bucket                 = "test-project"
    vault_prefix                 = "audit-vault"
    vault_retention_days         = 2555
    vault_retention_mode         = "Locked"
    scenarios_bucket             = "test-project"
    scenarios_prefix             = "scenarios"
    enable_rfp_board             = true
    rfp_process                  = "benefits-rfp"
    enable_email_ingress         = true
    email_bucket                 = "test-project"
    email_prefix                 = "email"
    email_mailbox                = "rfp-quotes@broker.example"
    email_token_secret_id        = "bridge-email-token-secret"
    email_webhook_sa_email       = "bridge-email-webhook@test-project.iam.gserviceaccount.com"
    email_graph_tenant_id        = "00000000-0000-0000-0000-000000000000"
    email_graph_client_id        = "11111111-1111-1111-1111-111111111111"
    email_graph_client_secret_id = "bridge-email-graph-client-secret"
    email_client_state_secret_id = "bridge-email-client-state"
  }

  assert {
    condition = {
      for e in google_cloud_run_v2_service.bridge.template[0].containers[0].env :
      e.name => e.value
      if contains([
        "BRIDGE_SKILLS_BUCKET",
        "BRIDGE_SKILLS_PREFIX",
        "BRIDGE_ARTIFACT_BUCKET",
        "BRIDGE_VAULT_BUCKET",
        "BRIDGE_VAULT_PREFIX",
        "BRIDGE_VAULT_RETENTION_DAYS",
        "BRIDGE_VAULT_RETENTION_MODE",
        "OTEL_INSTRUMENTATION_GENAI_UPLOAD_BASE_PATH",
      ], e.name)
      } == {
      BRIDGE_SKILLS_BUCKET                        = "test-project"
      BRIDGE_SKILLS_PREFIX                        = "skills"
      BRIDGE_ARTIFACT_BUCKET                      = "test-project"
      BRIDGE_VAULT_BUCKET                         = "test-project"
      BRIDGE_VAULT_PREFIX                         = "audit-vault"
      BRIDGE_VAULT_RETENTION_DAYS                 = "2555"
      BRIDGE_VAULT_RETENTION_MODE                 = "Locked"
      OTEL_INSTRUMENTATION_GENAI_UPLOAD_BASE_PATH = "gs://test-project/genai-payloads/"
    }
    error_message = "bridge-kyc must carry single-bucket prefix, WORM retention, and genai-payloads/ envs"
  }

  assert {
    condition = {
      for e in google_cloud_run_v2_service.rfp_board[0].template[0].containers[0].env :
      e.name => e.value
      if contains([
        "BRIDGE_SKILLS_BUCKET",
        "BRIDGE_SKILLS_PREFIX",
        "BRIDGE_VAULT_BUCKET",
        "BRIDGE_VAULT_PREFIX",
        "BRIDGE_VAULT_RETENTION_DAYS",
        "BRIDGE_VAULT_RETENTION_MODE",
        "BRIDGE_SCENARIOS_DIR",
        "BRIDGE_EMAIL_BUCKET",
        "BRIDGE_EMAIL_PREFIX",
        "OTEL_INSTRUMENTATION_GENAI_UPLOAD_BASE_PATH",
      ], e.name)
      } == {
      BRIDGE_SKILLS_BUCKET                        = "test-project"
      BRIDGE_SKILLS_PREFIX                        = "skills"
      BRIDGE_VAULT_BUCKET                         = "test-project"
      BRIDGE_VAULT_PREFIX                         = "audit-vault"
      BRIDGE_VAULT_RETENTION_DAYS                 = "2555"
      BRIDGE_VAULT_RETENTION_MODE                 = "Locked"
      BRIDGE_SCENARIOS_DIR                        = "gs://test-project/scenarios/"
      BRIDGE_EMAIL_BUCKET                         = "test-project"
      BRIDGE_EMAIL_PREFIX                         = "email"
      OTEL_INSTRUMENTATION_GENAI_UPLOAD_BASE_PATH = "gs://test-project/genai-payloads/"
    }
    error_message = "bridge-rfp must carry single-bucket prefix, scenarios, email, WORM retention, and genai-payloads/ envs"
  }

  assert {
    condition = {
      for e in google_cloud_run_v2_service.bridge_meridian.template[0].containers[0].env :
      e.name => e.value
      if contains([
        "BRIDGE_SKILLS_PREFIX",
        "BRIDGE_VAULT_PREFIX",
        "BRIDGE_SCENARIOS_DIR",
        "OTEL_INSTRUMENTATION_GENAI_UPLOAD_BASE_PATH",
      ], e.name)
      } == {
      BRIDGE_SKILLS_PREFIX                        = "skills"
      BRIDGE_VAULT_PREFIX                         = "audit-vault"
      BRIDGE_SCENARIOS_DIR                        = "gs://test-project/scenarios/"
      OTEL_INSTRUMENTATION_GENAI_UPLOAD_BASE_PATH = "gs://test-project/genai-payloads/"
    }
    error_message = "bridge-meridian must carry single-bucket prefix, scenarios, and genai-payloads/ envs"
  }

  assert {
    condition = {
      for e in google_cloud_run_v2_service.email_webhook[0].template[0].containers[0].env :
      e.name => e.value
      if contains(["BRIDGE_EMAIL_BUCKET", "BRIDGE_EMAIL_PREFIX"], e.name)
      } == {
      BRIDGE_EMAIL_BUCKET = "test-project"
      BRIDGE_EMAIL_PREFIX = "email"
    }
    error_message = "email_webhook must carry BRIDGE_EMAIL_BUCKET and BRIDGE_EMAIL_PREFIX in single-bucket mode"
  }
}

run "root_single_bucket_standalone_wires_all_prefixes_and_outputs" {
  command = plan

  variables {
    project_id               = var.project_id
    region                   = var.region
    single_bucket_mode       = true
    create_shared_bucket     = true
    create_eval_dataset      = true
    create_vertex_connection = true
    enable_audit_vault       = true
    enable_telemetry_bq      = true
    enable_email_ingress     = true
    enable_rfp_board         = true
    rfp_process              = "benefits-rfp"
    email_mailbox            = "rfp-quotes@broker.example"
    email_graph_tenant_id    = "00000000-0000-0000-0000-000000000000"
    email_graph_client_id    = "11111111-1111-1111-1111-111111111111"
    port_mode                = "gcp"
  }

  assert {
    condition = (
      output.shared_bucket_name == "test-project"
      && output.skills_bucket_name == "test-project"
      && output.skills_prefix == "skills"
      && output.artifacts_bucket_name == "test-project"
      && output.artifacts_prefix == "document_bridge"
      && output.audit_vault_bucket_name == "test-project"
      && output.audit_vault_prefix == "audit-vault"
      && output.scenarios_bucket_name == "test-project"
      && output.scenarios_prefix == "scenarios"
      && output.email_bucket_name == "test-project"
      && output.email_prefix == "email"
      && output.telemetry_genai_payloads_bucket_name == "test-project"
      && output.telemetry_genai_payloads_upload_base_path == "gs://test-project/genai-payloads/"
      && output.telemetry_completions_table_fqn == "test-project.agent_eval.completions"
      && output.portal_bucket_name == ""
    )
    error_message = "root standalone single-bucket outputs must route every subsystem through gs://<project_id> with expected prefixes"
  }
}

run "root_single_bucket_colocated_adopts_shared_resources" {
  command = plan

  variables {
    project_id                              = var.project_id
    region                                  = var.region
    single_bucket_mode                      = true
    create_shared_bucket                    = false
    create_eval_dataset                     = false
    create_vertex_connection                = false
    vertex_connection_service_account_email = "bqcx-test@gcp-sa-bigquery-condel.iam.gserviceaccount.com"
    enable_audit_vault                      = true
    enable_telemetry_bq                     = true
    enable_email_ingress                    = true
    enable_rfp_board                        = true
    rfp_process                             = "benefits-rfp"
    email_mailbox                           = "rfp-quotes@broker.example"
    email_graph_tenant_id                   = "00000000-0000-0000-0000-000000000000"
    email_graph_client_id                   = "11111111-1111-1111-1111-111111111111"
    port_mode                               = "gcp"
  }

  assert {
    condition = (
      output.shared_bucket_name == "test-project"
      && output.skills_bucket_name == "test-project"
      && output.skills_prefix == "skills"
      && output.audit_vault_bucket_name == "test-project"
      && output.audit_vault_prefix == "audit-vault"
      && output.email_bucket_name == "test-project"
      && output.email_prefix == "email"
      && output.telemetry_genai_payloads_bucket_name == "test-project"
      && output.telemetry_genai_payloads_upload_base_path == "gs://test-project/genai-payloads/"
      && output.telemetry_completions_table_fqn == "test-project.agent_eval.completions"
      && output.portal_bucket_name == ""
    )
    error_message = "root co-located single-bucket outputs must resolve cleanly when adopting existing bucket, eval dataset, and vertex connection"
  }
}
