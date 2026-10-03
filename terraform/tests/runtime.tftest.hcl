# Credential-free plan-time test of the runtime module (Cloud Run v2 skeleton).
# Proves the computed-ingress rule BOTH ways and that the service runs as the
# bridge-runtime SA with Direct VPC egress into the internal subnet.

mock_provider "google" {}
mock_provider "google-beta" {}

run "ingress_when_gateway_enabled" {
  command = plan

  module {
    source = "./modules/runtime"
  }

  variables {
    project_id            = "test-project"
    region                = "us-central1"
    container_image       = "us-docker.pkg.dev/cloudrun/container/hello"
    enable_gateway        = true
    service_account_email = "bridge-runtime@test-project.iam.gserviceaccount.com"
    vpc_network           = "projects/test-project/global/networks/bridge-vpc"
    egress_subnet         = "projects/test-project/regions/us-central1/subnetworks/bridge-vpc-internal"
  }

  assert {
    condition     = output.computed_ingress == "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"
    error_message = "enable_gateway = true must restrict ingress to the internal load balancer"
  }

  assert {
    condition     = google_cloud_run_v2_service.bridge.template[0].service_account == "bridge-runtime@test-project.iam.gserviceaccount.com"
    error_message = "service must run as the bridge-runtime SA"
  }

  # Datastore traffic stays on the private VPC path — never an HTTP hop.
  assert {
    condition     = google_cloud_run_v2_service.bridge.template[0].vpc_access[0].egress == "PRIVATE_RANGES_ONLY"
    error_message = "runtime must use Direct VPC egress (PRIVATE_RANGES_ONLY) for the private-IP datastore path"
  }

  # Gateway on, no public_url override → no BRIDGE_PUBLIC_URL env (the gateway/domain
  # URL must be supplied explicitly; do NOT fall back to the run.app URL).
  assert {
    condition = length([
      for e in google_cloud_run_v2_service.bridge.template[0].containers[0].env
      : e if e.name == "BRIDGE_PUBLIC_URL"
    ]) == 0
    error_message = "gateway-fronted service must not advertise the run.app URL without an explicit public_url override"
  }
}

run "ingress_when_gateway_disabled" {
  command = plan

  module {
    source = "./modules/runtime"
  }

  variables {
    project_id            = "test-project"
    region                = "us-central1"
    container_image       = "us-docker.pkg.dev/cloudrun/container/hello"
    enable_gateway        = false
    service_account_email = "bridge-runtime@test-project.iam.gserviceaccount.com"
    vpc_network           = "projects/test-project/global/networks/bridge-vpc"
    egress_subnet         = "projects/test-project/regions/us-central1/subnetworks/bridge-vpc-internal"
  }

  assert {
    condition     = output.computed_ingress == "INGRESS_TRAFFIC_ALL"
    error_message = "enable_gateway = false (local/dev default) must accept all ingress"
  }

  # The seam-mode/extraction-engine envs default "" → no env at plan time (the
  # P7-1/local default; keeps the container env untouched).
  assert {
    condition = length([
      for e in google_cloud_run_v2_service.bridge.template[0].containers[0].env
      : e if e.name == "BRIDGE_SEAM_MODE" || e.name == "BRIDGE_EXTRACTION_ENGINE" || e.name == "BRIDGE_DOCAI_PROCESSOR"
    ]) == 0
    error_message = "no seam-mode/extraction-engine/docai-processor env at the default (empty) plan"
  }

  # Gateway-less: the service advertises its OWN deterministic run.app URL as
  # BRIDGE_PUBLIC_URL, computed at plan time (no var injection, no second apply).
  assert {
    condition = length([
      for e in google_cloud_run_v2_service.bridge.template[0].containers[0].env
      : e if e.name == "BRIDGE_PUBLIC_URL"
    ]) == 1
    error_message = "gateway-less runtime must advertise its own deterministic run.app URL"
  }
}

# The P7-9 deploy shape: seam_mode = "gcp" + extraction_engine = "gemini" inject the
# seam master switch and the deployed extraction engine as PLAIN envs on the container.
run "seam_envs_when_deployed" {
  command = plan

  module {
    source = "./modules/runtime"
  }

  variables {
    project_id            = "test-project"
    region                = "us-central1"
    container_image       = "us-docker.pkg.dev/cloudrun/container/hello"
    enable_gateway        = true
    service_account_email = "bridge-runtime@test-project.iam.gserviceaccount.com"
    vpc_network           = "projects/test-project/global/networks/bridge-vpc"
    egress_subnet         = "projects/test-project/regions/us-central1/subnetworks/bridge-vpc-internal"
    seam_mode             = "gcp"
    extraction_engine     = "gemini"
  }

  assert {
    condition = length([
      for e in google_cloud_run_v2_service.bridge.template[0].containers[0].env
      : e if e.name == "BRIDGE_SEAM_MODE" && e.value == "gcp"
    ]) == 1
    error_message = "seam_mode = gcp must inject BRIDGE_SEAM_MODE=gcp (the seam master switch)"
  }

  assert {
    condition = length([
      for e in google_cloud_run_v2_service.bridge.template[0].containers[0].env
      : e if e.name == "BRIDGE_EXTRACTION_ENGINE" && e.value == "gemini"
    ]) == 1
    error_message = "extraction_engine = gemini must inject BRIDGE_EXTRACTION_ENGINE=gemini"
  }
}

# The docai engine: extraction_engine = "docai" + the client's processor by resource
# name → both PLAIN envs on the container (a resource name is not a secret).
run "docai_envs_when_deployed" {
  command = plan

  module {
    source = "./modules/runtime"
  }

  variables {
    project_id            = "test-project"
    region                = "us-central1"
    container_image       = "us-docker.pkg.dev/cloudrun/container/hello"
    enable_gateway        = true
    service_account_email = "bridge-runtime@test-project.iam.gserviceaccount.com"
    vpc_network           = "projects/test-project/global/networks/bridge-vpc"
    egress_subnet         = "projects/test-project/regions/us-central1/subnetworks/bridge-vpc-internal"
    seam_mode             = "gcp"
    extraction_engine     = "docai"
    docai_processor       = "projects/client-project/locations/eu/processors/abc123"
  }

  assert {
    condition = length([
      for e in google_cloud_run_v2_service.bridge.template[0].containers[0].env
      : e if e.name == "BRIDGE_EXTRACTION_ENGINE" && e.value == "docai"
    ]) == 1
    error_message = "extraction_engine = docai must inject BRIDGE_EXTRACTION_ENGINE=docai"
  }

  assert {
    condition = length([
      for e in google_cloud_run_v2_service.bridge.template[0].containers[0].env
      : e if e.name == "BRIDGE_DOCAI_PROCESSOR" && e.value == "projects/client-project/locations/eu/processors/abc123"
    ]) == 1
    error_message = "docai_processor must inject BRIDGE_DOCAI_PROCESSOR as a plain env"
  }
}
