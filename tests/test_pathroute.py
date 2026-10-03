"""Path routing tests (P6-1) — classify A vs B by what kind of response arrived.

Plus the hybrid edge's router units (S3-edge-3): ``resolve_edge_mode`` (the
``BRIDGE_EDGE_MODE`` flag parse, S3-edge-1 D2) and ``classify_route`` (the D3 route
table, both modes).
"""

import uuid

import pytest
from a2a.helpers import proto_helpers as ph
from a2a.types import Message, Role, TaskState
from google.protobuf import struct_pb2
from google.protobuf.json_format import ParseDict

from bridge.edge import encode_collect_request
from bridge.edge.pathroute import (
    EDGE_MODE_ENV,
    EdgeMode,
    Path,
    Route,
    classify_path,
    classify_route,
    decode_assertion,
    resolve_edge_mode,
)
from bridge.edge.wire import KIND_HUMAN_REVIEW_RESOLUTION
from contract.wire import CollectRequest


def _assertion() -> dict:
    return {
        "fields": {
            "doctype": "gov-id",
            "key_fields": {
                "name": "Jordan Lee",
                "address": "14 Elm Row",
                "doc_number": "DL-1",
                "issuing_authority": "DMV",
                "expiry": "2030-01-01",
            },
        },
        "overall_confidence": 0.96,
        "legible": True,
        "flagged_fields": [],
    }


def _msg(parts) -> Message:
    return Message(role=Role.ROLE_USER, message_id=uuid.uuid4().hex, parts=parts)


def test_classify_structured_assertion_is_path_a():
    """A structured extraction-assertion data part → Path.A."""
    parts = encode_collect_request(CollectRequest(process="address-proof"))
    parts.append(ph.new_data_part(_assertion()))
    assert classify_path(_msg(parts)) is Path.A


def test_classify_file_upload_is_path_b():
    """A file (url) part → Path.B."""
    parts = encode_collect_request(CollectRequest(process="address-proof"))
    parts.append(ph.new_url_part("images/gov-id-clean.svg", media_type="image/svg+xml"))
    assert classify_path(_msg(parts)) is Path.B


def test_classify_text_is_path_b():
    """Free text → Path.B."""
    parts = [ph.new_text_part("collect address proof")]
    assert classify_path(_msg(parts)) is Path.B


def test_classify_collect_request_only_defaults_path_b():
    """A CollectRequest-only message (no assertion, no file) defaults to Path.B."""
    parts = encode_collect_request(CollectRequest(process="address-proof"))
    assert classify_path(_msg(parts)) is Path.B


def test_decode_assertion_selects_by_shape():
    """decode_assertion returns the assertion regardless of part order."""
    parts = encode_collect_request(CollectRequest(process="address-proof"))
    parts.append(ph.new_data_part(_assertion()))
    result = decode_assertion(_msg(parts))
    assert result.fields.doctype == "gov-id"


def test_decode_assertion_raises_when_absent():
    parts = encode_collect_request(CollectRequest(process="address-proof"))
    with pytest.raises(ValueError, match="no extraction-assertion"):
        decode_assertion(_msg(parts))


def test_classify_consumer_vocabulary_assertion_is_path_a():
    """S0-debt-2: an asserter speaking `fields_requiring_review` (dotted paths) is
    Path A, not a silent fall-through to the portal path."""
    assertion = _assertion()
    del assertion["flagged_fields"]
    assertion["fields_requiring_review"] = ["commission.rate_pct"]
    parts = encode_collect_request(CollectRequest(process="address-proof"))
    parts.append(ph.new_data_part(assertion))

    assert classify_path(_msg(parts)) is Path.A
    assert decode_assertion(_msg(parts)).flagged_fields == ["commission.rate_pct"]


# --------------------------------------------------------------------------- #
# S3-edge-3: resolve_edge_mode (D2 flag parse)                                 #
# --------------------------------------------------------------------------- #
def test_edge_mode_unset_is_deterministic(monkeypatch):
    monkeypatch.delenv(EDGE_MODE_ENV, raising=False)
    assert resolve_edge_mode() is EdgeMode.deterministic
    assert resolve_edge_mode(None) is EdgeMode.deterministic


