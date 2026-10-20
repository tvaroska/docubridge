"""End-to-end tests for the 6-Zone Enterprise Data Architecture.

Verifies:
- Zone 1 & 2: SourceAnchor page/bbox preservation and deterministic edge PII redaction
- Zone 3: Cloud Storage Audit Vault (WORM lock + promotion gate) and AlloyDB
  Bi-Temporal Operational Store (Decimal math, Source Priority Hierarchy, cross-document
  conflict flags, and Slide 2 '2h ago' vs 'Now' + retroactive endorsement time-travel)
- Zone 4: Role-Based Business Views (Broker, Claims, Underwriter) and BigQuery Open
  Iceberg CDC analytics sync
- Zone 5: User Interaction Layer via A2UI to Gemini App (internal) and Custom Web
  (external) with one-click source PDF page citations
- Zone 6: Knowledge Catalog (Glossary & Quality Rules, Asset Map, End-to-End Lineage,
  and Agent Context & Access Control)
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bridge.knowledge_catalog import (
    AssetLocationZone,
    InMemoryKnowledgeCatalog,
    authorize_access,
)
from bridge.operational_store import (
    BIGQUERY_ICEBERG_DDL,
    SQL_VIEW_DEFINITIONS,
    InMemoryOperationalStore,
    SqlOperationalStore,
    compute_financial_summary,
    consolidate_entry,
)
from bridge.user_interaction import (
    build_user_interaction_app,
    ingest_accepted_submission,
    render_domain_agent_a2ui,
)
from bridge.vault import (
    InMemoryAuditVaultStore,
    VaultImmutabilityError,
    VaultPromotionRefused,
    promote_accepted_to_vault,
    redact_personal_data,
)
from contract.enums import Disposition, Gate
from contract.extraction import ExtractionFields, ExtractionResult, SourceAnchor
from contract.wire import LedgerEntry


def _make_accepted_entry(
    *,
    doc_id: str = "doc-quote-v1",
    doctype: str = "carrier-quote",
    issuer: str = "Aetna Life Insurance Company",
    key_fields: dict[str, str] | None = None,
    source_anchors: dict[str, SourceAnchor] | None = None,
    recorded_at: datetime | None = None,
    effective_from: datetime | None = None,
) -> LedgerEntry:
    fields = key_fields or {
        "currency": "USD",
        "effective_date": "2026-01-01",
        "premium": "$1,200,000.00",
        "deductible": "$150,000.00",
        "coverage_limit": "$10,000,000.00",
        "total_insured_value": "$25,000,000.00",
        "exclusions": "Cyber war exclusion clause 4B",
    }
    anchors = source_anchors or {
        "premium": SourceAnchor(
            page_number=4,
            bounding_box=(0.12, 0.34, 0.56, 0.41),
            snippet="Total Annual Premium: $1,200,000.00",
            artifact_uri="gs://docubridge-audit-vault/vault/ctx-1/doc-quote-v1/v1/quote.pdf",
        ),
        "deductible": SourceAnchor(
            page_number=7,
            bounding_box=(0.15, 0.50, 0.48, 0.55),
            snippet="Specific Deductible: $150,000.00",
            artifact_uri="gs://docubridge-audit-vault/vault/ctx-1/doc-quote-v1/v1/quote.pdf",
        ),
        "coverage_limit": SourceAnchor(
            page_number=3,
            bounding_box=(0.10, 0.20, 0.60, 0.28),
            snippet="Policy Coverage Limit: $10,000,000.00",
        ),
    }
    return LedgerEntry(
        doc_id=doc_id,
        doctype=doctype,
        issuer=issuer,
        disposition=Disposition.accepted,
        gate=Gate.auto_approve,
        key_fields=fields,
        source_anchors=anchors,
        recorded_at=recorded_at,
        effective_from=effective_from,
    )


def test_zone1_and_zone2_source_anchors_and_edge_pii_redaction() -> None:
    """Zone 1 & 2: SourceAnchor validates page/bbox and PII is redacted before storage."""
    raw_extraction = ExtractionResult(
        fields=ExtractionFields(
            doctype="carrier-quote",
            issuer="Aetna",
            key_fields={
                "member_ssn": "123-45-6789",
                "claimant_notes": "Contact John at john.doe@example.com or 312-555-0199; SSN 987-65-4321",
                "premium": "$1,200,000.00",
            },
            source_anchors={
                "premium": SourceAnchor(
                    page_number=2,
                    bounding_box=(0.1, 0.2, 0.4, 0.25),
                    snippet="Premium: $1,200,000.00",
                ),
                "claimant_notes": SourceAnchor(
                    page_number=5,
                    bounding_box=(0.1, 0.3, 0.8, 0.4),
                    snippet="Claimant SSN 987-65-4321 email john.doe@example.com",
                ),
            },
        ),
        overall_confidence=0.96,
        legible=True,
    )
    assert raw_extraction.fields.source_anchors["premium"].page_number == 2
    assert raw_extraction.fields.source_anchors["premium"].bounding_box == (
        0.1,
        0.2,
        0.4,
        0.25,
    )

    sanitized, audit = redact_personal_data(raw_extraction)
    assert sanitized.fields.key_fields["member_ssn"] == "[REDACTED-PII]"
    assert "987-65-4321" not in sanitized.fields.key_fields["claimant_notes"]
    assert "john.doe@example.com" not in sanitized.fields.key_fields["claimant_notes"]
    assert "312-555-0199" not in sanitized.fields.key_fields["claimant_notes"]
    assert sanitized.fields.key_fields["premium"] == "$1,200,000.00"
    assert (
        "987-65-4321"
        not in (sanitized.fields.source_anchors["claimant_notes"].snippet or "")
    )
    assert audit.redacted_count >= 3
    assert "fields.key_fields.member_ssn" in audit.redacted_paths


def test_zone3_audit_vault_worm_lock_and_promotion_gate() -> None:
    """Zone 3: Only accepted entries promote to Vault, and WORM lock blocks mutation."""
    vault = InMemoryAuditVaultStore()

    rejected_entry = LedgerEntry(
        doc_id="doc-rejected-1",
        doctype="carrier-quote",
        disposition=Disposition.rejected,
        gate=Gate.resubmit,
    )
    with pytest.raises(VaultPromotionRefused):
        promote_accepted_to_vault(
            vault,
            context_id="ctx-worm-1",
            entry=rejected_entry,
            data=b"%PDF-1.7 rejected",
        )

    accepted_entry = _make_accepted_entry(doc_id="doc-worm-1")
    promoted, rec1 = promote_accepted_to_vault(
        vault,
        context_id="ctx-worm-1",
        entry=accepted_entry,
        data=b"%PDF-1.7 original quote bytes",
        filename="quote.pdf",
        version=1,
    )
    assert rec1.retention_locked is True
    assert promoted.vault_uri == rec1.vault_uri
    assert (
        promoted.source_anchors["premium"].artifact_uri == rec1.vault_uri
    )

    # Attempting to overwrite the same (context_id, doc_id, version) raises VaultImmutabilityError
    with pytest.raises(VaultImmutabilityError):
        promote_accepted_to_vault(
            vault,
            context_id="ctx-worm-1",
            entry=accepted_entry,
            data=b"%PDF-1.7 tampered quote bytes",
            filename="quote.pdf",
            version=1,
        )

    # Attempting to delete a locked object raises VaultImmutabilityError
    with pytest.raises(VaultImmutabilityError):
        vault.delete_document("ctx-worm-1", "doc-worm-1", version=1)


def test_zone3_deterministic_decimal_financial_math() -> None:
    """Zone 3: Consolidation engine performs 100% deterministic Decimal math."""
    entry = _make_accepted_entry()
    facts = consolidate_entry(
        entry,
        entity_id="ent-aon-100",
        context_id="ctx-aon-100",
        recorded_at=datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
        effective_from=datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
    )
    summary = compute_financial_summary(facts)
    assert summary.total_premium == "1200000.00"
    assert summary.total_deductible == "150000.00"
    assert summary.total_coverage_limit == "10000000.00"
    assert summary.total_insured_value == "25000000.00"
    # 150,000 / 10,000,000 * 100 = 1.50%
    assert summary.deductible_to_limit_pct == "1.50"


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_zone3_bitemporal_updates_over_time_and_priority_hierarchy(
    backend: str,
    tmp_path: Path,
) -> None:
    """Slide 2 scenario: '2h ago' vs 'Now' point-in-time queries + retroactive endorsement."""
    if backend == "sqlite":
        db_file = tmp_path / "operational_store.sqlite"
        store = SqlOperationalStore(f"sqlite+aiosqlite:///{db_file}")
    else:
        store = InMemoryOperationalStore()

    try:
        t_2h_ago = datetime(2026, 3, 1, 10, 0, tzinfo=UTC)
        t_now = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
        t_endorsement_recorded = datetime(2026, 3, 15, 9, 0, tzinfo=UTC)

        eff_jan_1 = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
        eff_feb_1 = datetime(2026, 2, 1, 0, 0, tzinfo=UTC)

        # 1. At t_2h_ago (10:00Z): Initial Carrier Quote v1 ($1,200,000 premium, $150,000 deductible)
        quote_v1 = _make_accepted_entry(
            doc_id="doc-quote-v1",
            doctype="carrier-quote",
            recorded_at=t_2h_ago,
            effective_from=eff_jan_1,
        )
        store.ingest_entry(
            quote_v1,
            entity_id="ent-timeline-1",
            context_id="ctx-timeline-1",
            recorded_at=t_2h_ago,
            effective_from=eff_jan_1,
            version=1,
        )

        # 2. At t_now (12:00Z): Revised Carrier Quote v2 ($1,140,000 premium)
        quote_v2_fields = dict(quote_v1.key_fields)
        quote_v2_fields["premium"] = "$1,140,000.00"
        quote_v2 = _make_accepted_entry(
            doc_id="doc-quote-v2",
            doctype="carrier-quote",
            key_fields=quote_v2_fields,
            recorded_at=t_now,
            effective_from=eff_jan_1,
        )
        store.ingest_entry(
            quote_v2,
            entity_id="ent-timeline-1",
            context_id="ctx-timeline-1",
            recorded_at=t_now,
            effective_from=eff_jan_1,
            version=2,
        )

        # Verify append-only bi-temporal storage (v1 was NOT overwritten!)
        all_facts = store.list_facts("ent-timeline-1")
        premium_versions = [
            f for f in all_facts if f.field_path == "fields.key_fields.premium"
        ]
        assert len(premium_versions) == 2

        # Point-in-time query "2h ago" returns v1 ($1,200,000.00)
        view_2h_ago = store.broker_view("ent-timeline-1", learned_as_of=t_2h_ago)
        assert view_2h_ago.financials.total_premium == "1200000.00"

        # Point-in-time query "Now" returns v2 ($1,140,000.00) and flags the cross-doc conflict
        view_now = store.broker_view("ent-timeline-1", learned_as_of=t_now)
        assert view_now.financials.total_premium == "1140000.00"
        assert any(
            c.field_path == "fields.key_fields.premium" for c in view_now.conflicts
        )

        # 3. At t_endorsement_recorded (Mar 15): Signed Endorsement effective Feb 1 lowers
        #    deductible from $150,000 to $100,000.
        endorsement_fields = dict(quote_v2_fields)
        endorsement_fields["deductible"] = "$100,000.00"
        endorsement = _make_accepted_entry(
            doc_id="doc-endorsement-1",
            doctype="signed-endorsement",
            key_fields=endorsement_fields,
            recorded_at=t_endorsement_recorded,
            effective_from=eff_feb_1,
        )
        store.ingest_entry(
            endorsement,
            entity_id="ent-timeline-1",
            context_id="ctx-timeline-1",
            recorded_at=t_endorsement_recorded,
            effective_from=eff_feb_1,
            version=3,
        )

        # Claims View for a loss on Jan 15 (before Feb 1 endorsement effective date) -> $150,000
        loss_jan_15 = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
        claims_jan = store.claims_view(
            "ent-timeline-1",
            loss_date=loss_jan_15,
            learned_as_of=t_endorsement_recorded,
        )
        assert claims_jan.active_deductible_total == "150000.00"

        # Claims View for a loss on Feb 15 (after Feb 1 endorsement effective date) -> $100,000
        loss_feb_15 = datetime(2026, 2, 15, 12, 0, tzinfo=UTC)
        claims_feb = store.claims_view(
            "ent-timeline-1",
            loss_date=loss_feb_15,
            learned_as_of=t_endorsement_recorded,
        )
        assert claims_feb.active_deductible_total == "100000.00"
        assert claims_feb.endorsement_count == 1
    finally:
        store.close()


def test_zone4_views_and_bigquery_iceberg_cdc_sync() -> None:
    """Zone 4: Broker, Claims, and Underwriter views + BigQuery Open Iceberg CDC batch."""
    vault = InMemoryAuditVaultStore()
    store = InMemoryOperationalStore()
    catalog = InMemoryKnowledgeCatalog()

    t0 = datetime(2026, 3, 1, 10, 0, tzinfo=UTC)
    eff0 = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    entry = _make_accepted_entry(
        doc_id="doc-views-1",
        recorded_at=t0,
        effective_from=eff0,
    )
    ingest_accepted_submission(
        entry,
        entity_id="ent-views-1",
        context_id="ctx-views-1",
        raw_document_bytes=b"%PDF-1.7 sample quote",
        vault_store=vault,
        operational_store=store,
        knowledge_catalog=catalog,
        recorded_at=t0,
        effective_from=eff0,
    )

    b_view = store.broker_view("ent-views-1")
    assert len(b_view.quotes) == 1
    assert b_view.quotes[0].issuer == "Aetna Life Insurance Company"
    assert b_view.financials.total_premium == "1200000.00"
    assert b_view.quotes[0].traces["premium"].page_number == 4
    assert b_view.quotes[0].traces["premium"].bounding_box == (0.12, 0.34, 0.56, 0.41)

    u_view = store.underwriter_view("ent-views-1")
    assert u_view.financials.total_deductible == "150000.00"
    assert u_view.financials.total_insured_value == "25000000.00"
    assert u_view.financials.deductible_to_limit_pct == "1.50"

    cdc_batch = store.export_bigquery_sync_batch("ent-views-1")
    assert cdc_batch.row_count == len(entry.key_fields)
    prem_row = next(
        r for r in cdc_batch.records if r.field_path == "fields.key_fields.premium"
    )
    assert prem_row.numeric_value == "1200000.00"
    assert prem_row.page_number == 4
    assert "ICEBERG" in BIGQUERY_ICEBERG_DDL
    assert set(SQL_VIEW_DEFINITIONS.keys()) == {
        "v_broker_placement",
        "v_claims_coverage",
        "v_underwriter_exposure",
    }


def test_zone5_and_zone6_a2ui_delivery_and_knowledge_catalog_governance() -> None:
    """Zone 5 & 6: Knowledge Catalog lineage + A2UI delivery to Gemini App vs Custom Web."""
    vault = InMemoryAuditVaultStore()
    store = InMemoryOperationalStore()
    catalog = InMemoryKnowledgeCatalog()

    t0 = datetime(2026, 3, 1, 10, 0, tzinfo=UTC)
    t1 = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
    eff0 = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)

    # Ingest initial quote and a bound policy with updated premium to trigger conflict + timeline
    quote_entry = _make_accepted_entry(
        doc_id="doc-a2ui-quote",
        doctype="carrier-quote",
        recorded_at=t0,
        effective_from=eff0,
    )
    ingest_accepted_submission(
        quote_entry,
        entity_id="ent-a2ui-1",
        context_id="ctx-a2ui-1",
        raw_document_bytes=b"%PDF-1.7 quote v1",
        version=1,
        vault_store=vault,
        operational_store=store,
        knowledge_catalog=catalog,
        recorded_at=t0,
        effective_from=eff0,
    )

    policy_fields = dict(quote_entry.key_fields)
    policy_fields["premium"] = "$1,150,000.00"
    policy_entry = _make_accepted_entry(
        doc_id="doc-a2ui-policy",
        doctype="bound-policy",
        key_fields=policy_fields,
        recorded_at=t1,
        effective_from=eff0,
    )
    ingest_accepted_submission(
        policy_entry,
        entity_id="ent-a2ui-1",
        context_id="ctx-a2ui-1",
        raw_document_bytes=b"%PDF-1.7 bound policy v2",
        version=2,
        vault_store=vault,
        operational_store=store,
        knowledge_catalog=catalog,
        recorded_at=t1,
        effective_from=eff0,
    )

    # 1. Verify Knowledge Catalog end-to-end lineage from PDF page -> A2UI widget
    trace = catalog.trace_lineage("ent-a2ui-1", "premium")
    assert trace is not None
    assert trace.current_value == "$1,150,000.00"
    assert trace.source_anchor is not None
    assert trace.source_anchor.page_number == 4
    zones_in_trace = [node.zone for node in trace.nodes]
    assert zones_in_trace == [
        AssetLocationZone.INBOUND,
        AssetLocationZone.AUDIT_VAULT,
        AssetLocationZone.OPERATIONAL_STORE,
        AssetLocationZone.BUSINESS_VIEW,
        AssetLocationZone.BIGQUERY_ICEBERG,
        AssetLocationZone.USER_INTERACTION_A2UI,
    ]

    # 2. Internal Gemini App delivery for Broker role
    internal_env = render_domain_agent_a2ui(
        "ent-a2ui-1",
        role="broker",
        channel="gemini_app",
        operational_store=store,
        knowledge_catalog=catalog,
    )
    assert internal_env.channel == "gemini_app"
    assert internal_env.governance.allowed is True
    widget_types_internal = {w.widget_type for w in internal_env.widgets}
    assert "metric_card" in widget_types_internal
    assert "comparison_table" in widget_types_internal
    assert "time_travel_timeline" in widget_types_internal
    assert "conflict_alert" in widget_types_internal
    metric_widget_int = next(
        w for w in internal_env.widgets if w.widget_type == "metric_card"
    )
    assert any(
        link.viewer_href.startswith("vault://") and "#page=4" in link.viewer_href
        for link in metric_widget_int.source_links
    )

    # 3. External Custom Web delivery for Client role (security-isolated!)
    external_env = render_domain_agent_a2ui(
        "ent-a2ui-1",
        role="client",
        channel="custom_web",
        operational_store=store,
        knowledge_catalog=catalog,
    )
    assert external_env.channel == "custom_web"
    widget_types_external = {w.widget_type for w in external_env.widgets}
    assert "metric_card" in widget_types_external
    assert "comparison_table" in widget_types_external
    # Internal conflict alerts MUST NOT leak to external Custom Web clients
    assert "conflict_alert" not in widget_types_external
    metric_widget_ext = next(
        w for w in external_env.widgets if w.widget_type == "metric_card"
    )
    assert all(
        link.viewer_href.startswith("/portal/source-pdf/ent-a2ui-1/")
        for link in metric_widget_ext.source_links
    )

    # 4. Governance blocks unauthorized channel/role/view combinations
    decision_blocked = authorize_access(
        role="client",
        channel="gemini_app",
        target_view="v_broker_placement",
    )
    assert decision_blocked.allowed is False

    # 5. FastAPI router endpoints serve both A2UI and Knowledge Catalog
    app = build_user_interaction_app(
        operational_store=store,
        knowledge_catalog=catalog,
    )
    client = TestClient(app)

    resp_ok = client.get("/v1/a2ui/gemini_app/ent-a2ui-1?role=broker")
    assert resp_ok.status_code == 200
    assert resp_ok.json()["channel"] == "gemini_app"

    resp_forbidden = client.get(
        "/v1/a2ui/custom_web/ent-a2ui-1?role=carrier&view=v_underwriter_exposure"
    )
    assert resp_forbidden.status_code == 403

    resp_lineage = client.get("/v1/catalog/lineage/ent-a2ui-1/premium")
    assert resp_lineage.status_code == 200
    assert resp_lineage.json()["source_anchor"]["page_number"] == 4

    resp_assets = client.get("/v1/catalog/assets?external_safe_only=true")
    assert resp_assets.status_code == 200
    assert all(
        a["classification"] in ("public", "client_shared")
        for a in resp_assets.json()
    )


def test_best_practice_p0_stateless_gcp_vault_and_catalog_hydration(
    tmp_path: Path,
) -> None:
    """P0 #3: Stateless Cloud Run replicas reconstruct VaultRecord & LineageTrace without in-process memory."""
    from bridge.vault import GcpAuditVaultStore

    class _FakeBlob:
        def __init__(self, name: str) -> None:
            self.name = name
            self.metadata: dict[str, str] | None = None
            self.data: bytes = b""
            self.content_type: str = "application/pdf"

        def exists(self) -> bool:
            return bool(self.data)

        def reload(self) -> None:
            pass

        def upload_from_string(
            self,
            data: bytes,
            *,
            content_type: str = "application/pdf",
            if_generation_match: int | None = None,
        ) -> None:
            if if_generation_match == 0 and self.data:
                raise RuntimeError("PreconditionFailed: generation match 0")
            self.data = data
            self.content_type = content_type

        def download_as_bytes(self) -> bytes:
            if not self.data:
                raise RuntimeError("NotFound")
            return self.data

    class _FakeBucket:
        def __init__(self) -> None:
            self.blobs: dict[str, _FakeBlob] = {}

        def blob(self, key: str) -> _FakeBlob:
            if key not in self.blobs:
                self.blobs[key] = _FakeBlob(key)
            return self.blobs[key]

        def list_blobs(self, prefix: str = "") -> list[_FakeBlob]:
            return [
                b
                for k, b in sorted(self.blobs.items())
                if k.startswith(prefix) and b.data
            ]

    class _FakeClient:
        def __init__(self, bucket: _FakeBucket) -> None:
            self._bucket = bucket

        def bucket(self, _name: str) -> _FakeBucket:
            return self._bucket

    shared_bucket = _FakeBucket()
    replica_a_vault = GcpAuditVaultStore(
        "aon-audit-vault", client=_FakeClient(shared_bucket)
    )
    replica_b_vault = GcpAuditVaultStore(
        "aon-audit-vault", client=_FakeClient(shared_bucket)
    )

    entry = _make_accepted_entry(doc_id="doc-stateless-1")
    promote_accepted_to_vault(
        replica_a_vault,
        context_id="ctx-stateless-1",
        entry=entry,
        data=b"%PDF-1.7 stateless cloud run bytes",
        filename="quote.pdf",
        version=1,
    )

    # Replica B has an empty _index dict but reconstructs from GCS blob metadata
    assert replica_b_vault._index == {}
    rec_b = replica_b_vault.get_record("ctx-stateless-1", "doc-stateless-1", version=1)
    assert rec_b is not None
    assert rec_b.doc_id == "doc-stateless-1"
    assert rec_b.version == 1
    assert rec_b.retention_locked is True
    assert (
        replica_b_vault.read_bytes("ctx-stateless-1", "doc-stateless-1", version=1)
        == b"%PDF-1.7 stateless cloud run bytes"
    )
    listed = replica_b_vault.list_records("ctx-stateless-1")
    assert len(listed) == 1
    assert listed[0].vault_uri == rec_b.vault_uri

    # Stateless KnowledgeCatalog hydrates 6-hop lineage from SqlOperationalStore on a cold replica
    db_file = tmp_path / "stateless_catalog.sqlite"
    sql_store = SqlOperationalStore(f"sqlite+aiosqlite:///{db_file}")
    try:
        sql_store.ingest_entry(
            entry,
            entity_id="ent-stateless-1",
            context_id="ctx-stateless-1",
            version=1,
        )
        cold_catalog = InMemoryKnowledgeCatalog(operational_store=sql_store)
        hydrated_trace = cold_catalog.trace_lineage("ent-stateless-1", "premium")
        assert hydrated_trace is not None
        assert hydrated_trace.current_value == "$1,200,000.00"
        assert hydrated_trace.source_anchor is not None
        assert hydrated_trace.source_anchor.page_number == 4
        assert len(hydrated_trace.nodes) == 6
    finally:
        sql_store.close()


