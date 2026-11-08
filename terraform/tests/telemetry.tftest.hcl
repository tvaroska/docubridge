# Credential-free plan-time test of the telemetry module (Native Cloud Observability,
# GenAI payload bucket + BigLake external table, App Hub applications, and Zone 4 lakehouse).

mock_provider "google" {}
mock_provider "google-beta" {}

run "telemetry_disabled_by_default" {
  command = plan

  module {
    source = "./modules/telemetry"
  }

  variables {
    project_id             = "test-project"
    region                 = "us-central1"
    service_account_member = "serviceAccount:bridge-runtime@test-project.iam.gserviceaccount.com"
  }

  assert {
    condition     = output.obs_traces_dataset_id == "" && output.obs_logs_dataset_id == ""
    error_message = "enable_telemetry_bq = false must create no linked observability datasets"
  }

  assert {
    condition     = output.genai_payloads_bucket_name == "" && output.completions_table_fqn == ""
    error_message = "enable_telemetry_bq = false must create no GenAI payload bucket or completions table"
  }

  assert {
    condition     = length(output.apphub_application_ids) == 0
    error_message = "enable_telemetry_bq = false must create no App Hub applications"
  }
}

run "telemetry_enabled_provisions_bq_dataset_table_and_views" {
  command = plan

  module {
    source = "./modules/telemetry"
  }

  variables {
    project_id             = "test-project"
    region                 = "us-central1"
    enable_telemetry_bq    = true
    service_account_member = "serviceAccount:bridge-runtime@test-project.iam.gserviceaccount.com"
  }

  assert {
    condition     = output.obs_traces_dataset_id == "obs_traces"
    error_message = "enable_telemetry_bq = true must expose obs_traces_dataset_id"
  }

  assert {
    condition     = output.obs_logs_dataset_id == "obs_logs"
    error_message = "enable_telemetry_bq = true must expose obs_logs_dataset_id"
  }

  assert {
    condition     = output.genai_payloads_bucket_name == "test-project-genai-payloads"
    error_message = "enable_telemetry_bq = true must provision <project_id>-genai-payloads bucket"
  }

  assert {
    condition     = output.genai_payloads_upload_base_path == "gs://test-project-genai-payloads/"
    error_message = "enable_telemetry_bq = true must expose gs://<project_id>-genai-payloads/ upload path"
  }

  assert {
    condition     = output.completions_table_fqn == "test-project.agent_eval.completions"
    error_message = "enable_telemetry_bq = true must provision completions BigLake external table"
  }

  assert {
    condition = toset(output.apphub_application_ids) == toset([
      "benefits-rfp-procurement",
      "external-counterparty-agents",
      "kyc-address-verification",
      "meridian-loan-consolidation",
    ])
    error_message = "enable_telemetry_bq = true must provision all 4 App Hub applications"
  }

  assert {
    condition     = output.lakehouse_dataset_id == "docubridge_lakehouse"
    error_message = "enable_telemetry_bq = true must provision docubridge_lakehouse dataset"
  }

  assert {
    condition     = output.operational_facts_iceberg_table_fqn == "test-project.docubridge_lakehouse.bq_operational_facts_iceberg"
    error_message = "enable_telemetry_bq = true must provision bq_operational_facts_iceberg table"
  }
}
