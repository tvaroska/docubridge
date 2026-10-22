# Root outputs — the substrate surface later P7 primitives + the deploy step read.

output "network_self_link" {
  description = "The VPC self_link."
  value       = module.network.network_self_link
}

output "internal_subnet_self_link" {
  description = "Internal subnet self_link — the private-IP/PSA anchor Cloud SQL attaches to (P7-2)."
  value       = module.network.internal_subnet_self_link
}

output "external_subnet_id" {
  description = "External (DMZ/ingress) subnet id."
  value       = module.network.external_subnet_id
}

output "internal_subnet_id" {
  description = "Internal (trusted) subnet id."
  value       = module.network.internal_subnet_id
}

output "agent_engine_network_attachment_id" {
  description = "PSC network attachment id for Vertex AI Agent Engine private VPC egress (\"\" when enable_agent_engine_psc = false)."
  value       = module.network.agent_engine_network_attachment_id
}

output "bridge_runtime_email" {
  description = "The bridge-runtime workload SA email."
  value       = module.iam.bridge_runtime_email
}

output "bridge_runtime_member" {
  description = "The bridge-runtime SA IAM member string (reused by tasks/database, P7-2/P7-4)."
  value       = module.iam.bridge_runtime_member
}

output "docai_grant" {
  description = "The cross-project Document AI grant { project, role, member } for the client's processor project (null without docai_processor). Apply it on the client side when manage_docai_grant = false."
  value       = module.iam.docai_grant
}

output "runtime_service_name" {
  description = "The Cloud Run runtime service name."
  value       = module.runtime.service_name
}

output "runtime_service_uri" {
  description = "The Cloud Run runtime service URI."
  value       = module.runtime.service_uri
}

output "runtime_ingress" {
  description = "The runtime's computed ingress mode (from enable_gateway)."
  value       = module.runtime.computed_ingress
}

# Cloud SQL + secret surface (P7-2) — the deploy step (P7-9) composes the DSN from
# the private IP + instance/db names and injects it into the secret; none of these
# is the DSN itself (the DSN carries the credential, never a Terraform value).
output "db_instance_name" {
  description = "The Cloud SQL instance name."
  value       = module.database.instance_name
}

output "db_private_ip" {
  description = "The Cloud SQL private IP (the VPC-only endpoint the DSN targets)."
  value       = module.database.private_ip_address
}

output "db_name" {
  description = "The application database name (the deploy step composes it into the DSN — not itself the DSN, which carries the credential)."
  value       = module.database.database_name
}

output "db_user" {
  description = "The application SQL user (the deploy step composes it into the DSN — not itself the DSN, which carries the credential)."
  value       = module.database.db_user
}

output "db_secret_id" {
  description = "The Secret Manager secret id the runtime reads as the DATABASE_URL env."
  value       = module.secrets.secret_id
}

output "spiffe_svid_secret_id" {
  description = "The Secret Manager secret id the runtime reads as the BRIDGE_SPIFFE_SVID_SECRET env."
  value       = module.secrets.spiffe_svid_secret_id
}

# GCS surface (P7-3) — the bucket names the runtime reads as BRIDGE_SKILLS_BUCKET /
# BRIDGE_ARTIFACT_BUCKET (plain envs; bucket names are not secrets).
output "skills_bucket_name" {
  description = "The Skill Registry GCS bucket name (BRIDGE_SKILLS_BUCKET runtime env)."
  value       = module.gcs.skills_bucket_name
}

output "artifacts_bucket_name" {
  description = "The Artifacts GCS bucket name (BRIDGE_ARTIFACT_BUCKET runtime env; GcsArtifactService + signed URLs)."
  value       = module.gcs.artifacts_bucket_name
}

output "audit_vault_bucket_name" {
  description = "The immutable WORM Audit Vault GCS bucket name (BRIDGE_VAULT_BUCKET runtime env; \"\" when disabled)."
  value       = module.gcs.audit_vault_bucket_name
}

# Cloud Tasks surface (P7-4) — the queue the runtime injects as BRIDGE_TASKS_QUEUE
# (the GcpScheduler queue_path; a queue path is not a secret, so it rides a plain env).
output "tasks_queue_name" {
  description = "The bridge-followups Cloud Tasks queue name."
  value       = module.tasks.queue_name
}

output "tasks_queue_path" {
  description = "The full Cloud Tasks queue resource path (BRIDGE_TASKS_QUEUE runtime env)."
  value       = module.tasks.queue_path
}

# Gateway ingress surface (P7-5) — the external HTTPS LB IP + the Gateway-authorized
# public URL. Both "" when enable_gateway = false. gateway_url feeds the deploy step's
# out-of-band BRIDGE_PUBLIC_URL injection (served-card same-origin, adk >=2.7).
output "gateway_ip" {
  description = "The external HTTPS LB anycast IP (\"\" when the gateway is disabled)."
  value       = module.gateway.gateway_ip
}