@pytest.mark.parametrize("raw", ["", "  "])
def test_edge_mode_blank_env_is_deterministic(monkeypatch, raw):
    monkeypatch.setenv(EDGE_MODE_ENV, raw)
    assert resolve_edge_mode() is EdgeMode.deterministic


def test_edge_mode_env_hybrid(monkeypatch):
    monkeypatch.setenv(EDGE_MODE_ENV, "hybrid")
    assert resolve_edge_mode() is EdgeMode.hybrid
    monkeypatch.setenv(EDGE_MODE_ENV, " hybrid ")
    assert resolve_edge_mode() is EdgeMode.hybrid


@pytest.mark.parametrize("raw", ["bogus", "Hybrid", "DETERMINISTIC", "llm"])
def test_edge_mode_invalid_env_fails_closed(monkeypatch, raw):
    monkeypatch.setenv(EDGE_MODE_ENV, raw)
    with pytest.raises(ValueError, match=EDGE_MODE_ENV):
        resolve_edge_mode()


def test_edge_mode_invalid_explicit_fails_closed(monkeypatch):
    monkeypatch.delenv(EDGE_MODE_ENV, raising=False)
    with pytest.raises(ValueError, match="invalid edge mode 'Hybrid'"):
        resolve_edge_mode("Hybrid")


def test_edge_mode_explicit_argument_overrides_env(monkeypatch):
    monkeypatch.setenv(EDGE_MODE_ENV, "deterministic")
    assert resolve_edge_mode("hybrid") is EdgeMode.hybrid
    monkeypatch.setenv(EDGE_MODE_ENV, "hybrid")
    assert resolve_edge_mode(EdgeMode.deterministic) is EdgeMode.deterministic
    assert resolve_edge_mode("deterministic") is EdgeMode.deterministic
    # An explicit value wins even over an invalid env.
    monkeypatch.setenv(EDGE_MODE_ENV, "bogus")
    assert resolve_edge_mode("hybrid") is EdgeMode.hybrid


def test_edge_mode_explicit_blank_falls_through_to_env(monkeypatch):
    monkeypatch.setenv(EDGE_MODE_ENV, "hybrid")
    assert resolve_edge_mode("  ") is EdgeMode.hybrid


# --------------------------------------------------------------------------- #
# S3-edge-3: classify_route (D3 route table)                                   #
# --------------------------------------------------------------------------- #
INPUT_REQUIRED = TaskState.TASK_STATE_INPUT_REQUIRED
COMPLETED = TaskState.TASK_STATE_COMPLETED


def _collect_parts():
    return encode_collect_request(CollectRequest(process="address-proof"))


def _url_part():
    return ph.new_url_part("images/gov-id-clean.svg", media_type="image/svg+xml")


def _resolution_part():
    part = ph.new_data_part(
        {"call_id": "c1", "resolution": "accepted", "message": "looks fine"}
    )
    part.metadata.CopyFrom(
        ParseDict({"kind": KIND_HUMAN_REVIEW_RESOLUTION}, struct_pb2.Struct())
    )
    return part


def _assertion_msg():
    return _msg([*_collect_parts(), ph.new_data_part(_assertion())])


def _route(message, mode, *, task_state=None, pending=False):
    return classify_route(
        message, mode=mode, task_state=task_state, has_pending_review=pending
    )


