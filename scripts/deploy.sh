#!/usr/bin/env bash
# P7-9 deploy — build the Bridge image, provision the GCP substrate, and deploy the
# runtime + gateway + frontend. A TWO-PHASE terraform apply resolves the self-reference
# cycle: public_url / followup_callback_url are the runtime's OWN URL, unknown until
# after the first apply (wiring module.runtime.service_uri back into module.runtime is a
# Terraform cycle — DECISIONS.md P7-4/P7-5), so they default "" for phase 1 and are
# injected out-of-band in phase 2.
#
# SECRETS: the DB DSN (which embeds the password) is composed at deploy and piped to
# `gcloud secrets versions add` on STDIN — NEVER written to disk, HCL, or tfvars
# (CRITICAL.md "No secrets in HCL"; P7-2 no-secret_version structural guard). DB_PASSWORD
# is read from the environment only.
#
# Live GCP auth + a provisioned-project budget are required; this is the gated OPERATOR
# step of P7-9 (the credential-free artifacts + parity harness are the in-sandbox
# deliverable). Run `scripts/parity.sh` after this to exercise the deployed-path gate.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TF_DIR="${REPO_ROOT}/terraform"

# --------------------------------------------------------------------------- #
# 1. Preflight — required inputs (all non-secret except DB_PASSWORD, from env). #
# --------------------------------------------------------------------------- #
: "${PROJECT_ID:?set PROJECT_ID}"
: "${REGION:=us-central1}"
: "${DB_PASSWORD:?set DB_PASSWORD (never committed; only ever piped to Secret Manager)}"
: "${SSL_DOMAIN:?set SSL_DOMAIN (managed-cert domain for the runtime/edge HTTPS LB)}"
: "${FRONTEND_SSL_DOMAIN:=${SSL_DOMAIN}}"
: "${SERVICE_NAME:=bridge-runtime}"
: "${AR_REPO:=bridge}"                      # Artifact Registry repo for the image
: "${PARTY_INVOKER_MEMBERS:=}"              # comma-separated run.invoker members (P7-5)
: "${CALLBACK_PATH:=/followups}"            # Cloud Tasks follow-up callback route
: "${ENABLE_RFP_BOARD:=true}"               # Deploy Internal RFP Board + 6-Zone Showcase Console
: "${ENABLE_AUDIT_VAULT:=true}"             # Provision 7-year WORM GCS Audit Vault bucket
: "${ENABLE_TELEMETRY_BQ:=true}"            # Provision BigQuery OTel spans dataset + SQL eval views

gcloud config set project "${PROJECT_ID}" >/dev/null

echo "==> Enabling required APIs"
gcloud services enable \
  run.googleapis.com \
  sqladmin.googleapis.com \
  secretmanager.googleapis.com \
  cloudtasks.googleapis.com \
  compute.googleapis.com \
  servicenetworking.googleapis.com \
  artifactregistry.googleapis.com \
  iam.googleapis.com \
  aiplatform.googleapis.com \
  cloudscheduler.googleapis.com \
  cloudtrace.googleapis.com \
  bigquery.googleapis.com \
  documentai.googleapis.com \
  logging.googleapis.com \
  storage.googleapis.com

# --------------------------------------------------------------------------- #
# 2. Build + push the container image (Artifact Registry).                     #
# --------------------------------------------------------------------------- #
gcloud artifacts repositories describe "${AR_REPO}" --location="${REGION}" >/dev/null 2>&1 ||
  gcloud artifacts repositories create "${AR_REPO}" \
    --repository-format=docker --location="${REGION}" \
    --description="Bridge runtime images"

IMAGE_TAG="$(git -C "${REPO_ROOT}" rev-parse --short HEAD 2>/dev/null || date +%s)"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${AR_REPO}/${SERVICE_NAME}:${IMAGE_TAG}"
echo "==> Running Cloud Build pipeline (lint + pytest + terraform test + image push) for ${IMAGE}"
gcloud builds submit "${REPO_ROOT}" \
  --config "${REPO_ROOT}/cloudbuild.yaml" \
  --substitutions "_REGION=${REGION},_AR_REPO=${AR_REPO},_SERVICE_NAME=${SERVICE_NAME},_IMAGE=${IMAGE}"

# --------------------------------------------------------------------------- #
# 3. Terraform init.                                                           #
# --------------------------------------------------------------------------- #
terraform -chdir="${TF_DIR}" init -input=false

# Shared plan-time vars for both phases. deletion_protection=false for the demo stack.
TF_VARS=(
  -var "project_id=${PROJECT_ID}"
  -var "region=${REGION}"
  -var "service_name=${SERVICE_NAME}"
  -var "container_image=${IMAGE}"
  -var "enable_gateway=true"
  -var "enable_frontend=true"
  -var "enable_rfp_board=${ENABLE_RFP_BOARD}"
  -var "enable_audit_vault=${ENABLE_AUDIT_VAULT}"
  -var "enable_telemetry_bq=${ENABLE_TELEMETRY_BQ}"
  -var "deletion_protection=false"
  -var "ssl_domains=[\"${SSL_DOMAIN}\"]"
  -var "frontend_ssl_domains=[\"${FRONTEND_SSL_DOMAIN}\"]"
  -var "seam_mode=gcp"
  -var "extraction_engine=gemini"
)
if [[ -n "${PARTY_INVOKER_MEMBERS}" ]]; then
  # Turn a,b,c into ["a","b","c"] for the list(string) var.
  members_hcl="[\"${PARTY_INVOKER_MEMBERS//,/\",\"}\"]"
  TF_VARS+=(-var "party_invoker_members=${members_hcl}")
