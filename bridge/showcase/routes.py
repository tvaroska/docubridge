"""Showcase scenario routes: generic `/v1/demo/scenarios/*` plus legacy aliases.

Legacy paths (`/v1/demo/consolidation-collab/*`, `/v1/demo/schema-evolution/*`,
`/v1/demo/seed`, `/ebc*`) are thin aliases over the engine so the shipped demo
consoles and documentation keep working while new consumers use the generic routes.
"""

from __future__ import annotations

import base64
import os
import re
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse, Response

from bridge.showcase.engine import ScenarioEngine, UnknownScenarioError
from bridge.showcase.live_extraction import (
    build_initial_live_telemetry,
    is_live_gemini_configured,
    process_uploaded_pdfs,
)
from bridge.showcase.loader import demo_static_root
from bridge.showcase.pdf_assets import build_pdf_zip, pdf_metadata
from bridge.user_interaction.schemas import (
    AdvanceConsolidationCollabRequest,
    AdvanceSchemaEvolutionCollabRequest,
    LiveCollabModeRequest,
    UploadConsolidationPdfRequest,
)
from contract.scenario import AdvanceScenarioRequest, ScenarioStageKind

_SAFE_FILENAME_RE = re.compile(r"[^a-zA-Z0-9._-]+")


def _not_found(scenario_id: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"Unknown scenario {scenario_id!r}.")


def _advance(engine: ScenarioEngine, scenario_id: str, beat: int) -> dict[str, Any]:
    try:
        return engine.advance(scenario_id, beat).payload
    except UnknownScenarioError as exc:
        raise _not_found(scenario_id) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _require(
    engine: ScenarioEngine, kind: ScenarioStageKind, domain: str | None = None
) -> str:
    manifest = engine.find(kind=kind, domain=domain) or engine.find(kind=kind)
    if manifest is None:
        raise HTTPException(
            status_code=404, detail=f"No {kind.value} scenario is loaded."
        )
    return manifest.id


def _static(path: Path, media_type: str) -> Response:
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"{path.name} not found.")
    return Response(content=path.read_text(encoding="utf-8"), media_type=media_type)


def _decode_uploads(
    engine: ScenarioEngine, scenario_id: str, body: UploadConsolidationPdfRequest
) -> list[tuple[str, bytes]]:
    resolved: list[tuple[str, bytes]] = []
    for sid in body.sample_ids:
        asset = engine.resolve_pdf(scenario_id, sid)
        if asset is None:
            raise HTTPException(status_code=404, detail=f"Unknown sample PDF {sid!r}.")
        resolved.append((asset.filename, asset.pdf_bytes))
    for item in body.files:
        safe_name = _SAFE_FILENAME_RE.sub("_", Path(item.filename).name) or "upload.pdf"
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
        engine.store_custom_pdf(scenario_id, safe_name, pdf_bytes)
        resolved.append((safe_name, pdf_bytes))
    if not resolved:
        raise HTTPException(
            status_code=400,
            detail="Provide at least one file in `files` or `sample_ids`.",
        )
    return resolved


def _live_config(engine: ScenarioEngine, scenario_id: str) -> dict[str, Any]:
    stage = engine.get(scenario_id).stage
    if stage is None or not stage.live_extraction:
        raise HTTPException(
            status_code=404, detail=f"Scenario {scenario_id!r} has no live PDF stage."
        )
    return dict(stage.live_extraction)


def _beats_for(engine: ScenarioEngine, scenario_id: str) -> dict[str, int]:
    beats = _live_config(engine, scenario_id).get("beats", {})
    return {
        "expand": int(beats.get("expand", 1)),
        "reconcile": int(beats.get("reconcile", 2)),
        "waiver": int(beats.get("waiver", 3)),
        "distill": int(beats.get("distill", 4)),
    }


