"""Zone 1–4 Intake, BigQuery Open Iceberg CDC Sync, and Interactive Demo Console routes."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from bridge.agent_gym import AgentGymEngine
from bridge.identity import authorize_entity_access
from bridge.knowledge_catalog import CatalogRole, KnowledgeCatalog
from bridge.operational_store import OperationalStore
from bridge.user_interaction.auth import (
    AuthenticationRequiredError,
    resolve_compound_identity,
)
from bridge.user_interaction.demo_seed import seed_demo_scenarios
from bridge.user_interaction.schemas import IngestSubmissionRequest
from bridge.user_interaction.surfaces import ingest_accepted_submission
from contract.enums import Disposition, Gate
from contract.wire import LedgerEntry


def register_intake_and_demo_routes(
    router: APIRouter,
    *,
    store: OperationalStore,
    catalog: KnowledgeCatalog,
    gym: AgentGymEngine,
    principal_roles: dict[str, CatalogRole] | None,
    require_auth: bool,
) -> None:
    """Register intake, BigQuery CDC sync, and interactive demo routes onto ``router``."""

    @router.post("/v1/intake/ingest")
    def ingest_submission_endpoint(
        request: Request,
        body: IngestSubmissionRequest,
        role: CatalogRole | None = None,
    ) -> dict[str, Any]:
        """Ingest a document submission across WORM Vault, Operational Store, Knowledge Catalog, and Agent Gym."""
        try:
            compound = resolve_compound_identity(
                request,
                query_role=role or "underwriter",
                principal_roles=principal_roles,
                require_auth=require_auth,
            )
            authorize_entity_access(compound.user, body.entity_id)
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
                operational_store=store,
                knowledge_catalog=catalog,
                agent_gym=gym,
                recorded_at=rec_at,
                effective_from=eff_from,
            )
            return {
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
        except AuthenticationRequiredError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    @router.get("/v1/bigquery/sync")
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

    @router.post("/v1/demo/seed")
    def seed_demo_scenarios_endpoint() -> dict[str, Any]:
        """Seed baseline bi-temporal facts and 6-hop lineage across Banking, Mortgage, and Insurance scenarios."""
        return seed_demo_scenarios(
            operational_store=store,
            knowledge_catalog=catalog,
            agent_gym=gym,
        )

    @router.get("/demo", response_class=HTMLResponse)
    @router.get("/console", response_class=HTMLResponse)
    def get_interactive_enterprise_console() -> HTMLResponse:
        """Serve the 5-scene interactive Enterprise Showcase HTML console (/demo and /console)."""
        demo_path = (
            Path(__file__).resolve().parents[2] / "demo" / "enterprise-demo.html"
        )
        if not demo_path.is_file():
            raise HTTPException(
                status_code=404,
                detail="Interactive console (demo/enterprise-demo.html) not found.",
            )
        return HTMLResponse(content=demo_path.read_text(encoding="utf-8"))

    @router.get("/demo/enterprise-demo.css")
    def get_interactive_enterprise_console_css() -> Response:
        """Serve the modular stylesheet for the Enterprise Showcase console."""
        css_path = Path(__file__).resolve().parents[2] / "demo" / "enterprise-demo.css"
        if not css_path.is_file():
            raise HTTPException(
                status_code=404,
                detail="Stylesheet (demo/enterprise-demo.css) not found.",
            )
        return Response(
            content=css_path.read_text(encoding="utf-8"),
            media_type="text/css; charset=utf-8",
        )

    @router.get("/demo/enterprise-demo.js")
    def get_interactive_enterprise_console_js() -> Response:
        """Serve the modular JavaScript controller for the Enterprise Showcase console."""
        js_path = Path(__file__).resolve().parents[2] / "demo" / "enterprise-demo.js"
        if not js_path.is_file():
            raise HTTPException(
                status_code=404,
                detail="Script (demo/enterprise-demo.js) not found.",
            )
        return Response(
            content=js_path.read_text(encoding="utf-8"),
            media_type="application/javascript; charset=utf-8",
        )
