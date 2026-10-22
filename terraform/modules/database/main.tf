# database — Cloud SQL PostgreSQL 16 on PRIVATE IP only (P7-2).
#
# The shared relational store behind the sessions / task_store / exchange_store
# GCP seam adapters. It MUST be a shared store, never per-replica in-memory — a
# peer reference measured 86.8% poll-404s with a per-replica store on multi-replica
# Agent Runtime (design/seams.md, "the session-affinity trap"; DECISIONS.md
# 2026-08-24). The Bridge→Cloud SQL path is PRIVATE IP over the VPC via Private
# Services Access — ipv4_enabled = false, no public IP, never an HTTP hop.
#
# This is a small delta on the P7-1 substrate module set (design/gcp-substrate.md):
# the PSA reserved range + service-networking peering are Cloud-SQL-specific, so
# they live HERE (consuming module.network.network_id), keeping the generic
# `network` module untouched.

# Private Services Access reserved range — the INTERNAL VPC_PEERING address block
# Google's service-networking uses to peer the managed Cloud SQL producer network
# into our VPC. Cloud SQL's private IP is allocated from here.
resource "google_compute_global_address" "psa_range" {
  project       = var.project_id
  name          = "${var.instance_name}-psa"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = var.psa_prefix_length
  network       = var.vpc_network
}

# The service-networking connection that peers Google's managed producer network
# over the reserved range above — the plumbing that makes private-IP Cloud SQL
# reachable from the VPC.
resource "google_service_networking_connection" "psa" {
  network                 = var.vpc_network
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.psa_range.name]
}

resource "google_sql_database_instance" "bridge" {
  project             = var.project_id
  name                = var.instance_name
  region              = var.region
  database_version    = "POSTGRES_16"
  deletion_protection = var.deletion_protection

  # The instance can only be created once the PSA peering exists.
  depends_on = [google_service_networking_connection.psa]

  settings {
    tier = var.db_tier
    # Pin the edition: db-custom-* tiers are ENTERPRISE-only. Without this the API
    # now defaults new instances to ENTERPRISE_PLUS, which rejects db-custom tiers
    # (requires db-perf-optimized-N-*). Keep ENTERPRISE so the cheap shared-core
    # demo tier is valid.
    edition           = var.db_edition
    user_labels       = var.labels
    availability_type = "ZONAL"

    # PRIVATE IP ONLY — the trust-boundary invariant (CRITICAL.md, DECISIONS.md
    # 2026-08-24). No public IP surface; the instance is reachable only over the
    # VPC private-IP path the runtime's Direct VPC egress rides.
    ip_configuration {
      ipv4_enabled    = false
      private_network = var.vpc_network
    }

    # Enable Cloud SQL IAM database authentication alongside the Secret Manager DSN
    # path so service accounts can authenticate via short-lived OAuth2 tokens.
    database_flags {
      name  = "cloudsql.iam_authentication"
      value = "on"
    }
  }
}

resource "google_sql_database" "bridge" {
  project  = var.project_id
  name     = var.db_name
  instance = google_sql_database_instance.bridge.name
}

resource "google_sql_user" "bridge" {
  project  = var.project_id
  name     = var.db_user
  instance = google_sql_database_instance.bridge.name

  # Password is supplied at deploy (P7-9), never committed (see variables.tf).
  # Prefer IAM database authentication (no password at all). The composed DSN
  # rides the Secret Manager secret env (the secrets module), not plaintext HCL.
  password = var.db_password
}

resource "google_sql_user" "iam_runtime" {
  count = var.iam_service_account_email != "" ? 1 : 0

  project  = var.project_id
  name     = trimsuffix(var.iam_service_account_email, ".gserviceaccount.com")
  instance = google_sql_database_instance.bridge.name
  type     = "CLOUD_IAM_SERVICE_ACCOUNT"
}

