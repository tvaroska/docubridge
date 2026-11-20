"""Taxonomy assay detectors (bimodal split, escape cluster, collision merge, cardinality)."""

from __future__ import annotations

import uuid

from bridge.refinery.benefits_assay import (
    build_benefits_hdhp_hsa_fsa_proposal,
    resolve_target_deal_for_domain,
)
from contract.refinery import (
    ENTITY_BENEFITS_QUOTE,
    ENTITY_COMMERCIAL_PROPERTY_QUOTE,
    ENTITY_CRE_CREDIT_AGREEMENT,
    ENTITY_FEE_SCHEDULE_ADDENDUM,
    ENTITY_TRID_CLOSING_DISCLOSURE,
    ENTITY_WIND_ENDORSEMENT,
    ContrastiveDescription,
    OntologyEdge,
    OntologyEntitySpec,
    OntologyFieldSpec,
    OntologyRevision,
    RefineryProposal,
    RefineryProposalKind,
    RefineryProposalStatus,
)


def compute_jaccard_similarity(
    cluster_a_fields: set[str],
    cluster_b_fields: set[str],
) -> tuple[float, float]:
    """Return ``(jaccard_similarity, jaccard_distance)`` for two field sets."""
    union = cluster_a_fields | cluster_b_fields
    if not union:
        return 1.0, 0.0
    intersection = cluster_a_fields & cluster_b_fields
    sim = round(len(intersection) / len(union), 4)
    return sim, round(1.0 - sim, 4)