def test_best_practice_p0_a2ui_principal_auth_and_role_spoofing_protection() -> None:
    """P0 #2: Authenticated principal role cannot be spoofed via ?role= query param."""
    store = InMemoryOperationalStore()
    catalog = InMemoryKnowledgeCatalog(operational_store=store)
    entry = _make_accepted_entry(doc_id="doc-auth-1")
    store.ingest_entry(entry, entity_id="ent-auth-1", context_id="ctx-auth-1")

    app = build_user_interaction_app(
        operational_store=store,
        knowledge_catalog=catalog,
        require_auth=True,
    )
    client = TestClient(app)

    # 1. Without authenticated principal header when require_auth=True -> 401
    resp_unauth = client.get("/v1/a2ui/gemini_app/ent-auth-1?role=broker")
    assert resp_unauth.status_code == 401

    # 2. Authenticated external client attempting to spoof ?role=broker -> 403
    resp_spoof = client.get(
        "/v1/a2ui/gemini_app/ent-auth-1?role=broker",
        headers={"X-Bridge-Role": "client"},
    )
    assert resp_spoof.status_code == 403

    # 3. Authenticated internal broker email via IAP header succeeds
    resp_iap = client.get(
        "/v1/a2ui/gemini_app/ent-auth-1",
        headers={
            "X-Goog-Authenticated-User-Email": "accounts.google.com:jane.broker@aon.com"
        },
    )
    assert resp_iap.status_code == 200
    assert resp_iap.json()["role"] == "broker"


