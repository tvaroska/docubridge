"""Pure prompt builders, structured-output schema unions, and JSON parsers for GeminiExtractor."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any

from bridge.doctypes import (
    declares_issuer,
    doctype_description,
    doctype_fields,
    doctype_tables,
    issuer_description,
    key_field_properties,
    slice_ontology_for_extraction,
    table_properties,
    table_spec,
)
from bridge.extraction.resolve import (
    DEFAULT_CORPUS_ROOT,
    ResolvedDocument,
    mime_for,
    resolve_document,
)
from contract.enums import ReviewReason
from contract.extraction import ExtractionResult

# A resolver maps an inbound artifact name/URI to a ``ResolvedDocument`` (bytes, or a
# by-reference ``gs://`` pointer). Injectable so tests never touch disk or the
# network; it may be sync or async (``maybe_await``), and a legacy ``(bytes, mime)``
# 2-tuple is still accepted (``_coerce_resolved``) so every injected byte-source in
# the suite keeps working.
Resolver = Callable[
    [str], "ResolvedDocument | tuple[bytes, str] | Awaitable[ResolvedDocument]"
]

# A pre-pass maps the resolved document to extra grounding text for the request (the
# ``docai`` engine's Document AI OCR text layer), or ``None`` to add nothing. Sync or
# async (``maybe_await``); run ONCE per extraction, never per retry.
Prepass = Callable[[ResolvedDocument], "str | Awaitable[str | None] | None"]

# The extraction contract asked of the model. response_mime_type pins JSON output and
# ``build_response_schema`` pins the field shape; the prompt carries the installed
# doctype catalog between these two halves.
PROMPT_HEADER = """You are a document extraction engine. Read the attached document and return ONLY a JSON object matching the response schema (no prose, no markdown fences)."""

PROMPT_FOOTER = """Rules:
- Report the document type you observe in fields.doctype. If the document matches none of the installed types above, report the type you actually observe (e.g. "passport") — do NOT force it into a listed type.
- All key_fields values MUST be strings; use null for a field this document does not carry.
- A table (fields.tables) is one object per row the document prints, every cell a string copied exactly as printed (amounts are never computed or converted) except that a closed-vocabulary column takes the listed value that matches; use null for a cell the row does not carry, and omit a table the document does not print.
- field_confidence is your per-field confidence 0.0-1.0; overall_confidence covers the extraction as a whole.
- Set legible to false when the document is too degraded to read.
- flagged_fields names fields that look wrong, expired, or need review.
- Every name in flagged_fields SHOULD also appear in field_reviews with its reason: absent_from_document for a field this document does not carry, derived for a value you inferred rather than read off the document, low_confidence when you distrust what you read, model_flagged otherwise. Use the key_fields name (or a dotted path) as the field_reviews path.
- Do not judge acceptance — only extract and report signals."""

# Bounded schema-validated retry (S0-bug-2).
DEFAULT_MAX_ATTEMPTS = 3

# Upper bound on the rejected payload echoed back in the correction turn.
MAX_ECHO_CHARS = 4000

RETRY_CORRECTION = """Your previous output was REJECTED by schema validation. It was not accepted and must be corrected.

Your rejected output{truncated}:
{previous}

Rejection reason:
{error}

Return ONLY the corrected JSON object matching the response schema — no prose, no markdown fence. Every key_fields value and every table cell must be a JSON string (use null for a field this document does not carry), and emit no key the schema does not declare. Correct the STRUCTURE only: keep the values you actually read from the document and never invent one to satisfy the schema."""

# Large documents (S0-perf-1).
DEFAULT_MAX_OUTPUT_TOKENS = 32768
DEFAULT_CHUNK_PAGES = 8
DEFAULT_CHUNK_CONCURRENCY = 4
DEFAULT_MAX_WINDOWS = 128

MAX_OUTPUT_TOKENS_ENV = "BRIDGE_EXTRACTION_MAX_OUTPUT_TOKENS"
CHUNK_PAGES_ENV = "BRIDGE_EXTRACTION_CHUNK_PAGES"
CHUNK_CONCURRENCY_ENV = "BRIDGE_EXTRACTION_CHUNK_CONCURRENCY"

PAGE_WINDOW_INSTRUCTION = """[bridge-page-window {first}-{last}/{total}] This request covers table rows printed on pages {first} to {last} of the attached {total}-page document ONLY. Emit, in printed order, exactly the fields.tables rows printed on those pages (a row belongs to the page it starts on) and no others. Report doctype, issuer, key_fields, legible, overall_confidence and flagged_fields for the document as a whole, as usual. Any row index in a path (flagged_fields, field_reviews, field_confidence) is 0-based WITHIN THIS RESPONSE's table."""

