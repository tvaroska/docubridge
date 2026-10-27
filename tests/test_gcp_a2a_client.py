"""GCP a2a_client (Agent-Identity transport) adapter unit tests (P7-5).

Credential-free, ALWAYS run (no ``adapter`` fixture, no BRIDGE_TEST_GCP gate, no
google.oauth2 client built): an injected ``id_token_fetcher`` + ``sender`` exercise
the **transport-construction + host-reconciliation + token-minting half** — the
Gateway-host reconciliation (the adk >=2.7 same-origin re-verify outcome: match
passes, mismatch refuses loud — NO ``.mtls`` repoint), the workload OIDC ID token
minted with ``audience == the reconciled URL``, and the ``Authorization: Bearer``
header. The live authenticated wire send to a real counterparty is deferred
(S1-benefits-6) / gated on ``BRIDGE_TEST_GCP`` (a P7-9 input). Mirrors
``test_gcp_scheduler.py``.
"""

from __future__ import annotations

import dataclasses
import os
import sys

import pytest

from bridge.outbound import (
    A2AClient,
    Delivery,
    DialOut,
    FakePeerDirectory,
    Nudge,
    PeerCard,
)
from bridge.ports import Port, PortMode, get_adapter
from bridge.ports.gcp import (
    BRIDGE_GATEWAY_HOST_ENV,
    BRIDGE_RUNTIME_SA_EMAIL_ENV,
    GcpA2AClient,
    reconcile_card_host,
)
from contract.enums import LifecycleStatus
from contract.wire import ArtifactRef

_GATEWAY_HOST = "bridge-runtime-abc.a.run.app"
_CARD_URL = f"https://{_GATEWAY_HOST}/rpc"
_SA = "bridge-runtime@test-project.iam.gserviceaccount.com"


class _RecordingFetcher:
    """A fake OIDC ID-token fetcher: records each audience, returns a marker token
    (no google.oauth2, no credentials)."""

    def __init__(self) -> None:
        self.audiences: list[str] = []

    def __call__(self, audience: str) -> str:
        self.audiences.append(audience)
        return f"id-token::{audience}"


class _RecordingSender:
    """A fake HTTP sender: records the POST, never fires (mirrors the scheduler's fake
    Cloud Tasks client)."""

    def __init__(self) -> None:
        self.posts: list[dict] = []

    def __call__(self, *, url, headers, body) -> None:
        self.posts.append({"url": url, "headers": headers, "body": body})


def _build(**overrides):
    cfg = {"gateway_host": _GATEWAY_HOST, "sa_email": _SA}
    cfg.update(overrides)
    return get_adapter(Port.a2a_client, PortMode.gcp).build_client(**cfg)


# --- wired, not stubbed ----------------------------------------------------


def test_get_adapter_returns_gcp_a2a_client():
    """gcp a2a_client no longer raises NotImplementedError (P7-5)."""
    adapter = get_adapter(Port.a2a_client, PortMode.gcp)
    assert isinstance(adapter, GcpA2AClient)
    assert adapter.port is Port.a2a_client


def test_built_client_is_a2a_client_protocol():
    client = _build(id_token_fetcher=_RecordingFetcher(), sender=_RecordingSender())
    assert isinstance(client, A2AClient)


# --- refuse-without-config (the fake-transport-swallow trap analogue) -------


def test_build_refuses_without_gateway_host_env(monkeypatch):
    """No gateway_host arg + no BRIDGE_GATEWAY_HOST env → RuntimeError naming the env.
    The gcp a2a_client NEVER falls back to a silent fake transport (which would
    silently swallow outbound sends on the deployed path)."""
    for env in (BRIDGE_GATEWAY_HOST_ENV, BRIDGE_RUNTIME_SA_EMAIL_ENV):
        monkeypatch.delenv(env, raising=False)
    with pytest.raises(RuntimeError, match=BRIDGE_GATEWAY_HOST_ENV):
        get_adapter(Port.a2a_client, PortMode.gcp).build_client()


