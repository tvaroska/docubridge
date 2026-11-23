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
    uri = scoped_uri("http://test/", context_id="ab/cd", filename="a b.svg", version=2)
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
        assert resp.headers["location"] == _fake_signer(
            ctx, "gov-id-clean.svg", _UNSCOPED, 0
        )
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

    monkeypatch.setenv(ARTIFACT_BUCKET_ENV, "shared-project-bucket/document_bridge")
    scoped_store = build_artifact_store()
    assert isinstance(scoped_store, _StubGcs)
    assert built["bucket"] == "shared-project-bucket"


async def test_edge_enterprise_stores_wire_and_vault_submitted_bytes() -> None:
    """Binding enterprise stores on build_a2a_app vaults exact submitted bytes on Collect (P13-5)."""
    import hashlib

    from bridge.edge.server import _enterprise_stores_from_env
    from bridge.knowledge_catalog import InMemoryKnowledgeCatalog
    from bridge.operational_store import InMemoryOperationalStore
    from bridge.vault import InMemoryAuditVaultStore

    # Default local env resolves all three enterprise stores cleanly via ports.
    env_vault, env_op, env_cat = _enterprise_stores_from_env()
    assert env_vault is not None and env_op is not None and env_cat is not None

    vault = InMemoryAuditVaultStore()
    op_store = InMemoryOperationalStore()
    catalog = InMemoryKnowledgeCatalog(operational_store=op_store)
    executor = CollectExecutor(collector=Collector(FixtureExtractor()))
    app = build_a2a_app(
        url=URL,
        executor=executor,
        vault_store=vault,
        operational_store=op_store,
        knowledge_catalog=catalog,
    )
    async with _client(app) as (_, client):
        turn = await _turn_for_accepted(client)
        assert turn.status.entries[0].disposition is Disposition.accepted

    records = vault.list_records(context_id=turn.context_id)
    assert len(records) == 1
    assert records[0].sha256 == hashlib.sha256(_GOV_ID_BYTES).hexdigest()
    assert records[0].size_bytes == len(_GOV_ID_BYTES)


async def test_edge_freshness_clock_routes_stale_utility_bill_to_resubmit() -> None:
    """Binding freshness_clock on build_a2a_app evaluates validation.yaml within_days on Collect (P13-15)."""
    from datetime import date

    from contract.enums import Gate

    executor = CollectExecutor(collector=Collector(FixtureExtractor()))
    app = build_a2a_app(
        url=URL,
        executor=executor,
        freshness_clock=lambda: date(2026, 11, 22),
    )
    async with _client(app) as (_, client):
        task_id = await _send(
            client,
            "address-proof",
            [("bill-powerco-clean", "images/bill-powerco-clean.svg")],
        )
        task = await client.get_task(GetTaskRequest(id=task_id))
        turn = _exchange_turn_from_artifacts(task)

    entry = turn.status.entries[0]
    assert entry.disposition is Disposition.rejected
    assert entry.gate is Gate.resubmit
    assert entry.reason_code == "stale_document"


