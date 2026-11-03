"""FastAPI route registration for Live Commercial Consolidation PDFs & `/ebc` Console."""

from __future__ import annotations

import base64
import copy
import os
import re
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, Response

from bridge.a2ui_inbox import ActionInboxStore
from bridge.agent_gym import AgentGymEngine
from bridge.knowledge_catalog import KnowledgeCatalog
from bridge.operational_store import OperationalStore
from bridge.user_interaction.consolidation_live import (
    _is_live_gemini_configured,
    build_initial_live_telemetry,
    get_custom_uploaded_pdf,
    process_uploaded_pdfs,
    store_custom_uploaded_pdf,
)
from bridge.user_interaction.consolidation_pdfs import (
    build_demo_pdfs_zip,
    list_demo_pdf_metadata,
    resolve_demo_pdf_by_name_or_id,
)
from bridge.user_interaction.schemas import (
    LiveCollabModeRequest,
    UploadConsolidationPdfRequest,
)

_SAFE_FILENAME_RE = re.compile(r"[^a-zA-Z0-9._-]+")


def register_live_ebc_and_pdf_routes(
    router: APIRouter,
    *,
    get_state_ref: Any,
    store: OperationalStore,
    catalog: KnowledgeCatalog,
    inbox: ActionInboxStore,
    gym: AgentGymEngine,
    execute_beat_1_fn: Any,
    execute_beat_2_fn: Any,
    execute_beat_3_fn: Any,
    execute_beat_4_fn: Any,
    recompute_counts_fn: Any,
) -> None:
    """Mount live PDF download/upload, waiver, Agent Gym compiler, and `/ebc` console routes."""
    demo_dir = Path(__file__).resolve().parents[2] / "demo"

    @router.get("/v1/demo/consolidation-collab/pdfs")
    def list_consolidation_pdfs_endpoint() -> dict[str, Any]:
        """List all five downloadable multi-page showcase PDFs."""
        return {
            "zip_url": "/v1/demo/consolidation-collab/pdfs.zip",
            "pdfs": list_demo_pdf_metadata(),
        }

    @router.get("/v1/demo/consolidation-collab/pdfs.zip")
    def download_consolidation_pdfs_zip_endpoint() -> Response:
        """Download a `.zip` bundle containing all five multi-page showcase PDFs."""
        return Response(
            content=build_demo_pdfs_zip(),
            media_type="application/zip",
            headers={
                "Content-Disposition": (
                    'attachment; filename="DocuBridge_Commercial_Consolidation_PDFs.zip"'
                )
            },
        )

    @router.get("/v1/demo/consolidation-collab/pdfs/{filename}")
    def get_consolidation_pdf_file_endpoint(filename: str) -> Response:
        """Serve a showcase or user-uploaded PDF inline for browser `#page=N` Proof viewing."""
        asset = resolve_demo_pdf_by_name_or_id(filename)
        if asset is not None:
            raw = asset.pdf_bytes
            served_name = asset.filename
        else:
            custom = get_custom_uploaded_pdf(filename)
            if custom is None:
                raise HTTPException(
                    status_code=404, detail=f"PDF {filename!r} not found."
                )
            raw = custom
            served_name = filename
        return Response(
            content=raw,
            media_type="application/pdf",
            headers={"Content-Disposition": f'inline; filename="{served_name}"'},
        )

    @router.post("/v1/demo/consolidation-collab/upload-pdf")
    def upload_consolidation_pdf_endpoint(
        body: UploadConsolidationPdfRequest,
    ) -> dict[str, Any]:
        """Accept dragged-and-dropped base64 PDFs or sample IDs and run live Gemini extraction."""
        resolved: list[tuple[str, bytes]] = []
        for sid in body.sample_ids:
            asset = resolve_demo_pdf_by_name_or_id(sid)
            if asset is None:
                raise HTTPException(
                    status_code=404, detail=f"Unknown sample PDF {sid!r}."
                )
            resolved.append((asset.filename, asset.pdf_bytes))

        for item in body.files:
            safe_name = (
                _SAFE_FILENAME_RE.sub("_", Path(item.filename).name) or "upload.pdf"
            )
            if not safe_name.lower().endswith(".pdf"):
                safe_name = f"{safe_name}.pdf"
            raw_b64 = item.content_base64.strip()
            if "," in raw_b64 and raw_b64.lower().startswith("data:"):
                raw_b64 = raw_b64.split(",", 1)[1]
            try:
                pdf_bytes = base64.b64decode(raw_b64, validate=False)
            except Exception as exc:
                raise HTTPException(
                    status_code=400, detail=f"Invalid base64 payload for {safe_name!r}."
                ) from exc
            if not pdf_bytes:
                raise HTTPException(
                    status_code=400, detail=f"Empty PDF payload for {safe_name!r}."
                )
            store_custom_uploaded_pdf(safe_name, pdf_bytes)
            resolved.append((safe_name, pdf_bytes))

        if not resolved:
            raise HTTPException(
                status_code=400,
                detail="Provide at least one file in `files` or `sample_ids`.",
            )

        state = get_state_ref()
        process_uploaded_pdfs(
            state,
            resolved_files=resolved,
            mode=body.mode,
            store=store,
            catalog=catalog,
            inbox=inbox,
            gym=gym,
            execute_beat_1_fn=execute_beat_1_fn,
            execute_beat_2_fn=execute_beat_2_fn,
            execute_beat_4_fn=execute_beat_4_fn,
            recompute_counts_fn=recompute_counts_fn,
        )
        return copy.deepcopy(state)

    @router.post("/v1/demo/consolidation-collab/approve-waiver")
    def approve_consolidation_waiver_endpoint(
        body: LiveCollabModeRequest,
    ) -> dict[str, Any]:
        """Execute Senior Underwriter 1-click SPIFFE OBO waiver approval (`RSR = 0.98`)."""
        t0 = time.monotonic()
        state = get_state_ref()
        if state.get("beat", 0) < 1:
            execute_beat_1_fn(state, store=store, catalog=catalog, gym=gym)
        if state.get("beat", 0) < 2:
            execute_beat_2_fn(state, store=store, catalog=catalog, gym=gym)
        execute_beat_3_fn(state, inbox=inbox)
        telem = state.setdefault("live_telemetry", build_initial_live_telemetry())
        telem["execution_mode"] = (
            "live_gcp" if body.mode == "live" else "instant_cached"
        )
        telem["last_action_latency_ms"] = max(12, int((time.monotonic() - t0) * 1000))
        return copy.deepcopy(state)

    @router.post("/v1/demo/consolidation-collab/distill-skill")
    def distill_consolidation_skill_endpoint(
        body: LiveCollabModeRequest,
    ) -> dict[str, Any]:
        """Distill the cross-doc wire waiver into `consolidation-payoff@1.1.0` via `gemini-4-argon`."""
        t0 = time.monotonic()
        state = get_state_ref()
        if state.get("beat", 0) < 3:
            if state.get("beat", 0) < 1:
                execute_beat_1_fn(state, store=store, catalog=catalog, gym=gym)
            if state.get("beat", 0) < 2:
                execute_beat_2_fn(state, store=store, catalog=catalog, gym=gym)
            execute_beat_3_fn(state, inbox=inbox)

        prev_env = os.environ.get("BRIDGE_GYM_LIVE_LLM")
        try:
            if body.mode == "live" and _is_live_gemini_configured():
                os.environ["BRIDGE_GYM_LIVE_LLM"] = "true"
            execute_beat_4_fn(state, store=store, catalog=catalog, gym=gym)
        finally:
            if prev_env is None:
                os.environ.pop("BRIDGE_GYM_LIVE_LLM", None)
            else:
                os.environ["BRIDGE_GYM_LIVE_LLM"] = prev_env

        revs = gym.list_skill_revisions("consolidation-payoff")
        if revs and state.get("agent_gym_payoff") is not None:
            state["agent_gym_payoff"]["skill_markdown"] = revs[0].skill_markdown

        telem = state.setdefault("live_telemetry", build_initial_live_telemetry())
        elapsed_ms = max(25, int((time.monotonic() - t0) * 1000))
        telem["last_action_latency_ms"] = elapsed_ms
        if body.mode == "live" and _is_live_gemini_configured():
            telem["execution_mode"] = "live_gcp"
            telem["argon_calls"] += 1
            telem["argon_last_latency_ms"] = elapsed_ms
        else:
            telem["execution_mode"] = "instant_cached"
        return copy.deepcopy(state)

    @router.get("/ebc", response_class=HTMLResponse)
    def get_ebc_live_console_html() -> HTMLResponse:
        """Serve the unified split-screen Commercial Consolidation Live Console (`/ebc`)."""
        html_path = demo_dir / "ebc-live.html"
        if not html_path.is_file():
            raise HTTPException(
                status_code=404, detail="Live console (demo/ebc-live.html) not found."
            )
        return HTMLResponse(content=html_path.read_text(encoding="utf-8"))

    @router.get("/ebc/ebc-live.css")
    def get_ebc_live_console_css() -> Response:
        """Serve the Google Cloud GM3 Light stylesheet for `/ebc`."""
        css_path = demo_dir / "ebc-live.css"
        if not css_path.is_file():
            raise HTTPException(
                status_code=404, detail="Stylesheet (demo/ebc-live.css) not found."
            )
        return Response(
            content=css_path.read_text(encoding="utf-8"),
            media_type="text/css; charset=utf-8",
        )

    @router.get("/ebc/ebc-live.js")
    def get_ebc_live_console_js() -> Response:
        """Serve the interactive controller for `/ebc`."""
        js_path = demo_dir / "ebc-live.js"
        if not js_path.is_file():
            raise HTTPException(
                status_code=404, detail="Script (demo/ebc-live.js) not found."
            )
        return Response(
            content=js_path.read_text(encoding="utf-8"),
            media_type="application/javascript; charset=utf-8",
        )

    @router.get("/ebc/favicon.svg")
    def get_ebc_live_favicon_svg() -> Response:
        """Serve the high-contrast SVG tab icon for `/ebc`."""
        fav_path = demo_dir / "ebc-favicon.svg"
        if not fav_path.is_file():
            raise HTTPException(
                status_code=404, detail="Favicon (demo/ebc-favicon.svg) not found."
            )
        return Response(
            content=fav_path.read_text(encoding="utf-8"),
            media_type="image/svg+xml",
        )
