"""Gemini extraction engine — the deployed schema-driven extraction engine (P7-6).

The capability-axis sibling of ``FixtureExtractor``: same ``extract(artifact_name)
-> ExtractionResult`` shape, same module posture ("no ADK or port imports, avoiding
import cycles" — never imports ``bridge.edge``/``bridge.ports``/``agents``), but the
engine is a real Gemini call instead of a corpus lookup. Extraction is the
**capability** axis (``fixture|gemini|docai``, selected by config), NOT a local↔gcp
store swap (design/ports.md) — so this "gcp"/deployed engine sits behind the same
``ExtractionPort`` the fixture engine does.

**Schema-driven (S0-arch-5).** The prompt and the pinned structured-output schema are
not literals: every installed **doctype** skill's ``bridge-schema`` asset (normally
``assets/schema.json``) is resolved through the ``skill_registry`` port — by
``bridge/doctypes.py``, the shared reader Path-A validation builds its typed key-field
models from (S0-arch-8), so the prompt and the validator can never learn to disagree
about what a doctype declares — its declared
key fields are rendered into the prompt as the installed-doctype catalog, and the
UNION of those fields is pinned as the request's ``response_schema``. Installing a
doctype skill folder therefore changes extraction with no code change and no redeploy
— the same no-redeploy property the Agent Card already has. ``extract`` takes no
doctype argument (the Bridge *classifies*; doctype is an output), so the catalog is
every installed doctype, and ``fields.doctype`` stays a **free string** — never an
enum over installed types, or an out-of-label-space document (the corpus's passport)
could not be reported and the ``unsupported`` terminal outcome would silently die.

**Schema-validated retry (S0-bug-2).** The parse is the contract: a payload that fails
``ExtractionResult`` validation is re-asked up to ``max_attempts`` times with its own
rejected output and the specific validator reason fed back, then the last error is
raised. No fixture fallback, no silent success — a missing extraction is a measurable
miss; a fabricated one is a lie.

**Large documents — page-window chunking (S0-perf-1).** Output truncation, not the
input window, is what loses a 1000-row rate sheet: one structured response that big is
cut at ``max_output_tokens`` (``finish_reason == MAX_TOKENS``), and re-asking at the same
budget only reproduces the cut. So every request carries an explicit
``max_output_tokens`` (``BRIDGE_EXTRACTION_MAX_OUTPUT_TOKENS``, default 32768 — a
documented budget makes truncation detectable), and a truncated response NEVER enters the
schema-retry loop. Instead the engine asks for less output per request: an inline PDF
longer than ``chunk_pages`` pages (``BRIDGE_EXTRACTION_CHUNK_PAGES``, default 8; 0 = off)
is extracted as page windows up front, and a truncated single call is bisected by page
(a ``gs://`` object or an unparsable PDF first gets one tiny page-count survey call).
Each window request carries the WHOLE document plus a window instruction
(``[prompt, document, (ocr_context), window_instruction]`` — a by-reference object cannot
be split without downloading it, and no PDF library is installed), runs the same bounded
schema-validated retry, and windows run concurrently (``chunk_concurrency``, default 4)
under a hard ``max_windows`` cap. ``bridge/extraction/chunking.py`` merges the windows
code-side — rows concatenated in page order, row paths rebased, disagreements flagged,
never partial: any failed window fails the extraction. A truncated single page raises
:class:`ExtractionTruncated` (a ``ValueError``). Every ``generate_content`` call runs via
``asyncio.to_thread`` so a slow call never blocks the shared event loop (the SSE /
dashboard / portal hosts). A short document (every Address record, every one-page quote)
is still exactly ONE call with the request shape ``[prompt, document]``.

Two independently-testable halves, mirroring the sibling GCP adapters' injectable
port:

1. :func:`_parse_extraction` (plus the pure builders :func:`build_extraction_prompt` /
   :func:`build_response_schema`) — a pure JSON-text → ``ExtractionResult`` parse. No
   client, no network; unit-testable directly.
2. :meth:`GeminiExtractor.extract` — resolves the document (injectable ``resolver``,
   defaulting to :func:`artifact_resolver`, the real resolver of
   ``bridge/extraction/resolve.py``: a corpus-relative name, a **party's http(s)
   URL fetched under a default-deny egress guard**, or a ``gs://`` object handed to
   the model **by reference** — S0-arch-7), builds the request, and calls Gemini
   through an **injectable** ``client`` (default
   ``None`` → a real ``google.genai.Client()`` built lazily on first use, exactly like
   ``_GcpCloudTasksScheduler(client=...)``). ``google.genai`` is imported lazily INSIDE
   the method so importing this module pulls no genai/heavy client (a structural test
   enforces it); the ``bridge.ports`` import is lazy for the same cycle reason
   (``bridge.ports.local`` imports ``bridge.extraction.fixture`` at module level).

**Document inputs (S0-arch-6, S0-arch-7).** The engine's input envelope is a
*resolved reference*, not a local filename. SVG is passed as a text part; a
``gs://`` reference rides as a native ``Part.from_uri`` (Vertex reads the object —
the Bridge downloads nothing); every other resolved mime rides as an inline bytes
``Part`` — so a **native-digital PDF** (a real text layer) is read directly, whether
it came off the eval corpus or off a third party's signed URL, exercised end-to-end
against the benefits corpus asset
(``wiki/evals/benefits/images/benefits-carrier-c-eur-fr.pdf``).

**Document AI pre-pass (the ``docai`` engine).** A **scanned/raster** PDF (no text
layer) is the ``docai`` engine's envelope, and that engine is THIS extractor with an
injected ``prepass`` (``bridge/extraction/docai.py::DocAIPrepass``): the client's
Document AI processor OCRs the resolved document once, before the retry loop, and the
text rides as a third part next to the original document
(``[prompt, document, ocr_context]``). Everything schema-driven is shared; with no
prepass the request is exactly ``[prompt, document]`` (design/docai-prepass.md).

Parity with the corpus is **terminal-outcome, never ledger-identical** (CLAUDE.md;
design/ports.md): a real Gemini call will not reproduce the corpus's exact confidence
floats, so the live gate asserts the extracted signals feed ``run_disposition_gate``
to the same ``expected_disposition``/``expected_gate``, not float-equality.
"""