# --------------------------------------------------------------------------- #
# P14-1: Edge authentication & cross-caller task/artifact isolation            #
# --------------------------------------------------------------------------- #
async def test_second_caller_cannot_list_or_fetch_first_callers_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two callers on the A2A edge cannot list/get each other's tasks or download artifacts (P14-1)."""
    from a2a.types import ListTasksRequest
    from a2a.utils.errors import A2AError

    from bridge.edge.auth import EdgeAuthBackend
    from bridge.identity import (
        SPIFFE_SVID_HEADER,
        SPIFFE_SVID_SECRET_ENV,
        USER_JWT_SECRET_ENV,
        USER_TOKEN_HEADER,
        mint_jwt_svid,
        mint_user_token,
    )

    monkeypatch.setenv("K_SERVICE", "bridge-kyc")
    monkeypatch.setenv(SPIFFE_SVID_SECRET_ENV, "p14-1-edge-svid-signing-secret-2026")
    monkeypatch.setenv(USER_JWT_SECRET_ENV, "p14-1-edge-user-jwt-secret-2026")

    # Under K_SERVICE, build_a2a_app refuses to start without an auth_backend
    with pytest.raises(RuntimeError, match="auth_backend"):
        build_a2a_app(
            url=URL,
            executor=CollectExecutor(collector=Collector(FixtureExtractor())),
        )

    store = InMemoryArtifactService()
    app = build_a2a_app(
        url=URL,
        executor=CollectExecutor(collector=Collector(FixtureExtractor())),
        artifact_store=store,
        auth_backend=EdgeAuthBackend(),
    )
    caller_a_headers = {
        SPIFFE_SVID_HEADER: mint_jwt_svid(
            "spiffe://agents.docubridge.io/internal/broker-agent"
        )
    }
    caller_b_headers = {
        USER_TOKEN_HEADER: mint_user_token("usr:second-caller@other.example", "client")
    }

    transport = httpx.ASGITransport(app=app)

    # Public agent-card remains readable for discovery/healthcheck; anonymous JSON-RPC is 401
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as anon_hc:
        card_resp = await anon_hc.get("/.well-known/agent-card.json")
        assert card_resp.status_code == 200
        rpc_anon = await anon_hc.post(
            "/",
            json={"jsonrpc": "2.0", "id": "1", "method": "tasks/list", "params": {}},
        )
        assert rpc_anon.status_code == 401

    # Caller A submits a document and retrieves both task and artifact
    async with httpx.AsyncClient(
        transport=transport, base_url=BASE, headers=caller_a_headers
    ) as hc_a:
        cfg_a = ClientConfig(
            httpx_client=hc_a,
            streaming=True,
            supported_protocol_bindings=[TransportProtocol.JSONRPC.value],
        )
        client_a = await ClientFactory(cfg_a).create_from_url(URL)
        task_id = await _send(client_a, "address-proof", [ACCEPTED])
        task_a = await client_a.get_task(GetTaskRequest(id=task_id))
        turn_a = _exchange_turn_from_artifacts(task_a)
        listed_a = await client_a.list_tasks(ListTasksRequest())
        assert [t.id for t in listed_a.tasks] == [task_id]

        artifact_path = _fetch_path(turn_a.status.entries[0].artifact.uri)
        own_fetch = await hc_a.get(artifact_path)
        assert own_fetch.status_code == 200
        assert own_fetch.content == _GOV_ID_BYTES

    # Anonymous artifact fetch -> 401
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as anon_hc:
        assert (await anon_hc.get(artifact_path)).status_code == 401

    # Caller B cannot list Caller A's task, cannot get it by id, and cannot fetch its artifact
    async with httpx.AsyncClient(
        transport=transport, base_url=BASE, headers=caller_b_headers
    ) as hc_b:
        cfg_b = ClientConfig(
            httpx_client=hc_b,
            streaming=True,
            supported_protocol_bindings=[TransportProtocol.JSONRPC.value],
        )
        client_b = await ClientFactory(cfg_b).create_from_url(URL)
        listed_b = await client_b.list_tasks(ListTasksRequest())
        assert listed_b.tasks == []
        with pytest.raises(A2AError):
            await client_b.get_task(GetTaskRequest(id=task_id))
        foreign_fetch = await hc_b.get(artifact_path)
        assert foreign_fetch.status_code == 404

    # P0 regression: showcase viewer cookie minted by GET /demo must be refused (401) on A2A edge
    viewer_jwt = mint_user_token("viewer:demo", "broker", scope="showcase_read")
    async with httpx.AsyncClient(
        transport=transport,
        base_url=BASE,
        headers={USER_TOKEN_HEADER: viewer_jwt},
    ) as hc_viewer:
        rpc_viewer = await hc_viewer.post(
            "/",
            json={"jsonrpc": "2.0", "id": "2", "method": "tasks/list", "params": {}},
        )
        assert rpc_viewer.status_code == 401
        assert (await hc_viewer.get(artifact_path)).status_code == 401

    # P1 regression: Bearer OIDC without BRIDGE_PUBLIC_URL audience or unverified email -> 401
    oidc_app = build_a2a_app(
        url=URL,
        executor=CollectExecutor(collector=Collector(FixtureExtractor())),
        artifact_store=store,
        auth_backend=EdgeAuthBackend(
            oidc_verifier=lambda _tok, expected_audience=None: {
                "email": "sa@test.iam.gserviceaccount.com",
                "email_verified": False,
            }
        ),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=oidc_app),
        base_url=BASE,
        headers={"Authorization": "Bearer fake-oidc"},
    ) as hc_oidc:
        assert (
            await hc_oidc.post(
                "/",
                json={
                    "jsonrpc": "2.0",
                    "id": "3",
                    "method": "tasks/list",
                    "params": {},
                },
            )
        ).status_code == 401
        monkeypatch.setenv("BRIDGE_PUBLIC_URL", "https://bridge.example.run.app")
        assert (
            await hc_oidc.post(
                "/",
                json={
                    "jsonrpc": "2.0",
                    "id": "4",
                    "method": "tasks/list",
                    "params": {},
                },
            )
        ).status_code == 401


