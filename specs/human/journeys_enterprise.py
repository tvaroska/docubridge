"""Enterprise journeys (J6-J9) and matrix columns for the DocuBridge /human spec."""

from __future__ import annotations

from specs.human.contract import JourneySpec, JourneyStepSpec

MATRIX_COLUMNS: tuple[str, ...] = (
    "Journey",
    "Core Features",
    "Gates & Quality",
    "Runtime & Stores",
    "Ports & Gaps",
    "",
)

ENTERPRISE_JOURNEYS: tuple[JourneySpec, ...] = (
    JourneySpec(
        journey_id="J6",
        fit_arch="6.5/10",
        fit_secondary="6.0/10",
        tier="Banking · /ebc",
        plain_name="Commercial Loan & 4-Beat /ebc",
        spec_name="J6: CRE Term Loan ($45M LOAN-MERIDIAN-001) & 4-Beat Stage (/ebc)",
        tagline=(
            "5 banking PDFs enter Portal -> DocuBridge locks WORM bytes, resolves "
            "Page 4 Addendum & PAID IN FULL wire in Decimal -> Underwriter recommends waiver."
        ),
        works_from_core=(
            "Decimal math ($45M LTV=72.00%, $1.8M LTV=69.23%/DSCR=1.44x), ADR-16 "
            "table replacement, PAID IN FULL exact status check (P13-9)."
        ),
        external_build="Cloud Run /ebc host + 7-year GCS WORM bucket + Cloud SQL PG16.",
        blocking_gap=(
            "3 showcase callers retry VaultImmutabilityError unconditionally; "
            "derivations still live partly in Python (R2-B); N=0 live noisy PDFs."
        ),
        matrix={
            "core": "ENT-1..6 · SourceAnchor",
            "eval": "Decimal currency + consolidation (P13-2,3,9)",
            "runtime": "WORM Vault + SqlOperationalStore",
            "ext": "/demo + /ebc live multimodal PDFs",
        },
        steps=(
            JourneyStepSpec(
                node_id="ext_portal",
                edge=None,
                plain_action=(
                    "5 institutional banking PDFs (Financials, MAI Appraisal, Payoff, "
                    "Oct Bank Statement, Fee Addendum) upload through /ebc."
                ),
                spec_action=(
                    "/ebc streams multimodal PDFs extracting monetary fields + "
                    "SourceAnchor page polygons."
                ),
                delta_note="Supports live PDF bytes or deterministic cached replay.",
                adrs=("ADR-05", "ADR-09"),
                gap="R2-A duplicate demo/ vs frontend/ UI surfaces.",
            ),
            JourneyStepSpec(
                node_id="db_intake_gate",
                edge="m_ext_db",
                plain_action=(
                    "Stage-B Python derivation spots exact 'PAID IN FULL' wire status "
                    "and derives $0.00 balance with SHA-256 receipt."
                ),
                spec_action=(
                    "bridge/extraction/derivations.py emits FieldDerivation only on "
                    "exact PAID IN FULL status (P13-9 closed 'NOT PAID IN FULL')."
                ),
                delta_note="Models extract text; Python computes derived balances.",
                adrs=("ADR-05", "ADR-15"),
                gap="R2-B: Move remaining hardcoded banking derivations into OKF.",
            ),
            JourneyStepSpec(
                node_id="db_override_core",
                edge=None,
                plain_action=(
                    "Decimal Override Engine resolves Schedule B Page 4 Fee Addendum "
                    "($100K vs $50K) and replaces outdated debt tables cleanly."
                ),
                spec_action=(
                    "ADR-16 orders winners (effective_from DESC -> source_priority -> "
                    "recorded_at) with closed fullmatch currency grammar (P13-2/3)."
                ),
                delta_note="Closed 1,200 MXN -> 1.2B regex bug and partial row mixing.",
                adrs=("ADR-06", "ADR-13", "ADR-16"),
            ),
            JourneyStepSpec(
                node_id="db_vault_ledger",
                edge=None,
                plain_action=(
                    "Scrubs SSNs/routing numbers, locks raw PDFs in 7-yr WORM storage, "
                    "and writes append-only LTV/DSCR role views."
                ),
                spec_action=(
                    "GcpAuditVaultStore locks content hash (P13-5/10); "
                    "SqlOperationalStore stamps ontology & consolidation lineage."
                ),
                delta_note="Every dollar links 6 hops back to its locked PDF polygon.",
                adrs=("ADR-06", "ADR-07", "ADR-16"),
            ),
            JourneyStepSpec(
                node_id="hr_inbox",
                edge="m_db_hr",
                plain_action=(
                    "Human Reviewer verifies citation + dwell time and sends a verified "
                    "waiver recommendation for retired Loan #8841."
                ),
                spec_action=(
                    "ActionInbox logs SPIFFE OBO identity & citation_opened (P13-7) "
                    "to emit recommendation."
                ),
                delta_note="Reviewer recommends; Use-Case Agent owns final waiver.",
                adrs=("ADR-08", "ADR-10", "ADR-14"),
            ),
            JourneyStepSpec(
                node_id="uc_rfp_lending",
                edge="m_db_uc",
                plain_action=(
                    "Lending Use-Case Agent receives reconciled 72.00% LTV facts + "
                    "waiver recommendation to make the credit decision."
                ),
                spec_action="Underwriting consumer reads Role View projection over contract/.",
                delta_note="Evidence Left (DocuBridge), Credit Policy Right (Caller).",
                adrs=("ADR-14", "ADR-16"),
                gap="3 showcase callers retry VaultImmutabilityError unconditionally.",
            ),
        ),
    ),
    JourneySpec(
        journey_id="J7",
        fit_arch="6.0/10",
        fit_secondary="6.0/10",
        tier="Mortgage & Insurance",
        plain_name="Mortgage TRID & Sovereign Placement",
        spec_name="J7: Westbrook $1.85M TRID & Terrepower $142.5M Insurance Placement",
        tagline=(
            "Path-A JSON + Path-B PDF addenda reconcile across as-known / "
            "as-effective timelines (2026 renewal beats 2024 endorsement) & sync to BQ."
        ),
        works_from_core=(
            "Composite bi-temporal index queries, P13-3 effective-date renewal vs old "
            "endorsement fix, P14-3 tenant & Decimal limit enforcement."
        ),
        external_build=(
            "AlloyDB / Cloud SQL PG16 + Cloud IAP ES256 audience + BigQuery Open "
            "Iceberg CDC."
        ),
        blocking_gap=(
            "Multiple roles lack formal governance sign-off taxonomy; deployed "
            "multi-tenant IAM/IAP requires Secret Manager setup (P14-6)."
        ),
        matrix={
            "core": "ENT-2..5 · Bi-temporal",
            "eval": "Effective-before-priority + tenant isolation",
            "runtime": "SqlOperationalStore + Iceberg CDC",
            "ext": "Banking/Mortgage/Insurance presets",
        },
        steps=(
            JourneyStepSpec(
                node_id="ext_counterparty",
                edge=None,
                plain_action=(
                    "External lender/broker feeds structured URLA Form 1003 JSON "
                    "alongside late TRID Closing Disclosure and Wind Endorsement PDFs."
                ),
                spec_action=(
                    "Combines $0 Path-A JSON baseline ($4,250 fee) with Path-B PDF "
                    "addenda ($8,500 fee; $50K -> $100K deductible)."
                ),
                delta_note="Mixed structured + PDF packets share one schema contract.",
                adrs=("ADR-05", "ADR-13"),
            ),
            JourneyStepSpec(
                node_id="db_front_door",
                edge="m_ext_db",
                plain_action=(
                    "Zero-Trust Front Door enforces tenant isolation and "
                    "currency-exact approval ceilings across Agent ∩ User."
                ),
                spec_action=(
                    "Blocks cross-client substring bypasses (P14-3) and verifies "
                    "Cloud IAP ES256 audience (P14-7)."
                ),
                delta_note="Closed unscoped tenant and missing limit claims.",
                adrs=("ADR-08",),
            ),
            JourneyStepSpec(
                node_id="db_override_core",
                edge=None,
                plain_action=(
                    "Override Core ensures a 2026 policy renewal beats an older 2024 "
                    "endorsement while same-date endorsements beat base quotes."
                ),
                spec_action=(
                    "ADR-16 (_winning_key) resolves active_coverage_as_of_loss in "
                    "exact Decimal."
                ),
                delta_note="Aligned Python consolidation with ontology [:SUPERSEDES] SQL.",
                adrs=("ADR-06", "ADR-16"),
            ),
            JourneyStepSpec(
                node_id="db_vault_ledger",
                edge=None,
                plain_action=(
                    "Analysts query facts as-of effective date (T1) and recorded date "
                    "(T2), mirrored to BigQuery Iceberg tables."
                ),
                spec_action=(
                    "ix_operational_facts_bitemporal serves time-travel views; "
                    "export_bigquery_sync_batch streams watermarked CDC."
                ),
                delta_note="Zero overwrite of prior state satisfies audit retention.",
                adrs=("ADR-06", "ADR-12", "ADR-15", "ADR-16"),
                gap="55 cloud-gated BigQuery/WORM tests skip offline without creds.",
            ),
            JourneyStepSpec(
                node_id="uc_rfp_lending",
                edge="m_db_uc",
                plain_action=(
                    "Mortgage & Insurance Use-Case Agents consume reconciled closing "
                    "disclosures and bindable placement summaries."
                ),
                spec_action="Callers read role-scoped projections without raw PDFs.",
                delta_note="Reusable across Banking, Mortgage, and Commercial Insurance.",
                adrs=("ADR-03", "ADR-14"),
            ),
        ),
    ),
    JourneySpec(
        journey_id="J8",
        fit_arch="5.5/10",
        fit_secondary="6.0/10",
        tier="Calibration · CUJ-5, 8",
        plain_name="Calibration (Gym & Refinery)",
        spec_name="J8: Two-Sided Calibration — Agent Gym (Skills) & Schema Refinery",
        tagline=(
            "Human Reviewer fixes scan -> RSR guard verifies rigor -> Day-2 Loop "
            "compiles hints & schema splits -> DocuBridge improves; policy untouched."
        ),
        works_from_core=(
            "P13-1 real held-out evaluation, P13-7 RSR fail-closed defaults, 1412b76 "
            "honest offline alpha/AC1 reporting, immutable OKF snapshots."
        ),
        external_build="gemini-4-argon judge endpoint + optional Cloud Run nightly job.",
        blocking_gap=(
            "compile_rules_to_skill pulls CANDIDATE/REJECTED rules on fallback (P16-2); "
            "HTTP onboard-use-case LFI (P16-1); SqlKnowledgeCatalog wipe (P16-3)."
        ),
        matrix={
            "core": "SKILL-4, ENT-6 · RSR>=0.80",
            "eval": "3-Tier Assay Gate (NFR=0%, alpha>=0.80 live)",
            "runtime": "SqlAgentGym + SqlKnowledgeCatalog",
            "ext": "OKF v0.2 split/merge + SKILL.md compiler",
        },
        steps=(
            JourneyStepSpec(
                node_id="hr_inbox",
                edge=None,
                plain_action=(
                    "Human Reviewer opens a flagged PDF page box and submits a field "
                    "or table correction."
                ),
                spec_action=(
                    "ActionInbox records RFC 6902 patch + citation_opened + "
                    "review_dwell_ms."
                ),
                delta_note="Captures structured training signal from normal operations.",
                adrs=("ADR-09", "ADR-14"),
                gap="P16-4 double-resolve guard needed.",
            ),
            JourneyStepSpec(
                node_id="hr_rsr_guard",
                edge=None,
                plain_action=(
                    "Anti-Rubber-Stamp Guard blocks low-effort clicks (<0.50 RSR) "
                    "and passes only verified edits (>=0.80 RSR) into learning."
                ),
                spec_action=(
                    "P13-7 enforces non-default citation_verified=True and blind "
                    "pre-commitment."
                ),
                delta_note="Prevents bad reviewer habits from poisoning prompts.",
                adrs=("ADR-10",),
            ),
            JourneyStepSpec(
                node_id="dl_agent_gym",
                edge="m_hr_dl",
                plain_action=(
                    "Agent Gym distills extraction hints & Stage-B rules, testing "
                    "against held-out cases before compiling a SkillRevision."
                ),
                spec_action=(
                    "P13-1 runs real held-out checks with auto_promote=False; ADR-15 "
                    "forbids checklist/waiver edits."
                ),
                delta_note="Use-Case Agents on the right stay completely untouched.",
                adrs=("ADR-04", "ADR-10", "ADR-15"),
                gap="P16-2 fallback rule promotion & P16-1 onboard-use-case LFI open.",
            ),
            JourneyStepSpec(
                node_id="dl_schema_refinery",
                edge=None,
                plain_action=(
                    "Schema Refinery tests subtype splits against fail-closed 3-Tier "
                    "Assay (0% negative flips) before promoting an OntologyRevision."
                ),
                spec_action=(
                    "Assay defaults passed=False offline (1412b76) and writes "
                    "immutable lineage to Port #14."
                ),
                delta_note="Eliminated fabricated 38/40 alpha/AC2 constants.",
                adrs=("ADR-07", "ADR-13", "ADR-15"),
                gap="P16-3 SqlKnowledgeCatalog concurrent wipe open.",
            ),
            JourneyStepSpec(
                node_id="db_intake_gate",
                edge="m_dl_db",
                plain_action=(
                    "Next-day documents in DocuBridge extract cleanly on the first "
                    "pass using pinned ontology and skill revisions."
                ),
                spec_action=(
                    "Extractor pins OntologyRevision per flight and applies promoted "
                    "EXTRACTION_HINTs."
                ),
                delta_note="In-flight multi-page PDFs never mix schema versions mid-run.",
                adrs=("ADR-04", "ADR-13", "ADR-15"),
                gap="R5: GcpExtraction instantiates GeminiExtractor with _agent_gym=None.",
            ),
        ),
    ),
    JourneySpec(
        journey_id="J9",
        fit_arch="6.0/10",
        fit_secondary="6.0/10",
        tier="Observability & 14 Ports",
        plain_name="OTel, BQ Trace Eval & 14 Ports",
        spec_name="J9: 22-Step OTel Tracing, 3-Layer BQ SQL Eval & 14-Port Parity",
        tagline=(
            "Every exchange crossing Use-Case <-> DocuBridge emits 22-step W3C "
            "traces -> SQL Trace Eval filters ~95% at $0 -> 14 ports prove parity."
        ),
        works_from_core=(
            "22 instrumented pipeline steps, 5 deterministic Rule 0 SQL scorecards, "
            "14 dual-mode Port interfaces, AST import/InMemory guards."
        ),
        external_build="GCP Cloud Trace v2, BigQuery telemetry dataset, Cloud Build 4-track CI.",
        blocking_gap=(
            "59 lines in 16 files name external bq-eval views directly (decided "
            "2026-11-24 to decouple); scripts/gate.sh misses --cov-fail-under=90."
        ),
        matrix={
            "core": "PORT-1..14 · ENT-8 · TEST-1..5",
            "eval": "5 Rule 0 SQL gates + P15 gate fixes",
            "runtime": "otel_spans + DsnSqlRuntime",
            "ext": "4-track cloudbuild.yaml + terraform test",
        },
        steps=(
            JourneyStepSpec(
                node_id="uc_address",
                edge=None,
                plain_action=(
                    "Caller request enters over the AST-guarded contract/ wire "
                    "carrying W3C traceparent context."
                ),
                spec_action=(
                    "OtelAsgiMiddleware propagates traceparent; ast-grep enforces "
                    "bridge/<->caller isolation in <30ms."
                ),
                delta_note="Zero trace breaks across Cloud Run proxies.",
                adrs=("ADR-03", "ADR-12"),
                gap="P15-2 silent ast-grep skip if off PATH; P15-7 SQL/import blind spots.",
            ),
            JourneyStepSpec(
                node_id="db_front_door",
                edge="m_uc_db",
                plain_action=(
                    "Request executes across DocuBridge's shared async SQL pool and "
                    "dual-mode Ports."
                ),
                spec_action=(
                    "@pytest.mark.port runs identical contracts over local & GCP "
                    "adapters; ADR-11 bans cloud InMemory fallbacks."
                ),
                delta_note="57 Terraform plan assertions run offline without creds.",
                adrs=("ADR-01", "ADR-11"),
                gap="P15-8: 31 get_adapter() & 160 os.environ reads outside bridge/ports/.",
            ),
            JourneyStepSpec(
                node_id="db_vault_ledger",
                edge=None,
                plain_action=(
                    "22 instrumented pipeline spans stream directly to Cloud Trace "
                    "v2 and BigQuery in <1s."
                ),
                spec_action=(
                    "CloudTraceV2SpanExporter + BigQuery insertAll mirror spans "
                    "alongside operational Iceberg facts."
                ),
                delta_note="No waiting for slow log-router sinks.",
                adrs=("ADR-06", "ADR-12", "ADR-15"),
            ),
            JourneyStepSpec(
                node_id="dl_trace_eval",
                edge="m_dl_db",
                plain_action=(
                    "SQL Trace Evaluation runs 5 free compliance checks on 100% of "
                    "traffic, sampling ~5% for Gemini judging + Rogan-Gladen debiasing."
                ),
                spec_action=(
                    "v_trace_sql_evaluation checks PII scrub, SPIFFE auth, Decimal "
                    "audit, RSR rigor & model tiering."
                ),
                delta_note="Cuts LLM judge spend ~95% while correcting judge bias.",
                adrs=("ADR-12",),
                gap="59 lines cite external bq-eval views; gate.sh skips --cov-fail-under=90.",
            ),
        ),
    ),
)