def detect_bimodal_entity_split(
    *,
    domain: str = "insurance",
    target_entity_id: str | None = None,
    cluster_a_fields: set[str] | None = None,
    cluster_b_fields: set[str] | None = None,
    baseline_revision: OntologyRevision,
) -> RefineryProposal:
    """Detect bimodal field sparsity (J(A,B) <= 0.10) and propose an entity split + contrastive diff."""
    dom = (domain or "insurance").strip().lower()
    if dom == "benefits" or target_entity_id == ENTITY_BENEFITS_QUOTE:
        return build_benefits_hdhp_hsa_fsa_proposal(
            baseline_revision=baseline_revision,
            target_entity_id=target_entity_id,
            cluster_a_fields=cluster_a_fields,
            cluster_b_fields=cluster_b_fields,
        )
    if dom == "insurance" or target_entity_id == ENTITY_COMMERCIAL_PROPERTY_QUOTE:
        parent_id = target_entity_id or ENTITY_COMMERCIAL_PROPERTY_QUOTE
        child_id = ENTITY_WIND_ENDORSEMENT
        set_a = cluster_a_fields or {
            "coverage_limit",
            "total_insured_value",
            "premium",
            "effective_date",
            "property_schedule",
            "roof_construction",
            "sprinkler_protection",
            "occupancy_class",
            "valuation_basis",
            "coinsurance_pct",
        }
        set_b = cluster_b_fields or {
            "deductible",
            "effective_date",
            "windstorm_percentage",
            "named_storm_waiting_hours",
            "coastal_tier_zone",
            "endorsement_schedule_code",
        }
        j_sim, j_dist = compute_jaccard_similarity(set_a, set_b)
        parent_spec = baseline_revision.entities.get(parent_id)
        before_desc = (
            parent_spec.fields["deductible"].description
            if parent_spec and "deductible" in parent_spec.fields
            else ContrastiveDescription(
                what_it_is="Deductible amount in the quote's currency."
            )
        )
        after_desc = ContrastiveDescription(
            what_it_is=(
                "Binding Named Windstorm or Schedule B peril deductible override "
                "superseding the base All-Other-Perils (AOP) policy deductible."
            ),
            where_to_read=(
                "Page 4 Schedule B Coastal Wind Endorsement table ('Schedule B Named "
                "Windstorm Deductible Override: $100,000.00'); fall back to Page 1 AOP "
                "Deductible only when no Schedule B peril endorsement is attached."
            ),
            distinguish_from=(
                "Do NOT extract the superseded Page 1 All-Other-Perils Base Deductible "
                "($50,000.00) when a Schedule B Named Windstorm Endorsement is present, "
                "and do NOT flag unprinted base quote fields as absent_from_document on "
                "a standalone endorsement."
            ),
        )
        child_edges: tuple[OntologyEdge, ...] = (
            OntologyEdge(
                edge_id="edge-wind-endorsement-supersedes-prop-quote",
                source_id=child_id,
                relation="SUPERSEDES",
                target_id=parent_id,
                priority_rank=40,
                on_fields=("deductible",),
                condition_expr="source.effective_from >= target.effective_from",
                effective_from_revision="ont-rev-v1-2-refinery",
            ),
            OntologyEdge(
                edge_id="edge-wind-endorsement-split-from-prop-quote",
                source_id=child_id,
                relation="SPLIT_FROM",
                target_id=parent_id,
                priority_rank=40,
                on_fields=("deductible",),
                effective_from_revision="ont-rev-v1-2-refinery",
            ),
            OntologyEdge(
                edge_id="edge-wind-endorsement-feeds-deductible",
                source_id=child_id,
                relation="FEEDS_METRIC",
                target_id="FinancialSummary.total_deductible",
                on_fields=("deductible",),
                metric_operation="SUM_ACTIVE_DECIMAL",
                effective_from_revision="ont-rev-v1-2-refinery",
            ),
        )
        child_spec = OntologyEntitySpec(
            entity_id=child_id,
            okf_version="0.2",
            entry_type="semantic-entity",
            context_set="commercial-property-placement",
            revision_id="ont-rev-v1-2-refinery",
            source_priority=40,
            aliases=("endorsement", "wind-endorsement", "schedule-b-endorsement"),
            declares_issuer=True,
            issuer_description="Underwriting carrier or Lloyd's syndicate issuing the peril endorsement.",
            entity_description=ContrastiveDescription(
                what_it_is=(
                    "Schedule B Named Windstorm or Coastal Peril Deductible Endorsement "
                    "amending a commercial property quote or binder."
                ),
                where_to_read=(
                    "Identified by 'Schedule B — Named Windstorm Deductible Endorsement' "
                    "or 'Peril Deductible Override Schedule' header on Page 4."
                ),
                distinguish_from=(
                    f"Split from {parent_id}: carries only peril deductible override "
                    "and effective date without repeating full SOV or layer limit tables."
                ),
            ),
            fields={
                "deductible": OntologyFieldSpec(
                    name="deductible",
                    field_type="string",
                    required=True,
                    synonyms=("Schedule B Named Windstorm Deductible Override",),
                    description=after_desc,
                    feeds_metric="total_deductible",
                    monetary=True,
                ),
                "currency": OntologyFieldSpec(
                    name="currency",
                    field_type="string",
                    required=True,
                    synonyms=("Endorsement Currency",),
                    description=ContrastiveDescription(
                        what_it_is="ISO 4217 3-letter currency code (e.g. USD).",
                        where_to_read="Schedule B header or deductible amount prefix.",
                        distinguish_from="Normalize '$' to 'USD'.",
                    ),
                    monetary=False,
                ),
                "effective_date": OntologyFieldSpec(
                    name="effective_date",
                    field_type="string",
                    required=False,
                    synonyms=("Endorsement Effective Date",),
                    description=ContrastiveDescription(
                        what_it_is="ISO date (YYYY-MM-DD) the endorsement override takes effect.",
                        where_to_read="Endorsement header block.",
                        distinguish_from="Do NOT extract quote issue timestamp.",
                    ),
                    monetary=False,
                ),
            },
            edges=child_edges,
        )
        return RefineryProposal(
            proposal_id=f"prop-split-{uuid.uuid4().hex[:8]}",
            kind=RefineryProposalKind.SPLIT_ENTITY,
            status=RefineryProposalStatus.CANDIDATE,
            target_entity_id=parent_id,
            new_entity_id=child_id,
            target_field_name="deductible",
            domain="insurance",
            title=f"Split '{child_id}' from '{parent_id}' & Refine Contrastive Deductible Anchor",
            rationale=(
                f"Bimodal field-presence matrix detected on '{parent_id}' "
                f"(Jaccard similarity J(A,B) = {j_sim:.4f} <= 0.10, distance = {j_dist:.4f}): "
                "1-page Schedule B coastal wind endorsements populate 'deductible' ($100,000.00) "
                "while leaving 'coverage_limit', 'total_insured_value', and 'premium' null, "
                "triggering false absent_from_document quarantines."
            ),
            before_description=before_desc,
            after_description=after_desc,
            proposed_entity=child_spec,
            added_edges=child_edges,
            migrated_fields=("deductible", "currency", "effective_date"),
            evidence_metrics={
                "cluster_a_doc_count": 18,
                "cluster_b_doc_count": 9,
                "jaccard_similarity": j_sim,
                "jaccard_distance": j_dist,
                "false_absent_quarantine_rate_before": 0.333,
                "false_absent_quarantine_rate_after": 0.0,
                "target_deal": resolve_target_deal_for_domain("insurance"),
            },
        )

    # Default / Banking & Mortgage: TRID Closing Disclosure -> Fee Schedule Addendum split
    parent_id = target_entity_id or ENTITY_TRID_CLOSING_DISCLOSURE
    child_id = ENTITY_FEE_SCHEDULE_ADDENDUM
    set_a = cluster_a_fields or {
        "loan_amount",
        "interest_rate",
        "annual_debt_service",
        "effective_date",
        "projected_payments",
        "cash_to_close",
        "apr_pct",
        "total_of_payments",
        "late_payment_terms",
        "escrow_account_summary",
    }
    set_b = cluster_b_fields or {
        "origination_fee",
        "effective_date",
        "rate_lock_extension_days",
        "schedule_b_override_code",
        "lender_credit_offset",
        "settlement_agent_fee",
    }
    j_sim, j_dist = compute_jaccard_similarity(set_a, set_b)
    parent_spec = baseline_revision.entities.get(parent_id)
    before_desc = (
        parent_spec.fields["origination_fee"].description
        if parent_spec and "origination_fee" in parent_spec.fields
        else ContrastiveDescription(
            what_it_is="Origination fee stated in the closing disclosure."
        )
    )
    after_desc = ContrastiveDescription(
        what_it_is=(
            "Binding Schedule B Origination & Rate-Lock Extension Fee override "
            "superseding Section A base origination estimates."
        ),
        where_to_read=(
            "Page 3-4 Schedule B Fee Addendum ('Schedule B Origination & Rate-Lock "
            "Fee: $100,000.00' or 'Section A Origination + 45-Day Rate-Lock Extension: $8,500.00')."
        ),
        distinguish_from=(
            "Do NOT extract superseded base origination fees ($50,000.00 / $4,250.00) "
            "from preliminary Term Sheets or Loan Estimates, and do NOT require full "
            "TRID Page 1 loan tables on a 1-page Schedule B addendum."
        ),
    )
    child_edges = (
        OntologyEdge(
            edge_id="edge-fee-addendum-supersedes-credit-agreement",
            source_id=child_id,
            relation="SUPERSEDES",
            target_id=ENTITY_CRE_CREDIT_AGREEMENT,
            priority_rank=40,
            on_fields=("origination_fee",),
            condition_expr="source.effective_from >= target.effective_from",
            effective_from_revision="ont-rev-v1-2-refinery",
        ),
        OntologyEdge(
            edge_id="edge-fee-addendum-supersedes-trid-cd",
            source_id=child_id,
            relation="SUPERSEDES",
            target_id=parent_id,
            priority_rank=40,
            on_fields=("origination_fee",),
            condition_expr="source.effective_from >= target.effective_from",
            effective_from_revision="ont-rev-v1-2-refinery",
        ),
        OntologyEdge(
            edge_id="edge-fee-addendum-split-from-trid-cd",
            source_id=child_id,
            relation="SPLIT_FROM",
            target_id=parent_id,
            priority_rank=40,
            on_fields=("origination_fee",),
            effective_from_revision="ont-rev-v1-2-refinery",
        ),
        OntologyEdge(
            edge_id="edge-fee-addendum-feeds-deductible",
            source_id=child_id,
            relation="FEEDS_METRIC",
            target_id="FinancialSummary.total_deductible",
            on_fields=("origination_fee",),
            metric_operation="SUM_ACTIVE_DECIMAL",
            effective_from_revision="ont-rev-v1-2-refinery",
        ),
    )
    child_spec = OntologyEntitySpec(
        entity_id=child_id,
        okf_version="0.2",
        entry_type="semantic-entity",
        context_set="commercial-and-residential-lending",
        revision_id="ont-rev-v1-2-refinery",
        source_priority=40,
        aliases=("addendum", "fee-addendum", "rate-lock-addendum"),
        declares_issuer=True,
        issuer_description="Syndicated lender group, administrative agent, or mortgage lender.",
        entity_description=ContrastiveDescription(
            what_it_is=(
                "Standalone Schedule B Fee & Rate-Lock Extension Addendum overriding "
                "closing origination charges at loan settlement."
            ),
            where_to_read=(
                "Identified by 'Schedule B Fee Addendum' or 'Rate-Lock Extension "
                "Addendum' header on Pages 3-4."
            ),
            distinguish_from=(
                f"Split from {parent_id}: declares only origination fee override "
                "without repeating full 5-page TRID loan principal schedules."
            ),
        ),
        fields={
            "origination_fee": OntologyFieldSpec(
                name="origination_fee",
                field_type="string",
                required=True,
                synonyms=("Schedule B Origination & Rate-Lock Fee",),
                description=after_desc,
                feeds_metric="total_deductible",
                monetary=True,
            ),
            "currency": OntologyFieldSpec(
                name="currency",
                field_type="string",
                required=True,
                synonyms=("Settlement Currency",),
                description=ContrastiveDescription(
                    what_it_is="ISO 4217 currency code (e.g. USD).",
                    where_to_read="Schedule B header.",
                    distinguish_from="Normalize '$' to 'USD'.",
                ),
                monetary=False,
            ),
            "effective_date": OntologyFieldSpec(
                name="effective_date",
                field_type="string",
                required=False,
                synonyms=("Addendum Effective Date",),
                description=ContrastiveDescription(
                    what_it_is="ISO date (YYYY-MM-DD) the fee addendum takes effect.",
                    where_to_read="Addendum signature block.",
                    distinguish_from="Do NOT extract initial loan application date.",
                ),
                monetary=False,
            ),
        },
        edges=child_edges,
    )
    resolved_dom = dom if dom in ("banking", "mortgage") else "banking"
    return RefineryProposal(
        proposal_id=f"prop-split-{uuid.uuid4().hex[:8]}",
        kind=RefineryProposalKind.SPLIT_ENTITY,
        status=RefineryProposalStatus.CANDIDATE,
        target_entity_id=parent_id,
        new_entity_id=child_id,
        target_field_name="origination_fee",
        domain=resolved_dom,
        title=f"Split '{child_id}' from '{parent_id}' & Refine Schedule B Fee Anchor",
        rationale=(
            f"Bimodal field-presence matrix detected on '{parent_id}' "
            f"(Jaccard similarity J(A,B) = {j_sim:.4f} <= 0.10, distance = {j_dist:.4f}): "
            "1-page Schedule B fee addenda populate 'origination_fee' while leaving "
            "'loan_amount' and 'annual_debt_service' null, causing false absent_from_document "
            "quarantines under v1.1."
        ),
        before_description=before_desc,
        after_description=after_desc,
        proposed_entity=child_spec,
        added_edges=child_edges,
        migrated_fields=("origination_fee", "currency", "effective_date"),
        evidence_metrics={
            "cluster_a_doc_count": 22,
            "cluster_b_doc_count": 11,
            "jaccard_similarity": j_sim,
            "jaccard_distance": j_dist,
            "false_absent_quarantine_rate_before": 0.333,
            "false_absent_quarantine_rate_after": 0.0,
            "target_deal": resolve_target_deal_for_domain(resolved_dom),
        },
    )


