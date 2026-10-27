# Feature catalog

The build broken into named, individually-verifiable features, each pinned to its ADK/A2A construct, the **area** it belongs to (Core · Address · Benefits · RFP · Enterprise · Deploy), the [CUJ](../spec/cujs.md) it serves, and a one-line acceptance check. This is the *what*; [`TODO.md`](../TODO.md) is the *in what order*, and [`roadmap.md`](roadmap.md) is the milestone view. Each feature links back to the design note ([`design/`](../design/bridge.md)) that specifies it — this catalog re-specifies nothing.

**Area legend.** `Core` = the reusable Bridge (`bridge/`, `contract/`); `Address` / `Benefits` / `RFP` = the demo whose skills/consumer exercise it; `Enterprise` = the 6-Zone Enterprise Data Architecture, Agent Gym, Agent Registry & Gateway, and BigQuery SQL Trace Evaluation; `Deploy` = GCP substrate. Area is a *decision*, not progress — this catalog carries no status markers.

**Acceptance oracle.** Wherever a feature touches Address behavior, the enforced check is the eval corpus in `wiki/evals/address/` (8 synthetic docs, party `jordan-lee`) and the golden runs — not prose. Benefits behavior is checked against `wiki/evals/benefits/` (5 carrier-quote records + `conformance.json`) and its golden runs. See [CUJ catalog](../spec/cujs.md).

---

## A. Bridge core — the reasoning spine ([design](../design/adk.md))

| ID | Feature | Construct | Area | CUJ | Acceptance |
|---|---|---|---|---|---|
| CORE‑1 | Root mediation agent `document_bridge` | `LlmAgent` on stock `Runner`/`App` | Core | 1,2 | In-scope submission → agent calls the gate and relays its verdict; off-script intent falls through to the right tool |
| CORE‑2 | Deterministic disposition gate ("the one inversion") | `run_disposition_gate` `FunctionTool` (pure) | Core | 1,4,5 | Identical signals → identical verdict; LLM cannot mint an acceptance without/against the gate; held at the agent — the gate's inputs are pinned from session-recorded signals by a before_tool_callback, never from model-supplied args |
| CORE‑3 | Extraction as a subagent | `extraction_agent` attached as **`AgentTool`** (not a `sub_agents` handoff) | Core | 2 | A doc turn calls the AgentTool, returns `{classification, key_fields, signals}`, control returns to `document_bridge` |
| CORE‑4 | Four-signal disposition model | single `SignalProvider` interface; quality + confidence gates | Core | 4,5 | Each signal in isolation drives the documented route (resubmit / reject / HITL / auto-approve) |
| CORE‑5 | Classification + issuer canonicalization | `bridge.classification.canonicalize_issuer`; canonical key on `LedgerEntry` | Core | 10 | `PowerCo`/`Power Co.`→`power-co`; unrequested doctype flagged; per-doc normalization is Sense A |
| CORE‑6 | Classified ledger | `CollectionStatus` = tuple of `LedgerEntry` (doctype · disposition · issuer · key fields) + outstanding + `terminal` | Core | 1,10 | Append-only projection over the exchange's tasks; delivered as `CollectionStatus` |
| CORE‑7 | Agent-authored explanations | `Requirement`/`RequirementsList`/`RequirementStatus`; `reason_code` + `message` | Core | 4 | Rejected entry and each outstanding requirement carry both a machine `reason_code` and human `message` |
| CORE‑8 | Off-script intent routing | `LlmAgent` → `handle_deletion_request` / `reject_submission` / escalation | Core | — | Deletion request, wrong file, out-of-scope each route correctly; a new intent needs a tool + a line, not a new flow; reachable agent-to-agent on the hybrid edge (a text-only A2A message → the LlmAgent leg; the outcome rides status.message.metadata.reason_code), and model text never reaches the wire |
| CORE‑9 | Disposition gate as the published review contract + calibration corridor | `bridge/disposition.py` review vocabulary; `bridge/calibration.py`; `tests/test_disposition_calibration.py` | Core | 4,5 | Published disposition states + confidence/flagged-fields calibration thresholds gate verdicts |

**Invariants** (verified across the above): *LLM routes, code decides*; **Sense A** (per-artifact: legible/type/unexpired/issuer — Bridge, authoritative) vs **Sense B** (set completeness / deal — app decides, Bridge advisory) ([disposition](../design/disposition.md), [collect](../design/collect.md)).

---

## B. Collect & completeness ([collect](../design/collect.md), [patterns](../design/patterns.md))

