---
type: reference
up: "bridge.md"
related:
  - "adk.md"
  - "seams.md"
  - "gcp-substrate.md"
  - "../spec/demos/demo-suite.md"
  - "../spec/cujs.md"
tags: [bridge, layout]
---

# Project layout

> One map of the repository and the boundaries between its parts. It does not re-specify any component (each links to its design note).

## Top-level layout

```
docbride3/
├── contract/        shared Pydantic wire types — the ONLY package both bridge/ and agents/ import
├── bridge/          the reusable Bridge CORE (the platform showcase) — never imports agents/
│   ├── edge/        A2A server, Agent Card, executor router, LlmAgent leg, artifact fetch, email webhook
│   ├── portal/      A2UI Path-B host + carrier checklist
│   ├── dashboard/   ops read-model + SSE, /rfps API, RFP board host
│   ├── timewarp/    local-only time-warp host
│   ├── seams/       local/ + gcp/ adapters, registry.py
│   ├── extraction/  extraction engine (fixture, gemini with Agent Gym hints, docai OCR pre-pass)
│   ├── email_ingress/  email intake + correlation
│   ├── demos/       fenced demo-host glue (seeding) — the core never imports it
│   ├── vault.py             Zone 2/3 PII redaction & WORM Cloud Storage Audit Vault
│   ├── operational_store.py Zone 3/4 bi-temporal Operational Store, Decimal consolidation & Iceberg CDC
│   ├── knowledge_catalog.py Zone 6 Knowledge Catalog, 6-hop lineage, glossary & asset map
│   ├── identity.py          SPIFFE JWT-SVID verification, OBO compound identity & tenant isolation
│   ├── a2ui_inbox.py        Zone 5 A2UI v0.9 Action Inbox, SSE deltas & RFC 6902 audit trail
│   ├── agent_gym.py         Zone 6 Agent Gym continuous learning, stress suite & Programmable Trust
│   ├── telemetry.py         End-to-end OpenTelemetry spans, Cloud Trace v2 & BigQuery streaming
│   └── user_interaction.py  Zone 5/6 FastAPI router (/v1/a2ui/*, /v1/catalog/*, /v1/gym/*, /v1/telemetry/*, /demo)
├── demo/            5-scene interactive Enterprise Showcase Console (enterprise-demo.html) + GCP recorder
├── agents/          service-agent CONSUMERS + demo drivers
│   ├── address/     the service agent: Workflow, RemoteA2aAgent collect, gate, CLI driver, wake
│   ├── console/     the Agent Console console_server
│   └── benefits/    simulated carrier counterparty agents, ADK + LangGraph
├── skills/          Agent Skills folders the Bridge loads at runtime (doctype + process)
│   ├── address-proof, gov-id, utility-bill (Address)
│   ├── benefits-procurement, benefits-quote (Benefits)
│   └── benefits-rfp (emergent-Collect process)
├── frontend/        four apps (provider portal = A2UI Path-B; console + dashboard = SSE; timewarp)
│   ├── provider-portal/, agent-console/, ops-dashboard/, timewarp/
│   ├── shared/      @docbride/shared: theme, Panel, Badges, contract mirror
│   └── e2e/         Playwright, network-stubbed
├── terraform/       IaC, organized by GCP substrate primitive
├── scripts/         local run-*.sh launchers, deploy.sh, parity.sh
├── tests/           the shared seam suite (local + GCP adapters) + demo golden runs
│   ├── conformance/, perf/
│   └── address/, test_*.py
├── spec/            THE WHAT — requirements: cujs.md, standards.md, demos/, open-questions.md (forkable, protected)
├── design/          THE HOW — design docs (this dir); the design source of truth (status-free)
├── docs/            implementation-facing docs (features.md, roadmap.md, upstream-adk-issues.md)
├── wiki/evals/      the eval corpus (spec corpus — a runtime fixture, referenced by code + tests)
└── TODO.md          tasks + live status
```

**Design vs. planning.** `design/` is the design source of truth (status-free), and `spec/` holds forkable requirements. Sequencing lives outside them: `docs/features.md` (the feature catalog), `docs/roadmap.md` (milestones), and `TODO.md` (the phased plan + live status). These reference `design/`; it does not depend on them.