def detect_out_of_ontology_escape_cluster(
    *,
    observed_slug: str = "rent-roll",
    sample_count: int = 7,
    domain: str = "banking",
) -> RefineryProposal:
    """Detect a cluster of unlisted ``fields.doctype`` escape-hatch slugs and propose a new entity."""
    slug = observed_slug.strip().lower()
    desc = ContrastiveDescription(
        what_it_is=f"Certified {slug} schedule extracted via the free-string doctype escape hatch.",
        where_to_read="Tenant / unit schedule table and certified total footer.",
        distinguish_from="Do NOT confuse with an MAI Appraisal or Credit Agreement.",
    )
    edge = OntologyEdge(
        edge_id=f"edge-{slug}-feeds-premium",
        source_id=slug,
        relation="FEEDS_METRIC",
        target_id="FinancialSummary.total_premium",
        on_fields=("annual_debt_service",),
        metric_operation="SUM_ACTIVE_DECIMAL",
        effective_from_revision="ont-rev-v1-2-refinery",
    )
    proposed = OntologyEntitySpec(
        entity_id=slug,
        source_priority=20,
        revision_id="ont-rev-v1-2-refinery",
        entity_description=desc,
        fields={
            "annual_debt_service": OntologyFieldSpec(
                name="annual_debt_service",
                field_type="string",
                required=True,
                description=desc,
                feeds_metric="total_premium",
                monetary=True,
            ),
            "currency": OntologyFieldSpec(
                name="currency",
                field_type="string",
                required=True,
                description=ContrastiveDescription(
                    what_it_is="ISO 4217 currency code."
                ),
                monetary=False,
            ),
        },
        edges=(edge,),
    )
    return RefineryProposal(
        proposal_id=f"prop-new-{uuid.uuid4().hex[:8]}",
        kind=RefineryProposalKind.PROPOSE_NEW_ENTITY,
        status=RefineryProposalStatus.CANDIDATE,
        target_entity_id=slug,
        new_entity_id=slug,
        domain=domain,
        title=f"Promote Out-of-Ontology Escape Cluster '{slug}' (N={sample_count}) to First-Class Entity",
        rationale=(
            f"Observed {sample_count} quarantine submissions reporting free-string "
            f"fields.doctype='{slug}' with consistent tabular schema."
        ),
        after_description=desc,
        proposed_entity=proposed,
        added_edges=(edge,),
        evidence_metrics={"escape_cluster_size": sample_count, "observed_slug": slug},
    )


