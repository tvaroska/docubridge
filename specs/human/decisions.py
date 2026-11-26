"""All 14 Ports, Adapters and Contracts plus ADR-01..ADR-16 for DocuBridge."""

from __future__ import annotations

from specs.human.contract import DecisionSpec, PortContractSpec

PORTS: tuple[PortContractSpec, ...] = (
    PortContractSpec(
        port_id="sessions",
        title="Port #1: Sessions Service",
        kind="storage",
        contract_paths=("bridge/ports/port.py", "bridge/ports/protocols.py"),
        adapter_paths=("bridge/ports/local/stores.py", "bridge/ports/gcp/stores.py"),
        test_paths=("tests/test_durable_ports.py", "tests/test_gcp_ports.py"),
        summary=(
            "ADK session persistence: SqliteSessionService locally, Agent Engine / "
            "PG16 in GCP."
        ),
    ),
    PortContractSpec(
        port_id="task_store",
        title="Port #2: A2A Task & Push Config Store",
        kind="storage",
        contract_paths=("bridge/ports/port.py", "bridge/ports/protocols.py"),
        adapter_paths=("bridge/ports/local/stores.py", "bridge/ports/gcp/stores.py"),
        test_paths=("tests/test_durable_ports.py", "tests/test_push_config.py"),
        summary=(
            "Durable A2A task state + SSRF-guarded push webhook store over SQLite "
            "or PG16."
        ),
    ),
    PortContractSpec(
        port_id="exchange_store",
        title="Port #3: Exchange & RFP CAS Store",
        kind="storage",
        contract_paths=("bridge/ports/port.py", "bridge/ports/protocols.py"),
        adapter_paths=("bridge/ports/local/stores.py", "bridge/ports/gcp/stores.py"),
        test_paths=("tests/test_exchange_store_port.py", "tests/test_durable_ports.py"),
        summary=(
            "Compare-and-set exchange and N x M RFP matrix state over InMemory or "
            "SQLAlchemy Core."
        ),
    ),
    PortContractSpec(
        port_id="skill_registry",
        title="Port #4: Runtime Skill Registry",
        kind="registry",
        contract_paths=("bridge/ports/port.py", "bridge/ports/protocols.py"),
        adapter_paths=(
            "bridge/ports/local/services.py",
            "bridge/ports/gcp/services.py",
        ),
        test_paths=(
            "tests/test_skill_registry_port.py",
            "tests/test_gcp_skill_registry.py",
        ),
        summary="Hot-loads and validates declarative skills/<name>/ folders (filesystem vs GCS).",
    ),
    PortContractSpec(
        port_id="memory_bank",
        title="Port #5: Memory Bank",
        kind="storage",
        contract_paths=("bridge/ports/port.py", "bridge/ports/protocols.py"),
        adapter_paths=("bridge/ports/local/stores.py", "bridge/ports/gcp/stores.py"),
        test_paths=("tests/test_ports.py", "tests/test_gcp_ports.py"),
        summary="ADK memory service (SQLite/InMemory locally, VertexAiMemoryBankService in GCP).",
    ),
    PortContractSpec(
        port_id="scheduler",
        title="Port #6: SLA Chase Scheduler",
        kind="transport",
        contract_paths=("bridge/ports/port.py", "bridge/ports/protocols.py"),
        adapter_paths=(
            "bridge/ports/local/services.py",
            "bridge/ports/gcp/services.py",
        ),
        test_paths=("tests/test_scheduler_port.py", "tests/test_gcp_scheduler.py"),
        summary="Zero-idle chase alarms: in-process VirtualClock locally vs Cloud Tasks queues.",
    ),
    PortContractSpec(
        port_id="a2a_client",
        title="Port #7: Outbound A2A Client",
        kind="transport",
        contract_paths=("bridge/ports/port.py", "bridge/ports/protocols.py"),
        adapter_paths=(
            "bridge/ports/local/services.py",
            "bridge/ports/gcp/services.py",
        ),
        test_paths=("tests/test_a2a_client_port.py", "tests/test_gcp_a2a_client.py"),
        summary=(
            "Discovers peer Agent Cards and invokes SendMessage (fake transport vs "
            "OIDC/SPIFFE)."
        ),
    ),
    PortContractSpec(
        port_id="extraction",
        title="Port #8: Document Extraction",
        kind="inference",
        contract_paths=("bridge/ports/port.py", "bridge/ports/protocols.py"),
        adapter_paths=(
            "bridge/ports/local/services.py",
            "bridge/ports/gcp/services.py",
        ),
        test_paths=("tests/test_extraction.py", "tests/test_docai_extraction.py"),
        summary="Fixture extractor locally; Gemini multimodal + Document AI prepass in GCP.",
    ),
    PortContractSpec(
        port_id="email_ingress",
        title="Port #9: Email Landing & Drain Store",
        kind="transport",
        contract_paths=("bridge/ports/port.py", "bridge/ports/protocols.py"),
        adapter_paths=(
            "bridge/ports/local/services.py",
            "bridge/ports/gcp/services.py",
        ),
        test_paths=(
            "tests/test_email_ingress_port.py",
            "tests/test_gcp_email_ingress.py",
        ),
        summary="Create-only MIME landing queue with pre-I/O filename and key validation.",
    ),
    PortContractSpec(
        port_id="audit_vault",
        title="Port #10: 7-Year WORM Audit Vault",
        kind="storage",
        contract_paths=("bridge/ports/port.py", "bridge/ports/protocols.py"),
        adapter_paths=(
            "bridge/ports/local/enterprise.py",
            "bridge/ports/gcp/enterprise.py",
        ),
        test_paths=(
            "tests/test_ports_and_adapters.py",
            "tests/test_single_bucket_gcp.py",
        ),
        summary="PII-scrubbed write-once artifact archive (InMemory/GCS locally, WORM GCS in GCP).",
    ),
    PortContractSpec(
        port_id="operational_store",
        title="Port #11: Bi-Temporal SQL Ledger",
        kind="storage",
        contract_paths=("bridge/ports/port.py", "bridge/ports/protocols.py"),
        adapter_paths=(
            "bridge/ports/local/enterprise.py",
            "bridge/ports/gcp/enterprise.py",
        ),
        test_paths=(
            "tests/test_operational_store_sql.py",
            "tests/test_ports_and_adapters.py",
        ),
        summary="Append-only Decimal bi-temporal fact store + role views + Iceberg CDC batch.",
    ),
    PortContractSpec(
        port_id="action_inbox",
        title="Port #12: A2UI Action Inbox",
        kind="governance",
        contract_paths=("bridge/ports/port.py", "bridge/ports/protocols.py"),
        adapter_paths=(
            "bridge/ports/local/enterprise.py",
            "bridge/ports/gcp/enterprise.py",
        ),
        test_paths=(
            "tests/test_enterprise_data_architecture.py",
            "tests/test_ports_and_adapters.py",
        ),
        summary="Page-pinned HITL review queue with RFC 6902 patch audit trail.",
    ),
    PortContractSpec(
        port_id="agent_gym",
        title="Port #13: Agent Gym Calibration Store",
        kind="learning",
        contract_paths=("bridge/ports/port.py", "bridge/ports/protocols.py"),
        adapter_paths=(
            "bridge/ports/local/enterprise.py",
            "bridge/ports/gcp/enterprise.py",
        ),
        test_paths=("tests/test_agent_gym_gate.py", "tests/test_ports_and_adapters.py"),
        summary="Distills verified corrections into EXTRACTION_HINT rules & compiled SKILL.md.",
    ),
    PortContractSpec(
        port_id="knowledge_catalog",
        title="Port #14: OKF Knowledge Catalog",
        kind="ontology",
        contract_paths=("bridge/ports/port.py", "bridge/ports/protocols.py"),
        adapter_paths=(
            "bridge/ports/local/enterprise.py",
            "bridge/ports/gcp/enterprise.py",
        ),
        test_paths=(
            "tests/test_ontology_mcp_and_query_validator.py",
            "tests/test_ports_and_adapters.py",
        ),
        summary="Immutable TBox OntologyRevision store, 6-hop PROV-O lineage, and query validator.",
    ),
)

