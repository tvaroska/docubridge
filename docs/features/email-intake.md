# Email Intake

Feature area for **email ingestion into RFP boards** — inbound email processor,
correlation to open RFPs, attachment extraction + filing into collection cells, and
deployed infrastructure to drain the landing bucket.

Completed-work archive for this feature area (`docs/features/`). This holds the
plan **substance**, not links — `.claude/plans/*` are local and gitignored, so
their reasoning must be captured HERE (and decisions logged in `DECISIONS.md`).

**Requirements (THE WHAT) live in `design/`, already authored — do not duplicate here:**
- `design/email-intake.md` — email processor architecture, three-phase processing
  (extraction, correlation, filing), token-based reply addresses, deployed topology.
- `design/rfp-views.md` — RFP read API surface (served by both demo hosts and the
  deployed board host).
- `design/gcp-substrate.md` — deployed services (webhook, runtime, RFP board host),
  Cloud Scheduler drain trigger.

---

## S0-arch-19: The deployed A2A runtime opens RFPs onto the durable board

**Completed:** 2026-10-01

**Gap.** S0-arch-17 deployed the board host, but nothing deployed opened an RFP. The broker
agent's emergent-Collect `CollectRequest` reached the A2A runtime's `Collector`, so every
drained carrier reply was quarantined `no_exchange_token`.

**What was built:**

- **`bridge/edge/rfp_open.py`** (new):
  - `rfp_board_from_env()` returns an opener-only `RfpBoard` for `BRIDGE_RFP_PROCESS`
    over `exchange_store_from_env()`. Unset or blank returns `None`, and the edge is
    unchanged. A skill that is not emergent-Collect raises `ValueError` at build. An
    in-memory store raises `RuntimeError` naming `BRIDGE_EXCHANGE_DB_URL` /
    `DATABASE_URL`.
  - `rfp_context_id()`: `rfp_id` = `request.context_id`, else the A2A context. It must
    match `RFP_CONTEXT_RE`.
  - `open_or_update_rfp()` runs read → open / `update_requirements` / status read →
    put → `to_exchange_turn` as one sync call. A `StaleExchangeWrite` is retried 3 times.
    An identical requirements list writes nothing.
- **`CollectExecutor`**:
  - `bind_rfp(board)`. In `_execute_collect`, a `CollectRequest` for the board's process
    goes to `_execute_rfp`. That method runs `open_or_update_rfp` on a worker thread
    (`asyncio.to_thread`, because CAS versions are thread-local) and emits the turn:
    `INPUT_REQUIRED`, every carrier × line cell `invited`.
  - Refusals are JSON-RPC errors raised before any task event:
    `rfp_submissions_unsupported` (inline or URL file parts), `invalid_rfp_context`,
    `invalid_rfp_request: <reason>` (all InvalidParamsError) and
    `rfp_changed_concurrently` (InvalidRequestError).
  - The route is model-free in both edge modes. Other processes stay on the Collector.
- **`build_a2a_app(rfp_board=None)`**: `rfp_board or rfp_board_from_env()`, bound on the
  executor and exposed as `app.state.rfp_board`.
- **`RFP_PROCESS_ENV`** moved to `bridge/rfp.py`. `board_host` re-exports it.
- **Terraform**:
  - With `enable_rfp_board` and `rfp_process`, the A2A runtime (`google_cloud_run_v2_service.bridge`)
    gets `BRIDGE_RFP_PROCESS`.
  - The board service gets `BRIDGE_RUNTIME_SA_EMAIL` and `BRIDGE_GATEWAY_HOST` (new root +
    module var `gateway_host`, default `""`, omitted when empty) for its chase route.
  - The A2A runtime still carries no `BRIDGE_EMAIL_*`.

**Why the A2A runtime, not the board host:** the broker agent already speaks A2A to the
runtime. The board host stays internal-zone (read API, drain, outcome markers), and the
shared durable store is the hand-off between them.

**Residuals:**
- A changed `due_at` on a follow-up is ignored, because it is fixed at open.
- The edge has no per-party authorization on who may open or update an RFP. That is the
  Gateway's job, as on the rest of the A2A edge.
- A refusal sent with an existing `task_id` marks that task FAILED, like every
  `execute`-time refusal.

**Verification:**
- `tests/test_rfp_a2a_open.py` (30 tests). The deployed-path driver is
  `test_mail_drained_by_board_host_files_onto_rfp_opened_through_a2a`, run in both edge
  modes with 0 LLM calls.
- `terraform/tests/rfp_board.tftest.hcl` gains the new env assertions.

---

## S0-arch-17: Deployed RFP board host with email processor