def test_build_resolves_config_from_env(monkeypatch):
    """With no args, build_client resolves gateway_host + sa_email from env."""
    monkeypatch.setenv(BRIDGE_GATEWAY_HOST_ENV, _GATEWAY_HOST)
    monkeypatch.setenv(BRIDGE_RUNTIME_SA_EMAIL_ENV, _SA)
    client = get_adapter(Port.a2a_client, PortMode.gcp).build_client(
        id_token_fetcher=_RecordingFetcher(), sender=_RecordingSender()
    )
    assert isinstance(client, A2AClient)


# --- reconcile_card_host: the adk >=2.7 same-origin re-verify outcome -------
# adk >=2.7 (verified 2.7.1) RemoteA2aAgent._validate_card_rpc_targets enforces that
# a network-fetched card may only aim RPC traffic at its own origin. The 2.3-era
# ``.mtls`` card-repoint hack (vibeflix-audit) would now HARD-FAIL, so it is NOT
# ported: our OutboundClient reconciles explicitly and refuses loud on a mismatch.


def test_reconcile_matching_host_passes():
    assert reconcile_card_host(_CARD_URL, _GATEWAY_HOST) == _CARD_URL
    # A bare-host / host:port Gateway value also reconciles on origin.
    assert (
        reconcile_card_host("https://h.example/rpc", "h.example")
        == "https://h.example/rpc"
    )
    assert (
        reconcile_card_host("https://h.example:8443/rpc", "h.example:8443")
        == "https://h.example:8443/rpc"
    )


def test_reconcile_mismatched_host_refuses_loud():
    with pytest.raises(ValueError, match=r"evil\.example") as exc:
        reconcile_card_host("https://evil.example/rpc", _GATEWAY_HOST)
    # Names BOTH hosts (refuse-loud), never a silent repoint.
    assert _GATEWAY_HOST in str(exc.value)


# --- the Agent-Identity transport: OIDC audience + Bearer header ------------


def test_transport_mints_oidc_id_token_with_authorized_audience():
    """A dial mints an OIDC ID token whose audience == the reconciled URL (Agent
    Identity workload auth) and POSTs it as an Authorization: Bearer header — never a
    bearer token on the message."""
    fetcher = _RecordingFetcher()
    sender = _RecordingSender()
    client = _build(id_token_fetcher=fetcher, sender=sender)

    directory = FakePeerDirectory(
        {_CARD_URL: PeerCard("carrier-a", _CARD_URL, ("benefits-procurement",))}
    )
    dialout = DialOut(
        context_id="ctx-a",
        party="carrier-a",
        process="benefits-procurement",
        card_url=_CARD_URL,
        message="request benefits-quote",
    )
    receipt = client.dial(dialout, directory=directory)
    assert receipt.resent is False

    # Audience is the reconciled URL (the authorized origin), not the SA / not a party.
    assert fetcher.audiences == [_CARD_URL]
    # The token rode the transport header, minted per-call.
    assert len(sender.posts) == 1
    assert sender.posts[0]["url"] == _CARD_URL
    assert (
        sender.posts[0]["headers"]["Authorization"] == f"Bearer id-token::{_CARD_URL}"
    )


def test_dial_to_mismatched_card_host_refuses_loud():
    """A peer card advertising a host != the Gateway-authorized host is refused loud
    at send time (no .mtls repoint) — nothing minted, nothing posted."""
    fetcher = _RecordingFetcher()
    sender = _RecordingSender()
    client = _build(id_token_fetcher=fetcher, sender=sender)

    bad_url = "https://evil.example/rpc"
    directory = FakePeerDirectory(
        {bad_url: PeerCard("evil", bad_url, ("benefits-procurement",))}
    )
    dialout = DialOut(
        context_id="ctx-b",
        party="evil",
        process="benefits-procurement",
        card_url=bad_url,
        message="request benefits-quote",
    )
    with pytest.raises(ValueError, match=r"evil\.example"):
        client.dial(dialout, directory=directory)
    assert fetcher.audiences == []
    assert sender.posts == []


