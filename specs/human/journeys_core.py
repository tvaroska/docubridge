"""Core CUJ walkthroughs (J1-J5) for the DocuBridge /human specification."""

from __future__ import annotations

from specs.human.contract import JourneySpec, JourneyStepSpec

CORE_JOURNEYS: tuple[JourneySpec, ...] = (
    JourneySpec(
        journey_id="J1",
        fit_arch="7.0/10",
        fit_secondary="7.0/10",
        tier="Core · CUJ-1, 4, 10",
        plain_name="Bounded Address Collect",
        spec_name="J1: Single Pull & Bounded Address Collect (CUJ-1, CUJ-4, CUJ-10)",
        tagline=(
            "Use-Case Agent asks once -> DocuBridge chases & checks 1 gov-ID or 2 "
            "distinct utility bills -> returns SufficiencyVerdict while caller owns 'done'."
        ),
        works_from_core=(
            "133/133 gate-inversion checks, Path A byte-identical parity, blurry "
            "resubmit loop <=3, same-issuer bill rejection."
        ),
        external_build=(
            "Service agent Workflow (in docubridge-examples) owning is_satisfied & 'done'."
        ),
        blocking_gap=(
            "Path-A validator omits as_of freshness check (P16-5); example consumer "
            "gate helpers still need direct A' verdict consumption."
        ),
        matrix={
            "core": "CORE-1..7, PATH-A/B",
            "eval": "Rule 0 4-Signal + Address oracle",
            "runtime": "A2A R1/R2 (0 LLM on struct)",
            "ext": "strategy.yaml Sense A' + docubridge-examples",
        },
        steps=(
            JourneyStepSpec(
                node_id="uc_address",
                edge=None,
                plain_action=(
                    "Address Use-Case Agent opens an exchange requesting 1 gov ID or "
                    "2 distinct-issuer utility bills while keeping waiver authority."
                ),
                spec_action=(
                    "ADK Workflow node (RemoteA2aAgent) sends CollectRequest("
                    "process='address-proof') over contract/ A2A v1.0."
                ),
                delta_note=(
                    "ADR-14/15 clarify caller owns completeness ('done') and waivers; "
                    "core never imports caller agents (ADR-03)."
                ),
                adrs=("ADR-03", "ADR-14", "ADR-15"),
            ),
            JourneyStepSpec(
                node_id="db_front_door",
                edge="m_uc_db",
                plain_action=(
                    "DocuBridge Front Door authenticates the caller and routes "
                    "structured turns straight to the zero-LLM Collector."
                ),
                spec_action=(
                    "CollectExecutor routes R1/R2 at 0 LLM invocations and mints "
                    "durable context_id per exchange."
                ),
                delta_note="Hybrid mode proves structured collection burns zero LLM calls.",
                adrs=("ADR-02", "ADR-03", "ADR-14"),
                gap="executor.py at 800-LOC cap (P16-10).",
            ),
            JourneyStepSpec(
                node_id="ext_portal",
                edge="m_db_ext",
                plain_action=(
                    "Applicant or external peer submits either structured JSON "
                    "(Path A) or a scanned utility bill PDF (Path B)."
                ),
                spec_action=(
                    "External party submits structured assertion or leg-scoped "
                    "FileWithUri scan back into the open exchange."
                ),
                delta_note="Requester sees one uniform task model regardless of maturity.",
                adrs=("ADR-05", "ADR-09"),
            ),
            JourneyStepSpec(
                node_id="db_intake_gate",
                edge="m_ext_db",
                plain_action=(
                    "Intake validates JSON at $0 or extracts PDF fields, then runs "
                    "the code-only 4-signal gate (resubmit <=3 on blurry scans)."
                ),
                spec_action=(
                    "Path-A validator or Port #8 extractor -> dispose_signals + "
                    "pin_gate_args enforces quality, freshness & validation.yaml."
                ),
                delta_note="Models cannot override or self-approve a rejected scan.",
                adrs=("ADR-01", "ADR-02", "ADR-05", "ADR-15"),
                gap="P16-5: Path-A validator omits as_of freshness check.",
            ),
            JourneyStepSpec(
                node_id="db_override_core",
                edge=None,
                plain_action=(
                    "Static sufficiency checker evaluates '1 more bill from a different "
                    "utility needed' with evidence—never waiving requirements."
                ),
                spec_action=(
                    "Evaluates skills/address-proof/assets/strategy.yaml "
                    "(distinct_issuer_from fixed in P13-8) into SufficiencyVerdict."
                ),
                delta_note="ADR-14 Sense A' computes evidence; Sense B stays outside.",
                adrs=("ADR-14", "ADR-15"),
            ),
            JourneyStepSpec(
                node_id="uc_address",
                edge="m_db_uc",
                plain_action=(
                    "DocuBridge returns clean verified facts + sufficiency evidence "
                    "to the Use-Case Agent, which marks onboarding done."
                ),
                spec_action=(
                    "Caller inspects ExchangeTurn.sufficiency + ledger projection and "
                    "sets final workflow completion."
                ),
                delta_note="Clean separation lets multiple callers apply distinct policies.",
                adrs=("ADR-03", "ADR-14"),
            ),
        ),
    ),
    JourneySpec(
        journey_id="J2",
        fit_arch="6.5/10",
        fit_secondary="6.0/10",
        tier="Core · CUJ-2, 5, 7",
        plain_name="Portal HITL Park & Restart",
        spec_name="J2: Human Upload, Zero-Compute HITL Park & Process Restart (CUJ-2, 5, 7)",
        tagline=(
            "External Portal upload flags borderline scan -> DocuBridge parks "
            "INPUT_REQUIRED at zero compute across restarts -> Human resolves & resumes."
        ),
        works_from_core=(
            "LongRunningFunctionTool pause/resume, R6 call_id+reviewer allowlist "
            "guards, P14 principal scoping & EdgeAuthBackend."
        ),
        external_build=(
            "Cloud Run / Agent Engine managed session + task store + IAP/OIDC "
            "secret configuration."
        ),
        blocking_gap=(
            "ActionInboxStore.resolve_action lacks terminal-state guard (P16-4); "
            "frontend/deploy.sh routing gap (S-12)."
        ),
        matrix={
            "core": "WIRE-3,8 · DUR-1..4",
            "eval": "R6 Reviewer auth + P14 openAPI sweep",
            "runtime": "SqlExchangeStore / DatabaseTaskStore",
            "ext": "A2UI Portal Path-B + Agent Engine",
        },
        steps=(
            JourneyStepSpec(
                node_id="ext_portal",
                edge=None,
                plain_action=(
                    "Human counterparty uploads a borderline PDF in the A2UI Portal "
                    "without seeing internal storage."
                ),
                spec_action=(
                    "provider-portal emits A2UI Path-B surface; uploaded file receives "
                    "leg-scoped FileWithUri."
                ),
                delta_note="P14-2 isolated showcase viewer cookie from non-showcase routes.",
                adrs=("ADR-09",),
                gap="S-12: deploy.sh Vite env wiring mismatch.",
            ),
            JourneyStepSpec(
                node_id="db_front_door",
                edge="m_ext_db",
                plain_action=(
                    "Front Door verifies compound identity (Agent ∩ User) and pins "
                    "leg access."
                ),
                spec_action=(
                    "EdgeAuthBackend validates IAP ES256 audience (P14-7), JWT-SVID "
                    "alg pin (P14-10) & gs:// allowlist (P14-8)."
                ),
                delta_note="P14 hardened auth across 163 API routes.",
                adrs=("ADR-08", "ADR-10", "ADR-11"),
                gap="P16-6 NAT64 SSRF & P16-8 live Model Armor fail-open stay open.",
            ),
            JourneyStepSpec(
                node_id="db_intake_gate",
                edge=None,
                plain_action=(
                    "4-Signal Gate routes mid-confidence scan to hitl_review instead "
                    "of guessing approval, parking at zero idle compute."
                ),
                spec_action=(
                    "request_human_review LongRunningFunctionTool persists call_id in "
                    "durable SQL/Agent Engine task store across container restarts."
                ),
                delta_note=(
                    "ADR-11 shared DsnSqlRuntime + AST guard prevent 86.8% "
                    "multi-replica InMemory 404 drops."
                ),
                adrs=("ADR-02", "ADR-11"),
                gap="P16-9: _task_outcomes leaks across repeated HITL/failed turns.",
            ),
            JourneyStepSpec(
                node_id="hr_inbox",
                edge="m_db_hr",
                plain_action=(
                    "Human Reviewer inspects page-pinned bounding boxes side-by-side "
                    "and submits an authorized resolution."
                ),
                spec_action=(
                    "Action Inbox enforces external-role 403 & Decimal role limits "
                    "(P14-3), logs RFC 6902 diff, and emits FunctionResponse."
                ),
                delta_note="R6 pre-dispatch guard refuses wrong caller or bad call_id.",
                adrs=("ADR-08", "ADR-09", "ADR-14"),
                gap="P16-4: sequential resolve_action lacks terminal-state CAS.",
            ),
            JourneyStepSpec(
                node_id="db_vault_ledger",
                edge="m_hr_db",
                plain_action=(
                    "Resumed exchange locks bytes in WORM Vault and writes final "
                    "consolidated facts onto any replica without affinity."
                ),
                spec_action=(
                    "GcpAuditVaultStore + SqlOperationalStore commit final truth with "
                    "full audit lineage."
                ),
                delta_note="Zero compute burned while waiting hours for human action.",
                adrs=("ADR-06", "ADR-11"),
            ),
        ),
    ),
    JourneySpec(
        journey_id="J3",
        fit_arch="6.0/10",
        fit_secondary="6.5/10",
        tier="Core · CUJ-6, 14",
        plain_name="Proactive Chases & Off-Script Intents",
        spec_name="J3: Durable Chase Ladder & Off-Script Delete/Reject/Escalate (CUJ-6, 14)",
        tagline=(
            "DocuBridge nudges silent External Parties on SLA alarms (overdue -> "
            "reminder -> escalated) and routes free-text delete/escalate safely."
        ),
        works_from_core=(
            "Idempotent VirtualClock/Cloud Tasks ladder, real delete_artifact 404 "
            "erase across versions, adversarial R4 text guard."
        ),
        external_build="HTTP /followups wake webhook on deployed Cloud Run / Cloud Tasks queue.",
        blocking_gap=(
            "terraform wires BRIDGE_FOLLOWUP_CALLBACK_URL=/followups while HTTP "
            "/followups wake handler is not yet mounted in bridge/."
        ),
        matrix={
            "core": "CORE-8, ART-1, DUR-6",
            "eval": "Off-script A2A & nudge-no-token guards",
            "runtime": "VirtualClock / Cloud Tasks · ArtifactService",
            "ext": "timewarp UI (local) + outbound A2A Nudge",
        },
        steps=(
            JourneyStepSpec(
                node_id="db_proactive_chase",
                edge=None,
                plain_action=(
                    "Scheduler alarm advances a silent exchange through overdue -> "
                    "reminder -> escalated with zero idle compute."
                ),
                spec_action=(
                    "Port #6 triggers idempotent Nudge(idemp='nudge:<ctx>:<step>') over "
                    "Port #7 outbound a2a_client without leaking bearer tokens."
                ),
                delta_note="Alarms survive restarts and never double-nudge.",
                adrs=("ADR-01",),
                gap="HTTP /followups wake route in Terraform still needs mount in bridge/.",
            ),
            JourneyStepSpec(
                node_id="ext_counterparty",
                edge="m_db_ext",
                plain_action=(
                    "External Party replies in free text instead of uploading: "
                    "'delete my file' or 'I need a human'."
                ),
                spec_action="Counterparty sends text-only message entering R4 off-script.",
                delta_note="Never misclassified as a structured CollectRequest.",
                adrs=("ADR-03",),
            ),
            JourneyStepSpec(
                node_id="db_front_door",
                edge="m_ext_db",
                plain_action=(
                    "Conversational router dispatches strict off-script tools; model "
                    "prose can never mint accepted documents."
                ),
                spec_action=(
                    "document_bridge LlmAgent calls handle_deletion_request, "
                    "reject_submission, or escalate_submission."
                ),
                delta_note="Adversary suite proves off-script tools cannot forge acceptance.",
                adrs=("ADR-02",),
            ),
            JourneyStepSpec(
                node_id="db_vault_ledger",
                edge=None,
                plain_action=(
                    "Deletion requests erase all versions of that leg's draft "
                    "artifacts so later fetches return 404 Not Found."
                ),
                spec_action=(
                    "Deletes versions scoped strictly to (document_bridge, party, "
                    "context_id), isolating foreign contexts (CUJ-14)."
                ),
                delta_note="Real deletion rather than soft-delete flags on drafts.",
                adrs=("ADR-06",),
            ),
            JourneyStepSpec(
                node_id="hr_inbox",
                edge="m_db_hr",
                plain_action=(
                    "Escalations park cleanly in the Human Reviewer queue until "
                    "resolved or canceled by the Use-Case Agent."
                ),
                spec_action=(
                    "escalate_submission holds lifecycle='escalated' across restarts "
                    "(DUR-6); caller retains cancel authority."
                ),
                delta_note="Preserves caller ownership over terminal cancellation.",
                adrs=("ADR-09", "ADR-14"),
            ),
        ),
    ),
    JourneySpec(
        journey_id="J4",
        fit_arch="6.5/10",
        fit_secondary="6.5/10",
        tier="Benefits · CUJ-3, 11, 12",
        plain_name="Benefits Negotiate & LangGraph",
        spec_name="J4: Multi-Carrier Benefits Fan-Out, Negotiate & Deliver (CUJ-3, 11, 12)",
        tagline=(
            "Benefits Use-Case Agent -> DocuBridge fans out to ADK & LangGraph "
            "External Carriers in USD/EUR/GBP -> normalizes, negotiates & delivers."
        ),
        works_from_core=(
            "ADK<->LangGraph A2A parity, fixed FX normalization, versioned revise "
            "cap -> escalate, Deliver idempotency."
        ),
        external_build="External Benefits consumer service agent & carrier A2A servers.",
        blocking_gap=(
            "open-questions.md notes live ownership fork: caller delegates extraction "
            "vs keeps own BenefitsQuote extractor."
        ),
        matrix={
            "core": "BEN-1..7, OUT-3..4",
            "eval": "Conformance JSON + interop tests",
            "runtime": "DialOut + Delivery A2A Client",
            "ext": "ADK + LangGraph counterparties",
        },
        steps=(
            JourneyStepSpec(
                node_id="uc_benefits",
                edge=None,
                plain_action=(
                    "Benefits Use-Case Agent requests quotes across 4 competing "
                    "carriers with different languages and currencies."
                ),
                spec_action="Starts program:acme-2026 fan-out (BEN-1) over contract/ A2A.",
                delta_note="Caller owns carrier selection, negotiation caps, and bind policy.",
                adrs=("ADR-03", "ADR-14"),
                gap="Open ownership question on BenefitsQuote extraction boundary.",
            ),
            JourneyStepSpec(
                node_id="db_proactive_chase",
                edge="m_uc_db",
                plain_action="DocuBridge dials out to each carrier in an isolated exchange leg.",
                spec_action=(
                    "Port #7 DialOut opens per-carrier contexts so peers never see "
                    "rival bids."
                ),
                delta_note="Unifies DialOut, Nudge, and Deliver on one outbound port.",
                adrs=("ADR-01",),
            ),
            JourneyStepSpec(
                node_id="ext_counterparty",
                edge="m_db_ext",
                plain_action=(
                    "ADK and LangGraph carrier agents respond with multi-page "
                    "French/EUR and German/GBP rate tables."
                ),
                spec_action=(
                    "CUJ-12 interop runs multi-turn revise loops across heterogeneous "
                    "agent frameworks."
                ),
                delta_note="Canonical A2A v1.0 requires zero framework-specific Bridge glue.",
                adrs=("ADR-01", "ADR-03"),
            ),
            JourneyStepSpec(
                node_id="db_intake_gate",
                edge="m_ext_db",
                plain_action=(
                    "Chunks 1,000-row rate sheets without dropping rows, converts FX "
                    "deterministically, and flags standard.yaml deviations."
                ),
                spec_action=(
                    "Page-window chunking bisects on MAX_TOKENS; normalizer maps FX & "
                    "checks standard.yaml (BEN-2/3)."
                ),
                delta_note="Preserves duplicate rows visibly instead of silent truncation.",
                adrs=("ADR-05",),
            ),
            JourneyStepSpec(
                node_id="uc_benefits",
                edge="m_db_uc",
                plain_action=(
                    "DocuBridge serves live side-by-side carrier rankings to Benefits "
                    "Agent and delivers bound package outward by URI."
                ),
                spec_action=(
                    "comparison.py recomputes live from SQL ledger; Deliver sends "
                    "scoped ArtifactRef."
                ),
                delta_note="Zero inline bytes or bearer token leaks on delivery.",
                adrs=("ADR-01", "ADR-06", "ADR-14"),
            ),
        ),
    ),
    JourneySpec(
        journey_id="J5",
        fit_arch="6.0/10",
        fit_secondary="6.0/10",
        tier="RFP · CUJ-8, 9, 13, 15",
        plain_name="Emergent RFP, Live Add & Email",
        spec_name=(
            "J5: Emergent N x M RFP Matrix, Zero-Redeploy Skill Upload & Email "
            "(CUJ-8,9,13,15)"
        ),
        tagline=(
            "Hot-load a new industry live -> Use-Case Agent mutates N x M supplier "
            "matrix -> External Suppliers reply via HMAC-tracked emails."
        ),
        works_from_core=(
            "Compare-and-set RFP board, live Agent Card regeneration next GET, "
            "create-only email quarantine, replay idempotency."
        ),
        external_build=(
            "M365 Graph webhook credentials, Cloud Scheduler /email/drain, runtime "
            "Skill Registry GCS bucket."
        ),
        blocking_gap=(
            "email_ingress.py at 792 LOC (P16-10); email intake omits as_of freshness "
            "check (P16-5); board_host S-14 docs/redoc unauthenticated."
        ),
        matrix={
            "core": "COLL-5..7, SKILL-4..5, RFP-1..2",
            "eval": "Live use-case add + email replay noop",
            "runtime": "EmailIngress Port + SqlExchangeStore CAS",
            "ext": "public email webhook + scheduler drain",
        },
        steps=(
            JourneyStepSpec(
                node_id="db_vault_ledger",
                edge=None,
                plain_action=(
                    "Operator hot-uploads a new doctype/skill bundle into the running "
                    "registry with zero server restart."
                ),
                spec_action=(
                    "Port #4 (skill_registry) validates OKF/SKILL.md before install "
                    "(CUJ-8/9); dynamic AgentCard updates on next GET."
                ),
                delta_note="In-flight exchanges continue uninterrupted.",
                adrs=("ADR-04", "ADR-14"),
            ),
            JourneyStepSpec(
                node_id="uc_rfp_lending",
                edge="m_db_uc",
                plain_action=(
                    "RFP Use-Case Agent opens an N x M supplier matrix and adds or "
                    "waives line items dynamically at runtime."
                ),
                spec_action=(
                    "open_or_update_rfp updates CAS SqlExchangeStore; caller owns "
                    "waivers and 'done' (ADR-14)."
                ),
                delta_note="DocuBridge omits waived items from delta without mutating policy.",
                adrs=("ADR-03", "ADR-14"),
                gap="S-14: board_host /docs route unauthenticated.",
            ),
            JourneyStepSpec(
                node_id="ext_email",
                edge="m_db_ext",
                plain_action=(
                    "Suppliers reply by emailing PDF attachments to their unique "
                    "HMAC-tokenized leg address."
                ),
                spec_action=(
                    "MIME reply lands create-only via Port #9; /email/drain correlates "
                    "HMAC token with OIDC auth (P14-2)."
                ),
                delta_note="Unsigned or ambiguous emails quarantine safely.",
                adrs=("ADR-01", "ADR-08"),
                gap="P16-5: EmailProcessor ignores within_days freshness clock.",
            ),
            JourneyStepSpec(
                node_id="db_intake_gate",
                edge="m_ext_db",
                plain_action=(
                    "Email attachments extract and run through the 4-signal gate onto "
                    "the exact (supplier, item) cell idempotently."
                ),
                spec_action=(
                    "Extracts attachments and updates RFP cell via compare-and-set; "
                    "duplicate email replay is a strict no-op."
                ),
                delta_note="Same gate enforces quality across API, portal, and email.",
                adrs=("ADR-02",),
            ),
        ),
    ),
)
