output "dataset_id" {
  description = "BigQuery dataset id for OpenTelemetry trace analytics (\"\" when disabled)."
  value       = var.enable_telemetry_bq ? google_bigquery_dataset.telemetry[0].dataset_id : ""
}

output "spans_table_fqn" {
  description = "Fully qualified BigQuery table (project.dataset.table) injected as BRIDGE_TELEMETRY_BQ_TABLE (\"\" when disabled)."
  value       = var.enable_telemetry_bq ? "${var.project_id}.${google_bigquery_dataset.telemetry[0].dataset_id}.${google_bigquery_table.otel_spans[0].table_id}" : ""
}

output "step_latency_view_fqn" {
  description = "Fully qualified BigQuery view for step p50/p95/p99 latency analytics (\"\" when disabled)."
  value       = var.enable_telemetry_bq ? "${var.project_id}.${google_bigquery_dataset.telemetry[0].dataset_id}.${google_bigquery_table.v_step_latency_analytics[0].table_id}" : ""
}

output "trace_waterfall_view_fqn" {
  description = "Fully qualified BigQuery view for chronological trace waterfall (\"\" when disabled)."
  value       = var.enable_telemetry_bq ? "${var.project_id}.${google_bigquery_dataset.telemetry[0].dataset_id}.${google_bigquery_table.v_trace_waterfall[0].table_id}" : ""
}

output "entity_summary_view_fqn" {
  description = "Fully qualified BigQuery view for per-entity pipeline trace summary (\"\" when disabled)."
  value       = var.enable_telemetry_bq ? "${var.project_id}.${google_bigquery_dataset.telemetry[0].dataset_id}.${google_bigquery_table.v_entity_pipeline_summary[0].table_id}" : ""
}

output "trace_sql_evaluation_view_fqn" {
  description = "Fully qualified BigQuery view for Rule 0 deterministic SQL trajectory evaluation and selective AI.GENERATE_TABLE sampling (\"\" when disabled)."
  value       = var.enable_telemetry_bq ? "${var.project_id}.${google_bigquery_dataset.telemetry[0].dataset_id}.${google_bigquery_table.v_trace_sql_evaluation[0].table_id}" : ""
}

output "lakehouse_dataset_id" {
  description = "BigQuery dataset id for the Zone 4 operational facts lakehouse (\"\" when disabled)."
  value       = var.enable_telemetry_bq ? google_bigquery_dataset.lakehouse[0].dataset_id : ""
}

output "operational_facts_iceberg_table_fqn" {
  description = "Fully qualified BigQuery table (project.docubridge_lakehouse.bq_operational_facts_iceberg) for Zone 4 operational facts (\"\" when disabled)."
  value       = var.enable_telemetry_bq ? "${var.project_id}.${google_bigquery_dataset.lakehouse[0].dataset_id}.${google_bigquery_table.operational_facts_iceberg[0].table_id}" : ""
}