def _upload(
    engine: ScenarioEngine, scenario_id: str, body: UploadConsolidationPdfRequest
) -> dict[str, Any]:
    _live_config(engine, scenario_id)
    resolved = _decode_uploads(engine, scenario_id, body)
    ctx = engine.context(scenario_id)
    process_uploaded_pdfs(
        ctx,
        resolved_files=resolved,
        mode=body.mode,
        run_beat=lambda beat: engine.run_beat(scenario_id, beat),
    )
    return engine.state(scenario_id).payload


def _approve_waiver(
    engine: ScenarioEngine, scenario_id: str, body: LiveCollabModeRequest
) -> dict[str, Any]:
    beats = _beats_for(engine, scenario_id)
    t0 = time.monotonic()
    ctx = engine.context(scenario_id)
    for name in ("expand", "reconcile"):
        if ctx.state.get("beat", 0) < beats[name]:
            engine.run_beat(scenario_id, beats[name])
    engine.run_beat(scenario_id, beats["waiver"])
    telem = ctx.state.setdefault("live_telemetry", build_initial_live_telemetry())
    telem["execution_mode"] = "live_gcp" if body.mode == "live" else "instant_cached"
    telem["last_action_latency_ms"] = max(12, int((time.monotonic() - t0) * 1000))
    return engine.state(scenario_id).payload


def _distill_skill(
    engine: ScenarioEngine, scenario_id: str, body: LiveCollabModeRequest
) -> dict[str, Any]:
    config = _live_config(engine, scenario_id)
    beats = _beats_for(engine, scenario_id)
    t0 = time.monotonic()
    ctx = engine.context(scenario_id)
    for name in ("expand", "reconcile", "waiver"):
        if ctx.state.get("beat", 0) < beats[name]:
            engine.run_beat(scenario_id, beats[name])
    live = body.mode == "live" and is_live_gemini_configured()
    prev_env = os.environ.get("BRIDGE_GYM_LIVE_LLM")
    try:
        if live:
            os.environ["BRIDGE_GYM_LIVE_LLM"] = "true"
        engine.run_beat(scenario_id, beats["distill"])
    finally:
        if prev_env is None:
            os.environ.pop("BRIDGE_GYM_LIVE_LLM", None)
        else:
            os.environ["BRIDGE_GYM_LIVE_LLM"] = prev_env
    revs = engine.gym.list_skill_revisions(config["gym_skill_doctype"])
    if revs and ctx.state.get("agent_gym_payoff") is not None:
        ctx.state["agent_gym_payoff"]["skill_markdown"] = revs[0].skill_markdown
    telem = ctx.state.setdefault("live_telemetry", build_initial_live_telemetry())
    elapsed_ms = max(25, int((time.monotonic() - t0) * 1000))
    telem["last_action_latency_ms"] = elapsed_ms
    if live:
        telem["execution_mode"] = "live_gcp"
        telem["argon_calls"] += 1
        telem["argon_last_latency_ms"] = elapsed_ms
    else:
        telem["execution_mode"] = "instant_cached"
    return engine.state(scenario_id).payload


def _serve_pdf(engine: ScenarioEngine, scenario_id: str, filename: str) -> Response:
    asset = engine.resolve_pdf(scenario_id, filename)
    if asset is not None:
        raw, served = asset.pdf_bytes, asset.filename
    else:
        custom = engine.custom_pdf(scenario_id, filename)
        if custom is None:
            raise HTTPException(status_code=404, detail=f"PDF {filename!r} not found.")
        raw, served = custom, filename
    return Response(
        content=raw,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{served}"'},
    )


def _pdf_listing(
    engine: ScenarioEngine, scenario_id: str, zip_url: str
) -> dict[str, Any]:
    base = engine.pdf_url_base(scenario_id)
    return {
        "zip_url": zip_url,
        "pdfs": [
            pdf_metadata(r, url_base=base)
            for r in engine.pdf_catalog(scenario_id).values()
        ],
    }


def _zip_response(engine: ScenarioEngine, scenario_id: str) -> Response:
    return Response(
        content=build_pdf_zip(list(engine.pdf_catalog(scenario_id).values())),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{scenario_id}-sample-pdfs.zip"'
        },
    )


