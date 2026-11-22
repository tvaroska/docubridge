"""Unit tests for consolidation table resolution, effective dates, winner order, and currency integrity (P13-2, P13-3, P13-13)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from bridge.knowledge_catalog import InMemoryKnowledgeCatalog
from bridge.operational_store import (
    InMemoryOperationalStore,
    compute_financial_summary,
    consolidate_entry,
    resolve_active_facts,
)
from bridge.operational_store.iceberg_sync import SQL_VIEW_DEFINITIONS
from bridge.user_interaction import ingest_accepted_submission
from bridge.vault import InMemoryAuditVaultStore
from contract.enums import Disposition, Gate
from contract.wire import LedgerEntry


def _entry(
    *,
    doc_id: str,
    doctype: str,
    key_fields: dict[str, str] | None = None,
    tables: dict[str, list[dict[str, str]]] | None = None,
    recorded_at: datetime | None = None,
    effective_from: datetime | None = None,
) -> LedgerEntry:
    return LedgerEntry(
        doc_id=doc_id,
        doctype=doctype,
        disposition=Disposition.accepted,
        gate=Gate.auto_approve,
        issuer="Acme Insurance",
        key_fields=key_fields or {},
        tables=tables or {},
        recorded_at=recorded_at,
        effective_from=effective_from,
    )


def test_superseding_document_replaces_table_rows() -> None:
    """A superseding document's table replaces the older table completely without leaving surplus rows."""
    t1 = datetime(2026, 1, 10, 9, 0, tzinfo=UTC)
    t2 = datetime(2026, 2, 10, 9, 0, tzinfo=UTC)
    eff1 = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    eff2 = datetime(2026, 2, 1, 0, 0, tzinfo=UTC)

    base_policy = _entry(
        doc_id="doc-policy-1",
        doctype="policy",
        tables={
            "schedule": [
                {"location": "A", "coverage_limit": "$1,000,000.00"},
                {"location": "B", "coverage_limit": "$2,000,000.00"},
            ]
        },
        recorded_at=t1,
        effective_from=eff1,
    )
    endorsement = _entry(
        doc_id="doc-end-2",
        doctype="endorsement",
        tables={
            "schedule": [
                {"location": "B", "coverage_limit": "$2,500,000.00"},
            ]
        },
        recorded_at=t2,
        effective_from=eff2,
    )

    store = InMemoryOperationalStore()
    store.ingest_entry(base_policy, entity_id="ENT-TBL", context_id="ctx-tbl")
    store.ingest_entry(endorsement, entity_id="ENT-TBL", context_id="ctx-tbl")
    active, conflicts = store.query_as_of("ENT-TBL")
    summary = compute_financial_summary(active)

    assert summary.total_coverage_limit == "2500000.00"
    assert all(
        f.doc_id == "doc-end-2" for f in active if "tables.schedule" in f.field_path
    )
    assert conflicts


def test_document_effective_date_reaches_facts() -> None:
    """Document effective_date reaches consolidated facts through ingest_accepted_submission, and bad dates are flagged."""
    vault = InMemoryAuditVaultStore()
    store = InMemoryOperationalStore()
    catalog = InMemoryKnowledgeCatalog(operational_store=store)

    entry = _entry(
        doc_id="doc-eff-1",
        doctype="policy",
        key_fields={
            "effective_date": "2026-03-15",
            "premium": "$12,000.00",
        },
    )
    res = ingest_accepted_submission(
        entry,
        entity_id="ENT-EFF",
        context_id="ctx-eff",
        raw_document_bytes=b"%PDF-1.7 real",
        vault_store=vault,
        operational_store=store,
        knowledge_catalog=catalog,
    )
    assert res.facts
    for fact in res.facts:
        assert fact.effective_from == datetime(2026, 3, 15, 0, 0, tzinfo=UTC)
        assert fact.flagged_for_review is False

    # Unreadable non-ISO effective_date flags facts for review instead of silently falling back
    bad_entry = _entry(
        doc_id="doc-eff-bad",
        doctype="policy",
        key_fields={
            "effective_date": "March 15th, 2026",
            "premium": "$12,000.00",
        },
    )
    bad_facts = consolidate_entry(
        bad_entry,
        entity_id="ENT-EFF-BAD",
        context_id="ctx-eff-bad",
    )
    eff_fact = next(f for f in bad_facts if f.field_path.endswith("effective_date"))
    assert eff_fact.flagged_for_review is True


