"""Tests for the disposition gate (the load-bearing core)."""

import json
from pathlib import Path

from bridge.disposition import (
    DEFAULT_THRESHOLDS,
    DispositionThresholds,
    DispositionVerdict,
    dispose,
    run_disposition_gate,
)
from contract.enums import Disposition, Gate


def test_corpus_parity():
    """Corpus parity: the oracle anchor.

    Load all 8 records from wiki/evals/address/expected.json and verify
    that each one disposes to its expected_disposition/expected_gate.
    """
    # Load the corpus
    repo_root = Path(__file__).resolve().parents[1]
    corpus_path = repo_root / "wiki/evals/address/expected.json"
    corpus = json.loads(corpus_path.read_text())

    candidate_doctypes = ["gov-id", "utility-bill"]

    # Verify all 8 documents
    for doc in corpus["documents"]:
        extraction = doc["extraction"]
        result = run_disposition_gate(
            doctype=doc["doctype"],
            candidate_doctypes=candidate_doctypes,
            legible=extraction["legible"],
            overall_confidence=extraction["overall_confidence"],
            flagged_fields=extraction.get("flagged_fields", []),
        )

        # Assert disposition and gate match expected values
        assert result["disposition"] == doc["expected_disposition"], (
            f"Document {doc['id']}: expected disposition {doc['expected_disposition']}, "
            f"got {result['disposition']}"
        )
        assert result["gate"] == doc["expected_gate"], (
            f"Document {doc['id']}: expected gate {doc['expected_gate']}, "
            f"got {result['gate']}"
        )


def test_determinism():
    """Determinism: identical signals → identical verdict."""
    # Test with a couple of different signal combinations
    test_cases = [
        {
            "doctype": "gov-id",
            "candidate_doctypes": ["gov-id", "utility-bill"],
            "legible": True,
            "overall_confidence": 0.96,
            "flagged_fields": [],
        },
        {
            "doctype": "utility-bill",
            "candidate_doctypes": ["gov-id", "utility-bill"],
            "legible": False,
            "overall_confidence": 0.30,
            "flagged_fields": [],
        },
    ]

    for signals in test_cases:
        result1 = run_disposition_gate(**signals)
        result2 = run_disposition_gate(**signals)

        # Assert both results are identical
        assert result1 == result2, (
            f"Gate must be deterministic but got different results for {signals}"
        )


def test_threshold_boundaries():
    """Threshold boundaries: explicit unit cases at and around thresholds."""
    candidate_doctypes = ["gov-id"]

    # Exactly at auto_approve_at (0.85) → auto_approve
    verdict = dispose(
        doctype="gov-id",
        candidate_doctypes=candidate_doctypes,
        legible=True,
        overall_confidence=0.85,
        flagged_fields=[],
    )
    assert verdict.disposition == Disposition.accepted
    assert verdict.gate == Gate.auto_approve

    # Just below auto_approve_at (0.84) → hitl_review
    verdict = dispose(
        doctype="gov-id",
        candidate_doctypes=candidate_doctypes,
        legible=True,
        overall_confidence=0.84,
        flagged_fields=[],
    )
    assert verdict.disposition == Disposition.pending
    assert verdict.gate == Gate.hitl_review

    # At resubmit_below (0.55) → hitl_review (boundary is <, not <=)
    verdict = dispose(
        doctype="gov-id",
        candidate_doctypes=candidate_doctypes,
        legible=True,
        overall_confidence=0.55,
        flagged_fields=[],
    )
    assert verdict.disposition == Disposition.pending
    assert verdict.gate == Gate.hitl_review

    # Just below resubmit_below (0.54) → resubmit
    verdict = dispose(
        doctype="gov-id",
        candidate_doctypes=candidate_doctypes,
        legible=True,
        overall_confidence=0.54,
        flagged_fields=[],
    )
    assert verdict.disposition == Disposition.rejected
    assert verdict.gate == Gate.resubmit

    # legible=False at high confidence → resubmit (quality gate precedes confidence)
    verdict = dispose(
        doctype="gov-id",
        candidate_doctypes=candidate_doctypes,
        legible=False,
        overall_confidence=0.95,
        flagged_fields=[],
    )
    assert verdict.disposition == Disposition.rejected
    assert verdict.gate == Gate.resubmit
    assert verdict.reason_code == "illegible"

    # Flagged at high confidence → hitl_review
    verdict = dispose(
        doctype="gov-id",
        candidate_doctypes=candidate_doctypes,
        legible=True,
        overall_confidence=0.95,
        flagged_fields=["expiry"],
    )
    assert verdict.disposition == Disposition.pending
    assert verdict.gate == Gate.hitl_review
    assert verdict.reason_code == "flagged_fields"

    # Unknown doctype → unsupported (precedes everything)
    verdict = dispose(
        doctype="passport",
        candidate_doctypes=candidate_doctypes,
        legible=True,
        overall_confidence=0.95,
        flagged_fields=[],
    )
    assert verdict.disposition == Disposition.rejected
    assert verdict.gate == Gate.unsupported
    assert verdict.reason_code == "unsupported_doctype"


