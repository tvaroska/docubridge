"""S0-arch-7 — the artifact resolver: a by-reference document is actually readable.

ART-2 puts documents on the wire **by reference**, so what reaches the extraction
port is the counterparty's URI. These tests drive the four inbound shapes end to end:
a ``gs://`` object handed to the model by reference, a party's ``http(s)`` URL fetched
under the default-deny egress guard, an opt-in ``file://`` path, and the eval corpus's
bare relative name (whose traversal containment must survive untouched).

Credential-free, always-run, and **network-free** — a loopback ``http.server`` is not
"the network". Gotcha worth naming: the egress guard rejects ``127.0.0.1`` because it
is non-global, so every fetch test allowlists the loopback host:port via
``BRIDGE_FETCH_ALLOWLIST``. That is the feature under test, not a workaround — it is
the same escape hatch ``tests/test_push_ssrf.py`` exercises on the push leg.

Scope boundaries: the whole-journey A2A conformance run is S0-arch-9, and a LIVE
``gs://`` read needs a provisioned bucket + Vertex credentials (gated below, skipped
by default).
"""

from __future__ import annotations

import http.server
import json
import logging
import os
import socket
import threading
from pathlib import Path
from typing import ClassVar

import httpx
import pytest
from google.adk.artifacts import InMemoryArtifactService
from google.genai import types as genai_types

from bridge.edge.artifacts import (
    APP_NAME,
    UNSCOPED_USER,
    build_fetch_app,
    resolve_bytes,
)
from bridge.extraction.gemini import GeminiExtractor, artifact_resolver
from bridge.extraction.resolve import (
    MAX_REDIRECTS,
    ResolvedDocument,
    _fetch_http,
    mime_for,
    resolve_document,
)
from contract.extraction import ExtractionResult

REPO_ROOT = Path(__file__).resolve().parents[1]
PDF_PATH = REPO_ROOT / "wiki/evals/benefits/images/benefits-carrier-c-eur-fr.pdf"
PDF_BYTES = PDF_PATH.read_bytes()
CORPUS_SVG = "images/gov-id-clean.svg"

_CANNED = json.dumps(
    {
        "fields": {
            "doctype": "benefits-quote",
            "issuer": "Carrier C",
            "key_fields": {"premium": "EUR 1000.00"},
        },
        "overall_confidence": 0.9,
        "field_confidence": {"premium": 0.9},
        "legible": True,
        "flagged_fields": [],
    }
)


# --------------------------------------------------------------------------- #
# Doubles                                                                      #
# --------------------------------------------------------------------------- #
class _FakeModels:
    def __init__(self, text: str) -> None:
        self._text = text
        self.calls: list[dict] = []

    def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        return type("_R", (), {"text": self._text})()


class _FakeClient:
    """A genai ``Client``-shaped double. Deliberately has NO ``vertexai`` attribute."""

    def __init__(self, text: str = _CANNED) -> None:
        self.models = _FakeModels(text)


class _DevApiClient(_FakeClient):
    """The Gemini Developer API backend — ``gs://`` is not readable there."""

    vertexai = False


class _Handler(http.server.BaseHTTPRequestHandler):
    """Serves the committed corpus PDF plus the redirect/edge-case routes."""

    seen: ClassVar[list[str]] = []

    def log_message(self, *args):  # keep the test output clean
        pass

    def do_GET(self):
        type(self).seen.append(self.path)
        if self.path == "/redirect":
            self.send_response(307)
            self.send_header("Location", "/doc.pdf")
            self.end_headers()
            return
        if self.path == "/evil-redirect":
            self.send_response(307)
            self.send_header("Location", "http://169.254.169.254/latest/meta-data/")
            self.end_headers()
            return
        if self.path == "/loop":
            self.send_response(307)
            self.send_header("Location", "/loop")
            self.end_headers()
            return
        if self.path == "/missing":
            self.send_response(404)
            self.end_headers()
            return
        body = PDF_BYTES
        self.send_response(200)
        if self.path != "/no-type.pdf":
            self.send_header("Content-Type", "application/pdf; charset=binary")
        if self.path != "/undeclared.pdf":
            # /undeclared.pdf omits Content-Length (HTTP/1.0 close-delimited) so the
            # hard read cap, not the declared-length check, is what stops an
            # oversized body.
            self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def server():
    """A loopback HTTP server serving the committed corpus PDF; yields its base URL."""
    _Handler.seen = []
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.fixture
def allowlisted(server, monkeypatch):
    """The loopback server, with its host:port in ``BRIDGE_FETCH_ALLOWLIST``."""
    monkeypatch.setenv("BRIDGE_FETCH_ALLOWLIST", server.removeprefix("http://"))
    return server


