"""Tests for bridge.classification — canonicalization + label-space check (CORE-5)."""

import json
from pathlib import Path

import pytest

from bridge.classification import canonicalize_issuer, is_in_label_space


@pytest.fixture
def corpus_records():
    """Load the corpus expected.json records."""
    corpus_path = Path(__file__).parent.parent / "wiki" / "evals" / "address" / "expected.json"
    with corpus_path.open() as f:
        data = json.load(f)
    return data["documents"]


@pytest.mark.parametrize(
    "record",
    [
        pytest.param(r, id=r["id"])
        for r in json.loads(
            (Path(__file__).parent.parent / "wiki" / "evals" / "address" / "expected.json").read_text()
        )["documents"]
    ],
)
def test_canonicalize_issuer_corpus_parity(record):
    """Corpus issuer parity: canonicalize(issuer_raw) == issuer for all records."""
    assert canonicalize_issuer(record["issuer_raw"]) == record["issuer"]


def test_canonicalize_issuer_documented_variants():
    """Documented variants not in the corpus."""
    assert canonicalize_issuer("PowerCo Ltd") == "power-co"
    assert canonicalize_issuer("PowerCo Inc.") == "power-co"


def test_canonicalize_issuer_co_kept_suffixes_dropped():
    """'co' is kept; corporate suffixes are dropped."""
    # "co" is kept
    assert canonicalize_issuer("PowerCo") == "power-co"
    assert canonicalize_issuer("Power Co.") == "power-co"
    # Suffixes are dropped
    assert canonicalize_issuer("Acme Ltd") == "acme"
    assert canonicalize_issuer("Acme Inc") == "acme"
    assert canonicalize_issuer("Acme LLC") == "acme"


def test_canonicalize_issuer_idempotent(corpus_records):
    """Idempotency: canonicalize(canonicalize(x)) == canonicalize(x)."""
    # Corpus issuer values
    for record in corpus_records:
        issuer = record["issuer"]
        if issuer is not None:
            assert canonicalize_issuer(canonicalize_issuer(issuer)) == canonicalize_issuer(issuer)

    # Unit test strings
    test_strings = ["PowerCo", "Power Co.", "AquaUtil", "Gov", "PowerCo Ltd"]
    for s in test_strings:
        assert canonicalize_issuer(canonicalize_issuer(s)) == canonicalize_issuer(s)


def test_canonicalize_issuer_edge_cases():
    """Edge cases: None, blank, whitespace, suffix-only -> None."""
    assert canonicalize_issuer(None) is None
    assert canonicalize_issuer("") is None
    assert canonicalize_issuer("   ") is None
    assert canonicalize_issuer("Ltd") is None
    assert canonicalize_issuer("Inc.") is None


def test_is_in_label_space():
    """Label-space check."""
    assert is_in_label_space("gov-id", ["gov-id", "utility-bill"]) is True
    assert is_in_label_space("utility-bill", ["gov-id", "utility-bill"]) is True
    assert is_in_label_space("passport", ["gov-id", "utility-bill"]) is False
    assert is_in_label_space("driver-license", ["gov-id"]) is False
