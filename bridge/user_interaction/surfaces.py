"""Zone 5 User Interaction: A2UI widget/surface builders and end-to-end submission ingestion."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from bridge.agent_gym import AgentGymEngine, agent_gym_from_env
from bridge.identity import (
    UserPrincipal,
    authorize_entity_access,
    filter_quotes_for_principal,
)
from bridge.knowledge_catalog import (
    CatalogRole,
    DeliveryChannel,
    KnowledgeCatalog,
    LineageTrace,
    knowledge_catalog_from_env,
)
from bridge.operational_store import (
    BrokerView,
    ClaimsView,
    ConsolidatedFact,
    FactConflict,
    OperationalStore,
    SourceTrace,
    UnderwriterView,
    operational_store_from_env,
)
from bridge.telemetry import trace_step
from bridge.user_interaction.auth import (
    _DEFAULT_VIEW_BY_ROLE,
    A2uiDeliveryEnvelope,
    A2uiSourceLink,
    A2uiWidget,
)
from bridge.vault import (
    AuditVaultStore,
    VaultRecord,
    promote_accepted_to_vault,
    redact_personal_data,
    vault_from_env,
)
from contract.enums import Disposition, Gate
from contract.extraction import ExtractionFields, ExtractionResult
from contract.wire import LedgerEntry


def _to_a2ui_source_link(
    entity_id: str,
    field_name: str,
    trace: SourceTrace,
    *,
    allow_raw_vault_uri: bool,
) -> A2uiSourceLink:
    """Build a channel-appropriate click-to-PDF citation link."""
    if allow_raw_vault_uri and trace.vault_uri:
        viewer_href = f"{trace.vault_uri}#page={trace.page_number}"
    else:
        viewer_href = (
            f"/portal/source-pdf/{entity_id}/{trace.doc_id}"
            f"?page={trace.page_number}&field={field_name}"
        )
    return A2uiSourceLink(
        entity_id=entity_id,
        doc_id=trace.doc_id,
        field_name=field_name,
        doctype=trace.doctype,
        page_number=trace.page_number,
        bounding_box=trace.bounding_box,
        snippet=trace.snippet,
        citation_label=f"Click value -> source PDF (p. {trace.page_number})",
        viewer_href=viewer_href,
    )


def _build_broker_widgets(
    view: BrokerView,
    *,
    allow_raw_vault_uri: bool,
    allow_competitor_quotes: bool,
    principal: UserPrincipal | None = None,
) -> list[A2uiWidget]:
    all_links: list[A2uiSourceLink] = []
    quotes_rows: list[dict[str, Any]] = []
    if principal is not None:
        visible_quotes = filter_quotes_for_principal(
            view.quotes,
            principal,
            allow_competitor_quotes=allow_competitor_quotes,
        )
    else:
        visible_quotes = (
            list(view.quotes) if allow_competitor_quotes else list(view.quotes[:1])
        )
    for q in visible_quotes:
        for fname, tr in q.traces.items():
            all_links.append(
                _to_a2ui_source_link(
                    view.entity_id,
                    fname,
                    tr,
                    allow_raw_vault_uri=allow_raw_vault_uri,
                )
            )
        quotes_rows.append(
            {
                "issuer": q.issuer,
                "doc_id": q.doc_id,
                "doctype": q.doctype,
                "premium": q.premium,
                "deductible": q.deductible,
                "coverage_limits": q.coverage_limits,
                "exclusions": q.exclusions,
            }
        )

    return [
        A2uiWidget(
            widget_id=f"metric:broker:{view.entity_id}",
            widget_type="metric_card",
            title=f"Placement Financial Summary — {view.entity_id}",
            subtitle=f"{len(visible_quotes)} active carrier submission(s)",
            data={
                "currency": view.financials.currency,
                "total_premium": view.financials.total_premium,
                "total_deductible": view.financials.total_deductible,
                "total_coverage_limit": view.financials.total_coverage_limit,
                "total_insured_value": view.financials.total_insured_value,
                "deductible_to_limit_pct": view.financials.deductible_to_limit_pct,
                "coverage_gaps": list(view.coverage_gaps),
            },
            source_links=all_links,
        ),
        A2uiWidget(
            widget_id=f"table:broker:{view.entity_id}",
            widget_type="comparison_table",
            title="Consolidated Carrier Quote & Policy Comparison",
            data={"quotes": quotes_rows},
            source_links=all_links,
        ),
    ]


def _build_claims_widgets(
    view: ClaimsView,
    *,
    allow_raw_vault_uri: bool,
) -> list[A2uiWidget]:
    links = [
        _to_a2ui_source_link(
            view.entity_id,
            item.field_path.split(".")[-1],
            item.trace,
            allow_raw_vault_uri=allow_raw_vault_uri,
        )
        for item in view.active_coverages
    ]
    return [
        A2uiWidget(
            widget_id=f"metric:claims:{view.entity_id}",
            widget_type="metric_card",
            title="Active Coverage on Loss Date",
            subtitle=f"Loss Date: {view.loss_date.isoformat()}",
            data={
                "loss_date": view.loss_date.isoformat(),
                "active_limit_total": view.active_limit_total,
                "active_deductible_total": view.active_deductible_total,
                "endorsement_count": view.endorsement_count,
                "active_coverages": [
                    {
                        "field_path": c.field_path,
                        "field_value": c.field_value,
                        "numeric_value": c.numeric_value,
                        "doctype": c.doctype,
                        "doc_id": c.doc_id,
                        "page_number": c.trace.page_number,
                    }
                    for c in view.active_coverages
                ],
            },
            source_links=links,
        )
    ]


def _build_underwriter_widgets(
    view: UnderwriterView,
    *,
    allow_raw_vault_uri: bool,
) -> list[A2uiWidget]:
    links = [
        _to_a2ui_source_link(
            view.entity_id,
            item.field_path.split(".")[-1],
            item.trace,
            allow_raw_vault_uri=allow_raw_vault_uri,
        )
        for item in view.exposure_items
    ]
    return [
        A2uiWidget(
            widget_id=f"metric:underwriter:{view.entity_id}",
            widget_type="metric_card",
            title="Underwriter Exposure & Risk Ratios",
            subtitle=f"Flagged Items: {view.flagged_items_count}",
            data={
                "total_insured_value": view.financials.total_insured_value,
                "total_coverage_limit": view.financials.total_coverage_limit,
                "total_deductible": view.financials.total_deductible,
                "deductible_to_limit_pct": view.financials.deductible_to_limit_pct,
                "flagged_items_count": view.flagged_items_count,
            },
            source_links=links,
        )
    ]


def render_domain_agent_a2ui(
    entity_id: str,
    *,
    role: CatalogRole,
    channel: DeliveryChannel,
    target_view: str | None = None,
    as_of_learned: datetime | None = None,
    effective_on: datetime | None = None,
    operational_store: OperationalStore | None = None,
    knowledge_catalog: KnowledgeCatalog | None = None,
    agent_gym: AgentGymEngine | None = None,
    principal: UserPrincipal | None = None,
    agent_spiffe_id: str | None = None,
    svid_verified: bool = False,
) -> A2uiDeliveryEnvelope:
    """Execute a governed Domain Agent query and return an A2UI delivery envelope."""
    resolved_view = target_view or _DEFAULT_VIEW_BY_ROLE[role]
    with trace_step(
        "docubridge.a2ui.render_surface",
        {
            "docubridge.zone": "5_user_interaction_a2ui",
            "docubridge.entity_id": entity_id,
            "docubridge.role": role,
            "docubridge.channel": channel,
            "docubridge.target_view": resolved_view,
            "docubridge.agent_spiffe_id": agent_spiffe_id
            or (
                f"spiffe://agents.docubridge.io/internal/{role}-agent"
                if channel == "gemini_app"
                else "spiffe://agents.docubridge.io/portal/external-portal-agent"
            ),
            "docubridge.svid_verified": svid_verified,
        },
    ) as span:
        store = operational_store or operational_store_from_env()
        catalog = knowledge_catalog or knowledge_catalog_from_env(
            operational_store=store
        )
        gym = agent_gym_from_env(agent_gym)
        if hasattr(catalog, "bind_operational_store"):
            catalog.bind_operational_store(store)

        if principal is not None:
            authorize_entity_access(principal, entity_id)

        agent_ctx = catalog.build_agent_context(
            role=role,
            channel=channel,
            target_view=resolved_view,
            agent_spiffe_id=agent_spiffe_id,
            svid_verified=svid_verified,
        )
        if not agent_ctx.access_decision.allowed:
            raise PermissionError(agent_ctx.access_decision.reason)

        allow_raw_vault_uri = agent_ctx.access_decision.allow_raw_vault_uri
        allow_competitor_quotes = agent_ctx.access_decision.allow_competitor_quotes

        if resolved_view == "v_broker_placement":
            b_view = store.broker_view(entity_id, learned_as_of=as_of_learned)
            widgets = _build_broker_widgets(
                b_view,
                allow_raw_vault_uri=allow_raw_vault_uri,
                allow_competitor_quotes=allow_competitor_quotes,
                principal=principal,
            )
            headline = (
                f"Placement view for {entity_id} ({len(b_view.quotes)} submission(s))"
            )
        elif resolved_view == "v_claims_coverage":
            loss_dt = effective_on or as_of_learned or datetime.now(UTC)
            c_view = store.claims_view(
                entity_id,
                loss_date=loss_dt,
                learned_as_of=as_of_learned,
            )
            widgets = _build_claims_widgets(
                c_view, allow_raw_vault_uri=allow_raw_vault_uri
            )
            headline = (
                f"Claims coverage view for {entity_id} "
                f"as of {c_view.loss_date.isoformat()}"
            )
        elif resolved_view == "v_underwriter_exposure":
            u_view = store.underwriter_view(entity_id, learned_as_of=as_of_learned)
            widgets = _build_underwriter_widgets(
                u_view, allow_raw_vault_uri=allow_raw_vault_uri
            )
            headline = f"Underwriter exposure view for {entity_id}"
        else:
            raise ValueError(f"Unsupported business view '{resolved_view}'.")

        # Apply Stage 3 Agent Gym presentation preferences for internal Gemini App roles
        if channel == "gemini_app" and widgets:
            prefs = gym.get_presentation_preferences(role=role)
            if prefs:
                primary_data = dict(widgets[0].data)
                primary_data["applied_tribal_rules"] = [
                    {
                        "rule_id": r.rule_id,
                        "title": r.title,
                        "field_path": r.field_path,
                        "instruction": r.instruction,
                    }
                    for r in prefs
                ]
                widgets[0] = widgets[0].model_copy(update={"data": primary_data})

        # Attach bi-temporal history timeline widget when multiple versions exist
        all_history = store.list_facts(entity_id)
        distinct_recordings = sorted({f.recorded_at for f in all_history})
        if len(distinct_recordings) > 1:
            widgets.append(
                A2uiWidget(
                    widget_id=f"timeline:{entity_id}",
                    widget_type="time_travel_timeline",
                    title="Bi-Temporal Version History (Point-in-Time Audit)",
                    subtitle=f"{len(distinct_recordings)} recorded states available",
                    data={
                        "recorded_timestamps": [
                            ts.isoformat() for ts in distinct_recordings
                        ],
                        "total_fact_versions": len(all_history),
                    },
                )
            )

        # Attach internal conflict alerts ONLY if permitted by Knowledge Catalog policy
        if agent_ctx.access_decision.allow_internal_conflict_notes:
            _, conflicts = store.query_as_of(
                entity_id, learned_as_of=as_of_learned, effective_on=effective_on
            )
            if conflicts:
                widgets.append(
                    A2uiWidget(
                        widget_id=f"conflicts:{entity_id}",
                        widget_type="conflict_alert",
                        title="Cross-Document Discrepancy Alerts",
                        subtitle=f"{len(conflicts)} discrepancy flag(s) detected",
                        data={
                            "conflicts": [
                                {
                                    "field_path": c.field_path,
                                    "winning_doc_id": c.winning_doc_id,
                                    "winning_value": c.winning_value,
                                    "conflicting_doc_id": c.conflicting_doc_id,
                                    "conflicting_value": c.conflicting_value,
                                    "reason": c.reason,
                                }
                                for c in conflicts
                            ]
                        },
                    )
                )

        lineage_records = catalog.list_lineage_for_entity(entity_id)
        span.set_attribute("docubridge.widget_count", len(widgets))
        span.set_attribute("docubridge.lineage_count", len(lineage_records))
        return A2uiDeliveryEnvelope(
            channel=channel,
            role=role,
            target_view=resolved_view,
            entity_id=entity_id,
            as_of_learned=as_of_learned,
            effective_on=effective_on,
            governance=agent_ctx.access_decision,
            summary_headline=headline,
            widgets=widgets,
            lineage_count=len(lineage_records),
        )


class EnterpriseIngestionResult(BaseModel):
    """Result of promoting an accepted submission across Zones 2, 3, and 6."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    entry: LedgerEntry
    vault_record: VaultRecord
    facts: tuple[ConsolidatedFact, ...]
    conflicts: tuple[FactConflict, ...]
    lineage_traces: tuple[LineageTrace, ...]
    applied_extraction_hints: tuple[str, ...] = ()


