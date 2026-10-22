# GCP Deployment

Feature area for **deploying the Bridge to GCP** — Terraform infrastructure-as-code
by substrate primitive, GCP seam adapters for every managed-service boundary, and
deployed-path parity so the Address golden runs reproduce the same terminal outcomes
on the provisioned substrate, not just local.

Completed-work archive for this feature area (`docs/features/`). This holds the
implementation **substance** and rationale (with architectural decisions logged in `DECISIONS.md`).
When `/implement` finishes a task, it appends a completed entry below.

**Requirements (THE WHAT) live in `spec/` and `design/`, already authored — do not duplicate here:**
- `design/gcp-substrate.md` — the GCP substrate architecture, substrate-by-primitive
  module layout, runtime = Cloud Run v2, managed services for each seam.
- `design/zones.md` — two-zone network model (external/DMZ + internal), trust boundary,
  isolation, Gateway scoping.
- `design/seams.md` — every managed-service boundary is a seam with local + GCP adapters
  and shared parametrized parity tests.

**Phase 7 breakdown (P7-1…9):**
- P7-1: Terraform scaffold (network, iam, runtime) + credential-free plan-time tests
- P7-2: Cloud SQL database module + shared-store seam adapters (sessions, task_store,
  exchange_store) with cross-replica parity
- P7-3: GCS artifacts + skill_registry, signed URLs, retention
- P7-4: Cloud Tasks module + OIDC callback auth
- P7-5: Gateway live module + Agent-Identity transport
- P7-6: Deployed extraction adapter (Gemini engine behind the seam)
- P7-7: Frontend deploy module
- P7-8: Service-wiring guard test (no hardcoded in-memory services in `bridge/`) — entry in `docs/features/production-readiness.md`
- P7-9: Deployed-path parity gate (live provisioned project)

---

## P7-1: Terraform scaffold by primitive + credential-free plan-time tests

**Completed:** 2026-09-07

Built the foundational Terraform infrastructure: three primitive modules (network, iam,
runtime) and the root module wiring that later P7 tasks extend. Validated at plan time
with **no GCP credentials** using `mock_provider` — the modules are tested and versioned
before any live provisioning.

**What was built:**

- **Root module** (`terraform/`): `versions.tf` (Terraform >= 1.7, google/google-beta
  providers >= 6.0), `variables.tf` (project_id, region, service_name, container_image,
  enable_gateway, labels), `main.tf` (provider config + three module calls with documented
  extension seams for P7-2…7 primitives), `outputs.tf` (network/SA/runtime references),
  `terraform.tfvars.example` (non-secret sample values).

- **`modules/network/`**: Custom-mode VPC with **two-zone model** (`design/zones.md`) —
  external subnet (DMZ/ingress, 10.10.0.0/24) and internal subnet (trusted, 10.20.0.0/24),
  both with private-IP Google access. Firewall isolation rule enforces the zone boundary.
  Outputs network/subnet self-links for later modules to reference (P7-2 uses
  `internal_subnet_self_link` for Cloud SQL private-IP PSA).

- **`modules/iam/`**: `bridge-runtime` service account with base project grants
  (`roles/cloudsql.client`, `roles/cloudtasks.enqueuer`, `roles/aiplatform.user`) plus
  `roles/iam.serviceAccountTokenCreator` scoped **to the SA itself** (for Cloud Tasks OIDC
  callbacks). `extra_roles` variable allows later primitives to extend grants without
  editing the module. Outputs SA email/id/member for runtime and tasks modules to consume.

- **`modules/runtime/`**: Cloud Run v2 service skeleton — **hosts the ADK App** (FastAPI +
  a2a-sdk edge + A2UI portal), does NOT wrap Vertex Agent Engine (Agent Engine only appears
  behind the Sessions seam, P7-6). Template runs as the bridge-runtime SA, uses placeholder
  container image (real image wired at deploy/P7-9). **Direct VPC egress** into the internal
  subnet (`egress = "PRIVATE_RANGES_ONLY"`) for the private-IP Cloud SQL path (P7-2) —
  datastore traffic stays on VPC, never forced onto an HTTP hop (avoiding the Gateway
  HTTP-only egress trap). **Computed ingress** from `enable_gateway` variable:
  `INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER` when true (production, behind Gateway P7-5),
  `INGRESS_TRAFFIC_ALL` when false (local/dev default).

- **Test harness** (`terraform/tests/*.tftest.hcl`): `mock_provider "google"` +
  `mock_provider "google-beta"` with `command = plan` run blocks. Assertions on
  input-derived values (CIDRs, ingress mode, SA grants, resource counts) — not
  provider-computed IDs. Per-module tests via `module { source = "./modules/..." }` override
  plus a root integration test. The `enable_gateway` branch is tested both ways (two runtime
  run blocks asserting the computed ingress). **No credentials required** — validated
  `terraform test` runs green with no `GOOGLE_APPLICATION_CREDENTIALS` or `gcloud auth`.

- **.gitignore updates**: Added `terraform/**/.terraform/`, `*.tfstate*`, `crash.log` to
  keep ephemeral state/plugins out of git. Deleted `terraform/.gitkeep` (dir now has real
  content).

**Why substrate-by-primitive:**

Module boundaries are stable **infrastructure primitives** (network, iam, runtime,
database, tasks, secrets, gateway, frontend) — **not** one module per platform service.
The Bridge's platform services (Agent Runtime, Sessions/Memory Bank, Skill Registry,
Gateway, Identity, Extraction) are realized through the **seams' GCP adapters** (config +
IAM + client code in `bridge/seams/gcp/`), not as same-named Terraform modules. This keeps
the IaC layer stable across substrate swaps and allows the six later primitives (P7-2…7)
to attach as small deltas to the scaffold without restructuring.

**Why credential-free plan-time tests:**

The `mock_provider` + `command = plan` harness is the P7 plan-time exit criterion. Every
module is validated without authenticating to GCP — developers can iterate on Terraform
HCL without GCP project access, CI can run `terraform test` in any environment, and the
planned resource shape is locked in before any live apply. Assertions target statically-
known values (CIDRs, roles, ingress rules) so the tests state real intent and remain
deterministic. Live provisioning happens only at P7-9 (deployed-path parity gate).

**Gotchas:**

- **Token-creator scope**: `roles/iam.serviceAccountTokenCreator` is granted on the
  **SA resource itself** via `google_service_account_iam_member`, not as a project-level
  `google_project_iam_member`. This scopes the grant so Cloud Tasks can mint OIDC tokens
  as bridge-runtime but nothing else can.

- **Assert via module outputs in tests**: Expose `computed_ingress` (the ternary value) as
  a runtime module output so plan-time tests can assert the `enable_gateway` branch without
  an apply. Asserting the resource's `ingress` attribute directly also works, but the output
  makes the branch explicit and stable.

- **No live apply**: Everything is plan/test only in P7-1. `terraform apply` and real
  project provisioning happen at P7-9.

**Acceptance verified:**

1. `terraform fmt -check -recursive` clean (exit 0)
2. `terraform init -backend=false` succeeds (no GCP auth, downloads providers)
3. `terraform validate` passes
4. `terraform test` green with **NO credentials configured** (all run blocks pass)
5. Negative check confirms creds not needed (still exit 0 with credentials explicitly unset)
6. No live resources created (mock_provider only, no apply)

All six criteria passed. The foundation is ready for P7-2 (database + shared-store
adapters) through P7-7 (frontend deploy) to attach.

---

## P7-2: Cloud SQL shared stores + cross-replica parity (sessions, task_store, exchange_store)

**Completed:** 2026-09-07

Built the shared-datastore GCP seam adapters and Terraform infrastructure to avoid the
**session-affinity trap** — the production bug from `vibeflix-audit` where a per-replica
in-memory task store caused an 86.8% poll-404 rate on multi-replica deployments (write
lands on replica A, poll hits replica B, session not found). Our GCP adapters back onto
**Cloud SQL PostgreSQL 16** so cross-replica read-after-write holds.

**What was built:**

- **Terraform `modules/database/`**: Cloud SQL PostgreSQL 16 instance on **private IP only**
  (`ipv4_enabled = false`, `private_network = <vpc>`) to enforce the Gateway/Identity
  hardening invariant — the Bridge→Cloud SQL connection is private-IP over the VPC via
  Direct VPC egress, never forced onto an HTTP hop (avoiding the Gateway HTTP-only egress
  trap). Includes Private Services Access (PSA) reserved range + service-networking
  connection for the VPC peering, a `bridge` database, and a `bridge` SQL user. **No
  password in HCL** — the credential is injected out-of-band at deploy (P7-9), never
  committed in plaintext.

- **Terraform `modules/secrets/`**: Secret Manager container for the DB DSN
  (`bridge-database-url`) with `secretAccessor` IAM binding to the `bridge-runtime` SA.
  **No secret VERSION in HCL** (no plaintext DSN) — the actual value is injected by the
  deploy step (P7-9). Only the secret container and IAM binding are in Terraform.

- **Runtime secret-env injection** (`modules/runtime/`): Added optional `db_url_secret_id`
  variable and a dynamic `DATABASE_URL` env block injecting from
  `value_source.secret_key_ref` (version `"latest"`) when the secret ID is set. The DSN
  reaches the app ONLY via this secret env, preserving the private-IP path.

