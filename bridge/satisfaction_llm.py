"""The model-backed COLL-2 satisfaction assessor (``BRIDGE_SATISFACTION=llm``).

One stateless ``BaseLlm.generate_content_async`` call per assessment with a JSON
``response_schema`` — no ``Runner``, no session (a one-shot advisory read needs
neither). The model reads the process skill's prose satisfaction description against
a MINIMIZED ledger (doc id, doctype, disposition, canonical issuer, reason code,
message — never ``key_fields`` / ``tables`` / field confidences / artifact refs) and
returns ``{"items": [...]}`` in the requirement vocabulary. Any exception or invalid
JSON propagates; :func:`bridge.satisfaction.propose_outstanding` turns it into the
per-entry fallback.

The model resolves like the ``document_bridge`` agent's (``BRIDGE_MODEL``, default
``gemini-2.5-flash``); a ``BaseLlm`` instance (a test double) is used as-is. A model
name becomes a ``BaseLlm`` lazily (first assessment), so building the app needs no
credentials. Never imports ``bridge.edge`` (core → edge is the wrong direction).
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from google.adk.models import BaseLlm, LlmRequest
from google.adk.models.registry import LLMRegistry
from google.genai import types
from pydantic import BaseModel

from bridge.extraction.agent import resolve_model
from bridge.satisfaction import ProposedItem
from contract.wire import LedgerEntry

ASSESSOR_INSTRUCTION = """You make an ADVISORY, best-effort assessment of whether a document collection looks complete against a natural-language satisfaction description.

Input (JSON): "satisfaction" — the description; "candidate_doctypes" — the document types this collection accepts; "ledger" — the documents received so far, each with its doctype, disposition and canonical issuer.

Rules:
- Only ledger entries with disposition "accepted" count toward satisfaction; "rejected" or "pending" entries do not.
- Return JSON {"items": [...]}: one item per requirement the description implies. Each item has "item" (a short human label), "status", "doctype_hint" and "message".
- status "satisfied" if the accepted entries already meet that requirement; "required" if something is still needed (say exactly what in "message": which kind of document and any constraint, such as a different issuer); "optional" if it is only nice-to-have.
- When the description offers alternatives, a single item that is satisfied by any one alternative is enough. If no alternative is met yet, emit one "required" item: when the accepted entries already partly meet an alternative (e.g. one of two needed documents), chase completing THAT alternative (its doctype_hint, and any constraint such as a different issuer); otherwise name the simplest alternative.
- "doctype_hint" must be one of candidate_doctypes or null.
- You never decide completeness — the requesting agent does. Do not invent documents that are not in the ledger."""


class _Proposal(BaseModel):
    """The model's JSON response shape."""

    items: list[ProposedItem]


def _ledger_view(entries: Sequence[LedgerEntry]) -> list[dict]:
    """The minimized ledger the model sees (no party data: no key fields/tables)."""
    return [
        {
            "doc_id": e.doc_id,
            "doctype": e.doctype,
            "disposition": e.disposition.value,
            "issuer": e.issuer,
            "reason_code": e.reason_code,
            "message": e.message,
        }
        for e in entries
    ]


class LlmSatisfactionAssessor:
    """A :class:`~bridge.satisfaction.SatisfactionAssessor` backed by one model call."""

    def __init__(self, model: str | BaseLlm | None = None) -> None:
        self._model = resolve_model(model)
        self._llm: BaseLlm | None = None  # built lazily (no creds at app build)

    @property
    def model(self) -> str | BaseLlm:
        """The resolved model (a name or a ``BaseLlm`` instance)."""
        return self._model

    def _resolve_llm(self) -> BaseLlm:
        if self._llm is None:
            self._llm = (
                self._model
                if isinstance(self._model, BaseLlm)
                else LLMRegistry.new_llm(self._model)
            )
        return self._llm

    def build_request(
        self,
        *,
        rule: str,
        entries: Sequence[LedgerEntry],
        candidate_doctypes: Sequence[str],
        model: str,
    ) -> LlmRequest:
        """The one-shot JSON-mode request (system instruction + a JSON payload)."""
        payload = json.dumps(
            {
                "satisfaction": rule,
                "candidate_doctypes": list(candidate_doctypes),
                "ledger": _ledger_view(entries),
            }
        )
        return LlmRequest(
            model=model,
            contents=[types.Content(role="user", parts=[types.Part(text=payload)])],
            config=types.GenerateContentConfig(
                system_instruction=ASSESSOR_INSTRUCTION,
                response_mime_type="application/json",
                response_schema=_Proposal,
                temperature=0.0,
            ),
        )

    async def assess(
        self,
        *,
        rule: str,
        entries: Sequence[LedgerEntry],
        candidate_doctypes: Sequence[str],
    ) -> list[ProposedItem]:
        """Ask the model; parse ``{"items": [...]}`` (invalid JSON → raises)."""
        llm = self._resolve_llm()
        request = self.build_request(
            rule=rule,
            entries=entries,
            candidate_doctypes=candidate_doctypes,
            model=llm.model,
        )
        chunks: list[str] = []
        async for response in llm.generate_content_async(request, stream=False):
            content = response.content
            for part in (content.parts or []) if content is not None else []:
                if part.text and not part.thought:
                    chunks.append(part.text)
        return _Proposal.model_validate_json("".join(chunks)).items