@pytest.fixture
def dead_host(monkeypatch):
    """An **allowlisted** loopback port with nothing listening; yields its base URL.

    Allowlisted on purpose: the URL must get past the egress guard so the failure
    under test is a real transport failure, not a refusal.
    """
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()  # bound then closed: the port is free, so the connect is refused
    monkeypatch.setenv("BRIDGE_FETCH_ALLOWLIST", f"127.0.0.1:{port}")
    return f"http://127.0.0.1:{port}"


# --------------------------------------------------------------------------- #
# gs:// — by reference, no download                                            #
# --------------------------------------------------------------------------- #
async def test_gs_uri_resolves_by_reference_without_fetching():
    """``gs://`` never downloads: no bytes, no client — the model reads the object."""

    def _boom(url, **kwargs):
        raise AssertionError(f"gs:// must not issue a fetch (got {url})")

    resolved = await resolve_document("gs://bucket/quote.pdf", fetch=_boom)

    assert resolved.by_reference is True
    assert resolved.uri == "gs://bucket/quote.pdf"
    assert resolved.data is None
    assert resolved.mime_type == "application/pdf"


async def test_gs_uri_without_a_known_suffix_refuses_loud():
    """An unclassifiable object refuses rather than shipping octet-stream garbage."""
    with pytest.raises(ValueError, match="mime type"):
        await resolve_document("gs://bucket/blob")


def test_resolved_document_needs_exactly_one_of_data_or_uri():
    with pytest.raises(ValueError, match="exactly one"):
        ResolvedDocument(mime_type="application/pdf")
    with pytest.raises(ValueError, match="exactly one"):
        ResolvedDocument(mime_type="application/pdf", data=b"x", uri="gs://b/o.pdf")


async def test_gs_uri_rides_the_request_as_file_data_not_inline():
    """THE by-reference assertion: the request part is ``Part.from_uri``, not bytes."""
    client = _FakeClient()
    extractor = GeminiExtractor(client=client, schemas={}, resolver=artifact_resolver())

    result = await extractor.extract("gs://bucket/quote.pdf")

    assert isinstance(result, ExtractionResult)
    part = client.models.calls[0]["contents"][1]
    assert isinstance(part, genai_types.Part)
    assert part.inline_data is None
    assert part.file_data.file_uri == "gs://bucket/quote.pdf"
    assert part.file_data.mime_type == "application/pdf"


async def test_gs_uri_on_the_developer_api_backend_refuses_loud():
    """``gs://`` is Vertex-only: a client that says so gets an actionable error."""
    extractor = GeminiExtractor(
        client=_DevApiClient(), schemas={}, resolver=artifact_resolver()
    )

    with pytest.raises(ValueError, match="GOOGLE_GENAI_USE_VERTEXAI"):
        await extractor.extract("gs://bucket/quote.pdf")


# --------------------------------------------------------------------------- #
# http(s) — the leg-scoped fetch                                               #
# --------------------------------------------------------------------------- #
async def test_https_style_fetch_returns_the_bytes_and_mime(allowlisted):
    """A party's URL resolves to the exact bytes with the served content type."""
    resolved = await resolve_document(f"{allowlisted}/doc.pdf")

    assert resolved.by_reference is False
    assert resolved.data == PDF_BYTES
    assert resolved.mime_type == "application/pdf"  # parameters stripped


async def test_fetched_document_is_extractable_through_the_engine(allowlisted):
    """The headline: a third-party by-reference document is extracted end to end."""
    client = _FakeClient()
    extractor = GeminiExtractor(client=client, schemas={}, resolver=artifact_resolver())

    result = await extractor.extract(f"{allowlisted}/doc.pdf")

    assert result.fields.doctype == "benefits-quote"
    part = client.models.calls[0]["contents"][1]
    assert part.inline_data.mime_type == "application/pdf"
    assert part.inline_data.data == PDF_BYTES


async def test_missing_content_type_falls_back_to_the_url_suffix(allowlisted):
    resolved = await resolve_document(f"{allowlisted}/no-type.pdf")

    assert resolved.mime_type == "application/pdf"
    assert resolved.data == PDF_BYTES


async def test_non_allowlisted_loopback_is_refused_with_no_request_issued(server):
    """Guard first, request second — the refused server never sees a connection."""
    with pytest.raises(ValueError, match="refused"):
        await resolve_document(f"{server}/doc.pdf")

    assert _Handler.seen == []


async def test_metadata_ip_is_refused():
    with pytest.raises(ValueError, match="non-global"):
        await resolve_document("http://169.254.169.254/latest/meta-data/")