output "gateway_url" {
  description = "The Gateway-authorized public HTTPS URL (\"\" when disabled / no domain); feeds BRIDGE_PUBLIC_URL at deploy (P7-9)."
  value       = module.gateway.gateway_url
}

output "gateway_security_policy_name" {
  description = "The Cloud Armor edge security policy name attached to the runtime backend service (\"\" when disabled)."
  value       = module.gateway.security_policy_name
}

# Frontend serving surface (P7-7) — the deploy step (P7-9) rsyncs each app's built
# dist/ into these buckets and injects each app's VITE_*_URL from the resolved
# deployed endpoints. All "" when enable_frontend = false. The portal is public
# (external zone); the console/dashboard buckets are private staging (internal zone).
output "portal_bucket_name" {
  description = "The provider-portal GCS bucket name — the deploy step rsyncs its built dist/ into it (\"\" when disabled)."
  value       = module.frontend.portal_bucket_name
}

output "console_bucket_name" {
  description = "The agent-console GCS bucket name (private staging) — the deploy step rsyncs its built dist/ into it (\"\" when disabled)."
  value       = module.frontend.console_bucket_name
}

output "dashboard_bucket_name" {
  description = "The ops-dashboard GCS bucket name (private staging) — the deploy step rsyncs its built dist/ into it (\"\" when disabled)."
  value       = module.frontend.dashboard_bucket_name
}

output "portal_ip" {
  description = "The portal external HTTPS LB anycast IP (\"\" when the frontend is disabled)."
  value       = module.frontend.portal_ip
}

output "portal_url" {
  description = "The portal public HTTPS URL (\"\" when disabled / no domain); feeds the provider-portal VITE_PORTAL_URL build env at deploy (P7-9)."
  value       = module.frontend.portal_url
}

# Email intake surface (S0-arch-13). All "" when enable_email_ingress = false. The
# operator subscribes Graph with notificationUrl = <email_webhook_uri>/email/graph/
# notifications (`python -m bridge.email_ingress subscribe`, design/email-intake.md).
output "email_bucket_name" {
  description = "The email landing bucket (BRIDGE_EMAIL_BUCKET) (\"\" when disabled)."
  value       = module.gcs.email_bucket_name
}

output "email_webhook_email" {
  description = "The email intake webhook SA email (\"\" when disabled)."
  value       = module.iam.email_webhook_email
}

output "email_webhook_uri" {
  description = "The public webhook-only email intake service URI (\"\" when disabled)."
  value       = module.runtime.email_webhook_uri
}

# The RFP board host (S0-arch-17). "" when enable_rfp_board = false (the job also needs
# enable_email_ingress). Internal-only: reached by Cloud Scheduler with an OIDC token.
output "rfp_board_uri" {
  description = "The internal RFP board host service URI (\"\" when disabled)."
  value       = module.runtime.rfp_board_uri
}

output "email_drain_job" {
  description = "The Cloud Scheduler job driving POST /email/drain on the RFP board (\"\" when disabled)."
  value       = module.runtime.email_drain_job
}

output "telemetry_bq_dataset_id" {
  description = "BigQuery dataset id for OpenTelemetry trace analytics (\"\" when disabled)."
  value       = module.telemetry.dataset_id
}

output "telemetry_bq_spans_table" {
  description = "Fully qualified BigQuery table (project.dataset.otel_spans) for OpenTelemetry trace spans (\"\" when disabled)."
  value       = module.telemetry.spans_table_fqn
}

output "telemetry_bq_step_latency_view" {
  description = "Fully qualified BigQuery view for step p50/p95/p99 latency analytics (\"\" when disabled)."
  value       = module.telemetry.step_latency_view_fqn
}

output "telemetry_bq_trace_waterfall_view" {
  description = "Fully qualified BigQuery view for chronological trace waterfall (\"\" when disabled)."
  value       = module.telemetry.trace_waterfall_view_fqn
}

output "telemetry_bq_entity_summary_view" {
  description = "Fully qualified BigQuery view for per-entity pipeline trace summary (\"\" when disabled)."
  value       = module.telemetry.entity_summary_view_fqn
}

output "telemetry_bq_trace_sql_evaluation_view" {
  description = "Fully qualified BigQuery view for Rule 0 deterministic SQL trajectory evaluation and selective AI.GENERATE_TABLE sampling (\"\" when disabled)."
  value       = module.telemetry.trace_sql_evaluation_view_fqn
}

output "telemetry_lakehouse_dataset_id" {
  description = "BigQuery dataset id for the Zone 4 operational facts lakehouse (\"\" when disabled)."
  value       = module.telemetry.lakehouse_dataset_id
}

output "telemetry_operational_facts_iceberg_table" {
  description = "Fully qualified BigQuery table (project.docubridge_lakehouse.bq_operational_facts_iceberg) for Zone 4 operational facts (\"\" when disabled)."
  value       = module.telemetry.operational_facts_iceberg_table_fqn
}

