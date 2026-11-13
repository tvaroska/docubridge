# telemetry — Native Cloud Observability (_Default log analytics + obs_logs linked dataset),
# GenAI payload GCS bucket + BigQuery BigLake external table (completions),
# App Hub applications (kyc-address-verification, benefits-rfp-procurement,
# meridian-loan-consolidation, external-counterparty-agents), telemetry IAM grants,
# and Zone 4 Lakehouse Analytics (docubridge_lakehouse).
#
# Note: The _Trace observability bucket and obs_traces linked dataset are provisioned
# idempotently via `gcloud observability` in scripts/deploy.sh (google_observability_*
# resources are not in the pinned hashicorp/google 6.50.0 provider schema).

locals {
  observability_apis = toset([
    "observability.googleapis.com",
    "apphub.googleapis.com",
    "telemetry.googleapis.com",
    "bigqueryconnection.googleapis.com",
  ])

  telemetry_iam_roles = toset([
    "roles/telemetry.tracesWriter",
    "roles/logging.logWriter",
    "roles/serviceusage.serviceUsageConsumer",
  ])

  apphub_applications = {
    "kyc-address-verification" = {
      display_name = "KYC Address Verification"
      description  = "Path-A and Path-B retail banking KYC address verification workload fleet."
    }
    "benefits-rfp-procurement" = {
      display_name = "Employee Benefits RFP Procurement"
      description  = "Multi-carrier employee benefits RFP intake, quote extraction, and board comparison."
    }
    "meridian-loan-consolidation" = {
      display_name = "Meridian Commercial Loan Consolidation"
      description  = "Collaborative commercial real estate loan consolidation, conflict escalation, and A2UI review."
    }
    "external-counterparty-agents" = {
      display_name = "External Counterparty Agents"
      description  = "External-tagged counterparty A2A agents (excluded from internal FinOps model spend)."
    }
  }
}

resource "google_project_service" "observability_apis" {
  for_each = var.enable_telemetry_bq ? local.observability_apis : toset([])

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

resource "google_project_iam_member" "telemetry_roles" {
  for_each = var.enable_telemetry_bq ? local.telemetry_iam_roles : toset([])

  project = var.project_id
  role    = each.value
  member  = var.service_account_member
}

resource "google_logging_project_bucket_config" "default_analytics" {
  count = var.enable_telemetry_bq ? 1 : 0

  project          = var.project_id
  location         = "global"
  bucket_id        = "_Default"
  enable_analytics = true
  retention_days   = 30
}

resource "google_logging_linked_dataset" "obs_logs" {
  count = var.enable_telemetry_bq ? 1 : 0

  parent      = "projects/${var.project_id}"
  location    = "global"
  bucket      = google_logging_project_bucket_config.default_analytics[0].bucket_id
  link_id     = "obs_logs"
  description = "Cloud Logging _Default analytics linked dataset (_AllLogs) for GenAI completion events."
}

resource "google_storage_bucket" "genai_payloads" {
  count = var.enable_telemetry_bq ? 1 : 0

  project                     = var.project_id
  name                        = "${var.project_id}-genai-payloads"
  location                    = "US"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = !var.deletion_protection
  labels                      = var.labels

  lifecycle_rule {
    action {
      type = "Delete"
    }
    condition {
      age = var.payload_retention_days
    }
  }
}

resource "google_storage_bucket_iam_member" "genai_payloads_creator" {
  count = var.enable_telemetry_bq ? 1 : 0

  bucket = google_storage_bucket.genai_payloads[0].name
  role   = "roles/storage.objectCreator"
  member = var.service_account_member
}

resource "google_bigquery_connection" "vertex_ai" {
  count = var.enable_telemetry_bq ? 1 : 0

  project       = var.project_id
  location      = "US"
  connection_id = "vertex_ai_connection"
  friendly_name = "DocuBridge Vertex AI & BigLake Connection"
  description   = "Cloud resource connection for BigLake external tables and AI.GENERATE_TABLE."

  cloud_resource {}
}

resource "google_storage_bucket_iam_member" "genai_payloads_biglake_viewer" {
  count = var.enable_telemetry_bq ? 1 : 0

  bucket = google_storage_bucket.genai_payloads[0].name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_bigquery_connection.vertex_ai[0].cloud_resource[0].service_account_id}"
}

resource "google_project_iam_member" "vertex_connection_aiplatform" {
  count = var.enable_telemetry_bq ? 1 : 0

  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_bigquery_connection.vertex_ai[0].cloud_resource[0].service_account_id}"
}

