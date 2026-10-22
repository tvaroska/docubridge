# Credential-free plan-time test of the iam module (the bridge-runtime workload
# identity + least-privilege grants). Asserts the exact role set the design names
# and the SA-scoped Cloud Tasks OIDC token-creator binding.

mock_provider "google" {}
mock_provider "google-beta" {}

run "base_role_set_and_token_creator" {
  command = plan

  module {
    source = "./modules/iam"
  }

  variables {
    project_id   = "test-project"
    service_name = "bridge-runtime"
  }

  assert {
    condition     = google_service_account.bridge_runtime.account_id == "bridge-runtime"
    error_message = "SA account_id must equal service_name"
  }

  assert {
    condition     = contains(output.granted_roles, "roles/cloudsql.client")
    error_message = "bridge-runtime SA must be granted roles/cloudsql.client"
  }

  assert {
    condition     = contains(output.granted_roles, "roles/cloudsql.instanceUser")
    error_message = "bridge-runtime SA must be granted roles/cloudsql.instanceUser for IAM DB authentication"
  }

  assert {
    condition     = contains(output.granted_roles, "roles/cloudtasks.enqueuer")
    error_message = "bridge-runtime SA must be granted roles/cloudtasks.enqueuer"
  }

  assert {
    condition     = contains(output.granted_roles, "roles/aiplatform.user")
    error_message = "bridge-runtime SA must be granted roles/aiplatform.user"
  }

  # Least-privilege: none of the dangerous project-wide roles.
  assert {
    condition     = !contains(output.granted_roles, "roles/owner") && !contains(output.granted_roles, "roles/editor")
    error_message = "bridge-runtime SA must NOT hold roles/owner or roles/editor"
  }

  # Cloud Build identity: the compute default SA gets the purpose-built
  # builds.builder role so a fresh-project image build works without a manual
  # gcloud grant (repeatable IaC). The build_sa grants key by role, so the
  # resource keys are the statically-known intent (the member — the compute SA —
  # resolves post-plan from the project number).
  assert {
    condition     = contains(output.build_sa_roles, "roles/cloudbuild.builds.builder")
    error_message = "Cloud Build SA must be granted roles/cloudbuild.builds.builder"
  }

  assert {
    condition     = contains(keys(google_project_iam_member.build_sa), "roles/cloudbuild.builds.builder")
    error_message = "build SA grant must be keyed by the builds.builder role"
  }

  # The Cloud Tasks OIDC token-creator is scoped to the SA resource itself
  # (google_service_account_iam_member, never a project-level binding). The
  # planned role is the statically-known intent; the SA-resource scope is
  # structural (this is not a google_project_iam_member).
  assert {
    condition     = google_service_account_iam_member.tasks_token_creator.role == "roles/iam.serviceAccountTokenCreator"
    error_message = "token-creator binding must grant roles/iam.serviceAccountTokenCreator"
  }

  # No Document AI processor configured → no cross-project grant at all.
  assert {
    condition     = length(google_project_iam_member.docai_api_user) == 0 && output.docai_grant == null
    error_message = "no docai_processor must mean no Document AI grant"
  }
}

# The docai extraction engine: the client's processor lives in the CLIENT's project,
# so the runtime SA gets roles/documentai.apiUser on THAT project (cross-project,
# least privilege) — never in base_roles/granted_roles (the Bridge's own project).
run "docai_grant_on_the_processor_project" {
  command = plan

  module {
    source = "./modules/iam"
  }

  variables {
    project_id      = "test-project"
    docai_processor = "projects/client-project/locations/eu/processors/abc123"
  }

  assert {
    condition     = length(google_project_iam_member.docai_api_user) == 1
    error_message = "a configured processor must produce exactly one Document AI grant"
  }

  assert {
    condition     = google_project_iam_member.docai_api_user[0].project == "client-project"
    error_message = "the Document AI grant must be on the processor's (client's) project"
  }

  assert {
    condition     = google_project_iam_member.docai_api_user[0].role == "roles/documentai.apiUser"
    error_message = "the Document AI grant must be roles/documentai.apiUser (never documentai.admin)"
  }

  assert {
    condition     = !contains(output.granted_roles, "roles/documentai.apiUser") && !contains(output.granted_roles, "roles/documentai.admin")
    error_message = "Document AI must not be granted on the Bridge's own project"
  }

  assert {
    condition     = output.docai_grant.project == "client-project" && output.docai_grant.role == "roles/documentai.apiUser"
    error_message = "docai_grant output must describe the cross-project grant"
  }
}

# The client grants out-of-band (the applying identity has no setIamPolicy on the
# client's project): no grant resource here, but the output still says what to grant.
run "docai_grant_out_of_band" {
  command = plan

  module {
    source = "./modules/iam"
  }

  variables {
    project_id         = "test-project"
    docai_processor    = "projects/client-project/locations/us/processors/abc123/processorVersions/pretrained-ocr-v2.0-2023-06-02"
    manage_docai_grant = false
  }

  assert {
    condition     = length(google_project_iam_member.docai_api_user) == 0
    error_message = "manage_docai_grant = false must not apply the cross-project grant"
  }

  assert {
    condition     = output.docai_grant.project == "client-project" && output.docai_grant.role == "roles/documentai.apiUser"
    error_message = "the docai_grant output must still name the grant for the client to apply"
  }
}

run "docai_processor_must_be_a_resource_name" {
  command = plan

  module {
    source = "./modules/iam"
  }

  variables {
    project_id      = "test-project"
    docai_processor = "projects/client-project/processors/abc123"
  }

  expect_failures = [var.docai_processor]
}