ADRS: tuple[DecisionSpec, ...] = (
    DecisionSpec(
        adr_id="ADR-01",
        status="amended",
        evidence="measured",
        title="Hexagonal Ports & Adapters across all 14 boundaries (local vs gcp)",
        delta=(
            "Unified 9 core + 5 enterprise store boundaries under bridge/ports/; "
            "shared @pytest.mark.port parity suite."
        ),
        nodes=(
            "ext_counterparty",
            "ext_email",
            "db_intake_gate",
            "db_vault_ledger",
            "db_proactive_chase",
            "uc_benefits",
        ),
        journeys=("J1", "J3", "J4", "J5", "J6", "J9"),
    ),
    DecisionSpec(
        adr_id="ADR-02",
        status="active",
        evidence="measured",
        title="Hybrid deterministic 4-signal gate + ADK LlmAgent",
        delta=(
            "Separates code-enforced quality/freshness/completeness/corroboration "
            "verdicts (pin_gate_args) from conversational routing."
        ),
        nodes=("db_front_door", "db_intake_gate"),
        journeys=("J1", "J2", "J3"),
    ),
    DecisionSpec(
        adr_id="ADR-03",
        status="active",
        evidence="measured",
        title="Strict two-way package isolation (bridge/ <-> contract/ <-> agents/)",
        delta=(
            "AST firewall keeps bridge/ reusable and isolated from callers; "
            "contract/ holds all shared Pydantic wire schemas."
        ),
        nodes=(
            "ext_counterparty",
            "db_front_door",
            "uc_address",
            "uc_benefits",
            "uc_rfp_lending",
        ),
        journeys=("J1", "J4", "J5", "J9"),
    ),
    DecisionSpec(
        adr_id="ADR-04",
        status="active",
        evidence="measured",
        title="Declarative skill packages & zero hardcoded skill names",
        delta=(
            "Hot-loads skills/<name>/ (SKILL.md, schema, policy) via registry; AST "
            "guard forbids naming skill dirs in core Python."
        ),
        nodes=("db_intake_gate", "db_vault_ledger", "dl_agent_gym"),
        journeys=("J5", "J8"),
    ),
    DecisionSpec(
        adr_id="ADR-05",
        status="active",
        evidence="measured",
        title="Dual-path intake & Gemini/DocAI extraction with SourceAnchor provenance",
        delta=(
            "Path A JSON checks at $0 LLM cost; Path B pins every extracted field to "
            "exact PDF page and bounding polygon vertices."
        ),
        nodes=("db_intake_gate", "db_override_core", "uc_benefits"),
        journeys=("J1", "J2", "J6", "J7"),
    ),
    DecisionSpec(
        adr_id="ADR-06",
        status="amended",
        evidence="measured",
        title="7-year WORM GCS Audit Vault & bi-temporal SQL Operational Store",
        delta=(
            "Pairs PII-scrubbed write-once GCS retention lock with append-only "
            "recorded_at vs effective_from Decimal ledger + Iceberg CDC."
        ),
        nodes=("db_override_core", "db_vault_ledger", "uc_benefits", "uc_rfp_lending"),
        journeys=("J6", "J7", "J9"),
    ),
    DecisionSpec(
        adr_id="ADR-07",
        status="active",
        evidence="measured",
        title="Zone 6 Knowledge Catalog & stateless 6-hop W3C PROV-O lineage",
        delta=(
            "Hydrates full Vault -> Extraction -> Gate -> Consolidation -> Fact -> "
            "A2UI trace dynamically across stateless Cloud Run replicas."
        ),
        nodes=("db_vault_ledger", "dl_schema_refinery"),
        journeys=("J6", "J8"),
    ),
    DecisionSpec(
        adr_id="ADR-08",
        status="amended",
        evidence="friction",
        title="SPIFFE JWT-SVID & OBO compound identity (Agent ∩ User)",
        delta=(
            "Enforces workload JWT-SVID + human OBO role/tenant intersection and Shadow "
            "AI blocking; hardened in P14 & 850f034."
        ),
        nodes=("ext_counterparty", "ext_email", "db_front_door", "hr_inbox"),
        journeys=("J2", "J7"),
    ),
    DecisionSpec(
        adr_id="ADR-09",
        status="active",
        evidence="measured",
        title="Declarative A2UI v0.9 surfaces & zone-isolated Action Inbox",
        delta=(
            "Splits internal vs portal widget catalogs, uses native ADK "
            "request_confirmation pauses, and logs RFC 6902 patch audits."
        ),
        nodes=("ext_portal", "db_proactive_chase", "hr_inbox", "hr_rsr_guard"),
        journeys=("J2", "J3", "J6", "J8"),
    ),
    DecisionSpec(
        adr_id="ADR-10",
        status="amended",
        evidence="friction",
        title="Agent Gym, Two-Sided Oversight (RSR) & Registry/Gateway policies",
        delta=(
            "Gates SME edits on RSR (citation + dwell), enforces 3-tier validation, "
            "compiles SKILL.md, and enforces Model Armor + UAP CEL."
        ),
        nodes=("db_front_door", "hr_rsr_guard", "dl_agent_gym"),
        journeys=("J6", "J8"),
    ),
    DecisionSpec(
        adr_id="ADR-11",
        status="active",
        evidence="measured",
        title="AST service-wiring guard & shared DsnSqlRuntime",
        delta=(
            "Bans unguarded InMemory* in cloud paths and multiplexes SQL stores over "
            "one refcounted async pool per DATABASE_URL."
        ),
        nodes=("db_front_door", "db_vault_ledger"),
        journeys=("J2", "J6", "J9"),
    ),
    DecisionSpec(
        adr_id="ADR-12",
        status="amended",
        evidence="design",
        title="22-step OpenTelemetry gen_ai.* tracing & BigQuery Rule 0 SQL eval",
        delta=(
            "Evaluates 100% of traces with 5 free SQL gates, samples ~5% for "
            "AI.GENERATE_TABLE judging, and debias-corrects via Rogan-Gladen."
        ),
        nodes=("ext_portal", "dl_trace_eval"),
        journeys=("J7", "J9"),
    ),
    DecisionSpec(
        adr_id="ADR-13",
        status="amended",
        evidence="measured",
        title="Schema Refinery, OKF doctype bundles & bi-temporal ontology graph",
        delta=(
            "Makes catalog/doctypes/*.okf.md canonical, drives graph precedence, and "
            "gates ontology splits/edits on NFR=0.0% + live alpha/AC1."
        ),
        nodes=(
            "db_intake_gate",
            "db_override_core",
            "db_vault_ledger",
            "dl_schema_refinery",
        ),
        journeys=("J6", "J7", "J8"),
    ),
    DecisionSpec(
        adr_id="ADR-14",
        status="amended",
        evidence="measured",
        title="Decision authority: evidence left (DocuBridge), judgment right (Caller)",
        delta=(
            "DocuBridge evaluates static strategy.yaml into SufficiencyVerdict (Sense A'); "
            "callers own waivers and 'done' (Sense B); core boots empty."
        ),
        nodes=(
            "db_front_door",
            "db_override_core",
            "uc_address",
            "uc_benefits",
            "uc_rfp_lending",
            "hr_inbox",
            "hr_rsr_guard",
        ),
        journeys=("J1", "J4", "J5", "J8"),
    ),
    DecisionSpec(
        adr_id="ADR-15",
        status="active",
        evidence="measured",
        title="Calibration scoped to fact extraction only + Port #14 knowledge_catalog",
        delta=(
            "Forbids Calibration from learning checklists/waivers; adds Stage-B "
            "FieldDerivation receipts and persists TBox/ABox graph in SQL + BQ."
        ),
        nodes=(
            "db_intake_gate",
            "db_override_core",
            "db_vault_ledger",
            "uc_address",
            "dl_agent_gym",
            "dl_schema_refinery",
        ),
        journeys=("J1", "J6", "J8", "J9"),
    ),
    DecisionSpec(
        adr_id="ADR-16",
        status="active",
        evidence="measured",
        title="Bi-temporal consolidation precedence: effective_from before priority",
        delta=(
            "P13-3 fix: sorts winners (effective_from, source_priority, recorded_at, "
            "version) DESC across Python and SQL views, replacing multi-row tables."
        ),
        nodes=("db_override_core", "db_vault_ledger", "uc_rfp_lending"),
        journeys=("J6", "J7"),
    ),
)