resource "google_bigquery_dataset" "agent_eval" {
  count = var.enable_telemetry_bq ? 1 : 0

  project                    = var.project_id
  dataset_id                 = "agent_eval"
  friendly_name              = "BigQuery Agent Evaluation & Observability Analytics"
  description                = "US multi-region dataset for _AllSpans/_AllLogs compatibility views, BigLake completions, and EVAL-P11 outcome attribution."
  location                   = "US"
  delete_contents_on_destroy = !var.deletion_protection
  labels                     = var.labels
}

resource "google_apphub_application" "apps" {
  for_each = var.enable_telemetry_bq ? local.apphub_applications : {}

  project        = var.project_id
  location       = var.region
  application_id = each.key
  display_name   = each.value.display_name
  description    = each.value.description

  scope {
    type = "REGIONAL"
  }
}

# Zone 4 Lakehouse Analytics — BigQuery dataset + bq_operational_facts_iceberg table
# matching bridge/operational_store/iceberg_sync.py::BIGQUERY_ICEBERG_DDL, plus the
# BigLake external table `completions` over gs://<project_id>-genai-payloads/*.jsonl.
resource "google_bigquery_dataset" "lakehouse" {
  count = var.enable_telemetry_bq ? 1 : 0

  project                    = var.project_id
  dataset_id                 = var.lakehouse_dataset_id
  friendly_name              = "DocuBridge Operational Facts Lakehouse"
  description                = "Zone 4 bi-temporal consolidated operational facts synced from AlloyDB/Cloud SQL to BigQuery Open Iceberg."
  location                   = var.region
  delete_contents_on_destroy = !var.deletion_protection
  labels                     = var.labels
}

resource "google_bigquery_dataset_iam_member" "lakehouse_runtime_editor" {
  count = var.enable_telemetry_bq ? 1 : 0

  project    = var.project_id
  dataset_id = google_bigquery_dataset.lakehouse[0].dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = var.service_account_member
}

resource "google_bigquery_table" "completions" {
  count = var.enable_telemetry_bq ? 1 : 0

  project             = var.project_id
  dataset_id          = google_bigquery_dataset.agent_eval[0].dataset_id
  table_id            = "completions"
  description         = "BigLake external table over GenAI completion hook JSONL payloads in Cloud Storage."
  deletion_protection = false
  labels              = var.labels

  schema = jsonencode([
    { name = "payload", type = "JSON", mode = "NULLABLE", description = "Uploaded OTel GenAI prompt/context/completion JSON payload." },
    { name = "user_prompt", type = "STRING", mode = "NULLABLE", description = "Optional top-level user prompt text." },
    { name = "retrieved_context", type = "STRING", mode = "NULLABLE", description = "Optional top-level retrieved context." },
    { name = "final_response", type = "STRING", mode = "NULLABLE", description = "Optional top-level final response." }
  ])

  external_data_configuration {
    autodetect            = false
    ignore_unknown_values = true
    source_format         = "NEWLINE_DELIMITED_JSON"
    source_uris           = ["gs://${google_storage_bucket.genai_payloads[0].name}/*.jsonl"]
    connection_id         = google_bigquery_connection.vertex_ai[0].name
  }

  depends_on = [google_storage_bucket_iam_member.genai_payloads_biglake_viewer]
}