- **`bridge/seams/gcp/` package**: GCP seam adapters for `sessions`, `task_store`,
  `exchange_store` — the same ADK/a2a-sdk Database\* classes the local adapter builds
  (`DatabaseSessionService`, `DatabaseTaskStore`, `DatabasePushNotificationConfigStore`)
  over a **Postgres DSN** from the `DATABASE_URL` secret env instead of an in-memory or
  sqlite store. Key invariants enforced:
  - **No in-memory fallback.** The GCP adapter refuses to build when no DSN is configured
    and raises a clear error mentioning the session-affinity trap — an in-memory shared
    store on multi-replica is exactly the bug this task prevents.
  - **Async Postgres driver required.** The adapter validates the DSN is
    `postgresql+asyncpg://...` and rejects sync or non-Postgres DSNs with a clear message
    (mirrors the local adapter's `require_async_driver` style).

- **Terminal-state write retry + loud logging**: `_TerminalRetryTaskStore` wrapper
  delegates to the inner `DatabaseTaskStore` and, when a task's status is terminal
  (completed/failed/canceled/rejected per a2a-sdk's `TERMINAL_TASK_STATES`), retries
  `save()` on exception with bounded attempts (default 3) and loud logging (WARNING on
  each retry, ERROR if all attempts fail then re-raise). Non-terminal saves pass straight
  through. Rationale: a lost terminal write hangs the poller to its deadline, so it fails
  far louder than a dropped intermediate `working` write.

- **Cross-replica read-after-write parity test**: The load-bearing case that proves the
  shared-store property — write a terminal task via one `build_task_store` handle, read it
  back via a second independent handle to the same backing store, with **no dispose
  between** (two concurrent engines/pools = the two-replica shape). For `local` this uses
  a `tmp_path` file sqlite DSN (always runs, single-process but proves shared-store). For
  `gcp` it uses the Postgres DSN (skipped unless `BRIDGE_TEST_GCP=1` + a provisioned DSN).
  This is a named P7-9 exit-gate criterion.

- **Updated seam registry** (`bridge/seams/registry.py`): `get_adapter(seam, SeamMode.gcp)`
  dispatches to `GCP_ADAPTERS[seam]()` for the three implemented seams; other seams still
  raise `NotImplementedError(f"gcp adapter for {seam.value} lands at P7")` (a2a_client,
  scheduler, skill_registry, extraction deferred to P7-3/4/5/6).

- **Plan-time tests** (`terraform/tests/database.tftest.hcl`,
  `terraform/tests/secrets.tftest.hcl`): Assert `POSTGRES_16`, `ipv4_enabled == false`,
  `private_network` set, PSA `purpose == "VPC_PEERING"`, `secretAccessor` role, and
  crucially **no `google_secret_manager_secret_version` resource** (the structural guard
  that no secret value lives in HCL). All tests run credential-free via `mock_provider`.

- **`pyproject.toml`**: Added optional `gcp` extra with `asyncpg>=0.29` (async Postgres
  driver). Importing `bridge.seams.gcp` does NOT pull asyncpg at module load — lazy
  imports inside methods preserve the default (non-GCP) suite running without it.

**Why shared stores, not per-replica in-memory:**

The A2A edge is `POST message:send` (creates task) then poll `GET tasks/{id}` for
completion, and on a multi-replica substrate without session affinity the two requests
can land on different replicas. With `InMemoryTaskStore`, replica A holds the task,
replica B returns 404, the polling loop fails. The `vibeflix-audit` reference measured
86.8% poll-404 rate and a full-run speedup from ~5m to ~1m44s after moving to a single
shared Cloud SQL store. Our design already specifies shared stores
(`design/seams.md`), but this task delivers the implementation and proves it via the
cross-replica parity test.

**Why Cloud SQL on private IP only:**

The Gateway HTTP-only egress governance (the trap from `vibeflix-audit`) forced their
shared task store over HTTPS through a single-instance app. We avoid the trap by keeping
the Bridge→Cloud SQL path on **private IP over the VPC** (Direct VPC egress into the
internal subnet, `egress = "PRIVATE_RANGES_ONLY"` from P7-1) — datastore traffic never
touches an HTTP hop. The `database` module enforces `ipv4_enabled = false` +
`private_network = <vpc>`, so there is no public IP.

**Gotchas:**

- **Same ADK/a2a class, different DSN.** The GCP adapters do NOT invent new store
  implementations — they build the identical `DatabaseSessionService` /
  `DatabaseTaskStore` / `DatabasePushNotificationConfigStore` classes the local adapter
  builds. The parity is "same class, Postgres DSN instead of sqlite DSN" + the
  terminal-retry wrapper. This is what makes the mock→real swap transparent to the agent.

- **The cross-replica parity test is terminal-outcome, never ledger-identical.** Seam
  parity is defined as terminal-outcome (same task reaches the same end state), not
  ledger-identical (every write produces byte-identical rows). Two independent engines
  may differ in connection-pool state, timestamps, retry counts — the shared-store
  assertion is that a write via handle A is **visible** to handle B, not that the
  internal ledger is bit-for-bit identical.

- **`ExchangeStoreSeam` stores materialized Exchange records (S0-arch-14).**
  `GcpExchangeStore.build_exchange_store()` returns the same `SqlExchangeStore` the local
  adapter builds, over the same `DATABASE_URL` (its own table `bridge_exchange_records`):
  versioned JSON records keyed by `(kind, ref)` with compare-and-set writes, today the RFP
  board. It refuses an in-memory fallback and a sync driver like the other shared stores;
  `tests/test_exchange_store_seam.py` carries the parity + cross-replica cases.

- **Terminal-retry wrapper is unit-testable without GCP.** The retry behavior is proven
  via a fake inner store (raises once then succeeds → assert WARNING logged + eventual
  success; always raises → assert ERROR logged + exception re-raised; non-terminal failure
  → no retry). This runs on every test run (no GCP credentials needed).

- **No secrets in HCL (CRITICAL path).** The `secrets` module creates the secret
  container only — no `google_secret_manager_secret_version` resource. The DSN value is
  injected out-of-band at deploy (P7-9). The `database` module omits the DB user password
  (IAM auth or a sensitive deploy-time variable). No plaintext credential in any `.tf` /
  `.tfvars`.

**Acceptance verified:**

1. `uv run ruff check` clean
2. Full suite green (`uv run pytest` all pass)
3. Cross-replica read-after-write (local) passes — write via handle A to a file DSN, read
   via independent handle B (the session-affinity guard); gcp param skipped (no
   `BRIDGE_TEST_GCP`)
4. Terminal-retry wrapper unit test passes (success-after-one-failure with WARNING logged,
   exhausted-retries with ERROR + raise, non-terminal-no-retry)
5. Registry dispatch correct — `Seam.sessions`, `task_store`, `exchange_store` return
   `GcpSessions`, `GcpTaskStore`, `GcpExchangeStore`; unimplemented seams raise
6. GCP adapter refuses in-memory (the trap guard raises when no DSN configured)
7. GCP adapter rejects sync/non-async Postgres DSN (clear async-driver error)
8. Isolation guard green (gcp pkg imports no `agents/`)
9. Terraform plan-time credential-free — `fmt`, `init`, `validate`, `test` all exit 0
   with no GCP auth
10. Deferred to P7-9: with `BRIDGE_TEST_GCP=1` + provisioned Cloud SQL, the gcp params
    of cross-replica + roundtrip cases pass against the real shared store

All criteria passed. The shared-store adapters are ready; they execute end-to-end only
at P7-9 against a provisioned Cloud SQL instance.

---

## P7-3: GCS artifacts + skill_registry, signed URLs, retention

**Completed:** 2026-09-07

Built the GCS-backed artifact storage and skill registry with time-limited signed URLs
for secure artifact delivery, plus object-lifecycle retention policies that keep
artifact GC off the Bridge runtime. Two backends implemented: the native ADK
`GcsArtifactService` swap and the `skill_registry` GCP seam adapter.

**What was built:**

- **Terraform `modules/gcs/`**: Two Cloud Storage buckets (skills registry + artifacts)
  on **private-only access** with V4 signed URLs as the sole external byte path.
  `uniform_bucket_level_access = true` + `public_access_prevention = "enforced"` (never
  public) on both. **Object versioning enabled** (`versioning.enabled = true`) —
  artifact resubmission history (ART-1) and skill re-authoring both create new versions.
  **Lifecycle Delete rule** on the artifacts bucket (`lifecycle_rule` with `age =
  var.retention_days`, default 365) — the post-exchange retention window (ART-3) is
  enforced by GCS object lifecycle, NOT Bridge-side GC code.

- **IAM (the only new grant in this task)**: `google_storage_bucket_iam_member` granting
  `roles/storage.objectAdmin` (write for `install_skill` + `save_artifact`; read for
  fetch) to `var.service_account_member` — **scoped per-bucket**, NOT project-level
  (least-privilege, mirrors the secrets module). Signing uses IAM SignBlob via the SA's
  **existing** self-scoped `roles/iam.serviceAccountTokenCreator` (P7-1 iam module), so
  no new IAM for signing.

- **Runtime bucket-name envs** (`modules/runtime/`): Two plain envs (bucket names are
  not secrets) `BRIDGE_SKILLS_BUCKET` and `BRIDGE_ARTIFACT_BUCKET`, each gated with a
  `for_each = var.<x>_bucket != "" ? [1] : []` dynamic block (mirroring the `db_url_secret_id`
  idiom) so the P7-1 no-bucket default keeps runtime tests green.

- **`GcsArtifactService` + signed-URL fetch endpoint** (`bridge/edge/artifacts.py`):
  `build_artifact_store()` factory returns `GcsArtifactService(bucket_name)` when
  `BRIDGE_ARTIFACT_BUCKET` env is set (lazy `from google.adk.artifacts import GcsArtifactService`),
  else `InMemoryArtifactService()`. Extended `build_fetch_app(..., signer=None)`: when
  `signer` is provided the handler **authorizes on the leg boundary first** (probes
  `_load_any_party` to confirm the blob exists and discover its owning `user_id`; **404 if
  absent**), then returns `RedirectResponse(signer(...), status_code=307)` to the
  time-limited signed URL — **bytes never ride the response** (the client fetches directly
  from GCS). This preserves "a reference is not a capability" (`design/artifacts.md`) —
  guessed paths cannot mint signed URLs. When `signer is None` (local), the handler
  streams bytes exactly as before.

- **`skill_registry` GCS adapter** (`bridge/seams/gcp/__init__.py`): `GcpSkillRegistry`
  implements the seam by **download-to-temp + reuse `skill_meta_from_dir`** (same format,
  different store — mirrors P7-2's "same class, different backend" pattern). Bucket name
  from arg or `BRIDGE_SKILLS_BUCKET` env; refuses (RuntimeError) if neither set (no
  in-memory fallback). Objects laid out `<name>/SKILL.md`, `<name>/assets/<file>` —
  identical to the local dir format. `list_skills()` downloads each skill prefix to a
  tempfile and calls `skill_meta_from_dir`; `install_skill` **reuses the S0-sec-1
  traversal defense** (name `^[a-z0-9-]+$`, every asset must not escape `<name>/`)
  before uploading. Added to `GCP_ADAPTERS[Seam.skill_registry]`; the seam parity test
  no longer raises `NotImplementedError`.

- **`pyproject.toml` gcp extra**: Added `google-cloud-storage>=2.18` to the `gcp = [...]`
  optional dependency group (needed by both the skill_registry adapter and the
  `GcsArtifactService`/signer). The default (non-gcp) suite runs without it — lazy
  imports inside methods preserve this.

- **Plan-time tests** (`terraform/tests/gcs.tftest.hcl`): `mock_provider` +
  `command = plan`. Asserts both buckets have `uniform_bucket_level_access == true` +
  `public_access_prevention == "ENFORCED"` (never public — signed URLs are the only
  external reach); both have `versioning[0].enabled == true`; artifacts bucket declares a
  `lifecycle_rule` with a Delete action (ART-3 retention exists); IAM grant role ==
  `roles/storage.objectAdmin` scoped per-bucket. All credential-free.

**Why GcsArtifactService is NOT a seam:**

The artifact backend is a **native ADK swap** (`InMemoryArtifactService` ↔ `GcsArtifactService`),
exactly like Sessions swaps `InMemory…` ↔ `VertexAISessionService`. The seam-in-waiting
lives in `bridge/edge/artifacts.py` (the `build_artifact_store()` factory) and is wired
in `server.py`/`executor.py` via the `artifact_store or build_artifact_store()` idiom
(injectable param wins). There is **no** `@pytest.mark.seam` artifact parity test and no
`Seam.artifact` enum entry — the swap point is a documented factory + the P7-8 wiring
guard, not a seam adapter.

**Why buckets are never public:**

`public_access_prevention = "enforced"` + uniform bucket-level access (UBLA) on both
buckets. Signed URLs are V4, time-limited (default expiration controlled by `gcs_signer`);
they are the **only** external byte path. This is a load-bearing security invariant, not
a style choice — asserted in the tftest.

**Why versioning + lifecycle, not Bridge-side GC:**

Object versioning (`versioning.enabled = true`) means a resubmission creates a new version
(ART-1 resubmission history). The GCS object-lifecycle Delete rule (artifacts bucket,
`age = retention_days`) implements ART-3 (retain during the exchange, delete only after
the post-exchange window). The Bridge keeps **ZERO** artifact GC/TTL code
(`bridge/edge/artifacts.py` docstring) — the ONLY Bridge-side deletion path is the
off-script explicit erase (`bridge/offscript.py::handle_deletion_request`, a user action,
unchanged). Lifecycle is the substrate primitive; do not add Bridge-side GC.

**Gotchas:**

- **Authorize before you sign.** The signed-URL handler MUST 404 on a foreign/unknown
  (context_id, filename) **before** minting a URL — otherwise a guessed path becomes a
  capability. Reuse `_load_any_party` for the existence+owner check, then sign that
  specific blob. This preserves "a reference is not a capability" (`design/artifacts.md`).

- **Same format, different store.** `GcpSkillRegistry` downloads to temp and reuses
  `skill_meta_from_dir` — does NOT re-implement frontmatter parsing. The format is the
  seam contract (`design/seams.md`); that one parser gates both sides.

- **Lazy imports everywhere.** Importing `bridge.seams.gcp` or `bridge.edge.artifacts`
  must NOT pull `google.cloud.storage`. Lazy-import `from google.cloud.storage import
  Client` and `from bridge.edge.skills import skill_meta_from_dir` inside methods only.
  Mirrors P7-2's asyncpg discipline. Tested by structural unit test (`sys.modules`
  assertion).

- **No secrets/values in HCL.** Bucket names are fine in outputs/envs (not secrets), but
  no bucket **objects**, no keys, no plaintext credentials. The plan-time test needs no
  GCP auth. Runtime bucket-name envs are PLAIN `env { name/value }` (not `secret_key_ref`,
  unlike the DB DSN — bucket names are not sensitive).

- **P7-8 wiring-guard compatibility.** The `artifact_store or build_artifact_store()`
  injection seam (param wins) is preserved. `build_artifact_store()` may construct
  `InMemoryArtifactService` internally — that's the reachable-via-`or` fallback the guard
  permits; the injectable seam is the point.

**Acceptance verified:**

1. `uv run pytest` full suite green (no regressions; local artifact/skill_registry parity
   still passes)
2. `uv run ruff check` clean
3. Isolation guard green (`bridge/` never imports `agents/`)
4. Terraform plan-time credential-free — `fmt`, `init`, `validate`, `test` all exit 0 with
   no GCP auth
5. `skill_registry` gcp adapter wired (`get_adapter(Seam.skill_registry, SeamMode.gcp)` →
   `GcpSkillRegistry`, no longer raises)
6. Refuse-without-bucket loud (`BRIDGE_SKILLS_BUCKET` unset → `RuntimeError` mentioning
   the bucket env)
7. Traversal defense before I/O (`install_skill("../evil", ...)` raises `ValueError` before
   any network)
8. Lazy imports verified (`import bridge.seams.gcp` → `google.cloud.storage` not in
   `sys.modules`)
9. Signed-URL endpoint unit tests green (307 redirect to fake signed URL when signer
   provided + blob exists; 404 on missing blob; no-signer path still streams bytes)
10. Store factory swaps on env (monkeypatch `BRIDGE_ARTIFACT_BUCKET` → `GcsArtifactService`,
    unset → `InMemoryArtifactService`)

Live GCS roundtrip (real bucket + `BRIDGE_TEST_GCP=1`) deferred to P7-9 (deployed-path
parity gate). No live apply in this task.

---

## P7-4: Cloud Tasks scheduler + OIDC callback auth (follow-up arming)

**Completed:** 2026-09-07

Built the GCP scheduler adapter backed by Cloud Tasks for clock-driven follow-up delivery,
plus the Terraform queue module and OIDC callback authentication. This delivers the
**arming half** of the follow-up ladder — the adapter mints correctly-scheduled,
OIDC-authenticated Cloud Tasks that POST to the runtime's callback URL; the **receiving
half** (the endpoint that drives `FollowupLadder.on_fired` → re-arm) is deferred to P7-9
with the deployed-path parity gate.

**What was built:**

- **`GcpScheduler` adapter** (`bridge/seams/gcp/__init__.py`): Implements `SchedulerSeam`
  backed by Cloud Tasks instead of the local `VirtualClock`. Key design:
  - **Poll→push impedance match**: Cloud Tasks is wall-clock/push-driven (fires by POSTing
    an OIDC callback at `schedule_time`), while the local adapter is poll-driven
    (test/timewarp calls `advance()` to fast-forward). Both satisfy the `Scheduler`
    protocol, but `advance()` / `advance_to()` **raise `NotImplementedError`** on GCP with
    a clear message — Cloud Tasks IS the clock; virtual time-warp has no meaning against a
    real queue.
  - **`now() -> int`**: Returns wall-clock epoch seconds (`int(time.time())`), while local
    returns a virtual counter. Both are ints; the follow-up ladder only compares/orders
    them.
  - **`schedule(key, fire_at, payload) -> Alarm`**: Creates a Cloud Task with
    `schedule_time` = `fire_at` (absolute epoch seconds → protobuf `Timestamp`), HTTP
    target = configured callback URL, method POST, body = compact JSON
    `{"key": key, "payload": payload}`, and **OIDC token** =
    `OidcToken(service_account_email=<sa email>, audience=<callback URL>)`. This OIDC auth
    is what the P7-1 SA self-scoped `serviceAccountTokenCreator` grant enables.
  - **Idempotent re-arm**: Derives a deterministic Cloud Tasks task **name** from `key`
    (sanitize to `[A-Za-z0-9_-]` charset), so re-arming the same key targets the same task.
    Cloud Tasks rejects duplicate names (`AlreadyExists`) and dedups recently-deleted names
    for ~1h, so re-arm = **delete then create** (crash-safe, mirrors `VirtualClock`'s
    "same key overwrites").
  - **`cancel(key) -> bool`**: Deletes by derived name; returns `True` if existed, `False`
    on `NotFound`.
  - **`pending() -> list[Alarm]`**: Lists tasks in the queue, maps each to an `Alarm`
    (best-effort; used by structural tests to observe an armed task).
  - **Refuse-without-config**: Missing queue path / callback URL / SA email raises
    `RuntimeError` naming the env — never a silent in-process fallback (an in-memory clock
    on multi-replica would silently lose alarms, the scheduler analogue of the
    session-affinity trap).
  - **Config resolution** from env: `BRIDGE_TASKS_QUEUE` (full queue resource path),
    `BRIDGE_FOLLOWUP_CALLBACK_URL`, `BRIDGE_RUNTIME_SA_EMAIL`.
  - **Injectable-seam idiom**: `build_scheduler(client=None, *, queue_path=None, ...)` —
    injected fake client wins (used by credential-free structural tests); when `client is
    None` a real `tasks_v2.CloudTasksClient` is lazily built.

- **Terraform `modules/tasks/`**: Cloud Tasks queue module with `google_cloud_tasks_queue
  "bridge_followups"` (name `bridge-followups`, location = region, explicit
  `rate_limits {}` + `retry_config {}` blocks for testable retry semantics). **No IAM in
  this module** — the enqueuer + token-creator grants live in the iam module (P7-1); a
  header comment states this (mirrors `modules/gcs/`). **No secrets/tasks in HCL** — the
  queue is a pure container; tasks are runtime data armed through the seam adapter.
  Outputs: `queue_name`, `queue_id`, constructed `queue_path`
  (`projects/<P>/locations/<L>/queues/bridge-followups`) — the value injected as
  `BRIDGE_TASKS_QUEUE`.

- **Runtime module envs** (`modules/runtime/`): Three new plain env blocks (bucket names /
  queue paths are not secrets) — `BRIDGE_TASKS_QUEUE`, `BRIDGE_RUNTIME_SA_EMAIL`,
  `BRIDGE_FOLLOWUP_CALLBACK_URL` — each gated `for_each = var.<x> != "" ? [1] : []` so the
  P7-1 no-tasks default keeps runtime tests green. **Callback-URL self-reference gotcha**:
  the callback URL is the runtime's own `service_uri`, only known post-apply — wiring
  `module.runtime.service_uri` back into `module.runtime` creates a cycle. Resolved by
  exposing a `followup_callback_url` variable (default `""`) injected at P7-9 from the
  resolved URL (mirrors the DB DSN VALUE out-of-band injection pattern).

- **Root wiring** (`terraform/main.tf` + `outputs.tf`): Added `module "tasks"` (passes
  `project_id`, `region`, `labels`), root outputs (`tasks_queue_name`, `tasks_queue_path`),
  updated "Extension seams" comment — P7-4 is now BUILT (module.tasks); the token-creator
  binding it consumes is already on the SA (module.iam).

- **`pyproject.toml` gcp extra**: Added `google-cloud-tasks>=2.16` to the `gcp = [...]`
  optional dependency (lazy-imported inside methods; default suite runs without it).

- **Structural tests** (`tests/test_gcp_scheduler.py`): Credential-free, always-run tests
  with a fake Cloud Tasks client injected via `build_scheduler(client=fake, ...)`.
  Asserts: `get_adapter(Seam.scheduler, SeamMode.gcp)` returns `GcpScheduler` (no longer
  raises); refuse-without-config loud (`RuntimeError` naming env); `schedule` mints the
  right task (OIDC token with SA email + audience == callback URL, POST to callback URL,
  JSON body, `schedule_time` ≈ `fire_at`); idempotent re-arm (same `key` → same derived
  task name, delete-then-create observed); `cancel` deletes by name and returns bool;
  `now()` is an int; `advance` / `advance_to` raise `NotImplementedError` mentioning
  wall-clock/push; lazy import (`google.cloud.tasks` not in `sys.modules`).

- **Seam parity test update** (`tests/test_scheduler_seam.py`): Flipped the gcp branch of
  `test_scheduler_local_builds_virtualclock` — previously raised `NotImplementedError`,
  now asserts `get_adapter` returns `GcpScheduler`, builds it with injected fake client +
  explicit config, asserts `now()` is int, `schedule`/`cancel` drive the fake, and
  `advance` raises. Local branch (full virtual-clock schedule/advance flow) unchanged.
  Other un-parametrized tests (`idempotent_rearm`, `cancel`, `same_tick`, `backwards`)
  already early-return on gcp — left as-is. One seam marker per seam (DECISIONS.md
  2026-09-06 S0-arch-3) — `@pytest.mark.seam("scheduler")` stays only on the one test.

- **Plan-time test** (`terraform/tests/tasks.tftest.hcl`): `mock_provider` +
  `command = plan`. Asserts queue name == `"bridge-followups"`, location tracks region, a
  `retry_config` is declared (explicit retry semantics), `queue_path` output ==
  `"projects/test-project/locations/us-central1/queues/bridge-followups"`.

- **Deferred receiver endpoint** (§1D in plan): The HTTP endpoint that receives the Cloud
  Tasks POST and re-drives the ladder (`FollowupLadder.on_fired` → re-arm next step) is
  **NOT built in this task**. Documented in `GcpScheduler` docstring. Rationale: (a) it is
  the deployed-wiring / follow-up-worker concern riding the same "LlmAgent / follow-up
  behind the edge" deferral logged in TODO.md "Deferred to later cycles" and DECISIONS.md
  2026-08-21 (S0-arch-1); (b) the live end-to-end firing is the **P7-9** deployed-path
  parity run, exactly as P7-2/P7-3 left their Cloud SQL / GCS roundtrips to P7-9. P7-4
  delivers the arming half; correctness (right queue, right OIDC token, right callback
  URL, idempotent re-arm) is proven credential-free here.

**Why poll→push divergence is honest:**

The `Scheduler` protocol has `advance(ticks)` / `advance_to(tick)` methods that fast-
forward a virtual clock — used by tests and the `bridge/timewarp/host.py` dev tool to
drive the follow-up ladder without waiting for wall-clock seconds. These methods have **no
meaning** against Cloud Tasks (a real queue fires callbacks at wall-clock `schedule_time`;
there is no "skip ahead"). Rather than fake a poll loop or a background poller, the GCP
adapter raises `NotImplementedError` with a clear message — this is the honest divergence.
Parity is **terminal-outcome** (the ladder reaches `escalated`), not ledger-identical
(both adapters manipulate the same virtual timeline). The follow-up ladder logic
(`FollowupLadder.on_fired` → stamp → re-arm) is adapter-agnostic; only *who calls
`on_fired`* differs (a test/timewarp `advance` loop locally; the OIDC callback on the
deployed path).

**Why no new IAM:**

The task title says "IAM Cloud Tasks OIDC token-creator grant on `bridge-runtime`" — that
grant **already exists** from P7-1. `terraform/modules/iam` grants the SA
`roles/cloudtasks.enqueuer` (base role) and a **self-scoped**
`roles/iam.serviceAccountTokenCreator` (SA-scoped via `google_service_account_iam_member`,
not project-level — least-privilege). P7-4 **consumes** these grants; it adds none. The
`tasks` module has a header comment stating this (mirrors the `gcs` module's IAM
disclosure). `terraform/tests/iam.tftest.hcl` already asserts both grants.

**Why callback-URL is a runtime variable:**

The follow-up callback URL is the runtime's own `service_uri`, which is only known
post-apply — wiring `module.runtime.service_uri` back into `module.runtime.env` creates a
Terraform dependency cycle. Resolved the same way the DB DSN VALUE is handled: expose a
`followup_callback_url` runtime variable (default `""`), injected as a plain env when set.
At plan time / P7-1 default it's `""` (no env, keeping runtime tftest green). The P7-9
deploy step sets it from the resolved service URL.

**Gotchas:**

- **OIDC token, not bearer token / API key.** The task is authorized by an
  `OidcToken(service_account_email, audience)` — this is what the P7-1 self-scoped
  `serviceAccountTokenCreator` grant enables. No credential/secret rides the task body
  (mirrors `test_nudge_carries_no_bearer_token` — the nudge path already forbids bearer
  tokens).

- **Idempotent re-arm via deterministic task name.** Sanitize `key` (e.g.
  `followup:<context_id>` → replace `:` and other non-`[A-Za-z0-9_-]` chars) into a valid
  Cloud Tasks task id, so re-arming the same key is delete-then-create on the same task
  (crash-safe re-arm, matching `VirtualClock`'s "same key overwrites"). Cloud Tasks
  name-dedup lasts ~1h after deletion — handle `AlreadyExists` by delete+recreate.

- **timewarp stays local-only.** `bridge/timewarp/host.py` fast-forwards a `VirtualClock`
  (dev tool, never runs against the gcp adapter). Do not try to make it advance Cloud
  Tasks — the honest divergence is documented.

- **Live firing is P7-9.** The real Cloud Tasks roundtrip (arm → the queue POSTs the OIDC
  callback → the ladder reaches `escalated` on the deployed path) runs only under
  `BRIDGE_TEST_GCP=1` against a provisioned queue + deployed callback endpoint — that is
  the P7-9 deployed-path parity gate. P7-4 proves the arming half credential-free.

- **Lazy imports everywhere.** Importing `bridge.seams.gcp` must not pull
  `google.cloud.tasks` (mirrors P7-2 asyncpg / P7-3 google-cloud-storage discipline).
  Lazy-import `from google.cloud import tasks_v2` inside methods only. Tested by
  structural unit test (`sys.modules` assertion).

- **No secrets/values in HCL.** Queue path / callback URL / SA email are plain runtime
  envs (not secrets — they're resource identifiers, not credentials). No task objects in
  HCL (tasks are runtime data armed through the adapter). Plan-time tests need no GCP
  auth.

**Acceptance verified:**

1. Full suite green (`uv run pytest` all pass)
2. `uv run ruff check` clean
3. Isolation guard green (`bridge/` never imports `agents/`)
4. Terraform plan-time credential-free — `fmt`, `init`, `validate`, `test` all exit 0 with
   no GCP auth (incl. new `tasks.tftest.hcl`)
5. `scheduler` gcp adapter wired (`get_adapter(Seam.scheduler, SeamMode.gcp)` →
   `GcpScheduler`, previously raised)
6. Refuse-without-config loud (`BRIDGE_TASKS_QUEUE` unset → `RuntimeError` mentioning env)
7. Poll→push divergence honest (`advance(1)` / `advance_to(1)` raise `NotImplementedError`
   mentioning wall-clock/push)
8. Lazy imports verified (`import bridge.seams.gcp` → `google.cloud.tasks` not in
   `sys.modules`)
9. Seam parity + structural tests green (`tests/test_scheduler_seam.py`,
   `tests/test_gcp_scheduler.py` — gcp branch returns `GcpScheduler`, mints right OIDC
   task with `schedule_time` from `fire_at`, POST to callback URL, idempotent re-arm by
   task name, cancel deletes, advance raises)
10. IAM already granted (grep confirms enqueuer + token-creator in `modules/iam`; P7-4
    diff adds no IAM resource/grant; `iam.tftest.hcl` unchanged)
11. Terraform queue module plan green (`tasks.tftest.hcl` asserts queue name, location,
    retry_config declared, queue_path output correct)
12. Local behavior untouched (`tests/test_followup.py`, `tests/test_sla.py`,
    `tests/test_outbound.py` green — follow-up ladder + SLA + outbound nudge run on
    `VirtualClock`, unchanged)

Live roundtrip (real Cloud Tasks fire + callback endpoint driving the ladder) deferred to
P7-9 (deployed-path parity gate). No live apply in this task.

---

## P7-5: Gateway ingress + Agent Identity a2a_client transport

**Completed:** 2026-09-07

Built the Gateway Terraform module for external ingress scoping, the `GcpA2AClient`
a2a_client seam adapter with Agent Identity transport, and the served-card public-URL env
to satisfy google-adk >=2.7 same-origin validation. The load-bearing finding: the 2.3-era
`.mtls` card-repoint hack from vibeflix-audit is **superseded and must NOT be ported** —
adk 2.7 now enforces same-origin validation, so the fix is served-card-origin +
refuse-loud reconciliation, not client-side repointing.

**What was built:**

- **Terraform `modules/gateway/`**: External HTTPS Load Balancer → Serverless NEG on the
  runtime service + per-party `run.invoker` scoping. All resources count/for_each-gated on
  `var.enable_gateway` (default false) so the module creates **nothing** when disabled.
  Chain: `google_compute_region_network_endpoint_group` (serverless, points to Cloud Run
  service) → `google_compute_backend_service` → `google_compute_url_map` →
  `google_compute_managed_ssl_certificate` (gated on `length(ssl_domains) > 0`) →
  `google_compute_target_https_proxy` → `google_compute_global_address` (LB anycast IP) →
  `google_compute_global_forwarding_rule` (port 443). Per-party scoping:
  `google_cloud_run_v2_service_iam_member` for each `var.party_invoker_members`, role
  `roles/run.invoker` (empty set at plan time; populated at deploy when per-party SAs
  exist). **Header comment (load-bearing):** states the module governs **INGRESS ONLY** —
  external HTTPS LB → runtime. No egress rule, no VPC connector, no datastore path.
  Bridge→Cloud SQL stays the runtime module's private-IP Direct VPC egress
  (`PRIVATE_RANGES_ONLY`), never an HTTP hop (sidesteps the Gateway HTTP-only egress trap
  structurally).

- **Root wiring** (`terraform/main.tf`): `module "gateway"` wired after `module "runtime"`,
  passes `service_name = module.runtime.service_name`, `enable_gateway`,
  `party_invoker_members`, `ssl_domains`, `labels`. Outputs: `gateway_ip` (the LB anycast
  IP, `""` when disabled), `gateway_url` (the authorized public HTTPS URL derived from first
  `ssl_domains` entry, `""`). Added `party_invoker_members` (list(string), default `[]`)
  and `ssl_domains` (list(string), default `[]`) to root variables. Updated "P7-5 gateway"
  extension-seam comment from future tense to a BUILT note (mirroring the P7-3/P7-4 style).

- **`GcpA2AClient` adapter** (`bridge/seams/gcp/__init__.py`): Implements `A2AClientSeam`
  backed by an **Agent-Identity transport** over the existing `OutboundClient` (no
  reimplementation of idempotency / crash-safe re-drive; only the transport differs).
  - **Agent Identity = per-call OIDC ID token**, never a message bearer field. The transport
    mints a workload OIDC ID token with `audience == reconciled URL` via
    `google.oauth2.id_token.fetch_id_token` and POSTs it as an `Authorization: Bearer`
    header. No `Nudge`, `DialOut`, or `Delivery` dataclass carries a token/credential field
    — asserted by `test_outbound_carries_no_bearer_token_field`.
  - **Host reconciliation (refuse-loud).** New pure-function helper
    `reconcile_card_host(advertised_url, gateway_host)` parses the advertised URL and
    compares hostname (and port if present) to `gateway_host`. Match → returns
    `advertised_url` unchanged. Mismatch → raises `ValueError` naming both hosts (never a
    silent repoint). This is the explicit reconciliation that the adk 2.7 same-origin
    validation requires: the consumer side (`RemoteA2aAgent`) validates the served card's
    URL origin; the outbound side (our `GcpA2AClient`) reconciles before sending.
  - **Injectable-seam idiom**: `build_client(*, gateway_host, sa_email, id_token_fetcher,
    sender)` — the ID-token fetcher and httpx sender are both **injectable** so the unit +
    parity tests run credential-free; the real fetcher/sender path is exercised only under
    `BRIDGE_TEST_GCP` (a P7-9 input).
  - **Refuse-without-config.** Resolves `gateway_host` and `sa_email` from env
    (`BRIDGE_GATEWAY_HOST`, `BRIDGE_RUNTIME_SA_EMAIL`), raising `RuntimeError` naming the
    missing env — no silent fake-transport fallback that would swallow sends on the
    deployed path.
  - **Config envs** (module-level constants): `BRIDGE_GATEWAY_HOST_ENV` (the
    Gateway-authorized host, injected by the runtime module), reuses existing
    `BRIDGE_RUNTIME_SA_EMAIL_ENV` (the same SA mints tokens for scheduler + a2a_client).
  - **Lazy imports**: `google.oauth2`, `google.auth.transport.requests`, and `httpx` are
    imported inside methods only. Importing `bridge.seams.gcp` pulls neither (mirrors P7-2
    asyncpg / P7-3 gcs / P7-4 tasks lazy-import discipline).

- **Served-card public-URL env** (`bridge/edge/card.py`, `bridge/edge/server.py`): New env
  constant `PUBLIC_URL_ENV = "BRIDGE_PUBLIC_URL"` and helper
  `resolved_card_url(explicit: str | None) -> str` (honors explicit; else env; else
  `DEFAULT_URL` for local/test). Threaded through `build_a2a_app`: when the caller does not
  pass an explicit `url`, resolve via `resolved_card_url()` so a deployed container with
  `BRIDGE_PUBLIC_URL` set advertises the Gateway URL in both the card `url` and the
  executor `base_url`. Local/test default (`DEFAULT_URL = "http://localhost:8000/"`)
  unchanged. This is the consumer-side half of the same-origin fix — the served card must
  advertise the same origin that a consumer fetches it from, else the consumer's
  `RemoteA2aAgent` raises `AgentCardResolutionError` under adk 2.7.

- **Runtime module envs** (`terraform/modules/runtime/main.tf`): Added gated
  `BRIDGE_GATEWAY_HOST` and `BRIDGE_PUBLIC_URL` plain-env blocks (bucket names / hosts are
  not secrets) with optional `gateway_host` and `public_url` vars (defaults `""`). Each env
  block `for_each = var.<x> != "" ? [1] : []` so the P7-1 no-gateway default leaves the
  container env untouched (runtime tftest stays green). The actual values are injected
  out-of-band at P7-9 (may not be known at plan time).

- **Seam registry** (`bridge/seams/registry.py`): Updated docstring to move `a2a_client`
  from "still raises" to "built (P7-5)"; only `extraction` (P7-6) and `memory_bank` remain
  raising.

- **Parity tests** (`tests/test_a2a_client_seam.py`, `tests/test_gcp_a2a_client.py`):
  Flipped the gcp branch of the ONE `@pytest.mark.seam("a2a_client")` test — previously
  raised `NotImplementedError`, now builds `GcpA2AClient` with injected fake fetcher/sender
  + `FakePeerDirectory` (card URLs match `gateway_host` so reconciliation passes), and the
  same three-activity terminal-outcome flow (send/dial/deliver → receipts, no `resent`)
  passes as it does locally. New dedicated test file `tests/test_gcp_a2a_client.py` (11
  passed, 2 skipped) with credential-free structural assertions: `reconcile_card_host`
  match passes / mismatch refuses loud (ValueError naming both hosts); transport mints OIDC
  ID token with audience == reconciled URL; no bearer token field on any message; lazy
  imports verified. The live authenticated send (real Gateway + counterparty) is
  **DEFERRED** to P7-9 / S1-benefits-6, gated on `BRIDGE_TEST_GCP`.

- **Plan-time test** (`terraform/tests/gateway.tftest.hcl`): Three credential-free
  `command = plan` runs — (a) `disabled_creates_nothing`: `enable_gateway = false` asserts
  NEG / forwarding-rule / backend resources have `length(...) == 0`, `output.gateway_ip ==
  ""`, (b) `enabled_builds_ingress_lb`: `enable_gateway = true` asserts serverless NEG
  `cloud_run[0].service == "bridge-runtime"`, forwarding rule has the external HTTPS shape,
  (c) `per_party_invoker_scoping`: `party_invoker_members = [SA-a, SA-b]` asserts two
  `google_cloud_run_v2_service_iam_member` entries with `role == "roles/run.invoker"`. T2.8
  invariant: `grep -RniE "firewall|egress|vpc_access|sql|database|cloudsql"
  terraform/modules/gateway/` → no match (the module declares no egress/datastore
  resource).

**Why adk >=2.7 same-origin supersedes the .mtls repoint hack:**

Before implementing, the task re-verified the card-url repointing question against the
installed `google-adk 2.7.1` (`a2a-sdk 1.1.2`). The finding:
`.venv/lib/python3.12/site-packages/google/adk/agents/remote_a2a_agent.py:506-550`
`RemoteA2aAgent._validate_card_rpc_targets` (new since 2.3) now **enforces**: every
advertised RPC URL must be `https` (or `http` on loopback), AND every advertised RPC URL's
**origin must equal the origin the card was fetched from** (`card_origin != source_origin`
→ raises `AgentCardResolutionError`). The 2.3-era `.mtls` card-repoint hack (rewrite a
fetched card's RPC URL to a sidecar host) would now **HARD-FAIL** on same-origin mismatch,
so it is deliberately NOT ported. Instead: (a) **Consumer side** (Address's
`RemoteA2aAgent` → Bridge): the served Bridge Agent Card must advertise the same
Gateway-authorized origin the consumer fetches it from — satisfied by `BRIDGE_PUBLIC_URL`
env → `resolved_card_url()` → card `url` + executor `base_url`. (b) **Bridge outbound
side** (our `a2a_client` seam): reconcile explicitly and refuse loud — host mismatch is a
misconfiguration, not something to paper over with a silent repoint.

**Why Gateway governs ingress only:**

The module builds only external-facing LB resources + per-party `run.invoker` IAM; it
touches **no** egress/datastore/private-network resource (enforced by T2.8 grep + the
runtime tftest preserving the private-IP invariant when the gateway is enabled). This
sidesteps the Gateway HTTP-only egress trap structurally: the Bridge→Cloud SQL connection
stays the runtime module's private-IP Direct VPC egress (`PRIVATE_RANGES_ONLY`, P7-1),
never routed through an HTTP hop.

**Gotchas:**

- **OIDC token, not bearer token / API key.** The transport mints a workload OIDC ID token
  per call (audience = the reconciled URL). No outbound message dataclass carries a
  token/credential field. Both the ID-token fetcher and the httpx sender are injectable, so
  unit + parity tests run credential-free.

- **Refuse-loud on host mismatch.** `reconcile_card_host` raises `ValueError` naming both
  hosts (the advertised URL and the Gateway-authorized host) on a mismatch — never a silent
  repoint, mirroring the seam's refuse-without-config posture.

- **Gateway env + public-URL env are optional at plan time.** Both runtime envs are gated
  (`for_each = var.<x> != "" ? [1] : []`) so the P7-1 default (no gateway) leaves the
  container env unchanged. The actual values are injected at deploy/P7-9.

- **Live authenticated wire send is deferred to P7-9 / S1-benefits-6.** The real outbound
  A2A send over a live network with credentials is exercised only under `BRIDGE_TEST_GCP`
  against a provisioned Gateway + counterparty — that is a P7-9 input. P7-5 proves the
  arming half (reconcile + mint + refuse-loud) credential-free.

**Acceptance verified:**

1. T1: Full suite green (660 passed, 16 skipped), `ruff check` clean, isolation guard green
2. Terraform plan-time credential-free (`fmt`, `init`, `validate`, `test` all exit 0 with
   no GCP auth; 13 tftest files passed incl. new `gateway.tftest.hcl` 3 runs)
3. `a2a_client` gcp adapter wired (`get_adapter(Seam.a2a_client, SeamMode.gcp)` →
   `GcpA2AClient`, previously raised)
4. Refuse-without-config loud (`BRIDGE_GATEWAY_HOST` unset → `RuntimeError` naming env)
5. Host reconciliation — match passes (`reconcile_card_host('https://host.example/rpc',
   'host.example')` → URL unchanged), mismatch refuses loud (ValueError naming both hosts)
6. Terminal-outcome parity local↔gcp (`test_a2a_client_seam.py` + `test_gcp_a2a_client.py`
   all pass — same send/dial/deliver receipts flow, credential-free via injected fakes)
7. OIDC token minted with the authorized audience; no bearer field on any outbound message
8. Served card honors `BRIDGE_PUBLIC_URL` (monkeypatch env → `resolved_card_url()` returns
   it; env unset → `DEFAULT_URL`)
9. Gateway module credential-free (`gateway.tftest.hcl` green — disabled=nothing,
   enabled=ingress LB, per-party invoker)
10. T2.8 invariant: `grep` the gateway module for egress/datastore tokens → no match (module
    is ingress-only)

Live authenticated wire send (real Gateway + counterparty over a live network) deferred to
P7-9 (deployed-path parity gate) / S1-benefits-6. No live apply in this task.

---

## P7-6: Deployed extraction adapter (Gemini engine behind the seam)

**Completed:** 2026-09-07

Built the Gemini extraction engine and its GCP seam adapter to support deployed document
extraction. This is unique among the P7 seams: extraction is the **capability axis**
(fixture|gemini|docai), not a local↔gcp storage swap — the deployed engine is selected by
config, not by switching between local and GCP modes.

**What was built:**

- **`bridge/extraction/gemini.py`**: Schema-driven Gemini extraction engine mirroring
  `FixtureExtractor`'s shape. Two independently-testable halves: (a) pure parse helper
  `_parse_extraction(text) -> ExtractionResult` (takes model's JSON response, returns
  validated `ExtractionResult` — unit-testable with no client/network), and (b) client
  call in `async def extract(artifact_name)` that reads document bytes, builds a Gemini
  request (JSON schema prompt + document as inline Part), and returns parsed result.
  - **Injectable seam**: `client` (default None → lazy `google.genai.Client()` on first
    use) and `resolver` (default corpus-relative byte reader at `wiki/evals/address/`,
    mirrors `bridge/edge/artifacts.py::resolve_bytes` swap point). Tests inject fakes →
    credential-free unit tests.
  - **Lazy imports**: `google.genai` imported INSIDE methods only, never at module top
    (keeps the module edge-free/cycle-free like `fixture.py`).
  - **Model config**: Uses `BRIDGE_MODEL` env (default `gemini-2.5-flash`, the existing
    `DEFAULT_MODEL` constant).
  - **SVG handling**: SVGs ride as **text parts** (corpus images are text SVGs; simpler
    than trying to force bytes as image parts).

- **`GcpExtraction` seam adapter** (`bridge/seams/gcp/__init__.py`): Wraps the engine
  behind the extraction seam. Engine selection by `BRIDGE_EXTRACTION_ENGINE` env
  (fixture|gemini|docai):
  - `gemini` (default) → `GeminiExtractor()`
  - `docai` → raises `NotImplementedError` with clear message (Document AI is a logged
    later swap — visible scope-out, never a silent gap)
  - `fixture` → explicit opt-in only (never a silent fallback on the deployed path)
  - Unknown engine → `ValueError` (refuse-loud on misconfiguration)
  - Registered in `GCP_ADAPTERS[Seam.extraction]`, so `get_adapter(Seam.extraction,
    SeamMode.gcp)` now returns `GcpExtraction` instead of raising.

- **Registry updates** (`bridge/seams/registry.py`): Updated docstring — extraction now
  dispatches to GCP_ADAPTERS; only `memory_bank` (deferred by design) still raises
  NotImplementedError.

- **Credential-free unit tests** (`tests/test_gemini_extraction.py`): Parse helper with
  canned JSON → valid `ExtractionResult`; fake-client extract (injected fake returns
  canned response) → parsed result; lazy-import guard (importing the module constructs no
  real client); engine-select refuse-loud (docai → NotImplementedError, bogus →
  ValueError); adapter identity.

- **Live corpus parity test** (`tests/test_gemini_extraction.py`): Gated on
  `BRIDGE_TEST_GEMINI=1`, runs the REAL `GeminiExtractor().extract()` for each of the 8
  corpus docs, feeds signals to `run_disposition_gate()`, asserts **terminal-outcome
  parity** (`disposition` and `gate` match `expected.json` — the extraction analogue of
  `BRIDGE_TEST_GCP`). Float-equality of confidences is deliberately NOT asserted (parity
  is terminal-outcome, never ledger-identical).

- **Seam test updates** (`tests/test_seams.py`): Removed `Seam.extraction` from the
  "raises NotImplementedError" loop (left only `Seam.memory_bank`); added positive
  assertion that GCP extraction adapter returns `GcpExtraction` instance.

**Why extraction is different from every other P7 seam:**

The other seven seams (P7-2…5) swap a **store/infra backend** between local and GCP (in-
memory sqlite ↔ Cloud SQL, in-memory artifact store ↔ GCS, VirtualClock ↔ Cloud Tasks).
Extraction swaps the **engine** — fixture|gemini|docai are three capability levels of the
same extraction function, deployed either way and selected by config. The "GCP adapter" is
really "the Gemini engine behind the seam," not a local→cloud backend swap.

**Why terminal-outcome parity, not float-equality:**

A real Gemini call produces stochastic confidence floats that will not bit-for-bit match
the fixture corpus values. The corpus reproduction gate is: extracted signals → feed
`run_disposition_gate` → assert the SAME `expected_disposition` and `expected_gate` for
all 8 docs. This proves the deployed extraction path reaches the same terminal outcomes
(accept/reject/HITL/resubmit) as the fixture, which is the seam parity invariant.

**Why no new dependency:**

`google.genai` ships with the core `google-adk[a2a]` dependency and is already importable.
The `gcp` extra (`asyncpg`/`google-cloud-storage`/`google-cloud-tasks`) is NOT needed for
Gemini. `pyproject.toml` untouched.

**Why Document AI is a logged later swap:**

The task shipped Gemini only. Document AI (a richer OCR engine for certain doc types) is a
documented future capability swap behind the extraction seam. Where the engine is selected,
`docai` raises `NotImplementedError` with a clear message naming Document AI as a later
swap — a visible scope-out following the "No silent caps" repo rule, never a silent gap.

**Gotchas:**

- **Extraction axis vs store axis.** Every other P7 task swapped local↔gcp backing stores;
  this one swaps capability levels (fixture|gemini|docai). Don't confuse "gcp extraction
  adapter" with "the extraction adapter for GCP storage" — it's the Gemini engine, not a
  storage backend.

- **No silent fixture fallback on the deployed path.** `GcpExtraction` refuses if the
  configured engine is unknown/unsupported — raises with a clear message. An explicit
  `BRIDGE_EXTRACTION_ENGINE=fixture` is honored (test/dev opt-in), but the default on GCP
  is `gemini`, and a missing/misconfigured engine never silently falls back to fixture.

- **Lazy imports preserve default-suite no-GCP posture.** `google.genai` is imported
  lazily inside methods (not at module top) so importing `bridge.seams.gcp` or
  `bridge.extraction.gemini` pulls no heavy client. Mirrors P7-2 asyncpg / P7-3
  google-cloud-storage / P7-4 tasks discipline.

- **Agent code untouched.** `bridge/extraction/agent.py::extract_document` already
  resolves via `get_adapter(Seam.extraction, resolve_mode(Seam.extraction))`. With
  `BRIDGE_SEAM_MODE=gcp` it now returns `GcpExtraction` → Gemini, with no edit needed.

- **Protected corpus is oracle.** `wiki/evals/address/expected.json` is read-only and
  CRITICAL-protected. If the real Gemini engine cannot hit an expected terminal outcome,
  that is a finding to REPORT (propose a prompt/schema fix), never adjust the corpus to
  make the test pass.

- **Live corpus test requires credentials.** The `BRIDGE_TEST_GEMINI=1` live test runs
  against the real Gemini API and requires an API key. It stays SKIPPED in the default
  suite (credential-free), exactly like `BRIDGE_TEST_GCP` live seam tests. The
  provisioned end-to-end run is P7-9.

**Acceptance verified:**

1. `uv run pytest` full suite green (674 passed, 24 skipped)
2. `uv run ruff check` clean
3. Isolation guard green (`bridge/extraction/gemini.py` imports only `contract/`,
   `bridge.*` (not `agents/`), `google.genai` (lazy), stdlib)
4. GCP adapter dispatches (`get_adapter(Seam.extraction, SeamMode.gcp)` →
   `GcpExtraction Seam.extraction`, previously raised NotImplementedError)
5. Importing package pulls no genai client at import time (lazy imports verified; no
   eager client construction)
6. Document AI is loud scope-out (`GcpExtraction(engine='docai')._resolve_engine()` →
   NotImplementedError naming Document AI as later swap)
7. Credential-free unit tests pass (13 passed; live corpus test reports SKIPPED without
   `BRIDGE_TEST_GEMINI=1`)
8. Corpus-reproduction gate written and structurally sound (live terminal-outcome run
   against real Gemini API with credentials is P7-9 provisioned proof, mirroring how
   GcpScheduler/GcpA2AClient gate their live half on `BRIDGE_TEST_GCP`)

Live provisioned end-to-end run (whole seam suite + Address golden runs on deployed path)
deferred to P7-9 (deployed-path parity gate). No live apply in this task.

---

## P7-7: Frontend deploy module (portal public LB+CDN, console/dashboard private)

**Completed:** 2026-09-07

Built the Terraform frontend deploy module that serves the built SPA assets: the external-zone
provider-portal on a public HTTPS LB with Cloud CDN, and the internal-zone console/dashboard
on private staging buckets. Plus build-time environment-variable wiring so deployed endpoint
URLs are baked into the bundles.

**What was built:**

- **Terraform `modules/frontend/`**: Four-file module (versions.tf, variables.tf, main.tf,
  outputs.tf) provisioning three GCS buckets + an external HTTPS Load Balancer for the
  portal. All resources gated on `var.enable_frontend` (default false) via
  `count = var.enable_frontend ? 1 : 0` (mirrors the gateway module's toggle idiom) so the
  disabled default creates nothing → credential-free plan-time test stays trivially green.

- **Portal bucket (public by design)**: `google_storage_bucket "portal"` with
  `uniform_bucket_level_access = true` but **NO** `public_access_prevention = "enforced"`
  (the external-zone portal is public web assets, not private byte data). Website config
  with `main_page_suffix = "index.html"` + `not_found_page = "index.html"` (SPA client-side
  routing fallback). `google_storage_bucket_iam_member "portal_public_read"` grants
  `roles/storage.objectViewer` to `allUsers` (the load-bearing public grant).

- **Console + dashboard buckets (private)**: `google_storage_bucket "console"` and
  `"dashboard"` with `uniform_bucket_level_access = true` + `public_access_prevention =
  "enforced"` (internal-zone privacy, no `allUsers` grant). Website config too (SPA
  fallback — harmless on a private bucket). Header comment notes live internal serving (IAP
  / internal LB) is a follow-up when serving surfaces split into their own services (the
  "Per-workload identity if serving surfaces split" TODO.md P7 note) — no silent cap.

- **Portal external HTTPS LB + CDN**: `google_compute_backend_bucket "portal"` (points at
  the portal bucket, `enable_cdn = true`) → `google_compute_url_map "portal"` (default
  service = backend bucket) → `google_compute_managed_ssl_certificate "portal"` (gated on
  `var.enable_frontend && length(var.ssl_domains) > 0`) → `google_compute_target_https_proxy
  "portal"` (url_map + ssl_certificates) → `google_compute_global_address "portal"` (LB
  anycast IP) → `google_compute_global_forwarding_rule "portal"` (load_balancing_scheme =
  EXTERNAL_MANAGED, port 443, labels). Mirrors the `modules/gateway/` LB structure but with a
  **backend bucket** instead of a Cloud Run serverless NEG.

- **Outputs**: `portal_bucket_name`, `console_bucket_name`, `dashboard_bucket_name` (the
  deploy step `gsutil rsync`es each app's `dist/` into these), `portal_ip` (the LB anycast
  IP, `""` when disabled), `portal_url` (derived from `ssl_domains[0]`, `""` when disabled).
  Each output guarded with `length(...) > 0 ? ...[0].x : ""` (mirrors gateway outputs).

- **Root wiring** (`terraform/main.tf`, `variables.tf`, `outputs.tf`,
  `terraform.tfvars.example`): Added `module "frontend"` after `module "gateway"` (passes
  `project_id`, `name_prefix = var.service_name`, `location = var.region`,
  `enable_frontend`, `ssl_domains = var.frontend_ssl_domains`, `labels`). Root variables:
  `enable_frontend` (bool, default false), `frontend_ssl_domains` (list(string), default
  `[]` — distinct from the gateway's `ssl_domains` since they front different hosts). Root
  re-exports the five frontend module outputs. Updated extension-seam comment at line ~46
  from P7-7 reservation to "BUILT (module.frontend) — …" (mirrors P7-3/4/5 entries).

- **Plan-time test** (`terraform/tests/frontend.tftest.hcl`): `mock_provider` +
  `command = plan`, two runs — (a) `disabled_creates_nothing`: `enable_frontend = false`
  asserts `length(google_storage_bucket.portal) == 0` +
  `length(google_compute_global_forwarding_rule.portal) == 0`. (b)
  `enabled_serves_portal_public_and_internal_private`: `enable_frontend = true`,
  `name_prefix = "bridge-runtime"`, `ssl_domains = ["portal.example.com"]` asserts bucket
  names track prefix; portal bucket does NOT enforce PAP; portal has the `allUsers`
  `objectViewer` member; console + dashboard buckets DO enforce PAP (the internal-zone
  privacy assertion); backend bucket has CDN on; forwarding rule exists. Extended
  `tests/root.tftest.hcl` with a run asserting the root wires `module.frontend` and the
  `portal_bucket_name` output resolves (mirrors the root-integration style).

- **Build-time endpoint wiring** (`frontend/*/env.production.example`): Added
  `frontend/provider-portal/.env.production.example`,
  `frontend/agent-console/.env.production.example`,
  `frontend/ops-dashboard/.env.production.example` — each containing the one `VITE_*_URL`
  var that app reads, with a comment that the deploy step (P7-9) sets it to the resolved
  deployed endpoint. (`.example` so Vite does NOT auto-load it and bake a placeholder; do
  not commit a real `.env.production`.) No new README (per CLAUDE.md, do not create
  documentation files gratuitously).

- **timewarp not provisioned**: Confirmed no `timewarp` bucket/resource in
  `modules/frontend/main.tf` — it is local-only by design (`design/frontend.md` lines 38,
  53).

**Why public portal vs private console/dashboard (zone boundary):**

The **external-zone portal is public by design** (`design/zones.md` — external zone =
untrusted counterparties reach it). Serving its static SPA assets from a **public** GCS
bucket behind an external HTTPS LB + Cloud CDN is the correct, standard pattern for public
web assets — NOT a violation of the "never public / signed-URLs-only" invariant, which
governs artifact/skill **byte data** (the modules/gcs buckets), not public web assets. GCS
`google_compute_backend_bucket` requires the bucket to be readable by the LB, which for a
public static site means `allUsers:objectViewer` — this is incompatible with
`public_access_prevention = "enforced"`, so the portal bucket must NOT enforce PAP and DOES
grant `allUsers:objectViewer`.

The **internal-zone console + dashboard** must NOT be publicly reachable (`design/zones.md`
"the internal zone never accepts unauthenticated calls"). Provisioned their buckets
**private** (`uniform_bucket_level_access = true`, `public_access_prevention = "enforced"`,
no `allUsers` grant, NOT on the public LB). Their deployed serving surface (IAP / internal
LB) is a follow-up wired when the internal serving surfaces split into their own services
(TODO.md P7 "Per-workload identity if serving surfaces split"). Provisioning them as
private staging buckets here honors the zone boundary; a header comment says live internal
serving is a follow-up (no silent cap).

**Why build-time endpoint wiring, not runtime config:**

Each app's `VITE_*_URL` is a **build-time** `import.meta.env.VITE_*_URL` read (see
`frontend/*/src/api.ts`) — so "wire to the deployed edge/dashboard endpoints" is realized by
passing the deployed URLs as build env vars at build time, NOT by a terraform resource or
runtime config fetch. Terraform provisions the buckets + LB; the P7-9 upload + build step
reads the terraform outputs (bucket names / LB URL), builds each app with the deployed
endpoint injected, and `gsutil rsync`es the `dist/` assets.

**Why no bucket OBJECTS in HCL:**

The module provisions buckets + LB only; the built `dist/` assets are uploaded at deploy
(P7-9) via `gsutil rsync dist/ gs://<bucket>`, never Terraform-managed. This is the "no
bucket OBJECTS in HCL" invariant (`modules/gcs/` header, CRITICAL.md) that keeps secrets /
large blobs out of state. The plan-time test asserts structural config (bucket names,
public/private, CDN), never object content.

**Why two separate `ssl_domains` vars:**

The gateway fronts the runtime/edge (Cloud Run service); the frontend LB fronts the portal
(backend bucket). Use a distinct root var (`frontend_ssl_domains`) so they can point at
different hostnames (the gateway may be `api.example.com`, the portal may be
`portal.example.com`).

**Gotchas:**

- **PAP-vs-public conflict is real.** A `google_compute_backend_bucket` cannot serve a
  `public_access_prevention = "enforced"` bucket to anonymous users. The portal bucket must
  be public (that is the *external zone*); the internal buckets stay enforced-private. Do
  not "fix" the portal bucket to enforced PAP — that breaks public serving. Do not relax
  the internal buckets to public — that breaks `design/zones.md`.

- **Keep `frontend` ingress-only + no datastore/egress resource.** Same discipline as the
  gateway module header: the frontend module must not create any VPC/firewall/private-network
  or datastore resource (mirrors the T2.8 gateway invariant).

- **`ssl_domains` may be unknown at plan time.** The managed-cert resource is gated on
  non-empty (exactly like the gateway module); the LB URL is injected out-of-band at deploy
  (P7-9), like `followup_callback_url` / `public_url` (do not hardcode it in HCL).

- **Console/dashboard live internal serving is a follow-up.** Provisioning them as private
  staging buckets now is deliberate; the header comment in `modules/frontend/main.tf` logs
  it so it is NOT a silent cap (TODO.md P7 "No silent caps" / "Per-workload identity if
  serving surfaces split").

**Acceptance verified:**

1. `terraform fmt -check -recursive` clean (exit 0)
2. `terraform test` green with **NO credentials configured** (all runs pass, incl. new
   `frontend.tftest.hcl` 2 runs + extended `root.tftest.hcl`)
3. Disabled default is credential-free-empty (`disabled_creates_nothing` run → zero
   frontend resources)
4. Enabled shape is correct (zone boundary enforced): `enabled_serves_portal_public_and_
   internal_private` run asserts portal bucket public (`allUsers:objectViewer`, PAP not
   enforced) behind a CDN-enabled backend bucket + forwarding rule; console + dashboard
   buckets private (`public_access_prevention = "enforced"`, no public grant). This is the
   load-bearing zone assertion.
5. Whole terraform suite still green, credential-free (`Success! 15 passed, 0 failed` with
   no GCP credentials present, up from 13 before this task)
6. Build-time endpoint wiring works end-to-end: built the portal with a deployed endpoint
   injected (`VITE_PORTAL_URL=https://bridge-runtime-abc.a.run.app pnpm --filter
   provider-portal build`), confirmed the built JS contains the injected deployed origin
   (`grep -rl "bridge-runtime-abc.a.run.app" frontend/provider-portal/dist/assets` → match).
   Proves the `VITE_*_URL` wiring the deploy step relies on actually reaches the bundle.
7. timewarp is not provisioned (grep → no `timewarp` bucket/resource in
   `modules/frontend/main.tf`)
8. `uv run pytest` green (no Python touched; confirm no regression)
9. `uv run ruff check` clean (no Python touched; confirm)
10. `pnpm install --frozen-lockfile && pnpm -r build` → all app builds succeed (the new
    `.env.production.example` files are inert; builds unaffected)
11. `pnpm -r test` green

All criteria passed. The frontend deploy module is ready; live provisioning happens at
P7-9.

## P7-9: Deployed-path parity gate (the P7 exit gate)

**Completed:** 2026-09-07 (artifacts + credential-free gate; live apply is the gated operator step)

The P7 exit gate assembles the deploy (container image + two-phase orchestration that
injects everything the prior tasks stubbed), authors the deployed-path Address parity
harness (the one exit criterion that had no test), and runs the four exit criteria — the
three that can run credential-free are green in-sandbox; the three needing a provisioned
project stay cleanly skipped as the documented operator step (no silent cap).

**The four exit criteria and where each is proven:**

1. **Whole seam suite with `BRIDGE_TEST_GCP=1`** — the gcp seam params + live roundtrips.
   Credential-free they skip (doubly gated: `BRIDGE_TEST_GCP=1` enables the param, each test
   also skips without its own resource env — DSN/bucket/queue). Live: `scripts/parity.sh`.
2. **Deployed Address golden runs** — `tests/test_deployed_parity.py` (new), the second exit
   criterion. Runs `run_collection` (the real driver) against the deployed edge and asserts
   the corpus **terminal outcomes** (CUJ-1 instant accept; CUJ-10 distinct-issuer accept +
   same-issuer reject; CUJ-4 resubmit first-turn reject). Gated on `BRIDGE_TEST_GCP=1` AND
   `BRIDGE_DEPLOYED_EDGE_URL`; skips cleanly credential-free naming both envs.
3. **Plan-time terraform, no credentials** — `terraform test` = 17 passed, 0 failed with
   `GOOGLE_APPLICATION_CREDENTIALS` unset (up from 16: the new `seam_envs_when_deployed` run).
4. **Cross-replica read-after-write** — `test_task_store_cross_replica_read_after_write`. The
   `[local]` param (session-affinity guard, two independent handles to one file-DSN store)
   passes credential-free; the `[gcp]` param runs against Cloud SQL under the live env block.

**What was built:**

- **`Dockerfile` + `.dockerignore` (repo root)** — the Cloud Run image. `python:3.12-slim`,
  `uv sync --frozen --no-dev --extra gcp --no-install-project` (the deployed-path deps:
  asyncpg / google-cloud-storage / google-cloud-tasks), copies `contract/ bridge/ skills/
  wiki/` ONLY (never `agents/` — the isolation invariant; the eval corpus ships because the
  fixture/artifact loaders read it via `__file__`-relative paths at runtime). Runs from the
  source tree (docubridge is a uv virtual project — no wheel install, so `__file__` stays in
  the tree and the corpus resolves). `EXPOSE 8080`; `CMD` boots `serve_container()`.

- **`serve_container()` in `bridge/edge/server.py`** — the container entrypoint: reads `PORT`
  (default 8080), binds `0.0.0.0`, builds the app via `build_a2a_app(url=resolved_card_url(
  None))` so the served card advertises `BRIDGE_PUBLIC_URL` (adk >=2.7 same-origin). The
  existing `serve()` loopback default is UNCHANGED (S0-sec-3); `serve_container` is a separate
  entrypoint. Binding `0.0.0.0` does not reopen S0-sec-3 — the deployed ingress is
  Gateway-locked (`INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER`). A `[project.scripts] bridge-serve`
  entry names the packaged equivalent.

- **`scripts/deploy.sh`** — the two-phase apply resolving the self-reference cycle
  (`public_url` / `followup_callback_url` are the runtime's OWN URL, unknown until after apply
  1). Preflight (APIs, required env), build+push the image to Artifact Registry, apply phase 1
  (`public_url=""`), inject the DB DSN secret VALUE out-of-band (composed in-memory, piped to
  `gcloud secrets versions add --data-file=-`, NEVER on disk / in HCL), resolve URLs, apply
  phase 2 with the runtime's own-URL envs, build+rsync the frontend apps, print the parity env
  block. Sets `seam_mode=gcp` + `extraction_engine=gemini` (the seam master switch + the
  deployed extraction engine). `set -euo pipefail`; `bash -n` + shellcheck clean.

- **`scripts/parity.sh`** — runs the exit gate: plan-time terraform, then the whole suite
  under the exported live env block (un-skips the gcp params + the deployed golden runs +
  the `[gcp]` cross-replica RAW). Reads config from the environment (the deploy block); the
  DSN is passed through env only, never written to disk.

- **Terraform runtime seam envs** — added gated `seam_mode` / `extraction_engine` vars to
  `modules/runtime` (mirroring the existing `for_each = var.x != "" ? [1] : []` idiom) so the
  deployed container gets `BRIDGE_SEAM_MODE=gcp` + `BRIDGE_EXTRACTION_ENGINE=gemini` as plain
  envs; default "" leaves the plan untouched. Wired through the root module; new `db_name` /
  `db_user` root outputs feed the DSN composition. A `seam_envs_when_deployed`
  `runtime.tftest.hcl` run asserts the deployed shape (and the default asserts no env).

**The parity env map (what the live run consumes):**

| Env | Consumed by |
|---|---|
| `BRIDGE_TEST_GCP=1` | the `adapter` fixture (un-skips gcp seam params) + every live test |
| `BRIDGE_GCP_TEST_DSN` | Cloud SQL DSN — durable roundtrip + the cross-replica RAW |
| `BRIDGE_SKILLS_BUCKET` | live GCS skill-registry roundtrip |
| `BRIDGE_TASKS_QUEUE` / `BRIDGE_FOLLOWUP_CALLBACK_URL` / `BRIDGE_RUNTIME_SA_EMAIL` | live Cloud Tasks arm→cancel |
| `BRIDGE_GATEWAY_HOST` | Gateway-authorized host reconciliation (a2a_client) |
| `BRIDGE_TEST_GEMINI=1` | live Gemini corpus terminal-outcome parity |
| `BRIDGE_DEPLOYED_EDGE_URL` | the deployed Address golden runs (`test_deployed_parity.py`) |

**Credential-free vs live (the honest split — no silent cap):** there is no reviewer
-provisioned Bridge stack in this environment (ADC resolves to the VM's ambient dev SA, but
no deployed edge / Cloud SQL / substrate exists, and provisioning a full VPC+Cloud SQL+Cloud
Run+LB stack is costly and unauthorized). So the deliverable is the deploy artifacts + the
parity harness + the credential-free gate green + this runbook; the live `bash scripts/
deploy.sh` + `bash scripts/parity.sh` is the gated operator step against a provisioned
project. Terminal-outcome parity, NEVER ledger-identical — the deployed edge (real Gemini,
real Cloud SQL, real timestamps) will not byte-match the fixture; only accept/reject/
satisfied/escalate are asserted.

**Deferral re-logged (not built):** the Cloud Tasks follow-up callback RECEIVER endpoint
(the route that receives the queue POST and re-drives `FollowupLadder.on_fired` → re-arm)
is NOT one of P7-9's four exit criteria (CUJ-6's deployed follow-up ladder is not in the
gate; `test_gcp_scheduler_live_roundtrip` arms→observes→cancels without waiting for a real
firing). It rides the "follow-up worker behind the edge" family already deferred in TODO.md,
and stays deferred here to keep P7-9 at exit-gate scope. If wanted, it is a small runtime
route mirroring `agents/address/wake.py` (parse the Cloud Tasks `{key,payload}`, verify the
OIDC audience, drive the ladder) — call it out before adding, as it is beyond the stated gate.

**Acceptance (credential-free, all executed and observed):**

1. `cd terraform && env -u GOOGLE_APPLICATION_CREDENTIALS terraform test` → Success! 17
   passed, 0 failed.
2. `uv run pytest -q` → 676 passed, 28 skipped (24 pre-existing gated + 4 new deployed-parity
   skips). `uv run pytest tests/test_deployed_parity.py -rs` → 4 SKIPPED, reason names
   `BRIDGE_TEST_GCP` + `BRIDGE_DEPLOYED_EDGE_URL`.
3. `uv run pytest "…::test_task_store_cross_replica_read_after_write[local]"` → passed.
4. `docker build -t bridge:local .` → succeeds; `docker run -e PORT=8080 -e
   BRIDGE_SEAM_MODE=local -p 8080:8080 bridge:local` then `curl -sf localhost:8080/.well-
   known/agent-card.json` → the card (`"name":"Document Bridge"`) → proves 0.0.0.0:$PORT boot.
5. `bash -n scripts/deploy.sh && bash -n scripts/parity.sh` clean; shellcheck clean; `grep
   -rnE "password|secret|BEGIN .*PRIVATE" scripts/ Dockerfile` → only env reads + discipline
   comments, no committed secret.
6. `uv run ruff check` clean; isolation guard + service-wiring guard green.

---

## S0-arch-14: RFP board persistence through the exchange_store seam

**Completed:** 2026-10-01

Built the `exchange_store` seam (previously an empty ABC with `pass` adapters) to persist
the RFP aggregate (`RfpCollection`) so RFPs with weeks-scale due dates survive process
restarts. The seam stores materialized Exchange records with optimistic concurrency control,
supporting both local development (SQLite) and GCP deployment (Cloud SQL PostgreSQL).

**What was built:**

- **`bridge/exchange_store.py`** — core exchange record store module with stdlib-only
  top-level imports:
  - `ExchangeRecord`: versioned JSON document keyed by `(kind, ref)` with A2A `context_id`
    as an indexed non-unique column (identity stays the A2A context token, never a minted id).
  - `StaleExchangeWrite`: exception raised on version conflicts.
  - `ExchangeStore` ABC with sync methods: `get`, `list` (creation-ordered), `put` (with
    compare-and-set via `expected_version`), `close`.
  - `InMemoryExchangeStore`: dict-backed with JSON round-trip validation and thread-safe.
  - `SqlExchangeStore`: SQLAlchemy Core over `AsyncEngine` (table `bridge_exchange_records`),
    runs on a private daemon loop thread with sync facade via `run_coroutine_threadsafe`.
    Lazy initialization (thread/loop/engine/table created on first operation, not `__init__`).
  - `exchange_store_from_env()`: resolves via seam registry.

- **Seam adapters** (`bridge/seams/protocols.py`, `local/__init__.py`, `gcp/__init__.py`):
  - `LocalExchangeStore.build_exchange_store()`: `BRIDGE_EXCHANGE_DB_URL` empty →
    `InMemoryExchangeStore()`; DSN set → `SqlExchangeStore(dsn)` with async-driver validation.
  - `GcpExchangeStore.build_exchange_store()`: `SqlExchangeStore` over `DATABASE_URL`
    (same Cloud SQL instance as sessions/task_store, distinct table), refuses in-memory
    and sync drivers (session-affinity trap guard).

- **`RfpBoard` read-through persistence** (`bridge/dashboard/board.py`):
  - Constructor accepts `store: ExchangeStore | None = None`, defaults to
    `InMemoryExchangeStore()` (keeps pure test constructions unchanged).
  - Thread-local version tracking per `rfp_id` for compare-and-set writes (FastAPI
    threadpool safety).
  - `board.rfps` becomes a read-only `Mapping` view (no `__setitem__`) backed by store.
  - All operations (`put`, `get`, `find_leg`, `items`, `statuses`) read-through with no
    cache (prevents per-replica split on multi-instance deployments).

- **RFP demo host wiring** (`bridge/demos/rfp_demo.py`):
  - `build_rfp_demo_app(..., exchange_store=None)` wires `exchange_store_from_env()`.
  - Seed only when absent: `if DEMO_RFP_ID not in board.rfps: _seed(...)` (prevents
    `StaleExchangeWrite` on restart with durable DSN).

- **Tests** (`tests/test_exchange_store_seam.py`, `tests/test_rfp_durable.py`):
  - Shared seam parity test with `@pytest.mark.seam("exchange_store")` covering durability,
    CAS (create-only + version conflicts), creation-order listing, cross-replica
    read-after-write, JSON validation.
  - Board-level restart survival (put → close → new handle sees same state), cross-board
    stale-write detection, demo host restart with real DSN, lossless JSON round-trip.
  - Updated `test_gcp_exchange_store_refuses_in_memory_and_sync_pg` (was
    `test_gcp_exchange_store_is_empty_by_design`).

**Key design decisions:**

- **Generic exchange-record store**, not RFP-specific: keyed by `(kind, ref)` with
  `context_id` indexed but NOT the primary key (multiple RFPs can share a `context_id` in tests).
- **Sync API over async implementation**: board callers are sync and write sites rely on
  "fresh read → put with no await between"; SQL store runs `AsyncEngine` on private loop
  thread. Trade-off: board read blocks caller's event loop for one DB round-trip (acceptable
  at RFP scale; S0-arch-17 may revisit).
- **Thread-local version tracking**: prevents FastAPI threadpool races where concurrent reads
  could create cross-replica-style conflicts within one process.
- **Lazy everything**: thread/loop/engine/table initialization deferred to first operation
  so `get_adapter` never raises and importing `bridge.seams.gcp` doesn't import `asyncpg`.
- **No 409 mapping for `StaleExchangeWrite` yet**: unhandled conflicts surface as 500 on
  routes or retry in email drain loop; proper retry/409 policy deferred to S0-arch-17
  (multi-replica host).

**Gotchas:**

- Seed-only-when-absent guard required for demo host restart with durable DSN.
- JSON round-trip on both stores (write and read, `allow_nan=False`) ensures identical
  `TypeError` surface and prevents mutation of stored state via returned dicts.
- Lazy `bridge.seams` import in `exchange_store_from_env` to avoid circular-import trap
  (see DECISIONS.md S0-bug-3 #7).
- No existing test needed re-read fixes — all write sites already followed fresh-read → put pattern.

**Acceptance verified:**

1. `uv run ruff check` clean, `uv run pytest` full suite green (no new skips except existing
   `BRIDGE_TEST_GCP` / `BRIDGE_GCP_TEST_DSN` gates).
2. Seam parity: `uv run pytest tests/test_exchange_store_seam.py -v -m seam` → all `[local]`
   passed, `[gcp]` skipped only by gate.
3. Board durability: `uv run pytest tests/test_rfp_durable.py -v` → all passed.
4. Manual restart proof (two separate processes, real SQLite file) → second process sees
   carrier-c CRITICAL_ILLNESS `received` from first process's upload.
5. GCP refusal: `build_exchange_store()` without `DATABASE_URL` raises `RuntimeError`
   mentioning session-affinity trap.

**Handoff to S0-arch-17:** deployed board-owning host needs `exchange_store_from_env()`
with `BRIDGE_SEAM_MODE=gcp`, `StaleExchangeWrite` → 409 / retry policy decision, and
store closure on shutdown.

---

## Full-Step OpenTelemetry Cloud Trace v2 Exporter & BigQuery Trace Analytics (`module.telemetry`)

**Completed:** 2026-10-20 (`v1.11.0-bq-telemetry` / `86aa8aa`)

Extended the deployed GCP observability substrate so every HTTP request and all 22 internal
pipeline steps across Zones 1–6 and Agent Gym emit correlated W3C OpenTelemetry spans to
three destinations: (1) the in-memory ring buffer (`GET /v1/telemetry/spans` and
`GET /v1/telemetry/analytics`), (2) GCP Cloud Trace v2 (`traces:batchWrite`) + trace-correlated
Cloud Logging (`logging.googleapis.com/trace`), and (3) BigQuery (`docubridge_telemetry.otel_spans`
+ SQL analytics views).

**What was built:**

- **Terraform `modules/telemetry/`** (`terraform/modules/telemetry/`, tested by
  `terraform/tests/telemetry.tftest.hcl`):
  - All resources gated on `var.enable_telemetry_bq` (default `false`) so the default
    credential-free root plan stays unchanged unless enabled.
  - `google_bigquery_dataset.telemetry` (`docubridge_telemetry`) + dataset-scoped
    `roles/bigquery.dataEditor` for `bridge-runtime`.
  - `google_bigquery_table.otel_spans`: day-partitioned on `start_time` and clustered by
    `["name", "zone", "entity_id", "trace_id"]`, storing W3C `trace_id`, `span_id`,
    `parent_span_id`, `name`, `kind`, `start_time`, `end_time`, `duration_ms`, `status`,
    `service_name`, `zone`, `entity_id`, `doc_id`, `doctype`, `actor_role`, `actor_principal`,
    `agent_spiffe_id`, and `attributes_json`.
  - Three deduplicated SQL analytics views (`ROW_NUMBER() OVER (PARTITION BY trace_id, span_id
    ORDER BY start_time DESC)` CTE):
    - `v_step_latency_analytics`: `span_count`, `error_count`, `error_rate_pct`, `avg_duration_ms`,
      `p50_duration_ms`, `p95_duration_ms`, `p99_duration_ms`, and `max_duration_ms` per
      `(span_name, zone)`.
    - `v_trace_waterfall`: chronological span waterfall with `offset_ms` relative to the
      earliest span in each `trace_id`.
    - `v_entity_pipeline_summary`: per-trace execution rollup (`total_spans`,
      `distinct_step_names`, `pii_redaction_spans`, `worm_vault_locks`,
      `operational_store_promotions`, `bitemporal_queries`, `bigquery_cdc_syncs`,
      `lineage_traces`, `spiffe_svid_verifications`, `inbox_governance_actions`,
      `agent_gym_operations`).
  - `google_logging_project_sink.otel_spans_to_bq` (`filter = "jsonPayload.otel.span_id:*"`)
    with dedicated writer identity granted `roles/bigquery.dataEditor`.
- **`CloudTraceV2SpanExporter` & `compute_trace_analytics` (`bridge/telemetry.py`)**:
  - Exports finished spans to Cloud Trace v2 (`POST https://cloudtrace.googleapis.com/v2/projects/{project}/traces:batchWrite`),
    streams rows directly to BigQuery (`POST https://bigquery.googleapis.com/bigquery/v2/projects/{project}/datasets/{dataset}/tables/{table}/insertAll`
    with deterministic `insertId = f"{trace_id}:{span_id}"`), and writes trace-correlated
    structured JSON log entries to `stdout`.

**Operational learnings & gotchas:**

- **Cloud Run GFE only traces the outer HTTP request by default:** In-process Python
  OpenTelemetry child spans (`trace_step`) are invisible in GCP Cloud Trace unless explicitly
  written via the Cloud Trace v2 REST API under `roles/cloudtrace.agent`.
- **Cloud Trace v2 `batchWrite` schema strictness:** `displayName` must be wrapped as
  `{"value": ...}`, attributes must use typed `attributeMap` wrappers (`stringValue` /
  `intValue` / `boolValue`), and unknown keys (e.g., `kind`) reject the entire batch with
  HTTP 400.
- **Direct `tabledata.insertAll` vs. Logging Sink buffering:** Streaming directly to
  `tabledata.insertAll` makes spans immediately queryable in `v_step_latency_analytics`,
  `v_trace_waterfall`, and `v_entity_pipeline_summary` at the end of an E2E test, while the
  `ROW_NUMBER()` deduplication CTE prevents duplicate counting across retries.
- **Offline `terraform test` in sandboxes:** Run
  `terraform init -backend=false -plugin-dir=terraform/.terraform/providers` to initialize
  module links against cached provider binaries without network access to
  `registry.terraform.io`.
