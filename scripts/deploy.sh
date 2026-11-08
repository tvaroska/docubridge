#!/usr/bin/env bash
# P7-9 deploy — build the Bridge image, provision the GCP substrate, and deploy the
# runtime + RFP board + gateway + frontend in a SINGLE `terraform apply`.
#
# Why a single apply works:
# 1. `module.runtime` computes its deterministic Cloud Run URL (`https://<service>-<project_number>.<region>.run.app`)
#    from `data.google_project.this` at plan time, and `main.tf` derives `public_url` /
#    `gateway_host` from `ssl_domains[0]` when `enable_gateway = true` — eliminating any
#    self-reference cycle.
# 2. `module.secrets` composes the private-IP Cloud SQL DSN from `module.database.private_ip_address`
#    and creates `google_secret_manager_secret_version` resources for `DATABASE_URL` and
#    `BRIDGE_SPIFFE_SVID_SECRET` before `module.runtime` provisions Cloud Run.
# 3. `module.gcs` seeds all `skills/**` files into the Skill Registry bucket during apply.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TF_DIR="${REPO_ROOT}/terraform"

# --------------------------------------------------------------------------- #
# 1. Preflight — required inputs (all non-secret except DB_PASSWORD, from env). #
# --------------------------------------------------------------------------- #
: "${PROJECT_ID:?set PROJECT_ID}"
# Operator-supplied deny-list of shared/Core project ids (comma-separated). Never
# committed; also handed to Terraform as TF_VAR_blocked_project_ids.
: "${BLOCKED_PROJECT_IDS:=}"
BLOCKED_PROJECT_IDS="${BLOCKED_PROJECT_IDS// /}"
IFS=',' read -r -a blocked_ids <<< "${BLOCKED_PROJECT_IDS}"
for blocked in "${blocked_ids[@]}"; do
  if [[ -n "${blocked}" && "${PROJECT_ID}" == "${blocked}" ]]; then
    echo "ERROR: Refusing to deploy demo stack into blocked project '${PROJECT_ID}' (BLOCKED_PROJECT_IDS). Specify a dedicated Demos or Experiments project." >&2
    exit 1
  fi
done
if [[ -n "${BLOCKED_PROJECT_IDS}" ]]; then
  export TF_VAR_blocked_project_ids="[\"${BLOCKED_PROJECT_IDS//,/\",\"}\"]"
fi
export CLOUDSDK_CORE_PROJECT="${PROJECT_ID}"
: "${REGION:=us-central1}"
: "${DB_PASSWORD:?set DB_PASSWORD (passed as TF_VAR_db_password; never committed)}"
export TF_VAR_db_password="${DB_PASSWORD}"
: "${ENABLE_GATEWAY:=false}"
: "${ENABLE_FRONTEND:=false}"
: "${SSL_DOMAIN:=}"
: "${FRONTEND_SSL_DOMAIN:=${SSL_DOMAIN}}"
: "${SERVICE_NAME:=${PROJECT_ID}-bridge}"
: "${KYC_SERVICE_NAME:=bridge-kyc}"
: "${RFP_SERVICE_NAME:=bridge-rfp}"
: "${MERIDIAN_SERVICE_NAME:=bridge-meridian}"
: "${AR_REPO:=bridge}"                      # Artifact Registry repo for the image
: "${PARTY_INVOKER_MEMBERS:=}"              # comma-separated run.invoker members (P7-5)
: "${CALLBACK_PATH:=/followups}"            # Cloud Tasks follow-up callback route
: "${TASKS_QUEUE_NAME:=bridge-followups}"   # Cloud Tasks queue name (override if tombstoned)
: "${ENABLE_RFP_BOARD:=true}"               # Deploy Internal RFP Board + 6-Zone Showcase Console
: "${RFP_PROCESS:=benefits-rfp}"            # Default RFP board process skill
: "${RFP_BOARD_INGRESS:=INGRESS_TRAFFIC_ALL}"
: "${MIN_INSTANCES:=0}"                     # Scale-to-zero default for scratch/demo stacks
: "${INVOKER_IAM_DISABLED:=true}"           # --no-invoker-iam-check for Argolis orgs
: "${ENABLE_AUDIT_VAULT:=true}"             # Provision 7-year WORM GCS Audit Vault bucket
: "${ENABLE_TELEMETRY_BQ:=true}"            # Provision BigQuery OTel spans dataset + SQL eval views

