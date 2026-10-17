# gcs — the Skill Registry bucket + the Artifacts bucket (P7-3).
#
# Two GCS buckets behind the skill_registry seam (GcpSkillRegistry) and the native
# GcsArtifactService backend swap (bridge/edge/artifacts.py). Both are NEVER public:
# uniform bucket-level access + public_access_prevention = ENFORCED. Signed V4 URLs
# (time-limited, minted only after the leg-scoping check) are the only external byte
# path — a load-bearing security invariant, not a style choice (design/gcp-substrate.md).
#
# CRITICAL — No bucket OBJECTS / secrets / plaintext values in HCL (CRITICAL.md).
# Skill folders and artifact bytes are RUNTIME data written through the seam /
# ArtifactService, never Terraform-managed. This module provisions the containers +
# their IAM only.

# Skill Registry bucket — a re-authored skill is a new object version (versioning on).
resource "google_storage_bucket" "skills" {
  project  = var.project_id
  name     = "${var.name_prefix}-skills"
  location = var.location
  labels   = var.labels

  uniform_bucket_level_access = true       # UBLA: no per-object ACLs
  public_access_prevention    = "enforced" # never public

  versioning {
    enabled = true # a re-authored skill folder is a new version
  }
}

# Artifacts bucket — ART-1 versioning (a resubmission is a new version) + ART-3
# retention (the post-exchange lifecycle Delete rule).
resource "google_storage_bucket" "artifacts" {
  project  = var.project_id
  name     = "${var.name_prefix}-artifacts"
  location = var.location
  labels   = var.labels

  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  versioning {
    enabled = true # ART-1: version-per-resubmission = the resubmission history
  }

  # ART-3 retention: delete objects only AFTER the retention window (never during
  # the exchange). The Bridge keeps zero artifact-GC/TTL code; this is the only
  # deletion path besides the explicit off-script erase (design/artifacts.md).
  lifecycle_rule {
    action {
      type = "Delete"
    }
    condition {
      age = var.retention_days
    }
  }

  # Expire superseded (ARCHIVED) versions after the same window so old resubmission
  # versions do not accumulate past retention.
  lifecycle_rule {
    action {
      type = "Delete"
    }
    condition {
      age        = var.retention_days
      with_state = "ARCHIVED"
    }
  }
}

# IAM (the ONLY new grant P7-3 adds): objectAdmin per-bucket to the bridge-runtime
# SA — write (install_skill / save_artifact) + read (fetch). Scoped to each bucket
# resource, NOT a project-level grant (least-privilege; mirrors the secrets module's
# per-resource secretAccessor, DECISIONS.md 2026-09-07 P7-2).
resource "google_storage_bucket_iam_member" "skills_object_admin" {
  bucket = google_storage_bucket.skills.name
  role   = "roles/storage.objectAdmin"
  member = var.service_account_member
}

resource "google_storage_bucket_iam_member" "artifacts_object_admin" {
  bucket = google_storage_bucket.artifacts.name
  role   = "roles/storage.objectAdmin"
  member = var.service_account_member
}

# Email landing bucket (S0-arch-13, design/email-intake.md) — the GCP adapter of the
# email_ingress seam (GcpEmailIngress): inbox/<key>.eml, attachments/<key>/<i>/<name>,
# processed/<key>.json. Never public (UBLA + PAP enforced, like the buckets above).
# Raw mail holds counterparty content, so retention is a lifecycle Delete after
# email_retention_days (ART-3: the lifecycle policy IS the retention; zero Bridge GC).
# No versioning: every write is create-only (if_generation_match=0), so there is no
# overwritten generation to keep. Gated: the default plan creates nothing.
resource "google_storage_bucket" "email" {
  count = var.enable_email_ingress ? 1 : 0

  project  = var.project_id
  name     = "${var.name_prefix}-email"
  location = var.location
  labels   = var.labels

  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  lifecycle_rule {
    action {
      type = "Delete"
    }
    condition {
      age = var.email_retention_days
    }
  }
}

# Create-only grants. The PUBLIC webhook SA gets objectCreator ONLY — it can land a
# new object but never read, list, overwrite, or delete one (an overwrite attempt is
# a 403 the adapter treats as a duplicate only when the object exists). The runtime
# SA gets objectViewer (list the inbox, read mail/markers) + objectCreator (write
# attachments + processed markers) — never objectAdmin on this bucket: nothing in
# the Bridge overwrites or deletes landed mail.
locals {
  email_bucket_grants = var.enable_email_ingress ? {
    webhook_creator = { role = "roles/storage.objectCreator", member = var.email_webhook_member }
    runtime_viewer  = { role = "roles/storage.objectViewer", member = var.service_account_member }
    runtime_creator = { role = "roles/storage.objectCreator", member = var.service_account_member }
  } : {}
}

resource "google_storage_bucket_iam_member" "email" {
  for_each = local.email_bucket_grants

  bucket = google_storage_bucket.email[0].name
  role   = each.value.role
  member = each.value.member
}

# Cloud Storage Audit Vault bucket (Zone 3 WORM compliance archive).
# Write-once, immutable object retention policy (no lifecycle Delete during the
# regulatory retention window; create-only + viewer IAM so runtime SA cannot
# overwrite or delete locked vault objects).
resource "google_storage_bucket" "audit_vault" {
  count = var.enable_audit_vault ? 1 : 0

  project  = var.project_id
  name     = "${var.name_prefix}-audit-vault"
  location = var.location
  labels   = var.labels

  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  retention_policy {
    is_locked        = var.lock_audit_vault_retention
    retention_period = var.vault_retention_days * 86400
  }
}

locals {
  audit_vault_bucket_grants = var.enable_audit_vault ? {
    runtime_viewer  = { role = "roles/storage.objectViewer", member = var.service_account_member }
    runtime_creator = { role = "roles/storage.objectCreator", member = var.service_account_member }
  } : {}
}

resource "google_storage_bucket_iam_member" "audit_vault" {
  for_each = local.audit_vault_bucket_grants

  bucket = google_storage_bucket.audit_vault[0].name
  role   = each.value.role
  member = each.value.member
}