import asyncio
import json
import logging
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from bridge.doctypes import doctype_schemas
from bridge.extraction.chunking import (
    ExtractionTruncated,
    bisect_window,
    merge_window_results,
    pdf_page_count,
    plan_windows,
)
from bridge.extraction.docai import build_ocr_context
from bridge.extraction.gemini_schema import (
    CHUNK_CONCURRENCY_ENV,
    CHUNK_PAGES_ENV,
    DEFAULT_CHUNK_CONCURRENCY,
    DEFAULT_CHUNK_PAGES,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_MAX_OUTPUT_TOKENS,
    DEFAULT_MAX_WINDOWS,
    MAX_ECHO_CHARS,
    MAX_OUTPUT_TOKENS_ENV,
    PAGE_WINDOW_INSTRUCTION,
    PROMPT_FOOTER,
    PROMPT_HEADER,
    RETRY_CORRECTION,
    SURVEY_MAX_OUTPUT_TOKENS,
    SURVEY_PROMPT,
    SURVEY_SCHEMA,
    Prepass,
    Resolver,
    _coerce_resolved,
    _corpus_resolver,
    _int_setting,
    _is_truncated,
    _mime_for,
    _parse_extraction,
    _prune_nulls,
    _tables_union,
    _Truncated,
    artifact_resolver,
    build_extraction_prompt,
    build_response_schema,
    build_retry_correction,
    build_window_instruction,
)
from bridge.extraction.resolve import (
    DEFAULT_CORPUS_ROOT,
    ResolvedDocument,
    maybe_await,
)
from bridge.telemetry import trace_step
from contract.extraction import ExtractionResult

_LOG = logging.getLogger("bridge.extraction.gemini")

# The default workhorse model — reuse the constant the extraction/bridge agents use
# (both ``bridge/extraction/agent.py`` and ``bridge/agent.py`` default to this);
# overridable via BRIDGE_MODEL. Deep reasoning / conflict adjudication / Agent Gym
# teacher & autorater use REASONING_MODEL (``gemini-4-argon``).
DEFAULT_MODEL = "gemini-3.8-flash"
WORKHORSE_MODEL = DEFAULT_MODEL
REASONING_MODEL = "gemini-4-argon"