def test_the_inversion_is_structural():
    """The inversion is structural: accepted only through auto-approve branch.

    There is no path to accepted except the auto-approve branch, and it
    requires: in-label + legible + high-conf + unflagged.
    """
    candidate_doctypes = ["gov-id"]

    # The ONLY way to get accepted: in-label, legible, high confidence, unflagged
    verdict = dispose(
        doctype="gov-id",
        candidate_doctypes=candidate_doctypes,
        legible=True,
        overall_confidence=0.90,
        flagged_fields=[],
    )
    assert verdict.disposition == Disposition.accepted
    assert verdict.gate == Gate.auto_approve

    # Any deviation blocks acceptance:

    # Out of label space
    verdict = dispose(
        doctype="passport",
        candidate_doctypes=candidate_doctypes,
        legible=True,
        overall_confidence=0.90,
        flagged_fields=[],
    )
    assert verdict.disposition != Disposition.accepted

    # Not legible
    verdict = dispose(
        doctype="gov-id",
        candidate_doctypes=candidate_doctypes,
        legible=False,
        overall_confidence=0.90,
        flagged_fields=[],
    )
    assert verdict.disposition != Disposition.accepted

    # Low confidence
    verdict = dispose(
        doctype="gov-id",
        candidate_doctypes=candidate_doctypes,
        legible=True,
        overall_confidence=0.50,
        flagged_fields=[],
    )
    assert verdict.disposition != Disposition.accepted

    # Flagged
    verdict = dispose(
        doctype="gov-id",
        candidate_doctypes=candidate_doctypes,
        legible=True,
        overall_confidence=0.90,
        flagged_fields=["expiry"],
    )
    assert verdict.disposition != Disposition.accepted


def test_verdict_shape():
    """Verdict shape: run_disposition_gate returns exactly the expected dict."""
    result = run_disposition_gate(
        doctype="gov-id",
        candidate_doctypes=["gov-id"],
        legible=True,
        overall_confidence=0.90,
        flagged_fields=[],
    )

    # Should be a dict with exactly these keys
    assert isinstance(result, dict)
    assert set(result.keys()) == {"disposition", "gate", "reason_code", "message"}

    # All values should be strings (enums serialize to their values)
    assert all(isinstance(v, str) for v in result.values())

    # Should be deserializable back to DispositionVerdict
    verdict = DispositionVerdict.model_validate(result)
    assert verdict.disposition == Disposition.accepted
    assert verdict.gate == Gate.auto_approve
    assert verdict.reason_code == "auto_approved"
    assert isinstance(verdict.message, str)


def test_thresholds_override():
    """Thresholds override: custom thresholds change borderline verdicts."""
    candidate_doctypes = ["gov-id"]

    # With default thresholds (auto_approve_at=0.85), 0.84 → hitl_review
    verdict_default = dispose(
        doctype="gov-id",
        candidate_doctypes=candidate_doctypes,
        legible=True,
        overall_confidence=0.84,
        flagged_fields=[],
        thresholds=DEFAULT_THRESHOLDS,
    )
    assert verdict_default.disposition == Disposition.pending
    assert verdict_default.gate == Gate.hitl_review

    # With custom thresholds (auto_approve_at=0.99), 0.84 → still hitl_review
    custom_thresholds = DispositionThresholds(auto_approve_at=0.99, resubmit_below=0.4)
    verdict_custom = dispose(
        doctype="gov-id",
        candidate_doctypes=candidate_doctypes,
        legible=True,
        overall_confidence=0.84,
        flagged_fields=[],
        thresholds=custom_thresholds,
    )
    assert verdict_custom.disposition == Disposition.pending
    assert verdict_custom.gate == Gate.hitl_review

    # Now test a confidence that changes verdict with custom thresholds
    # 0.50 with default (resubmit_below=0.55) → resubmit
    verdict_default2 = dispose(
        doctype="gov-id",
        candidate_doctypes=candidate_doctypes,
        legible=True,
        overall_confidence=0.50,
        flagged_fields=[],
        thresholds=DEFAULT_THRESHOLDS,
    )
    assert verdict_default2.disposition == Disposition.rejected
    assert verdict_default2.gate == Gate.resubmit

    # 0.50 with custom (resubmit_below=0.4) → hitl_review (passes quality gate)
    verdict_custom2 = dispose(
        doctype="gov-id",
        candidate_doctypes=candidate_doctypes,
        legible=True,
        overall_confidence=0.50,
        flagged_fields=[],
        thresholds=custom_thresholds,
    )
    assert verdict_custom2.disposition == Disposition.pending
    assert verdict_custom2.gate == Gate.hitl_review


