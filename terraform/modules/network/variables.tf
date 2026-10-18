variable "project_id" {
  type        = string
  description = "GCP project id."
}

variable "region" {
  type        = string
  description = "Region for the subnets."
}

variable "network_name" {
  type        = string
  description = "Name of the custom-mode VPC."
  default     = "bridge-vpc"
}

# The two subnets model the two trust zones (design/zones.md): external is the
# untrusted ingress/DMZ side, internal is the trusted side where the private-IP
# Cloud SQL path (P7-2) attaches. CIDRs must not overlap.
variable "external_subnet_cidr" {
  type        = string
  description = "CIDR for the external (untrusted/DMZ ingress) zone subnet."
  default     = "10.10.0.0/24"
}

variable "internal_subnet_cidr" {
  type        = string
  description = "CIDR for the internal (trusted) zone subnet."
  default     = "10.20.0.0/24"
}

variable "enable_agent_engine_psc" {
  type        = bool
  description = "Whether to provision a Private Service Connect network attachment on the internal subnet for Vertex AI Agent Engine private VPC egress."
  default     = false
}

variable "labels" {
  type        = map(string)
  description = "Resource labels."
  default     = {}
}
