"""An in-memory fake GCS bucket (no google-cloud-storage needed) — shared test double.

``FakeBucket`` is a dict-backed double of the slice of ``google.cloud.storage.Bucket`` the
gcp ``skill_registry`` adapter uses (``blob()`` + ``list_blobs(prefix=...)``); patch it in
as ``GcpSkillRegistry._bucket`` to exercise the gcp adapter credential-free.
``seed_from_dir`` uploads real skill folders into it. Test-only; never shipped in bridge/.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path


class FakeBlob:
    def __init__(self, store: dict[str, bytes], name: str) -> None:
        self._store = store
        self.name = name

    def upload_from_string(self, data) -> None:
        self._store[self.name] = data.encode() if isinstance(data, str) else data

    def download_to_filename(self, path) -> None:
        with open(path, "wb") as f:
            f.write(self._store[self.name])

    def exists(self) -> bool:
        return self.name in self._store

    def download_as_text(self) -> str:
        return self._store[self.name].decode()


class FakeBucket:
    """A dict-backed GCS bucket double: blob() + list_blobs(prefix=...)."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.built = 0  # counts _bucket() calls (proves no client for bad requests)

    def blob(self, name: str) -> FakeBlob:
        return FakeBlob(self.objects, name)

    def list_blobs(self, prefix: str | None = None):
        for name in sorted(self.objects):
            if prefix is None or name.startswith(prefix):
                yield FakeBlob(self.objects, name)


def seed_from_dir(bucket: FakeBucket, root: Path, names: Iterable[str]) -> None:
    """Upload every file under ``root/<name>/`` as the blob ``<name>/<rel>`` (bytes)."""
    for name in names:
        skill_dir = root / name
        for path in sorted(skill_dir.rglob("*")):
            if path.is_file():
                rel = path.relative_to(skill_dir).as_posix()
                bucket.blob(f"{name}/{rel}").upload_from_string(path.read_bytes())
