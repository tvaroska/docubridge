output "obs_traces_dataset_id" {
  description = "Linked BigQuery dataset id for Cloud Observability _Trace bucket (_AllSpans) (\"\" when disabled)."
  value       = var.enable_telemetry_bq ? "obs_traces" : ""
}

output "obs_logs_dataset_id" {
  description = "Linked BigQuery dataset id for Cloud Logging _Default analytics (_AllLogs) (\"\" when disabled)."
  value       = var.enable_telemetry_bq ? google_logging_linked_dataset.obs_logs[0].link_id : ""
}

output "genai_payloads_bucket_name" {
  description = "Cloud Storage bucket name for GenAI completion hook JSONL payloads (\"\" when disabled)."
  value       = var.enable_telemetry_bq ? google_storage_bucket.genai_payloads[0].name : ""
}

output "genai_payloads_upload_base_path" {
  description = "gs:// base URI injected as OTEL_INSTRUMENTATION_GENAI_UPLOAD_BASE_PATH (\"\" when disabled)."
  value       = var.enable_telemetry_bq ? "gs://${google_storage_bucket.genai_payloads[0].name}/" : ""
}

output "completions_table_fqn" {
  description = "Fully qualified BigLake external table (project.agent_eval.completions) over GenAI payload JSONL files (\"\" when disabled)."
  value       = var.enable_telemetry_bq ? "${var.project_id}.${google_bigquery_dataset.agent_eval[0].dataset_id}.${google_bigquery_table.completions[0].table_id}" : ""
}

output "apphub_application_ids" {
  description = "Provisioned App Hub application IDs ([] when disabled)."
  value       = var.enable_telemetry_bq ? sort([for k, v in google_apphub_application.apps : v.application_id]) : []
}

output "lakehouse_dataset_id" {
  description = "BigQuery dataset id for the Zone 4 operational facts lakehouse (\"\" when disabled)."
  value       = var.enable_telemetry_bq ? google_bigquery_dataset.lakehouse[0].dataset_id : ""
}

output "operational_facts_iceberg_table_fqn" {
  description = "Fully qualified BigQuery table (project.docubridge_lakehouse.bq_operational_facts_iceberg) for Zone 4 operational facts (\"\" when disabled)."
  value       = var.enable_telemetry_bq ? "${var.project_id}.${google_bigquery_dataset.lakehouse[0].dataset_id}.${google_bigquery_table.operational_facts_iceberg[0].table_id}" : ""
}