def test_best_practice_p0_live_collector_and_server_mounts() -> None:
    """P0 #1: Collector.collect redacts PII + promotes accepted docs; dashboard & portal mount /v1/a2ui."""
    import asyncio

    from bridge.collect import Collector, DocumentSubmission
    from bridge.dashboard.server import build_dashboard_app
    from bridge.portal.server import build_portal_app
    from bridge.portal.store import InMemoryPortalStore
    from contract.wire import CollectRequest

    class _FakeExtractor:
        async def extract(self, _artifact_name: str) -> ExtractionResult:
            return ExtractionResult(
                fields=ExtractionFields(
                    doctype="w9",
                    issuer="Acme Corp",
                    key_fields={
                        "tin": "12-3456789",
                        "member_ssn": "123-45-6789",
                        "premium": "$500,000.00",
                    },
                ),
                overall_confidence=0.98,
                legible=True,
            )

    vault = InMemoryAuditVaultStore()
    op_store = InMemoryOperationalStore()
    catalog = InMemoryKnowledgeCatalog(operational_store=op_store)

    collector = Collector(
        _FakeExtractor(),
        vault_store=vault,
        operational_store=op_store,
        knowledge_catalog=catalog,
    )

    turn = asyncio.run(
        collector.collect(
            CollectRequest(
                process="vendor-onboarding",
                context_id="ctx-live-1",
            ),
            documents=[
                DocumentSubmission(
                    doc_id="doc-live-1",
                    artifact_name="w9.pdf",
                )
            ],
            candidate_doctypes=["w9"],
        )
    )
    assert len(turn.status.entries) == 1
    promoted_entry = turn.status.entries[0]
    assert promoted_entry.disposition == Disposition.accepted
    # PII was deterministically redacted before LedgerEntry creation & promotion
    assert promoted_entry.key_fields["member_ssn"] == "[REDACTED-PII]"
    assert promoted_entry.vault_uri is not None
    # Promoted into OperationalStore and KnowledgeCatalog automatically
    b_view = op_store.broker_view("ctx-live-1")
    assert b_view.financials.total_premium == "500000.00"
    assert catalog.trace_lineage("ctx-live-1", "premium") is not None

    # Verify both Dashboard and Portal servers mount /v1/a2ui and /v1/catalog
    portal_store = InMemoryPortalStore()
    dash_app = build_dashboard_app(
        store=portal_store,
        operational_store=op_store,
        knowledge_catalog=catalog,
    )
    dash_client = TestClient(dash_app)
    assert (
        dash_client.get("/v1/a2ui/gemini_app/ctx-live-1?role=broker").status_code
        == 200
    )

    portal_app = build_portal_app(
        store=portal_store,
        operational_store=op_store,
        knowledge_catalog=catalog,
    )
    portal_client = TestClient(portal_app)
    assert (
        portal_client.get("/v1/a2ui/custom_web/ctx-live-1?role=client").status_code
        == 200
    )


def test_best_practice_p1_watermarked_cdc_and_windowed_sql_views(
    tmp_path: Path,
) -> None:
    """P1 #4: Incremental BigQuery CDC watermark + O(N) ROW_NUMBER() SQL views."""
    db_file = tmp_path / "cdc_watermark.sqlite"
    store = SqlOperationalStore(f"sqlite+aiosqlite:///{db_file}")
    try:
        t1 = datetime(2026, 3, 1, 10, 0, tzinfo=UTC)
        t2 = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
        eff = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)

        store.ingest_entry(
            _make_accepted_entry(doc_id="doc-cdc-1", recorded_at=t1, effective_from=eff),
            entity_id="ent-cdc-1",
            context_id="ctx-cdc-1",
            recorded_at=t1,
            effective_from=eff,
            version=1,
        )
        batch1 = store.export_bigquery_sync_batch("ent-cdc-1")
        assert batch1.row_count == 7
        assert batch1.watermark_recorded_at is not None

        store.ingest_entry(
            _make_accepted_entry(
                doc_id="doc-cdc-2",
                key_fields={"premium": "$1,300,000.00"},
                recorded_at=t2,
                effective_from=eff,
            ),
            entity_id="ent-cdc-1",
            context_id="ctx-cdc-1",
            recorded_at=t2,
            effective_from=eff,
            version=2,
        )
        # Incremental CDC sync after t1 returns ONLY the 1 new fact version from t2
        batch2 = store.export_bigquery_sync_batch(
            "ent-cdc-1", since_recorded_at=t1
        )
        assert batch2.row_count == 1
        assert batch2.records[0].doc_id == "doc-cdc-2"
        assert batch2.watermark_recorded_at == t2

        # Verify SQL views use ROW_NUMBER() OVER (PARTITION BY ...) instead of correlated NOT EXISTS
        for view_sql in SQL_VIEW_DEFINITIONS.values():
            assert "ROW_NUMBER() OVER" in view_sql
            assert "WHERE rn = 1" in view_sql
    finally:
        store.close()


def test_best_practice_p1_chunking_and_docai_source_anchors() -> None:
    """P1 #5: merge_window_results rebases SourceAnchor pages; docai extracts bounding boxes."""
    from types import SimpleNamespace

    from bridge.extraction.chunking import merge_window_results
    from bridge.extraction.docai import extract_docai_source_anchors

    r1 = ExtractionResult(
        fields=ExtractionFields(
            doctype="carrier-quote",
            key_fields={"coverage_limit": "$10,000,000"},
            source_anchors={
                "coverage_limit": SourceAnchor(
                    page_number=2,
                    bounding_box=(0.1, 0.2, 0.5, 0.3),
                    snippet="Limit: $10,000,000",
                )
            },
        ),
        overall_confidence=0.9,
        legible=True,
    )
    r2 = ExtractionResult(
        fields=ExtractionFields(
            doctype="carrier-quote",
            key_fields={"premium": "$1,200,000"},
            source_anchors={
                # Page 3 inside window 2 (pages 11..20) -> rebased to page 13!
                "premium": SourceAnchor(
                    page_number=3,
                    bounding_box=(0.2, 0.4, 0.6, 0.5),
                    snippet="Premium: $1,200,000",
                )
            },
        ),
        overall_confidence=0.95,
        legible=True,
    )
    merged = merge_window_results([(1, 10, r1), (11, 20, r2)])
    assert merged.fields.source_anchors["coverage_limit"].page_number == 2
    assert merged.fields.source_anchors["premium"].page_number == 13

    # Fake Document AI document with entity page_anchor
    fake_doc = SimpleNamespace(
        entities=[
            SimpleNamespace(
                type_="Annual Premium",
                mention_text="$1,200,000.00",
                page_anchor=SimpleNamespace(
                    page_refs=[
                        SimpleNamespace(
                            page=3,  # 0-based in DocAI -> 1-based page 4
                            bounding_poly=SimpleNamespace(
                                normalized_vertices=[
                                    SimpleNamespace(x=0.10, y=0.20),
                                    SimpleNamespace(x=0.50, y=0.20),
                                    SimpleNamespace(x=0.50, y=0.28),
                                    SimpleNamespace(x=0.10, y=0.28),
                                ]
                            ),
                        )
                    ]
                ),
            )
        ],
        pages=[],
    )
    anchors = extract_docai_source_anchors(
        fake_doc, artifact_uri="gs://vault/quote.pdf"
    )
    assert "Annual Premium" in anchors
    assert anchors["Annual Premium"].page_number == 4
    assert anchors["Annual Premium"].bounding_box == (0.10, 0.20, 0.50, 0.28)
    assert anchors["Annual Premium"].artifact_uri == "gs://vault/quote.pdf"


def test_best_practice_p1_p2_a2ui_no_pixel_fields_and_terraform_wiring() -> None:
    """P1 #6 & P2 #7: A2UI models have zero pixel/style fields and Terraform root wires Audit Vault."""
    from bridge.a2ui_inbox import (
        A2aDataPart,
        A2uiClientActionRequest,
        ActionCitation,
        ActionInboxItem,
        ActionProgressStep,
        InboxAuditRecord,
        Rfc6902PatchOp,
    )
    from bridge.user_interaction import (
        A2UI_CONTENT_TYPE,
        A2uiDeliveryEnvelope,
        A2uiSourceLink,
        A2uiWidget,
        encode_a2ui_envelope,
    )

    pixel_words = {"style", "color", "css", "width", "height", "pixel", "font"}
    for model_cls in (
        A2uiDeliveryEnvelope,
        A2uiWidget,
        A2uiSourceLink,
        ActionCitation,
        ActionProgressStep,
        A2aDataPart,
        Rfc6902PatchOp,
        InboxAuditRecord,
        ActionInboxItem,
        A2uiClientActionRequest,
    ):
        for field_name in model_cls.model_fields:
            tokens = set(field_name.lower().split("_"))
            assert not (tokens & pixel_words), (
                f"{model_cls.__name__}.{field_name} contains pixel/style word"
            )

    store = InMemoryOperationalStore()
    catalog = InMemoryKnowledgeCatalog(operational_store=store)
    store.ingest_entry(
        _make_accepted_entry(doc_id="doc-wire-1"),
        entity_id="ent-wire-1",
        context_id="ctx-wire-1",
    )
    env = render_domain_agent_a2ui(
        "ent-wire-1",
        role="broker",
        channel="gemini_app",
        operational_store=store,
        knowledge_catalog=catalog,
    )
    assert A2UI_CONTENT_TYPE == "application/vnd.a2ui+json"
    encoded = encode_a2ui_envelope(env)
    assert encoded["channel"] == "gemini_app"

    tf_root = Path(__file__).resolve().parents[1] / "terraform"
    assert "enable_audit_vault" in (tf_root / "variables.tf").read_text()
    assert "lock_audit_vault_retention" in (tf_root / "variables.tf").read_text()
    assert "audit_vault_bucket_name" in (tf_root / "main.tf").read_text()
    assert "audit_vault_bucket_name" in (tf_root / "outputs.tf").read_text()
    assert "BRIDGE_VAULT_BUCKET" in (
        tf_root / "modules" / "runtime" / "main.tf"
    ).read_text()


