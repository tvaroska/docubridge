# tasks — the bridge-followups Cloud Tasks queue (P7-4).
#
# The substrate behind the scheduler seam's GCP adapter (GcpScheduler): the
# clock-driven follow-up ladder arms one Cloud Task per exchange, fired by the
# queue POSTing an OIDC-authenticated request to the runtime's follow-up callback
# at schedule_time (design/gcp-substrate.md §"tasks = the bridge-followups queue").
#
# NO IAM in this module — the enqueuer grant (roles/cloudtasks.enqueuer) and the
# SA-scoped OIDC token-creator (roles/iam.serviceAccountTokenCreator) already live
# in modules/iam (P7-1). P7-4 CONSUMES them; it adds NO new grant.
#
# CRITICAL — No secrets / plaintext values / task objects in HCL (CRITICAL.md). A
# queue is a pure container; tasks are RUNTIME data armed through the seam adapter
# (GcpScheduler.schedule), never Terraform-managed.

resource "google_cloud_tasks_queue" "bridge_followups" {
  project  = var.project_id
  name     = var.queue_name
  location = var.region

  # Explicit, testable delivery semantics for the follow-up POST. The queue
  # retries a failed OIDC callback (transient runtime unavailability) with bounded
  # backoff rather than dropping the follow-up.
  rate_limits {
    max_dispatches_per_second = var.max_dispatches_per_second
    max_concurrent_dispatches = var.max_concurrent_dispatches
  }

  retry_config {
    max_attempts       = var.max_attempts
    min_backoff        = var.min_backoff
    max_backoff        = var.max_backoff
    max_doublings      = var.max_doublings
    max_retry_duration = var.max_retry_duration
  }
}
