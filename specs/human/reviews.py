"""Probes, audit findings, open checks, and Atlas metadata for DocuBridge."""

from __future__ import annotations

from specs.human.contract import (
    AtlasSummarySpec,
    FindingSpec,
    OpenCheckSpec,
    ProbeSpec,
)

PROBES: tuple[ProbeSpec, ...] = (
    ProbeSpec(
        status="pass",
        title="Offline Unit, Contract, AST & Terraform Gates (P13/P14 + 850f034)",
        detail=(
            "Verified green locally on main: gate test docubridge passes 2,017 tests "
            "(57 skipped offline), 90.72% coverage, ruff + mypy --strict + ast-grep "
            "clean, and 57 terraform test assertions credential-free."
        ),
        target_paths=("scripts/gate.sh", "specs/ast.rules.yaml"),
    ),
    ProbeSpec(
        status="pass",
        title="P13 Behavioral Hardening Executed on Real Bugs",
        detail=(
            "Reproduced & closed: Agent Gym held-out/auto_promote=False checks "
            "(P13-1), closed fullmatch parse_decimal_money grammar (P13-2), "
            "effective_from-first consolidation + table replacement (P13-3/ADR-16), "
            "and PAID IN FULL exact status guard (P13-9)."
        ),
        target_paths=(
            "bridge/extraction/derivations.py",
            "bridge/operational_store/consolidation_rules.py",
        ),
    ),
    ProbeSpec(
        status="pass",
        title="P14 Deployed Auth & Supply Chain Hardening Executed",
        detail=(
            "163 API routes walked against public allowlist (P14-9), EdgeAuthBackend "
            "+ principal-scoped tasks/artifacts (P14-1,4), viewer cookie isolated "
            "(P14-2), Secret Manager + IAP ES256 audience + gs:// allowlist (P14-6..8)."
        ),
        target_paths=("bridge/edge/auth.py", "bridge/user_interaction/demo_session.py"),
    ),
    ProbeSpec(
        status="pass",
        title="Honest Perturbation Calibration Discipline (1412b76)",
        detail=(
            "Removed synthetic constant alpha/AC2 matrices & guards in "
            "test_calibration_honesty.py; offline RefineryAssayReport defaults "
            "passed=False and prints 'not evaluated (no live extractor)'."
        ),
        target_paths=("bridge/refinery/assay.py", "tests/test_calibration_honesty.py"),
    ),
    ProbeSpec(
        status="open",
        title="Synthetic Only: N = 0 Live Noisy-PDF Benchmark Runs (R3)",
        detail=(
            "All 1,936-2,017 tests replay 15 synthetic corpus docs + 5 seeded "
            "scenarios; zero open-loop degraded-scan PDF benchmarks (N >= 50-100) "
            "have run yet."
        ),
        target_paths=("docs/roadmap.md",),
    ),
    ProbeSpec(
        status="open",
        title="33 GCP-Gated Port Tests + 15 Live LLM + 4 Consumer Checkout Skipped",
        detail=(
            "55-57 tests skip locally without live GCP credentials, Gemini/DocAI "
            "keys, or sibling consumer checkout (only mocked local adapters ran "
            "post-P14; live Showcase cutover is paused)."
        ),
        target_paths=("tests/test_gcp_ports.py",),
    ),
    ProbeSpec(
        status="open",
        title="Runtime State-Precondition Audit Gaps (2026-11-25)",
        detail=(
            "document_bridge pins gate args only on run_disposition_gate (leaving "
            "reject/escalate/delete unpinned); /v1/intake/ingest vaults "
            "status='accepted' without signals_from_result."
        ),
        target_paths=("bridge/gatepin.py", "bridge/user_interaction/intake_routes.py"),
    ),
)