def test_a2ui_v09_action_progress_monitoring_and_adk_agent_inbox() -> None:
    """Zone 5 Extension: End-user action progress monitoring + ADK HITL Inbox (A2UI v0.9)."""
    from google.adk.flows.llm_flows.functions import (
        REQUEST_CONFIRMATION_FUNCTION_CALL_NAME,
    )

    from bridge.a2ui_inbox import (
        A2UI_V09_MIME_TYPE,
        INBOX_CATALOG_ID,
        PORTAL_CATALOG_ID,
        InMemoryActionInboxStore,
        request_a2ui_inbox_confirmation,
    )

    op_store = InMemoryOperationalStore()
    catalog = InMemoryKnowledgeCatalog(operational_store=op_store)
    inbox_store = InMemoryActionInboxStore()

    t1 = datetime(2026, 3, 1, 10, 0, tzinfo=UTC)
    t2 = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
    eff = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)

    # Seed two conflicting submissions so the action tracker has both page anchors & discrepancies
    op_store.ingest_entry(
        _make_accepted_entry(doc_id="doc-prog-v1", recorded_at=t1, effective_from=eff),
        entity_id="ent-prog-1",
        context_id="ctx-prog-1",
        recorded_at=t1,
        effective_from=eff,
        version=1,
    )
    v2_fields = dict(_make_accepted_entry().key_fields)
    v2_fields["premium"] = "$1,140,000.00"
    op_store.ingest_entry(
        _make_accepted_entry(
            doc_id="doc-prog-v2",
            key_fields=v2_fields,
            recorded_at=t2,
            effective_from=eff,
        ),
        entity_id="ent-prog-1",
        context_id="ctx-prog-1",
        recorded_at=t2,
        effective_from=eff,
        version=2,
    )

    app = build_user_interaction_app(
        operational_store=op_store,
        knowledge_catalog=catalog,
        inbox_store=inbox_store,
    )
    client = TestClient(app)

    # 1. Verify governed A2UI v0.9 catalog schemas
    cat_ent = client.get("/v1/a2ui/catalogs/enterprise_inbox_catalog.json")
    assert cat_ent.status_code == 200
    assert "ActionProgressCard" in cat_ent.json()["components"]
    assert "FactConflictTable" in cat_ent.json()["components"]

    cat_portal = client.get("/v1/a2ui/catalogs/portal_customer_catalog.json")
    assert cat_portal.status_code == 200
    assert "ActionProgressCard" in cat_portal.json()["components"]
    assert "FactConflictTable" not in cat_portal.json()["components"]

    # 2. Create a tracked action (starts in `input-required` due to the premium discrepancy,
    #    with a $50,000 transfer authority cap to test server-side bounds checking)
    create_resp = client.post(
        "/v1/a2ui/inbox/actions?channel=gemini_app&role=broker",
        json={
            "entity_id": "ent-prog-1",
            "title": "Bind Carrier Placement & Premium Release",
            "external_title": "Your Policy Placement Progress",
            "hint": "Premium discrepancy detected between doc-prog-v1 and doc-prog-v2.",
            "external_hint": "Step 4 of 5: Finalizing policy premium confirmation.",
            "max_approval_amount": 50000.0,
            "data_model": {
                "transfer": {
                    "requestedAmount": 42500.0,
                    "approvedAmount": 42500.0,
                }
            },
        },
    )
    assert create_resp.status_code == 200
    created = create_resp.json()
    item_id = created["item_id"]
    assert created["task_state"] == "input-required"
    assert created["a2a_data_part"]["mime_type"] == A2UI_V09_MIME_TYPE
    internal_msgs = created["a2a_data_part"]["data"]
    assert len(internal_msgs) == 3
    assert internal_msgs[0]["createSurface"]["catalogId"] == INBOX_CATALOG_ID
    internal_comp_types = {
        c["component"] for c in internal_msgs[1]["updateComponents"]["components"]
    }
    assert "ActionProgressCard" in internal_comp_types
    assert "ApprovalCard" in internal_comp_types
    assert "FactConflictTable" in internal_comp_types
    assert "SourceCitationPill" in internal_comp_types
    assert (
        internal_msgs[2]["updateDataModel"]["value"]["progress"]["percent"] == 60
    )

    # 3. External end-user (`custom_web` + `client`) monitors the SAME action:
    #    receives `portal_customer_catalog.json`, external-safe step labels, and NO conflict table!
    ext_get = client.get(
        f"/v1/a2ui/inbox/items/{item_id}?channel=custom_web&role=client"
    )
    assert ext_get.status_code == 200
    ext_body = ext_get.json()
    assert ext_body["hint"] == "Step 4 of 5: Finalizing policy premium confirmation."
    ext_msgs = ext_body["a2a_data_part"]["data"]
    assert ext_msgs[0]["createSurface"]["catalogId"] == PORTAL_CATALOG_ID
    ext_comp_types = {
        c["component"] for c in ext_msgs[1]["updateComponents"]["components"]
    }
    assert "ActionProgressCard" in ext_comp_types
    assert "FactConflictTable" not in ext_comp_types
    assert "ApprovalCard" not in ext_comp_types
    ext_data_val = ext_msgs[2]["updateDataModel"]["value"]
    assert "discrepancy" not in ext_data_val
    assert (
        ext_data_val["progress"]["steps"][0]["label"]
        == "Step 1: Submission Received & Security Checked"
    )

    # 4. Verify ADK ToolContext.request_confirmation integration
    recorded_confirmations: list[dict] = []

    class _FakeToolContext:
        def request_confirmation(self, *, hint: str, payload: dict) -> None:
            recorded_confirmations.append({"hint": hint, "payload": payload})

    item_obj = inbox_store.get_item(item_id)
    assert item_obj is not None
    conf_res = request_a2ui_inbox_confirmation(
        _FakeToolContext(), item=item_obj, role="broker", channel="gemini_app"
    )
    assert conf_res["status"] == "PENDING_INBOX_APPROVAL"
    assert len(recorded_confirmations) == 1
    assert (
        recorded_confirmations[0]["payload"]["mime_type"] == A2UI_V09_MIME_TYPE
    )

    # 5. Advance a step and stream live A2UI v0.9 SSE progress frames
    prog_resp = client.post(
        f"/v1/a2ui/inbox/items/{item_id}/progress",
        json={
            "step_id": "step_consolidation_gate",
            "status": "input-required",
            "detail": "Waiting on broker resolution of premium conflict.",
        },
    )
    assert prog_resp.status_code == 200
    assert "updateDataModel" in prog_resp.json()["delta"]

    sse_resp = client.get(
        f"/v1/a2ui/inbox/items/{item_id}/events?channel=gemini_app&role=broker"
    )
    assert sse_resp.status_code == 200
    assert sse_resp.headers["content-type"].startswith("text/event-stream")
    assert "event: a2ui.surface" in sse_resp.text
    assert "event: a2ui.delta" in sse_resp.text

    # 6. Governance Verb #5 (Escalate): keeps task in `input-required` and reassigns role
    esc_resp = client.post(
        f"/v1/a2ui/inbox/items/{item_id}/action?channel=gemini_app&role=broker",
        json={
            "version": "v0.9",
            "action": {
                "name": "escalate",
                "context": {
                    "decision": "escalate",
                    "targetRole": "underwriter",
                    "review": {"memo": "Escalating discrepancy to senior underwriter."},
                },
            },
        },
    )
    assert esc_resp.status_code == 200
    assert esc_resp.json()["task_state"] == "input-required"
    assert esc_resp.json()["escalated_to"] == "underwriter"

    # 7. Server-side bounds verification rejects an approvedAmount > max_approval_amount (422)
    over_limit_resp = client.post(
        f"/v1/a2ui/inbox/items/{item_id}/action?channel=gemini_app&role=underwriter",
        json={
            "version": "v0.9",
            "action": {
                "name": "edit_and_approve",
                "context": {
                    "decision": "edit",
                    "transfer": {"approvedAmount": 75000.0},
                },
            },
        },
    )
    assert over_limit_resp.status_code == 422

    # 8. Governance Verb #2 (Edit & Approve): human edits `/transfer/approvedAmount` & `/resolution/selectedValue`,
    #    computing RFC 6902 JSON Patch diff, completing all 5 steps (100%), and emitting ADK FunctionResponse
    approve_resp = client.post(
        f"/v1/a2ui/inbox/items/{item_id}/action?channel=gemini_app&role=underwriter",
        json={
            "version": "v0.9",
            "action": {
                "name": "edit_and_approve",
                "context": {
                    "decision": "edit",
                    "resolution": {"selectedValue": "$1,140,000.00"},
                    "transfer": {"approvedAmount": 38000.0},
                    "review": {"memo": "Verified v2 carrier quote on page 4."},
                },
            },
        },
    )
    assert approve_resp.status_code == 200
    resolved = approve_resp.json()
    assert resolved["task_state"] == "completed"
    assert resolved["decision"] == "edit"
    patch_paths = {p["path"] for p in resolved["patches"]}
    assert "/transfer/approvedAmount" in patch_paths
    assert (
        resolved["adk_function_response"]["name"]
        == REQUEST_CONFIRMATION_FUNCTION_CALL_NAME
    )
    assert resolved["adk_function_response"]["response"]["confirmed"] is True
    assert (
        resolved["progress_delta_msg"]["updateDataModel"]["value"]["percent"]
        == 100
    )
    assert "deleteSurface" in resolved["resolved_surface_msg"]

    # Verify immutable audit trail captured both Escalate and Edit & Approve
    audits = inbox_store.list_audit_records(item_id)
    assert len(audits) == 2
    assert [a.decision for a in audits] == ["escalate", "edit"]


