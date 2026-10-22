"""Seam-parity test for the email_ingress seam (S0-arch-13).

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

from bridge.seams import EmailIngressSeam, Seam, SeamMode, get_adapter


@pytest.fixture
def seam(adapter, monkeypatch, tmp_path) -> EmailIngressSeam:
    if adapter is SeamMode.gcp:
        if not os.environ.get("BRIDGE_EMAIL_BUCKET", "").strip():
            pytest.skip("set BRIDGE_EMAIL_BUCKET=<provisioned mailbox bucket> for gcp")
    else:
        monkeypatch.setenv("BRIDGE_EMAIL_DIR", str(tmp_path))
    instance = get_adapter(Seam.email_ingress, adapter)
    assert instance.seam is Seam.email_ingress
    return instance


def _key() -> str:
    return uuid.uuid4().hex


@pytest.mark.seam("email_ingress")
def test_land_is_create_only_and_read_round_trips(seam):
    key = _key()
    raw = b"Message-ID: <x@y>\r\n\r\nbody\r\n"
    assert seam.land(key, raw) is True
    assert seam.land(key, b"different bytes") is False  # a duplicate, never overwritten
    assert seam.read(key) == raw
    with pytest.raises(KeyError):
        seam.read(_key())


@pytest.mark.seam("email_ingress")
def test_pending_is_landing_order_and_mark_processed_removes(seam):
    keys = [_key() for _ in range(3)]
    for key in keys:
        assert seam.land(key, key.encode())
        time.sleep(0.01)  # distinct landing timestamps
    pending = [k for k in seam.pending() if k in keys]
    assert pending == keys
    assert seam.processed(keys[1]) is None
    outcome = {"key": keys[1], "status": "filed", "filed": []}
    assert seam.mark_processed(keys[1], outcome) is True
    assert seam.mark_processed(keys[1], {"status": "other"}) is False
    assert seam.processed(keys[1]) == outcome
    assert [k for k in seam.pending() if k in keys] == [keys[0], keys[2]]


@pytest.mark.seam("email_ingress")
def test_put_attachment_is_idempotent_and_keeps_the_basename(seam, adapter):
    key = _key()
    seam.land(key, b"raw")
    uri = seam.put_attachment(key, 0, "quote.pdf", b"%PDF-1", "application/pdf")
    again = seam.put_attachment(key, 0, "quote.pdf", b"%PDF-1", "application/pdf")
    assert uri == again
    assert uri.endswith(f"/attachments/{key}/0/quote.pdf")
    assert uri.split("://", 1)[0] == ("gs" if adapter is SeamMode.gcp else "file")
    if adapter is SeamMode.local:
        assert Path(uri.removeprefix("file://")).read_bytes() == b"%PDF-1"


def _validating_adapters(tmp_path):
    """Both adapters, credential-free: validation must fire before any I/O or client."""
    from bridge.seams.gcp import GcpEmailIngress
    from bridge.seams.local import LocalEmailIngress

    return [LocalEmailIngress(tmp_path), GcpEmailIngress("never-touched-bucket")]


@pytest.mark.parametrize("bad", ["", "../etc", "A" * 32, "0" * 31, "0" * 32 + "/x"])
def test_bad_key_refused_before_io(tmp_path, bad):
    for seam in _validating_adapters(tmp_path):
        for call in (
            lambda s=seam: s.land(bad, b"x"),
            lambda s=seam: s.read(bad),
            lambda s=seam: s.processed(bad),
            lambda s=seam: s.mark_processed(bad, {}),
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
    for seam in _validating_adapters(tmp_path):
        with pytest.raises(ValueError):
            seam.put_attachment(_key(), index, filename, b"x", "application/pdf")
    assert not any(tmp_path.iterdir())


def test_local_needs_a_dir_at_first_use_not_at_construction(monkeypatch):
    monkeypatch.delenv("BRIDGE_EMAIL_DIR", raising=False)
    seam = get_adapter(Seam.email_ingress, SeamMode.local)  # constructs fine
    with pytest.raises(RuntimeError, match="BRIDGE_EMAIL_DIR"):
        seam.pending()


def test_local_pending_skips_stray_files(monkeypatch, tmp_path):
    monkeypatch.setenv("BRIDGE_EMAIL_DIR", str(tmp_path))
    seam = get_adapter(Seam.email_ingress, SeamMode.local)
    key = _key()
    seam.land(key, b"raw")
    (tmp_path / "inbox" / "notes.txt").write_text("stray")
    (tmp_path / "inbox" / ".landing-abc").write_text("torn write")
    assert seam.pending() == [key]