async def test_redirect_is_followed(allowlisted):
    """Our own deployed fetch endpoint 307s to a GCS signed URL — follow it."""
    resolved = await resolve_document(f"{allowlisted}/redirect")

    assert resolved.data == PDF_BYTES
    assert _Handler.seen == ["/redirect", "/doc.pdf"]


async def test_redirect_to_the_metadata_ip_is_refused_on_the_second_hop(allowlisted):
    """Every hop is re-validated: hop 1 allowlisted does not license hop 2."""
    with pytest.raises(ValueError, match="non-global"):
        await resolve_document(f"{allowlisted}/evil-redirect")


async def test_redirect_loop_exceeds_the_hop_budget(allowlisted):
    with pytest.raises(ValueError, match="redirects"):
        await resolve_document(f"{allowlisted}/loop")

    assert len(_Handler.seen) == MAX_REDIRECTS + 1


async def test_non_2xx_response_raises(allowlisted):
    with pytest.raises(ValueError, match="HTTP 404"):
        await resolve_document(f"{allowlisted}/missing")


async def test_transport_failure_refuses_as_a_value_error(dead_host):
    """A dead host is a *failing fetch* — a ValueError, not a leaked httpx error.

    Both callers are written against that vocabulary; letting ``httpx.ConnectError``
    escape is what broke ``resolve_bytes``'s never-raise contract below.
    """
    with pytest.raises(ValueError, match="fetch failed: ConnectError"):
        await resolve_document(f"{dead_host}/doc.pdf")


async def test_read_timeout_refuses_as_a_value_error(dead_host):
    """The same normalization for an error raised mid-request by the client."""

    class _TimingOutClient:
        def stream(self, *args, **kwargs):
            raise httpx.ReadTimeout("timed out")

    async def _fetch(url, *, allowlist):
        return await _fetch_http(url, allowlist=allowlist, client=_TimingOutClient())

    with pytest.raises(ValueError, match="fetch failed: ReadTimeout"):
        await resolve_document(f"{dead_host}/doc.pdf", fetch=_fetch)


async def test_oversized_body_is_refused(allowlisted):
    """The body cap is enforced (declared Content-Length here, and on the read)."""

    async def _small_cap(url, *, allowlist):
        return await _fetch_http(url, allowlist=allowlist, max_bytes=16)

    with pytest.raises(ValueError, match="too large"):
        await resolve_document(f"{allowlisted}/doc.pdf", fetch=_small_cap)


async def test_oversized_body_without_a_declared_length_is_refused(allowlisted):
    """A body that lies (or says nothing) about its size still hits the read cap."""

    async def _small_cap(url, *, allowlist):
        return await _fetch_http(url, allowlist=allowlist, max_bytes=16)

    with pytest.raises(ValueError, match="body exceeds"):
        await resolve_document(f"{allowlisted}/undeclared.pdf", fetch=_small_cap)


# --------------------------------------------------------------------------- #
# file:// and unsupported schemes                                              #
# --------------------------------------------------------------------------- #
async def test_file_uri_is_refused_by_default(monkeypatch):
    """Otherwise ``file:///etc/passwd`` launders host files into the ledger."""
    monkeypatch.delenv("BRIDGE_ALLOW_FILE_URI", raising=False)

    with pytest.raises(ValueError, match="file:// URIs are disabled"):
        await resolve_document("file:///etc/passwd")


async def test_file_uri_opt_in_reads_the_path(monkeypatch):
    monkeypatch.setenv("BRIDGE_ALLOW_FILE_URI", "1")

    resolved = await resolve_document(f"file://{PDF_PATH}")

    assert resolved.data == PDF_BYTES
    assert resolved.mime_type == "application/pdf"


@pytest.mark.parametrize("uri", ["mock://x", "bridge://ledger/1", "ftp://host/f.pdf"])
async def test_unsupported_scheme_refuses_loud(uri):
    with pytest.raises(ValueError):
        await resolve_document(uri)


async def test_empty_uri_refuses_loud():
    with pytest.raises(ValueError, match="empty artifact uri"):
        await resolve_document("   ")


# --------------------------------------------------------------------------- #
# The corpus path is unchanged                                                 #
# --------------------------------------------------------------------------- #
async def test_corpus_relative_name_still_reads_the_corpus():
    resolved = await resolve_document(CORPUS_SVG)

    assert resolved.mime_type == "image/svg+xml"
    assert resolved.data.startswith(b"<")


async def test_corpus_traversal_is_still_contained():
    with pytest.raises(ValueError, match="escapes the corpus root"):
        await resolve_document("../../pyproject.toml")


def test_mime_for_is_the_single_suffix_table():
    assert mime_for("x.pdf") == "application/pdf"
    assert mime_for("images/y.svg") == "image/svg+xml"
    assert mime_for("z.bin") == "application/octet-stream"


