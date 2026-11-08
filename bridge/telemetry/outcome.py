"""EVAL-P11 Outcome Attribution & W3C Baggage Propagation for Multi-Agent Systems.

Self-contained OpenTelemetry helper vendored into `bridge/telemetry/outcome.py`:
1. Mints opaque `outcome.id` keys at the edge (`start_outcome`) and resumes across HITL pauses (`resume_outcome`).
2. Propagates `outcome.id` and `outcome.owner` via W3C `tracecontext,baggage` and stamps every span (`OutcomeBaggageSpanProcessor`).
3. Strips `outcome.*` baggage on egress to external counterparties and sanitizes untrusted inbound headers.
4. Emits a zero-duration `outcome.closed` span (`close_outcome`) so outcomes and token spend share one trace store.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Mapping, MutableMapping
from typing import Any, Literal

from opentelemetry import baggage, context, propagate, trace
from opentelemetry.baggage.propagation import W3CBaggagePropagator
from opentelemetry.context import Context
from opentelemetry.propagators.composite import CompositePropagator
from opentelemetry.sdk.trace import ReadableSpan, Span, SpanProcessor
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

OUTCOME_ID_KEY = "outcome.id"
OUTCOME_OWNER_KEY = "outcome.owner"
OUTCOME_STATUS_KEY = "outcome.status"
OUTCOME_SUBJECT_KEY = "outcome.subject"
OUTCOME_PARENT_ID_KEY = "outcome.parent_id"
OUTCOME_CLOSED_SPAN_NAME = "outcome.closed"
OUTCOME_ID_HEADER = "x-outcome-id"

OutcomeStatus = Literal["succeeded", "failed", "abandoned", "escalated"]
VALID_OUTCOME_STATUSES: frozenset[str] = frozenset(
    {"succeeded", "failed", "abandoned", "escalated"}
)


class OutcomeBaggageSpanProcessor(SpanProcessor):
    """Copies `outcome.id` and `outcome.owner` from W3C baggage onto every started span."""

    def on_start(
        self,
        span: Span,
        parent_context: Context | None = None,
    ) -> None:
        entries: dict[str, object] = {}
        if parent_context is not None:
            entries.update(baggage.get_all(context=parent_context))
        for k, v in baggage.get_all().items():
            entries.setdefault(k, v)
        for key in (OUTCOME_ID_KEY, OUTCOME_OWNER_KEY):
            val = entries.get(key)
            if val is not None and str(val):
                span.set_attribute(key, str(val))

    def on_end(self, span: ReadableSpan) -> None:
        return None

    def shutdown(self) -> None:
        return None

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


BaggageSpanProcessor = OutcomeBaggageSpanProcessor


def configure_propagation(tracer_provider: Any | None = None) -> None:
    """Register W3C `tracecontext,baggage` propagators and attach `OutcomeBaggageSpanProcessor` idempotently."""
    propagate.set_global_textmap(
        CompositePropagator([TraceContextTextMapPropagator(), W3CBaggagePropagator()])
    )
    provider = tracer_provider or trace.get_tracer_provider()
    if hasattr(provider, "add_span_processor"):
        active_multi = getattr(provider, "_active_span_processor", None)
        existing = getattr(active_multi, "_span_processors", ())
        if not any(isinstance(p, OutcomeBaggageSpanProcessor) for p in existing):
            provider.add_span_processor(OutcomeBaggageSpanProcessor())


def get_current_outcome_id(ctx: Context | None = None) -> str | None:
    """Return the active `outcome.id` from OpenTelemetry baggage, if present."""
    val = baggage.get_baggage(OUTCOME_ID_KEY, context=ctx)
    return str(val) if val is not None and str(val).strip() else None


def get_current_outcome_owner(ctx: Context | None = None) -> str | None:
    """Return the active `outcome.owner` (App Hub application ID) from OpenTelemetry baggage."""
    val = baggage.get_baggage(OUTCOME_OWNER_KEY, context=ctx)
    return str(val) if val is not None and str(val).strip() else None


current_outcome_id = get_current_outcome_id
current_outcome_owner = get_current_outcome_owner


def _attach_outcome_baggage(
    outcome_id: str,
    owner: str | None = None,
    *,
    attach_context: bool = True,
) -> str:
    ctx = context.get_current()
    ctx = baggage.set_baggage(OUTCOME_ID_KEY, outcome_id, context=ctx)
    clean_owner = owner.strip() if owner and owner.strip() else None
    if clean_owner is not None:
        ctx = baggage.set_baggage(OUTCOME_OWNER_KEY, clean_owner, context=ctx)
    if attach_context:
        context.attach(ctx)
    span = trace.get_current_span()
    if span is not None and span.is_recording():
        span.set_attribute(OUTCOME_ID_KEY, outcome_id)
        if clean_owner is not None:
            span.set_attribute(OUTCOME_OWNER_KEY, clean_owner)
    return outcome_id


def start_outcome(
    *,
    owner: str | None = None,
    outcome_id: str | None = None,
    attach_context: bool = True,
) -> str:
    """Mint a new opaque `outcome.id` (or use explicit `outcome_id`) and attach it to W3C baggage."""
    resolved_id = (
        outcome_id.strip()
        if outcome_id is not None and outcome_id.strip()
        else f"out_{uuid.uuid4().hex}"
    )
    return _attach_outcome_baggage(
        resolved_id, owner=owner, attach_context=attach_context
    )


def resume_outcome(
    outcome_id: str,
    *,
    owner: str | None = None,
    attach_context: bool = True,
) -> str:
    """Re-attach a stored `outcome.id` when resuming after HITL review, A2A continuation, or task queue."""
    if not isinstance(outcome_id, str) or not outcome_id.strip():
        raise ValueError(
            "outcome_id must be a non-empty string when resuming an outcome."
        )
    return _attach_outcome_baggage(
        outcome_id.strip(), owner=owner, attach_context=attach_context
    )


def close_outcome(
    status: OutcomeStatus | str,
    *,
    subject: str | None = None,
    parent_id: str | None = None,
    owner: str | None = None,
    outcome_id: str | None = None,
    tracer: trace.Tracer | None = None,
) -> str:
    """Emit a zero-duration `outcome.closed` span recording terminal status and optional `outcome.subject`."""
    if status not in VALID_OUTCOME_STATUSES:
        raise ValueError(
            f"Invalid outcome status {status!r}; must be one of {sorted(VALID_OUTCOME_STATUSES)}."
        )
    resolved_id = (
        outcome_id.strip()
        if outcome_id is not None and outcome_id.strip()
        else get_current_outcome_id()
    )
    if not resolved_id:
        raise ValueError(
            "Cannot close outcome without an explicit or active baggage outcome.id."
        )
    resolved_owner = (
        owner.strip()
        if owner is not None and owner.strip()
        else get_current_outcome_owner()
    )

    attrs: dict[str, str] = {
        OUTCOME_ID_KEY: resolved_id,
        OUTCOME_STATUS_KEY: str(status),
    }
    if subject is not None and str(subject).strip():
        attrs[OUTCOME_SUBJECT_KEY] = str(subject).strip()
    if parent_id is not None and str(parent_id).strip():
        attrs[OUTCOME_PARENT_ID_KEY] = str(parent_id).strip()
    if resolved_owner is not None and str(resolved_owner).strip():
        attrs[OUTCOME_OWNER_KEY] = str(resolved_owner).strip()

    active_tracer = tracer or trace.get_tracer(__name__)
    now_ns = time.time_ns()
    span = active_tracer.start_span(
        OUTCOME_CLOSED_SPAN_NAME,
        attributes=attrs,
        start_time=now_ns,
    )
    span.end(end_time=now_ns)
    return resolved_id


def without_outcome_baggage(ctx: Context | None = None) -> Context:
    """Return a copy of `ctx` with all `outcome.*` keys removed from OpenTelemetry baggage."""
    base_ctx = ctx if ctx is not None else context.get_current()
    entries = dict(baggage.get_all(context=base_ctx))
    cleaned_ctx = baggage.clear(context=base_ctx)
    for k, v in entries.items():
        if not k.startswith("outcome."):
            cleaned_ctx = baggage.set_baggage(k, v, context=cleaned_ctx)
    return cleaned_ctx


def strip_outcome_baggage_headers(
    headers: Mapping[str, str],
    *,
    is_external: bool = True,
) -> dict[str, str]:
    """Strip `outcome.*` W3C baggage items and `x-outcome-id` before calling external counterparties."""
    out = {str(k): str(v) for k, v in headers.items()}
    if not is_external:
        return out

    for key in list(out.keys()):
        lower = key.lower()
        if lower == OUTCOME_ID_HEADER:
            del out[key]
        elif lower == "baggage":
            kept: list[str] = []
            for item in out[key].split(","):
                stripped = item.strip()
                if not stripped:
                    continue
                k_part = stripped.split("=", 1)[0].strip().lower()
                if not k_part.startswith("outcome."):
                    kept.append(stripped)
            if kept:
                out[key] = ",".join(kept)
            else:
                del out[key]
    return out


def sanitize_inbound_headers(
    headers: Mapping[str, str],
    *,
    trusted_caller: bool = False,
    allow_outcome_resume: bool | None = None,
) -> dict[str, str]:
    """Strip untrusted inbound `outcome.*` baggage and `x-outcome-id` headers at the edge."""
    if trusted_caller or allow_outcome_resume:
        return {str(k): str(v) for k, v in headers.items()}
    return strip_outcome_baggage_headers(headers, is_external=True)


def inject_outcome_headers(
    headers: MutableMapping[str, str] | None = None,
    *,
    is_external: bool = False,
    ctx: Context | None = None,
) -> dict[str, str]:
    """Inject W3C `traceparent` + `baggage` into `headers`, stripping `outcome.*` when `is_external=True`."""
    carrier: dict[str, str] = dict(headers) if headers is not None else {}
    target_ctx = (
        without_outcome_baggage(ctx) if is_external else (ctx or context.get_current())
    )
    propagate.inject(carrier, context=target_ctx)
    if is_external:
        carrier = strip_outcome_baggage_headers(carrier, is_external=True)
    return carrier


def extract_outcome_context(
    headers: Mapping[str, str],
    *,
    trusted_caller: bool = True,
) -> Context:
    """Extract W3C `traceparent` + `baggage` into a Context, stripping `outcome.*` if untrusted."""
    clean_headers = sanitize_inbound_headers(headers, trusted_caller=trusted_caller)
    return propagate.extract(clean_headers)
