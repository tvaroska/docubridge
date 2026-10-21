"""Gemini extraction engine — the deployed schema-driven extraction engine (P7-6).

The capability-axis sibling of ``FixtureExtractor``: same ``extract(artifact_name)
-> ExtractionResult`` shape, same module posture ("no ADK or seam imports, avoiding
import cycles" — never imports ``bridge.edge``/``bridge.seams``/``agents``), but the
engine is a real Gemini call instead of a corpus lookup. Extraction is the
**capability** axis (``fixture|gemini|docai``, selected by config), NOT a local↔gcp
store swap (design/seams.md) — so this "gcp"/deployed engine sits behind the same
``ExtractionSeam`` the fixture engine does.

**Schema-driven (S0-arch-5).** The prompt and the pinned structured-output schema are
not literals: every installed **doctype** skill's ``bridge-schema`` asset (normally
``assets/schema.json``) is resolved through the ``skill_registry`` seam — by
``bridge/doctypes.py``, the shared reader Path-A validation builds its typed key-field
models from (S0-arch-8), so the prompt and the validator can never learn to disagree
about what a doctype declares — its declared
key fields are rendered into the prompt as the installed-doctype catalog, and the
UNION of those fields is pinned as the request's ``response_schema``. Installing a
doctype skill folder therefore changes extraction with no code change and no redeploy
— the same no-redeploy property the Agent Card already has. ``extract`` takes no
doctype argument (the Bridge *classifies*; doctype is an output), so the catalog is
every installed doctype, and ``fields.doctype`` stays a **free string** — never an
enum over installed types, or an out-of-label-space document (the corpus's passport)
could not be reported and the ``unsupported`` terminal outcome would silently die.

**Schema-validated retry (S0-bug-2).** The parse is the contract: a payload that fails
``ExtractionResult`` validation is re-asked up to ``max_attempts`` times with its own
rejected output and the specific validator reason fed back, then the last error is
raised. No fixture fallback, no silent success — a missing extraction is a measurable
miss; a fabricated one is a lie.

**Large documents — page-window chunking (S0-perf-1).** Output truncation, not the
input window, is what loses a 1000-row rate sheet: one structured response that big is
cut at ``max_output_tokens`` (``finish_reason == MAX_TOKENS``), and re-asking at the same
budget only reproduces the cut. So every request carries an explicit
``max_output_tokens`` (``BRIDGE_EXTRACTION_MAX_OUTPUT_TOKENS``, default 32768 — a
documented budget makes truncation detectable), and a truncated response NEVER enters the
schema-retry loop. Instead the engine asks for less output per request: an inline PDF
longer than ``chunk_pages`` pages (``BRIDGE_EXTRACTION_CHUNK_PAGES``, default 8; 0 = off)
is extracted as page windows up front, and a truncated single call is bisected by page
(a ``gs://`` object or an unparsable PDF first gets one tiny page-count survey call).
Each window request carries the WHOLE document plus a window instruction
(``[prompt, document, (ocr_context), window_instruction]`` — a by-reference object cannot
be split without downloading it, and no PDF library is installed), runs the same bounded
schema-validated retry, and windows run concurrently (``chunk_concurrency``, default 4)
under a hard ``max_windows`` cap. ``bridge/extraction/chunking.py`` merges the windows
code-side — rows concatenated in page order, row paths rebased, disagreements flagged,
never partial: any failed window fails the extraction. A truncated single page raises
:class:`ExtractionTruncated` (a ``ValueError``). Every ``generate_content`` call runs via
``asyncio.to_thread`` so a slow call never blocks the shared event loop (the SSE /
dashboard / portal hosts). A short document (every Address record, every one-page quote)
is still exactly ONE call with the request shape ``[prompt, document]``.

Two independently-testable halves, mirroring the sibling GCP adapters' injectable
seam:

1. :func:`_parse_extraction` (plus the pure builders :func:`build_extraction_prompt` /
   :func:`build_response_schema`) — a pure JSON-text → ``ExtractionResult`` parse. No
   client, no network; unit-testable directly.
2. :meth:`GeminiExtractor.extract` — resolves the document (injectable ``resolver``,
   defaulting to :func:`artifact_resolver`, the real resolver of
   ``bridge/extraction/resolve.py``: a corpus-relative name, a **party's http(s)
   URL fetched under a default-deny egress guard**, or a ``gs://`` object handed to
   the model **by reference** — S0-arch-7), builds the request, and calls Gemini
   through an **injectable** ``client`` (default
   ``None`` → a real ``google.genai.Client()`` built lazily on first use, exactly like
   ``_GcpCloudTasksScheduler(client=...)``). ``google.genai`` is imported lazily INSIDE
   the method so importing this module pulls no genai/heavy client (a structural test
   enforces it); the ``bridge.seams`` import is lazy for the same cycle reason
   (``bridge.seams.local`` imports ``bridge.extraction.fixture`` at module level).

**Document inputs (S0-arch-6, S0-arch-7).** The engine's input envelope is a
*resolved reference*, not a local filename. SVG is passed as a text part; a
``gs://`` reference rides as a native ``Part.from_uri`` (Vertex reads the object —
the Bridge downloads nothing); every other resolved mime rides as an inline bytes
``Part`` — so a **native-digital PDF** (a real text layer) is read directly, whether
it came off the eval corpus or off a third party's signed URL, exercised end-to-end
against the benefits corpus asset
(``wiki/evals/benefits/images/benefits-carrier-c-eur-fr.pdf``).

**Document AI pre-pass (the ``docai`` engine).** A **scanned/raster** PDF (no text
layer) is the ``docai`` engine's envelope, and that engine is THIS extractor with an
injected ``prepass`` (``bridge/extraction/docai.py::DocAIPrepass``): the client's
Document AI processor OCRs the resolved document once, before the retry loop, and the
text rides as a third part next to the original document
(``[prompt, document, ocr_context]``). Everything schema-driven is shared; with no
prepass the request is exactly ``[prompt, document]`` (design/docai-prepass.md).

Parity with the corpus is **terminal-outcome, never ledger-identical** (CLAUDE.md;
design/seams.md): a real Gemini call will not reproduce the corpus's exact confidence
floats, so the live gate asserts the extracted signals feed ``run_disposition_gate``
to the same ``expected_disposition``/``expected_gate``, not float-equality.
"""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Coroutine, Sequence
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from bridge.doctypes import (
    declares_issuer,
    doctype_fields,
    doctype_schemas,
    doctype_tables,
    issuer_description,
    key_field_properties,
    table_properties,
    table_spec,
)
from bridge.extraction.chunking import (
    ExtractionTruncated,
    bisect_window,
    merge_window_results,
    pdf_page_count,
    plan_windows,
)
from bridge.extraction.docai import build_ocr_context
from bridge.extraction.resolve import (
    DEFAULT_CORPUS_ROOT,
    ResolvedDocument,
    maybe_await,
    mime_for,
    resolve_document,
)
from contract.enums import ReviewReason
from contract.extraction import ExtractionResult

