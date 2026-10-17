# runtime — Cloud Run v2 hosting the ADK App.
#
# This is the compute host for the deployed ADK `App` (FastAPI + a2a-sdk A2A edge
# + A2UI portal + scoped artifact-fetch + webhook receiver). It does NOT wrap
# Vertex Agent Engine — the managed session/Memory Bank appears only behind the
# Sessions seam's GCP adapter (P7-2 config), never as the compute host
# (design/gcp-substrate.md §"runtime = Cloud Run v2 … It does NOT wrap Vertex
# Agent Engine").

# The service's own public address. Cloud Run assigns every v2 service a
# DETERMINISTIC url — https://{service}-{project_number}.{region}.run.app — that
# is computable at plan time from inputs alone (no dependency on the service's own
# post-apply .uri). This breaks the self-reference cycle that otherwise forces a
# two-phase deploy: the runtime can advertise its own URL (served-card same-origin,
# P7-5) and target its own follow-up callback (P7-4) from a single apply, with no
# -var injection. The public_url / followup_callback_url vars remain as explicit
# OVERRIDES for the gateway/custom-domain path (where the entrypoint is the LB, not
# the run.app URL).
data "google_project" "this" {
  project_id = var.project_id
}

locals {
  # Computed-ingress rule (design/zones.md, design/gcp-substrate.md): when the
  # Gateway fronts the service, ingress is LB-only; otherwise all ingress is
  # accepted (local/dev default). Production sets enable_gateway = true.
  ingress = var.enable_gateway ? "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER" : "INGRESS_TRAFFIC_ALL"

  # The plan-time-knowable deterministic Cloud Run URL for this service.
  default_service_url = "https://${var.service_name}-${data.google_project.this.number}.${var.region}.run.app"

  # Public URL the served Agent Card advertises. Explicit override wins (the
  # gateway/domain URL); else, when the gateway does NOT front the service, the
  # deterministic run.app URL is the public entrypoint. When the gateway IS on but
  # no override is given, stay empty (the gateway URL must be supplied explicitly —
  # falling back to run.app would break same-origin behind the LB).
  effective_public_url = var.public_url != "" ? var.public_url : (var.enable_gateway ? "" : local.default_service_url)

  # Follow-up callback target Cloud Tasks POSTs to. Explicit override wins; else the
  # service's own deterministic URL + /followups, but only when a tasks queue is
  # wired (no queue → no callback env, matching the P7-1 default).
  effective_callback_url = var.followup_callback_url != "" ? var.followup_callback_url : (var.tasks_queue_path != "" ? "${local.default_service_url}/followups" : "")

  # The RFP board service (S0-arch-17) and its deterministic run.app URL — the Cloud
  # Scheduler target + OIDC audience. Plan-time-known (the same trick as
  # default_service_url); the resource's own .uri is unknown at plan time.
  board_service_name = "${var.service_name}-rfp-board"
  board_service_url  = "https://${local.board_service_name}-${data.google_project.this.number}.${var.region}.run.app"

  # The board service's PLAIN envs (names / identifiers / modes — no secrets), each
  # dropped when empty. BRIDGE_PUBLIC_URL is the A2A edge's URL on purpose: the
  # processor's artifact refs point at the edge's /artifacts fetch route (shared
  # artifacts bucket). BRIDGE_EMAIL_DRAIN_INTERVAL = 0: Cloud Run (request-based CPU)
  # starves a background loop between requests, so the drain runs inside the
  # Scheduler's request instead. BRIDGE_RUNTIME_SA_EMAIL + BRIDGE_GATEWAY_HOST are the
  # a2a_client gcp adapter's config (the OIDC token SA + the Gateway-authorized host):
  # the board's operator chase route (POST /rfps/{id}/chase) sends through it, and
  # 503s without both (S0-arch-19).
  board_plain_env = {
    BRIDGE_RFP_PROCESS          = var.rfp_process
    BRIDGE_RUNTIME_SA_EMAIL     = var.runtime_sa_email
    BRIDGE_GATEWAY_HOST         = var.gateway_host
    BRIDGE_SEAM_MODE            = var.seam_mode
    BRIDGE_ARTIFACT_BUCKET      = var.artifacts_bucket
    BRIDGE_VAULT_BUCKET         = var.vault_bucket
    BRIDGE_SKILLS_BUCKET        = var.skills_bucket
    BRIDGE_EXTRACTION_ENGINE    = var.extraction_engine
    BRIDGE_DOCAI_PROCESSOR      = var.docai_processor
    BRIDGE_PUBLIC_URL           = local.effective_public_url
    BRIDGE_EMAIL_BUCKET         = var.email_bucket
    BRIDGE_EMAIL_MAILBOX        = var.email_mailbox
    BRIDGE_EMAIL_DRAIN_INTERVAL = "0"
  }
}