class GeminiExtractor:
    """Schema-driven Gemini extraction engine (P7-6) — behind the extraction port.

    Satisfies the same ``extract(artifact_name) -> ExtractionResult`` shape as
    ``FixtureExtractor``. The ``client``, ``resolver`` and ``schemas`` are injectable
    (the credential-free test port): an injected fake ``client`` wins, else a real
    ``google.genai.Client()`` is built lazily on first ``extract`` — importing this
    module never constructs a client or pulls ``google.genai``. An injected
    ``schemas`` dict wins over the registry read, so the prompt/schema builders are
    exercisable with no skills dir at all.

    A payload the ``ExtractionResult`` validator rejects is re-asked up to
    ``max_attempts`` times with the specific rejection reason fed back; on exhaustion
    the last validation error is raised — never a fixture fallback.

    An optional ``prepass`` (the ``docai`` engine's ``DocAIPrepass``) runs once per
    extraction on the resolved document; its text, when non-empty, is added to the
    request as a bounded OCR grounding part (``docai.build_ocr_context``).

    **Large documents (S0-perf-1).** ``max_output_tokens`` / ``chunk_pages`` /
    ``chunk_concurrency`` default from ``BRIDGE_EXTRACTION_MAX_OUTPUT_TOKENS`` /
    ``BRIDGE_EXTRACTION_CHUNK_PAGES`` / ``BRIDGE_EXTRACTION_CHUNK_CONCURRENCY`` (read at
    construction; a kwarg wins; a malformed or out-of-range value raises ``ValueError``
    naming the env — so ``GcpExtraction`` building ``GeminiExtractor()`` with no kwargs
    gets the deployed knobs). A long inline PDF is extracted as concurrent page windows
    and a truncated response is bisected by page, never re-asked at the same budget;
    ``chunk_pages=0`` disables both (a truncation then raises
    :class:`ExtractionTruncated` at once). ``max_windows`` caps window requests per
    extraction. With the OCR pre-pass, every window carries the same whole-document OCR
    part (bounded at ``MAX_OCR_CHARS``), so a long scan's later pages may have no OCR
    grounding — a known limitation (the document itself is still attached).
    """

    def __init__(
        self,
        *,
        client: Any = None,
        resolver: Resolver | None = None,
        model: str | None = None,
        corpus_root: Path | str = DEFAULT_CORPUS_ROOT,
        schemas: dict[str, dict] | None = None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        prepass: Prepass | None = None,
        max_output_tokens: int | None = None,
        chunk_pages: int | None = None,
        chunk_concurrency: int | None = None,
        max_windows: int = DEFAULT_MAX_WINDOWS,
        agent_gym: Any = None,
        log: Any = _LOG,
    ) -> None:
        import os

        if max_attempts < 1:
            raise ValueError(f"max_attempts must be >= 1; got {max_attempts}")
        self._max_output_tokens = _int_setting(
            "max_output_tokens",
            max_output_tokens,
            MAX_OUTPUT_TOKENS_ENV,
            os.environ.get(MAX_OUTPUT_TOKENS_ENV),
            DEFAULT_MAX_OUTPUT_TOKENS,
            1,
        )
        self._chunk_pages = _int_setting(
            "chunk_pages",
            chunk_pages,
            CHUNK_PAGES_ENV,
            os.environ.get(CHUNK_PAGES_ENV),
            DEFAULT_CHUNK_PAGES,
            0,
        )
        self._chunk_concurrency = _int_setting(
            "chunk_concurrency",
            chunk_concurrency,
            CHUNK_CONCURRENCY_ENV,
            os.environ.get(CHUNK_CONCURRENCY_ENV),
            DEFAULT_CHUNK_CONCURRENCY,
            1,
        )
        self._max_windows = _int_setting(
            "max_windows", max_windows, "max_windows", None, DEFAULT_MAX_WINDOWS, 1
        )
        self._client = client  # injected fake wins; else built lazily on first use
        self._resolver = resolver or artifact_resolver(Path(corpus_root))
        self._model = model or os.environ.get("BRIDGE_MODEL", DEFAULT_MODEL)
        self._max_attempts = max_attempts
        self._prepass = prepass
        self._agent_gym = agent_gym
        self._log = log
        # Injected catalog wins; else the registry is read per extract (see _catalog).
        self._schemas_fixed = schemas
        self._schemas: dict[str, dict] | None = None
        self._hint_ids: tuple[str, ...] = ()
        self._prompt: str | None = None
        self._response_schema: dict | None = None

    def _catalog(self) -> tuple[str, dict | None]:
        """Prompt + pinned response schema — rebuilt only when the catalog changed.

        The only place the extraction path reads the ``skill_registry`` port. It is
        read on each ``extract`` (not at construction), because one extractor outlives
        the app's start and a doctype skill dropped into ``BRIDGE_SKILLS_DIR`` after
        the first extract must reach the next prompt and response schema (CUJ-9). The
        read is one directory listing against a model call; the prompt and schema are
        rebuilt only when the catalog content differs from the last one seen. An
        injected ``schemas`` catalog is fixed and never re-read.
        """
        schemas = (
            self._schemas_fixed
            if self._schemas_fixed is not None
            else doctype_schemas()
        )
        hints: list[Any] = (
            list(self._agent_gym.get_extraction_hints())
            if self._agent_gym is not None
            else []
        )
        hint_ids = tuple(getattr(h, "rule_id", str(h)) for h in hints)
        if (
            self._prompt is None
            or schemas != self._schemas
            or hint_ids != self._hint_ids
        ):
            self._schemas = schemas
            self._hint_ids = hint_ids
            self._prompt = build_extraction_prompt(schemas, extraction_hints=hints)
            self._response_schema = build_response_schema(schemas)
        return self._prompt, self._response_schema

    def _get_client(self) -> Any:
        # Lazy: importing this module must not pull google.genai / build a real
        # Client (no creds). An injected client (the credential-free tests) wins.
        if self._client is None:
            from google import genai

            self._client = genai.Client()
        return self._client

    def _document_part(self, resolved: ResolvedDocument, *, client: Any = None) -> Any:
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
        if resolved.mime_type == "image/svg+xml":
            return resolved.data.decode("utf-8")
        from google.genai import types as genai_types

        return genai_types.Part.from_bytes(
            data=resolved.data, mime_type=resolved.mime_type
        )

    async def extract(self, artifact_name: str) -> ExtractionResult:
        """Extract normalized fields + disposition signals via a Gemini call.

        Resolves ``artifact_name`` — which is the URI the counterparty put on the
        wire, not a local filename — through the injectable ``resolver`` (sync or
        async, ``ResolvedDocument`` or a legacy ``(bytes, mime)`` tuple), sends the
        document + the schema-driven extraction prompt to Gemini with JSON output
        and the pinned
        structured-output schema (the installed doctypes' field union) on the request,
        and parses the response into an ``ExtractionResult`` (pure
        ``_parse_extraction``). No installed doctype declares fields → JSON mime only,
        never an empty-properties schema that would forbid every field.

        **Schema-validated retry (S0-bug-2).** ``ExtractionResult`` is
        ``extra="forbid"``, so one stray field or one truncated token rejects the
        whole payload. A rejection is retried up to ``max_attempts`` times, each
        follow-up turn carrying the model's own rejected output plus the SPECIFIC
        validator reason; the catalog, the resolved bytes and the pinned schema are
        computed once and reused across attempts (a retry must not re-fetch the
        artifact). The optional ``prepass`` (Document AI OCR) runs once here, before
        the loop, and its grounding part sits in the base contents so every retry
        keeps it; a pre-pass error propagates unchanged with no Gemini call made (no
        fallback to plain Gemini). On exhaustion the LAST error is raised (type
        preserved), annotated with the attempt count. There is no fixture fallback
        and no silent success: a
        fabricated extraction is worse than a raised error. Transport/API errors are
        deliberately NOT caught — a 429/500 is not a schema rejection and must
        surface unchanged.

        **Large documents (S0-perf-1).** Order of operations: catalog → resolve once →
        client → document part → prepass once → stdlib page count (inline PDF only) →
        single call or page windows. An inline PDF over ``chunk_pages`` pages goes
        straight to page windows; a single call cut at ``max_output_tokens`` is bisected
        by page (after one page-count survey when the count is unknown) instead of
        entering the retry loop. Each window runs the same retry loop; any window
        failure fails the whole extraction (siblings cancelled, the original exception
        type re-raised). A truncation that cannot be chunked smaller raises
        :class:`ExtractionTruncated`.
        """
        prompt, response_schema = self._catalog()
        # The catalog is built first on purpose: a registry/authoring error must
        # surface before any artifact fetch is issued.
        resolved = _coerce_resolved(await maybe_await(self._resolver(artifact_name)))
        client = self._get_client()
        document = self._document_part(resolved, client=client)
        config: dict = {"response_mime_type": "application/json"}
        if response_schema is not None:
            config["response_schema"] = response_schema
        config["max_output_tokens"] = self._max_output_tokens

        base = [prompt, document]
        if self._prepass is not None:
            # Once per extraction, never per retry or per window; an error is not caught.
            ocr = await maybe_await(self._prepass(resolved))
            if ocr:
                base.append(build_ocr_context(ocr))

        is_pdf = resolved.mime_type == "application/pdf"
        pages = (
            pdf_page_count(resolved.data)
            if is_pdf and not resolved.by_reference
            else None
        )
        if self._chunk_pages and pages is not None and pages > self._chunk_pages:
            # Proactive: a long inline PDF never risks one giant structured output.
            self._check_window_cap(-(-pages // self._chunk_pages), artifact_name)
            windows = plan_windows(pages, self._chunk_pages)
            self._log.info(
                "extraction of %s: %d pages -> %d page windows of %d",
                artifact_name,
                pages,
                len(windows),
                self._chunk_pages,
            )
            return await self._extract_windows(
                client, base, config, windows, total=pages, label=artifact_name
            )

        try:
            return await self._generate_validated(
                client, base, config, label=artifact_name
            )
        except _Truncated:
            pass
        # Reactive: the single call was cut at the output budget. Re-asking at the same
        # budget reproduces the cut, so bisect by page instead (never the retry loop).
        if not self._chunk_pages:
            raise self._truncated(
                artifact_name, "page-window chunking is disabled (chunk_pages=0)"
            )
        if pages is None and is_pdf:
            pages = await self._survey_page_count(client, document, artifact_name)
        if pages is None or pages < 2:
            what = (
                f"a {resolved.mime_type} document is not paged"
                if pages is None
                else "the document has a single page"
            )
            raise self._truncated(
                artifact_name, f"{what}; it cannot be chunked smaller"
            )
        self._log.warning(
            "extraction of %s truncated at max_output_tokens=%d; bisecting its %d pages",
            artifact_name,
            self._max_output_tokens,
            pages,
        )
        return await self._extract_windows(
            client,
            base,
            config,
            list(bisect_window(1, pages)),
            total=pages,
            label=artifact_name,
        )

    async def _call(self, client: Any, contents: list, config: dict) -> Any:
        """One ``generate_content`` call, OFF the event loop (``asyncio.to_thread``).

        The genai call is sync; run inline it would block the shared loop (SSE /
        dashboard / portal on the same Cloud Run instance) for its whole latency. Every
        test double implements only ``.models.generate_content``, so the thread hop —
        not ``client.aio`` — is the port-neutral fix.
        """
        with trace_step(
            "docubridge.extract.call_llm",
            {
                "docubridge.zone": "2_extraction_quarantine",
                "gen_ai.system": "gemini_platform",
                "gen_ai.operation.name": "call_llm",
                "gen_ai.agent.name": "extraction_agent",
                "gen_ai.request.model": self._model,
                "docubridge.finops.traffic_type": "production_inference",
            },
        ) as span:
            response = await asyncio.to_thread(
                client.models.generate_content,
                model=self._model,
                contents=contents,
                config=config,
            )
            usage = getattr(response, "usage_metadata", None)
            if usage is not None:
                in_tok = getattr(usage, "prompt_token_count", None)
                out_tok = getattr(usage, "candidates_token_count", None)
                tot_tok = getattr(usage, "total_token_count", None)
                cached_tok = getattr(usage, "cached_content_token_count", None)
                if in_tok is not None:
                    span.set_attribute("gen_ai.usage.input_tokens", int(in_tok))
                if out_tok is not None:
                    span.set_attribute("gen_ai.usage.output_tokens", int(out_tok))
                if tot_tok is not None:
                    span.set_attribute("gen_ai.usage.total_tokens", int(tot_tok))
                if cached_tok is not None:
                    span.set_attribute("gen_ai.usage.cached_tokens", int(cached_tok))
            return response

    async def _generate_validated(
        self, client: Any, base: list, config: dict, *, label: str
    ) -> ExtractionResult:
        """The bounded schema-validated retry loop (S0-bug-2) for one request.

        ``base`` is the request's fixed contents (prompt, document, optional OCR part,
        optional window instruction); a correction turn is appended AFTER it. ``label``
        names the artifact (``"<artifact> pages a-b"`` for a window) in logs and notes.
        A MAX_TOKENS response raises the internal ``_Truncated`` signal BEFORE parsing —
        a truncated output is never echoed back and re-asked at the same budget.
        """
        contents = list(base)
        last_error: Exception | None = None
        for attempt in range(1, self._max_attempts + 1):
            # Not wrapped in try/except: a transport/API error is not a schema
            # rejection — it propagates unchanged and burns no attempt.
            response = await self._call(client, contents, config)
            if _is_truncated(response):
                raise _Truncated(label)
            # ``text`` is None on a safety block; normalize so an empty body is a
            # retryable rejection, not an AttributeError.
            raw = (getattr(response, "text", None) or "").strip()
            try:
                return _parse_extraction(raw)
            except (ValidationError, json.JSONDecodeError) as exc:
                last_error = exc
                self._log.warning(
                    "extraction of %s rejected by schema validation "
                    "(attempt %d/%d): %r",
                    label,
                    attempt,
                    self._max_attempts,
                    exc,
                )
                contents = [*base, build_retry_correction(raw, exc)]

        self._log.error(
            "extraction of %s exhausted %d schema-validated attempts: %r",
            label,
            self._max_attempts,
            last_error,
        )
        last_error.add_note(
            f"bridge extraction: {self._max_attempts} schema-validated attempt(s) "
            f"rejected for artifact {label!r}; raising the last validation "
            f"error — there is deliberately no fixture fallback (a fallback would "
            f"launder hand-authored values in as if extracted)."
        )
        raise last_error

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

    async def _survey_page_count(self, client: Any, document: Any, label: str) -> int:
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

    async def _extract_windows(
        self,
        client: Any,
        base: list,
        config: dict,
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


async def _gather_ordered(
    coros: list[Coroutine[Any, Any, Any]],
    spans: list[tuple[int, int]],
    label: str,
) -> list:
    """Run ``coros`` in an ``asyncio.TaskGroup``; results in input order.

    A failure cancels the siblings (the TaskGroup contract). The resulting
    ``ExceptionGroup`` is unwrapped so the caller sees the ORIGINAL exception type
    (``RuntimeError("503")``, ``ValidationError``, ``ExtractionTruncated``): one failure
    is re-raised bare; several → the first by page order, with a note counting the rest.
    """
    tasks: list[asyncio.Task] = []
    failure: BaseException | None = None
    try:
        async with asyncio.TaskGroup() as group:
            tasks = [group.create_task(c) for c in coros]
    except BaseExceptionGroup as eg:
        leaves = _leaves(eg)
        ordered = [
            (span, task.exception())
            for span, task in zip(spans, tasks, strict=True)
            if task.done() and not task.cancelled() and task.exception() is not None
        ]
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
    "CHUNK_CONCURRENCY_ENV",
    "CHUNK_PAGES_ENV",
    "DEFAULT_CHUNK_CONCURRENCY",
    "DEFAULT_CHUNK_PAGES",
    "DEFAULT_MAX_ATTEMPTS",
    "DEFAULT_MAX_OUTPUT_TOKENS",
    "DEFAULT_MAX_WINDOWS",
    "DEFAULT_MODEL",
    "MAX_ECHO_CHARS",
    "MAX_OUTPUT_TOKENS_ENV",
    "PAGE_WINDOW_INSTRUCTION",
    "PROMPT_FOOTER",
    "PROMPT_HEADER",
    "REASONING_MODEL",
    "RETRY_CORRECTION",
    "SURVEY_MAX_OUTPUT_TOKENS",
    "SURVEY_PROMPT",
    "SURVEY_SCHEMA",
    "WORKHORSE_MODEL",
    "GeminiExtractor",
    "Prepass",
    "Resolver",
    "_Truncated",
    "_coerce_resolved",
    "_corpus_resolver",
    "_gather_ordered",
    "_int_setting",
    "_is_truncated",
    "_leaves",
    "_mime_for",
    "_parse_extraction",
    "_prune_nulls",
    "_tables_union",
    "artifact_resolver",
    "build_extraction_prompt",
    "build_response_schema",
    "build_retry_correction",
    "build_window_instruction",
]
