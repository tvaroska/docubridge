"""The Address service agent — the collect→gate→present ``Workflow`` (P3).

Reaches the Bridge over the A2A wire by card URL only; never imports ``bridge/``
(keeps the mock→real swap a URL change). See ``workflow.py`` for the graph.
"""
from __future__ import annotations

from agents.address.app import (
    build_address_app,
    build_durable_session_service,
)
from agents.address.collect import build_bridge_remote_agent
from agents.address.driver import (
    feed_function_response,
    latest_parked_call,
    main,
    run_collection,
)
from agents.address.gate import (
    DEFAULT_MAX_ROUNDS,
    is_satisfied,
    make_gate_node_fn,
)
from agents.address.interceptor import (
    pending_resume_target,
    resume_stamping_interceptor,
)
from agents.address.turn_reader import read_latest_exchange_turn
from agents.address.wake import (
    SessionIndex,
    SessionRef,
    build_wake_app,
    extract_task_id,
    peer_task_id_for,
    wake_task,
)
from agents.address.wire import (
    DocRef,
    encode_doc_part,
    encode_doc_parts,
    extract_exchange_turn,
)
from agents.address.workflow import build_address_workflow, present_node_fn

__all__ = [
    "DEFAULT_MAX_ROUNDS",
    "DocRef",
    "SessionIndex",
    "SessionRef",
    "build_address_app",
    "build_address_workflow",
    "build_bridge_remote_agent",
    "build_durable_session_service",
    "build_wake_app",
    "encode_doc_part",
    "encode_doc_parts",
    "extract_exchange_turn",
    "extract_task_id",
    "feed_function_response",
    "is_satisfied",
    "latest_parked_call",
    "main",
    "make_gate_node_fn",
    "peer_task_id_for",
    "pending_resume_target",
    "present_node_fn",
    "read_latest_exchange_turn",
    "resume_stamping_interceptor",
    "run_collection",
    "wake_task",
]
