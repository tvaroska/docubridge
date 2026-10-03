"""Consumer-side A2A→domain decode for the Address agent (AGENT-7).

The inverse of ``bridge.edge.wire.encode_exchange_turn``, but decoding out of an
**ADK genai ``Part``** (not a raw a2a ``Task``). ``RemoteA2aAgent`` relays a task
artifact as an ADK event whose content parts are the A2A→genai converted parts;
the Bridge sends the ``ExchangeTurn`` as a *generic* ``application/json`` data
part, which the converter (``google.adk.a2a.converters.part_converter``) lands as
an ``inline_data`` blob whose bytes are the JSON wrapped in
``<a2a_datapart_json>…</a2a_datapart_json>`` tags, ``mime_type text/plain``.

Discipline (mirrors the server-side ``decode_exchange_turn`` / D5+D2):

- **Select by metadata, then validate by shape.** The converted part carries
  ``part_metadata.kind == "exchange_turn"`` on the Gemini variant (a *fast path*),
  but the Vertex variant drops ``part_metadata`` entirely — so metadata selection
  is never trusted alone; the payload must still validate against the
  ``ExchangeTurn`` shape (``extra="forbid"`` within our contract major rejects a
  wrong dict; a newer-major turn is decoded tolerantly via ``decode_wire``).
- **Never select by index.** The turn is found by shape/metadata, not position.
- **contract_version (D2)** rides in ``part_metadata`` *and* the turn body, and is
  now inspected: ``contract.wire.decode_wire`` gates on it, so a newer-major
  producer's added fields degrade gracefully rather than hard-failing the
  consumer's ``model_validate`` (tolerant reader for external callers).

This module supersedes the tag-stripping logic previously inlined in
``turn_reader.py``. It never imports ``bridge/`` (isolation convention).
"""
from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from google.genai import types

from contract.wire import ExchangeTurn, decode_wire

# Metadata keys — kept in sync by contract with ``bridge.edge.wire``.
META_KIND = "kind"
META_CONTRACT_VERSION = "contract_version"
META_DOC_ID = "doc_id"
KIND_EXCHANGE_TURN = "exchange_turn"

# Default document media type on the corpus (SVG rasters). Callers override per doc.
DEFAULT_DOC_MEDIA_TYPE = "image/svg+xml"

# The A2A converter wraps a generic data part's JSON bytes in these tags when it
# lands as an inline blob (google.adk.a2a.converters.part_converter). Kept as
# bytes (``inline_data.data`` is bytes); a ``str`` ``.text`` fallback is tolerated.
_TAG_START = b"<a2a_datapart_json>"
_TAG_END = b"</a2a_datapart_json>"


# --------------------------------------------------------------------------- #
# Outbound: consumer -> Bridge document-submission encoding (P3-5).            #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class DocRef:
    """An agents-side reference to a corpus document to submit (by reference).

    Deliberately NOT ``bridge.collect.DocumentSubmission`` — ``agents/address``
    never imports ``bridge/`` (the mock→real swap is a URL change). A ``DocRef`` is
    a plain ``(doc_id, uri, media_type)`` triple: the real edge's
    ``decode_submissions`` reads ``doc_id`` off the part metadata and keys the
    fixture extractor on it; the ``uri`` rides by reference (no inline bytes —
    the ART-2 invariant).
    """

    doc_id: str
    uri: str
    media_type: str = DEFAULT_DOC_MEDIA_TYPE


def encode_doc_part(doc: DocRef) -> types.Part:
    """Encode one ``DocRef`` as a by-reference genai ``file_data`` ``Part``.

    The ``doc_id`` rides in ``part_metadata``; the ADK genai→a2a converter
    (``google.adk.a2a.converters.part_converter.convert_genai_part_to_a2a_part``)
    maps a ``file_data`` part to an a2a **url part** and copies ``part_metadata``
    onto the a2a part's metadata, so the edge's ``decode_submissions`` reads the
    ``doc_id`` back. No inline bytes ever travel (ART-2).
    """
    return types.Part(
        file_data=types.FileData(file_uri=doc.uri, mime_type=doc.media_type),
        part_metadata={META_DOC_ID: doc.doc_id},
    )