def register_scenario_routes(router: APIRouter, *, engine: ScenarioEngine) -> None:
    """Mount generic scenario routes and the legacy aliases onto `router`."""

    @router.get("/v1/demo/scenarios")
    def list_scenarios_endpoint() -> dict[str, Any]:
        """List every loaded scenario manifest (seed + stage bodies omitted)."""
        return {"scenarios": engine.list()}

    @router.get("/v1/demo/scenarios/{scenario_id}/state")
    def scenario_state_endpoint(scenario_id: str) -> dict[str, Any]:
        try:
            return engine.state(scenario_id).model_dump(mode="json")
        except UnknownScenarioError as exc:
            raise _not_found(scenario_id) from exc

    @router.post("/v1/demo/scenarios/{scenario_id}/advance")
    def scenario_advance_endpoint(
        scenario_id: str, body: AdvanceScenarioRequest
    ) -> dict[str, Any]:
        try:
            return engine.advance(scenario_id, body.beat).model_dump(mode="json")
        except UnknownScenarioError as exc:
            raise _not_found(scenario_id) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.post("/v1/demo/scenarios/{scenario_id}/reset")
    def scenario_reset_endpoint(scenario_id: str) -> dict[str, Any]:
        try:
            return engine.reset(scenario_id).model_dump(mode="json")
        except UnknownScenarioError as exc:
            raise _not_found(scenario_id) from exc

    @router.post("/v1/demo/scenarios/seed")
    def scenario_seed_endpoint() -> dict[str, Any]:
        return engine.seed_all()

    @router.get("/v1/demo/scenarios/{scenario_id}/pdfs")
    def scenario_pdfs_endpoint(scenario_id: str) -> dict[str, Any]:
        return _pdf_listing(
            engine, scenario_id, f"/v1/demo/scenarios/{scenario_id}/pdfs.zip"
        )

    @router.get("/v1/demo/scenarios/{scenario_id}/pdfs.zip")
    def scenario_pdfs_zip_endpoint(scenario_id: str) -> Response:
        return _zip_response(engine, scenario_id)

    @router.get("/v1/demo/scenarios/{scenario_id}/pdfs/{filename}")
    def scenario_pdf_file_endpoint(scenario_id: str, filename: str) -> Response:
        return _serve_pdf(engine, scenario_id, filename)

    @router.post("/v1/demo/scenarios/{scenario_id}/upload-pdf")
    def scenario_upload_pdf_endpoint(
        scenario_id: str, body: UploadConsolidationPdfRequest
    ) -> dict[str, Any]:
        return _upload(engine, scenario_id, body)

    @router.post("/v1/demo/scenarios/{scenario_id}/approve-waiver")
    def scenario_approve_waiver_endpoint(
        scenario_id: str, body: LiveCollabModeRequest
    ) -> dict[str, Any]:
        return _approve_waiver(engine, scenario_id, body)

    @router.post("/v1/demo/scenarios/{scenario_id}/distill-skill")
    def scenario_distill_skill_endpoint(
        scenario_id: str, body: LiveCollabModeRequest
    ) -> dict[str, Any]:
        return _distill_skill(engine, scenario_id, body)

    _register_legacy_aliases(router, engine)


