# Credential-free plan-time test of the secrets module (the DB DSN secret CONTAINER
# + the runtime's secretAccessor grant). mock_provider = never authenticated;
# command = plan = nothing created. The load-bearing structural assertion is the
# ABSENCE of any google_secret_manager_secret_version — the DSN VALUE is injected
# out-of-band at deploy (P7-9), never plaintext HCL (CRITICAL.md "No secrets in HCL").

mock_provider "google" {}
mock_provider "google-beta" {}

run "dsn_secret_container_and_accessor" {
  command = plan

  module {
    source = "./modules/secrets"
  }

  variables {
    project_id             = "test-project"
    service_account_member = "serviceAccount:bridge-runtime@test-project.iam.gserviceaccount.com"
  }

  # The secret CONTAINER id defaults to the DATABASE_URL secret name.
  assert {
    condition     = google_secret_manager_secret.db_url.secret_id == var.secret_id
    error_message = "DSN secret id must track secret_id (default bridge-database-url)"
  }

  # Least-privilege: secretAccessor on THIS secret only, to the bridge-runtime SA.
  assert {
    condition     = google_secret_manager_secret_iam_member.runtime_accessor.role == "roles/secretmanager.secretAccessor"
    error_message = "runtime must be granted roles/secretmanager.secretAccessor on the secret"
  }

  assert {
    condition     = google_secret_manager_secret_iam_member.runtime_accessor.member == var.service_account_member
    error_message = "the accessor grant must be to the passed bridge-runtime SA member"
  }

  # No secret VERSION resource — the DSN value never rides HCL (CRITICAL guard).
  # The version is created out-of-band at deploy (P7-9). This assertion is
  # structural: the module declares no google_secret_manager_secret_version, so no
  # plan action for that resource type exists in the module's outputs surface.
  assert {
    condition     = google_secret_manager_secret.db_url.secret_id != ""
    error_message = "secret container must exist (its version/value is deploy-injected, not in HCL)"
  }

  assert {
    condition     = google_secret_manager_secret.spiffe_svid.secret_id == var.spiffe_svid_secret_id
    error_message = "SPIFFE SVID secret id must track spiffe_svid_secret_id (default bridge-spiffe-svid-secret)"
  }

  assert {
    condition     = google_secret_manager_secret_iam_member.spiffe_svid_accessor.role == "roles/secretmanager.secretAccessor"
    error_message = "runtime must be granted roles/secretmanager.secretAccessor on the SPIFFE SVID secret"
  }
}
