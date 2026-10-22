"""Per-document classification (sense A): issuer canonicalization + label-space check."""

import re

# Corporate suffixes dropped during canonicalization. NOTE: "co" is deliberately
# ABSENT — it is kept (PowerCo / Power Co. -> power-co), per wiki/evals description.
_CORPORATE_SUFFIXES: frozenset[str] = frozenset(
    {
        "ltd",
        "inc",
        "llc",
        "corp",
        "corporation",
        "incorporated",
        "limited",
        "plc",
        "gmbh",
        "ag",
        "sa",
        "nv",
        "bv",
    }
)
# camelCase/PascalCase boundary: lowercase-or-digit immediately followed by uppercase.
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def canonicalize_issuer(raw: str | None) -> str | None:
    """Canonicalize a raw issuer/company string to a stable key (sense A).

    e.g. 'PowerCo' / 'Power Co.' / 'PowerCo Ltd' -> 'power-co'; 'AquaUtil' -> 'aqua-util'.
    Idempotent: canonicalize_issuer(canonicalize_issuer(x)) == canonicalize_issuer(x).
    Returns None for None / blank / suffix-only input.
    """
    if raw is None:
        return None
    spaced = _CAMEL_BOUNDARY.sub(
        " ", raw
    )  # 1. split camelCase — MUST precede lowercasing
    spaced = re.sub(
        r"[^a-z0-9]+", " ", spaced.lower()
    )  # 2. lowercase + non-alnum -> space
    tokens = [
        t for t in spaced.split() if t not in _CORPORATE_SUFFIXES
    ]  # 3. drop corporate suffixes
    return "-".join(tokens) if tokens else None  # 4. join with hyphens


def is_in_label_space(doctype: str, candidate_doctypes: list[str]) -> bool:
    """True iff the classified doctype is one of the process's candidate doctypes."""
    return doctype in candidate_doctypes
