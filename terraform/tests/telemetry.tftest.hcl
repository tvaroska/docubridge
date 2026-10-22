# Credential-free plan-time test of the telemetry module (BigQuery OpenTelemetry Trace Analytics).

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
    condition     = output.dataset_id == ""
    error_message = "enable_telemetry_bq = false must create no dataset"
  }

  assert {
    condition     = output.spans_table_fqn == ""
    error_message = "enable_telemetry_bq = false must return empty spans_table_fqn"
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
    dataset_id             = "docubridge_telemetry"
    service_account_member = "serviceAccount:bridge-runtime@test-project.iam.gserviceaccount.com"
  }

  assert {
    condition     = output.dataset_id == "docubridge_telemetry"
    error_message = "enable_telemetry_bq = true must expose dataset_id"
  }

  assert {
    condition     = output.spans_table_fqn == "test-project.docubridge_telemetry.otel_spans"
    error_message = "enable_telemetry_bq = true must expose spans_table_fqn"
  }

  assert {
    condition     = output.step_latency_view_fqn == "test-project.docubridge_telemetry.v_step_latency_analytics"
    error_message = "enable_telemetry_bq = true must expose step_latency_view_fqn"
  }

  assert {
    condition     = output.trace_waterfall_view_fqn == "test-project.docubridge_telemetry.v_trace_waterfall"
    error_message = "enable_telemetry_bq = true must expose trace_waterfall_view_fqn"
  }

  assert {
    condition     = output.entity_summary_view_fqn == "test-project.docubridge_telemetry.v_entity_pipeline_summary"
    error_message = "enable_telemetry_bq = true must expose entity_summary_view_fqn"
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
