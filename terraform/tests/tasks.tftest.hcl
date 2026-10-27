# Credential-free plan-time test of the tasks module (the bridge-followups Cloud
# Tasks queue, P7-4). mock_provider = never authenticated; command = plan = nothing
# created. Assertions are on input-derived / statically-known values (queue name,
# location, retry_config, constructed queue_path), never mock-computed ids.
#
# CRITICAL invariant (CRITICAL.md): the module declares NO IAM and NO task objects
# — a queue is a pure container; the enqueuer + OIDC token-creator grants already
# live in modules/iam (P7-1) and tasks are runtime data armed through the port
# adapter (GcpScheduler.schedule), never Terraform-managed.

mock_provider "google" {}
mock_provider "google-beta" {}

run "queue_name_location_retry_and_path" {
  command = plan

  module {
    source = "./modules/tasks"
  }

  variables {
    project_id = "test-project"
    region     = "us-central1"
  }

  # Queue name is the follow-up ladder's queue (default input-derived).
  assert {
    condition     = google_cloud_tasks_queue.bridge_followups.name == "bridge-followups"
    error_message = "queue name must default to bridge-followups"
  }

  # Location tracks the region (so the OIDC callback stays in-region).
  assert {
    condition     = google_cloud_tasks_queue.bridge_followups.location == "us-central1"
    error_message = "queue location must track var.region"
  }

  # Explicit, testable retry semantics for the follow-up delivery.
  assert {
    condition     = google_cloud_tasks_queue.bridge_followups.retry_config[0].max_attempts == 5
    error_message = "queue must declare a retry_config with the expected max_attempts"
  }

  # queue_path is the value the runtime injects as BRIDGE_TASKS_QUEUE — constructed
  # from statically-known / input-derived parts (not a provider-computed id).
  assert {
    condition     = output.queue_path == "projects/test-project/locations/us-central1/queues/bridge-followups"
    error_message = "queue_path must be projects/<p>/locations/<r>/queues/bridge-followups"
  }
}