def test_winner_order_priority_versus_effective_date() -> None:
    """Newer effective_from beats an older higher-priority endorsement; same effective_from breaks ties by source_priority."""
    t_rec = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    old_endorsement = _entry(
        doc_id="doc-2024-end",
        doctype="endorsement",
        key_fields={"premium": "$90,000.00"},
        recorded_at=t_rec,
        effective_from=datetime(2024, 5, 1, 0, 0, tzinfo=UTC),
    )
    newer_renewal = _entry(
        doc_id="doc-2026-pol",
        doctype="policy",
        key_fields={"premium": "$110,000.00"},
        recorded_at=t_rec,
        effective_from=datetime(2026, 5, 1, 0, 0, tzinfo=UTC),
    )
    facts = consolidate_entry(
        old_endorsement, entity_id="ENT-ORD", context_id="ctx-ord"
    ) + consolidate_entry(newer_renewal, entity_id="ENT-ORD", context_id="ctx-ord")
    winners, _ = resolve_active_facts(facts)
    prem = next(f for f in winners if f.field_path.endswith("premium"))
    assert prem.doc_id == "doc-2026-pol"
    assert prem.field_value == "$110,000.00"

    # Equal effective_from: higher source_priority (endorsement=40) beats policy (30)
    same_eff = datetime(2026, 5, 1, 0, 0, tzinfo=UTC)
    same_date_end = _entry(
        doc_id="doc-2026-end",
        doctype="endorsement",
        key_fields={"premium": "$125,000.00"},
        recorded_at=t_rec,
        effective_from=same_eff,
    )
    winners_same, _ = resolve_active_facts(
        facts
        + consolidate_entry(same_date_end, entity_id="ENT-ORD", context_id="ctx-ord")
    )
    prem_same = next(f for f in winners_same if f.field_path.endswith("premium"))
    assert prem_same.doc_id == "doc-2026-end"


def test_tiebreak_effective_before_recorded_memory_and_sql() -> None:
    """In-memory resolution and governed SQL view DDL both order effective_from before recorded_at."""
    earlier_eff_recorded_later = _entry(
        doc_id="doc-eff-jan",
        doctype="policy",
        key_fields={"premium": "$10,000.00"},
        recorded_at=datetime(2026, 6, 10, 0, 0, tzinfo=UTC),
        effective_from=datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
    )
    later_eff_recorded_earlier = _entry(
        doc_id="doc-eff-mar",
        doctype="policy",
        key_fields={"premium": "$20,000.00"},
        recorded_at=datetime(2026, 5, 1, 0, 0, tzinfo=UTC),
        effective_from=datetime(2026, 3, 1, 0, 0, tzinfo=UTC),
    )
    facts = consolidate_entry(
        earlier_eff_recorded_later, entity_id="ENT-TIE", context_id="ctx-tie"
    ) + consolidate_entry(
        later_eff_recorded_earlier, entity_id="ENT-TIE", context_id="ctx-tie"
    )
    winners, _ = resolve_active_facts(facts)
    prem = next(f for f in winners if f.field_path.endswith("premium"))
    assert prem.doc_id == "doc-eff-mar"

    for sql in SQL_VIEW_DEFINITIONS.values():
        eff_idx = sql.index("effective_from DESC")
        prio_idx = sql.index("source_priority DESC")
        rec_idx = sql.index("recorded_at DESC")
        assert eff_idx < prio_idx < rec_idx