| ID | Feature | Construct | Area | CUJ | Acceptance |
|---|---|---|---|---|---|
| COLL‑1 | Collect pattern (multi-turn set) | durable A2A conversation (context → task → messages); no DSL | Core | 10 | Per turn Bridge returns `CollectionStatus` + advisory delta; app returns requirements + `done?`; loops until app says done |
| COLL‑2 | Best-effort completeness proposal | `bridge/satisfaction.py` (`propose_outstanding`, model-interpreted `bridge-satisfaction` prose) → `CollectionStatus.outstanding` `RequirementsList` proposal; opt-in `BRIDGE_SATISFACTION=llm` | Core | 10 | 1 gov-ID → "looks done"; 1 bill → chases a 2nd distinct-issuer; app can always override; Bridge never enforces |
| COLL‑3 | Status vocabulary as the only language | `required \| optional \| satisfied \| waived` | Core | 10 | `required`/`optional` acts as a chase knob, not a judgment; conditionals are *reasoned* next turn, never encoded |
| COLL‑4 | Request (pull) = one-turn Collect | same Collect code path | Core | 1 | Single-doc request returns normalized doc in one turn, no loop |
| COLL‑5 | Emergent Collect (agent-mutated requirements) | `RequirementsList` mutation; thinner Bridge best-effort | RFP | 13 | RFP: agent mutates requirements at runtime, N suppliers — same Bridge code |
| COLL‑6 | RFP collection process (`benefits-rfp`) | `bridge/rfp.py` pure aggregate; `RfpPolicy` from the process skill (`load_rfp_policy`); lifecycle derived on read, due-date-anchored ladder | RFP | 13 | Process skill metadata drives policy; lifecycle states computed from due dates |
| COLL‑7 | RFP opened over A2A onto the durable board | the deployed A2A runtime's RFP opener → `exchange_store` (CAS) | RFP | 13 | A `CollectRequest` on the deployed runtime opens an RFP on the shared durable board |

---

## C. Documents as artifacts ([artifacts](../design/artifacts.md))

| ID | Feature | Construct | Area | CUJ | Acceptance |
|---|---|---|---|---|---|
| ART‑1 | Documents as versioned ADK artifacts | `ArtifactService`; `save_artifact`/`delete_artifact` | Core | 4 | Resubmit same filename → new version, history intact; deletion is a real erase of every version (the scoped fetch then 404s), not a soft flag, confined to the requesting leg |
| ART‑2 | Artifacts cross the wire by reference | A2A `FilePart`/`FileWithUri`; scoped fetch endpoint | Core | 1,2 | No bytes in any A2A message; `CollectionStatus` carries `FileWithUri`; every fetch is leg-scoped |
| ART‑3 | Retention by backend object-lifecycle | GCS object-lifecycle policy; `GcsArtifactService` | Core | 7 | Versions persist for exchange life; post-terminal window enforced by backend policy, no Bridge GC code |

---

## D. A2A edge — Ring 1 wire ([a2a-edge](../design/a2a-edge.md), [standards](../spec/standards.md))

| ID | Feature | Construct | Area | CUJ | Acceptance |
|---|---|---|---|---|---|
| WIRE‑1 | Canonical A2A server | `a2a-sdk` JSON-RPC 2.0; `message/send`·`message/stream`·`tasks/get`·`list`·`cancel`·`resubscribe` | Core | 1,2 | Any conformant A2A client drives every spec op; domain payloads ride inside parts (no bespoke REST) |
| WIRE‑2 | Domain-in-parts contract | `CollectRequest`, `ExchangeTurn{CollectionStatus}`, `RequirementsList`, `LedgerEntry` inside `Message`/`Part`/`Artifact` | Core | 1 | App reads standard A2A task status + a ledger projection, **never ADK events** |
| WIRE‑3 | Disposition → task status | `_status_for` keyed off `ExchangeTurn.terminal`: non-terminal (a pending human review or an escalate) → `INPUT_REQUIRED`, else `COMPLETED` | Core | 5,7 | A pending item surfaces as the first-class resumable park signal |
| WIRE‑4 | Dynamic Agent Card | A2A Agent Card at well-known location, from skills' `name`/`description` | Core | 8 | Install a skill → card regenerates, no redeploy |
| WIRE‑5 | Intake: format vs capability | one `AgentSkill`, two `input_modes` (`application/json`+`text/plain`), content-routed | Core | 1 | JSON `DataPart` and free text hit the *same* skill; free text parsed to `CollectRequest`; no per-format skill split |
| WIRE‑6 | Mock Bridge contract double | maintained contract double (not throwaway) | Core | — | Address agent runs green against the mock; parity with real is terminal-outcome |
| WIRE‑7 | Hybrid edge routing | `BRIDGE_EDGE_MODE=deterministic\|hybrid`; pure `classify_route` (G, R1–R6) dispatched by `CollectExecutor` | Core | 1,5,10 | Structured traffic never reaches the model (0 LLM calls, byte-identical `ExchangeTurn` in both modes); free text, a park handoff and a resume go to the LlmAgent leg; an invalid mode fails app start |
| WIRE‑8 | Agent-to-agent human-review resolution | additive `HumanReviewResolution` (contract 1.4) in a data part; pre-dispatch `EdgeRequestHandler` guard; fail-closed reviewer allow-list from the call-context principal | Core | 5 | A parked task accepts only an id-matched resolution from an allow-listed principal; a mismatched, malformed, unauthorized or no-pending resolution is refused with the task unchanged |