SURVEY_PROMPT = """Count the pages of the attached document. Return ONLY a JSON object {"page_count": N} where N is the total number of pages."""
SURVEY_SCHEMA = {
    "type": "object",
    "properties": {"page_count": {"type": "integer"}},
    "required": ["page_count"],
}
SURVEY_MAX_OUTPUT_TOKENS = 256


def build_window_instruction(first: int, last: int, total: int) -> str:
    """The page-window instruction for pages ``first``-``last`` of ``total`` (pure)."""
    return PAGE_WINDOW_INSTRUCTION.format(first=first, last=last, total=total)


def _int_setting(
    name: str, value: int | None, env: str, raw: str | None, default: int, minimum: int
) -> int:
    """A kwarg wins over its env; malformed/out-of-range refuses loud naming the source."""
    if value is not None:
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise ValueError(f"{name} must be an integer >= {minimum}; got {value!r}")
        return value
    if raw is None or not raw.strip():
        return default
    try:
        parsed = int(raw.strip())
    except ValueError:
        raise ValueError(
            f"{env} must be an integer >= {minimum}; got {raw!r}"
        ) from None
    if parsed < minimum:
        raise ValueError(f"{env} must be an integer >= {minimum}; got {raw!r}")
    return parsed


def _is_truncated(response: Any) -> bool:
    """True when the response was cut at ``max_output_tokens`` (``MAX_TOKENS``)."""
    candidates = getattr(response, "candidates", None)
    if not candidates:
        return False
    reason = getattr(candidates[0], "finish_reason", None)
    return getattr(reason, "name", None) == "MAX_TOKENS" or reason == "MAX_TOKENS"


class _Truncated(Exception):
    """Internal signal: a response hit MAX_TOKENS (never escapes ``extract``)."""

    def __init__(self, label: str) -> None:
        super().__init__(label)
        self.label = label


def build_extraction_prompt(
    schemas: dict[str, dict],
    *,
    extraction_hints: Sequence[Any] = (),
    target_doctype: str | None = None,
    candidate_doctypes: Sequence[str] | None = None,
    context_set: str | None = None,
    revision: Any | None = None,
) -> str:
    """``PROMPT_HEADER`` + the installed-doctype (or target-scoped) field catalog + ``PROMPT_FOOTER``."""
    active_schemas = (
        slice_ontology_for_extraction(
            target_doctype=target_doctype,
            candidate_doctypes=candidate_doctypes,
            revision=revision,
            context_set=context_set,
            schemas=schemas,
        )
        if (target_doctype or candidate_doctypes or context_set)
        else schemas
    )
    scoped_hints = list(extraction_hints)
    if (target_doctype or candidate_doctypes or context_set) and scoped_hints:
        allowed_dtypes = {"*", "", *active_schemas.keys()}
        scoped_hints = [
            h
            for h in scoped_hints
            if str(getattr(h, "doctype", "*")).strip() in allowed_dtypes
        ]

    hints_section = ""
    if scoped_hints:
        hint_lines = [
            "Active Agent Gym extraction hints (apply when matching issuer/doctype):"
        ]
        for hint in scoped_hints:
            rid = getattr(hint, "rule_id", "")
            issuer_pat = getattr(hint, "issuer_pattern", "*")
            dtype = getattr(hint, "doctype", "*")
            fpath = getattr(hint, "field_path", "")
            instr = getattr(hint, "instruction", str(hint))
            prefix = f"{rid} · " if rid else ""
            hint_lines.append(f"- [{prefix}{issuer_pat}:{dtype}:{fpath}] {instr}")
        hints_section = "\n\n" + "\n".join(hint_lines)

    if not active_schemas:
        return f"{PROMPT_HEADER}{hints_section}\n\n{PROMPT_FOOTER}"

    lines = ["Installed document types and the fields each declares:"]
    for doctype in sorted(active_schemas):
        schema = active_schemas[doctype]
        label = doctype
        if declares_issuer(schema):
            note = issuer_description(schema) or "the issuing organization"
            label = f"{doctype} [top-level issuer: {note}]"
        entity_desc = doctype_description(schema)
        fields = doctype_fields(schema)
        tables = doctype_tables(schema)
        if not fields and not tables:
            lines.append(f"- {label}: (no declared fields)")
            continue
        rendered = "; ".join(
            f"{name} ({desc})" if desc else name for name, desc in fields.items()
        )
        if entity_desc:
            lines.append(f"- {label} [{entity_desc}]: {rendered}")
        else:
            lines.append(f"- {label}: {rendered}")
        for table, columns in tables.items():
            cols = "; ".join(
                f"{col} ({desc})" if desc else col for col, desc in columns.items()
            )
            lines.append(f"  - table {table} (one row each): {cols}")
    catalog = "\n".join(lines)
    return f"{PROMPT_HEADER}\n\n{catalog}{hints_section}\n\n{PROMPT_FOOTER}"


