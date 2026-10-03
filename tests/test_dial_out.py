"""Dial-out + Deliver over the fake transport (S1-benefits-4, CUJ-3, OUT-3/4).

Proves the two new outbound activities on the a2a_client seam's local adapter: the Bridge
originating an A2A exchange (Agent-Card discovery GATE → message/send) and Delivering a
by-reference result outward. Both are idempotent / crash-safe and carry NO bearer token —
the same invariants the Nudge path enforces.
"""

from __future__ import annotations

import dataclasses

import pytest

from bridge.outbound import (
    Delivery,
    DialOut,
    FakePeerDirectory,
    FakeTransport,
    OutboundClient,
    PeerCard,
)
from contract.wire import ArtifactRef


def _directory() -> FakePeerDirectory:
    return FakePeerDirectory(
        {
            "card://carrier-a": PeerCard(
                "carrier-a", "card://carrier-a", ("benefits-procurement",)
            )
        }
    )


def _dialout(**overrides) -> DialOut:
    base = dict(
        context_id="ctx-a",
        party="carrier-a",
        process="benefits-procurement",
        card_url="card://carrier-a",
        message="request benefits-quote for program:acme-2026",
    )
    base.update(overrides)
    return DialOut(**base)


class TestDialOut:
    def test_dial_delivers_and_returns_receipt(self):
        t = FakeTransport()
        c = OutboundClient(t)
        dialout = _dialout()
        r = c.dial(dialout, directory=_directory())
        assert r.resent is False
        assert r.dialout is dialout
        assert t.outbox == [dialout]
        assert r.idempotency_key == "dial:ctx-a"

    def test_idempotent_redrive_no_double_send(self):
        """Crash-safe re-drive: dialing the same context twice never double-sends."""
        t = FakeTransport()
        c = OutboundClient(t)
        directory = _directory()
        r1 = c.dial(_dialout(), directory=directory)
        r2 = c.dial(_dialout(message="different text"), directory=directory)
        assert r1.resent is False
        assert r2.resent is True
        assert len(t.outbox) == 1  # no double delivery — keyed by context_id

    def test_discovery_gate_rejects_unadvertised_process(self):
        """The Agent-Card discovery gate: a peer that can't serve the process is rejected."""
        t = FakeTransport()
        c = OutboundClient(t)
        with pytest.raises(ValueError, match="does not advertise process"):
            c.dial(_dialout(process="rfp-intake"), directory=_directory())
        assert t.outbox == []  # nothing delivered on rejection

    def test_discovery_gate_rejects_undiscoverable_card(self):
        """A card_url with no discoverable card is rejected before any send."""
        t = FakeTransport()
        c = OutboundClient(t)
        with pytest.raises(ValueError, match="no Agent Card discoverable"):
            c.dial(_dialout(card_url="card://unknown"), directory=_directory())
        assert t.outbox == []

    def test_dialout_carries_no_bearer_token(self):
        """OUT-3: the DialOut dataclass has no token/credential field."""
        names = {f.name for f in dataclasses.fields(DialOut)}
        assert names == {"context_id", "party", "process", "card_url", "message"}
        assert not any(
            tok in n for n in names for tok in ("token", "cred", "bearer")
        )


class TestDeliver:
    def test_deliver_pushes_by_reference_artifact(self):
        t = FakeTransport()
        c = OutboundClient(t)
        ref = ArtifactRef(uri="bridge://comparison/acme-2026", mime_type="application/json")
        delivery = Delivery(
            context_id="acme-2026", recipient="broker", artifact=ref, summary="4-leg"
        )
        r = c.deliver(delivery)
        assert r.resent is False
        assert r.delivery is delivery
        assert r.delivery.artifact.uri == "bridge://comparison/acme-2026"
        assert t.outbox == [delivery]
        assert r.idempotency_key == "deliver:acme-2026:broker"

    def test_delivery_has_no_bytes_field(self):
        """ART discipline: the Delivery carries a by-reference handle, never inline bytes."""
        names = {f.name for f in dataclasses.fields(Delivery)}
        assert names == {"context_id", "recipient", "artifact", "channel", "summary"}
        assert not any("bytes" in n or "data" in n or "content" in n for n in names)
        # ArtifactRef itself is by-reference (uri handle) — no bytes anywhere.
        assert set(ArtifactRef.model_fields) == {"uri", "mime_type", "name"}

    def test_idempotent_redrive_no_double_send(self):
        t = FakeTransport()
        c = OutboundClient(t)
        ref = ArtifactRef(uri="bridge://comparison/acme-2026")
        d1 = c.deliver(Delivery(context_id="acme-2026", recipient="broker", artifact=ref))
        d2 = c.deliver(
            Delivery(context_id="acme-2026", recipient="broker", artifact=ref, summary="x")
        )
        assert d1.resent is False
        assert d2.resent is True
        assert len(t.outbox) == 1

    def test_distinct_recipients_distinct_sends(self):
        t = FakeTransport()
        c = OutboundClient(t)
        ref = ArtifactRef(uri="bridge://comparison/acme-2026")
        c.deliver(Delivery(context_id="acme-2026", recipient="broker", artifact=ref))
        c.deliver(Delivery(context_id="acme-2026", recipient="auditor", artifact=ref))
        assert len(t.outbox) == 2

    def test_delivery_carries_no_bearer_token(self):
        names = {f.name for f in dataclasses.fields(Delivery)}
        assert not any(
            tok in n for n in names for tok in ("token", "cred", "bearer")
        )