fi

# --------------------------------------------------------------------------- #
# 4. Apply phase 1 — everything except the runtime's own-URL envs (default "").#
# --------------------------------------------------------------------------- #
echo "==> Terraform apply (phase 1)"
terraform -chdir="${TF_DIR}" apply -input=false -auto-approve \
  "${TF_VARS[@]}" \
  -var 'public_url=' \
  -var 'followup_callback_url='

tf_out() { terraform -chdir="${TF_DIR}" output -raw "$1"; }

# --------------------------------------------------------------------------- #
# 5. Inject the DB DSN secret VALUE out-of-band (piped; never on disk/in HCL). #
# --------------------------------------------------------------------------- #
DB_PRIVATE_IP="$(tf_out db_private_ip)"
DB_NAME="$(tf_out db_name)"
DB_USER="$(tf_out db_user)"
DB_SECRET_ID="$(tf_out db_secret_id)"
echo "==> Adding DATABASE_URL secret version (DSN composed in-memory, piped to stdin)"
# asyncpg async driver DSN targeting Cloud SQL over the private-IP VPC path (never HTTP).
printf 'postgresql+asyncpg://%s:%s@%s:5432/%s' \
  "${DB_USER}" "${DB_PASSWORD}" "${DB_PRIVATE_IP}" "${DB_NAME}" |
  gcloud secrets versions add "${DB_SECRET_ID}" --data-file=-

# --------------------------------------------------------------------------- #
# 6. Resolve URLs + apply phase 2 (inject the runtime's own-URL envs).         #
# --------------------------------------------------------------------------- #
RUNTIME_URI="$(tf_out runtime_service_uri)"
GATEWAY_URL="$(tf_out gateway_url)"
echo "==> Terraform apply (phase 2) — public_url=${GATEWAY_URL}"
terraform -chdir="${TF_DIR}" apply -input=false -auto-approve \
  "${TF_VARS[@]}" \
  -var "public_url=${GATEWAY_URL}" \
  -var "followup_callback_url=${RUNTIME_URI}${CALLBACK_PATH}"

# --------------------------------------------------------------------------- #
# 7. Frontend — build each app with its deployed endpoint + rsync into GCS.    #
#    (No bucket OBJECTS in HCL — assets uploaded here, P7-7 invariant.)        #
# --------------------------------------------------------------------------- #
PORTAL_URL="$(tf_out portal_url)"
PORTAL_BUCKET="$(tf_out portal_bucket_name)"
CONSOLE_BUCKET="$(tf_out console_bucket_name)"
DASHBOARD_BUCKET="$(tf_out dashboard_bucket_name)"
if [[ -n "${PORTAL_BUCKET}" ]]; then
  echo "==> Building + uploading frontend apps"
  pnpm -C "${REPO_ROOT}" install --frozen-lockfile
  # provider-portal (external zone, public bucket) — VITE_PORTAL_URL = the edge/gateway.
  VITE_PORTAL_URL="${GATEWAY_URL}" \
    pnpm -C "${REPO_ROOT}" --filter provider-portal build
  gcloud storage rsync -r "${REPO_ROOT}/frontend/provider-portal/dist" \
    "gs://${PORTAL_BUCKET}"
  # agent-console + ops-dashboard (internal zone, private staging buckets).
  VITE_DASHBOARD_URL="${GATEWAY_URL}" \
    pnpm -C "${REPO_ROOT}" --filter agent-console build
  gcloud storage rsync -r "${REPO_ROOT}/frontend/agent-console/dist" \
    "gs://${CONSOLE_BUCKET}"
  VITE_DASHBOARD_URL="${GATEWAY_URL}" \
    pnpm -C "${REPO_ROOT}" --filter ops-dashboard build
  gcloud storage rsync -r "${REPO_ROOT}/frontend/ops-dashboard/dist" \
    "gs://${DASHBOARD_BUCKET}"
fi

# --------------------------------------------------------------------------- #
# 8. Print the parity env block (scripts/parity.sh consumes it).              #
# --------------------------------------------------------------------------- #
cat <<EOF

==> Deploy complete.
    gateway_url : ${GATEWAY_URL}
    runtime_uri : ${RUNTIME_URI}
    portal_url  : ${PORTAL_URL}

    Run the deployed-path parity gate:
      BRIDGE_TEST_GCP=1 \\
      BRIDGE_GCP_TEST_DSN='postgresql+asyncpg://${DB_USER}:<DB_PASSWORD>@${DB_PRIVATE_IP}:5432/${DB_NAME}' \\
      BRIDGE_SKILLS_BUCKET="$(tf_out skills_bucket_name)" \\
      BRIDGE_TASKS_QUEUE="$(tf_out tasks_queue_path)" \\
      BRIDGE_FOLLOWUP_CALLBACK_URL="${RUNTIME_URI}${CALLBACK_PATH}" \\
      BRIDGE_RUNTIME_SA_EMAIL="$(tf_out bridge_runtime_email)" \\
      BRIDGE_GATEWAY_HOST="${GATEWAY_URL}" \\
      BRIDGE_DEPLOYED_EDGE_URL="${GATEWAY_URL}" \\
      BRIDGE_TEST_GEMINI=1 \\
      bash scripts/parity.sh
EOF
