output "queue_name" {
  description = "The Cloud Tasks queue name (bridge-followups)."
  value       = google_cloud_tasks_queue.bridge_followups.name
}

output "queue_id" {
  description = "The fully-qualified queue id."
  value       = google_cloud_tasks_queue.bridge_followups.id
}

# The full queue resource path — the value the runtime injects as the
# BRIDGE_TASKS_QUEUE plain env (the GcpScheduler queue_path). Constructed from
# statically-known / input-derived parts (not a provider-computed id), so a
# plan-time test can assert it with no credentials.
output "queue_path" {
  description = "Full queue resource path (projects/<p>/locations/<r>/queues/<name>) — the BRIDGE_TASKS_QUEUE runtime env."
  value       = "projects/${var.project_id}/locations/${var.region}/queues/${google_cloud_tasks_queue.bridge_followups.name}"
}