---

## E. Long-running durability ([long-running](../design/long-running.md), [service-agent](../design/service-agent-architecture.md))

| ID | Feature | Construct | Area | CUJ | Acceptance |
|---|---|---|---|---|---|
| DUR‑1 | Durable exchange = A2A context | context 1:1 exchange (no separate id); task 1:1 session; turn = invocation | Core | 7 | Context token *is* exchange identity; tasks in one exchange never clobber each other |
| DUR‑2 | Park on `input-required` | `RemoteA2aAgent` → `LongRunningFunctionTool` pause (zero compute) | Core | 7 | Invocation ends on park; a later id-matched `FunctionResponse` resumes the same task |
| DUR‑3 | Resume across process restart | `ResumabilityConfig(is_resumable=True)` on `App`; `DatabaseSessionService` + `DatabaseTaskStore` | Core | 7 | Kill/restart a parked leg, feed the `FunctionResponse`, same terminal outcome — no HTTP |
| DUR‑4 | HITL pause (platform-native) | `request_human_review` `LongRunningFunctionTool`, id-matched resume | Core | 5 | Low-confidence review parks with zero compute, survives restart, resumes on the matched response; agent-to-agent on the hybrid edge: a hitl_review verdict under a bridge-hitl: park process parks the A2A task INPUT_REQUIRED (awaiting_human_review + review_call_id) and an id-matched HumanReviewResolution resumes it on the same task |
| DUR‑5 | Progress without a held connection | `tasks/get`, `tasks/resubscribe`, `PushNotificationConfig` webhooks; `TaskStatusUpdateEvent.status.message` | Core | 6,7 | Learn progress days later by poll/resubscribe/push; **status.message must be non-empty** |
| DUR‑6 | No-TTL lifecycle, app-owned close | durable `escalated` status; `tasks/cancel`→`CANCELED` | Core | 6 | A stall holds at `escalated` indefinitely (visible on read-model); only app `cancel_task` terminates |
| DUR‑7 | Context/credential lifetime decoupling | per-turn re-auth at Gateway, leg-scoped | Core | 7 | Re-issue a party link without orphaning the context; no long-TTL bearer is the sole key |

---

## F. Service agent — the Address consumer ([service-agent](../design/service-agent-architecture.md), [consumer](../design/a2a-consumer.md))

| ID | Feature | Construct | Area | CUJ | Acceptance |
|---|---|---|---|---|---|
| AGENT‑1 | `Workflow` conditional-cycle graph | `google.adk.workflow.Workflow`; edges `START→collect→gate`, `gate--again-->collect`, `gate--done-->present` | Address | 10 | The one graph *kind* every service agent reuses; only nodes/gate vary |
| AGENT‑2 | Collect node | `RemoteA2aAgent` via `build_bridge_remote_agent(card_url, rerun_on_resume=True)` | Address | 1,10 | Sends `CollectRequest`, relays `ExchangeTurn`; treats `INPUT_REQUIRED` as a resumable pause |
| AGENT‑3 | Deterministic gate node (Sense B) | code-only `FunctionNode`; `is_satisfied`; `ctx.route`; round ceiling | Address | 10 | gov-ID OR 2 distinct-issuer bills → `done`; a model never mints `done`; ceiling fails a non-terminating run fast |
| AGENT‑4 | Present node | code pass-through or `LlmAgent` (documented drop-in) | Address | 1 | Emits terminal `ExchangeTurn` as graph output; never routes |
| AGENT‑5 | Shared durable session | one `InvocationContext`; ledger + `context_id` + peer `task_id` in `event.custom_metadata` | Address | 7 | Every node on one session; threads exchange `context_id` across rounds |
| AGENT‑6 | Ring-3 workaround: `RequestInterceptor` | send-path resume re-detection + `task_id`/`context_id` stamping | Address | 7 | Resume survives the graph appending an event after the `FunctionResponse` (no re-park / new task) |
| AGENT‑7 | Ring-3 workaround: read terminal turn | `wire.py` decode + Bridge emits artifact `last_chunk=True` | Address | 10 | Gate reading `ctx.session.events` sees the persisted terminal ledger (a partial event is never persisted) |
| AGENT‑8 | Reusability to new agents | `agents/benefits/` reusing the same A2A contract | Benefits | 11,13 | ADK carrier `carrier_a_adk.py` + LangGraph carrier `carrier_b_langgraph.py` + `server.py` reuse the same A2A contract; mock→real / local→GCP is a card-URL change |

