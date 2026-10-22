# Benefits

Feature area for the **Benefits demo cycle** — the *depth* demo. A broker's agent
gathers group-benefits quotes from N carriers and needs one comparable, normalized
view to decide on: the **Negotiate** pattern over a **program** (fan-out → normalize →
compare/standard-gap → negotiate → bind → rollup), plus **Bridge-as-client dial-out +
Deliver** and **cross-framework ADK↔LangGraph A2A interop**.

Completed-work archive for this feature area (`docs/features/`). This holds the
implementation **substance** and rationale (with architectural decisions logged in `DECISIONS.md`).
When `/implement` finishes a task, it appends a completed entry below.

**Supports CUJs:** CUJ-3 (Deliver), CUJ-11 (Negotiate over a program), CUJ-12
(cross-framework ADK↔LangGraph A2A interop).

**Requirements (THE WHAT) live in `spec/`, already authored — do not duplicate here:**
- `spec/cujs.md` — CUJ-3 / CUJ-11 / CUJ-12.
- `spec/demos/benefits-demo.md` — the full scenario, the six beats, the depth
  capabilities, the simulated framework-diverse counterparties, and the validation
  (golden runs per branch + a cross-framework interop test).

**Acceptance (the demo beats that prove it — from `spec/demos/benefits-demo.md`):**
- [x] **Fan-out over a program** — one `program:acme-2026` context links N legs; each
      leg is its own context+party with per-leg isolation (competitors never share a
      context). _(done S1-benefits-1; `tests/test_program.py::TestFanOutIsolation`)_
- [x] **Mixed dual-path responses at fan-out scale** — two Path-A A2A carriers (one ADK,
      one LangGraph, USD) + two Path-B PDF uploads (EUR/French, GBP/German). _(done S1-benefits-4/5; `tests/test_cuj11_negotiate_bind_rollup.py` includes both Path-A and Path-B legs)_
- [x] **Normalize** — multilingual extraction + **FX normalization** map every quote to
      one schema / one currency / one language (fixed FX rate table for a deterministic run). _(done S1-benefits-2; `tests/test_normalize.py`)_
- [x] **Compare + standard-gap** — the program comparison view renders N legs side by
      side; each flagged against the program-standard declared rules (sense A — the Bridge
      surfaces the gap, never decides the deal). _(done S1-benefits-3, S1-benefits-7)_
- [x] **Negotiate + versioned revise loop** — pushback over A2A yields a revised quote as
      a new artifact **version** on the same leg; the comparison recomputes live. The
      **LangGraph** carrier drives the multi-turn revise loop (interop covers negotiation,
      not a one-shot). _(done S1-benefits-5; `tests/test_cuj12_interop.py::test_carrier_b_multi_turn_revise_langgraph`, `tests/test_program_comparison_view.py::test_programs_recompute_live_after_revise`)_
