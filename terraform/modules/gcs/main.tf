# gcs — the Skill Registry bucket + the Artifacts bucket (P7-3).
#
# Two GCS buckets behind the skill_registry port (GcpSkillRegistry) and the native
# GcsArtifactService backend swap (bridge/edge/artifacts.py). Both are NEVER public:
# uniform bucket-level access + public_access_prevention = ENFORCED. Signed V4 URLs
# (time-limited, minted only after the leg-scoping check) are the only external byte
# path — a load-bearing security invariant, not a style choice (design/gcp-substrate.md).
#
# CRITICAL — No bucket OBJECTS / secrets / plaintext values in HCL (CRITICAL.md).
# Skill folders and artifact bytes are RUNTIME data written through the port /
# ArtifactService, never Terraform-managed. This module provisions the containers +
# their IAM only.

locals {
  shared_bucket_target = var.shared_bucket_name != "" ? var.shared_bucket_name : var.project_id
  shared_bucket_name = var.single_bucket_mode ? (
    var.create_shared_bucket ? google_storage_bucket.shared[0].name : data.google_storage_bucket.shared[0].name
  ) : ""

  skills_bucket_name      = var.single_bucket_mode ? local.shared_bucket_name : google_storage_bucket.skills[0].name
  artifacts_bucket_name   = var.single_bucket_mode ? local.shared_bucket_name : google_storage_bucket.artifacts[0].name
  scenarios_bucket_name   = var.single_bucket_mode ? local.shared_bucket_name : google_storage_bucket.scenarios[0].name
  email_bucket_name       = var.enable_email_ingress ? (var.single_bucket_mode ? local.shared_bucket_name : google_storage_bucket.email[0].name) : ""
  audit_vault_bucket_name = var.enable_audit_vault ? (var.single_bucket_mode ? local.shared_bucket_name : google_storage_bucket.audit_vault[0].name) : ""
}

# Shared single-bucket substrate (single_bucket_mode = true): one private bucket
# gs://<project_id> with per-object GCS Object Retention (enable_object_retention),
# prefix-scoped lifecycle Delete rules (NEVER touching audit-vault/, skills/, or
# scenarios/), and CEL IAM prefix conditions on every binding.
resource "google_storage_bucket" "shared" {
  count = var.single_bucket_mode && var.create_shared_bucket ? 1 : 0

  project                     = var.project_id
  name                        = local.shared_bucket_target
  location                    = var.location
  labels                      = var.labels
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  enable_object_retention     = true
  force_destroy               = var.force_destroy_shared_bucket

  versioning {
    enabled = true
  }

  lifecycle_rule {
    action {
      type = "Delete"
    }
    condition {
      age            = var.retention_days
      matches_prefix = ["document_bridge/"]
    }
  }

  lifecycle_rule {
    action {
      type = "Delete"
    }
    condition {
      age            = var.retention_days
      with_state     = "ARCHIVED"
      matches_prefix = ["document_bridge/"]
    }
  }

  lifecycle_rule {
    action {
      type = "Delete"
    }
    condition {
      age            = var.payload_retention_days
      matches_prefix = ["genai-payloads/"]
    }
  }

  lifecycle_rule {
    action {
      type = "Delete"
    }
    condition {
      age            = var.email_retention_days
      matches_prefix = ["email/"]
    }
  }
}

data "google_storage_bucket" "shared" {
  count = var.single_bucket_mode && !var.create_shared_bucket ? 1 : 0

  name = local.shared_bucket_target
}