_LOG = logging.getLogger("bridge.extraction.gemini")

# The default model — reuse the constant the extraction/bridge agents already use
# (both ``bridge/extraction/agent.py`` and ``bridge/agent.py`` default to this);
# overridable via BRIDGE_MODEL. Do not invent a new default.
DEFAULT_MODEL = "gemini-2.5-flash"

# A resolver maps an inbound artifact name/URI to a ``ResolvedDocument`` (bytes, or a
# by-reference ``gs://`` pointer). Injectable so tests never touch disk or the
# network; it may be sync or async (``maybe_await``), and a legacy ``(bytes, mime)``
# 2-tuple is still accepted (``_coerce_resolved``) so every injected byte-source in
# the suite keeps working. The default is the real resolver — see
# ``bridge/extraction/resolve.py`` for the four inbound shapes and the egress guard.
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

# Bounded schema-validated retry (S0-bug-2). One malformed token must not lose the
# document: a rejected payload is sent back with the SPECIFIC rejection reason and
# re-asked, up to this many attempts. Mirrors the terminal-write retry idiom in
# bridge/seams/gcp (_TerminalRetryStore: attempts=3, warn per failure, error on
# exhaustion, raise the last error). There is deliberately NO fixture fallback: a
# fallback would launder hand-authored corpus values in as if they had been
# extracted. A missing extraction is a measurable miss; a fabricated one is a lie.
DEFAULT_MAX_ATTEMPTS = 3

# Upper bound on the rejected payload echoed back in the correction turn.
MAX_ECHO_CHARS = 4000

RETRY_CORRECTION = """Your previous output was REJECTED by schema validation. It was not accepted and must be corrected.

Your rejected output{truncated}:
{previous}

Rejection reason:
{error}

Return ONLY the corrected JSON object matching the response schema — no prose, no markdown fence. Every key_fields value and every table cell must be a JSON string (use null for a field this document does not carry), and emit no key the schema does not declare. Correct the STRUCTURE only: keep the values you actually read from the document and never invent one to satisfy the schema."""

# Large documents (S0-perf-1). An explicit output budget on EVERY request (single,
# window, survey): a deterministic, documented budget is what makes truncation
# detectable, and the window sizing (8 pages x ~40 rows x ~60 tokens ~= 19k) leaves
# ~40% headroom for 2.5-flash thinking tokens, which count against the same budget.
DEFAULT_MAX_OUTPUT_TOKENS = 32768
# An inline PDF longer than this many pages is extracted as page windows of this size.
DEFAULT_CHUNK_PAGES = 8
# Window requests in flight at once (per extraction).
DEFAULT_CHUNK_CONCURRENCY = 4
# Hard cap on window requests per extraction (retries not counted) — bounds cost.
DEFAULT_MAX_WINDOWS = 128

MAX_OUTPUT_TOKENS_ENV = "BRIDGE_EXTRACTION_MAX_OUTPUT_TOKENS"
CHUNK_PAGES_ENV = "BRIDGE_EXTRACTION_CHUNK_PAGES"
CHUNK_CONCURRENCY_ENV = "BRIDGE_EXTRACTION_CHUNK_CONCURRENCY"

# Appended as the LAST base part of a window request. Starts with an ASCII sentinel a
# test double (and a log reader) can key on; asks for the window's rows only, but the
# whole-document scalars as usual so the merge can cross-check them.
PAGE_WINDOW_INSTRUCTION = """[bridge-page-window {first}-{last}/{total}] This request covers table rows printed on pages {first} to {last} of the attached {total}-page document ONLY. Emit, in printed order, exactly the fields.tables rows printed on those pages (a row belongs to the page it starts on) and no others. Report doctype, issuer, key_fields, legible, overall_confidence and flagged_fields for the document as a whole, as usual. Any row index in a path (flagged_fields, field_reviews, field_confidence) is 0-based WITHIN THIS RESPONSE's table."""

# The page-count survey (a truncated single call over a document whose page count the
# stdlib reader cannot see — a gs:// object, or an unparsable PDF).
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


