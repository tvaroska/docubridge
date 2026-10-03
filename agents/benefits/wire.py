"""Pure a2a + contract wire glue for the carrier agents (S1-benefits-6).

Mirrors the shape of ``bridge.edge.wire`` but is **self-contained** — it lives in the
external ``agents/`` zone and imports only ``a2a.*``, ``google.protobuf.*`` and
``contract.*`` (never ``bridge/``, the reverse isolation guard). It carries a carrier
``benefits-quote`` as one tagged JSON data Part and classifies an inbound turn as a
first quote request vs. a revise/pushback.

A2A SDK generation note (identical to ``bridge/edge/wire.py``): the installed
``a2a-sdk`` (1.1.x) is proto-based — ``Part`` is a flat protobuf message; structured
data rides in a ``struct_pb2.Struct`` and is read back with ``MessageToDict``. Parts
are selected by ``metadata.kind``, never by position (D5).
"""

from __future__ import annotations

from typing import Any

from a2a.helpers import proto_helpers as ph
from a2a.types import Message, Part
from google.protobuf import struct_pb2
from google.protobuf.json_format import MessageToDict, ParseDict

from contract.extraction import ExtractionResult

# Metadata tag keys (mirror bridge.edge.wire).
META_KIND = "kind"
KIND_BENEFITS_QUOTE = "benefits-quote"
JSON_MIME = "application/json"

# The revise/pushback marker (case-insensitive substring in the turn text).
REVISE_MARKER = "revise"


def _part_metadata(part: Part) -> dict[str, Any]:
    """Read a Part's metadata Struct as a plain dict ({} when absent)."""
    if part.HasField("metadata"):
        return MessageToDict(part.metadata)
    return {}


def encode_quote(result: ExtractionResult) -> list[Part]:
    """Encode an ``ExtractionResult`` as a single ``benefits-quote`` data Part.

    ``metadata.kind == "benefits-quote"`` is the select-by-shape key the consumer keys
    off (D5). Same encode idiom both carriers use, so the wire shape is framework-neutral.
    """
    part = ph.new_data_part(result.model_dump(mode="json"), media_type=JSON_MIME)
    part.metadata.CopyFrom(ParseDict({META_KIND: KIND_BENEFITS_QUOTE}, struct_pb2.Struct()))
    return [part]


def decode_quote(parts: list[Part]) -> ExtractionResult:
    """Inverse of :func:`encode_quote` — select by ``metadata.kind`` then validate.

    Prefers the part tagged ``metadata.kind == "benefits-quote"``; falls back to the
    first data part validating as an ``ExtractionResult`` (D5, never by index). Raises
    ``ValueError`` when no benefits-quote data part is present.
    """
    for part in parts:
        if not part.HasField("data"):
            continue
        if _part_metadata(part).get(META_KIND) == KIND_BENEFITS_QUOTE:
            return ExtractionResult.model_validate(MessageToDict(part.data))
    # Fallback: shape-select any data part that validates.
    for data in ph.get_data_parts(parts):
        if isinstance(data, dict):
            try:
                return ExtractionResult.model_validate(data)
            except Exception:
                continue
    raise ValueError("no benefits-quote data part found")


def classify_turn(message: Message) -> str:
    """Classify an inbound turn as ``"quote"`` (first request) or ``"revise"``.

    The classification is **stateless**: a revise/pushback turn carries the
    ``revise`` marker (case-insensitive) in its text; every other turn is a first
    quote request. Keeping the carriers stateless (no server-side turn counter) is
    what makes both deterministic — the LangGraph carrier's conditional edge is a
    clean classify → node.
    """
    text = " ".join(ph.get_text_parts(message.parts)).lower()
    return "revise" if REVISE_MARKER in text else "quote"
