#!/usr/bin/env python3
"""Render declarative `scenarios/<id>/pdf/*.yaml` assets in memory and upload to Cloud Storage.

Uploads `<id>/docs/*.pdf` and `<id>/scenario.yaml` (stamped with each PDF's `gcs_uri` and
`sha256`, with local `spec:` keys stripped) to `gs://<bucket>/[<prefix>/]<id>/`, and
optionally triggers `<2s` hot-reload via `POST <service_url>/v1/demo/scenarios/reload`.
Zero PDF files are written to local disk (`Decision 18`).
"""

from __future__ import annotations

import argparse
import copy
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bridge.showcase.gcs_scenarios import parse_gcs_uri  # noqa: E402
from bridge.showcase.loader import SCENARIOS_DIR, load_manifest  # noqa: E402
from bridge.showcase.pdf_assets import render_pdf_asset  # noqa: E402


def _gcp_credentials() -> Any:
    """Resolve ADC first; fall back to `gcloud auth print-access-token` when ADC is absent."""
    import subprocess

    import google.auth
    from google.auth.transport.requests import Request as GoogleAuthRequest
    from google.oauth2.credentials import Credentials

    try:
        creds, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        creds.refresh(GoogleAuthRequest())
        return creds
    except Exception:
        tok = subprocess.check_output(
            ["gcloud", "auth", "print-access-token"], text=True
        ).strip()
        return Credentials(token=tok)


def push_scenario(
    scenario_id: str,
    *,
    bucket_uri: str,
    scenarios_root: Path = SCENARIOS_DIR,
    reload_url: str | None = None,
    storage_client: Any = None,
    project_id: str | None = None,
) -> dict[str, Any]:
    """Render one scenario's PDFs in memory, upload to GCS, and optionally reload."""
    folder = scenarios_root / scenario_id
    manifest_path = folder / "scenario.yaml"
    manifest = load_manifest(manifest_path)
    raw_doc: dict[str, Any] = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))

    bucket_name, root_prefix = parse_gcs_uri(bucket_uri)
    prefix = f"{root_prefix.rstrip('/')}/" if root_prefix.strip("/") else ""

    if storage_client is None:
        from google.cloud import storage

        storage_client = storage.Client(
            project=project_id, credentials=_gcp_credentials()
        )
    bucket = storage_client.bucket(bucket_name)

    uploaded_docs: list[dict[str, Any]] = []
    rendered_by_id = {
        a.asset_id: render_pdf_asset(a)
        for a in (manifest.stage.pdf_assets if manifest.stage else ())
    }

    out_doc = copy.deepcopy(raw_doc)
    stage_doc = out_doc.get("stage")
    if isinstance(stage_doc, dict):
        for asset_dict in stage_doc.get("pdf_assets") or []:
            asset_dict.pop("spec", None)
            aid = str(asset_dict["asset_id"])
            rendered = rendered_by_id[aid]
            obj_key = f"{prefix}{scenario_id}/docs/{rendered.filename}"
            gcs_uri = f"gs://{bucket_name}/{obj_key}"
            blob = bucket.blob(obj_key)
            blob.upload_from_string(rendered.pdf_bytes, content_type="application/pdf")
            asset_dict.pop("gcs_uri", None)
            asset_dict["sha256"] = rendered.sha256
            asset_dict["document_title"] = rendered.asset.document_title
            uploaded_docs.append(
                {
                    "asset_id": aid,
                    "filename": rendered.filename,
                    "gcs_uri": gcs_uri,
                    "sha256": rendered.sha256,
                    "size_bytes": len(rendered.pdf_bytes),
                }
            )

    manifest_key = f"{prefix}{scenario_id}/scenario.yaml"
    manifest_yaml = yaml.safe_dump(out_doc, sort_keys=False, allow_unicode=True)
    bucket.blob(manifest_key).upload_from_string(
        manifest_yaml.encode("utf-8"), content_type="application/x-yaml"
    )

    reload_ms: int | None = None
    if reload_url:
        t0 = time.monotonic()
        target = f"{reload_url.rstrip('/')}/v1/demo/scenarios/reload"
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(target)
            resp.raise_for_status()
        reload_ms = int((time.monotonic() - t0) * 1000)

    return {
        "scenario_id": scenario_id,
        "manifest_gcs_uri": f"gs://{bucket_name}/{manifest_key}",
        "uploaded_pdfs": uploaded_docs,
        "reload_ms": reload_ms,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "scenario_ids",
        nargs="*",
        help="Scenario IDs under scenarios/ to push (default: all discovered scenarios).",
    )
    parser.add_argument(
        "--bucket",
        required=True,
        help="Destination Cloud Storage bucket or gs:// prefix (e.g. gs://btv-scratch-01-bridge-scenarios/).",
    )
    parser.add_argument(
        "--reload-url",
        default="",
        help="Optional live bridge-meridian base URL to POST /v1/demo/scenarios/reload.",
    )
    parser.add_argument(
        "--project",
        default=None,
        help="Optional GCP project ID for Cloud Storage client.",
    )
    args = parser.parse_args(argv)

    bucket_uri = (
        args.bucket if args.bucket.startswith("gs://") else f"gs://{args.bucket}"
    )
    targets = args.scenario_ids or sorted(
        p.parent.name for p in SCENARIOS_DIR.glob("*/scenario.yaml")
    )
    for idx, sid in enumerate(targets):
        trigger_reload = (
            args.reload_url if idx == len(targets) - 1 and args.reload_url else None
        )
        summary = push_scenario(
            sid,
            bucket_uri=bucket_uri,
            reload_url=trigger_reload,
            project_id=args.project,
        )
        print(yaml.safe_dump(summary, sort_keys=False).strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
