"""OperationalStore abstract interface, role-based business views, and InMemoryOperationalStore."""

from __future__ import annotations

import abc
from datetime import UTC, datetime

from bridge.operational_store.consolidation_rules import (
    compute_financial_summary,
    consolidate_entry,
    resolve_active_facts,
    source_priority_for_doctype,
)
from bridge.operational_store.iceberg_sync import build_bigquery_sync_batch
from bridge.operational_store.models import (
    BigQuerySyncBatch,
    BrokerQuoteRow,
    BrokerView,
    ClaimsActiveCoverage,
    ClaimsView,
    ConsolidatedFact,
    FactConflict,
    SourceTrace,
    UnderwriterExposureItem,
    UnderwriterView,
)
from bridge.telemetry import trace_step
from contract.wire import LedgerEntry


class OperationalStore(abc.ABC):
    """AlloyDB / Postgres bi-temporal operational store interface (append-only)."""

    @abc.abstractmethod
    def append_facts(self, facts: list[ConsolidatedFact]) -> int:
        """Append consolidated facts (never overwrites existing facts). Returns count."""

    @abc.abstractmethod
    def list_facts(
        self,
        entity_id: str | None = None,
        *,
        learned_as_of: datetime | None = None,
        effective_on: datetime | None = None,
        since_recorded_at: datetime | None = None,
    ) -> list[ConsolidatedFact]:
        """Return stored facts in append order, with optional bi-temporal pushdown filters."""

    def ingest_entry(
        self,
        entry: LedgerEntry,
        *,
        entity_id: str,
        context_id: str,
        recorded_at: datetime | None = None,
        effective_from: datetime | None = None,
        version: int = 1,
    ) -> list[ConsolidatedFact]:
        """Consolidate an accepted ``LedgerEntry`` and append its facts to the store."""
        with trace_step(
            "docubridge.consolidation.promote_to_operational_store",
            {
                "docubridge.zone": "3_alloydb_operational_store",
                "docubridge.entity_id": entity_id,
                "docubridge.context_id": context_id,
                "docubridge.doc_id": entry.doc_id,
                "docubridge.doctype": entry.doctype,
                "docubridge.version": version,
            },
        ) as span:
            facts = consolidate_entry(
                entry,
                entity_id=entity_id,
                context_id=context_id,
                recorded_at=recorded_at,
                effective_from=effective_from,
                version=version,
            )
            self.append_facts(facts)
            span.set_attribute("docubridge.appended_fact_count", len(facts))
            span.set_attribute(
                "docubridge.source_priority",
                facts[0].source_priority
                if facts
                else source_priority_for_doctype(entry.doctype),
            )
            return facts

    def query_as_of(
        self,
        entity_id: str,
        *,
        learned_as_of: datetime | None = None,
        effective_on: datetime | None = None,
    ) -> tuple[list[ConsolidatedFact], list[FactConflict]]:
        """Bi-temporal point-in-time query: what was active as of ``learned_as_of``
        and ``effective_on``."""
        with trace_step(
            "docubridge.consolidation.bitemporal_query_as_of",
            {
                "docubridge.zone": "3_alloydb_operational_store",
                "docubridge.entity_id": entity_id,
                "docubridge.learned_as_of": learned_as_of.isoformat()
                if learned_as_of
                else None,
                "docubridge.effective_on": effective_on.isoformat()
                if effective_on
                else None,
            },
        ) as span:
            filtered_facts = self.list_facts(
                entity_id,
                learned_as_of=learned_as_of,
                effective_on=effective_on,
            )
            winners, conflicts = resolve_active_facts(
                filtered_facts, learned_as_of=learned_as_of, effective_on=effective_on
            )
            span.set_attribute("docubridge.active_fact_count", len(winners))
            span.set_attribute("docubridge.conflict_count", len(conflicts))
            return winners, conflicts

    def broker_view(
        self,
        entity_id: str,
        *,
        learned_as_of: datetime | None = None,
        required_fields: tuple[str, ...] = (
            "premium",
            "deductible",
            "coverage_limits",
            "exclusions",
        ),
    ) -> BrokerView:
        """Zone 4 Role-Based Broker View: quotes, gaps, conflicts & source traces."""
        with trace_step(
            "docubridge.consolidation.role_based_view",
            {
                "docubridge.zone": "4_role_based_business_views",
                "docubridge.view_name": "v_broker_placement",
                "docubridge.entity_id": entity_id,
            },
        ) as span:
            as_of = learned_as_of or datetime.now(UTC)
            all_facts = self.list_facts(entity_id, learned_as_of=learned_as_of)
            active_facts, conflicts = resolve_active_facts(
                all_facts, learned_as_of=learned_as_of
            )

            by_doc: dict[str, list[ConsolidatedFact]] = {}
            for f in all_facts:
                by_doc.setdefault(f.doc_id, []).append(f)

            quotes: list[BrokerQuoteRow] = []
            for doc_id, doc_facts in by_doc.items():
                first = doc_facts[0]
                kv: dict[str, str] = {}
                traces: dict[str, SourceTrace] = {}
                for df in doc_facts:
                    leaf = df.field_path.split(".")[-1]
                    kv[leaf] = df.field_value
                    traces[leaf] = df.trace()
                quotes.append(
                    BrokerQuoteRow(
                        issuer=first.issuer or first.doc_id,
                        doc_id=doc_id,
                        doctype=first.doctype,
                        premium=kv.get("premium"),
                        deductible=kv.get("deductible"),
                        coverage_limits=kv.get("coverage_limits")
                        or kv.get("coverage_limit"),
                        exclusions=kv.get("exclusions"),
                        effective_from=max(f.effective_from for f in doc_facts),
                        recorded_at=max(f.recorded_at for f in doc_facts),
                        traces=traces,
                    )
                )

            present_leaves = {f.field_path.split(".")[-1] for f in active_facts}
            gaps = [req for req in required_fields if req not in present_leaves]
            span.set_attribute("docubridge.quote_count", len(quotes))
            span.set_attribute("docubridge.conflict_count", len(conflicts))
            return BrokerView(
                entity_id=entity_id,
                as_of_learned=as_of,
                quotes=quotes,
                coverage_gaps=gaps,
                conflicts=conflicts,
                financials=compute_financial_summary(active_facts),
            )

    def claims_view(
        self,
        entity_id: str,
        *,
        loss_date: datetime,
        learned_as_of: datetime | None = None,
    ) -> ClaimsView:
        """Zone 4 Role-Based Claims View: active limits & deductibles on ``loss_date``."""
        with trace_step(
            "docubridge.consolidation.role_based_view",
            {
                "docubridge.zone": "4_role_based_business_views",
                "docubridge.view_name": "v_claims_coverage",
                "docubridge.entity_id": entity_id,
                "docubridge.loss_date": loss_date.isoformat(),
            },
        ) as span:
            as_of = learned_as_of or datetime.now(UTC)
            active_facts, _ = self.query_as_of(
                entity_id, learned_as_of=learned_as_of, effective_on=loss_date
            )
            items = [
                ClaimsActiveCoverage(
                    field_path=f.field_path,
                    field_value=f.field_value,
                    numeric_value=f.numeric_value,
                    issuer=f.issuer,
                    doc_id=f.doc_id,
                    doctype=f.doctype,
                    effective_from=f.effective_from,
                    recorded_at=f.recorded_at,
                    trace=f.trace(),
                )
                for f in active_facts
            ]
            summary = compute_financial_summary(active_facts)
            endorsement_docs = {
                f.doc_id
                for f in active_facts
                if source_priority_for_doctype(f.doctype) >= 40
            }
            span.set_attribute("docubridge.active_coverage_count", len(items))
            span.set_attribute("docubridge.endorsement_count", len(endorsement_docs))
            return ClaimsView(
                entity_id=entity_id,
                loss_date=loss_date,
                as_of_learned=as_of,
                active_coverages=items,
                active_limit_total=summary.total_coverage_limit,
                active_deductible_total=summary.total_deductible,
                endorsement_count=len(endorsement_docs),
            )

    def underwriter_view(
        self,
        entity_id: str,
        *,
        learned_as_of: datetime | None = None,
    ) -> UnderwriterView:
        """Zone 4 Role-Based Underwriter View: insured values, exposure & risk ratios."""
        with trace_step(
            "docubridge.consolidation.role_based_view",
            {
                "docubridge.zone": "4_role_based_business_views",
                "docubridge.view_name": "v_underwriter_exposure",
                "docubridge.entity_id": entity_id,
            },
        ) as span:
            as_of = learned_as_of or datetime.now(UTC)
            active_facts, _ = self.query_as_of(entity_id, learned_as_of=learned_as_of)
            items = [
                UnderwriterExposureItem(
                    field_path=f.field_path,
                    field_value=f.field_value,
                    numeric_value=f.numeric_value,
                    doc_id=f.doc_id,
                    doctype=f.doctype,
                    flagged_for_review=f.flagged_for_review,
                    trace=f.trace(),
                )
                for f in active_facts
            ]
            flagged_count = sum(1 for item in items if item.flagged_for_review)
            span.set_attribute("docubridge.exposure_item_count", len(items))
            span.set_attribute("docubridge.flagged_items_count", flagged_count)
            return UnderwriterView(
                entity_id=entity_id,
                as_of_learned=as_of,
                exposure_items=items,
                financials=compute_financial_summary(active_facts),
                flagged_items_count=flagged_count,
            )

    def export_bigquery_sync_batch(
        self,
        entity_id: str | None = None,
        *,
        since_recorded_at: datetime | None = None,
    ) -> BigQuerySyncBatch:
        """Export append-only CDC rows from AlloyDB to BigQuery Open Iceberg tables."""
        with trace_step(
            "docubridge.consolidation.bigquery_iceberg_sync",
            {
                "docubridge.zone": "4_bigquery_analytics_iceberg",
                "docubridge.entity_id": entity_id,
                "docubridge.iceberg_table": "docubridge_lakehouse.bq_operational_facts_iceberg",
            },
        ) as span:
            facts = self.list_facts(entity_id, since_recorded_at=since_recorded_at)
            batch = build_bigquery_sync_batch(
                facts, since_recorded_at=since_recorded_at
            )
            span.set_attribute("docubridge.cdc_row_count", batch.row_count)
            return batch

    def close(self) -> None:  # noqa: B027
        """Release any underlying database connections (no-op for in-memory)."""


