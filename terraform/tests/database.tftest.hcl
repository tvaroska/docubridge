# Credential-free plan-time test of the database module (Cloud SQL PG16 on private
# IP via PSA). mock_provider means the google provider is never authenticated;
# command = plan means nothing is created. Asserts on input-derived / statically
# known values, not opaque mocked ids (P7-1 plan §2b).

mock_provider "google" {}
mock_provider "google-beta" {}

run "postgres16_private_ip_psa" {
  command = plan

  module {
    source = "./modules/database"
  }

  variables {
    project_id  = "test-project"
    region      = "us-central1"
    vpc_network = "projects/test-project/global/networks/bridge-vpc"
    db_name     = "bridge"
    db_user     = "bridge"
    # Tests/dev disable deletion protection so a plan/apply can be torn down.
    deletion_protection = false
  }

  # PostgreSQL 16, the design-named engine.
  assert {
    condition     = google_sql_database_instance.bridge.database_version == "POSTGRES_16"
    error_message = "Cloud SQL instance must be POSTGRES_16"
  }

  # PRIVATE IP ONLY — no public IP surface (the trust-boundary invariant).
  assert {
    condition     = google_sql_database_instance.bridge.settings[0].ip_configuration[0].ipv4_enabled == false
    error_message = "Cloud SQL must have ipv4_enabled = false (no public IP)"
  }

  # The private-IP path attaches to the passed VPC (the PSA peering target).
  assert {
    condition     = google_sql_database_instance.bridge.settings[0].ip_configuration[0].private_network == var.vpc_network
    error_message = "Cloud SQL private_network must be the passed VPC"
  }

  # PSA reserved range is an INTERNAL VPC_PEERING block on the same VPC.
  assert {
    condition     = google_compute_global_address.psa_range.purpose == "VPC_PEERING"
    error_message = "PSA reserved range purpose must be VPC_PEERING"
  }

  assert {
    condition     = google_compute_global_address.psa_range.address_type == "INTERNAL"
    error_message = "PSA reserved range must be INTERNAL"
  }

  assert {
    condition     = google_compute_global_address.psa_range.network == var.vpc_network
    error_message = "PSA reserved range must be on the passed VPC"
  }

  # The service-networking peering rides the reserved range.
  assert {
    condition     = google_service_networking_connection.psa.service == "servicenetworking.googleapis.com"
    error_message = "PSA connection must peer over servicenetworking.googleapis.com"
  }

  # The application database/user track the input names.
  assert {
    condition     = google_sql_database.bridge.name == var.db_name
    error_message = "database name must track db_name"
  }

  assert {
    condition     = google_sql_user.bridge.name == var.db_user
    error_message = "SQL user name must track db_user"
  }
}