echo "==> Enabling required APIs"
gcloud services enable \
  run.googleapis.com \
  sqladmin.googleapis.com \
  secretmanager.googleapis.com \
  cloudtasks.googleapis.com \
  compute.googleapis.com \
  servicenetworking.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  iam.googleapis.com \
  aiplatform.googleapis.com \
  cloudscheduler.googleapis.com \
  cloudtrace.googleapis.com \
  telemetry.googleapis.com \
  observability.googleapis.com \
  apphub.googleapis.com \
  bigquery.googleapis.com \
  bigqueryconnection.googleapis.com \
  documentai.googleapis.com \
  logging.googleapis.com \
  storage.googleapis.com

if [[ "${ENABLE_TELEMETRY_BQ}" == "true" ]]; then
  echo "==> Configuring Cloud Observability default storage location (us) and _Trace linked dataset (obs_traces)"
  gcloud observability settings update \
    --location=global \
    --update-mask=default_storage_location \
    --default-storage-location=us \
    --project="${PROJECT_ID}" || true
  gcloud observability buckets datasets links describe obs_traces \
    --bucket=_Trace \
    --dataset=Spans \
    --location=us \
    --project="${PROJECT_ID}" >/dev/null 2>&1 ||
    gcloud observability buckets datasets links create obs_traces \
      --bucket=_Trace \
      --dataset=Spans \
      --location=us \
      --project="${PROJECT_ID}" || true
fi

# --------------------------------------------------------------------------- #
# 2. Build + push the container image (Artifact Registry).                     #
# --------------------------------------------------------------------------- #
gcloud artifacts repositories describe "${AR_REPO}" --location="${REGION}" >/dev/null 2>&1 ||
  gcloud artifacts repositories create "${AR_REPO}" \
    --repository-format=docker --location="${REGION}" \
    --description="Bridge runtime images"

: "${IMAGE_TAG:=$(git -C "${REPO_ROOT}" rev-parse --short HEAD 2>/dev/null || date +%s)}"
: "${SKIP_IMAGE_BUILD:=false}"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${AR_REPO}/${SERVICE_NAME}:${IMAGE_TAG}"
if [[ "${SKIP_IMAGE_BUILD}" != "true" ]]; then
  echo "==> Running Cloud Build pipeline (lint + pytest + terraform test + image push) for ${IMAGE}"
  gcloud builds submit "${REPO_ROOT}" \
    --config "${REPO_ROOT}/cloudbuild.yaml" \
    --substitutions "_REGION=${REGION},_AR_REPO=${AR_REPO},_SERVICE_NAME=${SERVICE_NAME},_IMAGE=${IMAGE}"
else
  echo "==> Skipping Cloud Build (reusing ${IMAGE})"
fi

# --------------------------------------------------------------------------- #
# 3. Single-pass Terraform init + apply.                                       #
# --------------------------------------------------------------------------- #
export GOOGLE_OAUTH_ACCESS_TOKEN="$(gcloud auth print-access-token)"
terraform -chdir="${TF_DIR}" init -input=false

TF_VARS=(
  -var "project_id=${PROJECT_ID}"
  -var "region=${REGION}"
  -var "service_name=${SERVICE_NAME}"
  -var "kyc_service_name=${KYC_SERVICE_NAME}"
  -var "rfp_service_name=${RFP_SERVICE_NAME}"
  -var "meridian_service_name=${MERIDIAN_SERVICE_NAME}"
  -var "container_image=${IMAGE}"
  -var "enable_gateway=${ENABLE_GATEWAY}"
  -var "enable_frontend=${ENABLE_FRONTEND}"
  -var "enable_rfp_board=${ENABLE_RFP_BOARD}"
  -var "rfp_process=${RFP_PROCESS}"
  -var "rfp_board_ingress=${RFP_BOARD_INGRESS}"
  -var "min_instances=${MIN_INSTANCES}"
  -var "invoker_iam_disabled=${INVOKER_IAM_DISABLED}"
  -var "enable_audit_vault=${ENABLE_AUDIT_VAULT}"
  -var "enable_telemetry_bq=${ENABLE_TELEMETRY_BQ}"
  -var "deletion_protection=false"
  -var "tasks_queue_name=${TASKS_QUEUE_NAME}"
  -var "port_mode=gcp"
  -var "extraction_engine=gemini"
)
if [[ -n "${SSL_DOMAIN}" ]]; then
  TF_VARS+=(-var "ssl_domains=[\"${SSL_DOMAIN}\"]")
fi
if [[ -n "${FRONTEND_SSL_DOMAIN}" ]]; then
  TF_VARS+=(-var "frontend_ssl_domains=[\"${FRONTEND_SSL_DOMAIN}\"]")
fi
if [[ -n "${PARTY_INVOKER_MEMBERS}" ]]; then
  members_hcl="[\"${PARTY_INVOKER_MEMBERS//,/\",\"}\"]"
  TF_VARS+=(-var "party_invoker_members=${members_hcl}")