DET = EdgeMode.deterministic
HYB = EdgeMode.hybrid


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        pytest.param(_assertion_msg(), Route.PATH_A, id="assertion"),
        pytest.param(_msg([*_collect_parts(), _url_part()]), Route.COLLECT, id="collect+file"),
        pytest.param(_msg(_collect_parts()), Route.COLLECT, id="collect-only"),
        pytest.param(_msg([ph.new_text_part("delete bill.pdf")]), Route.COLLECT, id="text-only"),
        pytest.param(_msg([_resolution_part()]), Route.COLLECT, id="resolution"),
        pytest.param(
            _msg([_resolution_part(), ph.new_text_part("ok")]), Route.COLLECT,
            id="resolution+text",
        ),
    ],
)
@pytest.mark.parametrize(
    ("task_state", "pending"),
    [(None, False), (INPUT_REQUIRED, True), (INPUT_REQUIRED, False), (COMPLETED, True)],
)
def test_classify_route_deterministic_is_path_a_or_collect(
    message, expected, task_state, pending
):
    """Deterministic = R1 + R2 only, exactly classify_path; state/pending ignored."""
    assert _route(message, DET, task_state=task_state, pending=pending) is expected
    legacy = Route.PATH_A if classify_path(message) is Path.A else Route.COLLECT
    assert expected is legacy


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        pytest.param(_assertion_msg(), Route.PATH_A, id="assertion"),
        pytest.param(_msg(_collect_parts()), Route.COLLECT, id="collect-only"),
        pytest.param(_msg([_url_part()]), Route.COLLECT, id="file-only-url"),
        pytest.param(
            _msg([ph.new_raw_part(b"inline", media_type="image/svg+xml")]),
            Route.COLLECT, id="raw-inline-bytes",
        ),
        pytest.param(
            _msg([ph.new_text_part("hi"), ph.new_raw_part(b"x", media_type="image/png")]),
            Route.COLLECT, id="text+raw",
        ),
        pytest.param(
            _msg([ph.new_text_part("collect address proof"), _url_part()]),
            Route.COLLECT, id="text+url",
        ),
        pytest.param(
            _msg([ph.new_text_part("please"), *_collect_parts()]),
            Route.COLLECT, id="text+collect",
        ),
        pytest.param(_msg([ph.new_text_part("delete bill.pdf")]), Route.LLM, id="text-only"),
        pytest.param(
            _msg([ph.new_text_part("I need"), ph.new_text_part("a person")]),
            Route.LLM, id="multi-text",
        ),
        pytest.param(_msg([ph.new_text_part("   ")]), Route.COLLECT, id="whitespace-text"),
        pytest.param(_msg([]), Route.COLLECT, id="empty"),
        pytest.param(
            _msg([ph.new_data_part({"not": "a-request"})]), Route.COLLECT,
            id="unrelated-data",
        ),
        pytest.param(_msg([_resolution_part()]), Route.NO_PENDING_REVIEW, id="resolution"),
        pytest.param(
            _msg([ph.new_text_part("approve it"), _resolution_part()]),
            Route.NO_PENDING_REVIEW, id="resolution+text",
        ),
    ],
)
def test_classify_route_hybrid_new_task(message, expected):
    assert _route(message, HYB) is expected


def test_classify_route_hybrid_text_on_escalated_task_goes_to_llm():
    """R4 also covers an INPUT_REQUIRED task with no pending review (escalated)."""
    msg = _msg([ph.new_text_part("any news?")])
    assert _route(msg, HYB, task_state=INPUT_REQUIRED, pending=False) is Route.LLM


def test_classify_route_hybrid_resolution_matches_pending_review():
    msg = _msg([_resolution_part()])
    assert _route(msg, HYB, task_state=INPUT_REQUIRED, pending=True) is Route.RESUME_REVIEW
    with_text = _msg([ph.new_text_part("approved"), _resolution_part()])
    assert (
        _route(with_text, HYB, task_state=INPUT_REQUIRED, pending=True)
        is Route.RESUME_REVIEW
    )


@pytest.mark.parametrize(
    "message",
    [
        pytest.param(_assertion_msg(), id="assertion"),
        pytest.param(_msg([*_collect_parts(), _url_part()]), id="collect+file"),
        pytest.param(_msg([ph.new_text_part("hello?")]), id="text-only"),
        pytest.param(_msg([ph.new_data_part({"not": "a-request"})]), id="unrelated"),
    ],
)
def test_classify_route_hybrid_guard_precedes_everything(message):
    """G precedes R1: a task parked for a reviewer accepts only the resolution."""
    assert _route(message, HYB, task_state=INPUT_REQUIRED, pending=True) is (
        Route.GUARD_REFUSE
    )


@pytest.mark.parametrize("task_state", [None, COMPLETED, TaskState.TASK_STATE_WORKING])
def test_classify_route_hybrid_pending_only_guards_input_required(task_state):
    """has_pending_review alone does not guard — only on an INPUT_REQUIRED task."""
    assert _route(
        _msg([ph.new_text_part("hi")]), HYB, task_state=task_state, pending=True
    ) is Route.LLM
    assert _route(_assertion_msg(), HYB, task_state=task_state, pending=True) is (
        Route.PATH_A
    )
    assert _route(
        _msg([_resolution_part()]), HYB, task_state=task_state, pending=True
    ) is Route.NO_PENDING_REVIEW
