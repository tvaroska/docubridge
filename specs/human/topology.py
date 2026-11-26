"""C4 actor zones, macro flows, and AST-pinned subsystems for DocuBridge."""

from __future__ import annotations

from specs.human.contract import (
    ActorZoneSpec,
    MacroEdgeSpec,
    NodeEdgeSpec,
    SubsystemSpec,
)
from specs.human.nodes_downstream import DOWNSTREAM_NODES

ZONES: tuple[ActorZoneSpec, ...] = (
    ActorZoneSpec(
        zone_id="z_external",
        row=0,
        x_pos=20,
        width=268,
        inner_cols=1,
        plain_title="1. External Parties",
        plain_sub="Carriers, email replies & upload portal",
        spec_title="1. External Counterparties & Transports",
        spec_sub="ADK/LangGraph A2A · MIME Email · A2UI Portal",
        summary=(
            "Carriers, lenders, suppliers, and customers answering chases via their "
            "own A2A agents, tracked email attachments, or the web upload portal."
        ),
        node_ids=("ext_counterparty", "ext_email", "ext_portal"),
    ),
    ActorZoneSpec(
        zone_id="z_docubridge",
        row=0,
        x_pos=388,
        width=484,
        inner_cols=2,
        plain_title="2. DocuBridge Agent",
        plain_sub="Mediation core: chases, gates & reconciles in code",
        spec_title="2. DocuBridge Mediation Service (bridge/)",
        spec_sub="Hybrid A2A · Rule 0 Gate · Decimal Core · WORM & SQL",
        summary=(
            "Absorbs the messy multi-party document loop: solicits and chases files, "
            "enforces deterministic 4-signal checks, resolves version conflicts in "
            "Decimal math, and stores the audit trail without owning caller policy."
        ),
        node_ids=(
            "db_front_door",
            "db_intake_gate",
            "db_override_core",
            "db_vault_ledger",
            "db_proactive_chase",
        ),
    ),
    ActorZoneSpec(
        zone_id="z_usecase",
        row=0,
        x_pos=972,
        width=268,
        inner_cols=1,
        plain_title="3. Use-Case Agents",
        plain_sub="Address, Benefits, RFP & Lending — own policy",
        spec_title="3. Service-Agent Consumers (docubridge-examples)",
        spec_sub="RemoteA2aAgent · contract/ wire · Sense B owner",
        summary=(
            "Internal domain agents that ask DocuBridge once over the contract/ "
            "boundary, own business waivers and 'done' (Sense B), and receive "
            "normalized facts back."
        ),
        node_ids=("uc_address", "uc_benefits", "uc_rfp_lending"),
    ),
    ActorZoneSpec(
        zone_id="z_reviewers",
        row=1,
        x_pos=220,
        width=350,
        inner_cols=1,
        plain_title="4. Human Reviewers",
        plain_sub="Page-pinned review, task resume & waiver recs",
        spec_title="4. Human Oversight & Action Inbox (Zone 5)",
        spec_sub="A2UI ActionInbox · RFC 6902 · RSR >= 0.80",
        summary=(
            "Domain experts resolve ambiguous or flagged citations side-by-side with "
            "PDF bounding boxes, resuming parked exchanges and sending waiver "
            "recommendations while anti-rubber-stamp checks score review rigor."
        ),
        node_ids=("hr_inbox", "hr_rsr_guard"),
    ),
    ActorZoneSpec(
        zone_id="z_learning",
        row=1,
        x_pos=690,
        width=350,
        inner_cols=1,
        plain_title="5. Day-2 Learning Loop",
        plain_sub="Improves extraction & schemas — never policy",
        spec_title="5. Calibration & Trace Evaluation (Zone 6)",
        spec_sub="Agent Gym · Schema Refinery · 3-Layer SQL Eval",
        summary=(
            "Distills verified human corrections into extraction hints, Stage-B "
            "derivations, and subtype schema splits behind fail-closed regression "
            "gates, plus 100% SQL trace screening."
        ),
        node_ids=("dl_agent_gym", "dl_schema_refinery", "dl_trace_eval"),
    ),
)

