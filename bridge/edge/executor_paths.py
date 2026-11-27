"""Deterministic Path-A, RFP, and submission-persistence helpers for ``CollectExecutor``."""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING

from a2a.server.agent_execution import RequestContext
from a2a.types import Message, TaskState
from a2a.utils.errors import InvalidParamsError, InvalidRequestError
from google.adk.artifacts import BaseArtifactService
from pydantic import ValidationError

from bridge import patha
from bridge.collect import Collector, DocumentSubmission, project_collection_status
from bridge.edge.artifacts import (
    Resolver,
    filename_for,
    persist_submission,
    scoped_uri,
)
from bridge.edge.card import REQUEST_BASE_URL
from bridge.edge.executor_hitl import _require_context_ids
from bridge.edge.intake import decode_intake
from bridge.edge.pathroute import decode_assertion
from bridge.edge.rfp_open import (
    INVALID_RFP_CONTEXT_TEXT,
    INVALID_RFP_REQUEST_PREFIX,
    RFP_CONFLICT_TEXT,
    RFP_SUBMISSIONS_TEXT,
    InvalidRfpContext,
    open_or_update_rfp,
    rfp_context_id,
)
from bridge.exchange_store import StaleExchangeWrite
from bridge.extraction.resolve import maybe_await, validate_gs_uri
from bridge.satisfaction import evaluate_turn_sufficiency
from bridge.telemetry import trace_step
from bridge.telemetry.outcome import (
    close_outcome,
    current_outcome_id,
    current_outcome_owner,
)
from contract.enums import Disposition, Gate
from contract.wire import CollectRequest, ExchangeTurn, LedgerEntry

if TYPE_CHECKING:
    from bridge.dashboard.board import RfpBoard


async def preresolve_submissions(
    submissions: list[DocumentSubmission],
    resolver: Resolver,
) -> tuple[list[DocumentSubmission], dict[str, tuple[bytes, str]]]:
    """Validate ``gs://`` references and pre-fetch bytes for inbound submissions."""
    resolved_payloads: dict[str, tuple[bytes, str]] = {}
    enriched: list[DocumentSubmission] = []
    for sub in submissions:
        ref = sub.artifact
        if ref is not None and ref.uri:
            if ref.uri.strip().lower().startswith("gs://"):
                try:
                    validate_gs_uri(ref.uri.strip())
                except ValueError as exc:
                    raise InvalidParamsError(message=str(exc)) from exc
            resolved = await maybe_await(resolver(ref.uri))
            if resolved is not None:
                resolved_payloads[sub.doc_id] = resolved
                sub = sub.model_copy(update={"raw_bytes": resolved[0]})
        enriched.append(sub)
    return enriched, resolved_payloads


async def persist_turn_submissions(  # noqa: PLR0913 - explicit artifact persistence collaborators
    *,
    store: BaseArtifactService,
    base_url: str,
    turn: ExchangeTurn,
    submissions: list[DocumentSubmission],
    resolved_payloads: dict[str, tuple[bytes, str]],
    resolver: Resolver,
    party: str,
    caller_principal: str | None,
) -> None:
    """Persist inbound bytes into ``store`` and rewrite ledger entry URIs."""
    saved: dict[str, tuple[str, int]] = {}
    for sub in submissions:
        pre = resolved_payloads.get(sub.doc_id)

        def _pre_resolver(
            _uri: str, _payload: tuple[bytes, str] | None = pre
        ) -> tuple[bytes, str] | None:
            return _payload

        version = await persist_submission(
            store,
            party=party,
            context_id=turn.context_id,
            submission=sub,
            resolver=_pre_resolver if pre is not None else resolver,
            caller_principal=caller_principal,
        )
        if version is not None:
            saved[sub.doc_id] = (filename_for(sub), version)

    for entry in turn.status.entries:
        if entry.artifact is None:
            continue
        mapped = saved.get(entry.doc_id)
        if mapped is None:
            continue
        filename, version = mapped
        entry.artifact.uri = scoped_uri(
            REQUEST_BASE_URL.get() or base_url,
            context_id=turn.context_id,
            filename=filename,
            version=version,
        )


async def execute_rfp_turn(
    rfp_board: RfpBoard | None,
    context: RequestContext,
    message: Message,
    request: CollectRequest,
) -> ExchangeTurn:
    """Open, update, or read an RFP exchange on the durable ``RfpBoard``."""
    if rfp_board is None:
        raise RuntimeError("RFP board is not bound")
    if any(p.HasField("raw") or p.HasField("url") for p in message.parts):
        raise InvalidParamsError(message=RFP_SUBMISSIONS_TEXT)
    _, context_id = _require_context_ids(context)
    try:
        cid = rfp_context_id(request, context_id)
    except InvalidRfpContext as exc:
        raise InvalidParamsError(message=INVALID_RFP_CONTEXT_TEXT) from exc
    try:
        return await asyncio.to_thread(
            open_or_update_rfp, rfp_board, request, context_id=cid
        )
    except StaleExchangeWrite as exc:
        raise InvalidRequestError(message=RFP_CONFLICT_TEXT.format(rfp_id=cid)) from exc
    except ValueError as exc:
        raise InvalidParamsError(message=INVALID_RFP_REQUEST_PREFIX + str(exc)) from exc