def _is_truncated(response) -> bool:
    """True when the response was cut at ``max_output_tokens`` (``MAX_TOKENS``).

    Reads ``candidates[0].finish_reason`` as either the genai enum (``.name``) or a
    plain string. No candidates (a bare test double) → not truncated. Checked BEFORE
    parsing: a truncated constrained-decoding body never closes its braces, so the flag,
    not the JSON error, is authoritative.
    """
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
) -> str:
    """``PROMPT_HEADER`` + the installed-doctype field catalog + ``PROMPT_FOOTER``.

    Pure — ``schemas`` is (doctype skill name -> parsed ``bridge-schema`` document),
    normally read through the registry seam by ``bridge.doctypes.doctype_schemas``.
    Doctypes render sorted by name for determinism. A doctype whose schema declares
    no fields (an installed-but-asset-less skill) renders name-only, so it still
    reaches the model's label space. Optional promoted Agent Gym ``extraction_hints``
    are appended only when non-empty.
    """
    hints_section = ""
    if extraction_hints:
        hint_lines = ["Active Agent Gym extraction hints (apply when matching issuer/doctype):"]
        for hint in extraction_hints:
            rid = getattr(hint, "rule_id", "")
            issuer_pat = getattr(hint, "issuer_pattern", "*")
            dtype = getattr(hint, "doctype", "*")
            fpath = getattr(hint, "field_path", "")
            instr = getattr(hint, "instruction", str(hint))
            prefix = f"{rid} · " if rid else ""
            hint_lines.append(f"- [{prefix}{issuer_pat}:{dtype}:{fpath}] {instr}")
        hints_section = "\n\n" + "\n".join(hint_lines)

    if not schemas:
        # No installed doctypes → no catalog section (never an empty header).
        return f"{PROMPT_HEADER}{hints_section}\n\n{PROMPT_FOOTER}"

    lines = ["Installed document types and the fields each declares:"]
    for doctype in sorted(schemas):
        schema = schemas[doctype]
        label = doctype
        if declares_issuer(schema):
            note = issuer_description(schema) or "the issuing organization"
            label = f"{doctype} [top-level issuer: {note}]"
        fields = doctype_fields(schema)
        tables = doctype_tables(schema)
        if not fields and not tables:
            lines.append(f"- {label}: (no declared fields)")
            continue
        rendered = "; ".join(
            f"{name} ({desc})" if desc else name for name, desc in fields.items()
        )
        lines.append(f"- {label}: {rendered}")
        for table, columns in tables.items():
            cols = "; ".join(
                f"{col} ({desc})" if desc else col for col, desc in columns.items()
            )
            lines.append(f"  - table {table} (one row each): {cols}")
    catalog = "\n".join(lines)
    return f"{PROMPT_HEADER}\n\n{catalog}{hints_section}\n\n{PROMPT_FOOTER}"


