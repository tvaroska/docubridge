"""Server-side A2A wire encode/decode for the Bridge edge (Ring-1 D2).

Pure functions, no I/O — fully unit-testable. Maps between the Pydantic domain
contract (``contract.wire``) and standard A2A ``Message``/``Part``/``Artifact``
messages.

**A2A SDK generation note.** The installed ``a2a-sdk`` (1.1.x, the version
``docs/upstream-adk-issues.md`` targets) is *proto-based*: ``Part`` is a flat
protobuf message whose ``content`` oneof selects ``text`` / ``data`` / ``url`` /
``raw`` (there is no ``DataPart``/``FilePart``/``FileWithUri`` pydantic wrapper,
and no ``Part(root=...)``). We therefore build/read parts through
``a2a.helpers.proto_helpers`` + ``google.protobuf.struct_pb2`` — the same flat
representation ADK's own ``google/adk/a2a/_compat.py`` uses on its ``IS_A2A_V1``
branch. The plan's ``Part(root=DataPart(...))`` sketch reflected the older 0.3.x
pydantic surface; this module follows the actual 1.x proto surface.

Discipline highlights:
- **D2 (contract_version on the wire):** every emitted domain data part carries
  ``contract_version`` in its part metadata.
- **D5 (select-by-shape / metadata, never by position):** the domain payload is
  found by validating candidate data parts against the Pydantic model shape
  (``extra="forbid"`` within our contract major makes a wrong dict fail; a
  newer-major payload is decoded tolerantly via ``contract.wire.decode_wire``,
  D2) and tagged with a ``kind`` marker
  in metadata for the consumer (P3) to key off — never by index.
- **No bytes on the wire (D-artifacts):** inbound inline-bytes file parts are
  rejected; only by-reference (URI) document parts are accepted.
"""

from __future__ import annotations

from typing import Any

from a2a.helpers import proto_helpers as ph
from a2a.types import Message, Part, Role
from google.protobuf import struct_pb2
from google.protobuf.json_format import MessageToDict, ParseDict

from bridge.collect import DocumentSubmission
from contract.wire import (
    ArtifactRef,
    CollectRequest,
    ExchangeTurn,
    HumanReviewResolution,
    decode_wire,
)

# Metadata tag keys. ``kind`` is the select-by-shape marker the consumer keys
# off (D5); ``contract_version`` travels alongside the payload (D2).
META_KIND = "kind"
META_CONTRACT_VERSION = "contract_version"
META_DOC_ID = "doc_id"
# The A2A status.message.metadata key carrying an LLM-leg outcome's reason code
# (D6 — envelope extension space, not a contract field).
META_REASON_CODE = "reason_code"
# The status.message.metadata key carrying the pending ``request_human_review`` call
# id a reviewer's ``HumanReviewResolution.call_id`` must echo (D6).
META_REVIEW_CALL_ID = "review_call_id"
# The server-authored ``task.metadata`` key scoping a parked review to its A2A task:
# ``{"user_id", "session_id", "call_ids"}`` — the LLM-leg session key + the review
# call ids parked on THIS task. Written only through ``TaskUpdater.update_status``
# (client request metadata never reaches ``task.metadata``); always the full value
# (``MergeFrom`` replaces top-level keys).
META_REVIEW_SESSION = "bridge_review_session"

KIND_COLLECT_REQUEST = "collect_request"
KIND_EXCHANGE_TURN = "exchange_turn"
# The tag on a data part carrying a ``contract.HumanReviewResolution`` (S3-edge-1 D10).
KIND_HUMAN_REVIEW_RESOLUTION = "human_review_resolution"

JSON_MIME = "application/json"


# --------------------------------------------------------------------------- #
# Low-level proto-part helpers (flat 1.x Part; see module docstring).          #
# --------------------------------------------------------------------------- #
def _part_metadata(part: Part) -> dict[str, Any]:
    """Read a Part's metadata Struct as a plain dict ({} when absent)."""
    if part.HasField("metadata"):
        return MessageToDict(part.metadata)
    return {}