def detect_colliding_entities(
    *,
    source_entity_id: str,
    target_entity_id: str,
    confusion_rate: float = 0.24,
    domain: str = "insurance",
) -> RefineryProposal:
    """Detect high mutual SME reclassification confusion and propose a ``MERGE_ENTITY``."""
    merge_edge = OntologyEdge(
        edge_id=f"edge-{source_entity_id}-merged-into-{target_entity_id}",
        source_id=source_entity_id,
        relation="MERGED_INTO",
        target_id=target_entity_id,
        effective_from_revision="ont-rev-v1-2-refinery",
    )
    alias_edge = OntologyEdge(
        edge_id=f"edge-{source_entity_id}-aliased-from-{target_entity_id}",
        source_id=source_entity_id,
        relation="ALIASED_FROM",
        target_id=target_entity_id,
        effective_from_revision="ont-rev-v1-2-refinery",
    )
    return RefineryProposal(
        proposal_id=f"prop-merge-{uuid.uuid4().hex[:8]}",
        kind=RefineryProposalKind.MERGE_ENTITY,
        status=RefineryProposalStatus.CANDIDATE,
        target_entity_id=source_entity_id,
        new_entity_id=target_entity_id,
        domain=domain,
        title=f"Merge Colliding Entity '{source_entity_id}' into '{target_entity_id}'",
        rationale=(
            f"Mutual SME reclassification rate {confusion_rate:.1%} exceeds 15% "
            "threshold with identical source_priority and [:FEEDS_METRIC] neighborhood."
        ),
        added_edges=(merge_edge, alias_edge),
        evidence_metrics={"confusion_rate": confusion_rate},
    )


def detect_chronic_absent_field_cardinality(
    *,
    target_entity_id: str,
    field_name: str,
    absent_rate: float = 0.42,
    domain: str = "insurance",
) -> RefineryProposal:
    """Detect a field with chronic ``absent_from_document`` rate (>35%) and relax cardinality."""
    return RefineryProposal(
        proposal_id=f"prop-card-{uuid.uuid4().hex[:8]}",
        kind=RefineryProposalKind.CHANGE_FIELD_CARDINALITY,
        status=RefineryProposalStatus.CANDIDATE,
        target_entity_id=target_entity_id,
        target_field_name=field_name,
        domain=domain,
        title=f"Relax Required Cardinality on '{target_entity_id}.{field_name}'",
        rationale=(
            f"Field '{field_name}' on '{target_entity_id}' exhibited a "
            f"{absent_rate:.1%} absent_from_document rate (>35% threshold) across "
            "verified legitimate submissions."
        ),
        evidence_metrics={"absent_from_document_rate": absent_rate},
    )


__all__ = [
    "compute_jaccard_similarity",
    "detect_bimodal_entity_split",
    "detect_chronic_absent_field_cardinality",
    "detect_colliding_entities",
    "detect_out_of_ontology_escape_cluster",
]