def test_spiffe_svid_obo_and_multi_tenant_isolation() -> None:
    """Verify SPIFFE JWT-SVID workload identity, Shadow AI blocking, OBO compound
    identity, and row-level client/carrier tenant isolation across Zone 5 & Zone 6."""
    from bridge.a2ui_inbox import InMemoryActionInboxStore
    from bridge.identity import mint_jwt_svid

    vault = InMemoryAuditVaultStore()
    store = InMemoryOperationalStore()
    catalog = InMemoryKnowledgeCatalog(operational_store=store)
    inbox_store = InMemoryActionInboxStore()

    # Ingest two tenants: SUB-ACME-001 (with Chubb & AIG quotes) and SUB-GLOBEX-002
    acme_chubb = _make_accepted_entry(
        doc_id="doc-chubb-1",
        doctype="carrier-quote",
        issuer="Chubb",
        key_fields={
            "currency": "USD",
            "premium": "$1,200,000.00",
            "deductible": "$50,000.00",
            "coverage_limit": "$25,000,000.00",
            "total_insured_value": "$100,000,000.00",
        },
    )
    acme_aig = _make_accepted_entry(
        doc_id="doc-aig-1",
        doctype="carrier-quote",
        issuer="AIG",
        key_fields={
            "currency": "USD",
            "premium": "$1,350,000.00",
            "deductible": "$75,000.00",
            "coverage_limit": "$25,000,000.00",
            "total_insured_value": "$100,000,000.00",
        },
    )
    globex_quote = _make_accepted_entry(
        doc_id="doc-globex-1",
        doctype="carrier-quote",
        issuer="Zurich",
        key_fields={
            "currency": "USD",
            "premium": "$850,000.00",
            "deductible": "$25,000.00",
            "coverage_limit": "$15,000,000.00",
            "total_insured_value": "$60,000,000.00",
        },
    )
    for entry in (acme_chubb, acme_aig):
        ingest_accepted_submission(
            entry,
            entity_id="SUB-ACME-001",
            context_id="ctx-acme",
            vault_store=vault,
            operational_store=store,
            knowledge_catalog=catalog,
        )
    ingest_accepted_submission(
        globex_quote,
        entity_id="SUB-GLOBEX-002",
        context_id="ctx-globex",
        vault_store=vault,
        operational_store=store,
        knowledge_catalog=catalog,
    )

    app = build_user_interaction_app(
        operational_store=store,
        knowledge_catalog=catalog,
        inbox_store=inbox_store,
        require_auth=True,
    )
    client = TestClient(app)

    # 1. Zone 6 Knowledge Catalog exposes registered SPIFFE Agent Registry
    agents_resp = client.get("/v1/catalog/spiffe-agents?security_zone=internal")
    assert agents_resp.status_code == 200
    internal_spiffe_ids = {a["spiffe_id"] for a in agents_resp.json()}
    assert "spiffe://agents.docubridge.io/internal/broker-agent" in internal_spiffe_ids
    assert (
        "spiffe://agents.docubridge.io/internal/credit-officer-agent"
        in internal_spiffe_ids
    )
    assert (
        "spiffe://agents.docubridge.io/portal/external-portal-agent"
        not in internal_spiffe_ids
    )

    # 2. Valid signed SPIFFE JWT-SVID + OBO broker principal succeeds on gemini_app
    broker_svid = mint_jwt_svid(
        "spiffe://agents.docubridge.io/internal/broker-agent",
        obo_subject="usr:alice.broker@docubridge.io",
        obo_role="broker",
        max_approval_amount="50000.00",
    )
    ok_resp = client.get(
        "/v1/a2ui/gemini_app/SUB-ACME-001",
        headers={"X-Bridge-Spiffe-Svid": f"Bearer {broker_svid}"},
    )
    assert ok_resp.status_code == 200
    gov = ok_resp.json()["governance"]
    assert (
        gov["agent_spiffe_id"]
        == "spiffe://agents.docubridge.io/internal/broker-agent"
    )
    assert gov["svid_verified"] is True

    # 3. Tampered JWT-SVID signature is rejected with 401
    tampered_svid = broker_svid[:-4] + "XXXX"
    bad_sig_resp = client.get(
        "/v1/a2ui/gemini_app/SUB-ACME-001",
        headers={"X-Bridge-Spiffe-Svid": f"Bearer {tampered_svid}"},
    )
    assert bad_sig_resp.status_code == 401

    # 4. Unregistered Shadow AI SPIFFE ID is blocked with 403 (ShadowAgentError)
    shadow_svid = mint_jwt_svid(
        "spiffe://agents.docubridge.io/internal/shadow-rogue-agent",
        obo_subject="usr:alice.broker@docubridge.io",
        obo_role="broker",
    )
    shadow_resp = client.get(
        "/v1/a2ui/gemini_app/SUB-ACME-001",
        headers={"X-Bridge-Spiffe-Svid": f"Bearer {shadow_svid}"},
    )
    assert shadow_resp.status_code == 403
    assert "Shadow AI" in shadow_resp.json()["detail"]

    # 5. Least-privilege intersection: External portal SPIFFE agent cannot call gemini_app
    portal_agent_cross_zone = mint_jwt_svid(
        "spiffe://agents.docubridge.io/portal/external-portal-agent",
        obo_subject="usr:client.acme",
        obo_role="client",
        tenant_id="acme",
        allowed_entities=("SUB-ACME-001",),
    )
    cross_zone_resp = client.get(
        "/v1/a2ui/gemini_app/SUB-ACME-001",
        headers={"X-Bridge-Spiffe-Svid": f"Bearer {portal_agent_cross_zone}"},
    )
    assert cross_zone_resp.status_code == 403

    # 6. Multi-Tenant Row-Level Isolation for external `client` on `custom_web`
    acme_client_ok = client.get(
        "/v1/a2ui/custom_web/SUB-ACME-001",
        headers={"X-Bridge-Spiffe-Svid": f"Bearer {portal_agent_cross_zone}"},
    )
    assert acme_client_ok.status_code == 200

    acme_client_cross_tenant = client.get(
        "/v1/a2ui/custom_web/SUB-GLOBEX-002",
        headers={"X-Bridge-Spiffe-Svid": f"Bearer {portal_agent_cross_zone}"},
    )
    assert acme_client_cross_tenant.status_code == 403
    assert "Tenant isolation violation" in acme_client_cross_tenant.json()["detail"]

    # 7. Carrier Affiliation Isolation: Chubb carrier only sees Chubb quote, never AIG quote
    chubb_carrier_svid = mint_jwt_svid(
        "spiffe://agents.docubridge.io/portal/external-portal-agent",
        obo_subject="usr:underwriter@chubb.com",
        obo_role="carrier",
        carrier_slug="chubb",
        allowed_entities=("SUB-ACME-001",),
    )
    chubb_resp = client.get(
        "/v1/a2ui/custom_web/SUB-ACME-001",
        headers={"X-Bridge-Spiffe-Svid": f"Bearer {chubb_carrier_svid}"},
    )
    assert chubb_resp.status_code == 200
    table_widget = next(
        w
        for w in chubb_resp.json()["widgets"]
        if w["widget_type"] == "comparison_table"
    )
    returned_issuers = [q["issuer"] for q in table_widget["data"]["quotes"]]
    assert returned_issuers == ["Chubb"]

    # 8. Compound Audit Trail & Principal Approval Limit in A2UI Inbox
    create_acme = client.post(
        "/v1/a2ui/inbox/actions?channel=gemini_app",
        headers={"X-Bridge-Spiffe-Svid": f"Bearer {broker_svid}"},
        json={
            "entity_id": "SUB-ACME-001",
            "pause_for_review": True,
            "data_model": {"transfer": {"approvedAmount": 40000.0}},
        },
    )
    assert create_acme.status_code == 200
    acme_item_id = create_acme.json()["item_id"]

    # Exceeding the OBO principal's max_approval_amount ($50,000) is rejected with 422
    exceed_principal_limit = client.post(
        f"/v1/a2ui/inbox/items/{acme_item_id}/action?channel=gemini_app",
        headers={"X-Bridge-Spiffe-Svid": f"Bearer {broker_svid}"},
        json={
            "version": "v0.9",
            "action": {
                "name": "edit_and_approve",
                "context": {
                    "decision": "edit",
                    "transfer": {"approvedAmount": 60000.0},
                },
            },
        },
    )
    assert exceed_principal_limit.status_code == 422

    # Approving within limit logs both human `actor_principal` and `agent_spiffe_id`
    approve_ok = client.post(
        f"/v1/a2ui/inbox/items/{acme_item_id}/action?channel=gemini_app",
        headers={"X-Bridge-Spiffe-Svid": f"Bearer {broker_svid}"},
        json={
            "version": "v0.9",
            "action": {
                "name": "edit_and_approve",
                "context": {
                    "decision": "edit",
                    "transfer": {"approvedAmount": 45000.0},
                    "review": {"memo": "Approved under SPIFFE OBO identity."},
                },
            },
        },
    )
    assert approve_ok.status_code == 200
    assert (
        approve_ok.json()["agent_spiffe_id"]
        == "spiffe://agents.docubridge.io/internal/broker-agent"
    )
    audit_records = inbox_store.list_audit_records(acme_item_id)
    assert len(audit_records) == 1
    assert audit_records[0].actor_principal == "usr:alice.broker@docubridge.io"
    assert (
        audit_records[0].agent_spiffe_id
        == "spiffe://agents.docubridge.io/internal/broker-agent"
    )


def test_agent_gym_example_of_one_distillation_stress_testing_and_3_layers() -> None:
    """Verify Slide 3 Columns 1-3: 'Example of One' signal capture, Offline Agent Gym
    Synthetic Stress Testing + Quality Report Card Gate, and production improvements
    across Extraction, Consolidation ('Campala Ltd.' vs distinct syndicate), and A2UI Presentation."""
    from bridge.a2ui_inbox import InMemoryActionInboxStore
    from bridge.agent_gym import (
        InMemoryAgentGymEngine,
        TribalRuleCategory,
        TribalRuleStatus,
    )
    from bridge.identity import mint_jwt_svid

    vault = InMemoryAuditVaultStore()
    store = InMemoryOperationalStore()
    catalog = InMemoryKnowledgeCatalog(operational_store=store)
    inbox_store = InMemoryActionInboxStore()
    gym = InMemoryAgentGymEngine()

    app = build_user_interaction_app(
        operational_store=store,
        knowledge_catalog=catalog,
        inbox_store=inbox_store,
        agent_gym=gym,
    )
    client = TestClient(app)

    broker_svid = mint_jwt_svid(
        "spiffe://agents.docubridge.io/internal/broker-agent",
        obo_subject="usr:lead.broker@docubridge.io",
        obo_role="broker",
        tenant_id="tenant:acme",
        allowed_entities=("SUB-CAMPALA-001",),
        max_approval_amount="250000.00",
    )
    headers = {"X-Bridge-Spiffe-Svid": f"Bearer {broker_svid}"}

    # 1. Stage 1 (Smarter Doc Extraction): Distill a carrier extraction hint from a
    #    Zone 2 Review Quarantine fix ("Example of One")
    q_resp = client.post(
        "/v1/gym/signals/from-quarantine",
        headers=headers,
        json={
            "entity_id": "SUB-CAMPALA-001",
            "doc_id": "doc-lloyds-q1",
            "doctype": "quote",
            "issuer": "Lloyd's Syndicate 2987",
            "field_path": "deductible",
            "before_value": "$50,000.00",
            "after_value": "$125,000.00",
            "expert_memo": "Read policy deductible from Schedule B p. 7 instead of cover summary.",
            "page_number": 7,
        },
    )
    assert q_resp.status_code == 200
    ext_rule_id = q_resp.json()["candidate_rule"]["rule_id"]
    assert (
        q_resp.json()["candidate_rule"]["category"]
        == TribalRuleCategory.EXTRACTION_HINT.value
    )
    assert (
        q_resp.json()["candidate_rule"]["status"]
        == TribalRuleStatus.CANDIDATE.value
    )

    # Evaluate via Synthetic Stress Testing + Quality Report Card Gate -> auto-promotes
    eval_ext = client.post(
        f"/v1/gym/rules/{ext_rule_id}/evaluate",
        headers=headers,
    )
    assert eval_ext.status_code == 200
    report_ext = eval_ext.json()["report_card"]
    assert report_ext["passed_gate"] is True
    assert report_ext["math_firewall_intact"] is True
    assert report_ext["stress_cases_total"] == 4
    assert report_ext["stress_cases_passed"] == 4
    assert len(report_ext["historical_regressions"]) == 0
    assert (
        eval_ext.json()["rule"]["status"] == TribalRuleStatus.PROMOTED.value
    )

    # Promoted hint is now returned by Stage 1 Extraction Hints API
    hints_resp = client.get(
        "/v1/gym/extraction-hints?issuer=Lloyd's%20Syndicate%202987&doctype=quote"
    )
    assert hints_resp.status_code == 200
    assert len(hints_resp.json()) == 1
    assert hints_resp.json()[0]["rule_id"] == ext_rule_id

    # 2. Stage 2 (Cleaner Consolidation): Distill an Entity/Carrier Alias rule
    #    ("Campala Ltd." -> "Campala", while keeping "Campala Syndicate 1183" distinct)
    #    directly from a single Zone 5 Gemini App A2UI Inbox edit ("Teach Agent")!
    create_inb = client.post(
        "/v1/a2ui/inbox/actions?channel=gemini_app",
        headers=headers,
        json={
            "entity_id": "SUB-CAMPALA-001",
            "pause_for_review": True,
            "data_model": {
                "resolution": {"selectedValue": "Campala Ltd."},
            },
        },
    )
    assert create_inb.status_code == 200
    inb_item_id = create_inb.json()["item_id"]

    # Broker edits "Campala Ltd." -> "Campala" in the Gemini App A2UI Inbox
    act_resp = client.post(
        f"/v1/a2ui/inbox/items/{inb_item_id}/action?channel=gemini_app",
        headers=headers,
        json={
            "version": "v0.9",
            "action": {
                "name": "edit_and_approve",
                "context": {
                    "decision": "edit",
                    "resolution": {"selectedValue": "Campala"},
                    "review": {
                        "memo": (
                            "Teach Agent: Campala Ltd. is a legal suffix alias for Campala, "
                            "whereas Campala Syndicate 1183 is a separate syndicate."
                        )
                    },
                },
            },
        },
    )
    assert act_resp.status_code == 200

    distill_inb = client.post(
        f"/v1/gym/signals/from-inbox/{inb_item_id}",
        headers=headers,
        json={
            "doc_id": "doc-campala-q1",
            "doctype": "quote",
            "issuer": "Campala Ltd.",
            "field_path": "issuer",
            "category": "entity_alias",
            "distinct_entities": ["Campala Syndicate 1183"],
        },
    )
    assert distill_inb.status_code == 200
    alias_rule_id = distill_inb.json()["candidate_rule"]["rule_id"]

    eval_alias = client.post(
        f"/v1/gym/rules/{alias_rule_id}/evaluate",
        headers=headers,
    )
    assert eval_alias.status_code == 200
    assert eval_alias.json()["report_card"]["passed_gate"] is True

    # Verify Stage 2 Consolidation normalizes "Campala Ltd." -> "Campala"
    # while keeping "Campala Syndicate 1183" separate, and keeping Decimal math exact!
    e_campala_ltd = _make_accepted_entry(
        doc_id="doc-campala-ltd",
        doctype="carrier-quote",
        issuer="Campala Ltd.",
        key_fields={
            "currency": "USD",
            "premium": "$500,000.00",
            "deductible": "$50,000.00",
            "coverage_limit": "$5,000,000.00",
            "total_insured_value": "$10,000,000.00",
        },
    )
    e_syndicate = _make_accepted_entry(
        doc_id="doc-campala-syndicate",
        doctype="carrier-quote",
        issuer="Campala Syndicate 1183",
        key_fields={
            "currency": "USD",
            "premium": "$300,000.00",
            "deductible": "$25,000.00",
            "coverage_limit": "$5,000,000.00",
            "total_insured_value": "$10,000,000.00",
        },
    )
    res_1 = ingest_accepted_submission(
        e_campala_ltd,
        entity_id="SUB-CAMPALA-001",
        context_id="ctx-campala",
        vault_store=vault,
        operational_store=store,
        knowledge_catalog=catalog,
        agent_gym=gym,
    )
    res_2 = ingest_accepted_submission(
        e_syndicate,
        entity_id="SUB-CAMPALA-001",
        context_id="ctx-campala",
        vault_store=vault,
        operational_store=store,
        knowledge_catalog=catalog,
        agent_gym=gym,
    )
    assert res_1.entry.issuer == "Campala"
    assert res_2.entry.issuer == "Campala Syndicate 1183"

    # 3. Stage 3 (Tailored User Presentation): Distill & promote a presentation preference
    #    and verify it surfaces in the Gemini App A2UI widget!
    pres_q = client.post(
        "/v1/gym/signals/from-quarantine",
        headers=headers,
        json={
            "entity_id": "SUB-CAMPALA-001",
            "doc_id": "doc-campala-ltd",
            "doctype": "quote",
            "issuer": "Campala",
            "field_path": "deductible",
            "before_value": "collapsed",
            "after_value": "pre-highlight Schedule B p. 7",
            "expert_memo": "Always highlight deductible schedule page on broker view cards.",
            "category": "presentation_preference",
        },
    )
    assert pres_q.status_code == 200
    pres_rule_id = pres_q.json()["candidate_rule"]["rule_id"]
    client.post(f"/v1/gym/rules/{pres_rule_id}/evaluate", headers=headers)

    a2ui_resp = client.get(
        "/v1/a2ui/gemini_app/SUB-CAMPALA-001",
        headers=headers,
    )
    assert a2ui_resp.status_code == 200
    primary_widget = a2ui_resp.json()["widgets"][0]
    assert "applied_tribal_rules" in primary_widget["data"]
    assert primary_widget["data"]["applied_tribal_rules"][0]["rule_id"] == pres_rule_id

    # 4. Hallucination Firewall Gate: any rule attempting to override Decimal math is BLOCKED
    bad_q = client.post(
        "/v1/gym/signals/from-quarantine",
        headers=headers,
        json={
            "entity_id": "SUB-CAMPALA-001",
            "doc_id": "doc-bad",
            "doctype": "quote",
            "issuer": "Campala",
            "field_path": "premium",
            "before_value": "$500,000.00",
            "after_value": "$510,000.00",
            "expert_memo": "Use override_decimal_math and approximate_sum in LLM.",
        },
    )
    bad_rule_id = bad_q.json()["candidate_rule"]["rule_id"]
    bad_eval = client.post(
        f"/v1/gym/rules/{bad_rule_id}/evaluate",
        headers=headers,
    )
    assert bad_eval.status_code == 200
    assert bad_eval.json()["report_card"]["math_firewall_intact"] is False
    assert bad_eval.json()["report_card"]["passed_gate"] is False
    assert (
        bad_eval.json()["rule"]["status"] == TribalRuleStatus.REJECTED.value
    )


