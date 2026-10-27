"""Port-parity test for the email_ingress port (S0-arch-13).

The local adapter is a landing directory (``BRIDGE_EMAIL_DIR``); the gcp adapter is
the SAME object layout over a GCS bucket (``BRIDGE_EMAIL_BUCKET``), create-only via
``if_generation_match=0``. The gcp param runs under ``BRIDGE_TEST_GCP=1`` (the
``adapter`` fixture) AND a provisioned ``BRIDGE_EMAIL_BUCKET`` — skipped without it.
Keys are fresh uuids so a shared live bucket never collides. Parity is the terminal
outcome (land/duplicate, pending order, round-tripped bytes), never object metadata.
The credential-free gcp surface (412/403, no-bucket refusal) is in
``tests/test_gcp_email_ingress.py``.
"""

from __future__ import annotations

import os
import time
import uuid
from pathlib import Path

import pytest

from bridge.ports import EmailIngressPort, Port, PortMode, get_adapter


@pytest.fixture
def port(adapter, monkeypatch, tmp_path) -> EmailIngressPort:
    if adapter is PortMode.gcp:
        if not os.environ.get("BRIDGE_EMAIL_BUCKET", "").strip():
            pytest.skip("set BRIDGE_EMAIL_BUCKET=<provisioned mailbox bucket> for gcp")
    else:
        monkeypatch.setenv("BRIDGE_EMAIL_DIR", str(tmp_path))
    instance = get_adapter(Port.email_ingress, adapter)
    assert instance.port is Port.email_ingress
    return instance


def _key() -> str:
    return uuid.uuid4().hex


@pytest.mark.port("email_ingress")
def test_land_is_create_only_and_read_round_trips(port):
    key = _key()
    raw = b"Message-ID: <x@y>\r\n\r\nbody\r\n"
    assert port.land(key, raw) is True
    assert port.land(key, b"different bytes") is False  # a duplicate, never overwritten
    assert port.read(key) == raw
    with pytest.raises(KeyError):
        port.read(_key())


@pytest.mark.port("email_ingress")
def test_pending_is_landing_order_and_mark_processed_removes(port):
    keys = [_key() for _ in range(3)]
    for key in keys:
        assert port.land(key, key.encode())
        time.sleep(0.01)  # distinct landing timestamps
    pending = [k for k in port.pending() if k in keys]
    assert pending == keys
    assert port.processed(keys[1]) is None
    outcome = {"key": keys[1], "status": "filed", "filed": []}
    assert port.mark_processed(keys[1], outcome) is True
    assert port.mark_processed(keys[1], {"status": "other"}) is False
    assert port.processed(keys[1]) == outcome
    assert [k for k in port.pending() if k in keys] == [keys[0], keys[2]]


@pytest.mark.port("email_ingress")
def test_put_attachment_is_idempotent_and_keeps_the_basename(port, adapter):
    key = _key()
    port.land(key, b"raw")
    uri = port.put_attachment(key, 0, "quote.pdf", b"%PDF-1", "application/pdf")
    again = port.put_attachment(key, 0, "quote.pdf", b"%PDF-1", "application/pdf")
    assert uri == again
    assert uri.endswith(f"/attachments/{key}/0/quote.pdf")
    assert uri.split("://", 1)[0] == ("gs" if adapter is PortMode.gcp else "file")
    if adapter is PortMode.local:
        assert Path(uri.removeprefix("file://")).read_bytes() == b"%PDF-1"


def _validating_adapters(tmp_path):
    """Both adapters, credential-free: validation must fire before any I/O or client."""
    from bridge.ports.gcp import GcpEmailIngress
    from bridge.ports.local import LocalEmailIngress

    return [LocalEmailIngress(tmp_path), GcpEmailIngress("never-touched-bucket")]


@pytest.mark.parametrize("bad", ["", "../etc", "A" * 32, "0" * 31, "0" * 32 + "/x"])
def test_bad_key_refused_before_io(tmp_path, bad):
    for port in _validating_adapters(tmp_path):
        for call in (
            lambda s=port: s.land(bad, b"x"),
            lambda s=port: s.read(bad),
            lambda s=port: s.processed(bad),
            lambda s=port: s.mark_processed(bad, {}),
        ):
            with pytest.raises(ValueError):
                call()
    assert not any(tmp_path.iterdir())


@pytest.mark.parametrize(
    ("index", "filename"),
    [
        (0, "../x.pdf"),
        (0, "a/b.pdf"),
        (0, "a\\b.pdf"),
        (0, ".hidden"),
        (0, ""),
        (0, "q?.pdf"),
        (0, "q#.pdf"),
        (0, "x" * 129),
        (-1, "q.pdf"),
        (True, "q.pdf"),
    ],
)
def test_bad_attachment_slot_refused_before_io(tmp_path, index, filename):
    for port in _validating_adapters(tmp_path):
        with pytest.raises(ValueError):
            port.put_attachment(_key(), index, filename, b"x", "application/pdf")
    assert not any(tmp_path.iterdir())


def test_local_needs_a_dir_at_first_use_not_at_construction(monkeypatch):
    monkeypatch.delenv("BRIDGE_EMAIL_DIR", raising=False)
    port = get_adapter(Port.email_ingress, PortMode.local)  # constructs fine
    with pytest.raises(RuntimeError, match="BRIDGE_EMAIL_DIR"):
        port.pending()


def test_local_pending_skips_stray_files(monkeypatch, tmp_path):
    monkeypatch.setenv("BRIDGE_EMAIL_DIR", str(tmp_path))
    port = get_adapter(Port.email_ingress, PortMode.local)
    key = _key()
    port.land(key, b"raw")
    (tmp_path / "inbox" / "notes.txt").write_text("stray")
    (tmp_path / "inbox" / ".landing-abc").write_text("torn write")
    assert port.pending() == [key]
