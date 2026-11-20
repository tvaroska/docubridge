"""Deterministic filename-vs-content issuer mismatch gate and 1->N bundle fan-out."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict

from contract.wire import LedgerEntry

_ISSUER_NOISE_TOKENS = frozenset(
    {
        "quote",
        "quotes",
        "proposal",
        "policy",
        "binder",
        "schedule",
        "addendum",
        "endorsement",
        "agreement",
        "disclosure",
        "appraisal",
        "statement",
        "guide",
        "summary",
        "final",
        "draft",
        "clean",
        "revised",
        "rev",
        "ver",
        "copy",
        "signed",
        "executed",
        "submission",
        "package",
        "packet",
        "inc",
        "incorporated",
        "llc",
        "ltd",
        "limited",
        "corp",
        "corporation",
        "co",
        "company",
        "plc",
        "lp",
        "llp",
        "na",
        "group",
        "holdings",
        "insurance",
        "assurance",
        "reinsurance",
        "underwriters",
        "underwriting",
        "syndicate",
        "mutual",
        "casualty",
        "property",
        "indemnity",
        "benefits",
        "health",
        "life",
        "financial",
        "finance",
        "bank",
        "banking",
        "lending",
        "loans",
        "mortgage",
        "services",
        "solutions",
        "partners",
        "national",
        "global",
        "international",
        "commercial",
        "enterprise",
        "carrier",
        "lender",
        "pdf",
        "docx",
        "xlsx",
        "xlsm",
        "rtf",
        "zip",
        "msg",
        "eml",
    }
)

_DEFAULT_SPLIT_CANDIDATE_COLS = (
    "sub_entity_id",
    "plan_code",
    "plan_id",
    "plan_type",
    "product_line",
    "subsidiary",
    "entity_name",
    "location_id",
)


class IssuerMismatchResult(BaseModel):
    """Deterministic filename-vs-content issuer alignment check."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    mismatch: bool
    filename_issuer_hint: str | None = None
    extracted_issuer: str | None = None
    reason_code: str | None = None
    severity: Literal["none", "warning", "quarantine"] = "none"
    detail: str = ""


def _distinctive_issuer_tokens(label: str) -> set[str]:
    """Extract lowercase distinctive brand tokens from an issuer name or filename."""
    expanded = re.sub(r"([a-z])([A-Z])", r"\1 \2", label or "")
    raw_tokens = re.findall(r"[A-Za-z0-9]+", expanded.lower())
    out: set[str] = set()
    for tok in raw_tokens:
        if len(tok) < 3 or tok in _ISSUER_NOISE_TOKENS:
            continue
        if (
            tok.isdigit()
            or re.match(r"^(?:19|20)\d{2}$", tok)
            or re.match(r"^v\d+$", tok)
        ):
            continue
        out.add(tok)
    return out


def _match_candidate_issuer(
    file_tokens: set[str], candidate_issuers: Sequence[str]
) -> str | None:
    matched: str | None = None
    for cand in candidate_issuers:
        cand_tokens = _distinctive_issuer_tokens(cand)
        if not cand_tokens:
            continue
        if cand_tokens.issubset(file_tokens):
            return cand.strip()
        if cand_tokens & file_tokens:
            matched = cand.strip()
    return matched


def _evaluate_candidate_match(
    matched_candidate: str,
    ext_tokens: set[str],
    cleaned_extracted: str,
) -> IssuerMismatchResult:
    hint_tokens = _distinctive_issuer_tokens(matched_candidate)
    if hint_tokens & ext_tokens:
        return IssuerMismatchResult(
            mismatch=False,
            filename_issuer_hint=matched_candidate,
            extracted_issuer=cleaned_extracted,
            severity="none",
            detail=f"Filename issuer hint {matched_candidate!r} matches content issuer.",
        )
    return IssuerMismatchResult(
        mismatch=True,
        filename_issuer_hint=matched_candidate,
        extracted_issuer=cleaned_extracted,
        reason_code="issuer_filename_content_mismatch",
        severity="quarantine",
        detail=(
            f"Filename indicates issuer {matched_candidate!r}, "
            f"but document content asserts {cleaned_extracted!r}."
        ),
    )


def detect_issuer_mismatch(
    filename: str,
    extracted_issuer: str | None,
    candidate_issuers: Sequence[str] = (),
) -> IssuerMismatchResult:
    """Detect mismatches between an issuer named in ``filename`` and ``extracted_issuer``."""
    cleaned_extracted = (extracted_issuer or "").strip() or None
    stem = (filename or "").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if "." in stem:
        stem = stem.rsplit(".", 1)[0]

    file_tokens = _distinctive_issuer_tokens(stem)
    if not cleaned_extracted or not file_tokens:
        return IssuerMismatchResult(
            mismatch=False,
            filename_issuer_hint=None,
            extracted_issuer=cleaned_extracted,
            severity="none",
            detail="Insufficient filename or extracted issuer signal to flag mismatch.",
        )

    ext_tokens = _distinctive_issuer_tokens(cleaned_extracted)
    if not ext_tokens:
        return IssuerMismatchResult(
            mismatch=False,
            filename_issuer_hint=None,
            extracted_issuer=cleaned_extracted,
            severity="none",
            detail="Extracted issuer carries only generic entity tokens.",
        )

    matched_candidate = _match_candidate_issuer(file_tokens, candidate_issuers)
    if matched_candidate is not None:
        return _evaluate_candidate_match(
            matched_candidate, ext_tokens, cleaned_extracted
        )

    if file_tokens & ext_tokens:
        return IssuerMismatchResult(
            mismatch=False,
            filename_issuer_hint=" ".join(sorted(file_tokens)),
            extracted_issuer=cleaned_extracted,
            severity="none",
            detail="Filename tokens align with extracted issuer.",
        )

    # Heuristic prefix hint when candidate_issuers is omitted: inspect leading segment
    leading_seg = re.split(r"[_\-]+", stem, maxsplit=1)[0]
    leading_tokens = _distinctive_issuer_tokens(leading_seg)
    if leading_tokens and not (leading_tokens & ext_tokens):
        hint_label = leading_seg.strip()
        return IssuerMismatchResult(
            mismatch=True,
            filename_issuer_hint=hint_label,
            extracted_issuer=cleaned_extracted,
            reason_code="issuer_filename_content_mismatch",
            severity="quarantine",
            detail=(
                f"Filename prefix {hint_label!r} conflicts with extracted "
                f"content issuer {cleaned_extracted!r}."
            ),
        )

    return IssuerMismatchResult(
        mismatch=False,
        filename_issuer_hint=None,
        extracted_issuer=cleaned_extracted,
        severity="none",
        detail="No conflicting issuer prefix detected in filename.",
    )


