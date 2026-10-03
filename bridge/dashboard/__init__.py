"""Ops Dashboard (P6-4) — the internal-zone, Bridge-side operator read-model.

Exports:
- ``build_dashboard_app``: the FastAPI host builder (injectable ``PortalStore`` + optional
  ``ProgramBoard``).
- ``ProgramBoard``: the injectable in-memory program board (S1-benefits-7).
- ``RfpBoard``: the injectable in-memory RFP collection board (S0-arch-11).
- ``mount_rfp_routes`` / ``ChaseBody``: the consumer RFP read API + manual chase
  (S0-frontend-1). The local RFP demo host is ``bridge.demos.rfp_demo`` (a
  ``uvicorn --factory`` target in the fenced demo-host package — never imported here).
  The deployed RFP board host is ``bridge.dashboard.board_host`` (S0-arch-17; not
  re-exported, so a fresh ``import bridge.dashboard`` stays light).
- ``serve``: uvicorn runner for manual runs (mirrors ``bridge/portal/server.py``).
- read-model helpers: ``project_exchange``, ``project_comparison``,
  ``project_rfp_exchange``, ``project_rfp_status`` (+ ``RFP_STATUS_API_VERSION``),
  ``list_exchanges``,
  ``hitl_queue``, ``escalation_queue``, ``readmodel_snapshot``, ``sse_frames``.
"""

from bridge.dashboard.board import ProgramBoard, RfpBoard
from bridge.dashboard.readmodel import (
    RFP_STATUS_API_VERSION,
    escalation_queue,
    hitl_queue,
    list_exchanges,
    project_comparison,
    project_exchange,
    project_rfp_exchange,
    project_rfp_status,
    readmodel_snapshot,
    sse_frames,
)
from bridge.dashboard.rfp_api import ChaseBody, mount_rfp_routes
from bridge.dashboard.server import build_dashboard_app, serve

__all__ = [
    "RFP_STATUS_API_VERSION",
    "ChaseBody",
    "ProgramBoard",
    "RfpBoard",
    "build_dashboard_app",
    "escalation_queue",
    "hitl_queue",
    "list_exchanges",
    "mount_rfp_routes",
    "project_comparison",
    "project_exchange",
    "project_rfp_exchange",
    "project_rfp_status",
    "readmodel_snapshot",
    "serve",
    "sse_frames",
]
