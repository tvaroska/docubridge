"""Tests for Agent Gym promotion gate integrity and scoped extraction hints (P13-1)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from bridge.agent_gym import (
    InMemoryAgentGymEngine,
    TribalKnowledgeRule,
    TribalRuleCategory,
    TribalRuleStatus,
)
from bridge.extraction.derivations import apply_field_derivations
from bridge.identity import mint_jwt_svid
from bridge.knowledge_catalog import InMemoryKnowledgeCatalog
from bridge.operational_store import InMemoryOperationalStore
from bridge.user_interaction import (
    build_user_interaction_app,
    ingest_accepted_submission,
)
from bridge.vault import InMemoryAuditVaultStore
from contract.enums import Disposition, Gate
from contract.extraction import ExtractionFields
from contract.wire import LedgerEntry


def test_gate_rejects_rule_with_unrelated_target() -> None:
    """A monetary correction with an unrelated non-monetary target ('banana') fails the gate."""
    gym = InMemoryAgentGymEngine()
    _, rule = gym.distill_from_quarantine_correction(
        entity_id="ENT-A1",
        doc_id="doc-a1",
        doctype="quote",
        issuer="Acme",
        field_path="deductible",
        before_value="$500",
        after_value="banana",
        expert_memo="Corrupt deductible to banana.",
        actor_principal="reviewer@docubridge.io",
        actor_role="broker",
        citation_verified=True,
        review_dwell_ms=4200,
    )
    assert rule.status == TribalRuleStatus.CANDIDATE

    report = gym.evaluate_candidate_rule(rule.rule_id, auto_promote=True)
    assert report.passed_gate is False
    stored = next(r for r in gym.list_rules() if r.rule_id == rule.rule_id)
    assert stored.status == TribalRuleStatus.REJECTED


def test_rejected_rule_cannot_be_promoted() -> None:
    """A rule rejected for missing oversight (or prior gate failure) cannot be evaluated/promoted."""
    gym = InMemoryAgentGymEngine()
    _, rejected_rule = gym.distill_from_quarantine_correction(
        entity_id="ENT-A1-REJ",
        doc_id="doc-a1-rej",
        doctype="quote",
        issuer="Acme",
        field_path="deductible",
        before_value="$500.00",
        after_value="$1,000.00",
        expert_memo="Unverified rubber-stamp correction.",
        actor_principal="reviewer@docubridge.io",
        actor_role="broker",
        citation_verified=False,
        review_dwell_ms=0,
    )
    assert rejected_rule.status == TribalRuleStatus.REJECTED

    with pytest.raises(ValueError, match="candidate"):
        gym.evaluate_candidate_rule(rejected_rule.rule_id, auto_promote=True)

    stored = next(r for r in gym.list_rules() if r.rule_id == rejected_rule.rule_id)
    assert stored.status == TribalRuleStatus.REJECTED

    # HTTP route also refuses evaluating a non-candidate rule
    app = build_user_interaction_app(agent_gym=gym)
    client = TestClient(app)
    svid = mint_jwt_svid("spiffe://agents.docubridge.io/internal/broker-agent")
    resp = client.post(
        f"/v1/gym/rules/{rejected_rule.rule_id}/evaluate?auto_promote=true",
        headers={
            "X-Bridge-Spiffe-Svid": svid,
            "X-Bridge-Role": "broker",
            "X-Bridge-Principal": "reviewer@docubridge.io",
        },
    )
    assert resp.status_code in {409, 422}


def test_evaluate_defaults_to_no_auto_promote() -> None:
    """evaluate_candidate_rule defaults to auto_promote=False (VERIFIED, not PROMOTED)."""
    gym = InMemoryAgentGymEngine()
    _, rule = gym.distill_from_quarantine_correction(
        entity_id="ENT-DEFAULT",
        doc_id="doc-default",
        doctype="quote",
        issuer="Acme",
        field_path="deductible",
        before_value="$500.00",
        after_value="$1,000.00",
        expert_memo="Read deductible from Schedule B.",
        actor_principal="reviewer@docubridge.io",
        actor_role="broker",
        citation_verified=True,
        review_dwell_ms=4200,
    )
    report = gym.evaluate_candidate_rule(rule.rule_id)
    assert report.passed_gate is True
    stored = next(r for r in gym.list_rules() if r.rule_id == rule.rule_id)
    assert stored.status == TribalRuleStatus.VERIFIED
    assert stored.promoted_at is None


def test_extraction_hint_requires_matching_before_value_and_issuer_boundary() -> None:
    """Promoted hints only apply when raw value equals before_value and issuer matches on a word boundary."""
    gym = InMemoryAgentGymEngine()
    _, rule = gym.distill_from_quarantine_correction(
        entity_id="ENT-SCOPE",
        doc_id="doc-scope",
        doctype="quote",
        issuer="Acme",
        field_path="deductible",
        before_value="$500.00",
        after_value="$1,000.00",
        expert_memo="Map $500.00 cover value to $1,000.00 Schedule B deductible.",
        actor_principal="reviewer@docubridge.io",
        actor_role="broker",
        citation_verified=True,
        review_dwell_ms=4200,
    )
    assert rule.before_value == "$500.00"
    report = gym.evaluate_candidate_rule(rule.rule_id, auto_promote=True)
    assert report.passed_gate is True

    # 1. Held-out document with different deductible stays unchanged
    other_val_fields = ExtractionFields(
        doctype="quote",
        issuer="Acme",
        key_fields={"deductible": "$2,500.00"},
    )
    hints = gym.get_extraction_hints(issuer="Acme", doctype="quote")
    derived_other, applied_other = apply_field_derivations(
        other_val_fields, active_hints=hints
    )
    assert derived_other.key_fields["deductible"] == "$2,500.00"
    assert applied_other == ()

    # 2. Prefix-colliding issuer without word boundary ("AcmeCorp") does not match "Acme*"
    assert gym.get_extraction_hints(issuer="AcmeCorp", doctype="quote") == []

    # 3. Document with missing issuer does NOT receive issuer-scoped hints and stays pending
    vault = InMemoryAuditVaultStore()
    store = InMemoryOperationalStore()
    catalog = InMemoryKnowledgeCatalog(operational_store=store)
    missing_issuer_entry = LedgerEntry(
        doc_id="doc-missing-issuer",
        doctype="quote",
        disposition=Disposition.pending,
        gate=Gate.hitl_review,
        issuer=None,
        key_fields={"deductible": "$500.00"},
        reason_code="needs_review",
        message="Pending review",
    )
    with pytest.raises(ValueError, match="accepted"):
        ingest_accepted_submission(
            missing_issuer_entry,
            entity_id="ENT-NO-ISSUER",
            context_id="ctx-no-issuer",
            raw_document_bytes=b"%PDF-1.7 real",
            vault_store=vault,
            operational_store=store,
            knowledge_catalog=catalog,
            agent_gym=gym,
        )


def test_evaluate_refuses_missing_source_signal() -> None:
    """evaluate_candidate_rule raises instead of inventing a synthetic source signal (P13-10(e))."""
    gym = InMemoryAgentGymEngine()
    orphan = TribalKnowledgeRule(
        rule_id="rule-orphan-1",
        category=TribalRuleCategory.EXTRACTION_HINT,
        issuer_pattern="Acme*",
        doctype="quote",
        field_path="deductible",
        title="Orphan rule",
        instruction="Orphan instruction",
        before_value="$500.00",
        canonical_target="$1,000.00",
        source_signal_id="sig-does-not-exist",
        authored_by="reviewer@docubridge.io",
        status=TribalRuleStatus.CANDIDATE,
    )
    gym._save_rule(orphan)
    with pytest.raises(KeyError, match="sig-does-not-exist"):
        gym.evaluate_candidate_rule(orphan.rule_id)