def build_path_a_turn(
    message: Message,
    *,
    candidate_doctypes_fn: Callable[[str], list[str]],
    collector: Collector,
) -> ExchangeTurn:
    """Validate and dispose a structured Path-A extraction assertion into an ``ExchangeTurn``."""
    request = decode_intake(message)
    doctypes = candidate_doctypes_fn(request.process)
    context_id = request.context_id or uuid.uuid4().hex
    assertion = decode_assertion(message)
    doc_id = assertion.fields.doctype or "assertion"
    try:
        with trace_step(
            "docubridge.edge.path_a_validate",
            {
                "source_mode": "A2A_PATH_A",
                "skill_id": doc_id,
                "entity_id": request.party or context_id,
                "gen_ai.operation.name": "execute_tool",
                "gen_ai.tool.name": "path_a_validate_assertion",
                "gen_ai.agent.name": "document_bridge_path_a",
                "finops.traffic_type": "production_inference",
            },
        ):
            entry = patha.validate_only_entry(
                assertion, doc_id=doc_id, candidate_doctypes=doctypes
            )
        if entry.disposition is Disposition.accepted and (
            collector._vault_store is not None
            or collector._operational_store is not None
            or collector._knowledge_catalog is not None
        ):
            from bridge.user_interaction import ingest_accepted_submission

            entity_id = request.party or context_id
            ingested = ingest_accepted_submission(
                entry,
                entity_id=entity_id,
                context_id=context_id,
                raw_document_bytes=assertion.model_dump_json().encode("utf-8"),
                filename=f"{doc_id}.json",
                mime_type="application/json",
                source_mode="A2A_PATH_A",
                vault_store=collector._vault_store,
                operational_store=collector._operational_store,
                knowledge_catalog=collector._knowledge_catalog,
            )
            entry = ingested.entry
    except ValidationError as exc:
        entry = LedgerEntry(
            doc_id=doc_id,
            doctype=assertion.fields.doctype,
            disposition=Disposition.rejected,
            gate=Gate.unsupported,
            reason_code="schema_invalid",
            message=(
                "The structured extraction assertion failed schema validation "
                f"and was rejected before disposition: {exc.error_count()} error(s)."
            ),
        )
    status = project_collection_status([entry])
    sufficiency = evaluate_turn_sufficiency(
        request.process,
        [entry],
        caller_requirements=request.requirements,
    )
    return ExchangeTurn(
        context_id=context_id,
        status=status,
        sufficiency=sufficiency,
        terminal=True,
    )


def finalize_completed_turn_outcome(
    context: RequestContext,
    turn: ExchangeTurn,
    *,
    caller_owned_tasks: set[str],
    task_outcomes: dict[str, str],
) -> None:
    """Close telemetry outcome for edge-owned tasks upon ``TASK_STATE_COMPLETED``."""
    cid = context.context_id or context.task_id or "a2a-task"
    caller_owned = cid in caller_owned_tasks
    caller_owned_tasks.discard(cid)
    outcome_id = task_outcomes.pop(cid, None) or current_outcome_id() or f"out_{cid}"
    if caller_owned:
        return
    default_owner = (
        os.environ.get("BRIDGE_APPHUB_APPLICATION_ID", "").strip() or "a2a-edge"
    )
    has_accepted = any(
        e.disposition is Disposition.accepted for e in turn.status.entries
    )
    has_rejected = any(
        e.disposition is Disposition.rejected for e in turn.status.entries
    )
    outcome_status = "failed" if has_rejected and not has_accepted else "succeeded"
    close_outcome(
        outcome_status,
        subject=turn.context_id,
        outcome_id=outcome_id,
        owner=current_outcome_owner() or default_owner,
    )


def emit_turn_payload_summary(turn: ExchangeTurn, state: TaskState) -> None:
    """Emit trace payload log for an outbound ``ExchangeTurn``."""
    from bridge.telemetry.payload_log import emit_trace_payload_log

    emit_trace_payload_log(
        user_prompt=f"A2A edge exchange turn context={turn.context_id}",
        retrieved_context=turn.model_dump_json(),
        final_response=f"state={TaskState.Name(state)} entries={len(turn.status.entries)}",
    )