resource "google_cloud_run_v2_service" "bridge" {
  project  = var.project_id
  name     = var.service_name
  location = var.region
  ingress  = local.ingress
  labels   = var.labels

  deletion_protection = var.deletion_protection

  template {
    service_account = var.service_account_email

    containers {
      image = var.container_image

      # The DB DSN reaches the app ONLY as a secret env, sourced from the latest
      # version of the Secret Manager secret (the secrets module, P7-2) — never a
      # plaintext env value or tfvars. Gated on db_url_secret_id so the P7-1
      # default (no secret) leaves the container env untouched. The Bridge then
      # connects to Cloud SQL over the private-IP VPC path above, never an HTTP
      # hop (DECISIONS.md 2026-08-24).
      dynamic "env" {
        for_each = var.db_url_secret_id != "" ? [1] : []
        content {
          name = "DATABASE_URL"
          value_source {
            secret_key_ref {
              secret  = var.db_url_secret_id
              version = "latest"
            }
          }
        }
      }

      # Bucket NAMES are not secrets (P7-3) — inject as PLAIN envs (never a
      # secret_key_ref). Each gated so the P7-1 no-bucket default leaves the
      # container env untouched (runtime tests stay green). The gcs module
      # provisions the buckets + the runtime SA's per-bucket objectAdmin grant.
      dynamic "env" {
        for_each = var.skills_bucket != "" ? [1] : []
        content {
          name  = "BRIDGE_SKILLS_BUCKET"
          value = var.skills_bucket
        }
      }

      dynamic "env" {
        for_each = var.artifacts_bucket != "" ? [1] : []
        content {
          name  = "BRIDGE_ARTIFACT_BUCKET"
          value = var.artifacts_bucket
        }
      }

      dynamic "env" {
        for_each = var.vault_bucket != "" ? [1] : []
        content {
          name  = "BRIDGE_VAULT_BUCKET"
          value = var.vault_bucket
        }
      }

      # Cloud Tasks scheduler wiring (P7-4) — all PLAIN envs (a queue path, an SA
      # email, and a callback URL are NOT secrets; never a secret_key_ref). Each
      # gated so the P7-1 no-tasks default leaves the container env untouched
      # (runtime tests stay green).
      dynamic "env" {
        for_each = var.tasks_queue_path != "" ? [1] : []
        content {
          name  = "BRIDGE_TASKS_QUEUE"
          value = var.tasks_queue_path
        }
      }

      dynamic "env" {
        for_each = var.runtime_sa_email != "" ? [1] : []
        content {
          name  = "BRIDGE_RUNTIME_SA_EMAIL"
          value = var.runtime_sa_email
        }
      }

      # The follow-up callback URL — the service's OWN deterministic run.app URL +
      # /followups (or an explicit override). Computed from a plan-time local, so it
      # needs no out-of-band injection and no Terraform cycle.
      dynamic "env" {
        for_each = local.effective_callback_url != "" ? [1] : []
        content {
          name  = "BRIDGE_FOLLOWUP_CALLBACK_URL"
          value = local.effective_callback_url
        }
      }

      # The seam master switch (P7-9) — BRIDGE_SEAM_MODE routes EVERY seam through
      # its adapter (bridge/seams/config.py:resolve_mode). The deployed container sets
      # it to "gcp" so sessions/task_store/exchange_store hit Cloud SQL, skill_registry
      # GCS, scheduler Cloud Tasks, a2a_client Agent Identity — never a per-replica
      # in-memory store (the session-affinity trap, DECISIONS.md 2026-08-24). PLAIN env
      # (a mode name is not a secret). Gated so the P7-1/local default ("") leaves the
      # container env untouched (runtime tests stay green); the P7-8 service-wiring
      # guard is the static backstop if this is ever missed.
      dynamic "env" {
        for_each = var.seam_mode != "" ? [1] : []
        content {
          name  = "BRIDGE_SEAM_MODE"
          value = var.seam_mode
        }
      }

      # The extraction capability axis (P7-6) — BRIDGE_EXTRACTION_ENGINE selects the
      # deployed extraction engine (fixture|gemini|docai). The deployed container sets
      # it to "gemini" (the GcpExtraction default) so the extraction seam runs real
      # Gemini. PLAIN env (an engine name is not a secret). Gated so the P7-1/local
      # default ("") leaves the container env untouched (runtime tests stay green).
      dynamic "env" {
        for_each = var.extraction_engine != "" ? [1] : []
        content {
          name  = "BRIDGE_EXTRACTION_ENGINE"
          value = var.extraction_engine
        }
      }

      # The docai engine's processor (the client's existing Document AI processor,
      # by full resource name). PLAIN env — a resource name is not a secret. Gated
      # like the engine env so the default plan leaves the container env untouched.
      dynamic "env" {
        for_each = var.docai_processor != "" ? [1] : []
        content {
          name  = "BRIDGE_DOCAI_PROCESSOR"
          value = var.docai_processor
        }
      }

      # The public URL as a PLAIN env (P7-5) — not a secret. The served Agent Card
      # advertises this origin so a consumer's RemoteA2aAgent passes the adk >=2.7
      # same-origin card validation (DECISIONS.md P7-5). Sourced from the computed
      # local: the deterministic run.app URL when gateway-less, or an explicit
      # override (the gateway/domain URL). Empty only when the gateway is on and no
      # override is supplied — no env, no cycle, single apply.
      dynamic "env" {
        for_each = local.effective_public_url != "" ? [1] : []
        content {
          name  = "BRIDGE_PUBLIC_URL"
          value = local.effective_public_url
        }
      }

      # The RFP opener (S0-arch-19): with the RFP board service enabled, this A2A
      # runtime OPENS RFPs of that process (a broker agent's CollectRequest) onto the
      # durable exchange store the board service reads — the same Cloud SQL DB
      # (DATABASE_URL + BRIDGE_SEAM_MODE=gcp). It holds an opener-only board: it still
      # binds no email processor and carries no BRIDGE_EMAIL_* (S0-arch-17) — the
      # processor envs ride the RFP board service below.
      dynamic "env" {
        for_each = var.enable_rfp_board && var.rfp_process != "" ? [1] : []
        content {
          name  = "BRIDGE_RFP_PROCESS"
          value = var.rfp_process
        }
      }
    }

    # Direct VPC egress into the internal (trusted) subnet. This is the
    # private-IP path to Cloud SQL (P7-2) — datastore traffic stays on the VPC
    # and is NEVER pushed onto an HTTP hop (DECISIONS.md 2026-08-24
    # vibeflix-audit; TODO.md P7 "Gateway/Identity hardening"). PRIVATE_RANGES_ONLY
    # keeps only RFC-1918 traffic on the VPC path.
    vpc_access {
      egress = "PRIVATE_RANGES_ONLY"

      network_interfaces {
        network    = var.vpc_network
        subnetwork = var.egress_subnet
      }
    }
  }
}