def test_outbound_carries_no_bearer_token_field():
    """No outbound message dataclass carries a token/credential field — the OIDC token
    is a per-call transport header, never a message field (mirrors
    test_nudge_carries_no_bearer_token / test_schedule_carries_no_bearer_token)."""
    for msg_cls in (Nudge, DialOut, Delivery):
        field_names = {f.name for f in dataclasses.fields(msg_cls)}
        assert not (field_names & {"token", "bearer", "authorization", "credential"}), (
            f"{msg_cls.__name__} must carry no bearer/credential field"
        )


# --- lazy import: pkg import pulls no OIDC-minting deps ---------------------


def test_importing_gcp_pkg_does_not_import_google_oauth2():
    """The transport lazy-imports google.oauth2 / google.auth.transport.requests
    inside deliver(); importing the package must not pull them (mirrors the asyncpg /
    cloud-tasks / storage lazy-import invariants). NOTE: httpx and the base google.auth
    are deliberately NOT asserted absent — the parent ``bridge`` package already pulls
    them via its a2a-sdk / google-adk deps, not this adapter's module body; the
    adapter's OWN OIDC-minting deps (google.oauth2.id_token + the auth request
    transport) are the ones that must stay lazy."""
    import bridge.ports.gcp  # noqa: F401

    assert "bridge.ports.gcp" in sys.modules
    assert "google.oauth2" not in sys.modules
    assert "google.auth.transport.requests" not in sys.modules


# --- live send, gated (a P7-9 / S1-benefits-6 input) ------------------------


def test_gcp_a2a_client_live_send():
    """Against a provisioned Gateway + a real counterparty (BRIDGE_TEST_GCP=1 + the
    gateway-host/SA envs): build the client with the REAL id-token fetcher + httpx
    sender and dial a live peer. Deferred to S1-benefits-6 (real carriers) / P7-9
    (provisioned run); skipped otherwise (mirror test_gcp_scheduler_live_roundtrip)."""
    if os.environ.get("BRIDGE_TEST_GCP") != "1":
        pytest.skip(
            "set BRIDGE_TEST_GCP=1 to run the live Agent-Identity dial "
            "(S1-benefits-6 real carriers / P7-9 provisioned run)"
        )
    gateway_host = os.environ.get(BRIDGE_GATEWAY_HOST_ENV, "").strip()
    sa = os.environ.get(BRIDGE_RUNTIME_SA_EMAIL_ENV, "").strip()
    if not (gateway_host and sa):
        pytest.skip(
            "a provisioned Gateway host + runtime SA email (the two envs) are a P7-9 "
            "input for the live Agent-Identity dial"
        )
    # The real fetcher/sender path (google.oauth2 + httpx) is exercised only here.
    client = get_adapter(Port.a2a_client, PortMode.gcp).build_client()
    card_url = f"https://{gateway_host}/rpc"
    directory = FakePeerDirectory(
        {card_url: PeerCard("peer", card_url, ("benefits-procurement",))}
    )
    dialout = DialOut(
        context_id="live-dial-test",
        party="peer",
        process="benefits-procurement",
        card_url=card_url,
        message="request benefits-quote",
    )
    receipt = client.dial(dialout, directory=directory)
    assert receipt.resent is False
    # Deliver a by-reference artifact too (the party-channel live half).
    delivery = Delivery(
        context_id="live-dial-test",
        recipient="peer",
        artifact=ArtifactRef(uri="bridge://comparison/live"),
    )
    assert client.deliver(delivery).resent is False
    # A nudge is the party-channel chase (deferred live half — no-op transport).
    nudge = Nudge(
        context_id="live-dial-test",
        party="peer",
        step=LifecycleStatus.overdue,
        channel="email",
        message="hi",
    )
    assert client.send(nudge).resent is False
