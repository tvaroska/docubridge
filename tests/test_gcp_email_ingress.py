"""GCP email_ingress adapter unit tests (S0-arch-13) — credential-free, always run.

An in-memory fake bucket (no google-cloud-storage installed) exercises the
create-only contract: ``if_generation_match=0`` → 412 is a duplicate; a 403 (an
objectCreator-only identity attempting an overwrite) is a duplicate only when the
object exists, else re-raised. The live roundtrip is the gcp param of
``tests/test_email_ingress_port.py``.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime, timedelta

import pytest
from google.api_core.exceptions import Forbidden, PreconditionFailed

from bridge.ports import Port, PortMode, get_adapter
from bridge.ports.gcp import EMAIL_BUCKET_ENV, GcpEmailIngress

KEY = "0123456789abcdef0123456789abcdef"


class _FakeBlob:
    def __init__(self, bucket: _FakeBucket, name: str) -> None:
        self._bucket = bucket
        self.name = name

    @property
    def time_created(self):
        return self._bucket.created[self.name]

    def upload_from_string(self, data, content_type=None, if_generation_match=None):
        self._bucket.uploads.append((self.name, content_type, if_generation_match))
        if self._bucket.forbid_writes:
            raise Forbidden("objectCreator may not overwrite")
        if if_generation_match == 0 and self.name in self._bucket.objects:
            raise PreconditionFailed("exists")
        self._bucket.objects[self.name] = data
        self._bucket.created[self.name] = self._bucket.tick()

    def exists(self) -> bool:
        return self.name in self._bucket.objects

    def download_as_bytes(self) -> bytes:
        return self._bucket.objects[self.name]


class _FakeBucket:
    def __init__(self, name: str = "mailbox-bucket") -> None:
        self.name = name
        self.objects: dict[str, bytes] = {}
        self.created: dict[str, datetime] = {}
        self.uploads: list[tuple] = []
        self.forbid_writes = False
        self._t = datetime(2026, 9, 30, tzinfo=UTC)

    def tick(self) -> datetime:
        self._t += timedelta(seconds=1)
        return self._t

    def blob(self, name: str) -> _FakeBlob:
        return _FakeBlob(self, name)

    def list_blobs(self, prefix: str | None = None):
        # GCS lists lexicographically, NOT by creation time.
        for name in sorted(self.objects):
            if prefix is None or name.startswith(prefix):
                yield _FakeBlob(self, name)


@pytest.fixture
def pair(monkeypatch) -> tuple[GcpEmailIngress, _FakeBucket]:
    port = GcpEmailIngress("mailbox-bucket")
    fake = _FakeBucket()
    monkeypatch.setattr(port, "_bucket", lambda: fake)
    return port, fake


def test_get_adapter_returns_gcp_email_ingress():
    adapter = get_adapter(Port.email_ingress, PortMode.gcp)
    assert isinstance(adapter, GcpEmailIngress)
    assert adapter.port is Port.email_ingress


def test_land_412_is_a_duplicate_and_writes_are_create_only(pair):
    port, fake = pair
    assert port.land(KEY, b"raw") is True
    assert port.land(KEY, b"other") is False
    assert fake.objects[f"inbox/{KEY}.eml"] == b"raw"
    assert {u[2] for u in fake.uploads} == {0}  # every write if_generation_match=0
    assert fake.uploads[0][1] == "message/rfc822"


def test_403_with_existing_object_is_a_duplicate(pair):
    port, fake = pair
    port.land(KEY, b"raw")
    fake.forbid_writes = True
    assert port.land(KEY, b"raw") is False


def test_403_without_the_object_is_re_raised(pair):
    port, fake = pair
    fake.forbid_writes = True
    with pytest.raises(Forbidden):
        port.land(KEY, b"raw")


def test_pending_orders_by_creation_not_by_name(pair):
    port, _ = pair
    later, earlier = "f" * 32, "0" * 32
    port.land(later, b"1")
    port.land(earlier, b"2")
    assert port.pending() == [later, earlier]
    port.mark_processed(later, {"status": "filed"})
    assert port.pending() == [earlier]
    assert port.processed(later) == {"status": "filed"}
    assert port.mark_processed(later, {"status": "again"}) is False


def test_read_and_attachment_uri_shape(pair):
    port, _fake = pair
    with pytest.raises(KeyError):
        port.read(KEY)
    port.land(KEY, b"raw")
    assert port.read(KEY) == b"raw"
    uri = port.put_attachment(KEY, 2, "quote.pdf", b"%PDF", "application/pdf")
    assert uri == f"gs://mailbox-bucket/attachments/{KEY}/2/quote.pdf"
    assert port.put_attachment(KEY, 2, "quote.pdf", b"%PDF", "application/pdf") == uri


def test_no_bucket_refuses_before_any_storage_import(monkeypatch):
    monkeypatch.delenv(EMAIL_BUCKET_ENV, raising=False)
    monkeypatch.setitem(sys.modules, "google.cloud.storage", None)  # import would fail
    with pytest.raises(RuntimeError, match=EMAIL_BUCKET_ENV):
        GcpEmailIngress().land(KEY, b"raw")


def test_importing_the_gcp_adapters_does_not_import_storage():
    code = (
        "import sys, bridge.ports.gcp; "
        "sys.exit(1 if 'google.cloud.storage' in sys.modules else 0)"
    )
    assert subprocess.run([sys.executable, "-c", code]).returncode == 0
