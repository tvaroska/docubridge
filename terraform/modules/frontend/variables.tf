variable "project_id" {
  type        = string
  description = "GCP project id the frontend buckets + LB live in."
}

variable "name_prefix" {
  type        = string
  description = "Prefix for the bucket names (<prefix>-portal, <prefix>-console, <prefix>-dashboard). Bucket names are global; the deploy passes a project-unique prefix (mirrors the gcs module's name_prefix)."
  default     = "bridge"
}

variable "location" {
  type        = string
  description = "Bucket location (region or multi-region)."
  default     = "US"
}

variable "region" {
  type        = string
  description = "Region kept for parity with the gateway module signature; the external HTTPS LB fronting the portal is itself global."
  default     = "us-central1"
}

variable "enable_frontend" {
  type        = bool
  description = <<-EOT
    The frontend toggle. When false (local/dev default) the module creates NOTHING
    — every resource is count/for_each-gated on it, so the credential-free plan-time
    default is empty. When true it provisions the external-zone portal (public GCS
    bucket behind an external HTTPS LB + Cloud CDN) and the internal-zone console +
    dashboard staging buckets (private). Mirrors the gateway module's enable_gateway
    (design/zones.md).
  EOT
  default     = false
}

variable "ssl_domains" {
  type        = list(string)
  description = <<-EOT
    Managed-cert domains for the portal's external HTTPS LB. May be empty at plan
    time (the managed certificate resource is gated on non-empty; the LB URL is then
    injected out-of-band at deploy/P7-9, mirroring the gateway module's ssl_domains).
  EOT
  default     = []
}

variable "labels" {
  type        = map(string)
  description = "Resource labels applied to the frontend resources that support them."
  default     = {}
}
