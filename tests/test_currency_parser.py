"""Regression tests for deterministic Decimal currency parsing (`parse_decimal_money`)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from bridge.operational_store.consolidation_rules import parse_decimal_money


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("$45,000,000", Decimal("45000000.00")),
        ("-$250,000.00", Decimal("-250000.00")),
        ("$-250,000.00", Decimal("-250000.00")),
        ("-USD 1,250.50", Decimal("-1250.50")),
        ("($250,000.00)", Decimal("-250000.00")),
        ("(250,000.00)", Decimal("-250000.00")),
        ("($45M)", Decimal("-45000000.00")),
        ("-$1.85M", Decimal("-1850000.00")),
        ("$50K", Decimal("50000.00")),
        ("1e6", Decimal("1000000.00")),
        ("-1.5e3", Decimal("-1500.00")),
        ("2.25E+4", Decimal("22500.00")),
        ("+$100.00", Decimal("100.00")),
        ("Credit: -$500.25", Decimal("-500.25")),
    ],
)
def test_parse_decimal_money_preserves_signs_and_scientific_notation(
    raw: str, expected: Decimal
) -> None:
    assert parse_decimal_money(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "N/A",
        "-$ -50.00",
        "(-$50.00)",
        "1e999999",
        "1e50",
    ],
)
def test_parse_decimal_money_rejects_malformed_or_overflow_inputs(
    raw: str | None,
) -> None:
    assert parse_decimal_money(raw) is None
