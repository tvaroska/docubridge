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

  assert {
    condition     = google_secret_manager_secret.db_url.secret_id != ""
    error_message = "secret container must exist"
  }

  assert {
    condition     = length(google_secret_manager_secret_version.db_url) == 0 && length(google_secret_manager_secret_version.spiffe_svid) == 0
    error_message = "default credential-free plan must create no secret versions unless manage_*_version = true"
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

run "single_apply_secret_versions_when_managed" {
  command = plan

  module {
    source = "./modules/secrets"
  }

  variables {
    project_id                 = "test-project"
    service_account_member     = "serviceAccount:bridge-runtime@test-project.iam.gserviceaccount.com"
    manage_db_url_version      = true
    db_dsn                     = "postgresql+asyncpg://bridge:secret@10.20.0.5:5432/bridge"
    manage_spiffe_svid_version = true
    spiffe_svid_secret_data    = "spiffe-svid-hmac-secret"
  }

  assert {
    condition     = length(google_secret_manager_secret_version.db_url) == 1
    error_message = "manage_db_url_version = true must plan the DATABASE_URL secret version for single-pass apply"
  }

  assert {
    condition     = length(google_secret_manager_secret_version.spiffe_svid) == 1
    error_message = "manage_spiffe_svid_version = true must plan the SPIFFE SVID secret version for single-pass apply"
  }
}