def _set_part_metadata(part: Part, metadata: dict[str, Any]) -> None:
    """Write a plain dict onto a Part's metadata Struct."""
    part.metadata.CopyFrom(ParseDict(metadata, struct_pb2.Struct()))


def _data_part(data: dict[str, Any], metadata: dict[str, Any]) -> Part:
    """Build a structured-data Part (application/json) carrying metadata."""
    part = ph.new_data_part(data, media_type=JSON_MIME)
    _set_part_metadata(part, metadata)
    return part


# --------------------------------------------------------------------------- #
# Decode (inbound: client -> Bridge)                                          #
# --------------------------------------------------------------------------- #
def decode_collect_request(message: Message) -> CollectRequest:
    """Find the data Part whose shape validates as a ``CollectRequest``.

    Selects **by shape** (D5), not by position: ``CollectRequest`` is
    ``extra="forbid"`` (within our contract major), so an unrelated dict (e.g. an
    ``ExchangeTurn``) fails validation and is skipped. Decodes through
    ``decode_wire`` so a newer-major producer's added fields degrade gracefully
    (D2 tolerant reader). Raises ``ValueError`` if no data part matches.
    """
    for data in ph.get_data_parts(message.parts):
        if not isinstance(data, dict):
            continue
        try:
            return decode_wire(CollectRequest, data)
        except Exception:
            continue
    raise ValueError(
        "no CollectRequest data part found in inbound message "
        "(expected an application/json DataPart matching the CollectRequest shape)"
    )


def decode_submissions(message: Message) -> list[DocumentSubmission]:
    """Map inbound file Parts to ``DocumentSubmission``s (by-reference only).

    Each URI-backed file Part becomes one ``DocumentSubmission`` whose
    ``artifact_name`` is the URI (the extraction-port lookup key — for the local
    fixture adapter that is the corpus path, e.g. ``images/gov-id-clean.svg``).

    **Raises** ``ValueError`` on any inline-bytes (``raw``) file Part: document
    bytes never travel on the wire (``design/artifacts.md``). The real
    fetch-and-``save_artifact`` flow + scoped fetch endpoint are P2-4; here we
    only record the reference and pass the key through to the extractor.

    ``doc_id`` comes from the part's ``metadata.doc_id`` when present, else a
    stable positional id (``doc-{i}``).
    """
    subs: list[DocumentSubmission] = []
    for i, part in enumerate(message.parts):
        if part.HasField("raw"):
            raise ValueError(
                "inline document bytes are forbidden on the wire; submit documents "
                "by reference (a URI file part), not FileWithBytes"
            )
        if not part.HasField("url"):
            continue
        meta = _part_metadata(part)
        doc_id = str(meta.get(META_DOC_ID) or f"doc-{i}")
        uri = part.url
        subs.append(
            DocumentSubmission(
                doc_id=doc_id,
                artifact_name=uri,
                artifact=ArtifactRef(
                    uri=uri,
                    mime_type=part.media_type or None,
                    name=part.filename or None,
                ),
            )
        )
    return subs


def decode_human_review_resolution(message: Message) -> HumanReviewResolution | None:
    """Find + decode the data Part tagged ``metadata.kind == human_review_resolution``.

    Selected by the metadata tag (D10) — the same tag
    ``bridge.edge.pathroute.classify_route`` routes on — then decoded through
    ``contract.wire.decode_wire`` (tolerant reader: a newer-minor producer's added
    fields are stripped). ``None`` when no part carries the tag; a tagged part whose
    payload is the wrong shape raises ``ValidationError``.
    """
    for part in message.parts:
        if not part.HasField("data"):
            continue
        if _part_metadata(part).get(META_KIND) != KIND_HUMAN_REVIEW_RESOLUTION:
            continue
        data = MessageToDict(part.data)
        return decode_wire(HumanReviewResolution, data)
    return None