---

## G. Initiation surfaces & frontend ([frontend](../design/frontend.md))

> A2UI is **strictly Bridge → document provider**. The service agent does not speak A2UI; its console is SSE observability/control — showcase.

| ID | Feature | Construct | Area | CUJ | Acceptance |
|---|---|---|---|---|---|
| FE‑1 | CLI driver | `python -m agents.address …`; `App`/`Runner` host | Address | 1,10 | Scriptable; drives the golden/eval runs; fires opening `CollectRequest`, feeds wake `FunctionResponse` |
| FE‑2 | Agent Console (Vite+React) | `frontend/agent-console/` + `console_server`; SSE | Address | 5,10 | New-session-from-prepared-request button; session list + live status; per-turn ledger/reasoning/satisfaction viewer (Sense B watchable) |
| FE‑3 | Servicer Ops Dashboard | `frontend/ops-dashboard/`; internal, SSE | Address | 5,6 | Exchanges in flight, ledger filling live, disposition outcomes, HITL + escalation queues |
| FE‑4 | Provider Portal (A2UI Path-B) | `frontend/provider-portal/`; external zone | Address | 2 | Upload → see disposition + outstanding; relays agent `reason_code`+`message` into A2UI content |
| FE‑5 | Time-warp presenter control | `frontend/timewarp/`; plain REST, local-only | Address | 6 | Fast-forward an SLA window so `overdue→escalated` fires on cue; step/pause/replay |
| FE‑6 | Presentation modes | Split-Screen Theater / Timeline / Architecture X-Ray | Address | — | Portal↔console+dashboard with Gateway boundary drawn; event-stream scrub; live-traffic overlay |

---

## H. A2UI edge & dual-path ([a2ui-edge](../design/a2ui-edge.md), [dual-path](../design/dual-path.md))

| ID | Feature | Construct | Area | CUJ | Acceptance |
|---|---|---|---|---|---|
| PATH‑A | Structured path (validate-only) | A2A data response on the edge; **no extraction call** | Core | 1 | Party-with-agent submits structured data → schema + disposition only |
| PATH‑B | Portal path (extraction graph) | A2UI upload → `extract → dispose → HITL if flagged`; ≤3 resubmits | Core | 2,4,5 | Human upload runs the fulfillment graph; unreadable doc → `PENDING` (route to human), not rejection |
| A2UI‑1 | Content-and-protocol, not pixels | declarative A2UI; agent `reason_code`+`message` relayed uninterpreted | Core | 2 | Bridge is transport+layout over the agent's words; adds no policy/prose, keys only mechanical chase on `reason_code` |

---

## I. Proactive follow-up & outbound ([proactive](../design/proactive.md), [outbound](../design/outbound.md))

