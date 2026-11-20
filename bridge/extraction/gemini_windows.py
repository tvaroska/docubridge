"""Page-window chunking, truncation survey, and document-part helpers for Gemini extraction."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Coroutine
from typing import Any, Protocol

from bridge.extraction.chunking import (
    ExtractionTruncated,
    bisect_window,
    merge_window_results,
)
from bridge.extraction.gemini_schema import (
    CHUNK_PAGES_ENV,
    MAX_OUTPUT_TOKENS_ENV,
    SURVEY_MAX_OUTPUT_TOKENS,
    SURVEY_PROMPT,
    SURVEY_SCHEMA,
    _is_truncated,
    _Truncated,
    build_window_instruction,
)
from bridge.extraction.resolve import ResolvedDocument
from contract.extraction import ExtractionResult


class _GenAiModelsLike(Protocol):
    def generate_content(
        self, *, model: str, contents: list[object], config: dict[str, Any]
    ) -> object: ...


class _GenAiClientLike(Protocol):
    models: _GenAiModelsLike


class GeminiWindowMixin:
    """Mixin providing document-part assembly, page-count survey, and concurrent page-window chunking."""

    _max_output_tokens: int
    _max_windows: int
    _chunk_concurrency: int
    _log: logging.Logger

    async def _call(
        self, client: _GenAiClientLike, contents: list[object], config: dict[str, Any]
    ) -> object:
        raise NotImplementedError

    async def _generate_validated(
        self,
        client: _GenAiClientLike,
        base: list[object],
        config: dict[str, Any],
        *,
        label: str,
    ) -> ExtractionResult:
        raise NotImplementedError

    def _document_part(
        self, resolved: ResolvedDocument, *, client: _GenAiClientLike | None = None
    ) -> object:
        """Build the document content part for the request from a resolved reference.

        Three branches:

        * **By reference** (a ``gs://`` object, S0-arch-7): ``Part.from_uri`` — the
          backend reads the object, the Bridge downloads nothing. *Vertex gotcha:*
          ``gs://`` is readable only on the Vertex AI backend, so an explicitly
          non-Vertex client (``client.vertexai is False``) refuses loud naming the
          env that fixes it, rather than sending a part the API will reject. The
          check is ``is False``, never falsy — a test double has no such attribute
          and must pass through.
        * **SVG**: the Address corpus images are text SVGs and Gemini may reject raw
          SVG bytes as an image part, so an SVG — and ONLY an SVG — passes as a text
          part (a valid, simpler path).
        * **Everything else, notably ``application/pdf``**: an inline bytes ``Part``
          (lazily-imported genai types), which is what makes a native-digital PDF a
          first-class input: ``tests/test_pdf_extraction.py`` drives
          ``wiki/evals/benefits/images/benefits-carrier-c-eur-fr.pdf`` through this
          method and asserts the request carries ``inline_data`` of
          ``application/pdf``, not the SVG text branch (S0-arch-6).

        A **scanned/raster** PDF (no text layer, needing OCR) still rides through
        this same branch; what the ``docai`` engine adds is the Document AI OCR text
        as a separate part next to it (see ``extract``), never a replacement for the
        document itself.
        """
        if resolved.by_reference:
            if resolved.uri is None:
                raise ValueError("by-reference artifact is missing a uri")
            if getattr(client, "vertexai", None) is False:
                raise ValueError(
                    f"by-reference artifact {resolved.uri!r} needs the Vertex AI "
                    f"backend (gs:// is not readable by the Gemini Developer API); "
                    f"set GOOGLE_GENAI_USE_VERTEXAI=TRUE and GOOGLE_CLOUD_PROJECT"
                )
            from google.genai import types as genai_types

            return genai_types.Part.from_uri(
                file_uri=resolved.uri, mime_type=resolved.mime_type
            )
        if resolved.data is None:
            raise ValueError("inline artifact is missing data")
        if resolved.mime_type == "image/svg+xml":
            return resolved.data.decode("utf-8")
        from google.genai import types as genai_types

        return genai_types.Part.from_bytes(
            data=resolved.data, mime_type=resolved.mime_type
        )

    def _truncated(self, label: str, why: str) -> ExtractionTruncated:
        """An ``ExtractionTruncated`` for ``label`` with the budget in its note."""
        error = ExtractionTruncated(
            f"extraction of {label!r} was truncated at "
            f"max_output_tokens={self._max_output_tokens}: {why}"
        )
        error.add_note(
            f"bridge extraction: artifact {label!r} — output cut at the "
            f"max_output_tokens={self._max_output_tokens} budget; {why}. Raise "
            f"{MAX_OUTPUT_TOKENS_ENV} or lower {CHUNK_PAGES_ENV}; there is no partial "
            f"result (partial rows would masquerade as a complete document)."
        )
        return error

    def _check_window_cap(self, needed: int, label: str) -> None:
        if needed > self._max_windows:
            raise self._truncated(
                label,
                f"it needs {needed} page windows, over the max_windows="
                f"{self._max_windows} cap",
            )

    async def _survey_page_count(
        self, client: _GenAiClientLike, document: object, label: str
    ) -> int:
        """One tiny call asking the model for the page count (unknown-count PDFs only).

        ``[SURVEY_PROMPT, document]`` under ``SURVEY_SCHEMA`` with a 256-token budget.
        Bad JSON, a missing/non-integer/< 1 count, or a truncated survey → raise
        ``ExtractionTruncated`` ("survey failed"); a transport error propagates.
        """
        config = {
            "response_mime_type": "application/json",
            "response_schema": SURVEY_SCHEMA,
            "max_output_tokens": SURVEY_MAX_OUTPUT_TOKENS,
        }
        response = await self._call(client, [SURVEY_PROMPT, document], config)
        raw = (getattr(response, "text", None) or "").strip()
        count = None
        if not _is_truncated(response):
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = None
            value = payload.get("page_count") if isinstance(payload, dict) else None
            if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
                count = value
        if count is None:
            raise self._truncated(
                label, f"the page-count survey failed (answered {raw[:200]!r})"
            )
        self._log.info("extraction of %s: survey reports %d pages", label, count)
        return count

    async def _extract_windows(  # noqa: PLR0913 - concurrent window orchestrator
        self,
        client: _GenAiClientLike,
        base: list[object],
        config: dict[str, Any],
        windows: list[tuple[int, int]],
        *,
        total: int,
        label: str,
    ) -> ExtractionResult:
        """Extract page ``windows`` concurrently, bisecting any truncated one, then merge.

        At most ``chunk_concurrency`` window calls are in flight (the semaphore is held
        only around the call, never across a bisection — no deadlock). Window requests
        are counted against ``max_windows`` across the whole recursion. Results are
        re-assembled in page order and merged code-side
        (``chunking.merge_window_results``). Any failed window fails the extraction —
        its siblings are cancelled and no partial result is returned.
        """
        gate = asyncio.Semaphore(self._chunk_concurrency)
        issued = 0

        async def run(first: int, last: int) -> list[tuple[int, int, ExtractionResult]]:
            nonlocal issued
            window_label = f"{label} pages {first}-{last}"
            if issued >= self._max_windows:
                raise self._truncated(
                    window_label,
                    f"the max_windows={self._max_windows} cap on page-window requests "
                    f"was reached",
                )
            issued += 1
            contents = [*base, build_window_instruction(first, last, total)]
            try:
                async with gate:
                    result = await self._generate_validated(
                        client, contents, config, label=window_label
                    )
            except _Truncated:
                if first == last:
                    raise self._truncated(
                        window_label,
                        f"page {first} alone overflows the output budget",
                    ) from None
                left, right = bisect_window(first, last)
                self._log.warning(
                    "extraction of %s truncated; bisecting into pages %d-%d and %d-%d",
                    window_label,
                    *left,
                    *right,
                )
                halves = await _gather_ordered(
                    [run(*left), run(*right)], [left, right], label
                )
                return [part for half in halves for part in half]
            return [(first, last, result)]

        parts = await _gather_ordered([run(a, b) for a, b in windows], windows, label)
        return merge_window_results([part for chunk in parts for part in chunk])


async def _gather_ordered[T](
    coros: list[Coroutine[Any, Any, T]],
    spans: list[tuple[int, int]],
    label: str,
) -> list[T]:
    """Run ``coros`` in an ``asyncio.TaskGroup``; results in input order.

    A failure cancels the siblings (the TaskGroup contract). The resulting
    ``ExceptionGroup`` is unwrapped so the caller sees the ORIGINAL exception type
    (``RuntimeError("503")``, ``ValidationError``, ``ExtractionTruncated``): one failure
    is re-raised bare; several → the first by page order, with a note counting the rest.
    """
    tasks: list[asyncio.Task[T]] = []
    failure: BaseException | None = None
    try:
        async with asyncio.TaskGroup() as group:
            tasks = [group.create_task(c) for c in coros]
    except BaseExceptionGroup as eg:
        leaves = _leaves(eg)
        ordered: list[tuple[tuple[int, int], BaseException]] = []
        for span, task in zip(spans, tasks, strict=True):
            if task.done() and not task.cancelled():
                exc = task.exception()
                if exc is not None:
                    ordered.append((span, exc))
        failure = ordered[0][1] if ordered else leaves[0]
        if len(leaves) > 1:
            first, last = ordered[0][0] if ordered else (0, 0)
            failure.add_note(
                f"bridge extraction: {len(leaves)} page windows of {label!r} failed; "
                f"raising the first by page order (pages {first}-{last})"
            )
    if failure is not None:
        raise failure
    return [task.result() for task in tasks]


def _leaves(group: BaseExceptionGroup) -> list[BaseException]:
    """The leaf exceptions of a (possibly nested) exception group, in order."""
    out: list[BaseException] = []
    for exc in group.exceptions:
        if isinstance(exc, BaseExceptionGroup):
            out.extend(_leaves(exc))
        else:
            out.append(exc)
    return out


__all__ = [
    "GeminiWindowMixin",
    "_gather_ordered",
    "_leaves",
]
