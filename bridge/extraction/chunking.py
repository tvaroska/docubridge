"""Page-window chunking for large documents (S0-perf-1) — pure, stdlib + ``contract`` only.

**Why chunk at all.** One schema-driven Gemini call per document is the right shape for
every Address record and every one-page carrier quote, but a 1000-row carrier rate sheet
is one very large structured OUTPUT. The output budget (``max_output_tokens``), not the
input window, is what runs out: the response comes back cut mid-row
(``finish_reason == MAX_TOKENS``), and re-asking the same question at the same budget
reproduces the same truncation — the document is lost. The fix is to ask for less output
per request, so the engine asks for the table rows of a few PAGES at a time.

**Why page windows in the prompt, never a split file.** Every window request carries the
WHOLE document part (inline bytes, or a ``gs://`` object handed over by reference) plus a
window instruction naming the pages it must cover. A by-reference object cannot be split
without downloading it (S0-arch-7: the Bridge downloads nothing), and no PDF library is
installed (S0-arch-6: stdlib only). Input cost grows with windows × document tokens; the
output — the thing that truncates — is bounded per window.

**Merge is code-side, lossless and conservative** (:func:`merge_window_results`): table
rows concatenate in page order with NO dedupe (dropping a row is silent data loss; a
duplicate is visible to review); whole-document scalars (``doctype``, ``issuer``,
``key_fields``) take the first window's value and a later window that DISAGREES is flagged
for review with both values and page ranges, never silently overwritten; confidences take
the minimum (a low-confidence window means some rows are unreliable, so the gate must see
it); ``legible`` is the conjunction.

**Row-path rebasing.** A window numbers its rows from 0, so a window-local
``fields.tables.rates[3].frequency`` emitted by the second window (after 40 rows from the
first) is row 43 of the merged table. Every row path in ``flagged_fields``,
``field_reviews[].path`` and the ``field_confidence`` keys is rebased by the rows earlier
windows contributed to that table (:func:`rebase_path`). An index the window did not
actually emit cannot be placed, so it is COARSENED to the whole table
(``fields.tables.rates``) with a warning — never dropped, never guessed.

**No partial success.** The engine (``bridge/extraction/gemini.py``) raises if any window
fails; this module never sees a partial set. Partial rows masquerading as a complete rate
sheet is exactly the silent loss chunking exists to remove.

Vendorable like ``docai.py``: no ``google*`` import, never ``bridge.edge`` /
``bridge.seams`` / ``agents``.
"""

from __future__ import annotations

import logging
import re
import zlib
from collections.abc import Iterable

from contract.enums import ReviewReason
from contract.extraction import (
    ExtractionFields,
    ExtractionResult,
    FieldReview,
    review_path,
)

_LOG = logging.getLogger("bridge.extraction.chunking")


class ExtractionTruncated(ValueError):
    """The model's output was cut at ``max_output_tokens`` and could not be chunked smaller.

    A ``ValueError`` on purpose: it is a deterministic engine refusal for THIS document
    (a single page whose rows overflow the budget, a non-paged document, chunking
    disabled, or the window cap hit), so callers that map ``ValueError`` to "unreadable"
    (the email processor) take the right branch, while transport errors still propagate
    unchanged. Raised with an ``add_note`` naming the artifact, window/page and budget.
    """


# --------------------------------------------------------------------------- #
# Stdlib page count                                                           #
# --------------------------------------------------------------------------- #

_PAGES_NODE = re.compile(rb"/Type\s*/Pages\b")
_PAGE_LEAF = re.compile(rb"/Type\s*/Page\b")
_COUNT = re.compile(rb"/Count\s+(\d{1,9})\b")
_DICT_DELIM = re.compile(rb"<<|>>")
_STREAM = re.compile(rb"stream\r?\n")
_OBJ_START = re.compile(rb"\d+\s+\d+\s+obj\b")

# Bound on one inflated object stream (a decompression bomb is skipped, not followed).
MAX_INFLATED_BYTES = 50 * 1024 * 1024


def _enclosing_dict(text: bytes, pos: int, delims: list[tuple[int, bytes]]) -> bytes:
    """The ``<< ... >>`` dictionary that encloses byte offset ``pos`` (best effort)."""
    # Index of the first delimiter at/after pos.
    lo, hi = 0, len(delims)
    while lo < hi:
        mid = (lo + hi) // 2
        if delims[mid][0] < pos:
            lo = mid + 1
        else:
            hi = mid
    start, depth = None, 0
    for i in range(lo - 1, -1, -1):
        at, tok = delims[i]
        if tok == b">>":
            depth += 1
        elif depth:
            depth -= 1
        else:
            start = at
            break
    if start is None:
        return b""
    end, depth = len(text), 0
    for at, tok in delims[lo:]:
        if tok == b"<<":
            depth += 1
        elif depth:
            depth -= 1
        else:
            end = at + 2
            break
    return text[start:end]