def build_response_schema(
    schemas: dict[str, dict],
    *,
    target_doctype: str | None = None,
    candidate_doctypes: Sequence[str] | None = None,
    context_set: str | None = None,
    revision: Any | None = None,
) -> dict | None:
    """The pinned structured-output schema, or ``None`` when no doctype declares fields."""
    active_schemas = (
        slice_ontology_for_extraction(
            target_doctype=target_doctype,
            candidate_doctypes=candidate_doctypes,
            revision=revision,
            context_set=context_set,
            schemas=schemas,
        )
        if (target_doctype or candidate_doctypes or context_set)
        else schemas
    )
    union: dict[str, str] = {}
    for doctype in sorted(active_schemas):
        for name, desc in doctype_fields(active_schemas[doctype]).items():
            labelled = f"{doctype}: {desc}" if desc else doctype
            if name in union:
                union[name] = f"{union[name]} | {labelled}"
            else:
                union[name] = labelled
    if not union:
        return None

    key_fields = {
        name: {"type": "string", "nullable": True, "description": desc}
        for name, desc in union.items()
    }
    enums: dict[str, list | None] = {}
    for doctype in sorted(active_schemas):
        for name, spec in key_field_properties(active_schemas[doctype]).items():
            enum = spec.get("enum") if isinstance(spec, dict) else None
            enum = (
                enum
                if isinstance(enum, list) and all(isinstance(v, str) for v in enum)
                else None
            )
            enums[name] = enum if name not in enums or enums[name] == enum else None
    for name, enum in enums.items():
        if enum and name in key_fields:
            key_fields[name]["enum"] = list(enum)
    confidences = {
        name: {"type": "number", "nullable": True} for name in [*union, "issuer"]
    }
    tables = _tables_union(active_schemas)
    installed = ", ".join(sorted(active_schemas))
    entity_notes = [
        f"{dt} ({doctype_description(active_schemas[dt])})"
        if doctype_description(active_schemas[dt])
        else dt
        for dt in sorted(active_schemas)
    ]
    doctype_desc = (
        f"the document type you observe; installed types: {installed}. "
        f"Contrastive doctype guide: {' | '.join(entity_notes)}"
    )
    fields_properties: dict = {
        "doctype": {
            "type": "string",
            "description": doctype_desc,
        },
        "issuer": {
            "type": "string",
            "nullable": True,
            "description": "the issuing organization, or null",
        },
        "key_fields": {
            "type": "object",
            "description": (
                "Extracted key-value fields for the observed document type using "
                "contrastive positive, layout-anchor, and negative-boundary guidance."
            ),
            "properties": key_fields,
        },
    }
    if tables:
        fields_properties["tables"] = {"type": "object", "properties": tables}
    return {
        "type": "object",
        "description": (
            "Structured document extraction envelope with contrastive doctype and "
            "field descriptions, per-field confidence, and review signals."
        ),
        "properties": {
            "fields": {
                "type": "object",
                "description": (
                    "Observed document type, canonical issuer, and extracted key_fields."
                ),
                "properties": fields_properties,
                "required": ["doctype", "key_fields"],
            },
            "overall_confidence": {"type": "number"},
            "field_confidence": {"type": "object", "properties": confidences},
            "legible": {"type": "boolean"},
            "flagged_fields": {"type": "array", "items": {"type": "string"}},
            "field_reviews": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": (
                                "the reviewed field: its key_fields name, or a dotted "
                                "path such as fields.key_fields.expiry"
                            ),
                        },
                        "reason": {
                            "type": "string",
                            "enum": [r.value for r in ReviewReason],
                        },
                        "confidence": {"type": "number", "nullable": True},
                        "detail": {"type": "string", "nullable": True},
                    },
                    "required": ["path", "reason"],
                },
            },
        },
        "required": ["fields", "overall_confidence", "legible", "flagged_fields"],
    }


def _tables_union(schemas: dict[str, dict]) -> dict:
    """The pinned ``fields.tables`` properties: every installed doctype's tables."""
    union: dict[str, dict[str, dict]] = {}
    descriptions: dict[str, str] = {}
    for doctype in sorted(schemas):
        for table, columns in table_properties(schemas[doctype]).items():
            desc = str(table_spec(schemas[doctype], table).get("description", "") or "")
            labelled = f"{doctype}: {desc}" if desc else doctype
            descriptions[table] = (
                f"{descriptions[table]} | {labelled}"
                if table in descriptions
                else labelled
            )
            cols = union.setdefault(table, {})
            for col, col_spec in columns.items():
                col_spec = col_spec if isinstance(col_spec, dict) else {}
                cell: dict = {"type": "string", "nullable": True}
                if col_spec.get("description"):
                    cell["description"] = str(col_spec["description"])
                enum = col_spec.get("enum")
                if isinstance(enum, list) and all(isinstance(v, str) for v in enum):
                    cell["enum"] = list(enum)
                if col in cols and cols[col].get("enum") != cell.get("enum"):
                    cell.pop("enum", None)
                cols[col] = cell
    return {
        table: {
            "type": "array",
            "description": descriptions[table],
            "items": {"type": "object", "properties": cols},
        }
        for table, cols in union.items()
    }


