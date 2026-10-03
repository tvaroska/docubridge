"""``ScriptedLlm`` — a model-free ``BaseLlm`` test double (S3-edge-2).

Drives the real ``document_bridge`` App + ``Runner`` with no network and no Gemini:
each model call picks its reply from a content-keyed script.

- the latest request content is a function response → ``on_tool[<tool name>]``,
  else a text reply ``after_tool_text`` (deliberately a FALSE acceptance claim — the
  D4 projection must never trust it);
- else the latest user text → the first ``on_text`` key (insertion order) that is a
  case-insensitive substring, else a text reply ``default_text``.

Every scripted tool call must name a tool the request actually offers
(``llm_request.tools_dict``) — catches script typos, and lets a script scope a step
to the root agent vs the ``extraction_agent`` sub-agent (one shared instance sees
both agents' requests). Every request is recorded; ``call_count`` counts them.
"""
from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any

from google.adk.models import BaseLlm, LlmCapabilities, LlmRequest, LlmResponse
from google.genai import types
from pydantic import Field


@dataclass(frozen=True)
class ToolCall:
    """One scripted function call."""

    name: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Reply:
    """One scripted model turn: optional free text + zero or more tool calls."""

    tool_calls: tuple[ToolCall, ...] = ()
    text: str | None = None  # free text the projection must ignore


class ScriptedLlm(BaseLlm):
    """Model-free ``BaseLlm``: picks its reply from a content-keyed script."""

    # Must NOT look like a gemini name (BaseLlm's name-based capability fallback
    # would emit a FutureWarning); ``capabilities`` is overridden outright too.
    model: str = "scripted-llm"
    on_text: dict[str, Reply] = Field(default_factory=dict)
    on_tool: dict[str, Reply] = Field(default_factory=dict)
    default_text: str = "I can only help with document submissions."
    after_tool_text: str = "Done — the document was accepted."
    max_calls: int = 25
    requests: list[LlmRequest] = Field(default_factory=list)

    @property
    def capabilities(self) -> LlmCapabilities:
        return LlmCapabilities(output_schema_and_tools=False)

    @property
    def call_count(self) -> int:
        """How many model calls were made (root + sub-agent)."""
        return len(self.requests)

    def _pick(self, llm_request: LlmRequest) -> Reply:
        if not llm_request.contents:
            return Reply(text=self.default_text)
        last = llm_request.contents[-1]
        parts = last.parts or []
        responses = [p.function_response for p in parts if p.function_response is not None]
        if responses:
            return self.on_tool.get(responses[-1].name or "", Reply(text=self.after_tool_text))
        text = " ".join(p.text for p in parts if p.text).lower()
        for key, reply in self.on_text.items():
            if key.lower() in text:
                return reply
        return Reply(text=self.default_text)

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        self.requests.append(llm_request)
        if self.call_count > self.max_calls:
            raise RuntimeError(
                f"ScriptedLlm exceeded max_calls={self.max_calls} (script loop?)"
            )
        reply = self._pick(llm_request)
        offered = set(llm_request.tools_dict)
        for call in reply.tool_calls:
            if call.name not in offered:
                raise ValueError(
                    f"scripted tool {call.name!r} is not offered by this request; "
                    f"offered: {sorted(offered)}"
                )
        parts: list[types.Part] = []
        if reply.text:
            parts.append(types.Part(text=reply.text))
        parts.extend(
            types.Part(function_call=types.FunctionCall(name=c.name, args=dict(c.args)))
            for c in reply.tool_calls
        )
        yield LlmResponse(
            content=types.Content(role="model", parts=parts),
            usage_metadata=types.GenerateContentResponseUsageMetadata(
                prompt_token_count=0, candidates_token_count=0, total_token_count=0
            ),
        )