def test_empty_label_space_rejects_every_doctype():
    """S0-arch-18: an empty label space (an unresolved process) accepts nothing.

    Rejected unsupported_doctype with a message that never renders an empty
    "Accepted types: ." list; the non-empty message is unchanged.
    """
    verdict = dispose(
        doctype="gov-id",
        candidate_doctypes=[],
        legible=True,
        overall_confidence=0.99,
        flagged_fields=[],
    )
    assert verdict.disposition is Disposition.rejected
    assert verdict.gate is Gate.unsupported
    assert verdict.reason_code == "unsupported_doctype"
    assert "Accepted types" not in verdict.message
    assert verdict.message == (
        "The document type 'gov-id' is not supported for this process. "
        "This process accepts no document types."
    )

    non_empty = dispose(
        doctype="passport",
        candidate_doctypes=["gov-id"],
        legible=True,
        overall_confidence=0.99,
        flagged_fields=[],
    )
    assert non_empty.message == (
        "The document type 'passport' is not supported for this process. "
        "Accepted types: gov-id."
    )


def test_gate_refuses_nan_and_out_of_range_confidence():
    """P13-6: dispose(), Signals, and ExtractionResult refuse NaN, Inf, and out-of-range confidence."""
    import pytest
    from pydantic import ValidationError

    from bridge.signals import Signals
    from contract.extraction import ExtractionFields, ExtractionResult

    for bad in (float("nan"), float("inf"), float("-inf"), -0.01, 1.01, 60.0):
        with pytest.raises(ValueError, match="overall_confidence"):
            dispose(
                doctype="gov-id",
                candidate_doctypes=["gov-id"],
                legible=True,
                overall_confidence=bad,
                flagged_fields=[],
            )
        with pytest.raises(ValidationError):
            Signals(
                doctype="gov-id",
                candidate_doctypes=["gov-id"],
                legible=True,
                overall_confidence=bad,
            )
        with pytest.raises(ValidationError):
            ExtractionResult(
                fields=ExtractionFields(doctype="gov-id"),
                overall_confidence=bad,
                legible=True,
            )

    with pytest.raises(ValidationError):
        ExtractionResult.model_validate(
            {
                "fields": {"doctype": "gov-id"},
                "overall_confidence": "NaN",
                "legible": True,
            }
        )