def build_retry_correction(previous: str, error: Exception) -> str:
    """The correction turn appended after a rejected attempt (pure, unit-testable)."""
    echo = (previous or "").strip()
    truncated = ""
    if len(echo) > MAX_ECHO_CHARS:
        echo = echo[:MAX_ECHO_CHARS]
        truncated = f" (first {MAX_ECHO_CHARS} characters)"
    return RETRY_CORRECTION.format(truncated=truncated, previous=echo, error=error)


_mime_for = mime_for


def artifact_resolver(corpus_root: Path | str = DEFAULT_CORPUS_ROOT) -> Resolver:
    """The DEFAULT resolver: the real one (``resolve_document``), corpus-anchored."""

    async def _resolve(artifact_name: str) -> ResolvedDocument:
        return await resolve_document(artifact_name, corpus_root=corpus_root)

    return _resolve


def _corpus_resolver(corpus_root: Path) -> Resolver:
    """Corpus-only legacy helper: a SYNC ``(bytes, mime)`` reader under ``corpus_root``."""
    root = Path(corpus_root).resolve()

    def _resolve(artifact_name: str) -> tuple[bytes, str]:
        path = (root / artifact_name).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise ValueError(
                f"artifact {artifact_name!r} escapes the corpus root"
            ) from exc
        return path.read_bytes(), mime_for(artifact_name)

    return _resolve


def _coerce_resolved(value: Any) -> ResolvedDocument:
    """Accept a ``ResolvedDocument`` or a legacy ``(bytes, mime_type)`` 2-tuple."""
    if isinstance(value, ResolvedDocument):
        return value
    if isinstance(value, tuple) and len(value) == 2:
        data, mime_type = value
        return ResolvedDocument(data=data, mime_type=mime_type)
    raise TypeError(
        "resolver must return a ResolvedDocument or a (bytes, mime_type) tuple; "
        f"got {type(value).__name__}"
    )


def _prune_nulls(payload: Any) -> Any:
    """Drop null-valued ``key_fields`` / ``field_confidence`` entries and table cells."""
    if not isinstance(payload, dict):
        return payload
    fields = payload.get("fields")
    if isinstance(fields, dict) and isinstance(fields.get("key_fields"), dict):
        fields["key_fields"] = {
            k: v for k, v in fields["key_fields"].items() if v is not None
        }
    if isinstance(fields, dict) and "tables" in fields:
        tables = fields["tables"]
        if tables is None:
            fields.pop("tables")
        elif isinstance(tables, dict):
            fields["tables"] = {
                name: [
                    {c: v for c, v in row.items() if v is not None}
                    if isinstance(row, dict)
                    else row
                    for row in rows
                ]
                if isinstance(rows, list)
                else rows
                for name, rows in tables.items()
                if rows is not None
            }
    if isinstance(payload.get("field_confidence"), dict):
        payload["field_confidence"] = {
            k: v for k, v in payload["field_confidence"].items() if v is not None
        }
    return payload


def _parse_extraction(text: str) -> ExtractionResult:
    """Parse the model's JSON response text into an ``ExtractionResult``."""
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    return ExtractionResult.model_validate(_prune_nulls(json.loads(stripped)))


__all__ = [
    "CHUNK_CONCURRENCY_ENV",
    "CHUNK_PAGES_ENV",
    "DEFAULT_CHUNK_CONCURRENCY",
    "DEFAULT_CHUNK_PAGES",
    "DEFAULT_MAX_ATTEMPTS",
    "DEFAULT_MAX_OUTPUT_TOKENS",
    "DEFAULT_MAX_WINDOWS",
    "MAX_ECHO_CHARS",
    "MAX_OUTPUT_TOKENS_ENV",
    "PAGE_WINDOW_INSTRUCTION",
    "PROMPT_FOOTER",
    "PROMPT_HEADER",
    "RETRY_CORRECTION",
    "SURVEY_MAX_OUTPUT_TOKENS",
    "SURVEY_PROMPT",
    "SURVEY_SCHEMA",
    "Prepass",
    "Resolver",
    "_Truncated",
    "_coerce_resolved",
    "_corpus_resolver",
    "_int_setting",
    "_is_truncated",
    "_mime_for",
    "_parse_extraction",
    "_prune_nulls",
    "_tables_union",
    "artifact_resolver",
    "build_extraction_prompt",
    "build_response_schema",
    "build_retry_correction",
    "build_window_instruction",
]