FINDINGS: tuple[FindingSpec, ...] = (
    FindingSpec(
        finding_id="P13 (2026-11-21 -> 834cb12)",
        severity="closed",
        title="Fixed 15 correctness defects lifting daily review baseline (5.0/10 -> fixed)",
        body=(
            "Closed P13-1..P13-15: Agent Gym empty rule evaluation, currency "
            "multiplier bug (1,200 MXN -> 1.2B), table index overwrite & ADR-16 "
            "precedence inversion, dropped lineage IDs, synthetic Vault bytes, "
            "default citation_verified=True & paid-in-full negation."
        ),
        target_node="db_override_core",
        target_journey="J6",
    ),
    FindingSpec(
        finding_id="P14 + 850f034 (Fixed)",
        severity="closed",
        title="Closed 12 auth, Secret Manager, IAP ES256 & OIDC caller allowlist findings",
        body=(
            "Wired EdgeAuthBackend in serve_container, isolated showcase viewer "
            "cookie, enforced external-role 403 & Decimal role limits in Inbox, "
            "removed unguarded dev SVID secret under auth, added ES256 IAP verifier, "
            "gs:// allowlist, OIDC allowlists & Agent Engine parity."
        ),
        target_node="db_front_door",
        target_journey="J2",
    ),
    FindingSpec(
        finding_id="P16-1..4,10 (Fixed on main)",
        severity="closed",
        title="Closed P0 onboard-use-case LFI, compiler bypass, SQL CAS & 800-LOC splits",
        body=(
            "Split HTTP UseCaseOnboardingRequest + pre-promotion OKF assay & failed-"
            "assay install block (P16-1); restricted compile_rules_to_skill to "
            "PROMOTED/VERIFIED doctype rules & DRAFT default (P16-2); added single-tx "
            "SQL CAS in SqlKnowledgeCatalog & ActionInboxStore (P16-3/4) + cap splits."
        ),
        target_node="dl_agent_gym",
        target_journey="J8",
    ),
    FindingSpec(
        finding_id="P16-5..9 (Open)",
        severity="high",
        title="Open P1/P2: Path-A/email as_of freshness, NAT64 SSRF, PII logs & Model Armor",
        body=(
            "Path-A, email intake & pathb omit as_of freshness (P16-5); outbound "
            "NAT64 IPv6 SSRF & dispatch re-validation (P16-6); Path-A trace payload "
            "PII logs (P16-7); empty mcp_tool_name & fail-open Model Armor (P16-8)."
        ),
        target_node="db_vault_ledger",
        target_journey="J5",
    ),
    FindingSpec(
        finding_id="P15-1..8 (Open)",
        severity="med",
        title="Open P1/P2: scripts/gate.sh coverage drift, ast-grep skip & upward imports",
        body=(
            "gate.sh skips --cov-fail-under=90 (90.72%), terraform test & frontend "
            "build (P15-1); ast-grep skips silently off PATH (P15-2); AST rules "
            "miss execute(text(f'...')) (P15-7); 31 get_adapter() leaks (P15-8)."
        ),
        target_node="db_vault_ledger",
        target_journey="J9",
    ),
    FindingSpec(
        finding_id="Backlog (2026-11-24/25)",
        severity="med",
        title="Open: 59 lines naming external bq-eval views, UI split & advisory paths",
        body=(
            "bq-eval view names appear in 16 files (2026-11-24 decision to remove); "
            "duplicate demo/ vs frontend/ UIs (R2-A); Day-2 GeminiExtractor in GCP "
            "skips _agent_gym hints (R5)."
        ),
        target_node="dl_trace_eval",
        target_journey="J9",
    ),
)

OPEN_CHECKS: tuple[OpenCheckSpec, ...] = (
    OpenCheckSpec(
        check_id=1,
        status="Closed (main)",
        check="P16-1: HTTP LFI (sample_files), failed-assay install & post-assay override",
        scope="J8",
        gap="Fixed on main (pending v1.20.0 tag): HTTP 422 + pre-promotion OKF assay.",
        roadmap_ref="P16-1",
    ),
    OpenCheckSpec(
        check_id=2,
        status="Closed (main)",
        check="P16-2: Agent Gym compile_rules_to_skill pulls CANDIDATE/REJECTED rules",
        scope="J8",
        gap="Fixed on main (pending v1.20.0 tag): doctype VERIFIED/PROMOTED + DRAFT.",
        roadmap_ref="P16-2",
    ),
    OpenCheckSpec(
        check_id=3,
        status="Closed (main)",
        check="P16-3 & P16-4: SqlKnowledgeCatalog wipe + ActionInbox double-resolve",
        scope="J2, J8",
        gap="Fixed on main (pending v1.20.0 tag): single-txn SQL CAS + terminal 409.",
        roadmap_ref="P16-3",
    ),
    OpenCheckSpec(
        check_id=4,
        status="Open (P1)",
        check="P16-5: Path-A, email intake & pathb omit as_of freshness clock",
        scope="J1, J5, J7",
        gap="Expired utility bills auto-approve on Path A/email until wired.",
        roadmap_ref="P16-5",
    ),
    OpenCheckSpec(
        check_id=5,
        status="Open (P1)",
        check="P16-6 & P16-8: Outbound SSRF/NAT64 bypass + gateway CEL/Model Armor",
        scope="J2, J3",
        gap="Re-validate webhooks at dispatch, deny empty mcp_tool_name, fail closed.",
        roadmap_ref="P16-6",
    ),
    OpenCheckSpec(
        check_id=6,
        status="Open (P1)",
        check="P15-1 & P15-2: gate.sh coverage parity & non-skipping ast-grep control",
        scope="J9",
        gap="Local gate skips --cov-fail-under=90, terraform test & frontend build.",
        roadmap_ref="P15-1",
    ),
    OpenCheckSpec(
        check_id=7,
        status="Open (P1/P2)",
        check="P16-7 & P16-9: Path-A PII trace payload logs & _task_outcomes LRU",
        scope="J1, J2, J9",
        gap="P16-10 cap splits landed; Path-A trace log scrub & outcome LRU remain.",
        roadmap_ref="P16-7",
    ),
    OpenCheckSpec(
        check_id=8,
        status="Backlog",
        check="Decouple 59 lines naming bq-eval views + R3 live noisy-PDF N>=50..100",
        scope="J6, J9",
        gap="Replace hard-coded view names & measure open-loop noisy PDFs.",
        roadmap_ref="R3",
    ),
)

