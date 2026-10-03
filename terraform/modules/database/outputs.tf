# Outputs feed the DSN the deploy step (P7-9) composes into the Secret Manager
# secret — none of these are the DSN itself (the DSN carries the credential and is
# never a Terraform value; see the secrets module + CRITICAL.md "No secrets in HCL").

output "instance_name" {
  description = "The Cloud SQL instance name."
  value       = google_sql_database_instance.bridge.name
}

output "instance_connection_name" {
  description = "The instance connection name (project:region:instance)."
  value       = google_sql_database_instance.bridge.connection_name
}

output "private_ip_address" {
  description = "The instance's private IP address (the VPC-only endpoint the DSN targets)."
  value       = google_sql_database_instance.bridge.private_ip_address
}

output "database_name" {
  description = "The application database name."
  value       = google_sql_database.bridge.name
}

output "db_user" {
  description = "The application SQL user name."
  value       = google_sql_user.bridge.name
}
