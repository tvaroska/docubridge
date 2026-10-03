"""ART-2 acceptance suite: scoped artifact-fetch endpoint + by-reference wire.

Drives the edge in-process over ``httpx.ASGITransport`` (like
``tests/test_edge_a2a.py``): inbound persists document bytes into a shared
``InMemoryArtifactService``; outbound rewrites each ledger entry's
``ArtifactRef.uri`` to the scoped ``/artifacts/{context_id}/{filename}`` endpoint;
a GET fetches the bytes on demand and every fetch is leg-scoped (a foreign
``context_id``/filename → 404). Bytes never ride an A2A message.
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest
from a2a.client import ClientConfig, ClientFactory
from a2a.helpers import proto_helpers as ph
from a2a.types import GetTaskRequest, Message, Role, SendMessageRequest
from a2a.utils.constants import TransportProtocol
from google.adk.artifacts import InMemoryArtifactService
from google.protobuf import struct_pb2
from google.protobuf.json_format import MessageToDict, ParseDict

from bridge.collect import Collector
from bridge.edge import CollectExecutor, build_a2a_app, scoped_uri
from bridge.edge.artifacts import APP_NAME, UNSCOPED_USER
from bridge.edge.wire import KIND_EXCHANGE_TURN, META_KIND
from bridge.extraction.fixture import FixtureExtractor
from contract.enums import Disposition
from contract.wire import CollectRequest, ExchangeTurn

BASE = "http://test"
URL = "http://test/"

ACCEPTED = ("gov-id-clean", "images/gov-id-clean.svg")
_CORPUS_ROOT = Path(__file__).resolve().parents[1] / "wiki/evals/address"
_GOV_ID_BYTES = (_CORPUS_ROOT / "images/gov-id-clean.svg").read_bytes()


# --------------------------------------------------------------------------- #
# Helpers (mirrors tests/test_edge_a2a.py)                                     #
# --------------------------------------------------------------------------- #
def _fixture_app(artifact_store=None):
    executor = CollectExecutor(collector=Collector(FixtureExtractor()))
    return build_a2a_app(url=URL, executor=executor, artifact_store=artifact_store)


@asynccontextmanager
async def _client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as hc:
        config = ClientConfig(
            httpx_client=hc,
            streaming=True,
            supported_protocol_bindings=[TransportProtocol.JSONRPC.value],
        )
        client = await ClientFactory(config).create_from_url(URL)
        yield hc, client


def _url_doc_part(doc_id: str, uri: str, mime: str = "image/svg+xml"):
    part = ph.new_url_part(uri, media_type=mime)
    part.metadata.CopyFrom(ParseDict({"doc_id": doc_id}, struct_pb2.Struct()))
    return part


def _collect_message(
    process: str, docs: list[tuple[str, str]], context_id: str | None = None
) -> Message:
    from bridge.edge import encode_collect_request

    parts = encode_collect_request(
        CollectRequest(process=process, context_id=context_id)
    )
    parts += [_url_doc_part(doc_id, uri) for doc_id, uri in docs]
    return Message(role=Role.ROLE_USER, message_id=uuid.uuid4().hex, parts=parts)


async def _send(client, process: str, docs, context_id: str | None = None):
    task_id = None
    async for ev in client.send_message(
        SendMessageRequest(message=_collect_message(process, docs, context_id))
    ):
        if ev.HasField("task"):
            task_id = ev.task.id
        elif ev.HasField("status_update"):
            task_id = ev.status_update.task_id
        elif ev.HasField("artifact_update"):
            task_id = ev.artifact_update.task_id
    return task_id


def _exchange_turn_from_artifacts(task) -> ExchangeTurn:
    for artifact in task.artifacts:
        for part in artifact.parts:
            if not part.HasField("data"):
                continue
            meta = MessageToDict(part.metadata) if part.HasField("metadata") else {}
            if meta.get(META_KIND) == KIND_EXCHANGE_TURN:
                return ExchangeTurn.model_validate(MessageToDict(part.data))
    raise AssertionError("no exchange_turn artifact data part found")


async def _turn_for_accepted(client) -> ExchangeTurn:
    task_id = await _send(client, "address-proof", [ACCEPTED])
    task = await client.get_task(GetTaskRequest(id=task_id))
    return _exchange_turn_from_artifacts(task)


def _fetch_path(uri: str) -> str:
    """Turn an absolute scoped uri into the path+query the ASGI client can GET."""
    parts = urlsplit(uri)
    return parts.path + (f"?{parts.query}" if parts.query else "")


# --------------------------------------------------------------------------- #
# Outbound reference points at the fetch endpoint                              #
# --------------------------------------------------------------------------- #
async def test_outbound_ref_points_at_fetch_endpoint():
    async with _client(_fixture_app()) as (_hc, client):
        turn = await _turn_for_accepted(client)
        entry = turn.status.entries[0]
        assert entry.disposition is Disposition.accepted
        assert entry.artifact is not None
        uri = entry.artifact.uri
        # Absolute, points at the scoped endpoint, carries the (minted) context_id
        # and the derived filename — NOT the raw corpus path.
        assert uri.startswith(f"{URL.rstrip('/')}/artifacts/")
        assert turn.context_id in uri
        assert "gov-id-clean.svg" in uri
        assert uri != "images/gov-id-clean.svg"


# --------------------------------------------------------------------------- #
# Fetch serves the bytes                                                       #
# --------------------------------------------------------------------------- #
async def test_fetch_endpoint_serves_bytes():
    async with _client(_fixture_app()) as (hc, client):
        turn = await _turn_for_accepted(client)
        uri = turn.status.entries[0].artifact.uri
        resp = await hc.get(_fetch_path(uri))
        assert resp.status_code == 200
        assert resp.content == _GOV_ID_BYTES
        assert resp.content
        assert resp.headers["content-type"] == "image/svg+xml"


# --------------------------------------------------------------------------- #
# Leg-scoping: a reference is not a capability                                 #
# --------------------------------------------------------------------------- #
async def test_fetch_foreign_scope_is_404():
    async with _client(_fixture_app()) as (hc, client):
        await _turn_for_accepted(client)
        # Same filename, a foreign context_id → 404.
        foreign = f"/artifacts/{uuid.uuid4().hex}/gov-id-clean.svg?v=0"
        resp = await hc.get(foreign)
        assert resp.status_code == 404


async def test_fetch_unknown_filename_is_404():
    async with _client(_fixture_app()) as (hc, client):
        turn = await _turn_for_accepted(client)
        resp = await hc.get(f"/artifacts/{turn.context_id}/nope.svg?v=0")
        assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# Versioning through the edge (ART-1)                                          #
# --------------------------------------------------------------------------- #
async def test_resubmission_creates_new_version():
    store = InMemoryArtifactService()
    async with _client(_fixture_app(artifact_store=store)) as (_hc, client):
        # First submission mints a context_id.
        task_id = await _send(client, "address-proof", [ACCEPTED])
        task = await client.get_task(GetTaskRequest(id=task_id))
        turn1 = _exchange_turn_from_artifacts(task)
        context_id = turn1.context_id

        # Resubmit the SAME filename under the SAME context_id.
        await _send(client, "address-proof", [ACCEPTED], context_id=context_id)

        versions = await store.list_versions(
            app_name=APP_NAME,
            user_id=UNSCOPED_USER,
            session_id=context_id,
            filename="gov-id-clean.svg",
        )
        assert versions == [0, 1]


async def test_fetch_specific_version_and_latest():
    store = InMemoryArtifactService()
    async with _client(_fixture_app(artifact_store=store)) as (hc, client):
        task_id = await _send(client, "address-proof", [ACCEPTED])
        task = await client.get_task(GetTaskRequest(id=task_id))
        context_id = _exchange_turn_from_artifacts(task).context_id
        await _send(client, "address-proof", [ACCEPTED], context_id=context_id)

        v0 = await hc.get(f"/artifacts/{context_id}/gov-id-clean.svg?v=0")
        latest = await hc.get(f"/artifacts/{context_id}/gov-id-clean.svg")
        assert v0.status_code == 200
        assert latest.status_code == 200
        # Both resolve; latest (v1) equals the same corpus bytes as v0 here.
        assert v0.content == _GOV_ID_BYTES
        assert latest.content == _GOV_ID_BYTES


async def test_fetch_distinct_bytes_per_version():
    """With a resolver that returns distinct bytes per call, ?v selects the right one."""
    store = InMemoryArtifactService()
    calls = {"n": 0}

    def resolver(uri: str):
        calls["n"] += 1
        return (f"bytes-{calls['n']}".encode(), "image/svg+xml")

    executor = CollectExecutor(
        collector=Collector(FixtureExtractor()), resolver=resolver
    )
    app = build_a2a_app(url=URL, executor=executor, artifact_store=store)
    # Re-bind the resolver: bind_store does not touch the resolver, so it stays.
    async with _client(app) as (hc, client):
        task_id = await _send(client, "address-proof", [ACCEPTED])
        task = await client.get_task(GetTaskRequest(id=task_id))
        context_id = _exchange_turn_from_artifacts(task).context_id
        await _send(client, "address-proof", [ACCEPTED], context_id=context_id)

        v0 = await hc.get(f"/artifacts/{context_id}/gov-id-clean.svg?v=0")
        v1 = await hc.get(f"/artifacts/{context_id}/gov-id-clean.svg?v=1")
        latest = await hc.get(f"/artifacts/{context_id}/gov-id-clean.svg")
        assert v0.content == b"bytes-1"
        assert v1.content == b"bytes-2"
        assert latest.content == b"bytes-2"


# --------------------------------------------------------------------------- #
# No inline bytes on the wire (reinforce)                                      #
# --------------------------------------------------------------------------- #
async def test_no_inline_bytes_still_rejected():
    from bridge.edge import decode_submissions

    msg = Message(
        role=Role.ROLE_USER,
        message_id="m",
        parts=[ph.new_raw_part(b"inline", media_type="image/svg+xml")],
    )
    with pytest.raises(ValueError):
        decode_submissions(msg)


# --------------------------------------------------------------------------- #
# Shared store: writer == reader (Decision 4 footgun)                          #
# --------------------------------------------------------------------------- #
async def test_shared_store_writer_equals_reader():
    """Behavioural proof: send → immediately fetch succeeds against the same app."""
    store = InMemoryArtifactService()
    async with _client(_fixture_app(artifact_store=store)) as (hc, client):
        turn = await _turn_for_accepted(client)
        uri = turn.status.entries[0].artifact.uri
        # The fetch app reads the same object the executor wrote to.
        resp = await hc.get(_fetch_path(uri))
        assert resp.status_code == 200
        # And the injected store is the one that got written.
        versions = await store.list_versions(
            app_name=APP_NAME,
            user_id=UNSCOPED_USER,
            session_id=turn.context_id,
            filename="gov-id-clean.svg",
        )
        assert versions == [0]


def test_scoped_uri_encodes_and_is_absolute():
    uri = scoped_uri(
        "http://test/", context_id="ab/cd", filename="a b.svg", version=2
    )
    assert uri == "http://test/artifacts/ab%2Fcd/a%20b.svg?v=2"


# --------------------------------------------------------------------------- #
# P7-3: signed-URL fetch endpoint (bytes never on the wire) + store factory    #
# --------------------------------------------------------------------------- #
import google.genai.types as genai_types  # noqa: E402

from bridge.edge.artifacts import (  # noqa: E402
    ARTIFACT_BUCKET_ENV,
    build_artifact_store,
    build_fetch_app,
)

_APP_NAME = APP_NAME
_UNSCOPED = UNSCOPED_USER


async def _seed_store(store, context_id, filename, data=b"hello", *, user=None):
    part = genai_types.Part.from_bytes(data=data, mime_type="image/svg+xml")
    await store.save_artifact(
        app_name=_APP_NAME,
        user_id=user or _UNSCOPED,
        session_id=context_id,
        filename=filename,
        artifact=part,
    )


def _fake_signer(context_id, filename, user_id, version):
    return f"https://signed.example/{user_id}/{context_id}/{filename}?v={version}"


@asynccontextmanager
async def _fetch_client(app):
    transport = httpx.ASGITransport(app=app)
    # follow_redirects=False so a 307 is observed, not chased (GCS isn't real).
    async with httpx.AsyncClient(
        transport=transport, base_url=BASE, follow_redirects=False
    ) as hc:
        yield hc


async def test_signed_url_redirect_for_present_artifact():
    """With a signer, a present (context_id, filename) → 307 to the signed URL —
    bytes never ride the response (Location header, empty body)."""
    store = InMemoryArtifactService()
    ctx = uuid.uuid4().hex
    await _seed_store(store, ctx, "gov-id-clean.svg", _GOV_ID_BYTES)
    app = build_fetch_app(store, signer=_fake_signer)
    async with _fetch_client(app) as hc:
        resp = await hc.get(f"/{ctx}/gov-id-clean.svg?v=0")
        assert resp.status_code == 307
        assert resp.headers["location"] == _fake_signer(ctx, "gov-id-clean.svg", _UNSCOPED, 0)
        # No bytes on the wire — the client must fetch GCS directly via the URL.
        assert resp.content == b""


async def test_signed_url_latest_version_resolves_concrete_version():
    """?v omitted resolves to the highest stored version in the signed blob path."""
    store = InMemoryArtifactService()
    ctx = uuid.uuid4().hex
    await _seed_store(store, ctx, "gov-id-clean.svg", b"v0")
    await _seed_store(store, ctx, "gov-id-clean.svg", b"v1")
    app = build_fetch_app(store, signer=_fake_signer)
    async with _fetch_client(app) as hc:
        resp = await hc.get(f"/{ctx}/gov-id-clean.svg")  # no ?v → latest (1)
        assert resp.status_code == 307
        assert resp.headers["location"].endswith("?v=1")


async def test_signed_url_foreign_scope_is_404_before_minting():
    """A foreign context_id → 404 even with a signer — a signed URL for a guessed
    path is NEVER mintable (leg-scoping preserved: authorize before you sign)."""
    store = InMemoryArtifactService()
    ctx = uuid.uuid4().hex
    await _seed_store(store, ctx, "gov-id-clean.svg", _GOV_ID_BYTES)
    app = build_fetch_app(store, signer=_fake_signer)
    async with _fetch_client(app) as hc:
        foreign = await hc.get(f"/{uuid.uuid4().hex}/gov-id-clean.svg?v=0")
        assert foreign.status_code == 404
        unknown = await hc.get(f"/{ctx}/nope.svg?v=0")
        assert unknown.status_code == 404


async def test_no_signer_still_streams_bytes():
    """signer=None (local) is unchanged: the endpoint streams the stored bytes."""
    store = InMemoryArtifactService()
    ctx = uuid.uuid4().hex
    await _seed_store(store, ctx, "gov-id-clean.svg", _GOV_ID_BYTES)
    app = build_fetch_app(store)  # signer defaults to None
    async with _fetch_client(app) as hc:
        resp = await hc.get(f"/{ctx}/gov-id-clean.svg?v=0")
        assert resp.status_code == 200
        assert resp.content == _GOV_ID_BYTES
        assert resp.headers["content-type"] == "image/svg+xml"


def test_build_artifact_store_defaults_in_memory(monkeypatch):
    """No BRIDGE_ARTIFACT_BUCKET → InMemoryArtifactService (local default)."""
    monkeypatch.delenv(ARTIFACT_BUCKET_ENV, raising=False)
    assert isinstance(build_artifact_store(), InMemoryArtifactService)


def test_build_artifact_store_gcs_when_bucket_set(monkeypatch):
    """BRIDGE_ARTIFACT_BUCKET set → GcsArtifactService (stubbed; no real bucket)."""
    import google.adk.artifacts as adk_artifacts

    built = {}

    class _StubGcs:
        def __init__(self, bucket_name):
            built["bucket"] = bucket_name

    monkeypatch.setattr(adk_artifacts, "GcsArtifactService", _StubGcs)
    monkeypatch.setenv(ARTIFACT_BUCKET_ENV, "my-artifacts-bucket")
    store = build_artifact_store()
    assert isinstance(store, _StubGcs)
    assert built["bucket"] == "my-artifacts-bucket"
