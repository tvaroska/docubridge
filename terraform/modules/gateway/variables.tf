variable "project_id" {
  type        = string
  description = "GCP project id the gateway ingress resources live in."
}

variable "region" {
  type        = string
  description = "Region of the Cloud Run runtime the Serverless NEG points at."
}

variable "service_name" {
  type        = string
  description = "The Cloud Run service the Serverless NEG fronts and per-party invoker grants scope (module.runtime.service_name)."
}

variable "enable_gateway" {
  type        = bool
  description = <<-EOT
    The gateway toggle. When false (local/dev default) the module creates NOTHING
    — every resource is count/for_each-gated on it, so the P7-1 base plan is
    untouched. When true it builds the external HTTPS LB → Serverless NEG ingress
    chain on the runtime plus the per-party run.invoker scoping (design/zones.md).
  EOT
  default     = false
}

variable "party_invoker_members" {
  type        = list(string)
  description = <<-EOT
    Per-party identities granted roles/run.invoker on the runtime service — the
    "a party can address only its own leg" ingress scoping (design/zones.md).
    Empty at plan time; populated at deploy (P7-9) with the counterparty SAs.
  EOT
  default     = []
}

variable "ssl_domains" {
  type        = list(string)
  description = <<-EOT
    Managed-cert domains for the external HTTPS LB. May be empty at plan time (the
    managed certificate resource is gated on non-empty; the LB URL / BRIDGE_PUBLIC_URL
    is then injected out-of-band at deploy, mirroring followup_callback_url).
  EOT
  default     = []
}

variable "labels" {
  type        = map(string)
  description = "Resource labels applied to the gateway resources that support them."
  default     = {}
}
