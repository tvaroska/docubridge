"""``bridge.portal`` — the Provider-Portal external-zone A2UI Path-B host (P6-2).

A standalone ASGI host over the P6-1 engines (``bridge.pathb`` + ``bridge.edge.a2ui`` +
``bridge.policy``): it drives the Path-B graph in-process across parks, persists
``PathBState`` between HTTP requests (``PortalStore``, a native swap — NOT a ``Seam``),
and relays each ``reason_code``/``message`` verbatim across the HTTP boundary (A2UI-1).

**Isolation guard:** under ``bridge/`` → must NEVER import ``agents/``
(``tests/test_isolation_guard.py``).
"""

from bridge.portal.rfp_checklist import (
    RfpChecklist,
    RfpChecklistItem,
    render_rfp_checklist,
    upload_quote,
)
from bridge.portal.server import (
    HitlBody,
    RfpUploadBody,
    StartSessionBody,
    UploadBody,
    build_portal_app,
    serve,
)
from bridge.portal.session import (
    NoActivePark,
    current_view,
    resolve,
    start_session,
    upload,
)
from bridge.portal.store import (
    InMemoryPortalStore,
    PortalSessionRecord,
    PortalStore,
)

__all__ = [
    "HitlBody",
    "InMemoryPortalStore",
    "NoActivePark",
    "PortalSessionRecord",
    "PortalStore",
    "RfpChecklist",
    "RfpChecklistItem",
    "RfpUploadBody",
    "StartSessionBody",
    "UploadBody",
    "build_portal_app",
    "current_view",
    "render_rfp_checklist",
    "resolve",
    "serve",
    "start_session",
    "upload",
    "upload_quote",
]
