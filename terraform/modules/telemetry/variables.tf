variable "project_id" {
  type        = string
  description = "GCP project id."
}

variable "region" {
  type        = string
  description = "BigQuery dataset location (e.g. us-central1)."
  default     = "us-central1"
}

variable "enable_telemetry_bq" {
  type        = bool
  description = "Whether to provision the BigQuery OpenTelemetry trace analytics dataset, partitioned span table, SQL views, and Cloud Logging sink."
  default     = false
}

variable "dataset_id" {
  type        = string
  description = "BigQuery dataset id for OpenTelemetry trace analytics."
  default     = "docubridge_telemetry"
}

variable "lakehouse_dataset_id" {
  type        = string
  description = "BigQuery dataset id for the Zone 4 Iceberg operational facts lakehouse."
  default     = "docubridge_lakehouse"
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
