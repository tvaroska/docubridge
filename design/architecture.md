---
type: reference
up: "bridge.md"
tags: [bridge, layout]
---

# Project layout

> One map of the repository and the boundaries between its parts. It does not re-specify any component (each links to its design note).

## Top-level layout

```
docubridge/
├── contract/        shared Pydantic wire types — the ONLY package both bridge/ and agents/ import
├── bridge/          the reusable Bridge CORE (the platform showcase) — never imports agents/
│   ├── edge/        A2A server, Agent Card, executor router, LlmAgent leg, artifact fetch, email webhook
│   ├── portal/      A2UI Path-B host + carrier checklist
│   ├── dashboard/   ops read-model + SSE, /rfps API, RFP board host
│   ├── timewarp/    local-only time-warp host
│   ├── ports/       local/ + gcp/ adapters, registry.py
│   ├── extraction/  extraction engines (fixture, gemini with Agent Gym hints, docai OCR pre-pass), page-window chunking, container + office prepass
│   ├── email_ingress/  email intake + correlation
│   ├── demos/       fenced demo-host glue (seeding) — the core never imports it
│   ├── adk/         ADK integration core (experimental-surface register)
│   ├── refinery/    Schema Refinery: OKF loader, taxonomy assay, 3-Tier gate, ontology revisions
│   ├── agent.py             the root `document_bridge` LlmAgent on stock ADK
│   ├── collect.py           classified-ledger projection + the one-turn collect path
│   ├── classification.py    issuer canonicalization + label-space check (sense A)
│   ├── signals.py           four-signal disposition model behind one SignalProvider
│   ├── disposition.py       pure deterministic disposition gate
│   ├── gatepin.py           gate inversion — pinned gate inputs held at the agent
│   ├── calibration.py       threshold corridor + autorater / HITL calibration math
│   ├── policy.py            disposition-policy loader (process skill `disposition:` block)
│   ├── doctypes.py          the one reader of a doctype's schema (OKF bundle first, generated `schema.json` second) + ontology slicing
│   ├── satisfaction.py / satisfaction_llm.py  advisory set-satisfaction proposal (rule / model)
│   ├── patha.py / pathb.py  Path-A validate-only + Path-B extraction graph
│   ├── offscript.py         off-script intent routing tools
│   ├── program.py / normalize.py / standard.py / comparison.py / negotiate.py  Benefits fan-out, normalize, gap-flag, compare, negotiate
│   ├── rfp.py               emergent RFP collection process
│   ├── followup.py / sla.py / scheduler.py  chase ladder, SLA engine, virtual-clock scheduler
│   ├── outbound.py / netguard.py  outbound A2A client + default-deny egress guard
│   ├── exchange_store.py    materialized Exchange records behind the exchange_store port
│   ├── sql_runtime.py       shared per-DSN async SQLAlchemy runtime for all SQL stores
│   ├── vault.py             Zone 2/3 PII redaction & WORM Cloud Storage Audit Vault
│   ├── operational_store/   Zone 3/4 bi-temporal Operational Store, Decimal consolidation & Iceberg CDC
│   ├── knowledge_catalog/   Zone 6 Knowledge Catalog, 6-hop lineage, glossary & asset map
│   ├── identity.py          SPIFFE JWT-SVID verification, OBO compound identity & tenant isolation
│   ├── a2ui_inbox/          Zone 5 A2UI v0.9 Action Inbox, SQL store, SSE deltas & RFC 6902 audit trail
│   ├── agent_gym/           Zone 6 Agent Gym package (models, engine, sql_store, registry_gateway)
│   ├── telemetry/           End-to-end OpenTelemetry spans, BatchSpanProcessor Cloud Trace v2 & BigQuery
│   └── user_interaction/    Zone 5/6 FastAPI package (auth, surfaces, demo_seed, schemas, routes)
├── demo/            5-scene interactive Enterprise Showcase Console (enterprise-demo.{html,css,js}) + GCP recorder
├── agents/          service-agent CONSUMERS + demo drivers
│   ├── address/     the service agent: Workflow, RemoteA2aAgent collect, gate, CLI driver, wake
│   ├── console/     the Agent Console console_server
│   └── benefits/    simulated carrier counterparty agents, ADK + LangGraph
├── skills/          Agent Skills folders the Bridge loads at runtime (doctype + process)
│   ├── address-proof, gov-id, utility-bill (Address)
│   ├── benefits-procurement, benefits-quote, benefits-rfp (Benefits & RFP)
│   └── commercial-loan-underwriting, trid-closing-disclosure, cre-credit-agreement, mai-appraisal, commercial-property-quote (Financial Services, Banking & Insurance)
├── frontend/        four apps (provider portal = A2UI Path-B; console + dashboard = SSE; timewarp)
│   ├── provider-portal/, agent-console/, ops-dashboard/, timewarp/
│   ├── shared/      @docubridge/shared: theme, Panel, Badges, contract mirror
│   └── e2e/         Playwright, network-stubbed
├── terraform/       IaC, organized by GCP substrate primitive
├── scripts/         local run-*.sh launchers, deploy.sh, parity.sh
├── tests/           the shared port suite (local + GCP adapters) + demo golden runs
│   ├── conformance/, perf/
│   └── address/, test_*.py
├── spec/            THE WHAT — requirements: cujs.md, standards.md, demos/, open-questions.md (forkable, protected)
├── design/          THE HOW — bridge.md → model / edges / knowledge-model / runtime / architecture; the design source of truth (status-free)
├── docs/            implementation-facing docs (features.md, roadmap.md, upstream-adk-issues.md)
├── wiki/evals/      the eval corpus (spec corpus — a runtime fixture, referenced by code + tests)
└── catalog/doctypes/ OKF doctype bundles — canonical field schemas, contrastive descriptions, ontology edges (design/ontology.md)
```

