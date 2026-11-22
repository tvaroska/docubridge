"""Regression tests for strict closed-grammar Decimal currency parsing (P13-2, P13-13)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from bridge.operational_store.consolidation_rules import (
    parse_decimal_money,
    parse_money_amount,
)


@pytest.mark.parametrize(
    ("raw", "expected_amount", "expected_currency"),
    [
        ("$45,000,000", Decimal("45000000.00"), "USD"),
        ("-$250,000.00", Decimal("-250000.00"), "USD"),
        ("$-250,000.00", Decimal("-250000.00"), "USD"),
        ("- $250", Decimal("-250.00"), "USD"),
        ("-USD 1,250.50", Decimal("-1250.50"), "USD"),
        ("($250,000.00)", Decimal("-250000.00"), "USD"),
        ("$(250,000.00)", Decimal("-250000.00"), "USD"),
        ("(250,000.00) USD", Decimal("-250000.00"), "USD"),
        ("(250,000.00)", Decimal("-250000.00"), None),
        ("250.00-", Decimal("-250.00"), None),
        ("$250.00-", Decimal("-250.00"), "USD"),
        ("($45M)", Decimal("-45000000.00"), "USD"),
        ("-$1.85M", Decimal("-1850000.00"), "USD"),
        ("$50K", Decimal("50000.00"), "USD"),
        ("$1.2B", Decimal("1200000000.00"), "USD"),
        ("1,200.00 MXN", Decimal("1200.00"), "MXN"),
        ("€1,000.00", Decimal("1000.00"), "EUR"),
        ("£500.50", Decimal("500.50"), "GBP"),
        ("1e6", Decimal("1000000.00"), None),
        ("1e18", Decimal("1000000000000000000.00"), None),
        ("-1.5e3", Decimal("-1500.00"), None),
        ("2.25E+4", Decimal("22500.00"), None),
        ("+$100.00", Decimal("100.00"), "USD"),
        ("CAD $100", Decimal("100.00"), "CAD"),
        ("AUD $500", Decimal("500.00"), "AUD"),
    ],
)
def test_parse_decimal_money_preserves_signs_currency_and_scientific_notation(
    raw: str, expected_amount: Decimal, expected_currency: str | None
) -> None:
    assert parse_decimal_money(raw) == expected_amount
    parsed = parse_money_amount(raw)
    assert parsed == (expected_amount, expected_currency)


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "N/A",
        "-$ -50.00",
        "(-$50.00)",
        "-(5)",
        "-250.00-",
        "1e19",
        "1e999999",
        "1e50",
        "$1,200 monthly",
        "$500 minimum",
        "Annual premium - 1,200.00",
        "(USD 5,000 per claim)",
        "2% of TIV",
        "1.234,56",
        "Credit: -$500.25",
        "€1,000.00 USD",
        "$($250.00)",
    ],
)
def test_parse_decimal_money_rejects_malformed_prose_or_overflow_inputs(
    raw: str | None,
) -> None:
    assert parse_decimal_money(raw) is None
    assert parse_money_amount(raw) is None