def test_agent_gym_programmable_trust_dial_and_instant_safety_dialback() -> None:
    """Verify Slide 3 Column 4: Programmable Trust graduated autonomy (Shadow Mode ->
    Guided Review -> Trusted Auto-Approve), Gemini App A2UI Trust Controls, and
    Instant Safety Dial-Back on carrier format drift."""
    from bridge.agent_gym import InMemoryAgentGymEngine, TrustAutonomyLevel
    from bridge.identity import mint_jwt_svid

    gym = InMemoryAgentGymEngine()
    app = build_user_interaction_app(agent_gym=gym)
    client = TestClient(app)

    lead_svid = mint_jwt_svid(
        "spiffe://agents.docubridge.io/internal/underwriter-agent",
        obo_subject="usr:lead.underwriter@docubridge.io",
        obo_role="underwriter",
    )
    headers = {"X-Bridge-Spiffe-Svid": f"Bearer {lead_svid}"}

    # 1. Inspect initial Programmable Trust policies & A2UI Trust Readiness surfaces
    list_resp = client.get("/v1/gym/trust-policies")
    assert list_resp.status_code == 200
    policies = {p["slice_id"]: p for p in list_resp.json()}
    aig_slice = policies["slice:aig:endorsement:MEDIUM"]
    assert aig_slice["autonomy_level"] == TrustAutonomyLevel.GUIDED_REVIEW.value
    assert aig_slice["consecutive_matches"] == 4
    assert aig_slice["ready_for_promotion"] is False
    assert len(aig_slice["a2ui_messages"]) == 3

    # 2. Premature promotion to Trusted Auto-Approve before 5 consecutive matches is blocked (422)
    early_dial = client.post(
        "/v1/gym/trust-policies/slice:aig:endorsement:MEDIUM/dial",
        headers=headers,
        json={
            "target_level": "trusted_auto_approve",
            "reason": "Trying to promote at 4/5 matches.",
        },
    )
    assert early_dial.status_code == 422

    # 3. Record 5th consecutive exact match in Shadow/Review comparison -> ready_for_promotion=True!
    shd_resp = client.post(
        "/v1/gym/trust-policies/slice:aig:endorsement:MEDIUM/shadow",
        json={
            "entity_id": "SUB-AIG-009",
            "doc_id": "doc-aig-end-5",
            "issuer": "AIG",
            "doctype": "endorsement",
            "field_path": "coverage_limit",
            "ai_value": "$15,000,000.00",
            "human_value": "$15,000,000.00",
            "risk_level": "MEDIUM",
        },
    )
    assert shd_resp.status_code == 200
    assert shd_resp.json()["policy"]["consecutive_matches"] == 5
    assert shd_resp.json()["ready_for_promotion"] is True

    # 4. Domain Lead promotes slice to `trusted_auto_approve` via Gemini App Trust Controls
    promote_resp = client.post(
        "/v1/gym/trust-policies/slice:aig:endorsement:MEDIUM/dial",
        headers=headers,
        json={
            "target_level": "trusted_auto_approve",
            "reason": "Promoted after 5/5 consecutive exact matches.",
        },
    )
    assert promote_resp.status_code == 200
    assert (
        promote_resp.json()["policy"]["autonomy_level"]
        == TrustAutonomyLevel.TRUSTED_AUTO_APPROVE.value
    )
    assert (
        promote_resp.json()["policy"]["last_updated_by"]
        == "usr:lead.underwriter@docubridge.io"
    )
    assert (
        promote_resp.json()["policy"]["agent_spiffe_id"]
        == "spiffe://agents.docubridge.io/internal/underwriter-agent"
    )

    # 5. Instant Safety Dial-Back: when carrier format drift occurs (`ai_value != human_value`),
    #    the slice is automatically demoted from `trusted_auto_approve` back to `guided_review`!
    drift_resp = client.post(
        "/v1/gym/trust-policies/slice:aig:endorsement:MEDIUM/shadow",
        json={
            "entity_id": "SUB-AIG-010",
            "doc_id": "doc-aig-end-6",
            "issuer": "AIG",
            "doctype": "endorsement",
            "field_path": "coverage_limit",
            "ai_value": "$10,000,000.00",
            "human_value": "$15,000,000.00",
            "risk_level": "MEDIUM",
        },
    )
    assert drift_resp.status_code == 200
    assert drift_resp.json()["comparison"]["exact_match"] is False
    assert (
        drift_resp.json()["policy"]["autonomy_level"]
        == TrustAutonomyLevel.GUIDED_REVIEW.value
    )
    assert drift_resp.json()["policy"]["consecutive_matches"] == 0
    assert drift_resp.json()["policy"]["drift_incidents"] == 1

    # 6. External client/carrier role is blocked (403 Forbidden) from dialing trust policies
    client_svid = mint_jwt_svid(
        "spiffe://agents.docubridge.io/portal/external-portal-agent",
        obo_subject="usr:cfo@acme.com",
        obo_role="client",
    )
    ext_dial = client.post(
        "/v1/gym/trust-policies/slice:aig:endorsement:MEDIUM/dial",
        headers={"X-Bridge-Spiffe-Svid": f"Bearer {client_svid}"},
        json={
            "target_level": "shadow_mode",
            "reason": "External attempt",
        },
    )
    assert ext_dial.status_code == 403