**Design vs. planning.** `design/` is the design source of truth (status-free), and `spec/` holds forkable requirements. Sequencing lives outside them: `docs/features.md` (the feature catalog) and `docs/roadmap.md` (milestones + live status; an ephemeral root `TODO.md` may be created during an active sprint). These reference `design/`; it does not depend on them.

**The load-bearing invariant:** `bridge/` **never imports** `agents/` ([shared contract types](adk.md)). The core is a reusable artifact; the agents consume it. Both may import `contract/` (the shared third package) — that is the only permitted cross-link. Parity of logic that must agree across the two (canonicalization, disposition, `is_satisfied`) is held by the **shared port suite + an isolation-guard test**, never by import.

### `bridge/` — the core
The [`LlmAgent` `document_bridge`](adk.md) + tools; extraction as an `AgentTool`; HITL as a `LongRunningFunctionTool`; the [ports](ports.md) behind their ABCs (each with a local + GCP adapter); the [A2A edge](a2a-edge.md) and [human edge](human-edge.md); disposition + classification (`bridge.classification.canonicalize_issuer`); the extraction fixture table (`bridge.extraction.fixture`); the [outbound A2A client](outbound.md).

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

### `catalog/doctypes/` vs `skills/` vs `wiki/evals/`
A bundle in [`catalog/doctypes/`](ontology.md) says what a document *is* (field names, types, `required`, enums, tables, edges — a doctype skill's `assets/schema.json` is generated from it); a [skill](skills.md) says *how the Bridge collects* it and behaves (runtime config); an [eval](../spec/cujs.md) is *how we check it behaved* (spec). Keep the three separate ([knowledge model](knowledge-model.md)).

Installed skills:
- **Address:** `address-proof` (process), `gov-id`, `utility-bill` (doctypes)
- **Benefits:** `benefits-procurement` (process), `benefits-quote` (doctype)
- **RFP:** `benefits-rfp` (emergent-Collect process)
- **Financial Services & Insurance:** `commercial-loan-underwriting` (process), `trid-closing-disclosure`, `cre-credit-agreement`, `mai-appraisal`, `commercial-property-quote` (doctypes)

The eval corpus is the enforced source of truth for expected dispositions; `tests/test_doctype_consistency.py` holds each doctype's generated schema to its bundle.

### `frontend/` — four apps, three actor surfaces
`provider-portal/` (external zone), `agent-console/` + `ops-dashboard/` (internal zone), `timewarp/` (presenter control, local-only).

`shared/` (`@docubridge/shared`) holds the shared theme (`theme.ts`), `Panel.tsx`, `Badges.tsx`, `domain/contract.ts` (snake_case → camelCase mirror) and `domain/rfp.ts`, with vitest tests. The apps import it.

`e2e/` holds Playwright end-to-end tests, stubbed at the network boundary. See [human edge](human-edge.md).

### `terraform/` — substrate primitives
`network`, `runtime` (Cloud Run v2), `gateway`, `iam`, `database` (Cloud SQL), `tasks` (Cloud Tasks), `secrets`, `frontend`, and the `gcs` module holding the Skill Registry, Artifacts and email-landing buckets ([runtime](runtime.md)). Terraform provisions substrate; the ports provision services.

## Settled design decisions
- **ADK is pinned `google-adk >= 2.11.0, < 3`** — the [experimental-surfaces list](adk.md) is the risk register.
- **Three demos only** (Address, Benefits, RFP); the former "Maturity" demo is dropped ([human edge](human-edge.md) folds the X-Ray in as a presentation mode).
- **Outbound is first-class** ([outbound](outbound.md)); the pull spine remains the core motion.
- **Completeness** is the app's Sense-B decision; a process skill may carry a **prose** satisfaction description the Bridge best-efforts (no DSL) ([collect](collect.md), [skills](skills.md)).

Remaining genuinely-open items live in [open-questions](../spec/open-questions.md).

## Related
- [the Bridge](bridge.md) · [model](model.md) · [edges](edges.md) · [knowledge model](knowledge-model.md) · [runtime](runtime.md) · [demos](../spec/demos/demo-suite.md) · [CUJ catalog](../spec/cujs.md)
