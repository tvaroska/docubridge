"""Unit tests for the Address Sense-B rule ``is_satisfied`` (AGENT-3).

The rule is pure and deterministic — a model never mints it. Satisfied when there
is ≥1 accepted ``gov-id`` OR ≥2 accepted ``utility-bill`` entries from *distinct*
issuers (same-issuer does not satisfy — the CUJ-10 reject half). Only ``accepted``
entries count.
"""
from __future__ import annotations

from agents.address.gate import is_satisfied
from contract.enums import Disposition, Gate
from contract.wire import CollectionStatus, LedgerEntry


def _entry(
    doc_id: str,
    doctype: str,
    disposition: Disposition,
    *,
    issuer: str | None = None,
    gate: Gate = Gate.auto_approve,
) -> LedgerEntry:
    return LedgerEntry(
        doc_id=doc_id,
        doctype=doctype,
        disposition=disposition,
        gate=gate,
        issuer=issuer,
    )


def _status(*entries: LedgerEntry) -> CollectionStatus:
    return CollectionStatus(entries=list(entries))


def test_accepted_gov_id_alone_satisfies():
    status = _status(_entry("gov-id-clean", "gov-id", Disposition.accepted))
    assert is_satisfied(status) is True


def test_single_accepted_bill_does_not_satisfy():
    status = _status(
        _entry("bill-1", "utility-bill", Disposition.accepted, issuer="power-co")
    )
    assert is_satisfied(status) is False


def test_two_bills_distinct_issuers_satisfy():
    status = _status(
        _entry("bill-1", "utility-bill", Disposition.accepted, issuer="power-co"),
        _entry("bill-2", "utility-bill", Disposition.accepted, issuer="aqua-util"),
    )
    assert is_satisfied(status) is True


def test_two_bills_same_issuer_do_not_satisfy():
    """CUJ-10 same-issuer reject half: two power-co bills is still one issuer."""
    status = _status(
        _entry("bill-1", "utility-bill", Disposition.accepted, issuer="power-co"),
        _entry("bill-2", "utility-bill", Disposition.accepted, issuer="power-co"),
    )
    assert is_satisfied(status) is False


def test_rejected_gov_id_does_not_count():
    status = _status(
        _entry("gov-id-bad", "gov-id", Disposition.rejected, gate=Gate.resubmit)
    )
    assert is_satisfied(status) is False


def test_pending_gov_id_does_not_count():
    status = _status(
        _entry("gov-id-maybe", "gov-id", Disposition.pending, gate=Gate.hitl_review)
    )
    assert is_satisfied(status) is False


def test_bill_without_issuer_is_not_counted():
    """A null-issuer accepted bill cannot contribute to the distinct-issuer count."""
    status = _status(
        _entry("bill-1", "utility-bill", Disposition.accepted, issuer=None),
        _entry("bill-2", "utility-bill", Disposition.accepted, issuer="aqua-util"),
    )
    assert is_satisfied(status) is False


def test_empty_status_does_not_satisfy():
    assert is_satisfied(CollectionStatus()) is False