def test_credit_reduces_total_premium() -> None:
    """Negative/parenthesized credits reduce total_premium; mixed currencies are refused and unparseable values flagged."""
    base = _entry(
        doc_id="doc-prem-1",
        doctype="policy",
        tables={
            "charges": [
                {"item": "base", "premium": "$1,000.00"},
                {"item": "credit", "premium": "($250.00)"},
            ]
        },
        recorded_at=datetime(2026, 1, 1, tzinfo=UTC),
        effective_from=datetime(2026, 1, 1, tzinfo=UTC),
    )
    facts = consolidate_entry(base, entity_id="ENT-CR", context_id="ctx-cr")
    winners, _ = resolve_active_facts(facts)
    summary = compute_financial_summary(winners)
    assert summary.total_premium == "750.00"
    assert summary.currency == "USD"

    # Unparseable monetary string yields numeric_value=None and flags fact for review
    prose_entry = _entry(
        doc_id="doc-prose",
        doctype="policy",
        key_fields={"premium": "$1,200 monthly"},
        recorded_at=datetime(2026, 1, 1, tzinfo=UTC),
        effective_from=datetime(2026, 1, 1, tzinfo=UTC),
    )
    prose_facts = consolidate_entry(
        prose_entry, entity_id="ENT-PROSE", context_id="ctx-prose"
    )
    prem_fact = next(f for f in prose_facts if f.field_path.endswith("premium"))
    assert prem_fact.numeric_value is None
    assert prem_fact.flagged_for_review is True

    # EUR amount stored as EUR (not USD), and summing EUR + USD is refused
    eur_entry = _entry(
        doc_id="doc-eur",
        doctype="policy",
        key_fields={"premium": "€1,000.00"},
        recorded_at=datetime(2026, 1, 1, tzinfo=UTC),
        effective_from=datetime(2026, 1, 1, tzinfo=UTC),
    )
    eur_facts = consolidate_entry(eur_entry, entity_id="ENT-CCY", context_id="ctx-ccy")
    eur_prem = next(f for f in eur_facts if f.field_path.endswith("premium"))
    assert eur_prem.currency == "EUR"
    assert eur_prem.numeric_value == "1000.00"

    usd_entry = _entry(
        doc_id="doc-usd",
        doctype="policy",
        key_fields={"deductible": "$500.00"},
        recorded_at=datetime(2026, 1, 1, tzinfo=UTC),
        effective_from=datetime(2026, 1, 1, tzinfo=UTC),
    )
    usd_facts = consolidate_entry(usd_entry, entity_id="ENT-CCY", context_id="ctx-ccy")
    with pytest.raises(ValueError, match="mixed currencies"):
        compute_financial_summary(eur_facts + usd_facts)


def test_consolidate_entry_does_not_swallow_ontology_error(monkeypatch) -> None:
    """Ontology load failures propagate instead of silently falling back to static priority (P13-10)."""
    import bridge.knowledge_catalog.catalog as catalog_mod

    def _boom():
        raise RuntimeError("ontology unavailable")

    monkeypatch.setattr(catalog_mod, "_ensure_shared_ontology", _boom)
    entry = _entry(
        doc_id="doc-ont-err",
        doctype="policy",
        key_fields={"premium": "$1,000.00"},
    )
    with pytest.raises(RuntimeError, match="ontology unavailable"):
        consolidate_entry(entry, entity_id="ENT-ERR", context_id="ctx-err")


def test_http_intake_effective_date_and_content_base64() -> None:
    """POST /v1/intake/ingest honors key_fields.effective_date and validates content_base64 (P13-3, P13-5)."""
    import base64
    import hashlib

    from fastapi.testclient import TestClient

    from bridge.user_interaction import build_user_interaction_app

    store = InMemoryOperationalStore()
    catalog = InMemoryKnowledgeCatalog(operational_store=store)
    app = build_user_interaction_app(
        operational_store=store,
        knowledge_catalog=catalog,
        require_auth=False,
    )
    client = TestClient(app)

    pdf_bytes = b"%PDF-1.7 real submitted policy document bytes"
    resp = client.post(
        "/v1/intake/ingest",
        json={
            "entity_id": "ENT-HTTP-EFF",
            "context_id": "ctx-http-eff",
            "doc_id": "doc-http-1",
            "doctype": "policy",
            "issuer": "Acme",
            "key_fields": {
                "effective_date": "2026-03-15",
                "premium": "$5,000.00",
            },
            "content_base64": base64.b64encode(pdf_bytes).decode("ascii"),
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["vault_record"]["sha256"] == hashlib.sha256(pdf_bytes).hexdigest()
    assert body["vault_record"]["mime_type"] == "application/pdf"
    facts = store.list_facts("ENT-HTTP-EFF")
    assert facts
    assert all(
        f.effective_from == datetime(2026, 3, 15, 0, 0, tzinfo=UTC) for f in facts
    )

    bad_b64 = client.post(
        "/v1/intake/ingest",
        json={
            "entity_id": "ENT-HTTP-EFF",
            "context_id": "ctx-http-eff",
            "doc_id": "doc-http-bad",
            "doctype": "policy",
            "content_base64": "!!!not-base64!!!",
        },
    )
    assert bad_b64.status_code == 422

    empty_b64 = client.post(
        "/v1/intake/ingest",
        json={
            "entity_id": "ENT-HTTP-EFF",
            "context_id": "ctx-http-eff",
            "doc_id": "doc-http-empty",
            "doctype": "policy",
            "content_base64": "",
        },
    )
    assert empty_b64.status_code == 422
