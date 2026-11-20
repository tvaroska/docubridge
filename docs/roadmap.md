# Roadmap (`docubridge`)

Historical architectural decisions live in [DECISIONS.md](../DECISIONS.md), non-negotiable invariants in [CRITICAL.md](../CRITICAL.md), feature definitions in [features.md](features.md), and design specifications under [design/bridge.md](../design/bridge.md).

## Where we are

`docubridge` (`v1.17.0`) ships a 6-Zone governed A2A document mediation core across all 14 dual-mode (`local`/`gcp`) Hexagonal `Port` boundaries (`sessions`, `task_store`, `exchange_store`, `skill_registry`, `memory_bank`, `scheduler`, `a2a_client`, `extraction`, `email_ingress`, `audit_vault`, `operational_store`, `action_inbox`, `agent_gym`, `knowledge_catalog`), backed by `1,912` passing unit/contract tests (`>=90%` coverage), `56` credential-free `terraform test` checks, Schema Refinery (`P8-1`–`P8-8`), generic `ScenarioEngine` (`P9-1`–`P9-3`), **`P10` (`v1.15.0`)** security/IaC/SQL/supply-chain hardening, **`P11` (`v1.16.0`)** declarative collection strategy (`ADR-14`/`ADR-15`) and canonical A2A v1.0 counterparty dial-out, and completed **`P12` (`v1.17.0`)** (Tier-2 `ui-design` governance with `.stitch/DESIGN.md`, offline bundled fonts, 4 Vite favicons, MSVG-safe SVG, executed Playwright E2E CI gate, zero standards debt in `tests/standards_debt.json`, `SCENARIO_LITERAL_ALLOWLIST = {}`, `<800` non-blank LOC across all modules, `mypy --strict`, all 16 Ruff rule families, and `0` narration comments). All scheduled milestones (`P10`–`P12`) are shipped; only `Unscheduled backlog` items remain.

## Milestones at a glance

All scheduled milestones (`P10`–`P12`) are shipped (see `## Shipped` below); only `## Unscheduled backlog` remains.

## Unscheduled backlog

- **Live Single-Project Showcase Cutover & Rehearsal Gates**: Execute `scripts/deploy.sh` + `scripts/parity.sh`, `<20 min` cold wipe/restore drill, and 3–5 min backup screen recording onto the single co-located `Showcases` GCP project (`hibernate`) once operator gives explicit deploy approval.
- **Remote Terraform GCS State Backend**: Configure optional GCS backend (`gs://<project-id>/tfstate/docubridge`) so multi-operator or Cloud Build deploys share locked state rather than local `.tfstate`.
- **3-Arm Ontology Ablation Benchmark (`FX-1`)**: Compare Schema-Only vs `+ContrastiveDescription` vs `+OntologyGraph` arms in [bridge/refinery/assay.py](../bridge/refinery/assay.py) (`RefineryAssayReport`).
- **Role-Isolated `/portal`, `/overwatch` & `/registry` Surfaces (`Demo M3`)**: Dedicated role-scoped web surfaces composed by `/ebc` over SSE.
- **Managed Agent Runtime, Agent Registry & Network Services `AgentGateway` (`Demo M4`)**: Swap in-process Registry/Gateway shims for managed platform APIs once GA-stable.
- **Staged A2UI Widgets (`AR-2`), Prefix-Cache / Signed-URL Compaction (`AR-3`) & SQL/SSE Resilience (`AR-4`)**: Session-staged `after_agent_callback` widget flushing ([bridge/edge/a2ui.py](../bridge/edge/a2ui.py)), byte-identical `ContextCacheConfig` prompt prefixes ([bridge/agent.py](../bridge/agent.py)), and `_StreamDedupConverter` / `asyncio.shield` SSE disconnect cleanup / Cloud SQL `pool_pre_ping` failover retries ([bridge/sql_runtime.py](../bridge/sql_runtime.py), [bridge/edge/agent_engine_a2a.py](../bridge/edge/agent_engine_a2a.py)).

## Shipped

- **Phase 9 (`P9-1`–`P9-3`) & `v1.14.1-m0-empty-core`** — Empty-core `docubridge-examples` extraction, generic `ScenarioEngine`, single OKF doctype source of truth, and wire-only A2A consumer contract — [archive](archive/roadmap-history.md#phase-9-p9-1p9-3)
- **Cycle 1 (`M0`–`M7`), Benefits / Emergent RFP & Phase 8 (`P8-1`–`P8-8`)** — 6-Zone Document Bridge core, 14 dual-mode ports, `Rule 0` `Decimal` consolidation, Agent Gym, OKF Schema Refinery, Ontology MCP & SQL query validator, and Terraform GCP substrate — [archive](archive/roadmap-history.md#cycle-1-phase-8-m0m7-p8-1p8-8)
- **Phase 10 Hygiene & Post-Split Quickstart (P10-1, P10-2):** Scrubbed hardcoded scratchpad GCP IDs/numbers, activated repo hygiene + AST .env.example + link/skill checks, fixed post-split README/AGENTS/design/run-all.sh, and tracked standards baseline debt — [archive](archive/roadmap-history.md#phase-10-hygiene-post-split-quickstart-p10-1-p10-2)
- **Milestone P10 — Auth Hardening (`2B`), Single-Bucket IaC/Adapters, `bq-eval` SQL & Supply-Chain Hygiene (`v1.15.0`):** Fail-closed `K_SERVICE` auth + `Option 2B` cookies cover all mutating routes; standalone/co-located single-bucket Terraform + Python prefix/WORM adapters pass `terraform test` + unit suites locally (no live deploy yet); `sql_eval.py` matches `bq-eval` `v0.2.3`; `uv:0.12.24` + `HEALTHCHECK` + Trivy + `Decimal` approval limits pass; tag `v1.15.0` ships. — [archive](archive/roadmap-history.md#milestone-p10---auth-hardening-2b-single-bucket-iacadapters-bq-eval-sql-supply-chain-hygiene-v1150)
- **Milestone P11 — Declared Collection Strategy (`ADR-14`/`ADR-15`) & Canonical A2A Counterparty Wire (`v1.16.0`):** Added declarative `assets/strategy.yaml` + deterministic `bridge/strategy/` evaluator, `CONTRACT_VERSION = "1.5"` `ExchangeTurn.sufficiency`, recommendation-only waivers, post-split `agents/address/*` doc cleanup, and canonical A2A v1.0 `SendMessage` counterparty dial-out (`fnb`, `carrier-a`, `carrier-b`) with `/ebc` shim badges. — [archive](archive/roadmap-history.md#milestone-p11---declared-collection-strategy-adr-14adr-15-canonical-a2a-counterparty-wire-v1160)
- **Milestone P12 — Tier-2 `ui-design` Governance & Zero Standards Debt (`v1.17.0`):** `.stitch/DESIGN.md`, offline fonts, 4 Vite favicons, MSVG-safe SVG, and executed Playwright CI pass; `tests/standards_debt.json` reaches `0` (`<800` LOC, `mypy --strict`, all Ruff rule families, `0` narration comments). — [archive](archive/roadmap-history.md#milestone-p12---tier-2-ui-design-governance-zero-standards-debt-v1170)

