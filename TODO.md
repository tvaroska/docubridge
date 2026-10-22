# Implementation plan — Cycle 1 (Core + Address) & Enterprise Showcase (v1.14)

Last Updated: 2026-10-22 · **Active status: All Cycle 1 Sprints (P0–P7, Sprint 0–4) + 6-Zone Enterprise Data Architecture, Agent Gym (`gemini-3.8-flash` / `gemini-4-argon`), Agent Registry & Gateway, and GCP Cloud Build CI/CD complete & verified.**

<!-- Counters: p0=4 p1=6 p2=5 p3=5 p4=5 p5=4 p6=6 p7=9 s0-sec=3 s0-arch=20 s0-docs=11 s0-bug=3 s0-debt=3 s0-perf=1 s0-frontend=1 s1-benefits=7 s3-edge=8 s4-usecase=5 -->

The build order over the [feature catalog](docs/features.md). Design lives in [`design/`](design/bridge.md) (the source of truth); requirements live in [`spec/`](spec/cujs.md); this is the **planning artifact** — sequence, dependencies, and exit gates. The high-level milestone view is [`docs/roadmap.md`](docs/roadmap.md).

Scope is **Core + Address** ([layout & first-cycle scope](design/architecture.md)); the Benefits journeys followed in Sprint 1, RFP stays an eval scaffold.

## Guiding order

**Agent-first, contract-down, seam-parallel, deploy-last.**

1. **Agent-first.** The Address agent's A2A contract is nailed down against a *mock Bridge* before the real Bridge is committed ([service-agent architecture](design/service-agent-architecture.md)). So the wire and the consumer come early; the real core fills in behind a stable contract.
2. **Contract-down.** `contract/` and the seam ABCs are the foundation everything imports; they land first so nothing is built against a moving shape.
3. **Seam-parallel.** Every feature is built on its **local adapter** first; the GCP adapter is written against the same shared test and slotted in at the deploy phase. Local green is the gate to move on; GCP green is the gate to ship.
4. **Deploy-last, but designed-throughout.** The trust boundary, seams, and contracts are whole from day one; the Terraform + GCP adapters are assembled at the end so Address runs deployed, not just local.

## Critical path (at a glance)

```
P0 foundations ─▶ P1 core spine ─▶ P2 A2A edge + mock ─▶ P3 Collect + Address consumer
                                                              │
                          ┌───────────────────────────────────┤
                          ▼                                     ▼
                 P4 durability/park-resume            P5 proactive + outbound slice
                          │                                     │
                          └──────────────▶ P6 A2UI + frontend ◀─┘
                                                  │
                                                  ▼
                                        P7 GCP substrate + deploy
```

P4 and P5 can proceed in parallel once P3 lands (both depend on the durable session + A2A edge, not on each other). P6 needs P3 (console/driver) and P5 (dashboard shows escalation). P7 is last and depends on all seam GCP adapters being ready.

Feature IDs (`CORE‑1`, `AGENT‑3`, `SEAM‑7`, …) reference [`docs/features.md`](docs/features.md).

**How this plan is executed.** Each phase below is a checklist of `- [ ]` tasks with ids
(`P<phase>-<seq>`). `- [ ]` open · `- [x]` done · `- [!]` attempted-but-failed. A task is
**unblocked** when every task it "needs" is `- [x]`. Tasks are done strictly in order
(they share files + git history); each one: implement → `uv run pytest` green → mark `- [x]`
→ commit. Every managed-service boundary is a **seam** (local adapter now; GCP adapter at P7).

---

## Sprint 4 — RFP live use-case-add (CUJ-9) — **ACTIVE SPRINT**