| ID | Feature | Construct | Area | CUJ | Acceptance |
|---|---|---|---|---|---|
| PROA‑1 | SLA escalation ladder | `on_track→overdue→reminder→escalated` from process-skill policy | Core | 6 | Advance the (virtual) clock → ladder steps once, event recorded, next alarm re-armed; zero compute between |
| PROA‑2 | Durable scheduler | scheduler port: in-process `VirtualClock` (local) / Cloud Tasks (`bridge-followups`) | Core | 6 | Alarms idempotent + crash-safe; timer state persists and recovers on restart |
| PROA‑3 | Wake path | webhook receiver + `task_id`→session index + `PushNotificationConfig` (Cloud Run–hosted) | Core | 6,7 | Doorbell delivers "task X changed" only; restore from persistent stores; then `run_async` |
| OUT‑1 | Outbound A2A-client port | `a2a_client` port: fake transport (local) / Agent-Identity transport (GCP) | Core | 6 | Idempotent, crash-safe send; never carries a long-lived party bearer token |
| OUT‑2 | Real party-channel nudge | the actual chase send riding the outbound path | Core | 6 | A silent party gets a real reminder (email / portal link / party agent) on the ladder |
| OUT‑3 | Bridge-as-client dial-out | `DialOut` activity on the `a2a_client` port (`bridge/outbound.py`: card discovery → capability check against `process` → `message/send`) | Benefits | 11,12 | Bridge originates an A2A exchange against a counterparty |
| OUT‑4 | Deliver pattern | `Delivery` activity (`bridge/outbound.py`, by-reference `ArtifactRef`, no bytes, no bearer field) | Benefits | 3 | Bridge pushes a finished document outward |

---

## J. Skills ([skills](../design/skills.md))

| ID | Feature | Construct | Area | CUJ | Acceptance |
|---|---|---|---|---|---|
| SKILL‑1 | Process skill `address-proof` | `bridge-kind: process`, `bridge-pattern: collect`, `bridge-candidate-doctypes`, `assets/policy.yaml` | Address | 10 | Selects the Collect flow + candidate label space + SLA policy; carries a **prose** satisfaction description (no DSL) |
| SKILL‑2 | Doctype skill `gov-id` | schema `assets/schema.json`; key fields `name·address·doc_number·issuing_authority·expiry`; rules `not_expired`,`complete_fields` | Address | 10 | Matches the eval corpus field names; expired ID → flagged |
| SKILL‑3 | Doctype skill `utility-bill` | `issuer` (canonical) + key fields `name·service_address·statement_date`; rules `recent_bill(window_days:90)`,`address_present` | Address | 10 | Matches the eval corpus; issuer variants canonicalize identically |
| SKILL‑4 | Live doctype-add | skill folder → registry → Agent Card regenerates | Core | 8 | Upload a doctype skill to the running core; card updates, no redeploy |
| SKILL‑5 | Live use-case-add | process skill + its doctype skills installed through the `skill_registry` port; every per-request consumer (card, label space, policy loaders, extraction catalog, RFP board `live_policy`) reads the registry per call | RFP | 9 | Upload a whole use case to the running core while an exchange of another process is in flight: the next card GET advertises the new process, it is usable on the same app (Collect; an emergent-Collect process opens RFPs on the shared board), and the in-flight exchange reaches the terminal outcome it reaches without the upload; a malformed upload is skipped with a logged reason and refused at install, never removing an advertised skill |

---

## K. Ports & Adapters — Ring 2 ([ports](../design/ports.md))

> Thirteen Hexagonal ports, each with a local + GCP adapter designed together; one shared parametrized test suite (`@pytest.mark.port(...)`, `adapter` fixture over `("local","gcp")`). Parity is **terminal-outcome**, not ledger-identical; consuming code never keys off the active adapter.

