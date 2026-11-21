"""Zone 1–4 Intake, BigQuery Open Iceberg CDC Sync, and Interactive Demo Console routes."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from bridge.agent_gym import AgentGymEngine
from bridge.identity import authorize_entity_access
from bridge.knowledge_catalog import CatalogRole, KnowledgeCatalog
from bridge.operational_store import OperationalStore
from bridge.showcase.loader import demo_static_root
from bridge.telemetry.outcome import (
    close_outcome,
    current_outcome_id,
    start_outcome,
)
from bridge.user_interaction.demo_session import (
    issue_viewer_cookie,
    mutating_auth_dependency,
    read_auth_dependency,
    verified_mutating_identity,
)
from bridge.user_interaction.schemas import IngestSubmissionRequest
from bridge.user_interaction.surfaces import ingest_accepted_submission
from contract.enums import Disposition, Gate
from contract.wire import LedgerEntry


def _serve_safe_demo_asset(
    subdir: str,
    filename: str,
    *,
    allowed_types: dict[str, str],
    cache_control: str | None = None,
) -> Response:
    if (
        not filename
        or "\x00" in filename
        or "\\" in filename
        or filename.startswith("/")
    ):
        raise HTTPException(status_code=400, detail="Invalid static asset path.")
    rel = Path(filename)
    if ".." in rel.parts:
        raise HTTPException(status_code=400, detail="Invalid static asset path.")
    base = (demo_static_root() / subdir).resolve()
    target = (base / rel).resolve()
    if not target.is_relative_to(base) or target == base:
        raise HTTPException(status_code=400, detail="Invalid static asset path.")
    media_type = allowed_types.get(target.suffix.lower())
    if media_type is None:
        raise HTTPException(status_code=404, detail="Unsupported static asset.")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Static asset not found.")
    headers = {"Cache-Control": cache_control} if cache_control else None
    return Response(
        content=target.read_bytes(),
        media_type=media_type,
        headers=headers,
    )


def _register_demo_static_routes(router: APIRouter) -> None:
    """Register `/demo/fonts/*` and `/demo/modules/*` static asset routes."""

    @router.get("/demo/fonts/{filename:path}")
    def get_demo_font_asset(filename: str) -> Response:
        """Serve bundled offline font CSS and WOFF2 assets with strict path containment."""
        is_binary_font = filename.lower().endswith((".woff2", ".woff"))
        return _serve_safe_demo_asset(
            "fonts",
            filename,
            allowed_types={
                ".css": "text/css; charset=utf-8",
                ".woff2": "font/woff2",
                ".woff": "font/woff",
            },
            cache_control=(
                "public, max-age=31536000, immutable" if is_binary_font else None
            ),
        )

    @router.get("/demo/modules/{filename:path}")
    def get_demo_module_asset(filename: str) -> Response:
        """Serve modular Enterprise Showcase ES module scripts with strict path containment."""
        return _serve_safe_demo_asset(
            "modules",
            filename,
            allowed_types={
                ".js": "application/javascript; charset=utf-8",
                ".mjs": "application/javascript; charset=utf-8",
            },
        )


def _register_demo_console_routes(router: APIRouter) -> None:
    @router.api_route("/demo", methods=["GET", "HEAD"], response_class=HTMLResponse)
    @router.api_route("/console", methods=["GET", "HEAD"], response_class=HTMLResponse)
    def get_interactive_enterprise_console(
        request: Request,
        domain: str | None = None,
    ) -> HTMLResponse:
        """Serve the 5-scene interactive Enterprise Showcase HTML console (/demo and /console)."""
        demo_path = demo_static_root() / "enterprise-demo.html"
        if not demo_path.is_file():
            raise HTTPException(
                status_code=404,
                detail="Interactive console (enterprise-demo.html) not found.",
            )
        html_text = demo_path.read_text(encoding="utf-8")
        chosen_domain = (
            (domain or os.environ.get("BRIDGE_DEFAULT_DEMO_DOMAIN") or "")
            .strip()
            .lower()
        )
        if chosen_domain in ("banking", "mortgage", "insurance"):
            inject_script = (
                f'<script>window.DEFAULT_DEMO_DOMAIN = "{chosen_domain}";</script>\n'
            )
            if "</head>" in html_text:
                html_text = html_text.replace("</head>", f"{inject_script}</head>", 1)
            else:
                html_text = inject_script + html_text
        resp = HTMLResponse(content=html_text)
        issue_viewer_cookie(request, resp)
        return resp

    @router.get("/demo/enterprise-demo.css")
    def get_interactive_enterprise_console_css() -> Response:
        """Serve the modular stylesheet for the Enterprise Showcase console."""
        css_path = demo_static_root() / "enterprise-demo.css"
        if not css_path.is_file():
            raise HTTPException(
                status_code=404, detail="Stylesheet (enterprise-demo.css) not found."
            )
        return Response(
            content=css_path.read_text(encoding="utf-8"),
            media_type="text/css; charset=utf-8",
        )

    @router.get("/demo/enterprise-demo.js")
    def get_interactive_enterprise_console_js() -> Response:
        """Serve the modular JavaScript controller for the Enterprise Showcase console."""
        js_path = demo_static_root() / "enterprise-demo.js"
        if not js_path.is_file():
            raise HTTPException(
                status_code=404, detail="Script (enterprise-demo.js) not found."
            )
        return Response(
            content=js_path.read_text(encoding="utf-8"),
            media_type="application/javascript; charset=utf-8",
        )

    _register_demo_static_routes(router)


def register_intake_and_demo_routes(  # noqa: PLR0913 - public FastAPI route registrar
    router: APIRouter,
    *,
    store: OperationalStore,
    catalog: KnowledgeCatalog,
    gym: AgentGymEngine,
    principal_roles: dict[str, CatalogRole] | None,
    require_auth: bool,
) -> None:
    """Register intake, BigQuery CDC sync, and interactive demo routes onto ``router``."""

    ingest_auth = mutating_auth_dependency(
        default_role="underwriter",
        principal_roles=principal_roles,
        require_auth=require_auth,
        allow_external_roles=True,
    )

    @router.post("/v1/intake/ingest", dependencies=[ingest_auth])
    def ingest_submission_endpoint(
        request: Request,
        body: IngestSubmissionRequest,
        role: CatalogRole | None = None,
    ) -> dict[str, Any]:
        """Ingest a document submission across WORM Vault, Operational Store, Knowledge Catalog, and Agent Gym."""
        del role
        compound = verified_mutating_identity(request)
        try:
            authorize_entity_access(compound.user, body.entity_id)
            outcome_id = current_outcome_id() or start_outcome(
                owner=compound.user.subject
            )
            rec_at = body.recorded_at or datetime.now(UTC)
            eff_from = body.effective_from or rec_at
            initial_disp = (
                Disposition.pending
                if body.status in ("parked", "pending")
                else Disposition(body.status)
            )
            initial_gate = (
                Gate.hitl_review
                if initial_disp == Disposition.pending
                else Gate.auto_approve
            )
            entry = LedgerEntry(
                doc_id=body.doc_id,
                doctype=body.doctype,
                issuer=body.issuer,
                disposition=initial_disp,
                gate=initial_gate,
                overall_confidence=body.confidence,
                message=body.summary,
                key_fields={k: str(v) for k, v in body.key_fields.items()},
                source_anchors=body.source_anchors,
                recorded_at=rec_at,
                effective_from=eff_from,
            )
            res = ingest_accepted_submission(
                entry,
                entity_id=body.entity_id,
                context_id=body.context_id,
                raw_document_bytes=f"%PDF-1.7 {body.doc_id} {body.doctype}".encode(),
                filename=body.filename or f"{body.doc_id}.pdf",
                version=body.version,
                source_mode="CUSTOM_WEB_PORTAL",
                operational_store=store,
                knowledge_catalog=catalog,
                agent_gym=gym,
                recorded_at=rec_at,
                effective_from=eff_from,
            )
            if res.entry.disposition is Disposition.accepted:
                close_outcome(
                    "succeeded",
                    subject=body.entity_id,
                    outcome_id=outcome_id,
                )
            elif res.entry.disposition is Disposition.rejected:
                close_outcome(
                    "failed",
                    subject=body.entity_id,
                    outcome_id=outcome_id,
                )
            return {
                "outcome_id": outcome_id,
                "disposition": res.entry.disposition.value,
                "redacted_fields": dict(res.entry.key_fields),
                "applied_extraction_hints": list(res.applied_extraction_hints),
                "entry": res.entry.model_dump(mode="json"),
                "vault_record": res.vault_record.model_dump(mode="json"),
                "facts": [f.model_dump(mode="json") for f in res.facts],
                "conflicts": [c.model_dump(mode="json") for c in res.conflicts],
                "lineage_traces": [
                    t.model_dump(mode="json") for t in res.lineage_traces
                ],
            }
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    sync_read_deps = [
        read_auth_dependency(
            default_role="broker",
            principal_roles=principal_roles,
            require_auth=require_auth,
            allow_external_roles=False,
        )
    ]

    @router.get("/v1/bigquery/sync", dependencies=sync_read_deps)
    def get_bigquery_iceberg_sync_endpoint(
        entity_id: str | None = None,
        since_recorded_at: datetime | None = None,
    ) -> dict[str, Any]:
        """Export an incremental watermarked BigQuery Open Iceberg CDC sync batch."""
        batch = store.export_bigquery_sync_batch(
            entity_id=entity_id,
            since_recorded_at=since_recorded_at,
        )
        return batch.model_dump(mode="json")

    _register_demo_console_routes(router)