def test_sql_agent_gym_cross_replica_and_agent_engine_durability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify SqlAgentGymEngine and agent_gym_from_env() persist rules, report cards,
    and Programmable Trust state across independent Cloud Run & Vertex AI Agent Engine
    processes, and refuse in-memory fallback when BRIDGE_SEAM_MODE=gcp."""
    import pytest

    from bridge.agent_gym import (
        SqlAgentGymEngine,
        TribalRuleCategory,
        TrustAutonomyLevel,
        agent_gym_from_env,
    )

    # 1. Refuses in-memory fallback when running in GCP / Agent Engine mode without a DB DSN
    monkeypatch.delenv("BRIDGE_GYM_DB_URL", raising=False)
    monkeypatch.delenv("BRIDGE_OPERATIONAL_DB_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("BRIDGE_SEAM_MODE", "gcp")
    with pytest.raises(RuntimeError, match="session-affinity trap"):
        agent_gym_from_env()
    monkeypatch.delenv("BRIDGE_SEAM_MODE", raising=False)

    # 2. Instance A (simulating Cloud Run UI replica) distills & promotes a rule and dials trust
    db_file = tmp_path / "agent_gym_shared.db"
    dsn = f"sqlite+aiosqlite:///{db_file}"
    monkeypatch.setenv("DATABASE_URL", dsn)

    cloud_run_gym = agent_gym_from_env()
    assert isinstance(cloud_run_gym, SqlAgentGymEngine)

    _, rule = cloud_run_gym.distill_from_quarantine_correction(
        entity_id="SUB-AGENT-ENGINE-01",
        doc_id="doc-ae-1",
        doctype="quote",
        issuer="Campala Ltd.",
        field_path="issuer",
        before_value="Campala Ltd.",
        after_value="Campala",
        expert_memo="Normalize Campala Ltd. legal suffix for AlloyDB consolidation.",
        actor_principal="usr:broker@docubridge.io",
        actor_role="broker",
        category=TribalRuleCategory.ENTITY_ALIAS,
        distinct_entities=("Campala Syndicate 1183",),
    )
    report = cloud_run_gym.evaluate_candidate_rule(rule.rule_id, auto_promote=True)
    assert report.passed_gate is True

    cloud_run_gym.record_shadow_comparison(
        slice_id="slice:aig:endorsement:MEDIUM",
        entity_id="SUB-AGENT-ENGINE-01",
        doc_id="doc-ae-2",
        issuer="AIG",
        doctype="endorsement",
        field_path="coverage_limit",
        ai_value="$15,000,000.00",
        human_value="$15,000,000.00",
    )
    cloud_run_gym.dial_trust_policy(
        "slice:aig:endorsement:MEDIUM",
        target_level=TrustAutonomyLevel.TRUSTED_AUTO_APPROVE,
        actor_principal="usr:lead.underwriter@docubridge.io",
        actor_role="underwriter",
        reason="Promoted on Cloud Run UI replica.",
    )
    cloud_run_gym.close()

    # 3. Instance B (simulating a separate Vertex AI Agent Engine reasoning container)
    #    connects to the same DSN and immediately sees the promoted rule & trust level!
    agent_engine_gym = SqlAgentGymEngine(dsn)
    try:
        assert (
            agent_engine_gym.normalize_entity_or_issuer("Campala Ltd.")
            == "Campala"
        )
        assert (
            agent_engine_gym.normalize_entity_or_issuer("Campala Syndicate 1183")
            == "Campala Syndicate 1183"
        )
        aig_policy = agent_engine_gym.get_trust_policy("slice:aig:endorsement:MEDIUM")
        assert aig_policy is not None
        assert aig_policy.autonomy_level == TrustAutonomyLevel.TRUSTED_AUTO_APPROVE
        assert aig_policy.consecutive_matches == 5
    finally:
        agent_engine_gym.close()


def test_banking_loan_underwriting_observability_lineage_and_evaluation() -> None:
    """Verify all 4 pillars (Tracing/Lineage/Bi-Temporal, SPIFFE OBO + A2UI v0.9 SSE,
    Agent Gym Stress Suite + Decimal Firewall, and Programmable Trust + Safety Dial-Back)
    on the Banking Commercial Real Estate Loan Underwriting scenario (LOAN-MERIDIAN-001),
    plus the live interactive console routes (/demo and /console)."""
    from bridge.a2ui_inbox import InMemoryActionInboxStore
    from bridge.agent_gym import (
        InMemoryAgentGymEngine,
        TribalRuleCategory,
        TribalRuleStatus,
        TrustAutonomyLevel,
    )
    from bridge.identity import mint_jwt_svid

    vault = InMemoryAuditVaultStore()
    store = InMemoryOperationalStore()
    catalog = InMemoryKnowledgeCatalog(operational_store=store)
    inbox_store = InMemoryActionInboxStore()
    gym = InMemoryAgentGymEngine()

    app = build_user_interaction_app(
        operational_store=store,
        knowledge_catalog=catalog,
        inbox_store=inbox_store,
        agent_gym=gym,
        require_auth=True,
    )
    client = TestClient(app)

    # 0. Interactive Console Routes (/demo and /console) & Vendor Neutrality Check
    for route in ("/demo", "/console"):
        demo_resp = client.get(route)
        assert demo_resp.status_code == 200
        assert "DOCUBRIDGE" in demo_resp.text
        assert "Meridian Logistics Center LLC" in demo_resp.text
        assert "Terrepower Coastal LLC" in demo_resp.text
        assert "agents.global.aon.net" not in demo_resp.text

    assets_resp = client.get("/v1/catalog/assets")
    assert assets_resp.status_code == 200
    raw_assets_text = assets_resp.text
    assert "docubridge-audit-vault" in raw_assets_text
    assert "docubridge_lakehouse.bq_operational_facts_iceberg" in raw_assets_text
    assert "aon-docubridge-audit-vault" not in raw_assets_text
    assert "aon_lakehouse" not in raw_assets_text

    # ------------------------------------------------------------------ #
    # Pillar 1: Tracing, Provenance & Bi-Temporal Observability          #
    # ------------------------------------------------------------------ #
    t0 = datetime(2026, 10, 19, 18, 0, 0, tzinfo=UTC)
    t1 = datetime(2026, 10, 19, 20, 0, 0, tzinfo=UTC)
    eff_date = datetime(2026, 11, 1, 0, 0, 0, tzinfo=UTC)

    term_sheet_entry = _make_accepted_entry(
        doc_id="doc-meridian-ts-v1",
        doctype="term-sheet",
        issuer="Syndicated Lenders Group",
        key_fields={
            "currency": "USD",
            "loan_amount": "$45,000,000.00",
            "appraised_value": "$62,500,000.00",
            "origination_fee": "$50,000.00",
            "net_operating_income": "$4,185,000.00",
        },
        source_anchors={
            "origination_fee": SourceAnchor(
                page_number=1,
                bounding_box=(0.14, 0.31, 0.48, 0.36),
                snippet="Base Origination Fee: $50,000.00",
                artifact_uri="gs://docubridge-audit-vault/vault/LOAN-MERIDIAN-001/doc-meridian-ts-v1/v1/ts.pdf",
            ),
            "loan_amount": SourceAnchor(
                page_number=2,
                bounding_box=(0.15, 0.28, 0.52, 0.33),
                snippet="Senior Term Loan Facility: $45,000,000.00",
            ),
            "appraised_value": SourceAnchor(
                page_number=11,
                bounding_box=(0.18, 0.52, 0.60, 0.58),
                snippet="As-Stabilized Appraised Collateral Value: $62,500,000.00",
            ),
        },
        recorded_at=t0,
        effective_from=eff_date,
    )
    ingest_accepted_submission(
        term_sheet_entry,
        entity_id="LOAN-MERIDIAN-001",
        context_id="ctx-meridian-001",
        raw_document_bytes=b"%PDF-1.7 term-sheet",
        vault_store=vault,
        operational_store=store,
        knowledge_catalog=catalog,
        agent_gym=gym,
        recorded_at=t0,
        effective_from=eff_date,
    )

    addendum_entry = _make_accepted_entry(
        doc_id="doc-meridian-addendum-v2",
        doctype="closing-disclosure",
        issuer="Syndicated Lenders Group",
        key_fields={
            "currency": "USD",
            "origination_fee": "$100,000.00",
        },
        source_anchors={
            "origination_fee": SourceAnchor(
                page_number=4,
                bounding_box=(0.12, 0.44, 0.58, 0.51),
                snippet="Schedule B Origination & Rate-Lock Fee: $100,000.00",
                artifact_uri="gs://docubridge-audit-vault/vault/LOAN-MERIDIAN-001/doc-meridian-addendum-v2/v1/addendum.pdf",
            ),
        },
        recorded_at=t1,
        effective_from=eff_date,
    )
    ingest_accepted_submission(
        addendum_entry,
        entity_id="LOAN-MERIDIAN-001",
        context_id="ctx-meridian-001",
        raw_document_bytes=b"%PDF-1.7 schedule-b-addendum",
        vault_store=vault,
        operational_store=store,
        knowledge_catalog=catalog,
        agent_gym=gym,
        recorded_at=t1,
        effective_from=eff_date,
    )

    # 6-Hop End-to-End Lineage Trace
    trace = catalog.trace_lineage("LOAN-MERIDIAN-001", "origination_fee")
    assert trace is not None
    assert trace.current_value == "$100,000.00"
    assert trace.source_anchor is not None
    assert trace.source_anchor.page_number == 4
    assert "priority=40" in trace.consolidation_rule
    zones_in_trace = [node.zone for node in trace.nodes]
    assert zones_in_trace == [
        AssetLocationZone.INBOUND,
        AssetLocationZone.AUDIT_VAULT,
        AssetLocationZone.OPERATIONAL_STORE,
        AssetLocationZone.BUSINESS_VIEW,
        AssetLocationZone.BIGQUERY_ICEBERG,
        AssetLocationZone.USER_INTERACTION_A2UI,
    ]

    # Bi-temporal point-in-time query (t0 vs t1) and FactConflict detection
    uw_at_t0 = store.underwriter_view("LOAN-MERIDIAN-001", learned_as_of=t0)
    fee_at_t0 = next(
        i
        for i in uw_at_t0.exposure_items
        if i.field_path.endswith("origination_fee")
    )
    assert fee_at_t0.field_value == "$50,000.00"

    uw_at_t1 = store.underwriter_view("LOAN-MERIDIAN-001", learned_as_of=t1)
    fee_at_t1 = next(
        i
        for i in uw_at_t1.exposure_items
        if i.field_path.endswith("origination_fee")
    )
    assert fee_at_t1.field_value == "$100,000.00"
    assert uw_at_t1.financials.total_coverage_limit == "45000000.00"
    assert uw_at_t1.financials.total_insured_value == "62500000.00"
    assert uw_at_t1.financials.total_deductible == "100000.00"

    bv_at_t1 = store.broker_view("LOAN-MERIDIAN-001", learned_as_of=t1)
    assert len(bv_at_t1.conflicts) == 1
    assert bv_at_t1.conflicts[0].field_path.endswith("origination_fee")
    assert bv_at_t1.conflicts[0].winning_doc_id == "doc-meridian-addendum-v2"
    assert bv_at_t1.conflicts[0].conflicting_doc_id == "doc-meridian-ts-v1"

    # Watermarked BigQuery Open Iceberg CDC export
    cdc_delta = store.export_bigquery_sync_batch(
        "LOAN-MERIDIAN-001", since_recorded_at=t0
    )
    assert cdc_delta.iceberg_table == "docubridge_lakehouse.bq_operational_facts_iceberg"
    assert all(r.doc_id == "doc-meridian-addendum-v2" for r in cdc_delta.records)
    assert any(r.field_value == "$100,000.00" for r in cdc_delta.records)

    # ------------------------------------------------------------------ #
    # Pillar 2: Runtime Governance, SPIFFE OBO & A2UI v0.9 SSE Telemetry #
    # ------------------------------------------------------------------ #
    junior_svid = mint_jwt_svid(
        "spiffe://agents.docubridge.io/internal/credit-officer-agent",
        obo_subject="usr:junior.officer@docubridge.io",
        obo_role="underwriter",
        max_approval_amount="25000.00",
    )
    senior_svid = mint_jwt_svid(
        "spiffe://agents.docubridge.io/internal/credit-officer-agent",
        obo_subject="usr:senior.credit.officer@docubridge.io",
        obo_role="underwriter",
        max_approval_amount="25000000.00",
    )

    create_action_resp = client.post(
        "/v1/a2ui/inbox/actions?channel=gemini_app",
        headers={"X-Bridge-Spiffe-Svid": f"Bearer {senior_svid}"},
        json={
            "entity_id": "LOAN-MERIDIAN-001",
            "title": "Resolve Schedule B Origination Fee Conflict",
            "assigned_role": "underwriter",
            "target_view": "v_underwriter_exposure",
            "pause_for_review": True,
            "data_model": {
                "transfer": {"approvedAmount": 100000.0},
                "resolution": {"selectedValue": "$50,000.00"},
            },
        },
    )
    assert create_action_resp.status_code == 200
    loan_item_id = create_action_resp.json()["item_id"]

    # Junior Loan Officer ($25k authority) is blocked with HTTP 422 on $100,000 approval
    jr_attempt = client.post(
        f"/v1/a2ui/inbox/items/{loan_item_id}/action?channel=gemini_app&role=underwriter",
        headers={"X-Bridge-Spiffe-Svid": f"Bearer {junior_svid}"},
        json={
            "version": "v0.9",
            "action": {
                "name": "edit_and_approve",
                "context": {
                    "decision": "edit",
                    "transfer": {"approvedAmount": 100000.0},
                    "resolution": {"selectedValue": "$100,000.00"},
                },
            },
        },
    )
    assert jr_attempt.status_code == 422

    # Senior Credit Officer ($25M authority) succeeds and logs RFC 6902 patch + SPIFFE OBO
    sr_approve = client.post(
        f"/v1/a2ui/inbox/items/{loan_item_id}/action?channel=gemini_app&role=underwriter",
        headers={"X-Bridge-Spiffe-Svid": f"Bearer {senior_svid}"},
        json={
            "version": "v0.9",
            "action": {
                "name": "edit_and_approve",
                "context": {
                    "decision": "edit",
                    "transfer": {"approvedAmount": 100000.0},
                    "resolution": {"selectedValue": "$100,000.00"},
                    "review": {
                        "memo": "Schedule B fee addendum on page 4 sets origination fee to $100,000.00."
                    },
                },
            },
        },
    )
    assert sr_approve.status_code == 200
    audits = inbox_store.list_audit_records(loan_item_id)
    assert len(audits) == 1
    assert audits[0].actor_principal == "usr:senior.credit.officer@docubridge.io"
    assert (
        audits[0].agent_spiffe_id
        == "spiffe://agents.docubridge.io/internal/credit-officer-agent"
    )
    patch_paths = {p.path for p in audits[0].patches}
    assert "/resolution/selectedValue" in patch_paths

    # Live SSE stream emits a2ui.surface and a2ui.delta frames
    sr_headers = {"X-Bridge-Spiffe-Svid": f"Bearer {senior_svid}"}
    sse_resp = client.get(
        f"/v1/a2ui/inbox/items/{loan_item_id}/events?role=underwriter",
        headers=sr_headers,
    )
    assert sse_resp.status_code == 200
    assert "event: a2ui.surface" in sse_resp.text
    assert "event: a2ui.delta" in sse_resp.text

    # ------------------------------------------------------------------ #
    # Pillar 3: Continuous Evaluation (Agent Gym + Decimal Firewall)     #
    # ------------------------------------------------------------------ #
    distill_resp = client.post(
        "/v1/gym/signals/from-quarantine",
        headers=sr_headers,
        json={
            "entity_id": "LOAN-MERIDIAN-001",
            "doc_id": "syndicated-credit-agreement-meridian-2026.pdf",
            "doctype": "addendum",
            "issuer": "Syndicated Credit Agreement",
            "field_path": "origination_fee",
            "before_value": "$50,000.00",
            "after_value": "$100,000.00",
            "expert_memo": "Read origination fee from Schedule B p. 4 instead of cover summary.",
            "page_number": 4,
            "category": "extraction_hint",
        },
    )
    assert distill_resp.status_code == 200
    banking_rule_id = distill_resp.json()["candidate_rule"]["rule_id"]

    eval_resp = client.post(
        f"/v1/gym/rules/{banking_rule_id}/evaluate",
        headers=sr_headers,
    )
    assert eval_resp.status_code == 200
    rep = eval_resp.json()["report_card"]
    assert rep["passed_gate"] is True
    assert rep["math_firewall_intact"] is True
    assert rep["stress_cases_total"] == 4
    assert rep["stress_cases_passed"] == 4
    assert len(rep["historical_regressions"]) == 0
    assert rep["baseline_slice_score"] == 0.5
    assert rep["candidate_slice_score"] == 1.0
    assert eval_resp.json()["rule"]["status"] == TribalRuleStatus.PROMOTED.value

    # Borrower Entity Alias Rule ("Meridian Logistics Holdings Ltd." -> "Meridian Logistics", preserving "Meridian SPV II")
    alias_resp = client.post(
        "/v1/gym/signals/from-quarantine",
        headers=sr_headers,
        json={
            "entity_id": "LOAN-MERIDIAN-001",
            "doc_id": "doc-meridian-alias-1",
            "doctype": "credit-agreement",
            "issuer": "Meridian Logistics Holdings Ltd.",
            "field_path": "issuer",
            "before_value": "Meridian Logistics Holdings Ltd.",
            "after_value": "Meridian Logistics",
            "expert_memo": "Normalize borrower legal suffix while keeping Meridian SPV II separate.",
            "category": TribalRuleCategory.ENTITY_ALIAS.value,
            "distinct_entities": ["Meridian SPV II"],
        },
    )
    assert alias_resp.status_code == 200
    alias_rule_id = alias_resp.json()["candidate_rule"]["rule_id"]
    alias_eval = client.post(
        f"/v1/gym/rules/{alias_rule_id}/evaluate", headers=sr_headers
    )
    assert alias_eval.status_code == 200
    assert alias_eval.json()["report_card"]["passed_gate"] is True
    assert (
        gym.normalize_entity_or_issuer("Meridian Logistics Holdings Ltd.")
        == "Meridian Logistics"
    )
    assert (
        gym.normalize_entity_or_issuer("Meridian SPV II") == "Meridian SPV II"
    )

    # Hallucination / Decimal Math Firewall Gate blocks rule attempting to override Decimal math
    bad_math_resp = client.post(
        "/v1/gym/signals/from-quarantine",
        headers=sr_headers,
        json={
            "entity_id": "LOAN-MERIDIAN-001",
            "doc_id": "doc-bad-math",
            "doctype": "addendum",
            "issuer": "Syndicated",
            "field_path": "loan_amount",
            "before_value": "$45,000,000.00",
            "after_value": "$45,000,000.00",
            "expert_memo": "Use override_decimal_math and llm_calculate_total instead of Decimal.",
        },
    )
    bad_rule_id = bad_math_resp.json()["candidate_rule"]["rule_id"]
    bad_eval = client.post(
        f"/v1/gym/rules/{bad_rule_id}/evaluate", headers=sr_headers
    )
    assert bad_eval.status_code == 200
    assert bad_eval.json()["report_card"]["math_firewall_intact"] is False
    assert bad_eval.json()["report_card"]["passed_gate"] is False
    assert bad_eval.json()["rule"]["status"] == TribalRuleStatus.REJECTED.value

    # ------------------------------------------------------------------ #
    # Pillar 4: Graduated Autonomy & Instant Safety Dial-Back            #
    # ------------------------------------------------------------------ #
    policies_resp = client.get("/v1/gym/trust-policies", headers=sr_headers)
    assert policies_resp.status_code == 200
    policy_map = {p["slice_id"]: p for p in policies_resp.json()}
    assert "slice:closing-disclosure:fee-schedule:LOW" in policy_map
    assert "slice:credit-agreement:addendum:MEDIUM" in policy_map
    assert "slice:appraisal-report:commercial:HIGH" in policy_map

    credit_slice = policy_map["slice:credit-agreement:addendum:MEDIUM"]
    assert credit_slice["autonomy_level"] == TrustAutonomyLevel.GUIDED_REVIEW.value
    assert credit_slice["consecutive_matches"] == 4
    assert credit_slice["ready_for_promotion"] is False

    # Record 5th exact match -> ready_for_promotion=True -> promote to trusted_auto_approve
    shd5_resp = client.post(
        "/v1/gym/trust-policies/slice:credit-agreement:addendum:MEDIUM/shadow",
        headers=sr_headers,
        json={
            "entity_id": "LOAN-MERIDIAN-001",
            "doc_id": "syndicated-credit-agreement-meridian-2026.pdf",
            "issuer": "Syndicated",
            "doctype": "addendum",
            "field_path": "origination_fee",
            "ai_value": "$100,000.00",
            "human_value": "$100,000.00",
            "risk_level": "MEDIUM",
        },
    )
    assert shd5_resp.status_code == 200
    assert shd5_resp.json()["ready_for_promotion"] is True
    assert shd5_resp.json()["policy"]["consecutive_matches"] == 5

    dial_promote = client.post(
        "/v1/gym/trust-policies/slice:credit-agreement:addendum:MEDIUM/dial",
        headers=sr_headers,
        json={
            "target_level": "trusted_auto_approve",
            "reason": "Promoted after 5/5 consecutive exact matches on credit addenda.",
        },
    )
    assert dial_promote.status_code == 200
    assert (
        dial_promote.json()["policy"]["autonomy_level"]
        == TrustAutonomyLevel.TRUSTED_AUTO_APPROVE.value
    )

    # Inject format drift -> Instant Safety Dial-Back to guided_review with 0 streak and 1 drift
    drift_banking = client.post(
        "/v1/gym/trust-policies/slice:credit-agreement:addendum:MEDIUM/shadow",
        headers=sr_headers,
        json={
            "entity_id": "LOAN-MERIDIAN-002",
            "doc_id": "syndicated-credit-agreement-drift.pdf",
            "issuer": "Syndicated",
            "doctype": "addendum",
            "field_path": "origination_fee",
            "ai_value": "$50,000.00",
            "human_value": "$100,000.00",
            "risk_level": "MEDIUM",
        },
    )
    assert drift_banking.status_code == 200
    assert (
        drift_banking.json()["policy"]["autonomy_level"]
        == TrustAutonomyLevel.GUIDED_REVIEW.value
    )
    assert drift_banking.json()["policy"]["consecutive_matches"] == 0
    assert drift_banking.json()["policy"]["drift_incidents"] == 1

    # Reset endpoint restores default 6 slices cleanly
    reset_resp = client.post("/v1/gym/trust-policies/reset", headers=sr_headers)
    assert reset_resp.status_code == 200
    reset_map = {p["slice_id"]: p for p in reset_resp.json()}
    assert reset_map["slice:credit-agreement:addendum:MEDIUM"]["consecutive_matches"] == 4
    assert reset_map["slice:credit-agreement:addendum:MEDIUM"]["drift_incidents"] == 0

    # ------------------------------------------------------------------ #
    # End-to-End OpenTelemetry Spans Verification across all steps       #
    # ------------------------------------------------------------------ #
    otel_resp = client.get("/v1/telemetry/spans?limit=500")
    assert otel_resp.status_code == 200
    otel_payload = otel_resp.json()
    assert otel_payload["count"] > 10
    span_names = {s["name"] for s in otel_payload["spans"]}
    expected_step_spans = {
        "docubridge.intake.ingest_accepted_submission",
        "docubridge.intake.redact_pii",
        "docubridge.vault.lock_worm_artifact",
        "docubridge.consolidation.promote_to_operational_store",
        "docubridge.consolidation.bitemporal_query_as_of",
        "docubridge.consolidation.role_based_view",
        "docubridge.consolidation.compute_financial_summary",
        "docubridge.consolidation.bigquery_iceberg_sync",
        "docubridge.catalog.register_lineage",
        "docubridge.catalog.lineage_trace",
        "docubridge.catalog.authorize_access",
        "docubridge.identity.verify_jwt_svid",
        "docubridge.identity.authorize_spiffe_agent",
        "docubridge.identity.authorize_entity_access",
        "docubridge.a2ui.create_action_item",
        "docubridge.a2ui.resolve_inbox_action",
        "docubridge.agent_gym.distill_signal",
        "docubridge.agent_gym.evaluate_rule",
        "docubridge.agent_gym.shadow_comparison",
        "docubridge.agent_gym.dial_trust_policy",
        "docubridge.agent_gym.reset_trust_policies",
    }
    assert expected_step_spans.issubset(span_names)

    # Verify W3C traceparent propagation links HTTP server span and all child spans
    custom_trace_id = "4bf92f3577b34da6a3ce929d0e0e4736"
    custom_parent_span = "00f067aa0ba902b7"
    traced_lineage = client.get(
        "/v1/catalog/lineage/LOAN-MERIDIAN-001/origination_fee",
        headers={"traceparent": f"00-{custom_trace_id}-{custom_parent_span}-01"},
    )
    assert traced_lineage.status_code == 200
    assert traced_lineage.headers.get("x-docubridge-trace-id") == custom_trace_id

    trace_spans_resp = client.get(f"/v1/telemetry/spans?trace_id={custom_trace_id}")
    assert trace_spans_resp.status_code == 200
    trace_spans = trace_spans_resp.json()["spans"]
    traced_names = {s["name"] for s in trace_spans}
    assert "docubridge.catalog.lineage_trace" in traced_names
    assert any(
        s["name"].startswith("docubridge.http.user_interaction GET")
        and s["parent_span_id"] == custom_parent_span
        for s in trace_spans
    )

    # Verify BigQuery Trace Analytics endpoint (/v1/telemetry/analytics) & row formatting
    from bridge.telemetry import CloudTraceV2SpanExporter

    analytics_resp = client.get("/v1/telemetry/analytics?limit=500")
    assert analytics_resp.status_code == 200
    analytics_data = analytics_resp.json()
    assert analytics_data["total_spans_analyzed"] > 10
    assert analytics_data["bigquery_target"]["dataset_id"] == "docubridge_telemetry"
    assert analytics_data["bigquery_target"]["table_id"] == "otel_spans"
    assert len(analytics_data["step_latency"]) >= 15
    assert len(analytics_data["trace_waterfall"]) >= 10
    assert len(analytics_data["entity_pipeline_summary"]) >= 1

    bq_row = CloudTraceV2SpanExporter._to_bigquery_row(trace_spans[0])
    assert bq_row["insertId"] == f"{trace_spans[0]['trace_id']}:{trace_spans[0]['span_id']}"
    assert bq_row["json"]["trace_id"] == custom_trace_id
    assert "attributes_json" in bq_row["json"]