| ID | Port | Local / GCP adapter | Acceptance |
|---|---|---|---|
| PORT‑1 | `sessions` | `DatabaseSessionService` on async sqlite (in-memory without a DSN) / `VertexAiSessionService` or `DatabaseSessionService` on Cloud SQL Postgres | No-op swap; terminal-outcome parity |
| PORT‑2 | `task_store` | local embedded / Cloud SQL Postgres | Adopting the managed store is a one-adapter swap |
| PORT‑3 | `exchange_store` | in-memory or sqlite / Cloud SQL Postgres — one relational impl both sides | Materialized Exchange records (the RFP board): `(kind, ref)` key, A2A context indexed, JSON doc column, version compare-and-set (create-only at 0); cross-replica read-after-write parity |
| PORT‑4 | `skill_registry` | directory / GCS bucket (one loader `skill_meta_from_dir` → `SkillMeta` on both) | Skill authored once installs unchanged both sides; card from same metadata; a malformed skill folder is skipped from the listing with a logged reason (never raised), and install validates before any write |
| PORT‑5 | `memory_bank` | `InMemoryMemoryService` or SQLite / `VertexAiMemoryBankService` | Advisory-only party-memory interface; never consulted as a disposition or completeness gate input |
| PORT‑6 | `scheduler` | `VirtualClock` / Cloud Tasks | Idempotent crash-safe alarms; recover on restart |
| PORT‑7 | `a2a_client` | fake transport / Agent-Identity transport | Crash-safe re-drivable send; no long-lived party token |
| PORT‑8 | `extraction` | fixture / Gemini / Document AI (**capability axis**, not local/gcp) — engine selected per deployment (`BRIDGE_EXTRACTION_ENGINE`: fixture \| gemini \| docai = Gemini + one Document AI OCR pre-pass) | Gates read signals engine-agnostically; never hard-fails on an uncovered doctype |
| PORT‑9 | `email_ingress` | landing directory of `.eml` + attachments + processed markers / GCS landing bucket, same object layout (link: [email-intake](../design/email-intake.md)) | Create-only writes on both sides (landing is the idempotency check; inbox − processed = queue); shared parity test `tests/test_email_ingress_port.py` |
| PORT‑10 | `audit_vault` | `InMemoryAuditVaultStore` / `GcpAuditVaultStore` (7-year WORM GCS bucket) | Write-once lock with SHA-256 verification and `BRIDGE_PORT_MODE=gcp` fail-fast guard |
| PORT‑11 | `operational_store` | `InMemoryOperationalStore` / `SqlOperationalStore` (AlloyDB / Cloud SQL Postgres) | Bi-temporal append-only facts (`recorded_at` vs `effective_from`), `Decimal` consolidation, and role-based views |
| PORT‑12 | `action_inbox` | `InMemoryActionInboxStore` / `SqlActionInboxStore` | A2UI v0.9 Action Inbox with `4+1` governance verbs and immutable RFC 6902 JSON Patch audit trails |
| PORT‑13 | `agent_gym` | `InMemoryAgentGymEngine` / `SqlAgentGymEngine` | Continuous learning, Two-Sided Oversight (`RSR`), 3-Tier evaluation gate, Programmable Trust, and `SKILL.md` compilation |

---

## L. GCP substrate & zones ([substrate](../design/gcp-substrate.md), [zones](../design/zones.md))

> Terraform is organized **by substrate primitive** (what it provisions), never one-module-per-platform-service. "Terraform provisions substrate; the ports provision services."

| ID | Feature | Terraform / construct | Acceptance |
|---|---|---|---|
| GCP‑1 | Runtime | `runtime` — Cloud Run v2 hosting the ADK `App` + the custom HTTP endpoints (A2UI, artifact fetch, webhooks); state via the Cloud SQL-backed ports | Hosts custom HTTP endpoints; state via Cloud SQL-backed ports |
| GCP‑2 | Network + zones | `network` — two subnets (trust zones) + firewall isolation | Isolation at the network layer; one deployment = one trust domain |
| GCP‑3 | Gateway (ingress) | `gateway` — HTTPS LB + Cloud Armor WAF → Serverless NEG (`enable_gateway`) + per-party scoping | A party agent addresses only its own leg's context |
| GCP‑4 | Identity | `iam` — `bridge-runtime` SA + grants + Cloud Tasks OIDC token-creator + Cloud SQL IAM user | Bridge↔backend/managed calls authenticate as workloads |
| GCP‑5 | Database | `database` — Cloud SQL Postgres 16, private IP, `cloudsql.iam_authentication = "on"` | Backs task/exchange/session/operational/inbox/gym stores + SLA read-model |
| GCP‑6 | Tasks | `tasks` — `bridge-followups` Cloud Tasks queue | Backs the scheduler port GCP adapter |
| GCP‑7 | Secrets / Skill Registry / frontend | `secrets` (DSN via secret env), GCS module (registry + WORM audit vault), `frontend` | DB DSN never plaintext; registry holds skill folders; surfaces deployed by zone |
| GCP‑8 | Deployed-path parity | single deploy step; `terraform test` + `mock_provider` (plan-time); provisioned parity run | Address runs deployed, not just local; credential-free plan tests green |
| GCP‑9 | Deployed RFP board host + email processor | internal-only board host, the one public `allUsers` email webhook (clientState-authenticated), Cloud Scheduler driving `/email/drain` | Board host serves RFP board + drains the email landing bucket; the email webhook is the public Gateway exception |

---

## M. Contract & test harness ([layout](../design/architecture.md))

