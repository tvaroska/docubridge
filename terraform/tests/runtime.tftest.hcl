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

  # The port-mode/extraction-engine envs default "" → no env at plan time (the
  # P7-1/local default; keeps the container env untouched).
  assert {
    condition = length([
      for e in google_cloud_run_v2_service.bridge.template[0].containers[0].env
      : e if e.name == "BRIDGE_PORT_MODE" || e.name == "BRIDGE_EXTRACTION_ENGINE" || e.name == "BRIDGE_DOCAI_PROCESSOR"
    ]) == 0
    error_message = "no port-mode/extraction-engine/docai-processor env at the default (empty) plan"
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

# The P7-9 deploy shape: port_mode = "gcp" + extraction_engine = "gemini" inject the
# port master switch and the deployed extraction engine as PLAIN envs on the container.
run "port_envs_when_deployed" {
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
    port_mode             = "gcp"
    extraction_engine     = "gemini"
  }

  assert {
    condition = length([
      for e in google_cloud_run_v2_service.bridge.template[0].containers[0].env
      : e if e.name == "BRIDGE_PORT_MODE" && e.value == "gcp"
    ]) == 1
    error_message = "port_mode = gcp must inject BRIDGE_PORT_MODE=gcp (the port master switch)"
  }

  assert {
    condition = length([
      for e in google_cloud_run_v2_service.bridge.template[0].containers[0].env
      : e if e.name == "BRIDGE_EXTRACTION_ENGINE" && e.value == "gemini"
    ]) == 1
    error_message = "extraction_engine = gemini must inject BRIDGE_EXTRACTION_ENGINE=gemini"
  }

  assert {
    condition = length([
      for e in google_cloud_run_v2_service.bridge.template[0].containers[0].env
      : e if e.name == "GOOGLE_CLOUD_LOCATION" && e.value == "global"
    ]) == 1
    error_message = "port_mode = gcp must default GOOGLE_CLOUD_LOCATION=global for gemini-3.8-flash / gemini-4-argon"
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
    port_mode             = "gcp"
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

run "agent_engine_a2a_envs_when_deployed" {
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
    port_mode             = "gcp"
    agent_engine_id       = "projects/test-project/locations/us-central1/reasoningEngines/1000000000000000000"
    agent_engine_remote   = "1"
    agent_engine_a2a      = "1"
  }

  assert {
    condition = length([
      for e in google_cloud_run_v2_service.bridge.template[0].containers[0].env
      : e if e.name == "BRIDGE_AGENT_ENGINE_ID" && e.value == "projects/test-project/locations/us-central1/reasoningEngines/1000000000000000000"
    ]) == 1
    error_message = "agent_engine_id must inject BRIDGE_AGENT_ENGINE_ID as a plain env"
  }

  assert {
    condition = length([
      for e in google_cloud_run_v2_service.bridge.template[0].containers[0].env
      : e if e.name == "BRIDGE_AGENT_ENGINE_A2A" && e.value == "1"
    ]) == 1
    error_message = "agent_engine_a2a = 1 must inject BRIDGE_AGENT_ENGINE_A2A=1 as a plain env"
  }

  assert {
    condition     = google_cloud_run_v2_service.bridge.name == "bridge-kyc" && google_cloud_run_v2_service.bridge_meridian.name == "bridge-meridian"
    error_message = "runtime must provision per-app bridge-kyc and bridge-meridian Cloud Run services"
  }

  assert {
    condition = length([
      for e in google_cloud_run_v2_service.bridge.template[0].containers[0].env
      : e if e.name == "BRIDGE_APPHUB_APPLICATION_ID" && e.value == "kyc-address-verification"
    ]) == 1 && length([
      for e in google_cloud_run_v2_service.bridge_meridian.template[0].containers[0].env
      : e if e.name == "BRIDGE_APPHUB_APPLICATION_ID" && e.value == "meridian-loan-consolidation"
    ]) == 1
    error_message = "each per-app Cloud Run service must carry its App Hub application ID and OTel env vars"
  }
}

run "auth_secrets_and_policy_envs_when_configured" {
  command = plan

  module {
    source = "./modules/runtime"
  }

  variables {
    project_id              = "test-project"
    region                  = "us-central1"
    container_image         = "us-docker.pkg.dev/cloudrun/container/hello"
    service_account_email   = "bridge-runtime@test-project.iam.gserviceaccount.com"
    vpc_network             = "projects/test-project/global/networks/bridge-vpc"
    egress_subnet           = "projects/test-project/regions/us-central1/subnetworks/bridge-vpc-internal"
    enable_rfp_board        = true
    rfp_process             = "benefits-rfp"
    spiffe_svid_secret_id   = "bridge-spiffe-svid-secret"
    user_jwt_secret_id      = "bridge-user-jwt-secret"
    demo_passcode_secret_id = "bridge-demo-passcode"
    iap_audience            = "/projects/100000000000/global/backendServices/1"
    cors_origins            = "https://portal.example.com"
    principal_roles_json    = "{\"ops@example.com\":\"broker\"}"
    gs_allowlist            = "partner-inbound-bucket/quotes"
  }

  assert {
    condition = {
      for e in google_cloud_run_v2_service.rfp_board[0].template[0].containers[0].env :
      e.name => e.value_source[0].secret_key_ref[0].secret
      if contains(["BRIDGE_SPIFFE_SVID_SECRET", "BRIDGE_USER_JWT_SECRET", "BRIDGE_DEMO_PASSCODE"], e.name) && (e.value == null || e.value == "")
      } == {
      BRIDGE_SPIFFE_SVID_SECRET = "bridge-spiffe-svid-secret"
      BRIDGE_USER_JWT_SECRET    = "bridge-user-jwt-secret"
      BRIDGE_DEMO_PASSCODE      = "bridge-demo-passcode"
    }
    error_message = "rfp_board must inject SVID, user JWT, and demo passcode via Secret Manager secret_key_ref"
  }

  assert {
    condition = {
      for e in google_cloud_run_v2_service.bridge_meridian.template[0].containers[0].env :
      e.name => e.value_source[0].secret_key_ref[0].secret
      if contains(["BRIDGE_USER_JWT_SECRET", "BRIDGE_DEMO_PASSCODE"], e.name) && (e.value == null || e.value == "")
      } == {
      BRIDGE_USER_JWT_SECRET = "bridge-user-jwt-secret"
      BRIDGE_DEMO_PASSCODE   = "bridge-demo-passcode"
    }
    error_message = "bridge_meridian must inject BRIDGE_USER_JWT_SECRET and BRIDGE_DEMO_PASSCODE via secret_key_ref"
  }

  assert {
    condition = {
      for e in google_cloud_run_v2_service.bridge.template[0].containers[0].env :
      e.name => e.value
      if contains(["BRIDGE_IAP_AUDIENCE", "BRIDGE_GS_ALLOWLIST"], e.name)
      } == {
      BRIDGE_IAP_AUDIENCE = "/projects/100000000000/global/backendServices/1"
      BRIDGE_GS_ALLOWLIST = "partner-inbound-bucket/quotes"
    }
    error_message = "bridge runtime must wire BRIDGE_IAP_AUDIENCE and BRIDGE_GS_ALLOWLIST when configured"
  }
}