# Skill Registry bucket (legacy multi-bucket mode) — a re-authored skill is a new object version.
resource "google_storage_bucket" "skills" {
  count = var.single_bucket_mode ? 0 : 1

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

# Artifacts bucket (legacy multi-bucket mode) — ART-1 versioning + ART-3 lifecycle Delete.
resource "google_storage_bucket" "artifacts" {
  count = var.single_bucket_mode ? 0 : 1

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

# Minimal bucket-level list-only custom role (storage.objects.list; zero read, write,
# or delete) for single-bucket mode. In GCP Cloud Storage, storage.objects.list
# evaluates against resource.type == "storage.googleapis.com/Bucket" rather than
# individual object names (and objectListPrefix CAB attributes are invalid in bucket
# IAM allow-policies), so principals that must list prefixes receive this list-only
# role while all object read/write/create/delete/retention roles stay strictly
# CEL-conditioned on objects/<prefix>/. Never granted to the public email_webhook SA.
resource "google_project_iam_custom_role" "shared_bucket_object_lister" {
  count = var.single_bucket_mode ? 1 : 0

  project     = var.project_id
  role_id     = "docubridge_gcs_object_lister"
  title       = "DocuBridge Shared Bucket Object Lister"
  description = "Minimal list-only permission (storage.objects.list; zero read/write/delete) paired with CEL prefix-conditioned object bindings in single-bucket mode."
  permissions = ["storage.objects.list"]
}

locals {
  shared_bucket_lister_members = var.single_bucket_mode ? merge(
    { runtime = var.service_account_member },
    var.agent_registry_service_agent_member != "" ? { agent_registry = var.agent_registry_service_agent_member } : {},
  ) : {}
}

resource "google_storage_bucket_iam_member" "shared_bucket_lister" {
  for_each = local.shared_bucket_lister_members

  bucket = local.shared_bucket_name
  role   = google_project_iam_custom_role.shared_bucket_object_lister[0].name
  member = each.value
}

# IAM: objectAdmin scoped per-bucket (or per-prefix via CEL in single-bucket mode)
# to the bridge-runtime SA — never a project-level storage grant.
resource "google_storage_bucket_iam_member" "skills_object_admin" {
  bucket = local.skills_bucket_name
  role   = "roles/storage.objectAdmin"
  member = var.service_account_member

  dynamic "condition" {
    for_each = var.single_bucket_mode ? [1] : []
    content {
      title       = "skills_prefix_admin"
      description = "Scope skill registry objectAdmin to the skills/ prefix."
      expression  = "resource.name.startsWith(\"projects/_/buckets/${local.shared_bucket_name}/objects/skills/\")"
    }
  }
}

# Gemini Platform Agent Registry Service Agent read access on skills.
resource "google_storage_bucket_iam_member" "skills_agent_registry_viewer" {
  count = var.agent_registry_service_agent_member != "" ? 1 : 0

  bucket = local.skills_bucket_name
  role   = "roles/storage.objectViewer"
  member = var.agent_registry_service_agent_member

  dynamic "condition" {
    for_each = var.single_bucket_mode ? [1] : []
    content {
      title       = "skills_prefix_agent_registry_viewer"
      description = "Scope Agent Registry objectViewer to the skills/ prefix."
      expression  = "resource.name.startsWith(\"projects/_/buckets/${local.shared_bucket_name}/objects/skills/\")"
    }
  }
}

resource "google_storage_bucket_iam_member" "artifacts_object_admin" {
  bucket = local.artifacts_bucket_name
  role   = "roles/storage.objectAdmin"
  member = var.service_account_member

  dynamic "condition" {
    for_each = var.single_bucket_mode ? [1] : []
    content {
      title       = "artifacts_prefix_admin"
      description = "Scope ADK GcsArtifactService objectAdmin to the document_bridge/ prefix."
      expression  = "resource.name.startsWith(\"projects/_/buckets/${local.shared_bucket_name}/objects/document_bridge/\")"
    }
  }
}

# Scenarios bucket (legacy multi-bucket mode) — holds `<scenario_id>/scenario.yaml` + `docs/*.pdf`.
resource "google_storage_bucket" "scenarios" {
  count = var.single_bucket_mode ? 0 : 1

  project       = var.project_id
  name          = "${var.name_prefix}-scenarios"
  location      = var.location
  labels        = var.labels
  force_destroy = true

  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  versioning {
    enabled = true
  }
}

resource "google_storage_bucket_iam_member" "scenarios_object_admin" {
  bucket = local.scenarios_bucket_name
  role   = "roles/storage.objectAdmin"
  member = var.service_account_member

  dynamic "condition" {
    for_each = var.single_bucket_mode ? [1] : []
    content {
      title       = "scenarios_prefix_admin"
      description = "Scope scenarios objectAdmin to the scenarios/ prefix."
      expression  = "resource.name.startsWith(\"projects/_/buckets/${local.shared_bucket_name}/objects/scenarios/\")"
    }
  }
}

# Email landing bucket (legacy multi-bucket mode) — create-only landing storage.
resource "google_storage_bucket" "email" {
  count = !var.single_bucket_mode && var.enable_email_ingress ? 1 : 0

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

# Create-only grants: webhook SA gets objectCreator ONLY (never objectViewer,
# never shared_bucket_lister); runtime SA gets objectViewer + objectCreator
# (never objectAdmin on landed mail).
locals {
  email_bucket_grants = var.enable_email_ingress ? {
    webhook_creator = { role = "roles/storage.objectCreator", member = var.email_webhook_member }
    runtime_viewer  = { role = "roles/storage.objectViewer", member = var.service_account_member }
    runtime_creator = { role = "roles/storage.objectCreator", member = var.service_account_member }
  } : {}
}

resource "google_storage_bucket_iam_member" "email" {
  for_each = local.email_bucket_grants

  bucket = local.email_bucket_name
  role   = each.value.role
  member = each.value.member

  dynamic "condition" {
    for_each = var.single_bucket_mode ? [1] : []
    content {
      title       = "email_prefix_${each.key}"
      description = "Scope email ingress ${each.key} grant to the email/ prefix."
      expression  = "resource.name.startsWith(\"projects/_/buckets/${local.shared_bucket_name}/objects/email/\")"
    }
  }
}

# Cloud Storage Audit Vault bucket (legacy multi-bucket mode — Zone 3 WORM archive).
# In single_bucket_mode, WORM immutability uses per-object GCS Object Retention on
# the shared bucket (enable_object_retention = true) under audit-vault/.
resource "google_storage_bucket" "audit_vault" {
  count = !var.single_bucket_mode && var.enable_audit_vault ? 1 : 0

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

  bucket = local.audit_vault_bucket_name
  role   = each.value.role
  member = each.value.member

  dynamic "condition" {
    for_each = var.single_bucket_mode ? [1] : []
    content {
      title       = "audit_vault_prefix_${each.key}"
      description = "Scope WORM audit vault ${each.key} grant to the audit-vault/ prefix."
      expression  = "resource.name.startsWith(\"projects/_/buckets/${local.shared_bucket_name}/objects/audit-vault/\")"
    }
  }
}

# Least-privilege per-object WORM retention setter for single-bucket mode.
# GCS requires storage.objects.setRetention on upload when blob.retention is set,
# which roles/storage.objectCreator omits. Scoped strictly to audit-vault/ with
# zero delete/update/admin permissions.
resource "google_project_iam_custom_role" "audit_vault_retention_setter" {
  count = var.single_bucket_mode && var.enable_audit_vault ? 1 : 0

  project     = var.project_id
  role_id     = "docubridge_audit_vault_retention"
  title       = "DocuBridge Audit Vault Retention Setter"
  description = "Least-privilege per-object WORM retention setter (storage.objects.setRetention only; zero delete/update/admin) scoped to audit-vault/."
  permissions = ["storage.objects.setRetention"]
}

resource "google_storage_bucket_iam_member" "audit_vault_retention" {
  count = var.single_bucket_mode && var.enable_audit_vault ? 1 : 0

  bucket = local.audit_vault_bucket_name
  role   = google_project_iam_custom_role.audit_vault_retention_setter[0].name
  member = var.service_account_member

  condition {
    title       = "audit_vault_prefix_set_retention"
    description = "Scope per-object WORM retention setting strictly to the audit-vault/ prefix."
    expression  = "resource.name.startsWith(\"projects/_/buckets/${local.shared_bucket_name}/objects/audit-vault/\")"
  }
}