def build_response_schema(schemas: dict[str, dict]) -> dict | None:
    """The pinned structured-output schema, or ``None`` when no doctype declares fields.

    ``key_fields``/``field_confidence`` are pinned as the UNION of every installed
    doctype's declared fields — every property ``nullable`` and none required, because
    one document only carries one doctype's fields (and ``bill-aquautil-blurry`` in the
    corpus carries none at all). ``_parse_extraction`` prunes the resulting nulls.

    ``fields.doctype`` is deliberately a FREE STRING with no ``enum``: the Bridge
    classifies, and an out-of-label-space document (the corpus's passport) must stay
    reportable so the ``unsupported`` terminal outcome survives.

    Returns ``None`` when the union is empty — a ``key_fields`` object with zero
    declared properties would forbid every field, a silent capability loss; better to
    send JSON mime only. No ``additionalProperties`` anywhere (the Gemini backend's
    OpenAPI subset does not reliably accept it).
    """
    union: dict[str, str] = {}
    for doctype in sorted(schemas):
        for name, desc in doctype_fields(schemas[doctype]).items():
            labelled = f"{doctype}: {desc}" if desc else doctype
            if name in union:
                # A field shared by two doctypes (``name`` today) keeps both notes.
                union[name] = f"{union[name]} | {labelled}"
            else:
                union[name] = labelled
    if not union:
        return None

    key_fields = {
        name: {"type": "string", "nullable": True, "description": desc}
        for name, desc in union.items()
    }
    # A closed vocabulary (``product_line``) is pinned only while every declaring
    # doctype agrees on it — the same rule the table columns follow.
    enums: dict[str, list | None] = {}
    for doctype in sorted(schemas):
        for name, spec in key_field_properties(schemas[doctype]).items():
            enum = spec.get("enum") if isinstance(spec, dict) else None
            enum = enum if isinstance(enum, list) and all(
                isinstance(v, str) for v in enum
            ) else None
            enums[name] = enum if name not in enums or enums[name] == enum else None
    for name, enum in enums.items():
        if enum and name in key_fields:
            key_fields[name]["enum"] = list(enum)
    # issuer is a top-level field, not a key_field, but the corpus reports a
    # confidence for it — keep it in the field_confidence union.
    confidences = {
        name: {"type": "number", "nullable": True}
        for name in [*union, "issuer"]
    }
    tables = _tables_union(schemas)
    installed = ", ".join(sorted(schemas))
    fields_properties: dict = {
        "doctype": {
            "type": "string",
            "description": (
                "the document type you observe; installed types: "
                f"{installed}"
            ),
        },
        "issuer": {
            "type": "string",
            "nullable": True,
            "description": "the issuing organization, or null",
        },
        "key_fields": {"type": "object", "properties": key_fields},
    }
    if tables:
        # Row-shaped data (S0-arch-10). Optional (not in `required`): only a
        # doctype that prints rows fills it, so every other document regresses nothing.
        fields_properties["tables"] = {"type": "object", "properties": tables}
    return {
        "type": "object",
        "properties": {
            "fields": {
                "type": "object",
                "properties": fields_properties,
                "required": ["doctype", "key_fields"],
            },
            "overall_confidence": {"type": "number"},
            "field_confidence": {"type": "object", "properties": confidences},
            "legible": {"type": "boolean"},
            "flagged_fields": {"type": "array", "items": {"type": "string"}},
            # Provenance for the review view (S0-debt-2): WHY a field needs review.
            # Optional (NOT in `required`) — `flagged_fields` stays the required
            # signal, so an engine that says nothing here regresses nothing; the
            # contract derives `model_flagged`/`absent_from_document` for it.
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
    """The pinned ``fields.tables`` properties: every installed doctype's tables.

    Each table is an array of row objects whose cells are nullable strings (a closed
    ``enum`` column keeps its vocabulary), none required — ``_parse_extraction`` prunes
    the null cells. Two doctypes declaring the same table name share one column union;
    an enum survives only while every declaring doctype agrees on it.
    """
    union: dict[str, dict[str, dict]] = {}
    descriptions: dict[str, str] = {}
    for doctype in sorted(schemas):
        for table, columns in table_properties(schemas[doctype]).items():
            desc = str(table_spec(schemas[doctype], table).get("description", "") or "")
            labelled = f"{doctype}: {desc}" if desc else doctype
            descriptions[table] = (
                f"{descriptions[table]} | {labelled}" if table in descriptions else labelled
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
    """The correction turn appended after a rejected attempt (pure, unit-testable).

    Carries BOTH the model's own rejected output and the specific validator reason —
    the echo is what makes the next attempt a repair rather than a re-roll. The echo
    is bounded (``MAX_ECHO_CHARS``) so a runaway response cannot balloon the follow-up
    request.
    """
    echo = (previous or "").strip()
    truncated = ""
    if len(echo) > MAX_ECHO_CHARS:
        echo = echo[:MAX_ECHO_CHARS]
        truncated = f" (first {MAX_ECHO_CHARS} characters)"
    return RETRY_CORRECTION.format(truncated=truncated, previous=echo, error=error)


# The suffix→mime table is shared with the edge's artifact persistence and lives in
# ``resolve.py`` (one table, two callers). Kept under the private name this module
# has always exported.
_mime_for = mime_for


def artifact_resolver(corpus_root: Path | str = DEFAULT_CORPUS_ROOT) -> Resolver:
    """The DEFAULT resolver: the real one (``resolve_document``), corpus-anchored.

    Handles all four inbound shapes — a ``gs://`` object by reference, a party's
    http(s) URL fetched under the default-deny egress guard, an opt-in ``file://``
    path, and a bare corpus-relative name resolved under ``corpus_root``. Async,
    because the fetch is I/O and ``extract`` runs on a shared event loop.
    """

    async def _resolve(artifact_name: str) -> ResolvedDocument:
        return await resolve_document(artifact_name, corpus_root=corpus_root)

    return _resolve


def _corpus_resolver(corpus_root: Path) -> Resolver:
    """Corpus-only legacy helper: a SYNC ``(bytes, mime)`` reader under ``corpus_root``.

    Predates :func:`artifact_resolver` and is kept because a corpus-relative read is
    all some callers/tests want (and because it is sync). Contained to the corpus
    root (no path traversal). Anything with a scheme belongs to
    :func:`artifact_resolver`.
    """

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


def _coerce_resolved(value) -> ResolvedDocument:
    """Accept a ``ResolvedDocument`` or a legacy ``(bytes, mime_type)`` 2-tuple.

    The 2-tuple is the shape every injected test resolver uses and the shape
    :func:`_corpus_resolver` returns; normalizing here is what let the engine's input
    envelope become a *resolved reference* without touching a single caller.
    """
    if isinstance(value, ResolvedDocument):
        return value
    if isinstance(value, tuple) and len(value) == 2:
        data, mime_type = value
        return ResolvedDocument(data=data, mime_type=mime_type)
    raise TypeError(
        "resolver must return a ResolvedDocument or a (bytes, mime_type) tuple; "
        f"got {type(value).__name__}"
    )


def _prune_nulls(payload):
    """Drop null-valued ``key_fields`` / ``field_confidence`` entries and table cells.

    A direct consequence of pinning the UNION of every installed doctype's fields as
    the response schema: a model answering under it emits ``null`` for the fields that
    belong to the OTHER doctypes. ``key_fields`` is ``dict[str, str]`` and
    ``field_confidence`` is ``dict[str, float]``, so a ``None`` value would fail
    validation. Only null-valued entries inside those two open dicts are dropped — the
    top-level ``extra="forbid"`` loudness is deliberately untouched.
    """
    if not isinstance(payload, dict):
        return payload
    fields = payload.get("fields")
    if isinstance(fields, dict) and isinstance(fields.get("key_fields"), dict):
        fields["key_fields"] = {
            k: v for k, v in fields["key_fields"].items() if v is not None
        }
    if isinstance(fields, dict) and "tables" in fields:
        # Same union-schema consequence one level down (S0-arch-10): a null table and
        # a null cell are absences. A non-dict row is left for validation to reject.
        tables = fields["tables"]
        if tables is None:
            fields.pop("tables")
        elif isinstance(tables, dict):
            fields["tables"] = {
                name: [
                    {c: v for c, v in row.items() if v is not None}
                    if isinstance(row, dict) else row
                    for row in rows
                ] if isinstance(rows, list) else rows
                for name, rows in tables.items()
                if rows is not None
            }
    if isinstance(payload.get("field_confidence"), dict):
        payload["field_confidence"] = {
            k: v for k, v in payload["field_confidence"].items() if v is not None
        }
    return payload


def _parse_extraction(text: str) -> ExtractionResult:
    """Parse the model's JSON response text into an ``ExtractionResult``.

    Pure: no client, no network. The response is expected to be a bare JSON object
    (response_mime_type=application/json); a fenced ```json block is tolerated by
    stripping the fence before validating. Null-valued key_fields/field_confidence
    entries are pruned (``_prune_nulls`` — the union-schema consequence).
    ``ExtractionResult`` is ``extra="forbid"`` so a stray field fails loud rather
    than silently passing.
    """
    stripped = text.strip()
    if stripped.startswith("```"):
        # Tolerate an accidental ```json ... ``` fence: drop the first and last lines.
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    return ExtractionResult.model_validate(_prune_nulls(json.loads(stripped)))


class GeminiExtractor:
    """Schema-driven Gemini extraction engine (P7-6) — behind the extraction seam.

    Satisfies the same ``extract(artifact_name) -> ExtractionResult`` shape as
    ``FixtureExtractor``. The ``client``, ``resolver`` and ``schemas`` are injectable
    (the credential-free test seam): an injected fake ``client`` wins, else a real
    ``google.genai.Client()`` is built lazily on first ``extract`` — importing this
    module never constructs a client or pulls ``google.genai``. An injected
    ``schemas`` dict wins over the registry read, so the prompt/schema builders are
    exercisable with no skills dir at all.

    A payload the ``ExtractionResult`` validator rejects is re-asked up to
    ``max_attempts`` times with the specific rejection reason fed back; on exhaustion
    the last validation error is raised — never a fixture fallback.

    An optional ``prepass`` (the ``docai`` engine's ``DocAIPrepass``) runs once per
    extraction on the resolved document; its text, when non-empty, is added to the
    request as a bounded OCR grounding part (``docai.build_ocr_context``).

    **Large documents (S0-perf-1).** ``max_output_tokens`` / ``chunk_pages`` /
    ``chunk_concurrency`` default from ``BRIDGE_EXTRACTION_MAX_OUTPUT_TOKENS`` /
    ``BRIDGE_EXTRACTION_CHUNK_PAGES`` / ``BRIDGE_EXTRACTION_CHUNK_CONCURRENCY`` (read at
    construction; a kwarg wins; a malformed or out-of-range value raises ``ValueError``
    naming the env — so ``GcpExtraction`` building ``GeminiExtractor()`` with no kwargs
    gets the deployed knobs). A long inline PDF is extracted as concurrent page windows
    and a truncated response is bisected by page, never re-asked at the same budget;
    ``chunk_pages=0`` disables both (a truncation then raises
    :class:`ExtractionTruncated` at once). ``max_windows`` caps window requests per
    extraction. With the OCR pre-pass, every window carries the same whole-document OCR
    part (bounded at ``MAX_OCR_CHARS``), so a long scan's later pages may have no OCR
    grounding — a known limitation (the document itself is still attached).
    """

    def __init__(
        self,
        *,
        client=None,
        resolver: Resolver | None = None,
        model: str | None = None,
        corpus_root: Path | str = DEFAULT_CORPUS_ROOT,
        schemas: dict[str, dict] | None = None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        prepass: Prepass | None = None,
        max_output_tokens: int | None = None,
        chunk_pages: int | None = None,
        chunk_concurrency: int | None = None,
        max_windows: int = DEFAULT_MAX_WINDOWS,
        agent_gym: Any = None,
        log=_LOG,
    ) -> None:
        import os

        if max_attempts < 1:
            raise ValueError(f"max_attempts must be >= 1; got {max_attempts}")
        self._max_output_tokens = _int_setting(
            "max_output_tokens", max_output_tokens, MAX_OUTPUT_TOKENS_ENV,
            os.environ.get(MAX_OUTPUT_TOKENS_ENV), DEFAULT_MAX_OUTPUT_TOKENS, 1,
        )
        self._chunk_pages = _int_setting(
            "chunk_pages", chunk_pages, CHUNK_PAGES_ENV,
            os.environ.get(CHUNK_PAGES_ENV), DEFAULT_CHUNK_PAGES, 0,
        )
        self._chunk_concurrency = _int_setting(
            "chunk_concurrency", chunk_concurrency, CHUNK_CONCURRENCY_ENV,
            os.environ.get(CHUNK_CONCURRENCY_ENV), DEFAULT_CHUNK_CONCURRENCY, 1,
        )
        self._max_windows = _int_setting(
            "max_windows", max_windows, "max_windows", None, DEFAULT_MAX_WINDOWS, 1
        )
        self._client = client  # injected fake wins; else built lazily on first use
        self._resolver = resolver or artifact_resolver(Path(corpus_root))
        self._model = model or os.environ.get("BRIDGE_MODEL", DEFAULT_MODEL)
        self._max_attempts = max_attempts
        self._prepass = prepass
        self._agent_gym = agent_gym
        self._log = log
        # Injected catalog wins; else the registry is read per extract (see _catalog).
        self._schemas_fixed = schemas
        self._schemas: dict[str, dict] | None = None
        self._hint_ids: tuple[str, ...] = ()
        self._prompt: str | None = None
        self._response_schema: dict | None = None

    def _catalog(self) -> tuple[str, dict | None]:
        """Prompt + pinned response schema — rebuilt only when the catalog changed.

        The only place the extraction path reads the ``skill_registry`` seam. It is
        read on each ``extract`` (not at construction), because one extractor outlives
        the app's start and a doctype skill dropped into ``BRIDGE_SKILLS_DIR`` after
        the first extract must reach the next prompt and response schema (CUJ-9). The
        read is one directory listing against a model call; the prompt and schema are
        rebuilt only when the catalog content differs from the last one seen. An
        injected ``schemas`` catalog is fixed and never re-read.
        """
        schemas = self._schemas_fixed if self._schemas_fixed is not None else doctype_schemas()
        hints: list[Any] = (
            list(self._agent_gym.get_extraction_hints())
            if self._agent_gym is not None
            else []
        )
        hint_ids = tuple(getattr(h, "rule_id", str(h)) for h in hints)
        if self._prompt is None or schemas != self._schemas or hint_ids != self._hint_ids:
            self._schemas = schemas
            self._hint_ids = hint_ids
            self._prompt = build_extraction_prompt(schemas, extraction_hints=hints)
            self._response_schema = build_response_schema(schemas)
        return self._prompt, self._response_schema

    def _get_client(self):
        # Lazy: importing this module must not pull google.genai / build a real
        # Client (no creds). An injected client (the credential-free tests) wins.
        if self._client is None:
            from google import genai

            self._client = genai.Client()
        return self._client

    def _document_part(self, resolved: ResolvedDocument, *, client=None):
        """Build the document content part for the request from a resolved reference.

        Three branches:

        * **By reference** (a ``gs://`` object, S0-arch-7): ``Part.from_uri`` — the
          backend reads the object, the Bridge downloads nothing. *Vertex gotcha:*
          ``gs://`` is readable only on the Vertex AI backend, so an explicitly
          non-Vertex client (``client.vertexai is False``) refuses loud naming the
          env that fixes it, rather than sending a part the API will reject. The
          check is ``is False``, never falsy — a test double has no such attribute
          and must pass through.
        * **SVG**: the Address corpus images are text SVGs and Gemini may reject raw
          SVG bytes as an image part, so an SVG — and ONLY an SVG — passes as a text
          part (a valid, simpler path).
        * **Everything else, notably ``application/pdf``**: an inline bytes ``Part``
          (lazily-imported genai types), which is what makes a native-digital PDF a
          first-class input: ``tests/test_pdf_extraction.py`` drives
          ``wiki/evals/benefits/images/benefits-carrier-c-eur-fr.pdf`` through this
          method and asserts the request carries ``inline_data`` of
          ``application/pdf``, not the SVG text branch (S0-arch-6).

        A **scanned/raster** PDF (no text layer, needing OCR) still rides through
        this same branch; what the ``docai`` engine adds is the Document AI OCR text
        as a separate part next to it (see ``extract``), never a replacement for the
        document itself.
        """
        if resolved.by_reference:
            if getattr(client, "vertexai", None) is False:
                raise ValueError(
                    f"by-reference artifact {resolved.uri!r} needs the Vertex AI "
                    f"backend (gs:// is not readable by the Gemini Developer API); "
                    f"set GOOGLE_GENAI_USE_VERTEXAI=TRUE and GOOGLE_CLOUD_PROJECT"
                )
            from google.genai import types as genai_types

            return genai_types.Part.from_uri(
                file_uri=resolved.uri, mime_type=resolved.mime_type
            )
        if resolved.mime_type == "image/svg+xml":
            return resolved.data.decode("utf-8")
        from google.genai import types as genai_types

        return genai_types.Part.from_bytes(
            data=resolved.data, mime_type=resolved.mime_type
        )

    async def extract(self, artifact_name: str) -> ExtractionResult:
        """Extract normalized fields + disposition signals via a Gemini call.

        Resolves ``artifact_name`` — which is the URI the counterparty put on the
        wire, not a local filename — through the injectable ``resolver`` (sync or
        async, ``ResolvedDocument`` or a legacy ``(bytes, mime)`` tuple), sends the
        document + the schema-driven extraction prompt to Gemini with JSON output
        and the pinned
        structured-output schema (the installed doctypes' field union) on the request,
        and parses the response into an ``ExtractionResult`` (pure
        ``_parse_extraction``). No installed doctype declares fields → JSON mime only,
        never an empty-properties schema that would forbid every field.

        **Schema-validated retry (S0-bug-2).** ``ExtractionResult`` is
        ``extra="forbid"``, so one stray field or one truncated token rejects the
        whole payload. A rejection is retried up to ``max_attempts`` times, each
        follow-up turn carrying the model's own rejected output plus the SPECIFIC
        validator reason; the catalog, the resolved bytes and the pinned schema are
        computed once and reused across attempts (a retry must not re-fetch the
        artifact). The optional ``prepass`` (Document AI OCR) runs once here, before
        the loop, and its grounding part sits in the base contents so every retry
        keeps it; a pre-pass error propagates unchanged with no Gemini call made (no
        fallback to plain Gemini). On exhaustion the LAST error is raised (type
        preserved), annotated with the attempt count. There is no fixture fallback
        and no silent success: a
        fabricated extraction is worse than a raised error. Transport/API errors are
        deliberately NOT caught — a 429/500 is not a schema rejection and must
        surface unchanged.

        **Large documents (S0-perf-1).** Order of operations: catalog → resolve once →
        client → document part → prepass once → stdlib page count (inline PDF only) →
        single call or page windows. An inline PDF over ``chunk_pages`` pages goes
        straight to page windows; a single call cut at ``max_output_tokens`` is bisected
        by page (after one page-count survey when the count is unknown) instead of
        entering the retry loop. Each window runs the same retry loop; any window
        failure fails the whole extraction (siblings cancelled, the original exception
        type re-raised). A truncation that cannot be chunked smaller raises
        :class:`ExtractionTruncated`.
        """
        prompt, response_schema = self._catalog()
        # The catalog is built first on purpose: a registry/authoring error must
        # surface before any artifact fetch is issued.
        resolved = _coerce_resolved(await maybe_await(self._resolver(artifact_name)))
        client = self._get_client()
        document = self._document_part(resolved, client=client)
        config: dict = {"response_mime_type": "application/json"}
        if response_schema is not None:
            config["response_schema"] = response_schema
        config["max_output_tokens"] = self._max_output_tokens

        base = [prompt, document]
        if self._prepass is not None:
            # Once per extraction, never per retry or per window; an error is not caught.
            ocr = await maybe_await(self._prepass(resolved))
            if ocr:
                base.append(build_ocr_context(ocr))

        is_pdf = resolved.mime_type == "application/pdf"
        pages = (
            pdf_page_count(resolved.data)
            if is_pdf and not resolved.by_reference
            else None
        )
        if self._chunk_pages and pages is not None and pages > self._chunk_pages:
            # Proactive: a long inline PDF never risks one giant structured output.
            self._check_window_cap(-(-pages // self._chunk_pages), artifact_name)
            windows = plan_windows(pages, self._chunk_pages)
            self._log.info(
                "extraction of %s: %d pages -> %d page windows of %d",
                artifact_name, pages, len(windows), self._chunk_pages,
            )
            return await self._extract_windows(
                client, base, config, windows, total=pages, label=artifact_name
            )

        try:
            return await self._generate_validated(
                client, base, config, label=artifact_name
            )
        except _Truncated:
            pass
        # Reactive: the single call was cut at the output budget. Re-asking at the same
        # budget reproduces the cut, so bisect by page instead (never the retry loop).
        if not self._chunk_pages:
            raise self._truncated(
                artifact_name, "page-window chunking is disabled (chunk_pages=0)"
            )
        if pages is None and is_pdf:
            pages = await self._survey_page_count(client, document, artifact_name)
        if pages is None or pages < 2:
            what = (
                f"a {resolved.mime_type} document is not paged"
                if pages is None
                else "the document has a single page"
            )
            raise self._truncated(artifact_name, f"{what}; it cannot be chunked smaller")
        self._log.warning(
            "extraction of %s truncated at max_output_tokens=%d; bisecting its %d pages",
            artifact_name, self._max_output_tokens, pages,
        )
        return await self._extract_windows(
            client, base, config, list(bisect_window(1, pages)), total=pages,
            label=artifact_name,
        )

    async def _call(self, client, contents: list, config: dict):
        """One ``generate_content`` call, OFF the event loop (``asyncio.to_thread``).

        The genai call is sync; run inline it would block the shared loop (SSE /
        dashboard / portal on the same Cloud Run instance) for its whole latency. Every
        test double implements only ``.models.generate_content``, so the thread hop —
        not ``client.aio`` — is the seam-neutral fix.
        """
        return await asyncio.to_thread(
            client.models.generate_content,
            model=self._model,
            contents=contents,
            config=config,
        )

    async def _generate_validated(
        self, client, base: list, config: dict, *, label: str
    ) -> ExtractionResult:
        """The bounded schema-validated retry loop (S0-bug-2) for one request.

        ``base`` is the request's fixed contents (prompt, document, optional OCR part,
        optional window instruction); a correction turn is appended AFTER it. ``label``
        names the artifact (``"<artifact> pages a-b"`` for a window) in logs and notes.
        A MAX_TOKENS response raises the internal ``_Truncated`` signal BEFORE parsing —
        a truncated output is never echoed back and re-asked at the same budget.
        """
        contents = list(base)
        last_error: Exception | None = None
        for attempt in range(1, self._max_attempts + 1):
            # Not wrapped in try/except: a transport/API error is not a schema
            # rejection — it propagates unchanged and burns no attempt.
            response = await self._call(client, contents, config)
            if _is_truncated(response):
                raise _Truncated(label)
            # ``text`` is None on a safety block; normalize so an empty body is a
            # retryable rejection, not an AttributeError.
            raw = (getattr(response, "text", None) or "").strip()
            try:
                return _parse_extraction(raw)
            except (ValidationError, json.JSONDecodeError) as exc:
                last_error = exc
                self._log.warning(
                    "extraction of %s rejected by schema validation "
                    "(attempt %d/%d): %r",
                    label,
                    attempt,
                    self._max_attempts,
                    exc,
                )
                contents = [*base, build_retry_correction(raw, exc)]

        self._log.error(
            "extraction of %s exhausted %d schema-validated attempts: %r",
            label,
            self._max_attempts,
            last_error,
        )
        last_error.add_note(
            f"bridge extraction: {self._max_attempts} schema-validated attempt(s) "
            f"rejected for artifact {label!r}; raising the last validation "
            f"error — there is deliberately no fixture fallback (a fallback would "
            f"launder hand-authored values in as if extracted)."
        )
        raise last_error

    def _truncated(self, label: str, why: str) -> ExtractionTruncated:
        """An ``ExtractionTruncated`` for ``label`` with the budget in its note."""
        error = ExtractionTruncated(
            f"extraction of {label!r} was truncated at "
            f"max_output_tokens={self._max_output_tokens}: {why}"
        )
        error.add_note(
            f"bridge extraction: artifact {label!r} — output cut at the "
            f"max_output_tokens={self._max_output_tokens} budget; {why}. Raise "
            f"{MAX_OUTPUT_TOKENS_ENV} or lower {CHUNK_PAGES_ENV}; there is no partial "
            f"result (partial rows would masquerade as a complete document)."
        )
        return error

    def _check_window_cap(self, needed: int, label: str) -> None:
        if needed > self._max_windows:
            raise self._truncated(
                label,
                f"it needs {needed} page windows, over the max_windows="
                f"{self._max_windows} cap",
            )

    async def _survey_page_count(self, client, document, label: str) -> int:
        """One tiny call asking the model for the page count (unknown-count PDFs only).

        ``[SURVEY_PROMPT, document]`` under ``SURVEY_SCHEMA`` with a 256-token budget.
        Bad JSON, a missing/non-integer/< 1 count, or a truncated survey → raise
        ``ExtractionTruncated`` ("survey failed"); a transport error propagates.
        """
        config = {
            "response_mime_type": "application/json",
            "response_schema": SURVEY_SCHEMA,
            "max_output_tokens": SURVEY_MAX_OUTPUT_TOKENS,
        }
        response = await self._call(client, [SURVEY_PROMPT, document], config)
        raw = (getattr(response, "text", None) or "").strip()
        count = None
        if not _is_truncated(response):
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = None
            value = payload.get("page_count") if isinstance(payload, dict) else None
            if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
                count = value
        if count is None:
            raise self._truncated(
                label, f"the page-count survey failed (answered {raw[:200]!r})"
            )
        self._log.info("extraction of %s: survey reports %d pages", label, count)
        return count

    async def _extract_windows(
        self,
        client,
        base: list,
        config: dict,
        windows: list[tuple[int, int]],
        *,
        total: int,
        label: str,
    ) -> ExtractionResult:
        """Extract page ``windows`` concurrently, bisecting any truncated one, then merge.

        At most ``chunk_concurrency`` window calls are in flight (the semaphore is held
        only around the call, never across a bisection — no deadlock). Window requests
        are counted against ``max_windows`` across the whole recursion. Results are
        re-assembled in page order and merged code-side
        (``chunking.merge_window_results``). Any failed window fails the extraction —
        its siblings are cancelled and no partial result is returned.
        """
        gate = asyncio.Semaphore(self._chunk_concurrency)
        issued = 0

        async def run(first: int, last: int) -> list[tuple[int, int, ExtractionResult]]:
            nonlocal issued
            window_label = f"{label} pages {first}-{last}"
            if issued >= self._max_windows:
                raise self._truncated(
                    window_label,
                    f"the max_windows={self._max_windows} cap on page-window requests "
                    f"was reached",
                )
            issued += 1
            contents = [*base, build_window_instruction(first, last, total)]
            try:
                async with gate:
                    result = await self._generate_validated(
                        client, contents, config, label=window_label
                    )
            except _Truncated:
                if first == last:
                    raise self._truncated(
                        window_label,
                        f"page {first} alone overflows the output budget",
                    ) from None
                left, right = bisect_window(first, last)
                self._log.warning(
                    "extraction of %s truncated; bisecting into pages %d-%d and %d-%d",
                    window_label, *left, *right,
                )
                halves = await _gather_ordered(
                    [run(*left), run(*right)], [left, right], label
                )
                return [part for half in halves for part in half]
            return [(first, last, result)]

        parts = await _gather_ordered([run(a, b) for a, b in windows], windows, label)
        return merge_window_results([part for chunk in parts for part in chunk])


async def _gather_ordered(
    coros: list[Coroutine[Any, Any, Any]],
    spans: list[tuple[int, int]],
    label: str,
) -> list:
    """Run ``coros`` in an ``asyncio.TaskGroup``; results in input order.

    A failure cancels the siblings (the TaskGroup contract). The resulting
    ``ExceptionGroup`` is unwrapped so the caller sees the ORIGINAL exception type
    (``RuntimeError("503")``, ``ValidationError``, ``ExtractionTruncated``): one failure
    is re-raised bare; several → the first by page order, with a note counting the rest.
    """
    tasks: list[asyncio.Task] = []
    failure: BaseException | None = None
    try:
        async with asyncio.TaskGroup() as group:
            tasks = [group.create_task(c) for c in coros]
    except BaseExceptionGroup as eg:
        leaves = _leaves(eg)
        ordered = [
            (span, task.exception())
            for span, task in zip(spans, tasks, strict=True)
            if task.done() and not task.cancelled() and task.exception() is not None
        ]
        failure = ordered[0][1] if ordered else leaves[0]
        if len(leaves) > 1:
            first, last = ordered[0][0] if ordered else (0, 0)
            failure.add_note(
                f"bridge extraction: {len(leaves)} page windows of {label!r} failed; "
                f"raising the first by page order (pages {first}-{last})"
            )
    if failure is not None:
        raise failure
    return [task.result() for task in tasks]


def _leaves(group: BaseExceptionGroup) -> list[BaseException]:
    """The leaf exceptions of a (possibly nested) exception group, in order."""
    out: list[BaseException] = []
    for exc in group.exceptions:
        if isinstance(exc, BaseExceptionGroup):
            out.extend(_leaves(exc))
        else:
            out.append(exc)
    return out