class InMemoryOperationalStore(OperationalStore):
    """In-memory bi-temporal operational store (append-only)."""

    def __init__(self) -> None:
        self._facts: list[ConsolidatedFact] = []
        self._seen_ids: set[str] = set()

    def append_facts(self, facts: list[ConsolidatedFact]) -> int:
        """Append extracted canonical fact rows to the in-memory operational store."""
        for f in facts:
            if f.fact_id in self._seen_ids:
                raise ValueError(
                    f"ConsolidatedFact {f.fact_id!r} already exists; "
                    "OperationalStore is append-only (no overwrites)"
                )
            self._seen_ids.add(f.fact_id)
            self._facts.append(f)
        return len(facts)

    def list_facts(
        self,
        entity_id: str | None = None,
        *,
        learned_as_of: datetime | None = None,
        effective_on: datetime | None = None,
        since_recorded_at: datetime | None = None,
    ) -> list[ConsolidatedFact]:
        """List canonical fact rows filtered by entity ID, domain, document ID, or fact key."""
        out: list[ConsolidatedFact] = []
        for f in self._facts:
            if entity_id is not None and f.entity_id != entity_id:
                continue
            if learned_as_of is not None and f.recorded_at > learned_as_of:
                continue
            if effective_on is not None and f.effective_from > effective_on:
                continue
            if since_recorded_at is not None and f.recorded_at <= since_recorded_at:
                continue
            out.append(f)
        return out