def decode_exchange_turn(parts: list[Part]) -> ExchangeTurn:
    """Inverse of :func:`encode_exchange_turn` (select-by-metadata, then shape).

    Prefers the part tagged ``metadata.kind == "exchange_turn"``; falls back to
    the first data part validating as an ``ExchangeTurn`` (D5, never by index).
    Convenience for tests and the P2-5 mock double. The full consumer-side
    event-stream decoder (``extract_exchange_turn``) is P3-2.
    """
    # First pass: honour the explicit kind tag on the matching part.
    for part in parts:
        if not part.HasField("data"):
            continue
        if _part_metadata(part).get(META_KIND) == KIND_EXCHANGE_TURN:
            return decode_wire(ExchangeTurn, MessageToDict(part.data))
    # Fallback: shape-select any data part that validates.
    for data in ph.get_data_parts(parts):
        if isinstance(data, dict):
            try:
                return decode_wire(ExchangeTurn, data)
            except Exception:
                continue
    raise ValueError("no ExchangeTurn data part found")


# --------------------------------------------------------------------------- #
# Encode (outbound: Bridge -> client)                                         #
# --------------------------------------------------------------------------- #
def encode_exchange_turn(turn: ExchangeTurn) -> list[Part]:
    """Encode an ``ExchangeTurn`` as a single tagged JSON data Part.

    ``metadata.kind == "exchange_turn"`` is the select-by-shape key the consumer
    (P3) keys off (D5); ``metadata.contract_version`` carries the wire version
    (D2). The document ledger rides *inside* this standard A2A part — the app
    reads only ``Task`` status + this projection, never an ADK event (WIRE-2).
    """
    part = _data_part(
        turn.model_dump(mode="json"),
        {
            META_KIND: KIND_EXCHANGE_TURN,
            META_CONTRACT_VERSION: turn.contract_version,
        },
    )
    return [part]


def encode_collect_request(request: CollectRequest) -> list[Part]:
    """Encode a ``CollectRequest`` as a tagged JSON data Part (client-side).

    Symmetric with :func:`decode_collect_request`; used by tests and the P2-5
    mock/consumer to drive the edge.
    """
    part = _data_part(
        request.model_dump(mode="json"),
        {
            META_KIND: KIND_COLLECT_REQUEST,
            META_CONTRACT_VERSION: request.contract_version,
        },
    )
    return [part]


def encode_human_review_resolution(resolution: HumanReviewResolution) -> Part:
    """Encode a ``HumanReviewResolution`` as one tagged JSON data Part (client-side).

    ``metadata.kind == "human_review_resolution"`` routes it (R6, D10);
    ``metadata.contract_version`` travels alongside (D2). Symmetric with
    :func:`decode_human_review_resolution`.
    """
    return _data_part(
        resolution.model_dump(mode="json"),
        {
            META_KIND: KIND_HUMAN_REVIEW_RESOLUTION,
            META_CONTRACT_VERSION: resolution.contract_version,
        },
    )


def summarize_turn(turn: ExchangeTurn) -> str:
    """Short human summary of a turn (e.g. 'collected 2 documents; 1 outstanding').

    Only ``required`` / ``optional`` requirements count as outstanding — a COLL-2
    proposal's ``satisfied`` items are not (the per-entry list is all ``required``).
    """
    from contract.enums import LifecycleStatus, RequirementStatus

    open_statuses = (RequirementStatus.required, RequirementStatus.optional)
    collected = len(turn.status.entries)
    outstanding = (
        sum(
            1 for r in turn.status.outstanding.requirements if r.status in open_statuses
        )
        if turn.status.outstanding is not None
        else 0
    )
    doc_word = "document" if collected == 1 else "documents"
    base = f"collected {collected} {doc_word}; {outstanding} outstanding"

    # P4-5: append lifecycle status when not on_track to surface the stall
    if turn.lifecycle != LifecycleStatus.on_track:
        return f"{base} [{turn.lifecycle.value}]"
    return base


def status_message_for(turn: ExchangeTurn) -> Message:
    """Build a **non-empty** A2A status ``Message`` for a turn (D3).

    Critical (D3 / consumer gotcha): an empty ``status.message`` is silently
    dropped by ADK's a2a converter, so the status update MUST carry a real,
    non-empty text part.
    """
    return ph.new_message(
        parts=[ph.new_text_part(summarize_turn(turn))],
        context_id=turn.context_id,
        role=Role.ROLE_AGENT,
    )