def _register_legacy_aliases(router: APIRouter, engine: ScenarioEngine) -> None:
    consolidation = ScenarioStageKind.consolidation
    evolution = ScenarioStageKind.schema_evolution

    @router.post("/v1/demo/seed")
    def seed_demo_scenarios_endpoint() -> dict[str, Any]:
        """Seed baseline bi-temporal facts and lineage for every scenario with a `seed` block."""
        return engine.seed_all()

    @router.post("/v1/demo/reset")
    def reset_demo_scenarios_endpoint() -> dict[str, Any]:
        """Reset all live showcase scenarios and close any open outcomes as abandoned."""
        engine.reset_all()
        return {"status": "reset"}

    @router.get("/v1/demo/consolidation-collab/state")
    def get_consolidation_collab_state_endpoint() -> dict[str, Any]:
        """Current beat state of the default consolidation scenario (legacy alias)."""
        return engine.state(_require(engine, consolidation)).payload

    @router.post("/v1/demo/consolidation-collab/advance")
    def advance_consolidation_collab_endpoint(
        body: AdvanceConsolidationCollabRequest,
    ) -> dict[str, Any]:
        return _advance(engine, _require(engine, consolidation), body.beat)

    @router.post("/v1/demo/consolidation-collab/reset")
    def reset_consolidation_collab_endpoint() -> dict[str, Any]:
        return engine.reset(_require(engine, consolidation)).payload

    @router.get("/v1/demo/consolidation-collab/pdfs")
    def list_consolidation_pdfs_endpoint() -> dict[str, Any]:
        return _pdf_listing(
            engine,
            _require(engine, consolidation),
            "/v1/demo/consolidation-collab/pdfs.zip",
        )

    @router.get("/v1/demo/consolidation-collab/pdfs.zip")
    def download_consolidation_pdfs_zip_endpoint() -> Response:
        return _zip_response(engine, _require(engine, consolidation))

    @router.get("/v1/demo/consolidation-collab/pdfs/{filename}")
    def get_consolidation_pdf_file_endpoint(filename: str) -> Response:
        return _serve_pdf(engine, _require(engine, consolidation), filename)

    @router.post("/v1/demo/consolidation-collab/upload-pdf")
    def upload_consolidation_pdf_endpoint(
        body: UploadConsolidationPdfRequest,
    ) -> dict[str, Any]:
        return _upload(engine, _require(engine, consolidation), body)

    @router.post("/v1/demo/consolidation-collab/approve-waiver")
    def approve_consolidation_waiver_endpoint(
        body: LiveCollabModeRequest,
    ) -> dict[str, Any]:
        return _approve_waiver(engine, _require(engine, consolidation), body)

    @router.post("/v1/demo/consolidation-collab/distill-skill")
    def distill_consolidation_skill_endpoint(
        body: LiveCollabModeRequest,
    ) -> dict[str, Any]:
        return _distill_skill(engine, _require(engine, consolidation), body)

    @router.get("/v1/demo/schema-evolution/state")
    def get_schema_evolution_state(
        domain: str | None = Query(default=None),
    ) -> dict[str, Any]:
        """Current schema-evolution state; `?domain=` selects the scenario (legacy alias)."""
        return engine.state(_require(engine, evolution, domain)).payload

    @router.post("/v1/demo/schema-evolution/advance")
    def post_schema_evolution_advance(
        body: AdvanceSchemaEvolutionCollabRequest,
    ) -> dict[str, Any]:
        return _advance(engine, _require(engine, evolution, body.domain), body.beat)

    @router.post("/v1/demo/schema-evolution/reset")
    def post_schema_evolution_reset(
        domain: str | None = Query(default=None),
    ) -> dict[str, Any]:
        return engine.reset(_require(engine, evolution, domain)).payload

    @router.get("/ebc", response_class=HTMLResponse)
    def get_ebc_live_console_html() -> HTMLResponse:
        """Serve the split-screen live console for the default consolidation scenario."""
        html_path = demo_static_root() / "ebc-live.html"
        if not html_path.is_file():
            raise HTTPException(status_code=404, detail="Live console not found.")
        return HTMLResponse(content=html_path.read_text(encoding="utf-8"))

    @router.get("/ebc/ebc-live.css")
    def get_ebc_live_console_css() -> Response:
        return _static(demo_static_root() / "ebc-live.css", "text/css; charset=utf-8")

    @router.get("/ebc/ebc-live.js")
    def get_ebc_live_console_js() -> Response:
        return _static(
            demo_static_root() / "ebc-live.js", "application/javascript; charset=utf-8"
        )

    @router.get("/ebc/favicon.svg")
    def get_ebc_live_favicon_svg() -> Response:
        return _static(demo_static_root() / "ebc-favicon.svg", "image/svg+xml")


__all__ = ["register_scenario_routes"]
