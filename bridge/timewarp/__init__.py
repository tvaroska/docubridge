"""``bridge.timewarp`` — the local-only time-warp host (P6-5, FE-5).

A native local host (NOT a ``Port``) that drives the P5 SLA ladder over the P5-1
``VirtualClock`` and stamps the fired lifecycle onto the **shared** ``PortalStore`` so the
running Provider Portal / Ops Dashboard show ``on_track → overdue → reminder → escalated``
on cue. Deterministic stage time: the clock advances only by an explicit REST call — no
wall clock, no asyncio timers.

Exports:
- ``TimewarpHost``: the pure engine (clock + ladder registry + store stamping).
- ``build_timewarp_app``: the FastAPI host builder (injectable, shared ``PortalStore``).
- ``serve``: uvicorn runner for manual runs (mirrors ``bridge/dashboard/server.py``).

**Isolation guard:** under ``bridge/`` → must NEVER import ``agents/``
(``tests/test_isolation_guard.py`` walks ``bridge/**``). Imports ``contract/`` +
``bridge.*`` + ``fastapi``/``starlette``/``pydantic`` + stdlib only.
"""

from bridge.timewarp.host import TimewarpHost
from bridge.timewarp.server import build_timewarp_app, serve

__all__ = [
    "TimewarpHost",
    "build_timewarp_app",
    "serve",
]