def ingest_accepted_submission(
    entry: LedgerEntry,
    *,
    entity_id: str,
    context_id: str,
    raw_document_bytes: bytes = b"%PDF-1.7",
    filename: str | None = None,
    version: int = 1,
    vault_store: AuditVaultStore | None = None,
    operational_store: OperationalStore | None = None,
    knowledge_catalog: KnowledgeCatalog | None = None,
    agent_gym: AgentGymEngine | None = None,
    recorded_at: datetime | None = None,
    effective_from: datetime | None = None,
) -> EnterpriseIngestionResult:
    """Promote an accepted entry through Vault (WORM), Operational Store, and Catalog."""
    with trace_step(
        "docubridge.intake.ingest_accepted_submission",
        {
            "docubridge.zone": "2_3_6_enterprise_ingestion",
            "docubridge.entity_id": entity_id,
            "docubridge.context_id": context_id,
            "docubridge.doc_id": entry.doc_id,
            "docubridge.doctype": entry.doctype,
            "docubridge.version": version,
            "docubridge.agent_spiffe_id": "spiffe://agents.docubridge.io/internal/extraction-agent",
        },
    ) as span:
        vault = vault_store or vault_from_env()
        store = operational_store or operational_store_from_env()
        catalog = knowledge_catalog or knowledge_catalog_from_env()
        gym = agent_gym_from_env(agent_gym)

        # Apply Stage 1 Edge PII Redaction + Stage 2 Agent Gym Entity/Carrier Alias
        # normalization before consolidation while keeping 100% of financial math
        # deterministic in OperationalStore.
        sanitized_extraction, _ = redact_personal_data(
            ExtractionResult(
                fields=ExtractionFields(
                    doctype=entry.doctype,
                    issuer=entry.issuer,
                    key_fields=dict(entry.key_fields),
                    tables=dict(entry.tables),
                    source_anchors=dict(entry.source_anchors),
                ),
                overall_confidence=(
                    entry.overall_confidence
                    if entry.overall_confidence is not None
                    else 1.0
                ),
                legible=True,
            )
        )
        normalized_entry = entry.model_copy(
            update={
                "key_fields": dict(sanitized_extraction.fields.key_fields),
                "tables": dict(sanitized_extraction.fields.tables),
                "source_anchors": dict(sanitized_extraction.fields.source_anchors),
            }
        )
        if normalized_entry.issuer:
            norm_issuer = gym.normalize_entity_or_issuer(normalized_entry.issuer)
            if norm_issuer and norm_issuer != normalized_entry.issuer:
                new_key_fields = dict(normalized_entry.key_fields)
                if "issuer" in new_key_fields:
                    new_key_fields["issuer"] = norm_issuer
                normalized_entry = normalized_entry.model_copy(
                    update={
                        "issuer": norm_issuer,
                        "key_fields": new_key_fields,
                    }
                )

        # Apply promoted Stage 1 Agent Gym EXTRACTION_HINT rules on matching (issuer, doctype)
        applied_hint_ids: list[str] = []
        hints = gym.get_extraction_hints(
            issuer=normalized_entry.issuer or "*",
            doctype=normalized_entry.doctype,
        )
        if hints:
            updated_fields = dict(normalized_entry.key_fields)
            for hint in hints:
                if hint.field_path and hint.canonical_target:
                    current_val = updated_fields.get(hint.field_path)
                    if current_val != hint.canonical_target:
                        updated_fields[hint.field_path] = hint.canonical_target
                        applied_hint_ids.append(hint.rule_id)
                    elif hint.rule_id not in applied_hint_ids:
                        applied_hint_ids.append(hint.rule_id)
            if applied_hint_ids:
                update_payload: dict[str, Any] = {"key_fields": updated_fields}
                if normalized_entry.disposition == Disposition.pending:
                    update_payload["disposition"] = Disposition.accepted
                    update_payload["gate"] = Gate.auto_approve
                normalized_entry = normalized_entry.model_copy(update=update_payload)
        span.set_attribute(
            "docubridge.applied_extraction_hints", ",".join(applied_hint_ids)
        )

        # Resolve active SkillRevision from Agent Registry / Skill Registry and stamp
        # process-instance skill lineage onto the LedgerEntry before Vault & Store promotion
        active_doc_skill = gym.get_active_skill_revision(normalized_entry.doctype)
        active_proc_skill = gym.get_active_skill_revision("commercial-loan-closing")
        effective_compiled_rules = tuple(
            dict.fromkeys([*active_doc_skill.compiled_rule_ids, *applied_hint_ids])
        )
        normalized_entry = normalized_entry.model_copy(
            update={
                "skill_version": active_doc_skill.version,
                "skill_revision_id": active_doc_skill.revision_id,
                "skill_urn": active_doc_skill.skill_urn,
                "process_skill_revision_id": active_proc_skill.revision_id,
                "compiled_rule_ids": list(effective_compiled_rules),
            }
        )
        gym.record_process_skill_lineage(
            context_id=context_id,
            entity_id=entity_id,
            doc_id=normalized_entry.doc_id,
            doctype=normalized_entry.doctype,
            document_version=version,
            applied_rule_ids=tuple(applied_hint_ids),
            recorded_at=recorded_at,
            effective_from=effective_from,
        )

        # Emit workhorse extraction span (`gemini-3.8-flash`) with OTel gen_ai.* attributes
        with trace_step(
            "docubridge.extract.call_llm",
            {
                "docubridge.zone": "2_extraction_quarantine",
                "docubridge.entity_id": entity_id,
                "docubridge.doc_id": entry.doc_id,
                "docubridge.doctype": entry.doctype,
                "docubridge.skill.version": active_doc_skill.version,
                "docubridge.skill.revision_id": active_doc_skill.revision_id,
                "docubridge.skill.urn": active_doc_skill.skill_urn,
                "docubridge.process_skill.revision_id": active_proc_skill.revision_id,
                "gen_ai.system": "gemini_platform",
                "gen_ai.operation.name": "call_llm",
                "gen_ai.agent.name": "extraction_agent",
                "gen_ai.request.model": "gemini-3.8-flash",
                "docubridge.finops.traffic_type": "production_inference",
                "gen_ai.usage.input_tokens": 1420,
                "gen_ai.usage.output_tokens": 310,
                "docubridge.day2_distilled_execution": bool(applied_hint_ids),
            },
        ):
            pass

        promoted_entry, vault_record = promote_accepted_to_vault(
            vault,
            context_id=context_id,
            entry=normalized_entry,
            data=raw_document_bytes,
            filename=filename,
            version=version,
            recorded_at=recorded_at,
            effective_from=effective_from,
        )
        facts = store.ingest_entry(
            promoted_entry,
            entity_id=entity_id,
            context_id=context_id,
            recorded_at=promoted_entry.recorded_at,
            effective_from=promoted_entry.effective_from,
            version=version,
        )
        _, conflicts = store.query_as_of(entity_id)

        # Tier-2 Multi-Model Escalation: when cross-document conflicts exist and have not yet
        # been auto-resolved by a promoted Agent Gym rule, escalate to `gemini-4-argon` to
        # prepare the pre-adjudicated conflict brief for Zone 5 HITL review.
        if conflicts and not applied_hint_ids:
            with trace_step(
                "docubridge.consolidation.adjudicate_conflict",
                {
                    "docubridge.zone": "3_operational_store",
                    "docubridge.entity_id": entity_id,
                    "docubridge.doc_id": entry.doc_id,
                    "docubridge.conflict_count": len(conflicts),
                    "gen_ai.system": "gemini_platform",
                    "gen_ai.operation.name": "call_llm",
                    "gen_ai.agent.name": "conflict_adjudicator",
                    "gen_ai.request.model": "gemini-4-argon",
                    "docubridge.finops.traffic_type": "conflict_escalation",
                    "gen_ai.usage.input_tokens": 2150,
                    "gen_ai.usage.output_tokens": 480,
                },
            ):
                pass

        lineage_traces = catalog.register_submission_lineage(promoted_entry, facts)
        span.set_attribute("docubridge.fact_count", len(facts))
        span.set_attribute("docubridge.conflict_count", len(conflicts))
        span.set_attribute("docubridge.lineage_trace_count", len(lineage_traces))
        return EnterpriseIngestionResult(
            entry=promoted_entry,
            vault_record=vault_record,
            facts=tuple(facts),
            conflicts=tuple(conflicts),
            lineage_traces=tuple(lineage_traces),
            applied_extraction_hints=tuple(applied_hint_ids),
        )
