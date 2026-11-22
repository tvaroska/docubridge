"""Unit tests for Stage-B deterministic field derivations (P13-9)."""

from __future__ import annotations

from bridge.extraction.derivations import apply_field_derivations
from contract.extraction import ExtractionFields


def test_paid_in_full_requires_exact_status() -> None:
    """Negated or conditional text never mints $0.00; empty wire_reference names the actual source field."""
    negated = ExtractionFields(
        doctype="loan-payoff-statement",
        issuer="EquipLease",
        key_fields={
            "wire_reference": "NOT PAID IN FULL - $452,318.22 outstanding",
        },
    )
    neg_out, _ = apply_field_derivations(negated)
    assert "remaining_liability_balance" not in neg_out.key_fields
    assert "remaining_liability_balance" not in neg_out.derivations

    conditional = ExtractionFields(
        doctype="loan-payoff-statement",
        issuer="EquipLease",
        key_fields={
            "wire_reference": "Loan will be PAID IN FULL upon receipt of $48,210.00",
        },
    )
    cond_out, _ = apply_field_derivations(conditional)
    assert "remaining_liability_balance" not in cond_out.key_fields
    assert "remaining_liability_balance" not in cond_out.derivations

    # Empty wire_reference with valid payoff_waiver_status attributes the right source field
    exact_with_empty_wire = ExtractionFields(
        doctype="loan-payoff-statement",
        issuer="EquipLease",
        key_fields={
            "wire_reference": "",
            "payoff_waiver_status": "PAID_IN_FULL",
        },
    )
    exact_out, _ = apply_field_derivations(exact_with_empty_wire)
    assert exact_out.key_fields["remaining_liability_balance"] == "$0.00"
    deriv = exact_out.derivations["remaining_liability_balance"]
    assert deriv.derived_from == ["payoff_waiver_status"]
    assert deriv.raw_value == "PAID_IN_FULL"

    # Declared showcase recommendation status also derives $0.00 cleanly
    rec_status = ExtractionFields(
        doctype="loan-payoff-statement",
        issuer="EquipLease",
        key_fields={"payoff_waiver_status": "waiver_recommended_paid_in_full_wire"},
    )
    rec_out, _ = apply_field_derivations(rec_status)
    assert rec_out.key_fields["remaining_liability_balance"] == "$0.00"
    assert rec_out.derivations["remaining_liability_balance"].derived_from == [
        "payoff_waiver_status"
    ]