def test_freshness_is_inclusive_at_max_age():
    """P13-15: document_age_days == max_age_days is accepted; max_age_days + 1 is stale_document."""
    from datetime import date

    from bridge.signals import dispose_signals, signals_from_result
    from contract.extraction import ExtractionFields, ExtractionResult

    at_limit = dispose(
        doctype="utility-bill",
        candidate_doctypes=["utility-bill"],
        legible=True,
        overall_confidence=0.96,
        flagged_fields=[],
        document_age_days=90,
        max_age_days=90,
    )
    assert at_limit.disposition is Disposition.accepted
    assert at_limit.gate is Gate.auto_approve

    over_limit = dispose(
        doctype="utility-bill",
        candidate_doctypes=["utility-bill"],
        legible=True,
        overall_confidence=0.96,
        flagged_fields=[],
        document_age_days=91,
        max_age_days=90,
    )
    assert over_limit.disposition is Disposition.rejected
    assert over_limit.gate is Gate.resubmit
    assert over_limit.reason_code == "stale_document"

    # Producer check: signals_from_result evaluates utility-bill validation.yaml within_days=90
    as_of = date(2026, 8, 1)
    boundary_result = ExtractionResult(
        fields=ExtractionFields(
            doctype="utility-bill",
            issuer="PowerCo",
            key_fields={
                "name": "Jordan Lee",
                "service_address": "14 Elm Row, Springfield",
                "statement_date": "2026-05-03",  # exactly 90 days before 2026-08-01
            },
        ),
        overall_confidence=0.97,
        legible=True,
    )
    sig_ok = signals_from_result(
        boundary_result, candidate_doctypes=["utility-bill"], as_of=as_of
    )
    assert sig_ok.document_age_days == 90
    assert sig_ok.max_age_days == 90
    assert sig_ok.freshness_ok is True
    assert dispose_signals(sig_ok).gate is Gate.auto_approve

    stale_result = ExtractionResult(
        fields=ExtractionFields(
            doctype="utility-bill",
            issuer="PowerCo",
            key_fields={
                "name": "Jordan Lee",
                "service_address": "14 Elm Row, Springfield",
                "statement_date": "2026-05-02",  # 91 days before 2026-08-01
            },
        ),
        overall_confidence=0.97,
        legible=True,
    )
    sig_stale = signals_from_result(
        stale_result, candidate_doctypes=["utility-bill"], as_of=as_of
    )
    assert sig_stale.document_age_days == 91
    assert sig_stale.max_age_days == 90
    assert sig_stale.freshness_ok is False
    stale_v = dispose_signals(sig_stale)
    assert stale_v.disposition is Disposition.rejected
    assert stale_v.gate is Gate.resubmit
    assert stale_v.reason_code == "stale_document"


def test_failed_rules_route_to_hitl():
    """P13-15: declared validation.yaml failures populate failed_rules and route to hitl_review."""
    from datetime import date

    from bridge.signals import dispose_signals, signals_from_result
    from contract.extraction import ExtractionFields, ExtractionResult

    direct = dispose(
        doctype="gov-id",
        candidate_doctypes=["gov-id"],
        legible=True,
        overall_confidence=0.98,
        flagged_fields=[],
        failed_rules=["not_expired"],
    )
    assert direct.disposition is Disposition.pending
    assert direct.gate is Gate.hitl_review
    assert direct.reason_code == "corroboration_required"

    # Expired unflagged gov-id fails validation.yaml date_in_future -> corroboration_required
    expired_gov_id = ExtractionResult(
        fields=ExtractionFields(
            doctype="gov-id",
            key_fields={
                "name": "Jordan Lee",
                "address": "14 Elm Row, Springfield",
                "doc_number": "DL-6281035",
                "issuing_authority": "State DMV",
                "expiry": "2020-01-01",
            },
        ),
        overall_confidence=0.97,
        legible=True,
        flagged_fields=[],
    )
    sig_exp = signals_from_result(
        expired_gov_id,
        candidate_doctypes=["gov-id"],
        as_of=date(2026, 8, 1),
    )
    assert "not_expired" in sig_exp.failed_rules
    assert sig_exp.corroboration_ok is False
    verdict_exp = dispose_signals(sig_exp)
    assert verdict_exp.disposition is Disposition.pending
    assert verdict_exp.gate is Gate.hitl_review
    assert verdict_exp.reason_code == "corroboration_required"

    # Missing required key_field fails all_required_present
    incomplete_gov_id = ExtractionResult(
        fields=ExtractionFields(
            doctype="gov-id",
            key_fields={
                "name": "Jordan Lee",
                "expiry": "2030-01-01",
            },
        ),
        overall_confidence=0.97,
        legible=True,
        flagged_fields=[],
    )
    sig_inc = signals_from_result(
        incomplete_gov_id,
        candidate_doctypes=["gov-id"],
        as_of=date(2026, 8, 1),
    )
    assert "complete_fields" in sig_inc.failed_rules
    assert dispose_signals(sig_inc).reason_code == "corroboration_required"

    # Empty cell in benefits-quote rates table fails rate_amount_present
    bad_rates = ExtractionResult(
        fields=ExtractionFields(
            doctype="benefits-quote",
            issuer="carrier-a",
            key_fields={"effective_date": "2026-01-01", "currency": "USD"},
            tables={"rates": [{"amount": "", "frequency": "MONTHLY"}]},
        ),
        overall_confidence=0.97,
        legible=True,
        flagged_fields=[],
    )
    sig_rates = signals_from_result(bad_rates, candidate_doctypes=["benefits-quote"])
    assert "rate_amount_present" in sig_rates.failed_rules
    assert dispose_signals(sig_rates).reason_code == "corroboration_required"