ATLAS_SUMMARY = AtlasSummarySpec(
    repo_id="docubridge",
    name="A2A Document Bridge (docubridge)",
    short_name="DocuBridge",
    segment="Gen-3 Enterprise Data Intake · A2A · Agent Gym",
    color="#34a853",
    helicopter_bluf=(
        "Graduated Gen-3 reference platform for 6-Zone Enterprise Document Intake, "
        "Reconciliation, Knowledge Catalog governance, and Agent Gym Day-2 continual "
        "learning. Strictly enforces the Hallucination Firewall (contract/ <-> bridge/ "
        "<-> agents/) and 14 dual-mode (local <-> gcp) managed-service ports."
    ),
    headline_metrics=(
        {
            "label": "Architecture",
            "value": "6-Zone + A2A",
            "sub": "Path A (JSON) + Path B (DocAI)",
        },
        {
            "label": "Managed Ports",
            "value": "14 Dual-Mode",
            "sub": "local <-> gcp (@pytest.mark.port)",
        },
        {
            "label": "Frontend Apps",
            "value": "4 React/Vite",
            "sub": "+ 5-Scene Showcase Console",
        },
        {
            "label": "IaC & CI/CD",
            "value": "10 TF Modules",
            "sub": "14 .tftest.hcl + cloudbuild.yaml",
        },
    ),
    guardrails=(
        (
            "Hallucination Firewall (tests/test_isolation_guard.py): bridge/ never "
            "imports agents/, and agents/ never imports bridge/; both import contract/."
        ),
        (
            "Skill-Name Literal Guard (test_core_names_no_skill): never hardcode an "
            "installed skills/ folder name inside bridge/ core modules."
        ),
        (
            "Protected Spec (CRITICAL.md): never modify spec/ during implementation "
            "tasks; propose changes in DECISIONS.md."
        ),
        (
            "Verification Gate (T1): bash scripts/gate.sh (uv lock, ast-grep, ruff, "
            "mypy --strict contract bridge specs, pytest)."
        ),
    ),
    notes_files=(
        ("AGENTS.md", "AGENTS.md"),
        ("CRITICAL.md", "CRITICAL.md"),
        ("DECISIONS.md", "DECISIONS.md"),
        ("Architecture Overview", "docs/architecture-overview.md"),
        ("Features Catalog", "docs/features.md"),
        ("docs/roadmap.md", "docs/roadmap.md"),
        ("README.md", "README.md"),
    ),
    pillars=(
        {
            "id": "db-arch",
            "title": "1. Hallucination Firewall & Package Split",
            "category": "arch",
            "badge": "contract/ · bridge/ · agents/",
            "summary": (
                "AST-enforced isolation between Pydantic wire schemas (contract/), "
                "deterministic core (bridge/), and ADK consumer agents (agents/)."
            ),
            "note_path": "projects/docubridge/AGENTS.md",
            "children": [
                {
                    "id": "db-arch-contract",
                    "title": "contract/ — Shared Pydantic v2 Wire Types",
                    "category": "arch",
                    "badge": "extra='forbid'",
                    "summary": (
                        "Canonical wire schemas, Disposition enums, and Gate "
                        "transitions shared across bridge/ and agents/."
                    ),
                    "note_path": "projects/docubridge/AGENTS.md",
                },
                {
                    "id": "db-arch-bridge",
                    "title": "bridge/ — Deterministic 6-Zone Core & 14 Managed Ports",
                    "category": "arch",
                    "badge": "14 Ports (local <-> gcp)",
                    "summary": (
                        "Dual-Path Intake (Path A zero-AI structured vs Path B "
                        "Gemini/DocAI + 4-Signal Gate), Audit Vault & Agent Gym."
                    ),
                    "note_path": "projects/docubridge/DECISIONS.md",
                    "children": [
                        {
                            "id": "db-arch-ports",
                            "title": "14 Managed-Service Ports (bridge/ports/)",
                            "category": "gcp",
                            "badge": "resolve_port_mode()",
                            "summary": (
                                "sessions, task_store, exchange_store, skill_registry, "
                                "memory_bank, scheduler, a2a_client, extraction, "
                                "email_ingress, audit_vault, operational_store, "
                                "action_inbox, agent_gym, knowledge_catalog."
                            ),
                        },
                        {
                            "id": "db-arch-gym",
                            "title": "Agent Gym Day-2 Continual Learning",
                            "category": "workload",
                            "badge": "EXTRACTION_HINT",
                            "summary": (
                                "Distills verified reviewer edits into extraction hints "
                                "and Stage-B derivations behind held-out regression gates."
                            ),
                        },
                    ],
                },
            ],
        },
        {
            "id": "db-domains",
            "title": "2. Pluggable Domain Packs & Consumer Agents",
            "category": "workload",
            "badge": "Banking · Insurance · KYC",
            "summary": (
                "Zero-code-change domain specialization via runtime skills/ packs "
                "and A2A consumer agents."
            ),
            "note_path": "projects/docubridge/docs/features.md",
            "children": [
                {
                    "id": "db-dom-benefits",
                    "title": "Commercial Insurance & Benefits RFP Pack",
                    "category": "workload",
                    "badge": "RFP · Quote · Procurement",
                    "summary": (
                        "Census normalization, carrier quote comparison, and RFP "
                        "board orchestration."
                    ),
                    "note_path": "projects/docubridge/AGENTS.md",
                },
                {
                    "id": "db-dom-banking",
                    "title": "Banking KYC, Address Proof & Commercial Lending",
                    "category": "workload",
                    "badge": "gov-id · utility-bill · address-proof",
                    "summary": (
                        "Multi-document identity verification, utility bill freshness "
                        "checks, and commercial loan package intake."
                    ),
                    "note_path": "projects/docubridge/docs/features.md",
                },
            ],
        },
        {
            "id": "db-infra",
            "title": "3. GCP Deployment, Terraform & Showcase Console",
            "category": "gcp",
            "badge": "docubridge-showcase · Cloud Run",
            "summary": (
                "10 substrate Terraform modules, Cloud Build CI/CD, Vertex AI Agent "
                "Engine A2A wrapper, and 5-scene Enterprise Showcase Console."
            ),
            "note_path": "projects/docubridge/README.md",
            "children": [
                {
                    "id": "db-infra-tf",
                    "title": "terraform/ — 10 Substrate Modules & 56 .tftest.hcl Checks",
                    "category": "gcp",
                    "badge": "min_instances = 1",
                    "summary": (
                        "network, iam, database, secrets, gcs, tasks, telemetry, "
                        "runtime, gateway, frontend."
                    ),
                    "note_path": "projects/docubridge/AGENTS.md",
                },
                {
                    "id": "db-infra-demo",
                    "title": "demo/ & frontend/ — 5-Scene Enterprise Showcase & 4 Vite Apps",
                    "category": "workload",
                    "badge": "/demo + /ebc",
                    "summary": (
                        "Interactive 5-scene walkthrough + automated headless Chrome "
                        "GCP video recorder."
                    ),
                    "note_path": "projects/docubridge/README.md",
                },
            ],
        },
    ),
    atlas_edges=(
        {"from": "db-arch-contract", "to": "db-arch-bridge", "label": "Wire Schemas"},
        {
            "from": "db-arch-bridge",
            "to": "db-dom-benefits",
            "label": "Loads skills/ Dynamically",
        },
        {
            "from": "db-arch-bridge",
            "to": "db-dom-banking",
            "label": "Loads skills/ Dynamically",
        },
        {
            "from": "db-arch-gym",
            "to": "db-arch-bridge",
            "label": "Injects EXTRACTION_HINTs",
        },
        {
            "from": "db-arch-bridge",
            "to": "db-infra-tf",
            "label": "Deploys to Cloud Run & Agent Engine",
        },
    ),
    cockpit_group="doc_servicing",
    cockpit_sub_group="Core Platform",
    cockpit_role="6-Zone Bridge + Hallucination Firewall",
    cockpit_grade="5.0-6.2/10",
    cockpit_path="/docubridge/",
    cockpit_keywords="docubridge a2a worm sql okf agent gym schema refinery",
)