async def test_artifact_segment_and_email_owner_isolation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reserved party `_bridge_index`, path traversal segments, and email-owned artifacts fail closed."""
    from bridge.collect import DocumentSubmission
    from bridge.edge.artifacts import ARTIFACT_INDEX_USER, persist_submission
    from bridge.edge.auth import EdgeAuthBackend
    from bridge.identity import USER_TOKEN_HEADER, mint_user_token
    from contract.wire import ArtifactRef

    monkeypatch.setenv("K_SERVICE", "bridge")
    monkeypatch.setenv("BRIDGE_SPIFFE_SVID_SECRET", "p14-edge-seg-secret-2026")
    store = InMemoryArtifactService()
    ctx = uuid.uuid4().hex
    sub = DocumentSubmission(
        doc_id="mail-1",
        artifact_name="quote.pdf",
        artifact=ArtifactRef(
            uri="https://carrier.example/quote.pdf",
            mime_type="application/pdf",
            name="quote.pdf",
        ),
    )

    with pytest.raises(ValueError, match="Reserved"):
        await persist_submission(
            store,
            party=ARTIFACT_INDEX_USER,
            context_id=ctx,
            submission=sub,
            resolver=lambda _u: (b"%PDF", "application/pdf"),
            caller_principal="usr:attacker@example.com",
        )

    bad_sub = DocumentSubmission(
        doc_id="bad-1",
        artifact_name="../secret.pdf",
        artifact=ArtifactRef(
            uri="https://carrier.example/secret.pdf",
            mime_type="application/pdf",
            name="../secret.pdf",
        ),
    )
    with pytest.raises(ValueError, match="Unsafe"):
        await persist_submission(
            store,
            party="carrier-a",
            context_id=ctx,
            submission=bad_sub,
            resolver=lambda _u: (b"%PDF", "application/pdf"),
            caller_principal="usr:attacker@example.com",
        )

    # Email-persisted artifact owned by system:email-ingress cannot be read or overwritten by foreign user
    await persist_submission(
        store,
        party="carrier-a",
        context_id=ctx,
        submission=sub,
        resolver=lambda _u: (b"%PDF-email-body", "application/pdf"),
        caller_principal="system:email-ingress",
    )
    with pytest.raises(PermissionError, match="owned by another principal"):
        await persist_submission(
            store,
            party="carrier-a",
            context_id=ctx,
            submission=sub,
            resolver=lambda _u: (b"%PDF-hijack", "application/pdf"),
            caller_principal="usr:attacker@example.com",
        )

    edge_app = build_a2a_app(
        url=URL,
        executor=CollectExecutor(collector=Collector(FixtureExtractor())),
        artifact_store=store,
        auth_backend=EdgeAuthBackend(require_auth=True),
    )
    foreign_tok = mint_user_token("usr:attacker@example.com", "client")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=edge_app),
        base_url=BASE,
        headers={USER_TOKEN_HEADER: foreign_tok},
    ) as hc:
        assert (await hc.get(f"/artifacts/{ctx}/quote.pdf")).status_code == 404


# --------------------------------------------------------------------------- #
# P14-4: Party-scoped artifact fetch via public BaseArtifactService API only   #
# --------------------------------------------------------------------------- #
class _OpaqueArtifactStore:
    """Wrapper exposing ONLY public ``BaseArtifactService`` methods (no ``.artifacts`` dict)."""

    def __init__(self) -> None:
        self._inner = InMemoryArtifactService()

    async def save_artifact(self, **kwargs):
        return await self._inner.save_artifact(**kwargs)

    async def load_artifact(self, **kwargs):
        return await self._inner.load_artifact(**kwargs)

    async def list_versions(self, **kwargs):
        return await self._inner.list_versions(**kwargs)


async def test_fetch_finds_party_scoped_artifact_without_store_internals() -> None:
    """Party-scoped artifacts resolve on stores without a private ``.artifacts`` dict (P14-4)."""
    from bridge.collect import DocumentSubmission
    from bridge.edge.artifacts import persist_submission
    from contract.wire import ArtifactRef

    store = _OpaqueArtifactStore()
    assert not hasattr(store, "artifacts")
    ctx = uuid.uuid4().hex
    submission = DocumentSubmission(
        doc_id="quote-1",
        artifact_name="chubb-quote.pdf",
        artifact=ArtifactRef(
            uri="https://carrier.example/chubb-quote.pdf",
            mime_type="application/pdf",
            name="chubb-quote.pdf",
        ),
    )

    version = await persist_submission(
        store,  # type: ignore[arg-type]
        party="chubb-carrier",
        context_id=ctx,
        submission=submission,
        resolver=lambda _uri: (b"%PDF-1.7 chubb-body", "application/pdf"),
    )
    assert version == 0

    # Direct-stream fetch works without `.artifacts`
    direct_app = build_fetch_app(store)  # type: ignore[arg-type]
    async with _fetch_client(direct_app) as hc:
        resp = await hc.get(f"/{ctx}/chubb-quote.pdf?v=0")
        assert resp.status_code == 200
        assert resp.content == b"%PDF-1.7 chubb-body"
        assert resp.headers["content-type"] == "application/pdf"

    # Signed-URL redirect mode also resolves the named party without `.artifacts`
    signed_app = build_fetch_app(store, signer=_fake_signer)  # type: ignore[arg-type]
    async with _fetch_client(signed_app) as hc:
        resp_signed = await hc.get(f"/{ctx}/chubb-quote.pdf")
        assert resp_signed.status_code == 307
        assert resp_signed.headers["location"] == _fake_signer(
            ctx, "chubb-quote.pdf", "chubb-carrier", 0
        )


@pytest.mark.skipif(
    __import__("os").environ.get("BRIDGE_TEST_GCP") != "1",
    reason="Live GCS bucket check requires BRIDGE_TEST_GCP=1 and BRIDGE_ARTIFACT_BUCKET",
)
async def test_fetch_finds_party_scoped_artifact_on_gcs_bucket() -> None:
    """Live GCS variant of P14-4 verifying GcsArtifactService round-trip under a named party."""
    import os

    from bridge.collect import DocumentSubmission
    from bridge.edge.artifacts import persist_submission
    from contract.wire import ArtifactRef

    if not os.environ.get(ARTIFACT_BUCKET_ENV, "").strip():
        pytest.skip(f"{ARTIFACT_BUCKET_ENV} is not set")
    store = build_artifact_store()
    ctx = f"p14-4-{uuid.uuid4().hex}"
    sub = DocumentSubmission(
        doc_id="gcs-doc",
        artifact_name="party-doc.pdf",
        artifact=ArtifactRef(
            uri="https://carrier.example/party-doc.pdf",
            mime_type="application/pdf",
            name="party-doc.pdf",
        ),
    )
    ver = await persist_submission(
        store,
        party="named-party",
        context_id=ctx,
        submission=sub,
        resolver=lambda _u: (b"%PDF-1.7 live-gcs", "application/pdf"),
    )
    assert ver == 0
    app = build_fetch_app(store, signer=_fake_signer)
    async with _fetch_client(app) as hc:
        resp = await hc.get(f"/{ctx}/party-doc.pdf?v=0")
        assert resp.status_code == 307
        assert resp.headers["location"] == _fake_signer(
            ctx, "party-doc.pdf", "named-party", 0
        )


async def test_edge_oidc_allowlist_spiffe_policy_and_serve_container_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R1: OIDC Bearer enforces email allowlist, SPIFFE enforces agent policy, serve_container enforces auth without K_SERVICE."""
    from bridge.edge import server as edge_server
    from bridge.edge.auth import EdgeAuthBackend
    from bridge.identity import SPIFFE_SVID_HEADER, mint_jwt_svid

    monkeypatch.delenv("K_SERVICE", raising=False)
    monkeypatch.delenv("BRIDGE_A2UI_REQUIRE_AUTH", raising=False)
    monkeypatch.delenv("BRIDGE_OIDC_ALLOWED_EMAILS", raising=False)
    monkeypatch.delenv("BRIDGE_RUNTIME_SA_EMAIL", raising=False)
    monkeypatch.setenv("BRIDGE_PUBLIC_URL", "https://bridge.example.run.app")
    monkeypatch.setenv("BRIDGE_SPIFFE_SVID_SECRET", "r1-edge-auth-test-secret-2026")

    def _verifier(tok: str, expected_audience: str | None = None) -> dict[str, object]:
        assert expected_audience == "https://bridge.example.run.app"
        if tok == "tok-attacker":
            return {
                "email": "attacker@evil-proj.iam.gserviceaccount.com",
                "email_verified": True,
                "sub": "999",
            }
        if tok == "tok-runtime-sa":
            return {
                "email": "Bridge-Runtime@proj.iam.gserviceaccount.com",
                "email_verified": True,
                "sub": "101",
            }
        if tok == "tok-peer-sa":
            return {
                "email": "peer-caller@partner.iam.gserviceaccount.com",
                "email_verified": True,
                "sub": "202",
            }
        if tok == "tok-no-email":
            return {"email_verified": True, "sub": "only-sub"}
        raise ValueError("unknown token")

    store = InMemoryArtifactService()
    app = build_a2a_app(
        url=URL,
        executor=CollectExecutor(collector=Collector(FixtureExtractor())),
        artifact_store=store,
        auth_backend=EdgeAuthBackend(oidc_verifier=_verifier, require_auth=True),
        require_auth=True,
    )
    transport = httpx.ASGITransport(app=app)
    rpc_body = {"jsonrpc": "2.0", "id": "r1-1", "method": "ListTasks", "params": {}}
    v1_hdr = {"a2a-version": "1.0"}

    # Empty allowlist fails closed even for verified OIDC emails; anonymous fails closed without K_SERVICE
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as hc:
        assert (await hc.post("/", json=rpc_body, headers=v1_hdr)).status_code == 401
        assert (
            await hc.post(
                "/",
                json=rpc_body,
                headers={**v1_hdr, "Authorization": "Bearer tok-runtime-sa"},
            )
        ).status_code == 401

    # Configure BRIDGE_RUNTIME_SA_EMAIL + BRIDGE_OIDC_ALLOWED_EMAILS -> allowlisted pass, attacker/no-email 401
    monkeypatch.setenv(
        "BRIDGE_RUNTIME_SA_EMAIL", "bridge-runtime@proj.iam.gserviceaccount.com"
    )
    monkeypatch.setenv(
        "BRIDGE_OIDC_ALLOWED_EMAILS", "peer-caller@partner.iam.gserviceaccount.com"
    )
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as hc:
        ok_runtime = await hc.post(
            "/",
            json=rpc_body,
            headers={**v1_hdr, "Authorization": "Bearer tok-runtime-sa"},
        )
        assert ok_runtime.status_code == 200
        assert "result" in ok_runtime.json()

        ok_peer = await hc.post(
            "/",
            json=rpc_body,
            headers={**v1_hdr, "Authorization": "Bearer tok-peer-sa"},
        )
        assert ok_peer.status_code == 200
        assert "result" in ok_peer.json()

        assert (
            await hc.post(
                "/",
                json=rpc_body,
                headers={**v1_hdr, "Authorization": "Bearer tok-attacker"},
            )
        ).status_code == 401
        assert (
            await hc.post(
                "/",
                json=rpc_body,
                headers={**v1_hdr, "Authorization": "Bearer tok-no-email"},
            )
        ).status_code == 401

        # Registered SPIFFE SVID passes (header and Bearer); Shadow AI SVID -> 401
        valid_svid = mint_jwt_svid(
            "spiffe://agents.docubridge.io/internal/broker-agent"
        )
        shadow_svid = mint_jwt_svid(
            "spiffe://agents.docubridge.io/shadow/unregistered-probe"
        )
        ok_svid_hdr = await hc.post(
            "/",
            json=rpc_body,
            headers={**v1_hdr, SPIFFE_SVID_HEADER: valid_svid},
        )
        assert ok_svid_hdr.status_code == 200
        assert "result" in ok_svid_hdr.json()
        ok_svid_bearer = await hc.post(
            "/",
            json=rpc_body,
            headers={**v1_hdr, "Authorization": f"Bearer {valid_svid}"},
        )
        assert ok_svid_bearer.status_code == 200
        assert "result" in ok_svid_bearer.json()
        assert (
            await hc.post(
                "/",
                json=rpc_body,
                headers={**v1_hdr, SPIFFE_SVID_HEADER: shadow_svid},
            )
        ).status_code == 401
        assert (
            await hc.post(
                "/",
                json=rpc_body,
                headers={**v1_hdr, "Authorization": f"Bearer {shadow_svid}"},
            )
        ).status_code == 401

    # serve_container() explicitly passes require_auth=True even when K_SERVICE is unset
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        "uvicorn.run", lambda built_app, **kw: captured.update(app=built_app, kw=kw)
    )
    edge_server.serve_container()
    built_app = captured["app"]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=built_app), base_url=BASE
    ) as hc_srv:
        assert (await hc_srv.get("/.well-known/agent-card.json")).status_code == 200
        assert (await hc_srv.post("/", json=rpc_body)).status_code == 401
        assert (await hc_srv.get("/artifacts/ctx-1/file.pdf")).status_code == 401