| ID | Feature | Construct | Acceptance |
|---|---|---|---|
| CONTRACT‑1 | Shared `contract/` package | pydantic-only (no ADK / no a2a-sdk); editable path dep | Only package both `bridge/` and `agents/` import; `bridge/` never imports `agents/` (isolation-guard test) |
| TEST‑1 | Shared port suite | `Port` enum; `@pytest.mark.port`; `adapter` fixture; `BRIDGE_TEST_GCP` gate | Same body asserts local + gcp; invalid port name rejected at collection |
| TEST‑2 | Eval corpus parity | `tests/address/_corpus.py`, `test_corpus.py`; `expected.json`, `timeline.json` | Well-formedness, gate + collect-path parity, canonicalization parity, category coverage, no-PII, timeline validity; every corpus id resolves through the fixture extractor; every provider-portal sample id (`UploadPrompt.tsx` `CORPUS_IDS`) resolves to a corpus doc |
| TEST‑3 | Golden runs | Path-A instant, distinct-issuer accept, same-issuer reject, resubmission, HITL, escalation | Run against fixture and Gemini adapters, and on the deterministic and hybrid edges, to the same terminal outcome |
| TEST‑4 | Experimental-surface register | living list: `RemoteA2aAgent` (`@a2a_experimental`, `use_legacy=True`), `ResumabilityConfig` (`@experimental`, on `App`) | Each surface has an owner + revisit trigger; re-audit on any `google-adk` minor bump |
| TEST‑5 | Consumer conformance run | `tests/conformance/test_benefits_conformance.py`, `wiki/evals/benefits/conformance.json` | Carrier PDF by reference → A2A → the consumer's `BenefitsQuote` |

---

## N. Benefits — Negotiate over a program ([patterns](../design/patterns.md), [aggregate-model](../design/aggregate-model.md), [fulfillment-graph](../design/fulfillment-graph.md), [outbound](../design/outbound.md))

| ID | Feature | Construct | Area | CUJ | Acceptance |
|---|---|---|---|---|---|
| BEN‑1 | Programs fan-out with per-leg isolation | `bridge/program.py` `fan_out`/`add_leg`; `tests/test_program.py` | Benefits | 11 | Each carrier leg isolated; program state aggregates across legs |
| BEN‑2 | Multilingual extraction + FX normalization | `bridge/normalize.py`, `FxRates`/`fixed_fx`, currency/language inside `key_fields`; `tests/test_normalize.py` | Benefits | 11 | Non-USD quotes normalized to USD; non-English text extracted |
| BEN‑3 | Standard-gap flagger, sense A | `bridge/standard.py` `flag_quote`/`flag_program` | Benefits | 11 | Bridge flags quotes/programs that deviate from standard thresholds |
| BEN‑4 | Program comparison view, recompute-on-read | `bridge/comparison.py`, dashboard `GET /programs` + SSE; `tests/test_program_comparison_view.py` | Benefits | 11 | Live comparison view recomputes on every read; no stale cache |
| BEN‑5 | Versioned revise loop → bind → rollup + audit, cap → escalate | `bridge/negotiate.py` `revise`/`bind_leg`/`roll_up`; `tests/test_cuj11_negotiate_bind_rollup.py` | Benefits | 11 | Multi-turn revise; bind locks a leg; rollup aggregates; cap escalates |
| BEN‑6 | Cross-framework A2A interop | `agents/benefits/carrier_a_adk.py` + `carrier_b_langgraph.py`; `tests/test_cuj12_interop.py` | Benefits | 11,12 | ADK and LangGraph carriers interoperate over the same A2A wire |
| BEN‑7 | Structured rate-table rows | sibling `tables` map, contract minor bump | Benefits | 3 | Rate tables as structured sibling field, not opaque blob |

---

## O. RFP views & email intake ([rfp-views](../design/rfp-views.md), [email-intake](../design/email-intake.md))

| ID | Feature | Construct | Area | CUJ | Acceptance |
|---|---|---|---|---|---|
| RFP‑1 | Consumer read API + manual chase | `bridge/dashboard/rfp_api.py`: `GET /rfps`, `GET /rfps/{id}`, `POST /rfps/{id}/chase`, seq-keyed `Nudge`; `tests/test_rfp_views.py` | RFP | 13 | RFP board exposes read API; operator can manually chase a carrier |
| RFP‑2 | Leg-addressed carrier checklist + RFP board views | `frontend/shared` `domain/rfp.ts`, `RfpChecklist` | RFP | 13 | UI components render RFP state + per-carrier checklist |
| EMAIL‑1 | Dedicated M365 mailbox via Microsoft Graph, token-correlated, create-only landing | `bridge/email_ingress/` `graph.py`/`correlation.py`/`mime.py`; `tests/test_email_correlation.py`, `tests/test_email_graph.py` | Benefits | — | Email ingested via Microsoft Graph; correlated by token; landed create-only |
| EMAIL‑2 | Drain on the board-owning host | served drain + poll loop + `python -m bridge.email_ingress drain` CLI; `tests/test_email_served.py` | Benefits | — | Landed email drained by the board-owning host; CLI verb available |