# ---------------------------------------------------------------------------
# Email intake webhook (S0-arch-13, design/email-intake.md) — a SECOND, webhook-only
# Cloud Run service on the same image. Graph POSTs change notifications here; the
# service authenticates each one by clientState, fetches the raw MIME from ITS OWN
# mailbox by id, and lands it create-only in the email bucket. It hosts NO
# processor, no board, no A2A edge, and runs as the separate webhook SA whose only
# grants are secretAccessor on the two Graph secrets + objectCreator on the email
# bucket. Gated on enable_email_ingress: the default plan creates nothing.
# ---------------------------------------------------------------------------
resource "google_cloud_run_v2_service" "email_webhook" {
  count = var.enable_email_ingress ? 1 : 0

  project  = var.project_id
  name     = "${var.service_name}-email-webhook"
  location = var.region
  # Graph reaches the service from Microsoft's network, so ingress is ALL (it cannot
  # come through the internal LB / a Google identity — see the invoker note below).
  ingress = "INGRESS_TRAFFIC_ALL"
  labels  = var.labels

  template {
    service_account = var.email_webhook_sa_email

    scaling {
      max_instance_count = 3
    }

    containers {
      image   = var.container_image
      command = ["python"]
      args    = ["-c", "from bridge.edge.email_ingress import serve_email_webhook_container; serve_email_webhook_container()"]

      # Names and identifiers — PLAIN envs (a bucket name, a mailbox address, a tenant
      # id, and an app-registration client id are not secrets).
      env {
        name  = "BRIDGE_EMAIL_BUCKET"
        value = var.email_bucket
      }
      env {
        name  = "BRIDGE_EMAIL_MAILBOX"
        value = var.email_mailbox
      }
      env {
        name  = "BRIDGE_EMAIL_GRAPH_TENANT"
        value = var.email_graph_tenant_id
      }
      env {
        name  = "BRIDGE_EMAIL_GRAPH_CLIENT_ID"
        value = var.email_graph_client_id
      }
      # Only the email_ingress seam is used here; route it to the GCS adapter.
      env {
        name  = "BRIDGE_SEAM_EMAIL_INGRESS"
        value = "gcp"
      }

      # The two Graph secrets — secret_key_ref only, never a plaintext value; the
      # VALUES are added out-of-band (no secret version in HCL, CRITICAL.md).
      env {
        name = "BRIDGE_EMAIL_GRAPH_CLIENT_SECRET"
        value_source {
          secret_key_ref {
            secret  = var.email_graph_client_secret_id
            version = "latest"
          }
        }
      }
      env {
        name = "BRIDGE_EMAIL_CLIENT_STATE"
        value_source {
          secret_key_ref {
            secret  = var.email_client_state_secret_id
            version = "latest"
          }
        }
      }
    }
  }
}

