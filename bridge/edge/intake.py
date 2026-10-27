"""Content-routed dual-mode intake (WIRE-5).

Routes inbound messages to a ``CollectRequest`` by content: structured JSON
(primary) or free text (fallback). Free text is parsed deterministically (no
LLM) — Decision 1 / Decision 3.

**Decision 1 (not a managed port):** Free-text → ``CollectRequest`` parsing is
NOT one of the managed ports (``bridge.ports.port.Port`` enum) — it's not
a managed-service boundary. No local+GCP adapter pair; no ``@pytest.mark.port``
test. ``parse_text_request`` is the deterministic text intake: it serves every
text message in deterministic edge mode, and text accompanying a ``CollectRequest``
or file part in hybrid mode. In hybrid mode (``BRIDGE_EDGE_MODE=hybrid``) a
**text-only** message is routed to the ``document_bridge`` LlmAgent leg instead
(``bridge.edge.pathroute.classify_route`` → ``Route.LLM``), never through this parser.

**Decision 2 (precedence):** Structured JSON wins when both a JSON ``CollectRequest``
data part and a text part are present. Content is selected by shape, not position.

**Decision 3 (deterministic default):** The deterministic text parser stashes the
raw text verbatim in ``CollectRequest.message`` and resolves ``process`` by scanning
loaded skills for a name-token / tag substring match. If exactly one process skill
exists, default to it (the current single-skill reality: ``address-proof``). Leave
``party=None`` (it falls back to the exchange context per the field doc).
"""

from __future__ import annotations

from a2a.helpers import proto_helpers as ph
from a2a.types import Message

from bridge.edge.skills import loaded_skills
from bridge.edge.wire import decode_collect_request
from contract.wire import CollectRequest


def parse_text_request(text: str, *, context_id: str | None = None) -> CollectRequest:
    """Parse free text to a ``CollectRequest`` (the deterministic text intake).

    Resolves ``process`` by scanning loaded process skills for a name-token or tag
    substring match (case-insensitive). If exactly one process skill exists, default
    to it. Raises ``ValueError`` on no match with multiple skills, or on empty text.

    The raw text is stashed verbatim in ``CollectRequest.message`` so a downstream
    reader (the P3 consumer) can re-derive intent. ``party`` stays ``None`` (it falls
    back to the exchange context per the field doc).

    Text-only intents on a hybrid edge never reach this parser: they are routed to
    the ``document_bridge`` LlmAgent leg (``Route.LLM``).
    """
    stripped = text.strip()
    if not stripped:
        raise ValueError(
            "empty or whitespace-only text; cannot parse to a CollectRequest"
        )

    # Scan loaded process skills for a name-token or tag substring match
    skills = loaded_skills()
    process_skills = [s for s in skills if s.kind == "process"]

    # Try to find a match
    text_lower = stripped.lower()
    matched_process: str | None = None
    for skill in process_skills:
        # Check name tokens (e.g. "address-proof" → "address", "proof")
        name_tokens = skill.name.lower().split("-")
        if any(token in text_lower for token in name_tokens):
            matched_process = skill.name
            break
        # Check tags
        if any(tag.lower() in text_lower for tag in skill.tags):
            matched_process = skill.name
            break

    # Fallback: if exactly one process skill exists, default to it
    if matched_process is None:
        if len(process_skills) == 1:
            matched_process = process_skills[0].name
        elif len(process_skills) == 0:
            raise ValueError(
                "no process skills loaded; cannot resolve process from text"
            )
        else:
            raise ValueError(
                f"could not resolve process from text (no match in {len(process_skills)} "
                f"process skills); the text must mention a skill name or tag"
            )

    return CollectRequest(
        process=matched_process,
        message=stripped,
        context_id=context_id,
    )


def decode_intake(message: Message) -> CollectRequest:
    """Content-route intake: structured JSON (primary) or free text (fallback).

    **Decision 2 (precedence):** Tries ``decode_collect_request`` (structured JSON
    data part) first. On ``ValueError`` (no matching data part), falls back to
    parsing text parts via ``parse_text_request``. Raises ``ValueError`` if neither
    is present.

    This is the dual-mode intake router (WIRE-5): JSON and text hit the **same**
    skill, routed by content (not by separate ``AgentSkill``s).
    """
    # Primary path: structured JSON CollectRequest data part
    try:
        return decode_collect_request(message)
    except ValueError:
        pass  # No JSON data part; try text

    # Fallback: parse text/plain parts
    texts = ph.get_text_parts(message.parts)
    non_empty_texts = [t for t in texts if t and t.strip()]
    if non_empty_texts:
        # Join multiple text parts with space (unlikely, but defensive)
        combined_text = " ".join(non_empty_texts)
        # Pass context_id from the message if present
        context_id = message.context_id if message.context_id else None
        return parse_text_request(combined_text, context_id=context_id)

    # Neither JSON nor text
    raise ValueError(
        "no CollectRequest data part or text/plain intake found in inbound message"
    )