**Sprint transition 2026-10-02:** Sprint 3 (the `document_bridge` LlmAgent behind the A2A edge, `S3-edge-1…8`) closed and Sprint 0 drained. T3 gate green: 14/14 runnable CUJs pass both judges (CUJ-9 has no Driver yet — this sprint's target). Sprint 3 archived to `docs/features/a2a-edge.md` + `DECISIONS.md`.

**Focus.** CUJ-8 proves one *doctype* skill added live; CUJ-9 is the whole-use-case version: a
*process* skill plus its doctype skills dropped into the running core while an exchange of
another process is in flight. Carried-over context: `list_skills()` already reads the
`skill_registry` seam per call and the Agent Card regenerates per GET
(`bridge/edge/server.py` `_live_card`); `load_rfp_policy` and `candidate_doctypes_for`
resolve a process from its skill. Not yet shown: that every per-request consumer of a skill
(executor candidate labels, RFP policy, calibration, extraction schema) is read live rather
than captured at `build_a2a_app` / host start, and that an in-flight exchange is undisturbed.
The shipped Driver uses the synthetic `supplier-onboarding` skill (the
`tests/test_rfp_second_use_case.py` fixture) — industry-neutrality, no real skill needed.
Spec is protected: the CUJ-9 Driver line is a PROPOSAL (S4-usecase-5).

- [x] **S4-usecase-2**: **CUJ-9 Driver: whole use case added live while an exchange is in flight.** `tests/test_live_use_case_add.py` — one `build_a2a_app` over a tmp registry seeded with Address only. (a) Park an Address exchange mid-flight (`INPUT_REQUIRED`, Collect with one of two bills). (b) Control: an identical second core with no upload runs the same exchange to its terminal outcome. (c) On the first core, copy the synthetic `supplier-onboarding` process skill + `w9-form` / `insurance-cert` doctype skills into `BRIDGE_SKILLS_DIR` — same app object, no rebuild/restart. (d) Next card GET advertises `supplier-onboarding` + both doctypes. (e) Finish the parked exchange: terminal outcome, reason_code and ledger equal the control's. (f) Open and satisfy a `supplier-onboarding` Collect on the live core. Traps (hard-fail): app rebuilt / process restarted (assert one app, one executor identity), the in-flight task's state or ledger disturbed, the new process advertised but unusable. _(done 2026-10-03; see docs/features/usecase.md)_
      Files: tests/test_live_use_case_add.py, tests/_skill_fixtures.py (extract the supplier-onboarding fixture shared with tests/test_rfp_second_use_case.py)
      Needs: S4-usecase-1
      Added: 2026-10-02
- [x] **S4-usecase-3**: **A bad live upload is refused, not propagated.** A malformed / half-copied skill folder (missing `SKILL.md` frontmatter, unknown `bridge-kind`, policy.yaml unparseable, doctype named in `bridge-candidate-doctypes` that is absent) dropped into the live registry must not break discovery, not remove already-advertised skills, and not disturb in-flight exchanges: skipped with a logged reason, absent from the card, and picked up once repaired. A process whose candidate doctypes are missing is never dispositioned against another process's label space (S0-arch-18). Driver-level tests extend the S4-usecase-2 fixture; no new wire change. _(done 2026-10-03; reviewed; see docs/features/usecase.md)_
      Files: bridge/seams/local (skill registry adapter), bridge/edge/skills.py, bridge/edge/card.py, tests/test_live_use_case_add.py, tests/_skill_fixtures.py
      Needs: S4-usecase-2
      Added: 2026-10-02
- [x] **S4-usecase-4**: **A live-added RFP use case opens on the shared board next to a running exchange.** On the board host (`bridge/dashboard/board_host.py` over the shared exchange store), add the synthetic process live, open an RFP for it via the A2A Requirements path while an Address leg is parked: `/rfps` lists the new RFP, the carrier checklist and email-intake correlation resolve its policy (`item_field: credential`) from the live registry, and the Address leg still resumes to its baseline outcome. Local adapter in T1; gcp registry adapter under the shared seam test (gated on `BRIDGE_TEST_GCP`) asserts the same terminal outcome. _(done 2026-10-03; reviewed; see docs/features/usecase.md)_
      Files: bridge/dashboard/board_host.py, bridge/rfp.py, bridge/portal/rfp_checklist.py, bridge/seams/gcp/__init__.py, tests/test_live_use_case_add.py, tests/test_seam_skill_registry.py
      Needs: S4-usecase-2
      Added: 2026-10-02
- [x] **S4-usecase-5**: **Spec proposal + close-out.** PROPOSAL-only entry in `DECISIONS.md` (no `spec/` edit): CUJ-9 `**Driver:**` = the full node ids of S4-usecase-2 (+ S4-usecase-3/4 tests as companions) with the existing Judge kept verbatim and the bad-upload trap added; the exact replacement line is written out for the spec owner. Update `docs/features.md` (SKILL-5), new `docs/features/usecase.md` (what shipped), `docs/roadmap.md`; re-run `/verify CUJ-9` and record both judges. _(done 2026-10-03; see docs/features/usecase.md)_
      Files: DECISIONS.md, docs/features.md, docs/features/usecase.md, docs/roadmap.md, TODO.md (no spec/ edit)
      Needs: S4-usecase-2, S4-usecase-3, S4-usecase-4
      Added: 2026-10-02

---

## Sprint 0 — Review findings

Always-active critical lane. Last drained 2026-10-02 (the 2026-10-01 documentation review, the 2026-09-23 Benefits Consumer
extraction-capability reopen + the 2026-09-30 Benefits Consumer exec-demo feedback reopen); prior closes
2026-09-06 and 2026-08-22. Feature IDs: `sec` security · `arch` architecture · `bug`
correctness · `debt` technical debt · `docs` documentation · `perf` · `frontend`. Tasks
touching `spec/` are **proposal-only**; tasks touching `bridge/`, `contract/`, or `spec/`
**auto-escalate** per `CRITICAL.md`.

Drained 2026-10-02 (the 2026-10-01 whole-documentation review reopen, `S0-bug-3`, `S0-arch-14…20`, `S0-debt-3`, `S0-docs-6…11` — archived to `DECISIONS.md` + `docs/features/`). No open tasks.

---

## P0 — Foundations

**Goal.** A repo skeleton and test harness that everything else lands into; nothing built against a moving shape.

**Exit gate.** `pytest` green with placeholder seams; isolation-guard test passes; contract package importable by both `bridge/` and `agents/`.

- [x] **P0-1** — Repo tree + workspace scaffold: create `contract/ bridge/ agents/ skills/ frontend/ terraform/ tests/` packages with `__init__.py`, wire uv (Python 3.12) deps, and add the `bridge/`-never-imports-`agents/` **isolation-guard test** (fails if any `bridge/**` module imports `agents`). [CONTRACT‑1 scaffold, load-bearing invariant] _(done 2026-08-18; see design/architecture.md)_
- [x] **P0-2** — `contract/` Pydantic wire types (Pydantic-only, no ADK / no a2a-sdk): `CollectRequest`, `ExchangeTurn`, `CollectionStatus`, `LedgerEntry`, `Requirement`, `RequirementsList`, `RequirementStatus`, and the extraction field schemas. Round-trip + validation tests. [CONTRACT‑1] _(done 2026-08-18; see design/contracts.md)_
- [x] **P0-3** — Seam / Port harness: `Port` (`Seam`) enum (all 13 Hexagonal port names: `sessions`, `task_store`, `exchange_store`, `skill_registry`, `memory_bank`, `scheduler`, `a2a_client`, `extraction`, `email_ingress`, `audit_vault`, `operational_store`, `action_inbox`, `agent_gym`), the port ABCs/Protocols, the `@pytest.mark.port` / `@pytest.mark.seam` markers + `adapter` fixture parametrized over `("local","gcp")` (gcp gated on `BRIDGE_TEST_GCP`), `BRIDGE_PORT_MODE` / `BRIDGE_SEAM_MODE` + per-port overrides, and a collection-time guard rejecting invalid port names. [TEST‑1, seam ABCs] _(done 2026-08-18; expanded to 13 ports 2026-10-22; see design/seams.md)_
- [x] **P0-4** — Pin `google-adk >= 2.7.0, < 3` and stand up the **experimental-surface register** (`RemoteA2aAgent`, `ResumabilityConfig`, …) with a test that fails if an unlisted experimental surface is used. [TEST‑4] _(done 2026-08-18; see design/adk.md)_

---

## P1 — Bridge core reasoning spine

**Goal.** A `document_bridge` that classifies, dispositions, and ledgers a single document — the Sense-A engine — with the fixture extraction adapter.

**Exit gate.** Every `expected.json` record disposes to its `expected_disposition`/`expected_gate` through the gate (deterministic on repeat); issuer variants canonicalize identically; the LLM cannot produce an acceptance without a gate call.

- [x] **P1-1** — `document_bridge` `LlmAgent` on stock `Runner`/`App`, plus `run_disposition_gate` as a **pure deterministic `FunctionTool`** (the one inversion — the LLM cannot mint an acceptance without a gate call). [CORE‑1, CORE‑2] _(done 2026-08-18; see design/disposition.md)_
- [x] **P1-2** — Extraction seam + **fixture adapter** loaded from the eval corpus, exposed as the `extraction_agent` `AgentTool`; `InMemoryArtifactService`. [SEAM‑8, ART‑1] _(done 2026-08-18; see design/seams.md)_
- [x] **P1-3** — Four-signal `SignalProvider`, classification, and `canonicalize_issuer` (e.g. `PowerCo`/`Power Co.`→`power-co`). Canonicalization tests over the corpus issuer variants. [CORE‑3, CORE‑4] _(done 2026-08-18; see design/disposition.md)_
- [x] **P1-4** — Classified ledger as a `CollectionStatus` projection with `reason_code`+`message` on entries/requirements; the one-turn collect path. [CORE‑5, COLL‑4] _(done 2026-08-18; see design/collect.md)_
- [x] **P1-5** — Off-script routing tools: `handle_deletion_request`, `reject_submission`, escalation stub. [CORE‑6, CORE‑7, CORE‑8] _(done 2026-08-18; see design/a2a-edge.md)_
- [x] **P1-6** — Eval-corpus parity tests (TEST‑2): every `expected.json` record disposes to its expected disposition/gate deterministically. [TEST‑2] _(done 2026-08-18; see wiki/evals/README.md)_

---

## P2 — A2A edge + mock Bridge

**Goal.** The canonical A2A front door and a maintained mock double, so the Address agent has a stable contract to build against.

**Exit gate.** A conformant A2A client drives every spec op; domain payloads ride in parts; no bytes in any message; the app reads only standard task status + ledger projection (never ADK events).

- [x] **P2-1** — `a2a-sdk` JSON-RPC server exposing `message/send`, `message/stream` (SSE), `tasks/get`, `tasks/list`, `tasks/cancel`, `tasks/resubscribe`; domain types carried inside `Message`/`Part`/`Artifact`. [WIRE‑1, WIRE‑2, WIRE‑3] _(done 2026-08-18; see design/a2a-edge.md)_
- [x] **P2-2** — `_status_for` mapping (PENDING→`INPUT_REQUIRED`, else `COMPLETED`) + a **dynamic Agent Card** generated from the loaded skills' metadata. [WIRE‑4] _(done 2026-08-18; see design/a2a-edge.md)_
- [x] **P2-3** — Dual-mode intake: `input_modes` advertising JSON + text, content-routed at the edge. [WIRE‑5] _(done 2026-08-18; see design/edges.md)_
- [x] **P2-4** — FastAPI **scoped artifact-fetch endpoint** (InMemory; signed-URL path deferred to P7); `FileWithUri` by-reference on the wire, no inline bytes. [ART‑2] _(done 2026-08-18; see design/artifacts.md)_
- [x] **P2-5** — **Mock Bridge** contract double emitting the same `ExchangeTurn`/`CollectionStatus` shapes over the same wire, for the Address agent to build against. [WIRE‑6] _(needs P2-1)_ _(done 2026-08-18; see design/a2a-edge.md)_

---

## P3 — Collect + the Address consumer

**Goal.** The Address service agent end-to-end against the (mock, then real) Bridge — the reusable `Workflow` shape and the skills.

**Exit gate (the demo's heart).** Golden runs pass on both fixture and Gemini adapters *and* against mock and real Bridge: Path-A instant accept (CUJ‑1), distinct-issuer accept + same-issuer reject (CUJ‑10), resubmission (CUJ‑4). The gate — not a model — mints `done`.

- [x] **P3-1** — Service-agent `Workflow` conditional cycle: collect node (`build_bridge_remote_agent(card_url, rerun_on_resume=True)`), deterministic `is_satisfied` **gate node** (`ctx.route`, round ceiling), present node (code); edges `START→collect→gate`, `gate--again-->collect`, `gate--done-->present`. [AGENT‑1, AGENT‑2, AGENT‑3, AGENT‑4] _(needs P2-5)_ _(done 2026-08-18; see design/service-agent-architecture.md)_
- [x] **P3-2** — Shared durable session threading the exchange `context_id`, plus the two Ring‑3 workarounds: the `RequestInterceptor` (send-path resume re-detection) and `wire.py extract_exchange_turn` decode + Bridge emitting artifact `last_chunk=True`. [AGENT‑5, AGENT‑6] _(needs P3-1)_ _(done 2026-08-18; see design/a2a-consumer.md)_
- [x] **P3-3** — Skills: `address-proof` (process skill, prose satisfaction, `policy.yaml`), `gov-id`, `utility-bill` (field names + canonicalization matched to the corpus). [SKILL‑1, SKILL‑2, SKILL‑3] _(needs P3-1)_ _(done 2026-08-18; see design/skills.md)_
- [x] **P3-4** — CLI driver (`python -m agents.address`) — an App/Runner host, not an ADK agent. [FE‑1] _(needs P3-2, P3-3)_ _(done 2026-08-18; see design/service-agent-architecture.md)_
- [x] **P3-5** — Point the agent at the mock, then swap `card_url` to the **real edge**; golden runs: Path-A instant accept (CUJ‑1), distinct-issuer accept + same-issuer reject (CUJ‑10), resubmission (CUJ‑4). Parity = terminal outcome. [AGENT‑7, TEST‑3] _(needs P3-4)_ _(done 2026-08-18; see spec/demos/address-demo.md)_

---

## P4 — Durability: park, restart, resume, HITL

**Goal.** A collection that survives days and a process restart, and a human-in-the-loop pause.

**Exit gate.** Park a leg, kill the process, restart, resume to the same terminal outcome (CUJ‑7); an HITL review parks with zero compute and resumes on the matched response (CUJ‑5). Verify the throwaway-child-session anti-pattern is *not* used.

- [x] **P4-1** — `DatabaseSessionService` (async driver `sqlite+aiosqlite:///…`) + `DatabaseTaskStore` behind the sessions/task_store seams; `ResumabilityConfig(is_resumable=True)` on the `App`. [DUR‑1, DUR‑2] _(needs P3-5)_ _(done 2026-08-18; see design/long-running.md)_
- [x] **P4-2** — Park on `input-required` → `LongRunningFunctionTool` (zero compute); resume by feeding an id-matched `FunctionResponse` to a **fresh `Runner`** on the same DB — no HTTP. [DUR‑3, DUR‑4] _(needs P4-1)_ _(done 2026-08-18; see design/long-running.md)_
- [x] **P4-3** — `request_human_review` `LongRunningFunctionTool` (HITL pause). [DUR‑5] _(needs P4-2)_ _(done 2026-08-18; see design/long-running.md)_
- [x] **P4-4** — Progress surfaces: `tasks/get`, `tasks/resubscribe`, `PushNotificationConfig`, non-empty `TaskStatusUpdateEvent.status.message`. [DUR‑6] _(needs P4-2)_ _(done 2026-08-18; see design/a2a-edge.md)_
- [x] **P4-5** — No-TTL lifecycle: durable `escalated` status, `tasks/cancel`→`CANCELED`; artifact retention/versioning (ART‑3). [DUR‑7, ART‑3] _(needs P4-2)_ _(done 2026-08-18; see design/artifacts.md)_

---

## P5 — Proactive follow-up + outbound (first slice)

**Goal.** The clock-driven chase — the concrete proof of "mediation, not extraction" — with a real send. Parallelizable with P4 (both depend on P3, not each other).

**Exit gate.** On the virtual clock a silent party goes `overdue → reminder (real nudge fired) → escalated`, each event recorded, next alarm re-armed, zero compute between (CUJ‑6); a stall holds at `escalated` and survives restart.

**Note (silent cap):** dial-out (OUT‑3) and Deliver (OUT‑4) are designed but **not built this cycle** — Address's only send is the chase.

- [x] **P5-1** — Scheduler seam: in-process `VirtualClock` (local adapter) + an SLA/policy engine that reads cadence / deadline / max-nudges / ladder from the process-skill `policy.yaml`. [SEAM‑6, PROA‑1] _(needs P3-5)_ _(done 2026-08-19; see design/followup.md)_
- [x] **P5-2** — Follow-up ladder `on_track→overdue→reminder→escalated`; each step a scheduled callback that re-arms the next (zero compute between). [PROA‑2] _(needs P5-1)_ _(done 2026-08-19; see design/followup.md)_
- [x] **P5-3** — `a2a_client` seam (fake transport local) + a **real party-channel nudge** — the first concrete outbound send. [SEAM‑7, OUT‑1, OUT‑2] _(needs P5-2)_ _(done 2026-08-19; see design/outbound.md)_
- [x] **P5-4** — Wake path: webhook receiver + `task_id`→session index + `PushNotificationConfig`; doorbell carries only "task X changed", restore from persistent stores, then `run_async`. [PROA‑3] _(needs P5-2)_ _(done 2026-08-19; see design/long-running.md)_

---

## P6 — A2UI edge, Path B, and the frontend

**Goal.** The process made visible on stage, and the human-facing portal.

**Exit gate.** A human upload runs the full Path-B graph end-to-end (CUJ‑2); a doctype skill added to the running core regenerates the Agent Card with no redeploy (CUJ‑8); the three surfaces render the live run; Playwright e2e + a `console_server` test green.

- [x] **P6-1** — A2UI edge (Bridge → provider only): declarative content, relays agent `reason_code`+`message` uninterpreted; Path-B extraction graph (`extract→dispose→HITL`, ≤3 resubmits); Path-A validate-only. [PATH‑A, PATH‑B, A2UI‑1] _(needs P4-5, P5-4)_ _(done 2026-08-19; see design/a2ui.md)_
- [x] **P6-2** — Provider Portal (external zone, the A2UI Path-B host). [FE‑2] _(needs P6-1)_ _(done 2026-08-19; see design/frontend.md)_
- [x] **P6-3** — Agent Console: `console_server` (builds App + Runner) + Vite/React app with new-session-from-prepared-request, session list, per-turn viewer, over SSE. [FE‑3] _(needs P6-1)_ _(done 2026-08-19; see design/frontend.md)_
- [x] **P6-4** — Ops Dashboard: in-flight collections, ledger, HITL/escalation queues, over SSE. [FE‑4] _(needs P6-1)_ _(done 2026-08-19; see design/frontend.md)_
- [x] **P6-5** — Time-warp (local REST) + shared UI/domain package (`theme`, `Panel`, `Badges`, `domain/contract`, snake_case→camelCase) + presentation modes (Split-Screen / Timeline / Architecture X-Ray). [FE‑5, FE‑6] _(needs P6-2, P6-3, P6-4)_ _(done 2026-08-19; see design/frontend.md)_
- [x] **P6-6** — Live doctype-add: upload a skill → registry → Agent Card regenerates with no redeploy. [SKILL‑4] _(needs P6-3)_ _(done 2026-08-19; see design/skills.md)_

---

## P7 — GCP substrate & deployed-path parity

**Goal.** Address runs **deployed** on the platform, not just locally — the showcase.

**Delivers:** the GCP adapters for every seam, GCP‑1…8, ART‑2 signed URLs, ART‑3 retention.

**Depends on:** all prior phases (each seam's GCP adapter shares the phase's local test).

**Work.**
- Terraform by primitive: `network`, `runtime` (Cloud Run v2), `gateway` (`enable_gateway`), `iam` (`bridge-runtime` SA + grants + Cloud Tasks OIDC token-creator), `database` (Cloud SQL PG16 private IP), `tasks` (`bridge-followups`), `secrets` (DSN via secret env), GCS registry module, `frontend`.
- Seam GCP adapters: Sessions/Memory (Vertex Agent Engine or Cloud SQL), task_store/exchange_store (Cloud SQL), skill_registry (GCS), scheduler (Cloud Tasks), a2a_client (Agent-Identity transport), extraction (Gemini deployed; Document AI as the `docai` engine's OCR pre-pass feeding Gemini — S0-arch-12, [design/docai-prepass.md](design/docai-prepass.md)).
- `GcsArtifactService` + signed-URL fetch endpoint; GCS object-lifecycle retention policy.
- Trust boundary: two zones, Gateway ingress + per-party scoping, Agent Identity workload auth.
- Plan-time credential-free tests (`terraform test` + `mock_provider`); deployed-path parity run against a provisioned project.

**Gateway/Identity hardening (from the vibeflix-audit review — see `design/zones.md`, `DECISIONS.md` 2026-08-24).**
- **Gateway egress must not push datastore traffic onto an HTTP hop.** A peer reference's Gateway governs *HTTP egress only* (not gRPC/TCP), which forced their shared task store over HTTPS through a single-instance app. Confirm our Gateway config governs *ingress* per-party scoping while the Bridge→Cloud SQL path stays a **private-IP** connection — never route the datastore through an HTTP hop, and never inherit the "app owns the task store over HTTPS" indirection. If Gateway egress policy does constrain internal traffic, the answer is private-IP to Cloud SQL, not an HTTP proxy.
- **Reconcile Agent Card `url` against the Gateway-authorized host in the `a2a_client` adapter.** A host mismatch between the card's advertised URL and the Gateway-authorized host yields a `403` egress-denied at call time (the peer's `_resolve_agent_card` repoints to the `.mtls` host as a workaround). Verify our outbound A2A resolves to the Gateway-authorized host; **re-verify against `google-adk >=2.7` before porting any repointing hack** — it may be fixed or the surface may have shifted since their 2.3 pin.
- **Per-workload identity if serving surfaces split.** P7 names one `bridge-runtime` SA (fine for a single workload). If the edge, dashboard, and follow-up worker deploy as separate services, give each a per-workload Agent Identity so IAM grants stay least-privilege (the peer uses no shared mesh SA).

**Session-affinity discipline (task_store / sessions).** The A2A edge is send-then-poll across replicas, so the GCP `task_store` (and `sessions`) adapters MUST be shared stores (Cloud SQL), never per-replica in-memory — a peer reference measured 86.8% poll-404s with a per-replica store on multi-replica Agent Runtime (see `design/seams.md`, "the session-affinity trap"). Encode as: (a) a **cross-replica read-after-write** parity case (write via one adapter handle, read via a second independent handle to the same backing store); (b) terminal-state writes get retry + loud logging; (c) if any A2A-serving Cloud Run service is pinned to a single instance as a stopgap, `log()`/record it as a silent-cap in TODO — don't inherit "the app owns the task store" indirection.

**Exit gate.** The whole seam suite passes with `BRIDGE_TEST_GCP=1`; the Address golden runs reproduce the same terminal outcomes on the deployed path; plan-time Terraform tests green with no credentials; the cross-replica read-after-write case passes against the Cloud SQL task_store adapter.

---

## Cross-cutting, every phase

- **Shared seam suite is the contract.** Each new adapter is added under the existing parametrized test; parity is terminal-outcome, never ledger-identical.
- **Experimental-surface discipline (TEST‑4).** `RemoteA2aAgent` (`@a2a_experimental`, pin `use_legacy=True`), `ResumabilityConfig` (`@experimental`, on `App`) — validate before flipping any mode; re-audit on every `google-adk` minor bump.
- **Eval corpus is the oracle.** No Address behavior is "done" until it reproduces `wiki/evals/address/expected.json` + `timeline.json`.
- **No silent caps.** Anything scoped out this cycle (dial-out, Deliver, cold unsolicited push, Benefits/RFP) stays a designed-but-unbuilt seam, logged here, not quietly dropped.

## Deferred to later cycles

RFP live use-case-add (CUJ‑9) is Sprint 4; deferred:
- **Cold unsolicited inbound push** (the Extract secondary edge).
- _(Completed 2026-10-19)_ **Option B — A2A Server on Vertex AI Agent Engine (`BridgeA2aAgentEngine` / `BRIDGE_AGENT_ENGINE_A2A=1`):** Both **Option A** (`VertexAiSessionService`, `VertexAiMemoryBankService`, and remote `document_bridge` / `address` ADK `App` execution via `BRIDGE_AGENT_ENGINE_ID` + `BRIDGE_AGENT_ENGINE_REMOTE=1`) and **Option B** (`BridgeA2aAgentEngine` in `bridge/edge/agent_engine_a2a.py` hosting `CollectExecutor`, `EdgeRequestHandler`, dynamic `build_agent_card` over `loaded_skills()`, `TaskStore`, `PushNotificationConfigStore`, `LlmLeg`, and `RfpBoard` inside Vertex AI Agent Engine with thin Cloud Run proxy routes in `bridge/edge/server.py` behind `BRIDGE_AGENT_ENGINE_A2A=1`) are now built and tested (`tests/test_agent_engine_a2a.py`).

See [layout & scope](design/architecture.md) and [open questions](spec/open-questions.md).
(Benefits — Negotiate/FX/interop + dial-out/Deliver, CUJ‑3/11/12 — was the Sprint 1
cycle; **closed 2026-09-07**, T3-green, archived to `docs/features/benefits.md`.)
(P7 — GCP substrate & deployed-path parity, Cycle 1's M7 "it's deployed" milestone —
was the Sprint 2 cycle; **closed 2026-10-01**, archived to `docs/features/gcp-deployment.md`.
The full phase plan stays above at `## P7` as the cycle record.)
(Sprint 3 — the LlmAgent behind the A2A edge — **closed 2026-10-02**, T3-green, archived to `docs/features/a2a-edge.md`.)

## See also
- [`docs/features.md`](docs/features.md) — the feature catalog these phases sequence
- [`docs/roadmap.md`](docs/roadmap.md) — high-level milestone view
- [`design/bridge.md`](design/bridge.md) — the design source of truth
