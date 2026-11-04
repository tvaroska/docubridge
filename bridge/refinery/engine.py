"""Schema Refinery Engine: Taxonomy Assay, Contrastive Compilation & Bi-Temporal Promotion.

Coordinates the closed-loop evolution of the Zone 6 Ontology Graph and Zone 2
contrastive descriptions across in-memory and durable SQL backends.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import Any

from bridge.knowledge_catalog import KnowledgeCatalog, knowledge_catalog_from_env
from bridge.refinery.assay import (
    build_candidate_revision,
    detect_bimodal_entity_split,
    evaluate_ontology_proposal,
)
from bridge.refinery.okf_loader import build_baseline_ontology_revision
from contract.refinery import (
    OntologyEdge,
    OntologyRevision,
    OntologyRevisionState,
    RefineryAssayReport,
    RefineryProposal,
    RefineryProposalStatus,
)


class SchemaRefineryEngine(ABC):
    """Abstract interface for the Schema Refinery & Ontology Evolution engine."""

    @abstractmethod
    def get_active_revision(self) -> OntologyRevision:
        """Return the currently active OntologyRevision."""

    @abstractmethod
    def list_revisions(self) -> list[OntologyRevision]:
        """Return all OntologyRevision snapshots in chronological order."""

    @abstractmethod
    def list_proposals(
        self,
        *,
        domain: str | None = None,
        status: RefineryProposalStatus | None = None,
    ) -> list[RefineryProposal]:
        """Return candidate or promoted RefineryProposal records."""

    @abstractmethod
    def get_proposal(self, proposal_id: str) -> RefineryProposal | None:
        """Look up a single RefineryProposal by ID."""

    @abstractmethod
    def list_assay_reports(
        self,
        *,
        proposal_id: str | None = None,
    ) -> list[RefineryAssayReport]:
        """Return 3-Tier RefineryAssayReport scorecards."""

    @abstractmethod
    def get_assay_report(self, report_id: str) -> RefineryAssayReport | None:
        """Look up a single RefineryAssayReport by ID."""

    @abstractmethod
    def register_proposal(
        self,
        proposal: RefineryProposal,
    ) -> tuple[RefineryProposal, RefineryAssayReport, OntologyRevision]:
        """Evaluate a custom proposal through the 3-Tier Assay Gate and persist both."""

    @abstractmethod
    def run_taxonomy_assay(
        self,
        *,
        domain: str = "insurance",
        target_entity_id: str | None = None,
        cluster_a_fields: set[str] | None = None,
        cluster_b_fields: set[str] | None = None,
    ) -> tuple[RefineryProposal, RefineryAssayReport, OntologyRevision]:
        """Run bimodal field sparsity & contrastive description assay for a domain/entity."""

    @abstractmethod
    def promote_proposal(
        self,
        proposal_id: str,
        *,
        revision_id: str | None = None,
        version: str = "1.2.0",
        action_inbox: Any = None,
    ) -> OntologyRevision:
        """Promote an assay-passed proposal into a new active OntologyRevision."""

    @abstractmethod
    def reset(self) -> OntologyRevision:
        """Reset all ontology revisions, proposals, and assay reports back to baseline."""


class InMemorySchemaRefineryEngine(SchemaRefineryEngine):
    """In-memory Schema Refinery engine synchronized with Zone 6 KnowledgeCatalog."""

    def __init__(
        self,
        *,
        knowledge_catalog: KnowledgeCatalog | None = None,
    ) -> None:
        self._catalog = knowledge_catalog or knowledge_catalog_from_env()
        self._proposals: dict[str, RefineryProposal] = {}
        self._reports: dict[str, RefineryAssayReport] = {}

    def _save_revision(
        self,
        revision: OntologyRevision,
        *,
        activate: bool = True,
    ) -> OntologyRevision:
        return self._catalog.register_ontology_revision(revision, activate=activate)

    def _save_proposal(self, proposal: RefineryProposal) -> None:
        self._proposals[proposal.proposal_id] = proposal

    def _save_report(self, report: RefineryAssayReport) -> None:
        self._reports[report.report_id] = report

    def get_active_revision(self) -> OntologyRevision:
        """Return the currently active OntologyRevision."""
        return self._catalog.get_active_ontology_revision()

    def list_revisions(self) -> list[OntologyRevision]:
        """Return all OntologyRevision snapshots in chronological order."""
        return self._catalog.list_ontology_revisions()

    def list_proposals(
        self,
        *,
        domain: str | None = None,
        status: RefineryProposalStatus | None = None,
    ) -> list[RefineryProposal]:
        """Return candidate or promoted RefineryProposal records."""
        items = list(self._proposals.values())
        if domain is not None:
            dom = domain.strip().lower()
            items = [p for p in items if p.domain.lower() == dom]
        if status is not None:
            items = [p for p in items if p.status == status]
        return items

    def get_proposal(self, proposal_id: str) -> RefineryProposal | None:
        """Look up a single RefineryProposal by ID."""
        return self._proposals.get(proposal_id)

    def list_assay_reports(
        self,
        *,
        proposal_id: str | None = None,
    ) -> list[RefineryAssayReport]:
        """Return 3-Tier RefineryAssayReport scorecards."""
        items = list(self._reports.values())
        if proposal_id is not None:
            items = [r for r in items if r.proposal_id == proposal_id]
        return items

    def get_assay_report(self, report_id: str) -> RefineryAssayReport | None:
        """Look up a single RefineryAssayReport by ID."""
        return self._reports.get(report_id)

    def register_proposal(
        self,
        proposal: RefineryProposal,
    ) -> tuple[RefineryProposal, RefineryAssayReport, OntologyRevision]:
        """Evaluate a custom proposal through the 3-Tier Assay Gate and persist both."""
        baseline = self.get_active_revision()
        report, candidate_rev = evaluate_ontology_proposal(proposal, baseline)
        new_status = (
            RefineryProposalStatus.ASSAY_PASSED
            if report.passed
            else RefineryProposalStatus.ASSAY_FAILED
        )
        updated_proposal = proposal.model_copy(
            update={
                "status": new_status,
                "assay_report_id": report.report_id,
            }
        )
        self._save_proposal(updated_proposal)
        self._save_report(report)
        return updated_proposal, report, candidate_rev

    def run_taxonomy_assay(
        self,
        *,
        domain: str = "insurance",
        target_entity_id: str | None = None,
        cluster_a_fields: set[str] | None = None,
        cluster_b_fields: set[str] | None = None,
    ) -> tuple[RefineryProposal, RefineryAssayReport, OntologyRevision]:
        """Run bimodal field sparsity & contrastive description assay for a domain/entity."""
        baseline = self.get_active_revision()
        raw_proposal = detect_bimodal_entity_split(
            domain=domain,
            target_entity_id=target_entity_id,
            cluster_a_fields=cluster_a_fields,
            cluster_b_fields=cluster_b_fields,
            baseline_revision=baseline,
        )
        return self.register_proposal(raw_proposal)

    def promote_proposal(
        self,
        proposal_id: str,
        *,
        revision_id: str | None = None,
        version: str = "1.2.0",
        action_inbox: Any = None,
    ) -> OntologyRevision:
        """Promote an assay-passed proposal into a new active OntologyRevision."""
        proposal = self.get_proposal(proposal_id)
        if proposal is None:
            raise KeyError(f"Unknown RefineryProposal '{proposal_id}'")
        if proposal.status not in (
            RefineryProposalStatus.ASSAY_PASSED,
            RefineryProposalStatus.PROMOTED,
        ):
            raise ValueError(
                f"Cannot promote proposal '{proposal_id}' with status {proposal.status.value}"
            )
        baseline = self.get_active_revision()
        now = datetime.now(UTC)
        target_rev_id = revision_id or f"ont-rev-v1-2-{proposal.domain}"
        candidate = build_candidate_revision(
            baseline,
            proposal,
            candidate_revision_id=target_rev_id,
            candidate_version=version,
        )
        promoted_rev = candidate.model_copy(
            update={
                "state": OntologyRevisionState.ACTIVE,
                "effective_from": now,
                "recorded_at": now,
            }
        )
        self._save_revision(promoted_rev, activate=True)
        promoted_proposal = proposal.model_copy(
            update={
                "status": RefineryProposalStatus.PROMOTED,
                "promoted_revision_id": promoted_rev.revision_id,
            }
        )
        self._save_proposal(promoted_proposal)

        # Snapshot re-evaluation for parked HITL Inbox tasks pinned to older revisions
        if action_inbox is not None and hasattr(action_inbox, "list_items"):
            for item in action_inbox.list_items():
                if (
                    getattr(item, "task_state", "") == "input_required"
                    and getattr(item, "ontology_revision_id", "")
                    != promoted_rev.revision_id
                ):
                    item.ontology_revision_id = promoted_rev.revision_id
        return promoted_rev

    def list_edges(
        self,
        *,
        relation: str | None = None,
        source_id: str | None = None,
    ) -> list[OntologyEdge]:
        """Return graph edges from the active OntologyRevision."""
        return self._catalog.list_ontology_edges(relation=relation, source_id=source_id)

    def reset(self) -> OntologyRevision:
        """Reset all ontology revisions, proposals, and assay reports back to baseline."""
        self._proposals.clear()
        self._reports.clear()
        baseline = self._catalog.reset_ontology_revisions()
        if baseline is None:
            baseline = build_baseline_ontology_revision()
        return baseline