MACRO_EDGES: tuple[MacroEdgeSpec, ...] = (
    MacroEdgeSpec(
        edge_id="m_db_ext",
        source_zone="z_docubridge",
        target_zone="z_external",
        slot="h_top",
        label_plain="chase & solicit",
        label_spec="DialOut / Nudge",
    ),
    MacroEdgeSpec(
        edge_id="m_ext_db",
        source_zone="z_external",
        target_zone="z_docubridge",
        slot="h_bot",
        label_plain="PDFs, JSON & mail",
        label_spec="Path A / Path B / MIME",
    ),
    MacroEdgeSpec(
        edge_id="m_uc_db",
        source_zone="z_usecase",
        target_zone="z_docubridge",
        slot="h_top",
        label_plain="request & policy",
        label_spec="CollectRequest + waivers",
    ),
    MacroEdgeSpec(
        edge_id="m_db_uc",
        source_zone="z_docubridge",
        target_zone="z_usecase",
        slot="h_bot",
        label_plain="clean facts",
        label_spec="Sufficiency + Decimal facts",
    ),
    MacroEdgeSpec(
        edge_id="m_db_hr",
        source_zone="z_docubridge",
        target_zone="z_reviewers",
        slot="v_left_down",
        label_plain="park + page box",
        label_spec="INPUT_REQUIRED + SourceAnchor",
    ),
    MacroEdgeSpec(
        edge_id="m_hr_db",
        source_zone="z_reviewers",
        target_zone="z_docubridge",
        slot="v_left_up",
        label_plain="resume & waiver rec",
        label_spec="FunctionResponse + RFC 6902",
    ),
    MacroEdgeSpec(
        edge_id="m_hr_dl",
        source_zone="z_reviewers",
        target_zone="z_learning",
        slot="h_mid",
        label_plain="verified fixes",
        label_spec="RSR >= 0.80 overrides",
    ),
    MacroEdgeSpec(
        edge_id="m_dl_db",
        source_zone="z_learning",
        target_zone="z_docubridge",
        slot="v_right_up",
        label_plain="hints & schemas (never policy)",
        label_spec="SkillRevision + OntologyRevision",
    ),
)

EDGES: tuple[NodeEdgeSpec, ...] = ()