---

## P. Extraction depth ([ports](../design/ports.md), [docai-prepass](../design/docai-prepass.md))

| ID | Feature | Construct | Area | CUJ | Acceptance |
|---|---|---|---|---|---|
| EXT‑1 | Page-window chunking for large documents | `bridge/extraction/chunking.py`, explicit `max_output_tokens`, bisect on truncation, `ExtractionTruncated`; scale harness `tests/perf/`; `tests/test_extraction_chunking.py`, `tests/test_gemini_chunking.py` | Core | — | Large docs chunked into page windows; truncation handled via bisect; scale tests validate |
| EXT‑2 | Document AI OCR pre-pass | `bridge/extraction/docai.py`; `tests/test_docai_extraction.py` | Core | — | Document AI runs OCR pre-pass; output fed to Gemini |

---

## Q. 6-Zone Enterprise Data Architecture, Agent Gym & BigQuery SQL Eval ([agent-gym](../design/agent-gym.md), [harness-evolve](../design/harness-evolve.md))

| ID | Feature | Construct | Area | CUJ | Acceptance |
|---|---|---|---|---|---|
| ENT‑1 | Zone 3 WORM Audit Vault + Edge PII Redaction | `bridge/vault.py` (`redact_personal_data`, `GcpAuditVaultStore`) | Enterprise | 1,2 | Redacts SSN/TIN/DOB/routing numbers before storage; write-once SHA-256 retention lock |
| ENT‑2 | Zone 3–4 Bi-Temporal Operational Store + Decimal Consolidation | `bridge/operational_store/` (`SqlOperationalStore`, `ConsolidationEngine`) | Enterprise | 1,10 | Append-only `recorded_at` vs `effective_from` facts, Source Priority Hierarchy, and `Decimal` math |
| ENT‑3 | Zone 4 Role-Based Views + BigQuery Iceberg Sync | `bridge/operational_store/` (`broker_view`, `claims_view`, `underwriter_view`, `export_bigquery_sync_batch`) | Enterprise | — | Click-to-source-page bounding-box traceability and watermarked incremental Iceberg CDC |
| ENT‑4 | Zone 5 A2UI v0.9 Action Inbox & Governed Surfaces | `bridge/a2ui_inbox/`, `bridge/user_interaction/` | Enterprise | 2,5 | Security-zone catalog isolation, `4+1` governance verbs, and immutable RFC 6902 JSON Patch audit trails |
| ENT‑5 | Zone 6 Knowledge Catalog + SPIFFE Zero-Trust Identity | `bridge/knowledge_catalog.py`, `bridge/identity.py` | Enterprise | — | 6-hop lineage graph, asymmetric `RS256`/`ES256` SPIFFE JWT-SVIDs, and OBO least-privilege intersection |
| ENT‑6 | Agent Gym Continuous Learning & Programmable Trust | `bridge/agent_gym/`, `bridge/calibration.py` | Enterprise | 5 | Two-Sided Oversight (`RSR >= 0.80`), 3-Tier evaluation gate, Programmable Trust ladder, and `SKILL.md` compilation |
| ENT‑7 | Gemini platform Agent Registry + Agent Gateway | `bridge/agent_gym/registry_gateway.py` | Enterprise | 8,9 | 4 Registry component types (`urn:agent`, `urn:mcp`, `urn:endpoint`, `urn:skill`), `CLIENT_TO_AGENT` Model Armor/DLP, and `AGENT_TO_ANYWHERE` CEL |
| ENT‑8 | OpenTelemetry `gen_ai.*` + BigQuery 3-Tier SQL Trace Eval | `bridge/telemetry/`, `terraform/modules/telemetry/` (`v_trace_sql_evaluation`) | Enterprise | — | 22 pipeline steps traced to Cloud Trace v2 + BigQuery `otel_spans`; 5 `Rule 0` SQL gates (`$0`), selective `AI.GENERATE_TABLE`, and Rogan-Gladen debiasing |

---

## See also
- [`TODO.md`](../TODO.md) — the phased build order over these features
- [`roadmap.md`](roadmap.md) — high-level milestone view
- [repo layout](../design/architecture.md) · [CUJ catalog](../spec/cujs.md) · [the Bridge](../design/bridge.md)