def encode_doc_parts(docs: Iterable[DocRef]) -> list[types.Part]:
    """Encode a list of ``DocRef``s as by-reference genai document parts."""
    return [encode_doc_part(doc) for doc in docs]


def _part_metadata(part: Any) -> dict:
    """Return a genai ``Part``'s ``part_metadata`` as a dict ({} when absent)."""
    meta = getattr(part, "part_metadata", None)
    return meta if isinstance(meta, dict) else {}


def part_is_exchange_turn(part: Any) -> bool:
    """True iff ``part_metadata.kind == "exchange_turn"`` (D5 fast path).

    May be ``False`` even for a real turn on the Vertex variant, which drops
    ``part_metadata`` — callers must keep the shape-validation fallback.
    """
    return _part_metadata(part).get(META_KIND) == KIND_EXCHANGE_TURN


def _strip_tags(value: bytes) -> bytes:
    """Strip the ``<a2a_datapart_json>…</…>`` delimiters (if present)."""
    value = value.strip()
    if value.startswith(_TAG_START):
        value = value[len(_TAG_START):]
    if value.endswith(_TAG_END):
        value = value[: -len(_TAG_END)]
    return value.strip()


def _loads_dict(value: Any) -> dict | None:
    """Coerce a raw payload (bytes/str/dict) to a JSON dict, or ``None``."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        value = value.encode("utf-8")
    if isinstance(value, (bytes, bytearray)):
        stripped = _strip_tags(bytes(value))
        if not stripped:
            return None
        try:
            loaded = json.loads(stripped)
        except (ValueError, UnicodeDecodeError):
            return None
        return loaded if isinstance(loaded, dict) else None
    return None


def decode_part_payload(part: Any) -> dict | None:
    """Return the JSON dict a genai ``Part`` carries, or ``None``.

    Handles every shape the turn can arrive in:
    - an ``inline_data`` blob wrapped in ``<a2a_datapart_json>…</…>`` (the generic
      data-part path — the live shape),
    - a structured ``.data`` dict (should a future converter surface it directly),
    - a ``.text`` part carrying JSON.

    Strips the tag delimiters, ``json.loads``es, and returns a dict or ``None``
    (never raises).
    """
    # Structured data (a future converter may surface a raw dict).
    data = getattr(part, "data", None)
    if data is not None:
        coerced = _loads_dict(data)
        if coerced is not None:
            return coerced
    # The generic-data-part path: a tag-wrapped inline blob.
    inline = getattr(part, "inline_data", None)
    if inline is not None:
        coerced = _loads_dict(getattr(inline, "data", None))
        if coerced is not None:
            return coerced
    # A text part carrying JSON.
    text = getattr(part, "text", None)
    if text is not None:
        coerced = _loads_dict(text)
        if coerced is not None:
            return coerced
    return None


def _turn_from_part(part: Any) -> ExchangeTurn | None:
    payload = decode_part_payload(part)
    if payload is None:
        return None
    try:
        return decode_wire(ExchangeTurn, payload)
    except Exception:
        return None


def extract_exchange_turn(parts: Iterable[Any]) -> ExchangeTurn | None:
    """Return the first genai ``Part`` that decodes as an ``ExchangeTurn``.

    Order (never by index):
    1. Parts tagged ``kind == "exchange_turn"`` whose payload validates by shape
       (the D5 metadata fast path);
    2. any part whose payload validates by shape (the Vertex fallback, where
       ``part_metadata`` was dropped) — ``extra="forbid"`` (within our contract
       major) rejects a wrong dict; a newer-major turn decodes tolerantly.

    Returns ``None`` when no part carries a valid turn (no exception on bad JSON
    or a wrong-shaped dict).
    """
    parts = list(parts)
    # Pass 1: metadata-selected fast path.
    for part in parts:
        if part_is_exchange_turn(part):
            turn = _turn_from_part(part)
            if turn is not None:
                return turn
    # Pass 2: shape-only fallback (Vertex drops part_metadata).
    for part in parts:
        turn = _turn_from_part(part)
        if turn is not None:
            return turn
    return None
