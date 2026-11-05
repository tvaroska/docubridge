"""Edge routing — the dual-path ``classify_path`` and the hybrid-edge ``classify_route``.

Two pure classifiers over the same wire primitives ``bridge.edge.wire`` /
``bridge.edge.intake`` use:

**``classify_path(message) -> Path``** decides the fulfillment path by **what kind of
response arrived** (``design/a2a-edge.md``):

- a structured **extraction-assertion** data part (validates as the
  ``ExtractionResult`` / ``ExtractionFields`` shape, ``extra="forbid"``) present →
  **Path.A** (validate-only, no extraction);
- a file (``url``) part or free text → **Path.B** (portal / extraction graph).

A ``CollectRequest``-only message (no assertion, no file) defaults to **Path.B** intake —
today's one-turn collect / portal default (§2c: do not regress the file/text path).

**``classify_route(message, *, mode, task_state, has_pending_review) -> Route``** is the
hybrid edge's route table (``design/a2a-edge.md``; S3-edge-1 D3). The routing mode is
``BRIDGE_EDGE_MODE`` (:func:`resolve_edge_mode`, D2): ``deterministic`` (default) routes
every message to Path-A or the model-free Collector exactly as ``classify_path`` did;
``hybrid`` additionally hands text-only intents to the ``document_bridge`` LlmAgent leg
and recognises the human-review resolution / pending-review routes. Structured traffic
(assertions, ``CollectRequest``s, file submissions) never reaches the model in either
mode. The edge mode is a routing mode, not a port (no local/GCP adapter pair).

Imports ``a2a.*`` (the edge already does) + ``contract.*``; NEVER ``agents/``.
"""

from __future__ import annotations

import os
from enum import Enum

from a2a.helpers import proto_helpers as ph
from a2a.types import Message, TaskState
from google.protobuf.json_format import MessageToDict

from bridge.edge.wire import (
    KIND_HUMAN_REVIEW_RESOLUTION,
    META_KIND,
    decode_collect_request,
)
from contract.extraction import ExtractionResult

# D2: the edge routing-mode flag, read once at app build (``build_a2a_app``).
EDGE_MODE_ENV = "BRIDGE_EDGE_MODE"


class Path(str, Enum):
    """The dual-path fulfillment mode for an inbound response."""

    A = "A"  # structured extraction-assertion -> validate-only (no extraction)
    B = "B"  # file upload / free text -> portal / extraction graph


def _is_extraction_assertion(data: dict) -> bool:
    """True iff a data part validates as an extraction assertion (shape-select).

    Requires the full ``ExtractionResult`` shape (fields + the self-reported signals a
    validate-only disposition reads: ``overall_confidence``, ``legible``,
    ``flagged_fields``). ``extra="forbid"`` makes an unrelated dict (e.g. a
    ``CollectRequest``) fail and be skipped. Deviation from the plan sketch: the bare
    ``ExtractionFields`` shape is NOT treated as an assertion — it carries no signals to
    dispose, so accepting it in classification would disagree with ``decode_assertion``.
    """
    try:
        ExtractionResult.model_validate(data)
        return True
    except Exception:
        return False


def classify_path(message: Message) -> Path:
    """Classify an inbound message as Path A (structured) or Path B (portal).

    Path A iff some data part validates as an extraction assertion (select-by-shape,
    never by position). Otherwise Path B — the file-upload / free-text / ``CollectRequest``
    default, matching today's edge behavior.
    """
    for data in ph.get_data_parts(message.parts):
        if isinstance(data, dict) and _is_extraction_assertion(data):
            return Path.A
    return Path.B


def decode_assertion(message: Message) -> ExtractionResult:
    """Find the data part whose shape validates as an extraction assertion (Path A).

    Selects **by shape** (never by position): the first data part validating as
    ``ExtractionResult`` is returned. Raises ``ValueError`` if none match.
    """
    for data in ph.get_data_parts(message.parts):
        if not isinstance(data, dict):
            continue
        try:
            return ExtractionResult.model_validate(data)
        except Exception:
            continue
    raise ValueError(
        "no extraction-assertion data part found in inbound message "
        "(expected an application/json DataPart matching the ExtractionResult shape)"
    )


# --------------------------------------------------------------------------- #
# The hybrid edge: routing mode (D2) + route table (D3)                        #
# --------------------------------------------------------------------------- #
class EdgeMode(str, Enum):
    """The edge routing mode (``BRIDGE_EDGE_MODE``, S3-edge-1 D2)."""

    deterministic = "deterministic"  # R1 + R2 only — every message model-free
    hybrid = "hybrid"  # + the LlmAgent leg for text-only intents