**The load-bearing invariant:** `bridge/` **never imports** `agents/` ([shared contract types](adk.md)). The core is a reusable artifact; the agents consume it. Both may import `contract/` (the shared third package) — that is the only permitted cross-link. Parity of logic that must agree across the two (canonicalization, disposition, `is_satisfied`) is held by the **shared seam suite + an isolation-guard test**, never by import.

### `bridge/` — the core
The [`LlmAgent` `document_bridge`](adk.md) + tools; extraction as an `AgentTool`; HITL as a `LongRunningFunctionTool`; the [seams](seams.md) behind their ABCs (each with a local + GCP adapter); the [A2A edge](a2a-edge.md) and [A2UI edge](a2ui-edge.md); disposition + classification (`bridge.classification.canonicalize_issuer`); the extraction fixture table (`bridge.extraction.fixture`); the [outbound A2A client](outbound.md).

HTTP hosts: `bridge.edge.server:build_a2a_app` (A2A JSON-RPC server + `/artifacts` fetch), `bridge.portal.server:build_portal_app` (provider portal), `bridge.dashboard.server:build_dashboard_app` (ops dashboard, SSE, `/rfps` routes), `bridge.dashboard.board_host:build_rfp_board_app` (deployed RFP board host), `bridge.edge.email_ingress:build_email_app` (email intake webhook), `bridge.timewarp.server:build_timewarp_app` (local-only), `bridge.demos.rfp_demo:build_rfp_demo_app` (fenced local RFP demo host), `bridge.demos.showcase:build_showcase_app` (fenced local showcase runner), `bridge/edge/mock.py` (mock Bridge).

The edge is hybrid ([a2a-edge](a2a-edge.md)): structured traffic takes the deterministic Collector (model-free), text-only intents / HITL park / review resumes take the `document_bridge` LlmAgent leg.

#### Core vs. demo hosts
The core names no skill. An emergent-Collect component learns everything domain-specific from its process skill: `bridge.rfp.load_rfp_policy(skill)` reads the SKILL.md frontmatter (`bridge-kind: process`, `bridge-pattern: collect`, `bridge-candidate-doctypes` — the label space) and the policy's `rfp:` block (`tick_seconds`, the item key `item_field`) into an `RfpPolicy`, and the aggregate (`receive`, `to_exchange_turn`), the email intake and the carrier checklist read the process name, label space and item key from that policy. Every RFP is projected and mutated with **its own process's** policy (`RfpBoard.policy_for`). A board has a home process (loaded at build); a board bound with `live_policy` (the board host, the A2A opener) also serves any emergent-Collect process installed live in the skill registry, resolved per RFP per read (`bridge/edge/skills.py::rfp_policy_for`); a board without it refuses an RFP of any other process. A second emergent-Collect use case is therefore a new process skill + its doctypes, with no core edit and no host rebuild (`tests/test_rfp_second_use_case.py` runs a synthetic one end to end; `tests/test_live_rfp_use_case_add.py` adds one live next to a running exchange).

Demo-host glue — seeding synthetic data, naming a demo skill, reading the `wiki/evals/` corpus — lives in the fenced package `bridge/demos/` (e.g. `bridge.demos.rfp_demo`, the local RFP demo host, and `bridge.demos.showcase`, the showcase runner: one edge, card and dashboard over shared stores). Nothing else in `bridge/` imports it, and no string constant in the core equals an installed skill name (both enforced by `tests/test_isolation_guard.py`). It sits inside `bridge/` rather than `agents/` because `agents/` never imports `bridge/` (the mock→real swap property) and a demo host composes core builders directly.

Two named exceptions carry a skill-name literal in the core, each allowlisted with its reason: the mock Bridge test double (`bridge/edge/mock.py`, scripted from the Address corpus) and the corpus calibration tool (`bridge/calibration.py`, mapping each corpus file to its label space). An unresolved process (no loaded process skill of that name, or one without `bridge-candidate-doctypes`) gets an empty label space, so every document is rejected `unsupported_doctype`; the core never falls back to another process's doctypes. The RFP-named wire vocabulary (`/rfps`, `RfpCell.product_line`, "carriers" for parties) is the aggregate's API naming, not a binding to any skill.