**Completed:** 2026-10-01

Built the **deployed RFP board host** — a third Cloud Run service that owns the durable
RFP board, binds the email processor, and drains the GCS landing bucket on a Cloud
Scheduler tick. This completes the deployed email-intake path: the public webhook writes
landed mail into GCS, the internal board host drains it, correlates to open RFPs on the
shared durable exchange store, and files extracted attachments into collection cells.

**What was built:**

- **`bridge/dashboard/board_host.py`**: New deployed-host module with
  `build_rfp_board_app(...)` and `serve_rfp_board_container()`. The app mounts the
  dashboard (RFP read API at `/rfps`, drain at `/email/drain`) and runs an outer lifespan
  that composes the drain loop (when interval > 0) with store cleanup on shutdown. The
  host builds one `RfpBoard` over the durable `exchange_store_from_env()` (Cloud SQL
  `bridge_exchange_records`), reads the process skill from `BRIDGE_RFP_PROCESS` env, and
  binds the email processor only when email env is configured. **S0-bug-3 rule refined**:
  the host raises `RuntimeError` if a processor would bind over an in-memory store — the
  deployed board is the source of truth (a read-through view of the durable shared
  exchange store every RFP writer uses), so binding is safe. No email env → no processor
  → in-memory is allowed for read API only.

- **Cross-replica drain safety**: Three layers to handle concurrent drains across
  multiple instances — (a) `rfp_board_max_instances` default 1 (drains serialize on the
  in-process lock in the common case); (b) **CAS retry** in `EmailProcessor.process`
  phase 3: on `StaleExchangeWrite`, fresh read → apply → put again up to 3 attempts
  (extraction and artifact persist are not redone); (c) **re-file guard**: if a fresh
  read already holds any `email-<key>-<i>` doc id of this mail, skip the mutation and
  write the marker from stored entries. This upgrades "at-least-once may duplicate a
  version" to "no duplicate version" for mail with extracted attachments. Residuals: an
  unreadable-only mail re-applied may append one audit event; a retry after a crash in
  phase 2–3 may add one extra artifact version.

- **`StaleExchangeWrite` → HTTP 409** exception handler on the dashboard app (covers
  `/email/drain` and chase) and portal app (upload into a row). The handler returns
  `{"detail": "RFP '<ref>' changed concurrently; retry"}` with status 409. On
  `/email/drain` an exhausted retry surfaces as a logged Scheduler failure the next tick
  absorbs.