def resolve_edge_mode(value: EdgeMode | str | None = None) -> EdgeMode:
    """Resolve the edge routing mode (D2) — fail closed.

    An explicit ``value`` wins (test / app injection); a ``None`` or blank value falls
    through to ``BRIDGE_EDGE_MODE``; unset/blank env → ``EdgeMode.deterministic``. The
    match is exact and case-sensitive; any other value raises ``ValueError`` (mirrors
    ``bridge.ports.config.resolve_mode``). Not a port.
    """
    if isinstance(value, EdgeMode):
        return value
    raw = (value or "").strip()
    if not raw:
        raw = os.environ.get(EDGE_MODE_ENV, "").strip()
    if not raw:
        return EdgeMode.deterministic
    try:
        return EdgeMode(raw)
    except ValueError as e:
        raise ValueError(
            f"invalid edge mode {raw!r} for {EDGE_MODE_ENV}; "
            "expected 'deterministic' or 'hybrid'"
        ) from e


class Route(str, Enum):
    """Where the edge executor sends one inbound message (S3-edge-1 D3)."""

    GUARD_REFUSE = "guard_refuse"  # G  — task parked for a reviewer
    PATH_A = "path_a"  # R1 — validate-only assertion
    COLLECT = "collect"  # R2 — the deterministic Collector
    LLM = "llm"  # R4 — text-only intent → LlmAgent leg
    RESUME_REVIEW = "resume_review"  # R6 — resolution of the pending review
    NO_PENDING_REVIEW = "no_pending_review"  # R5 — resolution, nothing pending


def _has_resolution_part(message: Message) -> bool:
    """True iff a data part is tagged ``metadata.kind == human_review_resolution``.

    Selected by the metadata tag (D10), never by position or payload shape.
    """
    return any(
        part.HasField("data")
        and part.HasField("metadata")
        and MessageToDict(part.metadata).get(META_KIND) == KIND_HUMAN_REVIEW_RESOLUTION
        for part in message.parts
    )


def _has_collect_request(message: Message) -> bool:
    try:
        decode_collect_request(message)
    except ValueError:
        return False
    return True


def _has_file_part(message: Message) -> bool:
    """True iff any part is a file part — by reference (``url``) or inline (``raw``).

    ``raw`` counts on purpose: inline bytes must still reach ``decode_submissions``
    on the Collector path and be rejected there, never be handed to the model.
    """
    return any(part.WhichOneof("content") in ("url", "raw") for part in message.parts)


def _has_text(message: Message) -> bool:
    return any(t and t.strip() for t in ph.get_text_parts(message.parts))


def classify_route(
    message: Message,
    *,
    mode: EdgeMode,
    task_state: TaskState | None,
    has_pending_review: bool,
) -> Route:
    """Classify one inbound message onto the D3 route table (pure; first match wins).

    ``task_state`` is the existing task's state (``None`` on a new task);
    ``has_pending_review`` is whether that task's LLM session holds an unanswered
    ``request_human_review`` call.

    - **deterministic**: ``PATH_A`` iff ``classify_path`` says Path A, else ``COLLECT``
      (text-only included) — byte-identical to the pre-hybrid edge; task state and
      pending review are ignored.
    - **hybrid**:
      1. a task parked in INPUT_REQUIRED with a pending review is locked to its
         resolution: a resolution part → ``RESUME_REVIEW``, anything else →
         ``GUARD_REFUSE`` (G precedes R1, deliberately);
      2. a resolution part with nothing pending → ``NO_PENDING_REVIEW`` (resolution
         wins over text);
      3. an extraction assertion → ``PATH_A``;
      4. a ``CollectRequest`` data part or any file part (incl. text + files) →
         ``COLLECT``;
      5. non-empty text → ``LLM``;
      6. otherwise → ``COLLECT`` (today's ``decode_intake`` refusal).
    """
    if mode is EdgeMode.deterministic:
        return Route.PATH_A if classify_path(message) is Path.A else Route.COLLECT

    resolution = _has_resolution_part(message)
    parked = task_state == TaskState.TASK_STATE_INPUT_REQUIRED and has_pending_review
    if parked:
        return Route.RESUME_REVIEW if resolution else Route.GUARD_REFUSE
    if resolution:
        return Route.NO_PENDING_REVIEW
    if classify_path(message) is Path.A:
        return Route.PATH_A
    if _has_collect_request(message) or _has_file_part(message):
        return Route.COLLECT
    if _has_text(message):
        return Route.LLM
    return Route.COLLECT