def _count_pages(text: bytes) -> int | None:
    """(a) max ``/Count`` over ``/Type /Pages`` nodes, else (b) count ``/Type /Page`` leaves."""
    nodes = list(_PAGES_NODE.finditer(text))
    if nodes:
        delims = [(m.start(), m.group()) for m in _DICT_DELIM.finditer(text)]
        counts = []
        for node in nodes:
            match = _COUNT.search(_enclosing_dict(text, node.start(), delims))
            if match:
                counts.append(int(match.group(1)))
        if counts and max(counts) > 0:
            return max(counts)
    leaves = len(_PAGE_LEAF.findall(text))
    return leaves or None


def _object_streams(data: bytes) -> Iterable[bytes]:
    """Inflated ``/Type /ObjStm`` ``/FlateDecode`` stream bodies (bounded, never raising)."""
    starts = [m.start() for m in _OBJ_START.finditer(data)]
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(data)
        body = data[start:end]
        if b"/ObjStm" not in body or b"/FlateDecode" not in body:
            continue
        head = _STREAM.search(body)
        if head is None:
            continue
        raw = body[head.end():]
        stop = raw.rfind(b"endstream")
        if stop >= 0:
            raw = raw[:stop]
        inflater = zlib.decompressobj()
        try:
            inflated = inflater.decompress(raw, MAX_INFLATED_BYTES)
        except zlib.error:
            continue
        if inflater.unconsumed_tail:
            continue  # inflates past the bound: skip, never follow a bomb
        yield inflated


def pdf_page_count(data: bytes) -> int | None:
    """The page count of an inline PDF, read with the standard library only, or ``None``.

    (a) the largest ``/Count N`` on a ``/Type /Pages`` node (the root carries the total);
    (b) else the number of ``/Type /Page`` leaves; (c) else the same two reads over every
    ``/Type /ObjStm`` ``/FlateDecode`` object stream (PDF 1.5 compressed object streams),
    each inflate bounded by ``MAX_INFLATED_BYTES``; (d) else ``None`` — the engine then
    treats the page count as unknown. Never raises on garbage.
    """
    if not isinstance(data, bytes | bytearray) or not data:
        return None
    data = bytes(data)
    try:
        found = _count_pages(data)
        if found:
            return found
        for inflated in _object_streams(data):
            found = _count_pages(inflated)
            if found:
                return found
    except Exception:  # pragma: no cover - defensive: a parser must not kill extraction
        _LOG.warning("pdf page count failed; treating the page count as unknown")
    return None


# --------------------------------------------------------------------------- #
# Window planning                                                             #
# --------------------------------------------------------------------------- #


def plan_windows(total: int, size: int) -> list[tuple[int, int]]:
    """1-based inclusive page windows of ``size`` pages over ``total`` pages."""
    if total < 1 or size < 1:
        raise ValueError(f"plan_windows needs total >= 1 and size >= 1; got {total}, {size}")
    return [(first, min(first + size - 1, total)) for first in range(1, total + 1, size)]


def bisect_window(first: int, last: int) -> tuple[tuple[int, int], tuple[int, int]]:
    """Split a window of >= 2 pages into two halves (the left half takes the extra page)."""
    if last <= first:
        raise ValueError(f"cannot bisect a single-page window {first}-{last}")
    mid = (first + last) // 2
    return (first, mid), (mid + 1, last)


# --------------------------------------------------------------------------- #
# Merge                                                                       #
# --------------------------------------------------------------------------- #

_ROW_PATH = re.compile(
    r"^(?P<prefix>(?:fields\.)?(?:tables\.)?)(?P<table>[A-Za-z_][\w-]*)"
    r"\[(?P<idx>\d+)\](?P<rest>.*)$"
)


def rebase_path(
    path: str,
    *,
    offsets: dict[str, int],
    window_rows: dict[str, int],
    tables: set[str] | frozenset[str],
) -> tuple[str, bool]:
    """Rebase one window-local row path onto the merged table; ``(path, coarsened)``.

    A path ``[fields.][tables.]<table>[<i>]<rest>`` naming a known ``table`` has ``i``
    shifted by ``offsets[table]`` (rows earlier windows contributed) when the window
    actually emitted row ``i``; an index it did not emit cannot be placed, so the path is
    coarsened to ``fields.tables.<table>`` (``coarsened=True``). Anything else passes
    through untouched.
    """
    match = _ROW_PATH.match(path)
    if match is None or match["table"] not in tables:
        return path, False
    table, idx = match["table"], int(match["idx"])
    if idx < window_rows.get(table, 0):
        return f"{match['prefix']}{table}[{idx + offsets.get(table, 0)}]{match['rest']}", False
    return f"fields.tables.{table}", True


def _span(first: int, last: int) -> str:
    return f"pages {first}-{last}"