CORE_NODES: tuple[SubsystemSpec, ...] = (
    SubsystemSpec(
        node_id="ext_counterparty",
        height=96,
        span=1,
        status="built",
        recent=False,
        has_gap=False,
        plain_title="Counterparty Agents",
        plain_sub="ADK & LangGraph carrier/lender peers",
        spec_title="External A2A Peers",
        spec_sub="ADK + LangGraph canonical A2A (CUJ-12)",
        adrs=("ADR-01", "ADR-03", "ADR-08"),
        desc_plain=(
            "Third-party insurance carriers, lenders, or suppliers running their own "
            "ADK or LangGraph agents that negotiate and submit quotes over A2A."
        ),
        desc_spec=(
            "Multi-framework A2A v1.0 peers reached via Port #7 (a2a_client) with "
            "per-leg context isolation so competing carriers never see peer bids."
        ),
        ports_contract=(
            "Canonical A2A v1.0 wire; Port #7 a2a_client "
            "(local in-proc <-> GCP authenticated HTTP)."
        ),
        gap_note=None,
        code_files=("contract/wire.py", "bridge/ports/protocols.py"),
        code_symbols=(
            ("contract/wire.py", "ExchangeTurn"),
            ("bridge/ports/protocols.py", "A2AClientPort"),
        ),
    ),
    SubsystemSpec(
        node_id="ext_email",
        height=96,
        span=1,
        status="partial",
        recent=False,
        has_gap=True,
        plain_title="Tracked Email Replies",
        plain_sub="HMAC-addressed supplier & carrier mail",
        spec_title="Email Transport (bridge/email_ingress/)",
        spec_sub="Port #9 · M365 Graph / MIME · OIDC drain",
        adrs=("ADR-01", "ADR-08"),
        desc_plain=(
            "Lets suppliers without an API reply by attaching PDFs to a unique per-leg "
            "email address; unsigned or mismatched emails quarantine safely."
        ),
        desc_spec=(
            "Lands MIME messages create-only (if_generation_match=0), correlates HMAC "
            "reply token to (rfp_id, leg), and drains idempotently via /email/drain."
        ),
        ports_contract=(
            "Port #9 email_ingress (local drop-folder <-> M365 Graph webhook + "
            "GCS quarantine + Cloud Scheduler)."
        ),
        gap_note=(
            "P16-5: EmailProcessor omits within_days as_of freshness check; "
            "email_ingress.py sits at 792/800 LOC (P16-10)."
        ),
        code_files=(
            "bridge/email_ingress/mime.py",
            "bridge/email_ingress/correlation.py",
            "bridge/edge/email_ingress.py",
        ),
        code_symbols=(
            ("bridge/email_ingress/mime.py", "parse_mime"),
            ("bridge/email_ingress/correlation.py", "correlate"),
            ("bridge/edge/email_ingress.py", "EmailProcessor"),
        ),
    ),
    SubsystemSpec(
        node_id="ext_portal",
        height=98,
        span=1,
        status="partial",
        recent=False,
        has_gap=True,
        plain_title="Human Upload Portal",
        plain_sub="Declarative A2UI forms, /demo & /ebc",
        spec_title="Human Edge & Showcases",
        spec_sub="A2UI v0.9 Portal · FileWithUri · SSE",
        adrs=("ADR-09", "ADR-12"),
        desc_plain=(
            "Browser front door for applicants or brokers without an agent to upload "
            "scans, see instant legibility feedback, and supply missing items."
        ),
        desc_spec=(
            "Emits external A2UI v0.9 surfaces (provider-portal, /demo, /ebc); files "
            "travel strictly as leg-scoped FileWithUri references rather than bytes."
        ),
        ports_contract=(
            "A2UI v0.9 declarative JSON + SSE stream; viewer cookie isolated to "
            "read-only showcase routes (P14-2)."
        ),
        gap_note=(
            "Duplicate UI surfaces exist (demo/ vs frontend/*, R2-A); deploy.sh Vite "
            "service URL alignment remains open (S-12)."
        ),
        code_files=(
            "bridge/edge/a2ui.py",
            "bridge/portal/server.py",
            "bridge/user_interaction/intake_routes.py",
        ),
        code_symbols=(
            ("bridge/edge/a2ui.py", "render_upload_prompt"),
            ("bridge/portal/server.py", "build_portal_app"),
            (
                "bridge/user_interaction/intake_routes.py",
                "register_intake_and_demo_routes",
            ),
        ),
    ),
    SubsystemSpec(
        node_id="db_front_door",
        height=106,
        span=1,
        status="partial",
        recent=True,
        has_gap=True,
        plain_title="Front Door & Auth",
        plain_sub="$0 structured route + SPIFFE and User",
        spec_title="Hybrid A2A Edge & Identity",
        spec_sub="R1..R6 Collector/LLM · JWT-SVID · IAP ES256",
        adrs=("ADR-02", "ADR-03", "ADR-08", "ADR-10", "ADR-11", "ADR-14"),
        desc_plain=(
            "Routes structured requests at zero LLM cost, uses the conversational agent "
            "only for free-text or human pauses, and verifies both agent and user."
        ),
        desc_spec=(
            "CollectExecutor routes R1/R2 (0 LLM calls) vs R3/R4/R6 resumable LlmAgent; "
            "EdgeAuthBackend intersects Agent(SPIFFE) & User(IAP ES256) + gs:// allowlist."
        ),
        ports_contract=(
            "Ports #1 sessions, #2 task_store, #3 exchange_store (SQLite <-> Cloud SQL "
            "PG16 / Agent Engine); AST bans unguarded InMemory* on cloud paths."
        ),
        gap_note=(
            "executor.py at 800/800 LOC (P16-10); _task_outcomes leak on failed/HITL "
            "turns (P16-9); NAT64 IPv6 SSRF & live Model Armor fail-open (P16-6/8)."
        ),
        code_files=(
            "bridge/edge/server.py",
            "bridge/edge/executor.py",
            "bridge/edge/pathroute.py",
            "bridge/edge/auth.py",
            "bridge/identity.py",
        ),
        code_symbols=(
            ("bridge/edge/executor.py", "CollectExecutor"),
            ("bridge/edge/pathroute.py", "classify_route"),
            ("bridge/edge/auth.py", "EdgeAuthBackend"),
            ("bridge/identity.py", "verify_jwt_svid"),
        ),
    ),
    SubsystemSpec(
        node_id="db_intake_gate",
        height=106,
        span=1,
        status="partial",
        recent=True,
        has_gap=True,
        plain_title="Intake & 4-Signal Gate",
        plain_sub="$0 JSON, PDF bbox reader & code verdict",
        spec_title="Path A/B Extraction & pin_gate_args",
        spec_sub="Port #8 (Gemini/DocAI) · 4-Signal Rule 0",
        adrs=("ADR-01", "ADR-02", "ADR-04", "ADR-05", "ADR-13", "ADR-15"),
        desc_plain=(
            "Checks structured partner JSON at $0 or reads PDFs into page-pinned "
            "fields, then runs a code-only 4-signal gate models cannot fake."
        ),
        desc_spec=(
            "Path-A JSON validator (0 model calls) + Port #8 PDF extraction + Stage-B "
            "derivations; before_tool_callback overwrites LLM args from signals."
        ),
        ports_contract=(
            "Port #8 extraction (local fixture <-> Vertex Gemini + Document AI OCR); "
            "skills/*/assets/validation.yaml."
        ),
        gap_note=(
            "Path-A omits as_of freshness date (P16-5); GCP GeminiExtractor drops "
            "_agent_gym hints (R5); N=0 live degraded-scan PDF benchmark runs (R3)."
        ),
        code_files=(
            "bridge/gatepin.py",
            "bridge/signals.py",
            "bridge/patha.py",
            "bridge/extraction/docai.py",
            "bridge/extraction/derivations.py",
        ),
        code_symbols=(
            ("bridge/gatepin.py", "pin_gate_args"),
            ("bridge/signals.py", "signals_from_result"),
            ("bridge/patha.py", "validate_assertion"),
            ("bridge/extraction/derivations.py", "apply_result_derivations"),
        ),
    ),
    SubsystemSpec(
        node_id="db_override_core",
        height=106,
        span=1,
        status="built",
        recent=True,
        has_gap=True,
        plain_title="Decimal Override & Sufficiency",
        plain_sub="Effective-date math & Sense A' check",
        spec_title="Consolidation & Strategy (ADR-14/16)",
        spec_sub="effective_from DESC · strategy.yaml",
        adrs=("ADR-05", "ADR-06", "ADR-13", "ADR-14", "ADR-15", "ADR-16"),
        desc_plain=(
            "Resolves conflicting figures across contracts and late addenda in exact "
            "Decimal math (newest effective date wins) and reports static sufficiency."
        ),
        desc_spec=(
            "ADR-16 orders (effective_from, source_priority, recorded_at, version) DESC "
            "with whole-table replacement & fullmatch money parser; returns SufficiencyVerdict."
        ),
        ports_contract=(
            "Pure deterministic Python Decimal engine + declarative "
            "skills/<process>/assets/strategy.yaml (Sense A' evidence)."
        ),
        gap_note=(
            "Caller override accepts 'waived' without signed waiver receipt; "
            "_FALLBACK_METRIC_BUCKETS cross-domain fallback (A-15)."
        ),
        code_files=(
            "bridge/operational_store/consolidation_rules.py",
            "bridge/strategy/loader.py",
            "bridge/disposition.py",
        ),
        code_symbols=(
            ("bridge/operational_store/consolidation_rules.py", "consolidate_entry"),
            ("bridge/operational_store/consolidation_rules.py", "parse_decimal_money"),
            ("bridge/strategy/loader.py", "load_strategy"),
            ("bridge/disposition.py", "threshold_corridor"),
        ),
    ),
    SubsystemSpec(
        node_id="db_vault_ledger",
        height=106,
        span=1,
        status="partial",
        recent=True,
        has_gap=True,
        plain_title="Vault, SQL Ledger & OKF",
        plain_sub="7-yr WORM bytes, bi-temporal SQL & graph",
        spec_title="Zones 2-4 & Port #14 Catalog",
        spec_sub="WORM GCS · DsnSqlRuntime · OKF PROV-O",
        adrs=(
            "ADR-01",
            "ADR-04",
            "ADR-06",
            "ADR-07",
            "ADR-11",
            "ADR-13",
            "ADR-15",
            "ADR-16",
        ),
        desc_plain=(
            "Scrubs PII, locks accepted files in 7-year write-once storage, records "
            "append-only bi-temporal SQL history, and serves canonical OKF ontology."
        ),
        desc_spec=(
            "Ports #10 (WORM if_generation_match=0), #11 (SqlOperationalStore over "
            "DsnSqlRuntime + BigQuery Iceberg CDC), #4, #5 & #14 (SqlKnowledgeCatalog)."
        ),
        ports_contract=(
            "Ports #4, #5, #10, #11, #14 (local SQLite/FS <-> GCS WORM, Cloud SQL PG16, "
            "BigQuery Iceberg & Terraform; 31 get_adapter leaks open in P15-8)."
        ),
        gap_note=(
            "SqlKnowledgeCatalog unlocked DELETE/INSERT wipe (P16-3); /v1/intake/ingest "
            "skips gate; Path-A PII log leak (P16-7); 55 GCP tests skip offline."
        ),
        code_files=(
            "bridge/vault.py",
            "bridge/operational_store/sql_store.py",
            "bridge/sql_runtime.py",
            "bridge/knowledge_catalog/catalog.py",
            "bridge/refinery/okf_loader.py",
        ),
        code_symbols=(
            ("bridge/vault.py", "redact_personal_data"),
            ("bridge/operational_store/sql_store.py", "SqlOperationalStore"),
            ("bridge/sql_runtime.py", "DsnSqlRuntime"),
            ("bridge/knowledge_catalog/catalog.py", "KnowledgeCatalog"),
        ),
        pillar_category="gcp",
    ),
    SubsystemSpec(
        node_id="db_proactive_chase",
        height=88,
        span=2,
        status="target",
        recent=False,
        has_gap=True,
        plain_title="Proactive Chase, Timers & Outbound Delivery",
        plain_sub=(
            "Zero-idle SLA nudge ladder (overdue -> reminder -> escalated), "
            "A2A dial-out & by-ref delivery"
        ),
        spec_title="Ports #6 Scheduler & #7 Outbound A2A",
        spec_sub="VirtualClock / Cloud Tasks · Nudge / DialOut / Deliver",
        adrs=("ADR-01", "ADR-09"),
        desc_plain=(
            "Schedules durable SLA reminders with zero compute held idle between "
            "turns, dials counterparty agents, and delivers bound packages by URI."
        ),
        desc_spec=(
            "Idempotent Nudge, DialOut, and Deliver activities keyed by exchange leg "
            "over Port #6 (VirtualClock / Cloud Tasks) and Port #7 (outbound A2A)."
        ),
        ports_contract=(
            "Port #6 scheduler (VirtualClock <-> Cloud Tasks) & Port #7 a2a_client "
            "(local router <-> SPIFFE/OIDC HTTP)."
        ),
        gap_note=(
            "HTTP /followups wake handler configured in Terraform is unmounted in "
            "bridge/; run-procurement SSRF & gateway host bypass open (P16-6, R2)."
        ),
        code_files=(
            "bridge/scheduler.py",
            "bridge/followup.py",
            "bridge/outbound.py",
            "bridge/edge/pushguard.py",
        ),
        code_symbols=(
            ("bridge/scheduler.py", "VirtualClock"),
            ("bridge/followup.py", "FollowupLadder"),
            ("bridge/outbound.py", "Nudge"),
            ("bridge/edge/pushguard.py", "SsrfGuardPushConfigStore"),
        ),
        pillar_category="gcp",
    ),
)

NODES: tuple[SubsystemSpec, ...] = CORE_NODES + DOWNSTREAM_NODES
