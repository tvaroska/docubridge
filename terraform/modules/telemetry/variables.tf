variable "project_id" {
  type        = string
  description = "GCP project id."
}

variable "region" {
  type        = string
  description = "Regional location for App Hub applications and lakehouse dataset (e.g. us-central1)."
  default     = "us-central1"
}

variable "enable_telemetry_bq" {
  type        = bool
  description = "Whether to provision native Cloud Observability linked datasets, GenAI payload GCS bucket + BigLake external table, App Hub applications, and the Zone 4 lakehouse dataset."
  default     = false
}

variable "lakehouse_dataset_id" {
  type        = string
  description = "BigQuery dataset id for the Zone 4 Iceberg operational facts lakehouse."
  default     = "docubridge_lakehouse"
}

variable "payload_retention_days" {
  type        = number
  description = "Retention TTL (days) for GenAI completion payload JSONL objects in Cloud Storage."
  default     = 90
}

variable "service_account_member" {
  type        = string
  description = "IAM member string for the bridge-runtime workload SA."
}

variable "deletion_protection" {
  type        = bool
  description = "Whether to enable deletion protection on BigQuery tables and dataset contents."
  default     = true
}

variable "labels" {
  type        = map(string)
  description = "Resource labels."
  default     = {}
}

variable "single_bucket_mode" {
  type        = bool
  description = "When true, skip creating <project_id>-genai-payloads and point BigLake completions + CEL-scoped IAM at gs://<shared_bucket>/genai-payloads/."
  default     = false
}

variable "shared_bucket_name" {
  type        = string
  description = "Shared single-bucket GCS name used when single_bucket_mode = true (defaults to var.project_id)."
  default     = ""
}

variable "create_eval_dataset" {
  type        = bool
  description = "When true, create the BigQuery eval dataset (agent_eval); when false, adopt the existing dataset via data.google_bigquery_dataset (co-located install)."
  default     = true
}

variable "eval_dataset_id" {
  type        = string
  description = "BigQuery dataset ID for evaluation views and BigLake completions table."
  default     = "agent_eval"
}

variable "create_vertex_connection" {
  type        = bool
  description = "When true, create the BigQuery Vertex AI / BigLake cloud resource connection; when false, reference the existing connection without colliding (co-located install)."
  default     = true
}

variable "vertex_connection_id" {
  type        = string
  description = "BigQuery Cloud Resource connection ID for BigLake and AI.GENERATE_TABLE."
  default     = "vertex_ai_connection"
}

variable "vertex_connection_location" {
  type        = string
  description = "BigQuery location of the Vertex AI / BigLake cloud resource connection."
  default     = "US"
}

variable "vertex_connection_service_account_email" {
  type        = string
  description = "Optional existing BigLake connection service account email used to grant prefix-scoped genai-payloads/ objectViewer when create_vertex_connection = false."
  default     = ""
}

variable "create_shared_bucket_lister_role" {
  type        = bool
  description = "When true in single_bucket_mode, create a minimal storage.objects.list custom role locally for BigLake; when false, reuse shared_bucket_lister_role_name from module.gcs."
  default     = true
}

variable "shared_bucket_lister_role_name" {
  type        = string
  description = "Custom list-only IAM role name passed from module.gcs when create_shared_bucket_lister_role = false."
  default     = ""
}
