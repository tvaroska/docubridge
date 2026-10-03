variable "project_id" {
  type        = string
  description = "GCP project id the queue lives in."
}

variable "region" {
  type        = string
  description = "Queue location (tracks the runtime region so the OIDC callback stays in-region)."
}

variable "queue_name" {
  type        = string
  description = "Cloud Tasks queue name — the follow-up ladder's queue."
  default     = "bridge-followups"
}

# --- retry / rate-limit semantics for the follow-up delivery ---------------
# Explicit so the OIDC callback POST has bounded, testable retry behavior (a
# transient runtime blip retries with backoff rather than dropping the follow-up).

variable "max_attempts" {
  type        = number
  description = "Max delivery attempts for a follow-up task before it is dropped."
  default     = 5
}

variable "min_backoff" {
  type        = string
  description = "Minimum backoff between retries (duration string, e.g. 10s)."
  default     = "10s"
}

variable "max_backoff" {
  type        = string
  description = "Maximum backoff between retries (duration string, e.g. 300s)."
  default     = "300s"
}

variable "max_doublings" {
  type        = number
  description = "Number of times the backoff doubles before growing linearly."
  default     = 3
}

variable "max_retry_duration" {
  type        = string
  description = "Max total time to keep retrying a task (duration string; 0s = unlimited within max_attempts)."
  default     = "0s"
}

variable "max_dispatches_per_second" {
  type        = number
  description = "Rate limit — max task dispatches per second from the queue."
  default     = 10
}

variable "max_concurrent_dispatches" {
  type        = number
  description = "Rate limit — max concurrent outstanding dispatches from the queue."
  default     = 10
}

# Declared for interface parity with the other substrate modules (root passes
# var.labels). Cloud Tasks queues do NOT support a labels argument, so it is not
# applied to the resource — kept so root wiring is uniform.
variable "labels" {
  type        = map(string)
  description = "Resource labels (not applied — Cloud Tasks queues have no labels arg; kept for interface parity)."
  default     = {}
}
