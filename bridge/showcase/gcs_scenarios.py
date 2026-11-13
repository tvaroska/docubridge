"""Cloud Storage scenario manifest and PDF byte loader (`Decision 18`).

When `BRIDGE_SCENARIOS_DIR` is a `gs://<bucket>[/<prefix>]` URI, scenario manifests
(`<id>/scenario.yaml`) and PDF assets (`<id>/docs/*.pdf`) are discovered and fetched
from Cloud Storage with an injectable storage client so unit tests run offline.
"""

from __future__ import annotations

import hashlib
from typing import Any

import yaml
from pydantic import ValidationError

from contract.scenario import ScenarioManifest


def parse_gcs_uri(uri: str) -> tuple[str, str]:
    """Split `gs://bucket[/object]` into `(bucket, object_prefix)`."""
    cleaned = uri.strip()
    if not cleaned.startswith("gs://"):
        raise ValueError(f"Expected gs:// URI, got {uri!r}")
    rest = cleaned[len("gs://") :]
    bucket, _, key = rest.partition("/")
    if not bucket:
        raise ValueError(f"Missing bucket name in {uri!r}")
    return bucket, key.lstrip("/")


def _default_storage_client() -> Any:
    from google.cloud import storage

    return storage.Client()


def fetch_gcs_bytes(
    gcs_uri: str,
    *,
    expected_sha256: str | None = None,
    storage_client: Any = None,
) -> bytes:
    """Download object bytes from `gcs_uri` and optionally verify SHA-256."""
    bucket_name, blob_name = parse_gcs_uri(gcs_uri)
    if not blob_name:
        raise ValueError(f"Missing object path in {gcs_uri!r}")
    client = storage_client or _default_storage_client()
    blob = client.bucket(bucket_name).blob(blob_name)
    raw: bytes = blob.download_as_bytes()
    if expected_sha256:
        actual = hashlib.sha256(raw).hexdigest()
        if actual.lower() != expected_sha256.lower():
            raise ValueError(
                f"SHA-256 mismatch for {gcs_uri}: expected {expected_sha256}, got {actual}"
            )
    return raw


def discover_gcs_manifests(
    gcs_root_uri: str,
    *,
    manifest_filename: str = "scenario.yaml",
    storage_client: Any = None,
) -> tuple[ScenarioManifest, ...]:
    """Discover and validate all `<scenario_id>/scenario.yaml` objects under `gcs_root_uri`."""
    bucket_name, root_prefix = parse_gcs_uri(gcs_root_uri)
    prefix = f"{root_prefix.rstrip('/')}/" if root_prefix.strip("/") else ""
    client = storage_client or _default_storage_client()
    bucket = client.bucket(bucket_name)
    blobs = list(client.list_blobs(bucket, prefix=prefix))

    manifests: list[ScenarioManifest] = []
    suffix = f"/{manifest_filename}"
    for blob in sorted(blobs, key=lambda b: str(b.name)):
        name = str(blob.name)
        if not name.endswith(suffix):
            continue
        rel = name[len(prefix) :]
        parts = rel.split("/")
        if len(parts) != 2 or parts[1] != manifest_filename:
            continue
        folder_id = parts[0]
        raw_yaml = blob.download_as_bytes().decode("utf-8")
        doc = yaml.safe_load(raw_yaml)
        if not isinstance(doc, dict):
            raise ValueError(f"gs://{bucket_name}/{name}: top level must be a mapping")
        stage = doc.get("stage")
        if isinstance(stage, dict):
            for asset in stage.get("pdf_assets") or []:
                asset.pop("spec", None)
                asset.setdefault("document_title", asset.get("title", ""))
                if not asset.get("gcs_uri") and not asset.get("pages"):
                    doc_key = f"{prefix}{folder_id}/docs/{asset['filename']}"
                    asset["gcs_uri"] = f"gs://{bucket_name}/{doc_key}"
        try:
            manifest = ScenarioManifest.model_validate(doc)
        except ValidationError as exc:
            raise ValueError(f"gs://{bucket_name}/{name}: {exc}") from exc
        if manifest.id != folder_id:
            raise ValueError(
                f"gs://{bucket_name}/{name}: manifest id {manifest.id!r} must equal {folder_id!r}"
            )
        manifests.append(manifest)

    ids = [m.id for m in manifests]
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate scenario ids under {gcs_root_uri}: {ids}")
    return tuple(sorted(manifests, key=lambda m: (m.order, m.id)))