# DELIBERATE TRUST-BOUNDARY EXCEPTION: allUsers may invoke the webhook service.
# Microsoft Graph cannot present a Google identity, so Cloud Run IAM cannot
# authenticate it. Authentication is instead the per-subscription clientState secret
# (compared in constant time before ANY Graph call), and the blast radius is bounded:
# the service fetches only from its own configured mailbox by validated id, its SA
# can only CREATE objects in one bucket, and it holds no runtime/DB/Vertex grants.
# An org policy with Domain-Restricted Sharing (iam.allowedPolicyMemberDomains)
# rejects allUsers — the operator then needs a project-level exception or an
# external-LB/Gateway path in front of the service (DECISIONS.md S0-arch-13).
resource "google_cloud_run_v2_service_iam_member" "email_webhook_public" {
  count = var.enable_email_ingress ? 1 : 0

  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.email_webhook[0].name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# ---------------------------------------------------------------------------
# RFP board host (S0-arch-17, design/email-intake.md) — a THIRD Cloud Run service on
# the same image. It owns the RFP board over the durable exchange store (Cloud SQL,
# DATABASE_URL) and binds the email processor, serving the internal-zone RFP read API
# + POST /email/drain + GET /email/outcomes/{key} (the markers carry sender, subject,
# RFP and carrier — never on the public A2A edge or the public webhook). Runs as
# bridge-runtime, which already holds cloudsql.client, aiplatform.user, the artifacts
# bucket, the email bucket objectViewer + objectCreator and the token-key accessor —
# no new grant. Internal-only ingress, no allUsers: Cloud Scheduler POSTs
# /email/drain every tick with an OIDC token for bridge-runtime (the only invoker).
# BRIDGE_EMAIL_DRAIN_INTERVAL = 0 — request-based CPU starves a background loop, so
# the drain runs inside the Scheduler's request. Gated on enable_rfp_board.
# ---------------------------------------------------------------------------
resource "google_cloud_run_v2_service" "rfp_board" {
  count = var.enable_rfp_board ? 1 : 0

  project  = var.project_id
  name     = local.board_service_name
  location = var.region
  ingress  = var.rfp_board_ingress
  labels   = var.labels

  deletion_protection = var.deletion_protection

  template {
    service_account = var.service_account_email

    # 1 serializes drains on the in-process lock; more replicas are safe (CAS retry +
    # re-file guard in the processor) and only cost duplicate extraction.
    scaling {
      max_instance_count = var.rfp_board_max_instances
    }

    containers {
      image   = var.container_image
      command = ["python"]
      args    = ["-c", "from bridge.dashboard.board_host import serve_rfp_board_container; serve_rfp_board_container()"]

      dynamic "env" {
        for_each = { for k, v in local.board_plain_env : k => v if v != "" }
        content {
          name  = env.key
          value = env.value
        }
      }

      # The durable exchange store's DSN — secret_key_ref only (the runtime's secret).
      dynamic "env" {
        for_each = var.db_url_secret_id != "" ? [1] : []
        content {
          name = "DATABASE_URL"
          value_source {
            secret_key_ref {
              secret  = var.db_url_secret_id
              version = "latest"
            }
          }
        }
      }

      # The intake-token HMAC key — secret_key_ref, never a plaintext value.
      dynamic "env" {
        for_each = var.email_token_secret_id != "" ? [1] : []
        content {
          name = "BRIDGE_EMAIL_TOKEN_SECRET"
          value_source {
            secret_key_ref {
              secret  = var.email_token_secret_id
              version = "latest"
            }
          }
        }
      }
    }

    # Same private-IP path to Cloud SQL as the runtime (never an HTTP hop).
    vpc_access {
      egress = "PRIVATE_RANGES_ONLY"

      network_interfaces {
        network    = var.vpc_network
        subnetwork = var.egress_subnet
      }
    }
  }

  lifecycle {
    precondition {
      condition     = var.rfp_process != ""
      error_message = "enable_rfp_board requires rfp_process (the emergent-Collect process skill the board runs)."
    }
  }
}

# Only bridge-runtime may invoke the board service — it is the identity Cloud Scheduler
# mints the OIDC token for. No allUsers.
resource "google_cloud_run_v2_service_iam_member" "rfp_board_invoker" {
  count = var.enable_rfp_board ? 1 : 0

  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.rfp_board[0].name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${var.service_account_email}"
}

# The drain trigger: a Scheduler tick, not a GCS-finalize push (a push per object would
# fire for the attachments + markers the processor writes itself and race one key
# across instances; a tick drains the landing bucket — the queue — in landing order).
# retry_count = 0: the next tick IS the retry (a 409 / 5xx leaves the key pending).
resource "google_cloud_scheduler_job" "email_drain" {
  count = var.enable_rfp_board && var.enable_email_ingress ? 1 : 0

  project          = var.project_id
  region           = var.region
  name             = "${var.service_name}-email-drain"
  description      = "Drain landed carrier email onto the RFP board (POST /email/drain)."
  schedule         = var.email_drain_schedule
  time_zone        = "Etc/UTC"
  attempt_deadline = "300s"

  retry_config {
    retry_count = 0
  }

  http_target {
    http_method = "POST"
    uri         = "${local.board_service_url}/email/drain"

    oidc_token {
      service_account_email = var.service_account_email
      audience              = local.board_service_url
    }
  }

  depends_on = [google_cloud_run_v2_service_iam_member.rfp_board_invoker]
}