### `agents/` — consumers & drivers
The [service-agent `Workflow` shape](service-agent-architecture.md): a `RemoteA2aAgent` collect node, a deterministic gate node, a presenter.

`agents/address/` holds the consumer construct: `collect.py` (`build_bridge_remote_agent`, the `RemoteA2aAgent` collect node), `wire.py` (genai-Part → `ExchangeTurn` decode) and `interceptor.py` (resume-stamping `RequestInterceptor`) — the latter two are Ring-3 workarounds tracked in `docs/upstream-adk-issues.md`. The package also includes the gate (`gate.py`, `is_satisfied`), the workflow/app (`workflow.py`, `app.py`), the CLI driver (`driver.py`, `__main__.py`) and the custom push-doorbell receiver (`wake.py`).

`agents/console/` holds the Agent Console `console_server` (`agents.console.server:build_console_app`, FastAPI + SSE). It runs `agents.address.driver.run_collection` and never imports `bridge/`. Its test is `tests/address/test_console.py`.

`agents/benefits/` holds the simulated carrier counterparty A2A agents the Bridge dials: Carrier A built on ADK (`carrier_a_adk.py`), Carrier B built on LangGraph (`carrier_b_langgraph.py`), plus `server.py`, `wire.py`, `quotes.py`. These are external-zone A2A servers. They are not a service-agent `Workflow`.

The portal lives in `bridge/portal/` (`bridge.portal.server:build_portal_app`, the A2UI Path-B host + `rfp_checklist.py`, the leg-addressed carrier checklist), not in `agents/address/`.

### `skills/` vs `wiki/evals/`
[Skills](skills.md) are *how the Bridge behaves* (runtime config); [evals](../spec/cujs.md) are *how we check it behaved* (spec). Keep them separate.

Installed skills:
- **Address:** `address-proof` (process), `gov-id`, `utility-bill` (doctypes)
- **Benefits:** `benefits-procurement` (process), `benefits-quote` (doctype)
- **RFP:** `benefits-rfp` (emergent-Collect process)

The eval corpus is the enforced source of truth for field names + expected dispositions.

### `frontend/` — four apps, three actor surfaces
`provider-portal/` (external zone), `agent-console/` + `ops-dashboard/` (internal zone), `timewarp/` (presenter control, local-only).

`shared/` (`@docbride/shared`) holds the shared theme (`theme.ts`), `Panel.tsx`, `Badges.tsx`, `domain/contract.ts` (snake_case → camelCase mirror) and `domain/rfp.ts`, with vitest tests. The apps import it.

`e2e/` holds Playwright end-to-end tests, stubbed at the network boundary. See [frontend](frontend.md).

### `terraform/` — substrate primitives
`network`, `runtime` (Cloud Run v2), `gateway`, `iam`, `database` (Cloud SQL), `tasks` (Cloud Tasks), `secrets`, `frontend`, and the `gcs` module holding the Skill Registry, Artifacts and email-landing buckets ([substrate](gcp-substrate.md)). Terraform provisions substrate; the seams provision services.

## Settled design decisions
- **ADK is pinned `google-adk >= 2.7.0, < 3`** — the [experimental-surfaces list](adk.md) is the risk register.
- **Three demos only** (Address, Benefits, RFP); the former "Maturity" demo is dropped ([frontend](frontend.md) folds the X-Ray in as a presentation mode).
- **Outbound is first-class** ([outbound](outbound.md)); the pull spine remains the core motion.
- **Completeness** is the app's Sense-B decision; a process skill may carry a **prose** satisfaction description the Bridge best-efforts (no DSL) ([collect](collect.md), [skills](skills.md)).

Remaining genuinely-open items live in [open-questions](../spec/open-questions.md).

## Related
- [the Bridge](bridge.md) · [running on ADK](adk.md) · [seams](seams.md) · [Agent Gym & Programmable Trust](agent-gym.md) · [demos](../spec/demos/demo-suite.md) · [CUJ catalog](../spec/cujs.md)
