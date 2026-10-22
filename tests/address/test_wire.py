"""Unit tests for the consumer decoder ``extract_exchange_turn`` (AGENT-7).

Pins the **real** A2A→genai converted-part shape (not a hand-faked blob): encode a
turn with the server-side wire, convert with ADK's own converter, and assert the
consumer decoder round-trips it. Importing ``bridge.edge.wire`` from a *test* is
allowed — the isolation guard forbids ``bridge``↔``agents`` in first-party code,
not in ``tests/``.
"""

from __future__ import annotations

import json

from google.adk.a2a.converters.part_converter import convert_a2a_part_to_genai_part
from google.genai import types

from agents.address.wire import (
    KIND_EXCHANGE_TURN,
    decode_part_payload,
    extract_exchange_turn,
    part_is_exchange_turn,
)
from bridge.edge.wire import encode_collect_request, encode_exchange_turn
from contract.enums import Disposition, Gate
from contract.wire import (
    CollectionStatus,
    CollectRequest,
    ExchangeTurn,
    LedgerEntry,
)


def _turn() -> ExchangeTurn:
    return ExchangeTurn(
        context_id="ctx-1",
        terminal=True,
        status=CollectionStatus(
            entries=[
                LedgerEntry(
                    doc_id="gov-id-clean",
                    doctype="gov-id",
                    disposition=Disposition.accepted,
                    gate=Gate.auto_approve,
                )
            ]
        ),
    )


def _converted_turn_part() -> types.Part:
    """The exact genai Part a relayed ExchangeTurn lands as on the session."""
    parts = encode_exchange_turn(_turn())
    return convert_a2a_part_to_genai_part(parts[0])


def test_round_trips_real_converted_part():
    genai_part = _converted_turn_part()
    turn = extract_exchange_turn([genai_part])
    assert turn is not None
    assert turn == _turn()


def test_metadata_fast_path_marks_exchange_turn():
    genai_part = _converted_turn_part()
    # Gemini variant carries part_metadata.kind == "exchange_turn".
    assert part_is_exchange_turn(genai_part) is True
    assert genai_part.part_metadata.get("kind") == KIND_EXCHANGE_TURN


def test_shape_fallback_when_metadata_dropped():
    """Vertex variant drops part_metadata — must still decode by shape."""
    genai_part = _converted_turn_part()
    genai_part.part_metadata = None  # simulate the Vertex path
    assert part_is_exchange_turn(genai_part) is False
    turn = extract_exchange_turn([genai_part])
    assert turn is not None
    assert turn == _turn()


def test_rejects_wrong_shape_collect_request():
    """A CollectRequest blob must not mis-decode as an ExchangeTurn (extra=forbid)."""
    req = CollectRequest(process="address-proof", context_id="ctx-1")
    parts = encode_collect_request(req)
    genai_part = convert_a2a_part_to_genai_part(parts[0])
    # The wrong-shaped blob is present and tagged kind==collect_request.
    assert part_is_exchange_turn(genai_part) is False
    assert extract_exchange_turn([genai_part]) is None


def test_empty_and_non_json_blobs_return_none():
    empty = types.Part(inline_data=types.Blob(data=b"", mime_type="text/plain"))
    junk = types.Part(
        inline_data=types.Blob(
            data=b"<a2a_datapart_json>not json</a2a_datapart_json>",
            mime_type="text/plain",
        )
    )
    text = types.Part(text="hello, not a turn")
    assert decode_part_payload(empty) is None
    assert decode_part_payload(junk) is None
    assert extract_exchange_turn([empty, junk, text]) is None


def test_decode_part_payload_strips_tags():
    genai_part = _converted_turn_part()
    payload = decode_part_payload(genai_part)
    assert isinstance(payload, dict)
    assert payload["context_id"] == "ctx-1"
    # Round-trips the same dict the turn dumps.
    assert payload == _turn().model_dump(mode="json")


def test_text_part_carrying_json_decodes():
    blob = json.dumps(_turn().model_dump(mode="json"))
    part = types.Part(text=blob)
    turn = extract_exchange_turn([part])
    assert turn is not None
    assert turn == _turn()


def test_selects_turn_among_mixed_parts_never_by_index():
    """The turn is found by shape even when a wrong part precedes it."""
    req = CollectRequest(process="address-proof")
    wrong = convert_a2a_part_to_genai_part(encode_collect_request(req)[0])
    good = _converted_turn_part()
    turn = extract_exchange_turn([wrong, good])
    assert turn == _turn()