resource "google_bigquery_table" "operational_facts_iceberg" {
  count = var.enable_telemetry_bq ? 1 : 0

  project             = var.project_id
  dataset_id          = google_bigquery_dataset.lakehouse[0].dataset_id
  table_id            = "bq_operational_facts_iceberg"
  description         = "Bi-temporal consolidated operational facts table with SourceAnchor and SkillRevision lineage."
  deletion_protection = var.deletion_protection
  labels              = var.labels

  time_partitioning {
    type  = "DAY"
    field = "recorded_at"
  }

  clustering = ["entity_id", "doctype", "field_path"]

  schema = jsonencode([
    { name = "fact_id", type = "STRING", mode = "REQUIRED", description = "Unique consolidated fact identifier" },
    { name = "entity_id", type = "STRING", mode = "REQUIRED", description = "Entity / Loan / Submission identifier" },
    { name = "context_id", type = "STRING", mode = "REQUIRED", description = "A2A context identifier" },
    { name = "doc_id", type = "STRING", mode = "REQUIRED", description = "Source document identifier" },
    { name = "doctype", type = "STRING", mode = "REQUIRED", description = "Document type / skill identifier" },
    { name = "issuer", type = "STRING", mode = "NULLABLE", description = "Normalized issuer / carrier / lender" },
    { name = "field_path", type = "STRING", mode = "REQUIRED", description = "Consolidated field path" },
    { name = "field_value", type = "STRING", mode = "REQUIRED", description = "Consolidated field string value" },
    { name = "numeric_value", type = "NUMERIC", mode = "NULLABLE", description = "Exact Decimal numeric value" },
    { name = "currency", type = "STRING", mode = "NULLABLE", description = "ISO 4217 currency code" },
    { name = "source_priority", type = "INT64", mode = "REQUIRED", description = "Source document hierarchy priority" },
    { name = "recorded_at", type = "TIMESTAMP", mode = "REQUIRED", description = "Transaction-time bi-temporal timestamp (when learned)" },
    { name = "effective_from", type = "DATE", mode = "REQUIRED", description = "Valid-time start date" },
    { name = "effective_to", type = "DATE", mode = "NULLABLE", description = "Valid-time end date" },
    { name = "version", type = "INT64", mode = "REQUIRED", description = "Append-only fact version" },
    { name = "supersedes_fact_id", type = "STRING", mode = "NULLABLE", description = "Prior fact ID superseded by this fact" },
    { name = "vault_uri", type = "STRING", mode = "NULLABLE", description = "Zone 3 WORM Audit Vault URI" },
    { name = "page", type = "INT64", mode = "NULLABLE", description = "1-indexed source PDF page number" },
    { name = "bbox_json", type = "STRING", mode = "NULLABLE", description = "Normalized polygon vertices JSON" },
    { name = "skill_version", type = "STRING", mode = "NULLABLE", description = "Skill semantic version" },
    { name = "skill_revision_id", type = "STRING", mode = "NULLABLE", description = "Immutable content-addressed skill revision ID" },
    { name = "skill_urn", type = "STRING", mode = "NULLABLE", description = "Agent Registry skill URN" },
    { name = "process_skill_revision_id", type = "STRING", mode = "NULLABLE", description = "Process-instance skill revision ID" },
    { name = "ontology_revision_id", type = "STRING", mode = "NULLABLE", description = "TBox OntologyRevision ID pinned during extraction and consolidation" },
    { name = "consolidation_edge_id", type = "STRING", mode = "NULLABLE", description = "Typed OntologyEdge ID that governed cross-document precedence" }
  ])
}

resource "google_bigquery_table" "ontology_revisions" {
  count = var.enable_telemetry_bq ? 1 : 0

  project             = var.project_id
  dataset_id          = google_bigquery_dataset.lakehouse[0].dataset_id
  table_id            = "bq_ontology_revisions"
  description         = "TBox ontology revision history synced from Port.knowledge_catalog (catalog_ontology_revisions)."
  deletion_protection = var.deletion_protection
  labels              = var.labels

  schema = jsonencode([
    { name = "revision_id", type = "STRING", mode = "REQUIRED", description = "Immutable SHA-256 content-addressed ontology revision ID" },
    { name = "version", type = "STRING", mode = "REQUIRED", description = "Semantic ontology version" },
    { name = "status", type = "STRING", mode = "REQUIRED", description = "Lifecycle status (CANDIDATE, ACTIVE, SUPERSEDED)" },
    { name = "created_at", type = "TIMESTAMP", mode = "REQUIRED", description = "Revision creation timestamp (UTC)" },
    { name = "promoted_at", type = "TIMESTAMP", mode = "NULLABLE", description = "Revision promotion timestamp (UTC)" },
    { name = "parent_revision_id", type = "STRING", mode = "NULLABLE", description = "Parent ontology revision ID" }
  ])
}

resource "google_bigquery_table" "ontology_edges" {
  count = var.enable_telemetry_bq ? 1 : 0

  project             = var.project_id
  dataset_id          = google_bigquery_dataset.lakehouse[0].dataset_id
  table_id            = "bq_ontology_edges"
  description         = "TBox typed ontology edges ([:SUPERSEDES], [:REQUIRES_CORROBORATION], [:GOVERNS_FIELD]) synced from Port.knowledge_catalog."
  deletion_protection = var.deletion_protection
  labels              = var.labels

  clustering = ["revision_id", "relation", "source_doctype"]

  schema = jsonencode([
    { name = "revision_id", type = "STRING", mode = "REQUIRED", description = "Owning OntologyRevision ID" },
    { name = "edge_id", type = "STRING", mode = "REQUIRED", description = "Unique ontology edge identifier" },
    { name = "source_doctype", type = "STRING", mode = "REQUIRED", description = "Source doctype or entity node" },
    { name = "relation", type = "STRING", mode = "REQUIRED", description = "Typed edge relation (SUPERSEDES, REQUIRES_CORROBORATION, GOVERNS_FIELD)" },
    { name = "target_node", type = "STRING", mode = "REQUIRED", description = "Target doctype or field_path" },
    { name = "priority_boost", type = "INT64", mode = "REQUIRED", description = "Deterministic priority boost applied during consolidation" },
    { name = "rationale", type = "STRING", mode = "NULLABLE", description = "Contrastive extraction or precedence rationale" }
  ])
}