def merge_window_results(
    windows: list[tuple[int, int, ExtractionResult]],
) -> ExtractionResult:
    """Merge per-page-window results (in page order) into ONE ``ExtractionResult``.

    One window → returned unchanged. Otherwise: ``doctype`` from the first window and
    ``issuer`` / each ``key_fields`` value from the first window that carries it, a later
    DIFFERENT value flagged (``model_flagged`` review naming both values and page
    ranges); table rows concatenated in window order (no dedupe); row paths rebased
    (:func:`rebase_path`); ``overall_confidence`` = min, ``legible`` = all,
    ``field_confidence`` = the winning window's value for a key field else the min;
    ``flagged_fields`` an ordered union and ``field_reviews`` deduped on ``path`` (first
    wins), the merge's own disagreement findings first. The wire type is unchanged.
    """
    if not windows:
        raise ValueError("merge_window_results needs at least one window")
    if len(windows) == 1:
        return windows[0][2]

    first_a, first_b, head = windows[0]
    tables: set[str] = set()
    for _, _, result in windows:
        tables.update(result.fields.tables)

    # -- whole-document scalars: first wins, a later disagreement is flagged ----------
    conflicts: dict[str, list[str]] = {}  # flag name -> detail fragments (ordered)

    def _conflict(name: str, kept: str, kept_span: str, other: str, other_span: str):
        notes = conflicts.setdefault(name, [f"{kept_span} report {kept!r}"])
        notes.append(f"{other_span} report {other!r}")

    doctype = head.fields.doctype
    for a, b, result in windows[1:]:
        if result.fields.doctype != doctype:
            _conflict("doctype", doctype, _span(first_a, first_b),
                      result.fields.doctype, _span(a, b))

    issuer: str | None = None
    issuer_span = ""
    for a, b, result in windows:
        value = result.fields.issuer
        if value is None:
            continue
        if issuer is None:
            issuer, issuer_span = value, _span(a, b)
        elif value != issuer:
            _conflict("issuer", issuer, issuer_span, value, _span(a, b))

    key_fields: dict[str, str] = {}
    winner: dict[str, int] = {}  # key -> index of the window that supplied the value
    for w, (a, b, result) in enumerate(windows):
        for key, value in result.fields.key_fields.items():
            if key not in key_fields:
                key_fields[key], winner[key] = value, w
            elif value != key_fields[key]:
                wa, wb, _ = windows[winner[key]]
                _conflict(key, key_fields[key], _span(wa, wb), value, _span(a, b))

    # -- tables + rebased review paths --------------------------------------------------
    merged_tables: dict[str, list[dict[str, str]]] = {}
    offsets: dict[str, int] = {}
    flagged: list[str] = []
    reviews: list[FieldReview] = []
    confidences: dict[str, list[tuple[int, float]]] = {}
    for w, (a, b, result) in enumerate(windows):
        window_rows = {t: len(rows) for t, rows in result.fields.tables.items()}

        def _rebase(path: str, *, _a=a, _b=b, _rows=window_rows) -> str:
            rebased, coarse = rebase_path(
                path, offsets=offsets, window_rows=_rows, tables=tables
            )
            if coarse:
                _LOG.warning(
                    "page window %s: row path %r names a row the window did not emit; "
                    "coarsened to %r",
                    _span(_a, _b), path, rebased,
                )
            return rebased

        flagged.extend(_rebase(name) for name in result.flagged_fields)
        reviews.extend(
            review.model_copy(update={"path": _rebase(review.path)})
            for review in result.field_reviews
        )
        for key, value in result.field_confidence.items():
            confidences.setdefault(_rebase(key), []).append((w, value))
        for table, rows in result.fields.tables.items():
            merged_tables.setdefault(table, []).extend(rows)
            offsets[table] = offsets.get(table, 0) + len(rows)

    fields = ExtractionFields(
        doctype=doctype, issuer=issuer, key_fields=key_fields, tables=merged_tables
    )

    # The merge's own disagreement findings lead (first-wins dedupe keeps their detail).
    conflict_reviews = [
        FieldReview(
            path=review_path(name, fields),
            reason=ReviewReason.model_flagged,
            detail="page windows disagree: " + ", ".join(notes),
        )
        for name, notes in conflicts.items()
    ]
    flagged_union = list(dict.fromkeys([*conflicts, *flagged]))
    seen: set[str] = set()
    deduped: list[FieldReview] = []
    for review in [*conflict_reviews, *reviews]:
        if review.path in seen:
            continue
        seen.add(review.path)
        deduped.append(review)

    field_confidence: dict[str, float] = {}
    for key, reported in confidences.items():
        if key in winner:
            own = [v for w, v in reported if w == winner[key]]
            field_confidence[key] = own[0] if own else min(v for _, v in reported)
        else:
            field_confidence[key] = min(v for _, v in reported)

    return ExtractionResult.model_validate(
        {
            "fields": fields.model_dump(),
            "overall_confidence": min(r.overall_confidence for _, _, r in windows),
            "field_confidence": field_confidence,
            "legible": all(r.legible for _, _, r in windows),
            "flagged_fields": flagged_union,
            "field_reviews": [r.model_dump() for r in deduped],
        }
    )