# --------------------------------------------------------------------------- #
# Leg-scoping through our OWN fetch endpoint — a reference is not a capability #
# --------------------------------------------------------------------------- #
def _asgi_fetch(app):
    """A ``fetch`` override bound to an ASGI app (no sockets, no network)."""

    async def _fetch(url, *, allowlist):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport) as client:
            return await _fetch_http(url, allowlist=allowlist, client=client)

    return _fetch


@pytest.fixture
async def own_endpoint():
    """The real scoped fetch app over a store holding one artifact."""
    store = InMemoryArtifactService()
    context_id = "ctx-owned"
    await store.save_artifact(
        app_name=APP_NAME,
        user_id=UNSCOPED_USER,
        session_id=context_id,
        filename="quote.pdf",
        artifact=genai_types.Part.from_bytes(
            data=PDF_BYTES, mime_type="application/pdf"
        ),
    )
    return build_fetch_app(store), context_id


async def test_leg_scoped_own_reference_resolves(own_endpoint):
    app, context_id = own_endpoint

    resolved = await resolve_document(
        f"http://testserver/{context_id}/quote.pdf", fetch=_asgi_fetch(app)
    )

    assert resolved.data == PDF_BYTES
    assert resolved.mime_type == "application/pdf"


async def test_leg_scoped_foreign_context_id_is_refused_by_our_endpoint(own_endpoint):
    """A guessed/leaked URI of our own endpoint 404s — the resolve fails with it."""
    app, _ = own_endpoint

    with pytest.raises(ValueError, match="HTTP 404"):
        await resolve_document(
            "http://testserver/ctx-foreign/quote.pdf", fetch=_asgi_fetch(app)
        )


# --------------------------------------------------------------------------- #
# Edge parity — persistence keeps its never-raise posture (Decision 7)         #
# --------------------------------------------------------------------------- #
async def test_edge_resolve_bytes_still_reads_the_corpus():
    resolved = await resolve_bytes(CORPUS_SVG)

    assert resolved is not None
    data, mime = resolved
    assert mime == "image/svg+xml"
    assert data.startswith(b"<")


@pytest.mark.parametrize(
    "uri", ["mock://x", "", "images/does-not-exist.svg", "../../pyproject.toml"]
)
async def test_edge_resolve_bytes_never_raises(uri):
    """An unresolvable/refused URI skips persistence instead of failing the turn."""
    assert await resolve_bytes(uri) is None


async def test_edge_resolve_bytes_skips_by_reference_artifacts():
    """No bytes to persist: the consumer's ``gs://`` ref stays on the ledger."""
    assert await resolve_bytes("gs://bucket/quote.pdf") is None


async def test_edge_resolve_bytes_survives_a_transport_failure(dead_host, caplog):
    """THE blocker's regression test: an unreachable artifact host skips persistence.

    ``resolve_bytes`` used to catch ``(ValueError, OSError)`` only, so
    ``httpx.ConnectError`` escaped and a party's host being momentarily down failed
    the whole disposition turn instead of costing the ART-1 byte history.
    """
    with caplog.at_level(logging.WARNING, logger="bridge.edge.artifacts"):
        assert await resolve_bytes(f"{dead_host}/doc.pdf") is None

    assert any(
        "not persisted" in r.getMessage() and "ConnectError" in r.getMessage()
        for r in caplog.records
    )


async def test_edge_resolve_bytes_survives_an_unexpected_resolver_error(monkeypatch):
    """The never-raise contract is unconditional, not a list of anticipated types."""

    async def _boom(uri, **kwargs):
        raise RuntimeError("something nobody enumerated")

    monkeypatch.setattr("bridge.edge.artifacts.resolve_document", _boom)

    assert await resolve_bytes("images/gov-id-clean.svg") is None


async def test_edge_resolve_bytes_fetches_an_http_reference(allowlisted):
    resolved = await resolve_bytes(f"{allowlisted}/doc.pdf")

    assert resolved == (PDF_BYTES, "application/pdf")


# --------------------------------------------------------------------------- #
# Gated live leg (skipped by default)                                          #
# --------------------------------------------------------------------------- #
_LIVE_GS = os.environ.get("BRIDGE_TEST_GS_URI", "")


@pytest.mark.skipif(
    not (os.environ.get("BRIDGE_TEST_GEMINI") == "1" and _LIVE_GS),
    reason="set BRIDGE_TEST_GEMINI=1 and BRIDGE_TEST_GS_URI=gs://… to run live",
)
async def test_live_gs_uri_extraction():
    """A REAL ``gs://`` object read natively by Vertex (needs creds + a bucket)."""
    result = await GeminiExtractor().extract(_LIVE_GS)

    assert isinstance(result, ExtractionResult)
    assert result.fields.doctype
