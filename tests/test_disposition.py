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