def _slugify_key(raw: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", raw.strip().lower()).strip("-")
    return slug or "item"


def _expand_explicit_sub_entities(
    parent_entry: LedgerEntry,
    sub_entities: Sequence[Mapping[str, object]],
    *,
    split_key: str | None,
) -> list[LedgerEntry]:
    children: list[LedgerEntry] = []
    for idx, sub in enumerate(sub_entities, start=1):
        nested_kf = sub.get("key_fields")
        if isinstance(nested_kf, Mapping):
            child_kf = {
                **parent_entry.key_fields,
                **{str(k): str(v) for k, v in nested_kf.items() if v is not None},
            }
        else:
            child_kf = {
                **parent_entry.key_fields,
                **{
                    str(k): str(v)
                    for k, v in sub.items()
                    if k not in ("tables", "sub_entity_id") and v is not None
                },
            }
        label_val = str(
            sub.get("sub_entity_id")
            or (child_kf.get(split_key) if split_key else None)
            or child_kf.get("plan_code")
            or child_kf.get("plan_type")
            or child_kf.get("product_line")
            or child_kf.get("subsidiary")
            or f"part-{idx}"
        )
        raw_tables = sub.get("tables")
        child_tables = (
            dict(raw_tables)
            if isinstance(raw_tables, Mapping)
            else dict(parent_entry.tables)
        )
        children.append(
            parent_entry.model_copy(
                update={
                    "doc_id": f"{parent_entry.doc_id}#{_slugify_key(label_val)}",
                    "key_fields": child_kf,
                    "tables": child_tables,
                }
            )
        )
    return children


def _group_table_rows(
    rows: Sequence[dict[str, str]],
    resolved_key: str,
) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        group_val = (row.get(resolved_key) or "").strip()
        if group_val:
            grouped.setdefault(group_val, []).append(row)
    return grouped


def _promote_single_valued_columns(
    parent_kf: dict[str, str],
    resolved_key: str,
    group_val: str,
    group_rows: list[dict[str, str]],
) -> dict[str, str]:
    merged_kf = dict(parent_kf)
    merged_kf[resolved_key] = group_val
    all_cols = {col for r in group_rows for col in r}
    for col in sorted(all_cols):
        distinct_vals = {
            r[col].strip() for r in group_rows if col in r and r[col].strip()
        }
        if len(distinct_vals) == 1 and col not in merged_kf:
            merged_kf[col] = next(iter(distinct_vals))
    return merged_kf


def expand_bundled_extractions(
    parent_entry: LedgerEntry,
    *,
    split_key: str | None = None,
    table_name: str | None = None,
    sub_entities: Sequence[Mapping[str, object]] | None = None,
) -> list[LedgerEntry]:
    """Fan out one bundled ``LedgerEntry`` into ``N`` keyed child ``LedgerEntry`` records.

    Preserves the single vaulted artifact provenance (``vault_uri``, ``artifact``,
    ``source_anchors``) while keying each child ``doc_id`` as ``{parent.doc_id}#{slug}``.
    """
    if sub_entities:
        return _expand_explicit_sub_entities(
            parent_entry, sub_entities, split_key=split_key
        )

    target_tbl = table_name or (
        next(iter(parent_entry.tables.keys())) if parent_entry.tables else None
    )
    rows = parent_entry.tables.get(target_tbl, []) if target_tbl else []
    if not target_tbl or not rows:
        return [parent_entry]

    resolved_key = split_key or next(
        (c for c in _DEFAULT_SPLIT_CANDIDATE_COLS if c in rows[0]), None
    )
    if not resolved_key:
        return [parent_entry]

    grouped = _group_table_rows(rows, resolved_key)
    if not grouped:
        return [parent_entry]

    fanned: list[LedgerEntry] = []
    for group_val, group_rows in grouped.items():
        merged_kf = _promote_single_valued_columns(
            parent_entry.key_fields, resolved_key, group_val, group_rows
        )
        updated_tables = dict(parent_entry.tables)
        updated_tables[target_tbl] = list(group_rows)
        fanned.append(
            parent_entry.model_copy(
                update={
                    "doc_id": f"{parent_entry.doc_id}#{_slugify_key(group_val)}",
                    "key_fields": merged_kf,
                    "tables": updated_tables,
                }
            )
        )
    return fanned or [parent_entry]


__all__ = [
    "IssuerMismatchResult",
    "detect_issuer_mismatch",
    "expand_bundled_extractions",
]
