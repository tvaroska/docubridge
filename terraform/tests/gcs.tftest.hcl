# Credential-free plan-time test of the gcs module (Skill Registry + Artifacts
# buckets, P7-3). mock_provider = never authenticated; command = plan = nothing
# created. Assertions are on input-derived / statically-known values (bucket
# config, IAM role/member), never mock-computed ids.
#
# Load-bearing security invariant (design/gcp-substrate.md): the buckets are NEVER
# public (UBLA + public_access_prevention ENFORCED) — signed V4 URLs are the only
# external byte path. ART-1 versioning + ART-3 lifecycle Delete on the artifacts
# bucket; least-privilege per-bucket objectAdmin to the bridge-runtime SA.

mock_provider "google" {}
mock_provider "google-beta" {}

run "buckets_are_private_versioned_retained_and_scoped" {
  command = plan

  module {
    source = "./modules/gcs"
  }

  variables {
    project_id             = "test-project"
    name_prefix            = "bridge-runtime"
    service_account_member = "serviceAccount:bridge-runtime@test-project.iam.gserviceaccount.com"
    retention_days         = 30
  }

  # Bucket names track the prefix (input-derived, statically known).
  assert {
    condition     = google_storage_bucket.skills.name == "bridge-runtime-skills"
    error_message = "skills bucket name must be <name_prefix>-skills"
  }
  assert {
    condition     = google_storage_bucket.artifacts.name == "bridge-runtime-artifacts"
    error_message = "artifacts bucket name must be <name_prefix>-artifacts"
  }

  # NEVER public: UBLA on both buckets.
  assert {
    condition     = google_storage_bucket.skills.uniform_bucket_level_access == true
    error_message = "skills bucket must enable uniform bucket-level access"
  }
  assert {
    condition     = google_storage_bucket.artifacts.uniform_bucket_level_access == true
    error_message = "artifacts bucket must enable uniform bucket-level access"
  }

  # NEVER public: public access prevention ENFORCED on both buckets.
  assert {
    condition     = google_storage_bucket.skills.public_access_prevention == "enforced"
    error_message = "skills bucket must enforce public access prevention (never public)"
  }
  assert {
    condition     = google_storage_bucket.artifacts.public_access_prevention == "enforced"
    error_message = "artifacts bucket must enforce public access prevention (never public)"
  }

  # Versioning on both (ART-1 history / re-authored skill = new version).
  assert {
    condition     = google_storage_bucket.skills.versioning[0].enabled == true
    error_message = "skills bucket must enable versioning"
  }
  assert {
    condition     = google_storage_bucket.artifacts.versioning[0].enabled == true
    error_message = "artifacts bucket must enable versioning (ART-1 resubmission history)"
  }

  # ART-3 retention: the artifacts bucket declares a lifecycle Delete rule.
  # lifecycle_rule / action are SETs (not indexable) — scan for a Delete action.
  assert {
    condition = anytrue([
      for r in google_storage_bucket.artifacts.lifecycle_rule :
      anytrue([for a in r.action : a.type == "Delete"])
    ])
    error_message = "artifacts bucket must declare a lifecycle Delete rule (ART-3 retention)"
  }

  # Least-privilege, per-bucket: objectAdmin to the bridge-runtime SA on each bucket.
  assert {
    condition     = google_storage_bucket_iam_member.skills_object_admin.role == "roles/storage.objectAdmin"
    error_message = "skills bucket grant must be roles/storage.objectAdmin"
  }
  assert {
    condition     = google_storage_bucket_iam_member.artifacts_object_admin.role == "roles/storage.objectAdmin"
    error_message = "artifacts bucket grant must be roles/storage.objectAdmin"
  }
  assert {
    condition     = google_storage_bucket_iam_member.artifacts_object_admin.member == var.service_account_member
    error_message = "the objectAdmin grant must be to the passed bridge-runtime SA member"
  }

  assert {
    condition     = length(keys(google_storage_bucket_object.skill_files)) == 0
    error_message = "skills bucket must start empty by default (seed_skills = false) until use-case agents onboard"
  }
}
