"""Downstream actor subsystem specs (Use-Case, Reviewers, Learning) for DocuBridge."""

from __future__ import annotations

from specs.human.contract import SubsystemSpec

DOWNSTREAM_NODES: tuple[SubsystemSpec, ...] = (
    SubsystemSpec(
        node_id="uc_address",
        height=96,
        span=1,
        status="built",
        recent=False,
        has_gap=True,
        plain_title="Bounded Address Collect",
        plain_sub="1 ID or 2 distinct utility bills",
        spec_title="Address Consumer (CUJ-1, 4, 10)",
        spec_sub="ADK Workflow · caller owns 'done'",
        adrs=("ADR-03", "ADR-14", "ADR-15"),
        desc_plain=(
            "Onboarding service agent asking DocuBridge to collect 1 gov ID or 2 "
            "utility bills from distinct issuers while keeping final approval policy."
        ),
        desc_spec=(
            "External ADK Workflow (collect -> deterministic is_satisfied -> present) "
            "in docubridge-examples consuming Bridge over A2A + contract/ wire models."
        ),
        ports_contract=(
            "Pydantic v2 contract/ wire firewall (ast-grep + test_isolation_guard "
            "forbid bridge/ <-> caller imports)."
        ),
        gap_note=(
            "Example consumer gate helper needs direct Sense A' SufficiencyVerdict "
            "wiring; ast-grep skips silently if off PATH (P15-2)."
        ),
        code_files=(
            "contract/wire.py",
            "bridge/strategy/loader.py",
            "docs/consume-over-a2a.md",
        ),
        code_symbols=(
            ("contract/wire.py", "CollectRequest"),
            ("contract/wire.py", "SufficiencyVerdict"),
            ("bridge/strategy/loader.py", "load_strategy"),
        ),
        pillar_category="workload",
    ),
    SubsystemSpec(
        node_id="uc_benefits",
        height=96,
        span=1,
        status="built",
        recent=False,
        has_gap=True,
        plain_title="Benefits Fan-Out & Negotiate",
        plain_sub="Multi-carrier FX/lang quotes & bind",
        spec_title="Benefits Consumer (CUJ-3, 11, 12)",
        spec_sub="4-carrier fan-out · revise -> bind",
        adrs=("ADR-01", "ADR-03", "ADR-05", "ADR-06", "ADR-14"),
        desc_plain=(
            "Procurement agent fanning out across 4 carriers in USD/EUR/GBP and "
            "EN/FR/DE, comparing gaps against policy, negotiating revisions, and binding."
        ),
        desc_spec=(
            "Drives program:acme-2026 fan-out over A2A DialOut, live comparison "
            "rollups, turn-capped revise_leg loops, and outbound ArtifactRef delivery."
        ),
        ports_contract=(
            "contract/ wire + canonical A2A v1.0 across ADK and LangGraph carriers."
        ),
        gap_note=(
            "Open ownership fork in spec/open-questions.md: delegate extraction to "
            "Bridge vs caller-side BenefitsQuote extractor."
        ),
        code_files=(
            "bridge/program.py",
            "bridge/negotiate.py",
            "bridge/comparison.py",
            "bridge/normalize.py",
        ),
        code_symbols=(
            ("bridge/program.py", "open_program"),
            ("bridge/negotiate.py", "open_negotiation"),
            ("bridge/comparison.py", "build_comparison"),
            ("bridge/normalize.py", "normalize_quote"),
        ),
        pillar_category="workload",
    ),
    SubsystemSpec(
        node_id="uc_rfp_lending",
        height=98,
        span=1,
        status="built",
        recent=False,
        has_gap=True,
        plain_title="Emergent RFP & Lending",
        plain_sub="Runtime matrix, waivers & credit policy",
        spec_title="RFP & Underwriting Callers",
        spec_sub="CUJ-8,9,13,15 · $45M CRE & $1.85M TRID",
        adrs=("ADR-03", "ADR-06", "ADR-14", "ADR-16"),
        desc_plain=(
            "Procurement and commercial-lending callers mutating supplier matrices "
            "live, issuing explicit waivers, and underwriting from LTV/DSCR views."
        ),
        desc_spec=(
            "Uses open_or_update_rfp CAS matrix + /ebc banking/mortgage/insurance "
            "presets consuming bi-temporal SQL role views with click-to-source proof."
        ),
        ports_contract=(
            "contract/ wire + /rfps read API + Role Views (Broker, Claims, Underwriter); "
            "BRIDGE_EMPTY_CATALOG_ON_START=1 clean core."
        ),
        gap_note=(
            "3 showcase callers retry VaultImmutabilityError unconditionally; "
            "board_host /docs route unauthenticated (S-14)."
        ),
        code_files=(
            "bridge/edge/rfp_open.py",
            "bridge/dashboard/board.py",
            "bridge/rfp.py",
            "bridge/showcase/operations/consolidation.py",
        ),
        code_symbols=(
            ("bridge/edge/rfp_open.py", "open_or_update_rfp"),
            ("bridge/dashboard/board.py", "RfpBoard"),
            ("bridge/showcase/operations/consolidation.py", "consolidation_view"),
        ),
        pillar_category="workload",
    ),
    SubsystemSpec(
        node_id="hr_inbox",
        height=100,
        span=1,
        status="partial",
        recent=True,
        has_gap=True,
        plain_title="Page-Pinned Action Inbox",
        plain_sub="Side-by-side PDF boxes, resume & waiver rec",
        spec_title="Port #12 A2UI Action Inbox",
        spec_sub="4+1 verbs · RFC 6902 · task resume",
        adrs=("ADR-08", "ADR-09", "ADR-14"),
        desc_plain=(
            "Shows flagged extractions next to the exact PDF page polygon so humans "
            "can accept, edit, reject, escalate, or send a waiver rec to resume tasks."
        ),
        desc_spec=(
            "Internal zone-isolated A2UI catalog enforcing external-role 403 & Decimal "
            "role approval ceilings (P14-3), RFC 6902 diffs, and R6 task resume."
        ),
        ports_contract=(
            "Port #12 action_inbox (local SQLite <-> Cloud SQL PG16) + ADK "
            "request_confirmation FunctionResponse resume."
        ),
        gap_note=(
            "ActionInboxStore.resolve_action lacks a terminal-state CAS guard & "
            "single atomic audit transaction (P16-4)."
        ),
        code_files=(
            "bridge/a2ui_inbox/models.py",
            "bridge/a2ui_inbox/memory_store.py",
            "bridge/a2ui_inbox/sql_store.py",
            "bridge/user_interaction/a2ui_routes.py",
        ),
        code_symbols=(
            ("bridge/a2ui_inbox/models.py", "ActionInboxItem"),
            ("bridge/a2ui_inbox/memory_store.py", "ActionInboxStore"),
            ("bridge/a2ui_inbox/sql_store.py", "SqlActionInboxStore"),
        ),
    ),
    SubsystemSpec(
        node_id="hr_rsr_guard",
        height=96,
        span=1,
        status="built",
        recent=True,
        has_gap=False,
        plain_title="Anti-Rubber-Stamp (RSR) Guard",
        plain_sub="Blind pre-commit, citation open & dwell",
        spec_title="Two-Sided Oversight (RSR >= 0.80)",
        spec_sub="pre_committed · dwell_ms · P13-7",
        adrs=("ADR-09", "ADR-10", "ADR-14"),
        desc_plain=(
            "Verifies the human reviewer opened the PDF citation and spent real "
            "dwell time before trusting their edit as ground truth for Day-2 learning."
        ),
        desc_spec=(
            "Measures oversight_rsr_score from blind pre-commitment, citation open, and "
            "review_dwell_ms; P13-7 removed permissive True defaults so low RSR fails."
        ),
        ports_contract=(
            "Oversight telemetry contract between Port #12 (action_inbox) and "
            "Port #13 (agent_gym)."
        ),
        gap_note=None,
        code_files=(
            "bridge/calibration.py",
            "bridge/user_interaction/auth.py",
            "bridge/a2ui_inbox/service.py",
        ),
        code_symbols=(
            ("bridge/calibration.py", "compute_rair_rsr"),
            ("bridge/user_interaction/auth.py", "resolve_caller_role"),
            ("bridge/a2ui_inbox/service.py", "request_a2ui_inbox_confirmation"),
        ),
    ),
    SubsystemSpec(
        node_id="dl_agent_gym",
        height=98,
        span=1,
        status="partial",
        recent=True,
        has_gap=True,
        plain_title="Agent Gym (Hints & Derivations)",
        plain_sub="Verified fixes -> held-out test -> SKILL.md",
        spec_title="Port #13 Agent Gym (Skills Half)",
        spec_sub="TribalRules -> 3-Tier Gate -> SkillRevision",
        adrs=("ADR-04", "ADR-10", "ADR-15"),
        desc_plain=(
            "Distills verified expert edits into prompt extraction hints, aliases, "
            "and Stage-B Python derivations—promoting only if held-out tests pass."
        ),
        desc_spec=(
            "Runs 4-case stress + held-out regression checks (P13-1, auto_promote=False) "
            "+ gemini-4-argon 3-Tier gate; fenced by ADR-15 from touching checklists."
        ),
        ports_contract=(
            "Port #13 agent_gym (local SQLite <-> Cloud SQL PG16 + Vertex "
            "gemini-4-argon judge) -> Port #4 skill_registry."
        ),
        gap_note=(
            "compile_rules_to_skill fallback pulls CANDIDATE/REJECTED rules & defaults "
            "auto_promote=True (P16-2); HTTP onboard-use-case LFI open (P16-1)."
        ),
        code_files=(
            "bridge/agent_gym/engine.py",
            "bridge/agent_gym/compiler.py",
            "bridge/agent_gym/rule_evaluator.py",
            "bridge/agent_gym/gateway_policy.py",
        ),
        code_symbols=(
            ("bridge/agent_gym/engine.py", "AgentGymEngine"),
            ("bridge/agent_gym/compiler.py", "SkillCompilerMixin"),
            ("bridge/agent_gym/rule_evaluator.py", "RuleEvaluatorMixin"),
            ("bridge/agent_gym/gateway_policy.py", "GatewayPolicyMixin"),
        ),
        pillar_category="workload",
    ),
    SubsystemSpec(
        node_id="dl_schema_refinery",
        height=98,
        span=1,
        status="target",
        recent=False,
        has_gap=True,
        plain_title="Schema Refinery (Ontology Splits)",
        plain_sub="Splits overloaded doctypes; 0% flip gate",
        spec_title="Schema Refinery (Ontology Half)",
        spec_sub="SplitEntity J<=0.10 · NFR=0% · alpha/AC1",
        adrs=("ADR-07", "ADR-13", "ADR-15"),
        desc_plain=(
            "Spots when one document schema is overloaded, proposes subtype splits or "
            "sharper field descriptions, and blocks promotion on any regression."
        ),
        desc_spec=(
            "Detects bimodal null clusters; 3-Tier Counterfactual Assay defaults "
            "passed=False offline ('not evaluated') and writes immutable OntologyRevision."
        ),
        ports_contract=(
            "OKF v0.2 catalog/doctypes/*.okf.md -> Port #14 knowledge_catalog "
            "(SQLite/PG16 + BigQuery ontology tables)."
        ),
        gap_note=(
            "No live noisy-PDF perturbation run yet; assay.py sits near LOC cap (986 LOC)."
        ),
        code_files=(
            "bridge/refinery/engine.py",
            "bridge/refinery/assay.py",
            "bridge/refinery/detectors.py",
            "bridge/knowledge_catalog/query_validator.py",
        ),
        code_symbols=(
            ("bridge/refinery/engine.py", "SchemaRefineryEngine"),
            ("bridge/refinery/assay.py", "evaluate_ontology_proposal"),
            ("bridge/knowledge_catalog/query_validator.py", "validate_metric_query"),
        ),
    ),
    SubsystemSpec(
        node_id="dl_trace_eval",
        height=98,
        span=1,
        status="target",
        recent=False,
        has_gap=True,
        plain_title="SQL Trace Evaluation",
        plain_sub="100% free SQL rules -> 5% judge -> bias fix",
        spec_title="22-Step OTel & BQ Rule 0 Eval",
        spec_sub="otel_spans -> 5 SQL gates -> Rogan-Gladen",
        adrs=("ADR-12",),
        desc_plain=(
            "Checks 100% of 22-step execution traces with 5 free BigQuery SQL rules, "
            "samples ~5% for an LLM judge, and mathematically corrects judge bias."
        ),
        desc_spec=(
            "Streams W3C gen_ai.* spans to Cloud Trace v2 + BigQuery otel_spans; 5 Rule 0 "
            "SQL gates filter ~95% before AI.GENERATE_TABLE + Rogan-Gladen clip."
        ),
        ports_contract=(
            "W3C OTel exporter -> Cloud Trace v2 + BigQuery otel_spans + "
            "Terraform telemetry module."
        ),
        gap_note=(
            "59 lines across 16 files still cite external bq-eval view names; "
            "gate.sh skips --cov-fail-under=90 & terraform test (P15-1)."
        ),
        code_files=(
            "bridge/telemetry/exporters.py",
            "bridge/telemetry/outcome.py",
            "bridge/telemetry/sql_eval.py",
            "bridge/telemetry/sql_templates.py",
        ),
        code_symbols=(
            ("bridge/telemetry/exporters.py", "CloudTraceV2SpanExporter"),
            ("bridge/telemetry/outcome.py", "OutcomeBaggageSpanProcessor"),
            ("bridge/telemetry/sql_eval.py", "compute_sql_trace_evaluation"),
            ("bridge/telemetry/sql_templates.py", "build_sql_trace_queries"),
        ),
        pillar_category="eval",
    ),
)