- [x] **Bind → program rollup → audit** — per-leg bind (sense B — the deal is the agent's),
      program-level rollup, audit trail. _(done S1-benefits-5; `tests/test_cuj11_negotiate_bind_rollup.py::test_cuj11_negotiate_bind_rollup_golden_run`)_
- [x] **Cross-framework interop test** — the same A2A conversation passes whether the
      counterparty is the ADK or the LangGraph build. _(done S1-benefits-6)_
- [x] Golden runs per branch (structured vs PDF intake, each language/currency,
      standard-gap flag, revise loop, bind + rollup) green against **fixture and Gemini**
      extraction adapters. _(done S1-benefits-4/5; fixture leg runs every branch; Gemini leg proven on the PDF intake branch via `tests/test_pdf_extraction.py::test_pdf_replay_reaches_the_same_terminal_outcome_on_both_legs` + the credential-gated `test_live_pdf_terminal_outcome_parity`)_

**Cycle record:** The reusability thesis needed its *depth* proof — the same core that runs
the bounded-Collect Address demo must run a fan-out Negotiate over a program: N messy,
multi-currency, multi-language carrier quotes rendered as one apples-to-apples comparison
with gaps flagged, a multi-turn revise loop against a **non-ADK** (LangGraph) counterparty,
and bind → rollup — all from a single "ask once" per carrier. Closed 2026-09-07 (Sprint 1, T3-green); every `S1-benefits-*` task is archived under Completed Work.

**Architecture note:** the outbound seam, trust boundary, and wire contracts are already
whole ([outbound](../../design/outbound.md)) — this cycle adds demand-driven capability (Programs, FX/multilingual
normalization, standard-gap disposition, Negotiate/versioning, dial-out/Deliver, the
interop counterparties), not new machinery. The pattern set is closed — Negotiate/Deliver
are existing built-in flows.

---

## Completed Work

<!-- Newest first. One entry per completed task; capture what a future reader
     needs WITHOUT the local plan file. -->

### S0-arch-19 — The deployed A2A runtime opens RFPs onto the durable board (2026-10-01)

A broker agent's `benefits-rfp` `CollectRequest` sent to the deployed A2A runtime now
opens the RFP on the shared durable exchange store, when the runtime has
`BRIDGE_RFP_PROCESS` set. The RFP id is the A2A context, and the agent gets back
`INPUT_REQUIRED` with every carrier × line cell `invited`. A follow-up on the same context
updates the requirements. The board host then drains carrier replies onto that RFP. See
`docs/features/email-intake.md` S0-arch-19 and `DECISIONS.md` 2026-10-01 S0-arch-19.

### S0-arch-15 — The core names no skill: skill-resolved `RfpPolicy` + a fenced demo-host package (2026-10-01)

**Problem.** `bridge/rfp.py` hardcoded `PROCESS = "benefits-rfp"` / `QUOTE_DOCTYPE = "benefits-quote"`, imported by the email intake, the carrier checklist and the demo host, and the demo host (with its seeding) shipped inside `bridge/dashboard/`. A second emergent-Collect use case would have needed a core edit.

**What was built.** See `design/architecture.md` "Core vs. demo hosts" and `DECISIONS.md` 2026-10-01 S0-arch-15.
- **`RfpPolicy` from the process skill.** `load_rfp_policy(skill_name)` (name now required) validates the frontmatter (`bridge-kind: process`, `bridge-pattern: collect`, non-empty `bridge-candidate-doctypes`) and adds `process`, `candidate_doctypes`, `item_field` (policy `rfp.item_field`, default `product_line`) and a `doctype_hint` property (the sole candidate, else `None`). Frontmatter is read by the pure `bridge.sla._load_skill_frontmatter`.
- **Consumers read the policy.** `receive` matches on `key_fields[policy.item_field]`; `to_exchange_turn` hints `policy.doctype_hint`; the email drain and the checklist upload gate on `policy.candidate_doctypes`; the checklist's upload action accepts that label space (`render_rfp_checklist(..., candidate_doctypes=…)`).
- **One process per board.** `RfpBoard.put` refuses an RFP whose `process` is not the board's.
- **Demo-host fence.** The local RFP demo host moved out of `bridge/dashboard/` to `bridge/demos/rfp_demo.py` (`DEMO_PROCESS` named there). Guard tests: nothing outside `bridge/demos/` imports it, and no core string constant equals an installed skill name (three allowlisted exceptions).
- **Reusability proof.** `tests/test_rfp_second_use_case.py` builds a synthetic `supplier-onboarding` skill in `tmp_path` (label space `w9-form` + `insurance-cert`, item key `credential`) and runs it through the aggregate, board, A2A turn, email drain, carrier checklist and dashboard with no `bridge/` change.

### S0-frontend-1 — RFP views: consumer status API, manual "chase now", carrier checklist (2026-09-30)

**Problem.** For the Benefits Consumer demo the Bridge had no RFP views: nothing a consumer could render on its own screen, no RFP matrix on the ops dashboard, and no carrier-facing checklist.

**What was built.** See `design/rfp-views.md` and `DECISIONS.md` 2026-09-30 S0-frontend-1.
- **Aggregate (`bridge/rfp.py`).** `RfpLeg.chases` / `last_chased_at` (kept across `update_requirements`), `chaseable_items`, `record_chase` (audit-only `chased` event; no cell moves) and `chase_message`. `RfpCarrierView` carries the chase count.
- **Outbound.** `Nudge.seq`: a manual chase is keyed `nudge:<leg ctx>:chase-<seq>`, so a second chase is a new send and a re-drive dedupes. The ladder key is unchanged.
- **Reply addressing.** `IntakeAddressing` (`bridge/email_ingress/correlation.py`): mailbox + secret → a leg's reply address and subject tag; `from_env()` reads `BRIDGE_EMAIL_MAILBOX` + `BRIDGE_EMAIL_TOKEN_SECRET`.
- **Consumer read API** (dashboard host, `bridge/dashboard/rfp_api.py`): `GET /rfps`, `GET /rfps/{id}` (the `RfpStatusView` plus `api_version` "1" and per-carrier `reply_address` / `reply_subject_tag`), and `POST /rfps/{id}/chase` (one carrier or every chaseable one; 404 / 409 / 502 / 503). The outbound client is injected or built lazily from the `a2a_client` seam.
- **Carrier checklist** (portal host, `bridge/portal/rfp_checklist.py`): `GET /rfp/legs/{context_id}`, filtered to that carrier only, and `POST /rfp/legs/{context_id}/uploads`. The upload files a quote into a named row through extract → gate → ledger → fresh read → acknowledge → `receive(product_line=…)` → put (404 / 409 / 422, board untouched on refusal).
- **Local demo host** (`bridge/demos/rfp_demo.py`): one seeded RFP (3 carriers × CI / HI, seeded through the real upload path) behind `/ops` + `/portal` on one shared board.
- **Frontend.**
  - `@docubridge/shared`: `RfpItemStatus`, `FieldReview` + `mapFieldReview`, `domain/rfp.ts` (`mapRfpStatus`, `isChaseable`), `RfpStatusBadge`.
  - ops-dashboard: `RfpExchangeView` (carriers × product lines, `data-highlight` overdue / escalated, per-carrier lifecycle, chase count, "Chase now" + "Chase all waiting", portal link) in an "RFP exchanges" section; `api.chaseRfp`.
  - provider-portal: `?leg=<context_id>` opens `RfpChecklist` (verbatim reasons and field reviews, reply address, artifact picker + upload); themeable via `VITE_PORTAL_ACCENT` / `VITE_PORTAL_TITLE`.

**Verification.** `tests/test_rfp_views.py` (29 tests) plus vitest coverage for the mappers, `RfpStatusBadge`, `RfpExchangeView` and `RfpChecklist`. T2 `/tmp/s0frontend1_t2.py` passes every check against the demo host: the seeded matrix and reply addresses; overdue derived on read with no re-put and escalation at due + 4 days; one chase Nudge scoped to carrier-c, `chase-1` on the second, 409 for a fully received carrier; carrier-b's checklist free of other carriers, 404 for a random leg; an upload turning a row received on `/ops/rfps` and in an `/ops/events` frame.

**Proposals / follow-ups.** See `DECISIONS.md`: a CUJ-9/13 Driver line; reconcile `spec/demos/rfp-demo.md`; the Benefits Consumer S0-frontend-12/16 hand-off; a ladder-armed automatic chase; an outbound invitation carrying the reply address; a durable RFP store; deployed reachability and auth for the consumer API; a real byte upload.

### S0-arch-13 — Email intake: a dedicated M365 mailbox, token-correlated, create-only landing (2026-09-30)

**Problem.** Carriers answer RFPs by email, and the Bridge had no inbound email channel. Email was only an advisory outbound nudge, and cold inbound push is deferred.

**Ownership.** The Bridge owns the edge: email is a collection channel under Benefits Consumer S0-arch-3's split, and the reply is solicited (correlated by token), not cold push. See `DECISIONS.md` 2026-09-30 S0-arch-13.

**What was built.**
- **A ninth seam, `email_ingress`** (`EmailIngressSeam`): `land` / `pending` / `read` / `put_attachment` / `mark_processed` / `processed` over one layout (`inbox/<key>.eml`, `attachments/<key>/<i>/<filename>`, `processed/<key>.json`). Every write is create-only:
  - `LocalEmailIngress` (`BRIDGE_EMAIL_DIR`): temp file + `os.link`.
  - `GcpEmailIngress` (`BRIDGE_EMAIL_BUCKET`): `if_generation_match=0`; 412 = duplicate, 403 = duplicate only if the object exists.
- **`bridge/email_ingress/`** (pure, no ADK):
  - `mime.py`: `parse_mime` with size/attachment limits, filename sanitizing, inline skipping, and one-level forward unwrapping; `landing_key`.
  - `correlation.py`: the HMAC token per (RFP, carrier), the plus-address + `[ref:token]` subject tag, constant-time `correlate`, `AmbiguousToken`.
  - `graph.py`: `GraphMailClient` (client-credentials token, streamed capped `$value` fetch, no redirects, subscribe/renew).
  - `__main__.py`: `land <file.eml>`, `subscribe --notification-url`, `renew <id>`, `drain --url <host>` (POSTs the served board-owning host's `/email/drain`; never drains in-process).
- **`bridge/edge/email_ingress.py`**:
  - `EmailProcessor`: drain → parse → dmarc → token → correlate → extract all (board untouched) → persist leg-scoped artifacts → fresh read + `acknowledge` / `receive` / `note_unreadable` → `board.put` → marker. At-least-once.
  - `build_email_app`: the Graph handshake, the clientState-authenticated notification route (fetch + land in a threadpool, 503 on transient), `POST /email/drain` and `GET /email/outcomes/{key}` (both only on a host that binds a processor; the public webhook serves neither).
  - `serve_email_webhook_container`: the public webhook-only entrypoint.
  - Board-owning host wiring: `email_processor_from_env`, `mount_email_drain_routes`, `email_drain_loop` (`BRIDGE_EMAIL_DRAIN_INTERVAL`, default 30 s, `0` = on demand). `build_dashboard_app(rfps=…)` binds the processor from env onto its own board with a periodic drain; the local RFP demo host runs the loop in its own lifespan.
- **`bridge/rfp.py` `note_unreadable`**: an audit-only `attachment_unreadable` event; no fabricated verdict.
- **Terraform**, gated on `enable_email_ingress` (default creates nothing):
  - the `bridge-email-webhook` SA (no project roles)
  - three secret containers (no versions)
  - the `<prefix>-email` bucket (UBLA, PAP, lifecycle Delete at `email_retention_days`; webhook objectCreator only, runtime objectViewer + objectCreator)
  - runtime envs (`BRIDGE_EMAIL_BUCKET`, `BRIDGE_EMAIL_MAILBOX`, the `BRIDGE_EMAIL_TOKEN_SECRET` secret ref)
  - the webhook-only Cloud Run service with its deliberate `allUsers` invoker
  - `tests/email.tftest.hcl` (10 runs).
- **Design:** `design/email-intake.md` (new), plus `seams.md`, `bridge.md`, `edges.md`, `gcp-substrate.md`.

**Verification.**
- T1: ruff clean. pytest 1063 passed / 41 skipped after the CRITICAL-review fixes (1046 before them; the new skips are live legs only). `-m seam` 12 / 11. `uv lock --check` OK. terraform fmt/validate clean; `terraform test` 31 passed.
- T2: `/tmp/s0arch13_t2.py` 9/9 PASS:
  - the handshake
  - notification → land → drain: 2 filed, xlsx unreadable, inline skipped; CI received, HI incomplete `flagged_fields`, carrier-b invited
  - a leg-scoped `ArtifactRef` (fetch 200 own / 404 foreign)
  - re-delivery is a duplicate and the board is unchanged
  - a spoofed clientState causes zero Graph traffic
  - a foreign-secret token is quarantined `no_exchange_token`
  - Graph 500 → 503
  - the CLI prints landed then duplicate
  - the terraform email runs pass
- Live Graph / GCS / Gemini legs are gated and deferred to the provisioned run.

**Proposals / follow-ups.** See `DECISIONS.md`:
- spec "nine seams"
- a CUJ Driver line
- an optional `wiki/evals/rfp` reply case
- the Benefits Consumer S0-infra-7 owner
- a deployed drain trigger and subscription auto-renewal
- a durable RFP store / claim
- an xlsx parser
- reply-address injection (S0-frontend-1)

### S0-bug-3 — Email processor bound to the board-owning host (served drain + poll loop) (2026-10-01)

**Bug.** `EmailProcessor` (built in S0-arch-13) was constructed nowhere outside tests. The deployed webhook lands MIME create-only and binds no processor by design; the runtime container builds only `build_a2a_app` with no RFP board; the CLI had no `drain` verb. Landed carrier replies were never correlated or filed on any real host.

**What was built.**
- **Board-owning host wiring** (`build_dashboard_app(rfps=…)` and `build_rfp_demo_app`): the processor binds where the board lives, wired from env via `email_processor_from_env` (mailbox + secret + landing location → processor; blank mailbox or missing landing → `None` for reply-addresses-only mode; missing secret → `RuntimeError`). The host serves `POST /email/drain` and `GET /email/outcomes/{key}` via `mount_email_drain_routes` (extracted from `build_email_app`, which now calls it — webhook-only hosts still 404 both routes).
- **Periodic drain loop** (`email_drain_loop`): `BRIDGE_EMAIL_DRAIN_INTERVAL` seconds (default 30, `0` = on-demand only). Runs as a FastAPI lifespan context manager; failed drains log and retry next tick; `EmailProcessor.drain` holds an `asyncio.Lock` so the loop and `POST /email/drain` serialize. The demo host (`rfp_demo`) runs the loop in its outer lifespan (Starlette never runs a mounted sub-app's lifespan).
- **CLI `drain` verb** (`python -m bridge.email_ingress drain --url <host>`): POSTs to the served host's `/email/drain` endpoint, prints one line per outcome (`filed <key> <rfp_id> <party> ...` or `quarantined <key> <reason>`), then `drained <count>`. Never drains in-process (an empty board would quarantine every mail as `no_exchange_token`).
- **Reply-address derivation**: when a processor is bound and `intake=None`, the dashboard shows `processor.intake` (new read-only `EmailProcessor.intake -> IntakeAddressing`) rather than `IntakeAddressing.from_env()` to ensure addresses match what correlates.
- **Circular-import fix**: `RfpBoard` import in `bridge/edge/email_ingress.py` moved to `TYPE_CHECKING` (annotation-only); dashboard builders import email_ingress names lazily. Guarded by fresh-process subprocess tests.
- **Terraform**: comment-only updates to clarify that the runtime's `BRIDGE_EMAIL_*` envs and bucket grants are for a board-owning host running as `bridge-runtime`, not the runtime itself (which holds no RFP board). No HCL semantics change; `email.tftest.hcl` still passes.

**Verification.**
- T1: ruff clean. pytest 1611 passed / 91 skipped (+21 new in `tests/test_email_served.py`, no new skips). terraform test 31 passed. Fresh-process imports succeed.
- T2 (real `uvicorn --factory bridge.demos.rfp_demo:build_rfp_demo_app`): carrier reply lands → `drain --url http://127.0.0.1:8010/ops` → `filed <key> acme-vb-2027 carrier-c filed=2 unreadable=1`; board shows carrier-c CRITICAL_ILLNESS received, HOSPITAL_INDEMNITY incomplete; poll loop (`BRIDGE_EMAIL_DRAIN_INTERVAL=1`) drains automatically within 3 s. Webhook-only host still 404s drain.

**Follow-up.** S0-arch-17: a deployed board-owning host binds the processor with a Cloud Scheduler or GCS-finalize trigger on `/email/drain` (needs S0-arch-14 durable RFP store).

### S0-arch-11 — `benefits-rfp` collection process (2026-09-30)

**Problem.** The Benefits Consumer exec asked "which documents are ready, which are waiting on a carrier?" and the Bridge had no answer. CUJ-9/13 (RFP) was unbuilt.

**What was built.**
- **Contract 1.2** (additive, every field defaulted):
  - `Requirement.party`: the carrier the item awaits.
  - `CollectRequest.requirements` (distribution list × product lines) and `CollectRequest.due_at`.
  - New enum `RfpItemStatus`: invited · acknowledged · received · incomplete · declined · overdue.
- **Skill `skills/benefits-rfp/`** (process, `bridge-pattern: collect`, candidate doctype `benefits-quote`). `assets/policy.yaml` sets:
  - `disposition` with `max_resubmissions: 3`
  - `sla` of overdue 1 / reminder 3 / escalate 5 ticks
  - `rfp.tick_seconds: 86400`
- **`bridge/rfp.py`**: a pure, immutable aggregate, replace-and-return in the `bridge.negotiate` style.
  - `open_rfp`: one isolated context per carrier via `bridge.program.fan_out`.
  - `update_requirements`: CUJ-13 emergent add/waive. It never drops a cell.
  - `acknowledge`, `decline`, `receive`: takes a `LedgerEntry`. Accepted → received plus a version; otherwise incomplete. Past the resubmit cap the cell escalates, on the same boundary as `bridge.pathb`.
  - `rfp_status`: an `RfpStatusView` holding per-cell, per-carrier and overall lifecycle, ready/waiting lists and counts.
  - `to_exchange_turn`: `status.outstanding` holds one `Requirement(item=product_line, party=carrier, reason_code=<RfpItemStatus>)` per tracked cell not yet received.
- **Dashboard**: `RfpBoard`, projected on read with an injectable clock, like `ProgramBoard`. `project_rfp_exchange` builds the row.
  - `list_exchanges`, the queues, `readmodel_snapshot`, `sse_frames` and `build_dashboard_app` take `rfps=`. With `None` the output is byte-identical.
  - `/exchanges/{rfp_id}` returns the full matrix.

**Key approach.**
- **The due date anchors the SLA ladder**, measured in `rfp.tick_seconds`: overdue at due, reminder at due+2d, escalated at due+4d.
  - Status is derived on read as a pure function of `now`, so no alarm has to fire for the status to be right.
- **The Bridge never mints `done`.** Declined cells stay outstanding, and waiving them is the app's call (sense B).

**T2 evidence.**
- Setup: a synthetic 3-carrier × 2-product RFP, due 2026-10-15, run through the live uvicorn dashboard.
- **due−1d:** on_track, 3 ready / 3 waiting.
  - carrier-a: both products received.
  - carrier-b: both acknowledged.
  - carrier-c: LIFE received; AD_AND_D declined ("AD&D not offered.").
- **due:** carrier-b's cells are overdue, and so is the overall lifecycle.
  - The `ExchangeTurn` lists carrier-b LIFE/AD_AND_D as `overdue` and carrier-c AD_AND_D as `declined`, with done=false, terminal=false and contract 1.2.
- **due+4d:** carrier-b is escalated, and so is the overall lifecycle.
  - `GET /exchanges` shows `escalated: true` and counts {received 3, overdue 2, declined 1}.
  - The escalation queue is `['acme-vb-2027']`; at due−1d it was empty.
- The corpus end-to-end test runs real `FixtureExtractor` → dispose → ledger → `receive` over the benefits quotes.
- T1: ruff clean; pytest 856 passed / 31 skipped (34 new in `tests/test_rfp.py`).

### S0-arch-6 — A native-digital PDF in the benefits corpus (gemini extraction path)

Proves the native-digital PDF extraction path by adding a real, deterministic synthetic
carrier-quote PDF to the benefits corpus and driving it through both the fixture and
gemini extraction legs to the same terminal outcome. The PDF is generated from corpus
values using only stdlib code and verified with a byte-equality drift guard.

- **`tests/corpus_pdf.py`** (new) — deterministic stdlib PDF writer with no new dependencies
  (`reportlab`/`pypdf` were not added). Renders the corpus record's own `key_fields` into
  an uncompressed, deterministic (no `/CreationDate`, no `/ID`) PDF page. Lives in `tests/`
  because the corpus consumers do; `bridge/` does not depend on it. Regenerates with
  `uv run python -m tests.corpus_pdf`. Money prints as `EUR 1000.00` (ASCII code + dot
  decimal) instead of `1 000,00 €` because `bridge/normalize.py::_convert_money` strips
  currency symbols but not ISO-4217 codes or comma decimals. Free text prints the corpus
  values byte-for-byte (the `TRANSLATIONS` table is case-sensitive).
- **`wiki/evals/benefits/images/benefits-carrier-c-eur-fr.pdf`** (new, 1186 bytes) — the
  committed synthetic asset. Attached to the **existing** `benefits-carrier-c-eur-fr` record
  via an additive `"artifact"` key (avoiding a sixth document that would break the four-leg
  demo narrative). Carrier C chosen because EUR/fr exercises the full FX + translation path.
- **`wiki/evals/benefits/expected.json`** (corpus) — added `"artifact":
  "images/benefits-carrier-c-eur-fr.pdf"` to the carrier-c record; corrected the stale
  top-level note that said `images/` was deferred. The shared artifact name IS the parity
  pivot: `FixtureExtractor._normalize_key` strips `.pdf`, so both legs accept the same
  string. Values unchanged; the edit is purely additive provenance.
- **`wiki/evals/benefits/description.md`** (corpus doc) — updated the inputs table and Files
  section to reflect the committed PDF. States the boundary: **native-digital** PDF only;
  scanned/raster (OCR) is the deferred Document AI adapter.
- **`tests/test_pdf_extraction.py`** (new) — 7 credential-free tests + 1 gated live test.
  Guards: the PDF is a real parseable PDF, the generator reproduces it byte-for-byte (drift
  guard), the corpus resolver reads it as `application/pdf`, it rides as a `Part` with
  `inline_data` (not the SVG text branch), both legs reach the same terminal outcome
  (identical `expected_normalized` / `expected_gaps` / `ROUTE_NONE`, confidences deliberately
  not compared). The live test canonicalizes rendering variance (strips currency code,
  lowercases free text) in the TEST only — `bridge/normalize.py`, the oracle, and the
  `TRANSLATIONS` table are untouched.
- **`bridge/extraction/gemini.py`** — **docstrings only**, zero logic change. Extended
  `_document_part` docstring: SVG stays a text part; every other mime (notably
  `application/pdf`) rides as inline bytes. States the boundary: a **scanned/raster** PDF
  (no text layer) is the deferred **Document AI** adapter.
- **`design/seams.md`** — one status-free sentence: the Gemini engine takes native-digital
  documents (text SVG, images, native-digital PDF); scanned/raster PDF (OCR) is the Document
  AI adapter's envelope.

**Key decisions.** (1) Attached to an existing record, not a new document — a sixth doc
would break the four-leg assertions and demo narrative. (2) Carrier C (EUR/fr) exercises
full FX + translation. (3) A stdlib generator with byte-equality guard instead of a binary
blob or a new dependency. (4) `_document_part` logic unchanged; only docstrings updated.
(5) Scanned/OCR PDF explicitly deferred and documented everywhere (no silent cap).
Resolving a PDF through `bridge/edge/artifacts.py::resolve_bytes` (S0-arch-7, Address
corpus) and the whole-journey A2A conformance run (S0-arch-9) are out of scope.

**Verification.** T1: full suite green (715 passed / 30 skipped, baseline 708/29 + 7 new
credential-free + 1 gated), `ruff check` clean, `-m seam` unchanged. T2: `pdftotext` prints
the quote with accents intact; `uv run python -m tests.corpus_pdf` + `cmp` is BYTE-STABLE;
fake-client run prints `mime: application/pdf bytes: 1186 doctype: benefits-quote`; both
legs reproduce `expected_normalized` (premium 1100.00, deductible 550.00, USD/en) with no
gaps. Live gated leg not run (no credentials).

_Added 2026-09-23._

### S1-benefits-7 — Program comparison A2UI view (beat 4 UI · CUJ-11)

The program comparison view — a **side-by-side, N-legs-in-one-currency-and-schema display
with gaps flagged, recomputing live as revised versions land**. The data aggregate already
existed (`bridge/comparison.py` from S1-benefits-4), so this task adds the **read-model
surface** (Ops Dashboard backend routes + SSE snapshot) and the **frontend view** that
renders it.

- **`bridge/dashboard/board.py`** (new) — the injectable in-memory program board mirroring
  the dashboard's existing injectable `PortalStore` posture. `ProgramBoard` holds a
  `standard: ProgramStandard` + `programs: dict[str, NegotiationProgram]` (insertion-ordered).
  `put(program)` upserts by `program_id`; `comparisons()` recomputes on every read via
  `[comparison_from_negotiation(p, self.standard) for p in self.programs.values()]` — this
  **recompute-on-read design** is what satisfies "recomputes live as revised versions land"
  (a `board.put(revised_program)` is reflected on the next GET). Module docstring notes a
  **durable** program store is deferred-not-dropped to a later cycle (no silent cap).
- **`bridge/dashboard/readmodel.py`** extended — added `project_comparison(comparison)` returning
  `comparison.model_dump(mode="json")` (snake_case + JSON-ready; gaps + quote come through
  verbatim — content-not-pixels relay). Extended `readmodel_snapshot(store, *, board=None)`:
  when `board is not None`, adds a `"programs"` key; **when `board is None` returns
  byte-identically to today** (no key added) so the existing P6-4 dashboard suite stayed
  unchanged. `sse_frames` threads `board` through; diffing re-reads the snapshot each tick,
  so a board mutation (a revise landing) surfaces automatically.
- **`bridge/dashboard/server.py`** extended — `build_dashboard_app(*, store=None, board=None, ...)`;
  `GET /programs` → `[project_comparison(c) for c in board.comparisons()]` (empty list when
  board is None); `GET /programs/{program_id}` → single recompute (404 on unknown program);
  `GET /events` passes `board=board` into `sse_frames`.
- **`frontend/ops-dashboard/src/domain/ops.ts`** — wire (snake_case) types mirroring
  `ProgramComparison.model_dump(mode="json")` (`WireComparison`, `WireComparisonLeg`,
  `WireNormalizedQuote`, `WireGapVerdict`, `WireStandardGap`) + camelCase view types +
  `mapComparison` snake→camel mapper. Extended `WireSnapshot`/`Snapshot` with `programs`;
  `mapSnapshot` maps them via `(wire.programs ?? []).map(mapComparison)`.
- **`frontend/ops-dashboard/src/components/ProgramComparison.tsx`** (new) — the view: props
  `{ programs: Comparison[] }`, renders each program as a **legs-side-by-side table** (one
  column per carrier, rows = comparable fields). Stamps the canonical basis (`currency` /
  `language`) in the header. **Gaps flagged:** when `verdict.route === "negotiate"` shows a
  warning chip + lists each gap's `message` / `rule_id` **verbatim** (content-not-pixels —
  relays the Bridge's gap text, authors no prose). Conformant legs show a "conformant"
  marker. Pure props-driven component (no fetch inside).
- **`frontend/ops-dashboard/src/App.tsx`** extended — renders
  `<ProgramComparison programs={snapshot?.programs ?? []} />` in a full-width row. The App
  re-renders on each SSE snapshot, so the view updates live as revisions land (no extra
  wiring needed).
- **Tests** — `tests/test_program_comparison_view.py` is the hard `uv run pytest` gate:
  builds a negotiation identical to `test_cuj11_negotiate_bind_rollup.py` (4 legs,
  carrier-b flagged `no_orthodontics_exclusion`), asserts `GET /programs` → 4 legs all
  `currency=="USD"` / `language=="en"`, carrier-b `route=="negotiate"` with the gap,
  carrier-a/c/d conformant. Gate B (recomputes live): `revise(neg, "carrier-b", <v2>, ...)`
  → `board.put(revised_neg)` → `GET /programs` again shows carrier-b conformant (all four
  `route=="none"`). Gate D: SSE `GET /events` reflects the recompute across frames. Gate E:
  existing `test_dashboard.py` (board=None path) stayed green. Component test
  `ProgramComparison.test.tsx` (vitest) renders 4 legs + gap flag, then post-revise
  conformant render (no flag).

**Home = Ops Dashboard, not provider portal.** Per `design/frontend.md` "Surface
capabilities by demo": Benefits → "dashboard **program comparison view**". Only the provider
portal is a true A2UI (Path-B human-input) host; the two internal surfaces (console,
dashboard) are showcase observability over SSE, not A2UI protocol. The task title says
"A2UI view" loosely — the concrete home is the internal dashboard read-model.

**Verification.** T1: full suite green (602→608 passed, 13 skipped); `ruff check` clean;
isolation guard green; `pnpm --filter ops-dashboard build` + `pnpm --filter ops-dashboard test`
green. T2: `pytest tests/test_program_comparison_view.py -q` → all gates pass (4 legs USD/en,
carrier-b flagged before revise / conformant after, SSE reflects recompute, 404 on unknown
program); frontend test green (4 legs side by side, gap flag + verbatim message, post-revise
conformant). One-shot acceptance snippet (end-to-end print over the wire) shows
`basis: USD en | flagged: [('carrier-b', ['no_orthodontics_exclusion'])]` → after revise
all routes `none`.

**CRITICAL escalation.** Touches `bridge/dashboard/` (new `board.py`, edits to
`readmodel.py`/`server.py`/`__init__.py`) → auto-escalates. Staged path-scoped; the
unrelated pre-existing `spec/open-questions.md` edit left alone.

_Added 2026-09-07._

### S1-benefits-6 — Cross-framework carriers as real A2A servers (CUJ-12)

The two simulated benefits carriers stood up as genuine external-zone A2A servers under
`agents/benefits/` — **Carrier A in Google ADK**, **Carrier B in LangGraph** — proving
the SAME A2A quote conversation succeeds against both builds, including a multi-turn
revise loop the LangGraph carrier drives (it holds the coverage gap). The literal CUJ-12
claim: an ADK consumer negotiates with a LangGraph counterparty over A2A.

- **`agents/benefits/carrier_a_adk.py`** — Carrier A's internal is a deterministic
  `google.adk` `BaseAgent` (NO `LlmAgent`, no Gemini/API key — the model-free testable-agent
  posture of `bridge/edge/executor.py`) whose `_run_async_impl` yields the quote as JSON,
  hosted through a stock ADK `Runner` + `InMemorySessionService`. `CarrierAExecutor`
  (a2a-sdk `AgentExecutor`) runs it and emits a `benefits-quote` artifact. Conformant and
  stateless: re-emits the same quote on a revise turn (A has no gap → never pushed back on).
- **`agents/benefits/carrier_b_langgraph.py`** — Carrier B's internal is a compiled
  `langgraph` `StateGraph`: a classifier conditional edge routes a first quote request to
  `quote_v0` (returns `benefits-carrier-b-usd-en`, holds the orthodontics gap) and a revise
  pushback to `revise_v2` (returns `benefits-carrier-b-usd-en-v2`, gap closed). Nodes are
  pure Python, no LLM; the graph is compiled once at import. `CarrierBExecutor` classifies
  the inbound turn, invokes the graph, and emits the quote — the SAME wire shape A emits.
- **`agents/benefits/wire.py`** — self-contained a2a + contract glue (never imports
  `bridge/`): encodes a quote as one `benefits-quote` JSON data Part tagged `metadata.kind`
  (select-by-metadata, D5), decodes it back to `contract.ExtractionResult`, and classifies a
  turn as `quote` vs `revise` off a `revise` marker in the text. Stateless classification
  keeps both carriers deterministic (no server-side turn counter).
- **`agents/benefits/quotes.py`** — both carriers serve quotes **read-only** from the single
  Benefits eval oracle (`wiki/evals/benefits/expected.json`); no quote data is hardcoded or
  duplicated, and the corpus is unchanged this task.
- **`agents/benefits/server.py`** — re-implements the minimal a2a-sdk 1.1.x server assembly
  (card routes + JSON-RPC routes over a `DefaultRequestHandler` + `InMemoryTaskStore` on a
  `Starlette` app) self-contained in the external `agents/` zone, because `agents/` must never
  import `bridge/` (the reverse isolation guard stays green). Loopback serve default (S0-sec-3).
- **Real dial + live counterparty, not the P7 transport.** -6's "real dial + live
  counterparty" is embodied as these external-zone carrier servers + an ADK `RemoteA2aAgent`
  consumer (use_legacy=True, TEST-4 pin) over an in-process `httpx.ASGITransport` — no
  `bridge/outbound.py` / `bridge/negotiate.py` change (Bridge-core authenticated egress
  transport remains P7; their deferral docstrings are untouched, no silent cap).
- **Dependency.** Added `langgraph>=1.2,<2` (resolved 1.2.11) — resolves additively, no
  ADK/a2a downgrade.
- **Gotcha.** ADK `RemoteA2aAgent` rejects a non-loopback card RPC URL; the ADK-consumer test
  dials Carrier B under `http://localhost`. A generic `application/json` A2A data part
  surfaces through `RemoteA2aAgent` wrapped in `<a2a_datapart_json>…</a2a_datapart_json>`;
  the test reuses `agents/address/wire.py::decode_part_payload` to decode it.

**Verification.** T1: full suite green (602 passed, 13 GCP-gated skips), `ruff check` clean,
isolation guard green (`agents/benefits/` imports no `bridge/`). T2: `tests/test_cuj12_interop.py`
all 6 pass — same-conversation parametrized over both builds (each reproduces its corpus
`expected_normalized`), LangGraph multi-turn revise (turn 1 v0 → `negotiate`
`no_orthodontics_exclusion`; turn 2 same `context_id` → v2 `none`, responses provably
differ), ADK-consumer-dials-LangGraph (decodes to Carrier B v0), framework-diversity
structural guard (A is a `google.adk` `BaseAgent`, B is a compiled LangGraph), and
no-token-on-wire. Deterministic — no Gemini/API keys.

_Added 2026-09-06._

### S1-benefits-5 — Negotiate + versioned revise loop → bind → rollup (beats 5,6 · CUJ-11)

The CUJ-11 payoff tail: versioned revise loop, per-leg bind (sense B), program rollup,
and audit trail. Shipped as one cohesive Bridge-side aggregate composing the prior beats
(program, normalize, standard, comparison) with no `contract/` change and no new `Seam`.

- **`bridge/negotiate.py`** — the whole beats-5,6 surface in one module: domain-object-level
  versioned revise loop (a leg holds an ordered tuple of `QuoteVersion`s; attempt N is
  version N, v0 initial / v1 first revision per `design/artifacts.md`), comparison
  recompute (`comparison_from_negotiation` pulls each leg's current version and reuses
  `build_comparison` — flagging is not reimplemented), per-leg sense-B bind (recording-only,
  no accept/reject gate, non-conformant binds allowed), program rollup, append-only audit
  trail (monotonic `seq`, kinds `leg_opened`/`quote_revised`/`reflagged`/`leg_bound`), and
  `negotiate:` policy loader. Every function is pure and returns new immutable state (the
  `program.py` replace-and-return idiom). Pure imports only: stdlib + pydantic +
  `bridge.normalize`/`standard`/`comparison` + `bridge.sla._load_policy_doc` + escalate
  constants; NEVER `agents/` (isolation guard green).
- **One-module choice.** bind / rollup / audit / revise all operate on the same
  versioned-leg state, so one cohesive aggregate is the right seam (not four modules).
  Cohesive because bind/rollup/audit all operate on the same versioned-leg state.
- **Policy loader reuses `bridge.sla._load_policy_doc`** (the same trick
  `bridge/standard.py` used) so `bridge/sla.py` stayed off the change surface.
  `load_negotiate_policy` reads the `negotiate:` block from
  `skills/benefits-procurement/assets/policy.yaml` and validates `revise_cap` is an int
  `>= 1`, raising a `ValueError` naming the skill on a missing/invalid block. The
  benefits-procurement policy has `revise_cap == 2`.
- **Cap → escalate, never reject (sourced, not restated).** `revise()` enforces
  `revise_cap`; exceeding it raises `ReviseCapExceeded(ValueError)` whose `reason_code` /
  `message` are sourced from `bridge.offscript.REASON_ESCALATED` /
  `bridge.pathb.ESCALATE_MESSAGE`, so the wording can't drift from the Path-B engine. This
  is a route-to-human escalate, not a reject and not a silent crash (the same sourcing
  S0-debt-1 used to prove the §2b cap→escalate boundary). The golden run revises once
  (within cap = 2); unit tests and T2 acceptance drive the boundary.
- **Bind is recording-only (sense B).** `bind_leg` records the agent's bind of a chosen
  version (default = current/latest); the Bridge records, never decides — no
  accept/reject gate, no conformance enforcement. Carries an advisory `conformant` flag
  copied from that version's verdict, but does not block a non-conformant bind (mirrors
  the sense-A/sense-B split in `bridge/standard.py` and `design/disposition.md`).
- **Corpus revised-doc authored (`wiki/evals/benefits/expected.json`).** Added carrier-b's
  revised quote `benefits-carrier-b-usd-en-v2`: identical to the v0 carrier-b doc except
  `exclusions: "cosmetic surgery"` (drops orthodontics), with its authored `extraction` /
  `expected_normalized` / `expected_gaps: []`. Authored to correct values, not fitted to a
  test; the four existing docs are byte-for-byte unchanged. The additive fifth doc
  required updating the beats-2/3/4 corpus-oracle tests to filter out the `-v2` revision
  so a program still has four legs (a revision is a new version of a leg, not a fifth leg).
- **Over-the-wire pushback deferred to S1-benefits-6 (logged, not silent).** This task
  builds the revise-loop machinery Bridge-side and drives it off the corpus revised quote;
  the real multi-turn A2A send + a live counterparty that returns the revision belong to
  -6 (ADK Carrier A / LangGraph Carrier B). The pushback is deliberately NOT routed
  through `OutboundClient.dial` this task: `DialOut.idempotency_key` is
  `dial:{context_id}`, so a second dial to the same leg dedupes as `resent` — wrong
  semantics for a distinct negotiate turn. `bridge/outbound.py` was left untouched; the
  deferral is logged in the `bridge/negotiate.py` module docstring.

**Verification.** T1: full suite green (596 passed, 13 skipped), `ruff check` clean,
isolation guard intact. T2: CUJ-11 golden run
(`tests/test_cuj11_negotiate_bind_rollup.py`) green; the recompute+bind+rollup snippet
shows carrier-b flagged before / all `ROUTE_NONE` after, bound at version 1, audit
carries `quote_revised` + `leg_bound`; the cap snippet escalates with
`reason_code == escalated_for_review`; the corpus diff is additive only.

**CRITICAL escalation.** Touches `bridge/` (new `bridge/negotiate.py`, edit
`bridge/__init__.py`) + `wiki/evals/` (additive revised-quote oracle) → auto-escalates
(stronger model + mandatory review before commit). Staged path-scoped; the unrelated
pre-existing `spec/open-questions.md` edit left alone.

_Added 2026-09-06._

### S1-benefits-3 — Declared-rule standard-gap disposition, sense A (beat 4 · CUJ-11)

The standard-gap flagger: flag each **normalized** quote against the declared,
agent-overridable program-standard rules; a flagged gap **routes to negotiate**. Sense A —
the Bridge SURFACES the gap, it never decides the deal (bind is the agent's sense-B call in
S1-benefits-5).

- **`bridge/standard.py`** — a pure, synchronous module (imports stdlib + pydantic + pyyaml
  + `bridge.sla` (`SKILLS_DIR`) + `bridge.normalize.NormalizedQuote` only; isolation guard
  intact — no `google.adk`/`a2a`/`agents/`). Public API: `StandardRule` (frozen dataclass,
  `from_dict` parses + validates a YAML rule), `ProgramStandard` (frozen dataclass: rules +
  `overridable`), `load_program_standard(skill_name)`, `StandardGap` / `StandardGapVerdict`
  (Pydantic `extra="forbid"`), `flag_quote`, `flag_program`. Loads `standard.yaml` via the
  SKILL.md `metadata.bridge-program-standard` pointer (local `_load_standard_doc` mirroring
  `bridge.sla._load_policy_doc` — `sla.py` left untouched).
- **Verdict shape / sense A.** `StandardGapVerdict` fields are exactly
  `{party, conformant, gaps, route}` — deliberately NO accept/reject/bind. `route` is only
  ever `ROUTE_NEGOTIATE` ("negotiate", gaps present) or `ROUTE_NONE` ("none", conformant).
  Route stays a Bridge-internal string constant; promoting it to a `contract/` enum (so it
  rides the A2A wire to trigger dial-out/Negotiate) is **deferred to S1-benefits-4/5**.
- **Finalized check vocabulary (three verbs):** `max` (numeric ceiling; parses the field via
  the `normalize._convert_money` cleaning idiom → `Decimal`, gap iff value > limit),
  `must_include` (substring present, case-insensitive), `must_not_include` (substring
  absent). Unknown `check` (at load) or unknown `field` (not a `NormalizedQuote` attr, at
  eval) degrades LOUDLY with a `ValueError` naming it — never a silent no-op.
- **Rule fields re-pointed to `NormalizedQuote` attrs** (`deductible`, `coverage_limits`,
  `exclusions`) in `skills/benefits-procurement/assets/standard.yaml` — flagging runs
  POST-normalization (one currency/language), so a numeric cap is comparable across carriers
  that quoted in different currencies. Added the **`no_orthodontics_exclusion`** rule
  (`must_not_include: orthodontics`): the previous two rules flag NOBODY (all 4 carriers have
  deductible ≤ 1000 and include "dental"), but beat 4 needs Carrier B to show *the* coverage
  gap — Carrier B is the sole carrier excluding orthodontics, so this rule isolates it as the
  one flagged leg (the ADK/LangGraph gap-holder that drives the revise loop). `overridable:
  true` retained (agent may swap the standard per client).
- **Beat-4 corpus oracle (`wiki/evals/benefits/expected.json`)** — added an `expected_gaps`
  array to each of the 4 documents (the violated `rule_id`s under the default standard):
  carrier-a/c/d → `[]`, carrier-b → `["no_orthodontics_exclusion"]`. Additive only — the
  S1-benefits-2 `expected_normalized`/`extraction` values were left intact. The corpus stays
  the oracle: `tests/test_standard.py::TestCorpusOracle` iterates it, normalizes + flags each
  quote, and asserts violated rule_ids == `expected_gaps`, plus that Carrier B alone routes
  to negotiate (`flag_program` → 1 negotiate, 3 none).
- **Deferred, not dropped (no silent cap):** `route` as a `contract/` wire enum
  (S1-benefits-4/5); the versioned re-flag on a revised quote (S1-benefits-5); an
  agent-override *channel* (supported by construction — `flag_quote` takes an explicit
  `ProgramStandard` — but wiring the channel is out of scope). No seam / no `contract/`
  change: a pure Bridge transform consuming `NormalizedQuote` + a declared skill asset.

**Verification.** T1: full suite green (566 passed, 13 skipped), `ruff check` clean,
isolation guard intact. T2: the flagger over the real corpus prints exactly
`carrier-a True none []` / `carrier-b False negotiate ['no_orthodontics_exclusion']` /
`carrier-c True none []` / `carrier-d True none []`; `TestCorpusOracle` green; verdict
carries only `{party, conformant, gaps, route}`; loader returns the 3 rules + `overridable`.

**CRITICAL escalation.** Touches `bridge/` (new `bridge/standard.py`) + `wiki/evals/`
(added `expected_gaps` oracle field) → auto-escalates. Stronger model + mandatory review
before commit. Staged path-scoped; the unrelated pre-existing `spec/open-questions.md` edit
left alone.

_Added 2026-09-06._

### S1-benefits-4 — Dial-out + Deliver (beat 2 · CUJ-3)

The two remaining outbound activities on the existing `a2a_client` seam: **dial-out**
(Bridge-as-client originates an A2A exchange: Agent-Card discovery → `message/send`) and
**Deliver** (push the finished normalized comparison outward by reference). First concrete
use of the designed-but-unbuilt dial-out/Deliver flows.

- **`bridge/outbound.py` extended** — added `DialOut` / `Delivery` frozen dataclasses (no
  bearer token field, mirroring the existing `Nudge`), generalized `OutboundClient` to
  dispatch any `OutboundMessage` (a `runtime_checkable` Protocol exposing `idempotency_key`)
  through one private `_dispatch()` so crash-safe re-drive semantics are identical across
  `send` / `dial` / `deliver`. The `nudge:` / `dial:` / `deliver:` key prefixes namespace
  the receipt store so keys never collide. Added `PeerCard` / `PeerDirectory` (discovery
  protocol) + `FakePeerDirectory` (local fake injected directory, mirroring `FakeTransport`).
  Kept `send(nudge)` / `NudgeReceipt` bit-for-bit unchanged — existing nudge tests stayed
  green.
- **Discovery gates, not decorates.** `dial()` resolves the peer `PeerCard` via the injected
  `PeerDirectory` and **rejects** (`ValueError`) a peer that does not advertise the requested
  `process` — the "Agent-Card discovery" half of the task. Without it the task degenerates
  to a blind send; the rejection path is tested.
- **`bridge/comparison.py` (new)** — the apples-to-apples aggregate that Deliver pushes.
  `ProgramComparison` (Pydantic `extra="forbid"`) composes S1-benefits-2 normalize +
  S1-benefits-3 flag: `program_id`, `currency` (USD), `language` (en), `legs` (each leg:
  `party`, `quote: NormalizedQuote`, `verdict: StandardGapVerdict`). Pure module (imports
  pydantic + `bridge.normalize` + `bridge.standard` only). The **data** aggregate; the A2UI
  view rendering it is S1-benefits-7.
- **Kept off the critical path.** No `contract/` change (Deliver reuses the existing
  `contract.wire.ArtifactRef` by-reference shape — bytes never travel on the wire); no
  `wiki/evals/` change (corpus already carries `expected_normalized` + `expected_gaps`; the
  comparison composes them). The comparison is a **Bridge-side** model, not a wire type —
  same posture as S1-benefits-2/3.
- **No bearer token — everywhere.** `DialOut` and `Delivery` carry no token/credential
  field; tested by dataclass-field assertions mirroring `test_nudge_carries_no_bearer_token`.
- **Deferred, not dropped (no silent cap).** The **real** ADK dial via `RemoteA2aAgent` +
  consuming a counterparty `input-required` park → **S1-benefits-6** (this task dials a
  simulated counterparty over the fake transport); the **authenticated egress transport**
  (workload Agent Identity, card-vs-Gateway-host reconciliation) → **P7 GCP adapter**; the
  **multi-turn Negotiate revise loop** + versioned artifacts + bind/rollup →
  **S1-benefits-5** (this task delivers the *initial* comparison only). Logged in the
  `bridge/outbound.py` module docstring.

**Verification.** T1: full suite green (581 passed, 13 skipped), `ruff check` clean,
isolation guard intact. T2: dial-out idempotent re-drive (`resent` True, outbox length
unchanged), discovery-gate rejection (`ValueError` on unadvertised process), CUJ-3 golden
run (4 dial receipts + 1 Deliver receipt), comparison reproduces corpus oracle (carrier-b
flagged `no_orthodontics_exclusion`, all legs USD/en), artifact by-reference (uri set, no
bytes).

**CRITICAL escalation.** Touches `bridge/` (new `bridge/comparison.py`; edits to
`bridge/outbound.py`, `bridge/__init__.py`) → auto-escalates. Stronger model + mandatory
review before commit. Staged path-scoped; the unrelated pre-existing `spec/open-questions.md`
edit left alone.

_Added 2026-09-06._

### S1-benefits-1 — Benefits skills + Programs (beat 1 · CUJ-11)

The two Benefits skills and the Program fan-out aggregate — config + a small pure
Bridge aggregate only. Deliberately no extraction, FX, standard-gap flagger, dial-out,
Negotiate, bind, or rollup (those are S1-benefits-2..7 and consume what this lays down).

- **Doctype skill `benefits-quote`** (`skills/benefits-quote/`): schema
  (`assets/schema.json`) with required `doctype`/`currency`/`language` + `key_fields`
  (premium, deductible, coverage_limits, exclusions, effective_date, expiry_date; string
  values — the wire `key_fields` is `dict[str,str]`), a prose extraction prompt, and
  `assets/validation.yaml` (dated + currency-present rules; bridge/agent signal split
  mirroring `utility-bill`).
- **Process skill `benefits-procurement`** (`skills/benefits-procurement/`): the
  **Negotiate** pattern over a program. `candidate_doctypes: benefits-quote`, a prose
  satisfaction description (agent owns bind — sense B), `assets/policy.yaml` (disposition
  thresholds + a valid `sla:` block satisfying `load_sla_policy` invariants + a new
  `negotiate.revise_cap` consumed by S1-benefits-5), and `assets/standard.yaml` (the
  declared, agent-overridable program-standard rules the comparison will flag against —
  consumed by S1-benefits-3; the flagger is NOT built here).
- **Naming decision (load-bearing):** the spec names BOTH the process and doctype skill
  `benefits-quote`, but the flat skill registry keys by directory name and requires
  `SKILL.md name == dir name`, so two folders can't share a name. Resolution: keep the
  **doctype** skill `benefits-quote` (it is the classification/extraction label carried in
  `candidate_doctypes`, the card's `doctype:` tag, and the eval corpus) and name the
  **process** skill `benefits-procurement`. `CollectRequest.process` for this exchange is
  `"benefits-procurement"`. (Spec-rename proposal recorded in `DECISIONS.md`.)
- **Program aggregate** (`bridge/program.py`): a pure, in-memory, side-effect-free Bridge
  aggregate — `Leg(party, context_id)`, `Program(program_id, legs)`, `open_program`,
  `add_leg`, `fan_out`. Beat-1 scope is fan-out + the **isolation invariant**: N legs →
  N distinct minted `context_id`s, each party a distinct leg/context (competitors never
  share a context); duplicate party raises `ValueError`. No seam, no persistence, no ADK,
  no wire type.
- **Deferred, not dropped (no silent cap):** `program_id` on the wire (adding it to
  `contract.CollectRequest`) is deferred to S1-benefits-4 (dial-out fan-out); per-leg
  bind / program rollup / a materialized exchange record are S1-benefits-5. Program
  identity stays a Bridge-side aggregate this task; it does not ride the wire yet.
- **Intake regression handled (deviation from plan):** adding a second **process** skill
  removed `parse_text_request`'s "sole process skill → default" fallback, so
  scenario-only free-text seeds (`scenario:<name>`) could no longer resolve a process on
  the real edge. Fixed at the seed source (`agents/address/driver.py::_intake_text`) so
  the seed text carries BOTH the process token (real edge resolves it) and the scenario
  marker (mock keys off it) — restoring mock→real parity rather than reintroducing a
  guess-the-process fallback. Full suite green (539 passed).

_Added 2026-09-06._

### S1-benefits-2 — Multilingual extraction + FX normalization (beat 3 · CUJ-11)

The normalize layer that maps N messy, multi-currency, multi-language carrier quotes to
one apples-to-apples schema for comparison and gap-flagging.

- **`bridge/normalize.py`** — a pure, side-effect-free normalization module. Public API:
  `NormalizedQuote` (Pydantic model), `normalize_quote`, `normalize_from_extraction`.
  Target schema: USD currency, English language, provenance preserved (`original_currency`,
  `original_language`). Fixed FX rate table (`USD 1.00, EUR 1.10, GBP 1.25`) drives the
  deterministic demo; translation dictionaries for synthetic fr/de coverage_limits +
  exclusions. Monetary fields kept as strings (2-dp precision) for lossless corpus
  round-trip. Module is synchronous/pure (imports stdlib + pydantic + `contract/` only —
  isolation guard enforced).
- **FX swap point (deferred).** Thin `FxRates = Callable[[str], float]` protocol with a
  default `fixed_fx` reading the fixed rate table; `normalize_quote` takes `fx: FxRates =
  fixed_fx`. Live-FX-seam (real exchange rate lookup) is **designed but unbuilt** (no
  silent cap) — the fixed table satisfies the deterministic-demo requirement; a live seam
  swap is one argument change. Module docstring logs the deferral.
- **Currency/language on the wire (Option A decision, load-bearing).** The wire extraction
  shape (`contract.ExtractionFields`) has no top-level `currency`/`language` fields, but
  the doctype schema (`skills/benefits-quote/assets/schema.json`) declares them as
  required. S1-benefits-2 was the first task to need both (normalize requires them).
  **Resolution: carry currency and language INSIDE `key_fields`** (they are
  `dict[str,str]` already), with no `contract/` change. Rationale: keeps this task off the
  `contract/` critical path (a contract change would ripple to `decode_wire` + every
  consumer); the wire model already transports arbitrary `key_fields`. Reconciled the
  doctype skill assets to match: `schema.json` moved `currency`/`language` into
  `key_fields.properties` + `key_fields.required` (tests stayed green).
  `validation.yaml` changed `field: currency` → `field: key_fields.currency`.
  **Alternative (not chosen):** add optional `currency`/`language` to
  `contract.ExtractionFields` as top-level wire fields (heavier, `contract/`-critical —
  deferred unless a reviewer insists).
- **Benefits eval corpus authored (`wiki/evals/benefits/expected.json`)** — 4 carrier
  quotes (the demo oracle): `benefits-carrier-a-usd-en`, `benefits-carrier-b-usd-en`
  (Path-A ADK + LangGraph carriers), `benefits-carrier-c-eur-fr`, `benefits-carrier-d-gbp-de`
  (Path-B PDFs). Each extraction carries `currency`, `language`, `premium`, `deductible`,
  `coverage_limits`, `exclusions`, `effective_date`, `expiry_date` in `key_fields`, plus an
  `expected_normalized` block (the normalize oracle). Synthetic data only, no PII.
  `description.md` updated with the 4-row example table. PDF images deferred (not needed by
  `FixtureExtractor`; only required for portal/dial-out paths).
- **Tests (`tests/test_normalize.py`)** — pure-unit (FX conversion, translation,
  idempotency), error cases (unknown currency), and the load-bearing oracle test: iterate
  the benefits corpus (`FixtureExtractor(corpus_path=<benefits expected.json>)`), normalize
  each extraction, assert equality against `expected_normalized`. Corpus-path idiom reused
  from `bridge/extraction/fixture.py`; `_load_corpus` lru_cache keyed by path string, so
  benefits and Address corpora don't collide.

**Verification.** T1: full suite green, `ruff check` clean, isolation guard intact
(`bridge/normalize.py` imports stdlib + pydantic + `contract/` only). T2: normalize tests
green; EUR/fr quote converts + translates correctly (`1000.00 EUR → 1100.00 USD`); USD/en
idempotent; corpus oracle test passes (all 4 docs match `expected_normalized`).

**CRITICAL escalation.** Touches `bridge/` (new `bridge/normalize.py`) + `wiki/evals/`
(authored benefits corpus) → auto-escalates. Stronger model + mandatory review before
commit. Committed path-scoped, leaving the unrelated pre-existing `spec/open-questions.md`
edit alone.

_Added 2026-09-06._

## In Progress

_Tracked in `TODO.md` (live status lives there, not here)._

## Planned Work

_Use /new-feature / /new-task; requirements land in spec/._