- **Terraform infrastructure** (`modules/runtime/`, root):
  - **Third Cloud Run service** `<service_name>-rfp-board` on the same image, running as
    `bridge-runtime` SA (which already holds cloudsql.client, artifacts bucket,
    email bucket objectViewer + objectCreator, and token-key accessor — no new SA grant).
    Ingress `INGRESS_TRAFFIC_INTERNAL_ONLY` by default (validated variable, can override
    to `INGRESS_TRAFFIC_ALL` if Scheduler 403/404), no `allUsers`, `roles/run.invoker`
    for bridge-runtime SA only (the Scheduler's OIDC identity).
  - **Cloud Scheduler job** `<service_name>-email-drain` POSTs to the deterministic
    board URL `https://<name>-<project-number>.<region>.run.app/email/drain` with OIDC
    token (audience = board URL, SA email = bridge-runtime). Schedule default
    `* * * * *` (every minute), `attempt_deadline = 300s`, `retry_count = 0` (the next
    tick is the retry).
  - **Board env**: `BRIDGE_RFP_PROCESS`, `BRIDGE_EMAIL_DRAIN_INTERVAL=0` (Cloud Run
    request-based CPU starves a background loop between requests; the drain runs inside
    the Scheduler's request), `DATABASE_URL` secret ref, `BRIDGE_EMAIL_TOKEN_SECRET`
    secret ref, plus the shared artifact/skills bucket names and seam mode.
  - **Processor env moved off the A2A runtime**: `google_cloud_run_v2_service.bridge` no
    longer carries `BRIDGE_EMAIL_BUCKET/MAILBOX/TOKEN_SECRET` (dead config there); the
    board service carries them. Per-SA bucket/secret grants unchanged.
  - **Root check** warns (`check "email_ingress_has_a_drain"`) on `enable_email_ingress`
    without `enable_rfp_board`.
  - **`scripts/deploy.sh`** enables `cloudscheduler.googleapis.com`.

**Why a separate host, not mounted on the A2A runtime:**

The A2A edge is Gateway-public; the RFP read API, drain endpoint, and outcome markers
(sender / subject / RFP / carrier) are internal-zone per `design/zones.md`. The board
host is a distinct internal-only Cloud Run service on the same image, isolating the
internal RFP surface from the public edge.

**Why Cloud Scheduler, not GCS-finalize push:**

A GCS-finalize/Eventarc push fires per object (including the processor's own attachment
and marker writes) and would race the same key across instances. A Scheduler tick drains
the queue in landing order — the landing bucket IS the queue. `BRIDGE_EMAIL_DRAIN_INTERVAL=0`
on the board service means the drain runs inside the Scheduler's request, not a
background loop (Cloud Run request-based CPU starves background loops between requests).

**Gotchas:**

- **Deterministic board URL for the Scheduler**: Uses the Cloud Run URL formula
  `https://<name>-<project-number>.<region>.run.app` (known at plan time from
  `data.google_project.this.number`) rather than `google_cloud_run_v2_service.rfp_board[0].uri`
  (unknown under `mock_provider`, would fail tftest).
- **Mount at root + one loop**: `app.mount("", dashboard)` keeps `/rfps`, `/email/drain`
  at the root. Starlette never runs a mounted sub-app's lifespan, so the drain loop +
  store close live on the outer app (one loop per process).
- **Scheduler vs internal-only ingress**: Cloud Scheduler in the same project counts as
  internal for Cloud Run ingress. If a live deploy shows Scheduler 403/404, set
  `rfp_board_ingress = "INGRESS_TRAFFIC_ALL"` — IAM still gates.
- **Interval 0 on Cloud Run**: Request-based CPU allocation starves a background loop
  between requests, so the deployed board host sets `BRIDGE_EMAIL_DRAIN_INTERVAL=0` and
  the drain runs inside the Scheduler's POST.
- **`RfpBoard` CAS versions are thread-local**: The processor's correlate read and phase-3
  fresh read both run on the event-loop thread — the retry loop is synchronous (no
  `await` between read and put).

**Deviations from the plan:**

- Dropped the planned `email=` kwarg on `build_rfp_board_app` — an injected processor's
  `.board` could never be the host's internal board (would fail
  `build_dashboard_app`'s injection guard). Tests inject `extraction=` and env instead.
- Added a root `rfp_board_ingress` variable alongside the module var so the ingress
  fallback is operable from tfvars.
- `terraform/tests/email.tftest.hcl` now sets `enable_rfp_board` + `rfp_process` and
  asserts the token `secret_key_ref` and `BRIDGE_EMAIL_*` set on `rfp_board[0]` with NO
  `BRIDGE_EMAIL_*` on the A2A runtime.

**Not included (done in S0-arch-19, see above):**

S0-arch-17 left two gaps. First, the deployed RFP-open path: nothing turned the broker
agent's `CollectRequest` into `open_rfp` on the durable board. Second, the board host's
chase route lacked the a2a_client gcp env. Still residual: the `SqlExchangeStore` sync
facade blocks the loop for one round-trip per board call on the board host (S0-arch-14
residual).

**Verification:**

- T1: `uv run ruff check` clean, `uv run pytest -q` → 1699 passed / 97 skipped
  (baseline 1684 / 97: +14 in `tests/test_rfp_board_host.py`, +1 fresh-import param; no
  new skips). Terraform `fmt`, `validate`, `test` → 39 passed (+8 in
  `tests/rfp_board.tftest.hcl`). Fresh-process imports of `bridge.dashboard.board_host`,
  `bridge.edge.email_ingress`, `bridge.dashboard`, `bridge.demos.rfp_demo`,
  `bridge.portal.server` succeed.
- T2 (real `uvicorn` on `build_rfp_board_app(extraction=FixtureExtractor(...))`, sqlite
  DSN, `BRIDGE_EMAIL_DRAIN_INTERVAL=0`): separate process opened acme-vb-2027 onto
  `SqlExchangeStore`; host `GET /rfps/acme-vb-2027` 200 with carrier reply addresses;
  `land reply.eml` → `POST /email/drain` → one `filed` outcome (carrier-c CI received,
  HI incomplete, xlsx unsupported); clean shutdown, cross-process durability verified
  (restarted host + fresh board over DSN show CI received); refusals loud (unset
  `BRIDGE_EXCHANGE_DB_URL` / `BRIDGE_RFP_PROCESS` → `RuntimeError` naming env);
  `terraform test -filter=tests/rfp_board.tftest.hcl` 8/8 (Scheduler POSTs to board URL
  with OIDC token, retry 0, A2A runtime carries no `BRIDGE_EMAIL_*`); no duplicate
  version on re-drain; CAS conflict retried keeping both writes; exhausted retry → 409 +
  mail pending.
