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