fi

echo "==> Terraform apply (single pass)"
terraform -chdir="${TF_DIR}" apply -input=false -auto-approve "${TF_VARS[@]}"

tf_out() { terraform -chdir="${TF_DIR}" output -raw "$1"; }

RUNTIME_URI="$(tf_out runtime_service_uri)"
GATEWAY_URL="$(tf_out gateway_url)"
EFFECTIVE_EDGE_URL="${GATEWAY_URL:-${RUNTIME_URI}}"
DB_PRIVATE_IP="$(tf_out db_private_ip)"
DB_NAME="$(tf_out db_name)"
DB_USER="$(tf_out db_user)"

if [[ "${ENABLE_TELEMETRY_BQ}" == "true" ]]; then
  curl -fsS "${RUNTIME_URI}/.well-known/agent-card.json" >/dev/null 2>&1 || true
  curl -fsS "$(tf_out meridian_service_uri)/exchanges" >/dev/null 2>&1 || true
  gcloud observability buckets datasets links describe obs_traces \
    --bucket=_Trace \
    --dataset=Spans \
    --location=us \
    --project="${PROJECT_ID}" >/dev/null 2>&1 ||
    gcloud observability buckets datasets links create obs_traces \
      --bucket=_Trace \
      --dataset=Spans \
      --location=us \
      --project="${PROJECT_ID}" || true
fi

# --------------------------------------------------------------------------- #
# 4. Optional Frontend — build each app + rsync into GCS when enabled.         #
# --------------------------------------------------------------------------- #
PORTAL_URL="$(tf_out portal_url)"
PORTAL_BUCKET="$(tf_out portal_bucket_name)"
CONSOLE_BUCKET="$(tf_out console_bucket_name)"
DASHBOARD_BUCKET="$(tf_out dashboard_bucket_name)"
if [[ -n "${PORTAL_BUCKET}" ]]; then
  echo "==> Building + uploading frontend apps"
  pnpm -C "${REPO_ROOT}" install --frozen-lockfile
  VITE_PORTAL_URL="${EFFECTIVE_EDGE_URL}" \
    pnpm -C "${REPO_ROOT}" --filter provider-portal build
  gcloud storage rsync -r "${REPO_ROOT}/frontend/provider-portal/dist" \
    "gs://${PORTAL_BUCKET}"
  VITE_DASHBOARD_URL="${EFFECTIVE_EDGE_URL}" \
    pnpm -C "${REPO_ROOT}" --filter agent-console build
  gcloud storage rsync -r "${REPO_ROOT}/frontend/agent-console/dist" \
    "gs://${CONSOLE_BUCKET}"
  VITE_DASHBOARD_URL="${EFFECTIVE_EDGE_URL}" \
    pnpm -C "${REPO_ROOT}" --filter ops-dashboard build
  gcloud storage rsync -r "${REPO_ROOT}/frontend/ops-dashboard/dist" \
    "gs://${DASHBOARD_BUCKET}"
fi

# --------------------------------------------------------------------------- #
# 5. Print the parity env block (scripts/parity.sh consumes it).              #
# --------------------------------------------------------------------------- #
cat <<EOF

==> Deploy complete.
    edge_url      : ${EFFECTIVE_EDGE_URL}
    bridge_kyc    : ${RUNTIME_URI}
    bridge_rfp    : $(tf_out rfp_board_uri)
    bridge_merid  : $(tf_out meridian_service_uri)
    portal_url    : ${PORTAL_URL}

    Run the deployed-path parity gate:
      BRIDGE_TEST_GCP=1 \\
      BRIDGE_GCP_TEST_DSN='postgresql+asyncpg://${DB_USER}:<DB_PASSWORD>@${DB_PRIVATE_IP}:5432/${DB_NAME}' \\
      BRIDGE_SKILLS_BUCKET="$(tf_out skills_bucket_name)" \\
      BRIDGE_TASKS_QUEUE="$(tf_out tasks_queue_path)" \\
      BRIDGE_FOLLOWUP_CALLBACK_URL="${RUNTIME_URI}${CALLBACK_PATH}" \\
      BRIDGE_RUNTIME_SA_EMAIL="$(tf_out bridge_runtime_email)" \\
      BRIDGE_GATEWAY_HOST="${EFFECTIVE_EDGE_URL}" \\
      BRIDGE_DEPLOYED_EDGE_URL="${EFFECTIVE_EDGE_URL}" \\
      BRIDGE_TEST_GEMINI=1 \\
      bash scripts/parity.sh
EOF
