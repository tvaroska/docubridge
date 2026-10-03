# Root module provider + Terraform version pins.
#
# Terraform >= 1.7 is required for the `mock_provider` test harness (GA since 1.7)
# that lets `terraform test` plan the substrate credential-free (the P7-1 exit
# criterion). The google/google-beta providers are pinned to major 6 (the pin the
# GCP seam adapters are written against; matched across every child module).
terraform {
  required_version = ">= 1.7"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 6.0, < 7.0"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = ">= 6.0, < 7.0"
    }
  }
}
