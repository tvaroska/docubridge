"""Provider-Portal ASGI host (P6-2) — the external-zone A2UI Path-B edge for humans.

A standalone ``FastAPI`` host (distinct from the A2A JSON-RPC edge in
``bridge/edge/server.py``): it renders the P6-1 A2UI views as JSON, drives
``bridge.pathb.step`` **in-process** across parks (persisting ``PathBState`` in a
``PortalStore``), and posts the HITL resolution. It relays the agent's
``reason_code``+``message`` verbatim across the HTTP boundary (A2UI-1).

**Not on the A2A edge (§0 / gotcha 6).** The portal IS the external-zone Bridge edge
for humans; it calls ``pathb.step`` in-process, NOT back over A2A JSON-RPC. There is no
``/a2ui`` mount on ``bridge/edge/server.py`` — this is a separate host.

**Local upload = a corpus artifact name (§1.2 / gotcha 9).** The upload endpoint takes
a ``doc_id`` + ``artifact_name`` (a corpus id the fixture extractor resolves). P7 swaps
this for a real byte upload persisted via ``bridge/edge/artifacts.py::persist_submission``
+ Gemini extraction — noted, not built.

**Carrier RFP checklist (S0-frontend-1).** With an ``RfpBoard`` injected, a carrier reaches
its RFP checklist by its leg ``context_id`` (``GET /rfp/legs/{context_id}``) and uploads a
quote into a product-line row (``POST /rfp/legs/{context_id}/uploads``) —
``bridge.portal.rfp_checklist``. No board → 404. An upload racing another writer on the
same RFP (``StaleExchangeWrite``, the board's compare-and-set) → 409; retry (S0-arch-17).

Isolation: imports ``contract/`` + ``bridge.*`` + ``fastapi``/``pydantic`` + stdlib
only; NEVER ``agents/`` (it does not need ``a2a.*`` — in-process, no JSON-RPC).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from bridge.edge.a2ui import encode_a2ui_view
from bridge.email_ingress.correlation import IntakeAddressing
from bridge.exchange_store import StaleExchangeWrite
from bridge.portal import rfp_checklist
from bridge.portal import session as engine
from bridge.portal.session import NoActivePark
from bridge.portal.store import InMemoryPortalStore, PortalSessionRecord, PortalStore
from bridge.seams import Seam, get_adapter, resolve_mode
from contract.enums import Disposition
from contract.wire import ArtifactRef

if TYPE_CHECKING:
    from bridge.dashboard.board import RfpBoard

# The Vite dev server origins. One per frontend app so ``scripts/run-all.sh`` can bring
# the surfaces up side by side (docs/local-dev.md); real per-party Gateway/Agent-Identity
# scoping is deploy-only (P7, ``design/zones.md``) — locally CORS just lets the dev UI in.
VITE_DEV_ORIGINS = [f"http://localhost:{port}" for port in range(5173, 5178)]


def _stale_exchange_write_409(_request: Request, exc: Exception) -> JSONResponse:
    """A compare-and-set conflict on an RFP (S0-arch-17): 409, the client retries."""
    ref = getattr(exc, "ref", None)
    return JSONResponse(
        {"detail": f"RFP {ref!r} changed concurrently; retry"}, status_code=409
    )


class StartSessionBody(BaseModel):
    """POST /sessions request body."""

    model_config = ConfigDict(extra="forbid")

    process: str
    context_id: str | None = None


class UploadBody(BaseModel):
    """POST /sessions/{sid}/uploads request body. ``artifact_name`` is a corpus id
    the fixture extractor resolves locally (P7: a real persisted byte upload)."""

    model_config = ConfigDict(extra="forbid")

    doc_id: str
    artifact_name: str
    artifact: ArtifactRef | None = None


class HitlBody(BaseModel):
    """POST /sessions/{sid}/hitl request body (the reviewer's resolution)."""

    model_config = ConfigDict(extra="forbid")

    resolution: Literal["accepted", "rejected"]
    message: str


class RfpUploadBody(BaseModel):
    """POST /rfp/legs/{context_id}/uploads body: one quote into one product-line row.
    ``artifact_name`` is a corpus id locally (P7: a real persisted byte upload)."""

    model_config = ConfigDict(extra="forbid")

    doc_id: str
    artifact_name: str
    product_line: str


def build_portal_app(
    *,
    store: PortalStore | None = None,
    rfps: RfpBoard | None = None,
    intake: IntakeAddressing | None = None,
    extraction: Any = None,
) -> FastAPI:
    """Build the Provider-Portal FastAPI host.

    The session routes respond ``encode_a2ui_view(current_view(rec))`` (POST /sessions also
    returns the ``session_id``). ``store`` defaults to an in-memory store (native swap,
    §2b); tests inject a fresh one per case. ``rfps`` (optional, S0-frontend-1) is the
    shared ``RfpBoard`` the carrier RFP checklist routes read and file into; ``intake`` the
    legs' reply addressing (``None`` → ``IntakeAddressing.from_env()``); ``extraction`` the
    engine an RFP upload runs through (``None`` → the extraction seam adapter, resolved
    lazily at the first upload).
    """
    active_store: PortalStore = store or InMemoryPortalStore()
    reply_intake = intake if intake is not None else IntakeAddressing.from_env()
    engines: dict[str, Any] = {}
    app = FastAPI(title="Provider Portal (A2UI Path-B host)")
    app.add_exception_handler(StaleExchangeWrite, _stale_exchange_write_409)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=VITE_DEV_ORIGINS,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def _load(sid: str) -> PortalSessionRecord:
        try:
            return active_store.get(sid)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=f"unknown session {sid!r}") from e

    @app.post("/sessions")
    def create_session(body: StartSessionBody) -> dict:
        rec = engine.start_session(
            active_store, process=body.process, context_id=body.context_id
        )
        return {
            "session_id": rec.session_id,
            "view": encode_a2ui_view(engine.current_view(rec)),
        }

    @app.get("/sessions/{sid}/view")
    def get_view(sid: str) -> dict:
        rec = _load(sid)
        return encode_a2ui_view(engine.current_view(rec))

    @app.post("/sessions/{sid}/uploads")
    async def post_upload(sid: str, body: UploadBody) -> dict:
        _load(sid)  # 404 for an unknown session before touching the graph
        rec = await engine.upload(
            active_store,
            sid,
            doc_id=body.doc_id,
            artifact_name=body.artifact_name,
            artifact=body.artifact,
        )
        return encode_a2ui_view(engine.current_view(rec))

    @app.post("/sessions/{sid}/hitl")
    def post_hitl(sid: str, body: HitlBody) -> dict:
        _load(sid)
        try:
            rec = engine.resolve(
                active_store,
                sid,
                resolution=Disposition(body.resolution),
                message=body.message,
            )
        except NoActivePark as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        return encode_a2ui_view(engine.current_view(rec))

    def _rfp_board() -> RfpBoard:
        if rfps is None:
            raise HTTPException(status_code=404, detail="no RFP board configured")
        return rfps

    def _extraction() -> Any:
        if extraction is not None:
            return extraction
        if "extraction" not in engines:
            engines["extraction"] = get_adapter(
                Seam.extraction, resolve_mode(Seam.extraction)
            )
        return engines["extraction"]

    @app.get("/rfp/legs/{context_id}")
    def get_rfp_checklist(context_id: str) -> dict:
        board = _rfp_board()
        try:
            checklist = rfp_checklist.checklist_for(
                board, context_id, intake=reply_intake
            )
        except KeyError as e:
            raise HTTPException(status_code=404, detail="unknown RFP leg") from e
        return checklist.model_dump(mode="json")

    @app.post("/rfp/legs/{context_id}/uploads")
    async def post_rfp_upload(context_id: str, body: RfpUploadBody) -> dict:
        board = _rfp_board()
        try:
            checklist = await rfp_checklist.upload_quote(
                board,
                context_id,
                doc_id=body.doc_id,
                artifact_name=body.artifact_name,
                product_line=body.product_line,
                extraction=_extraction(),
                intake=reply_intake,
            )
        except rfp_checklist.UploadUnreadable as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
        except rfp_checklist.UploadConflict as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        except KeyError as e:
            raise HTTPException(
                status_code=404, detail="unknown RFP leg or product line"
            ) from e
        return checklist.model_dump(mode="json")

    return app


def serve(host: str = "127.0.0.1", port: int = 8001) -> None:  # pragma: no cover
    """Run the portal host with uvicorn for manual runs (mirrors edge/server.py)."""
    import uvicorn

    uvicorn.run(build_portal_app(), host=host, port=port)


if __name__ == "__main__":  # pragma: no cover
    serve()
