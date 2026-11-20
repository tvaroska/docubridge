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
prepass the request is exactly ``[prompt, document]`` (design/extraction.md).

Parity with the corpus is **terminal-outcome, never ledger-identical** (CLAUDE.md;
design/ports.md): a real Gemini call will not reproduce the corpus's exact confidence
floats, so the live gate asserts the extracted signals feed ``run_disposition_gate``
to the same ``expected_disposition``/``expected_gate``, not float-equality.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from bridge.doctypes import _OntologyRevisionLike

from pydantic import ValidationError

from bridge.doctypes import doctype_schemas, slice_ontology_for_extraction
from bridge.extraction.chunking import (
    ExtractionTruncated as ExtractionTruncated,
)
from bridge.extraction.chunking import (
    bisect_window,
    pdf_page_count,
    plan_windows,
)
from bridge.extraction.chunking import (
    merge_window_results as merge_window_results,
)
from bridge.extraction.derivations import apply_result_derivations
from bridge.extraction.docai import build_ocr_context
from bridge.extraction.gemini_schema import (
    CHUNK_CONCURRENCY_ENV as CHUNK_CONCURRENCY_ENV,
)
from bridge.extraction.gemini_schema import (
    CHUNK_PAGES_ENV as CHUNK_PAGES_ENV,
)
from bridge.extraction.gemini_schema import (
    DEFAULT_CHUNK_CONCURRENCY as DEFAULT_CHUNK_CONCURRENCY,
)
from bridge.extraction.gemini_schema import (
    DEFAULT_CHUNK_PAGES as DEFAULT_CHUNK_PAGES,
)
from bridge.extraction.gemini_schema import (
    DEFAULT_MAX_ATTEMPTS as DEFAULT_MAX_ATTEMPTS,
)
from bridge.extraction.gemini_schema import (
    DEFAULT_MAX_OUTPUT_TOKENS as DEFAULT_MAX_OUTPUT_TOKENS,
)
from bridge.extraction.gemini_schema import (
    DEFAULT_MAX_WINDOWS as DEFAULT_MAX_WINDOWS,
)
from bridge.extraction.gemini_schema import (
    MAX_ECHO_CHARS as MAX_ECHO_CHARS,
)
from bridge.extraction.gemini_schema import (
    MAX_OUTPUT_TOKENS_ENV as MAX_OUTPUT_TOKENS_ENV,
)
from bridge.extraction.gemini_schema import (
    PAGE_WINDOW_INSTRUCTION as PAGE_WINDOW_INSTRUCTION,
)
from bridge.extraction.gemini_schema import (
    PROMPT_FOOTER as PROMPT_FOOTER,
)
from bridge.extraction.gemini_schema import (
    PROMPT_HEADER as PROMPT_HEADER,
)
from bridge.extraction.gemini_schema import (
    RETRY_CORRECTION as RETRY_CORRECTION,
)
from bridge.extraction.gemini_schema import (
    SURVEY_MAX_OUTPUT_TOKENS as SURVEY_MAX_OUTPUT_TOKENS,
)
from bridge.extraction.gemini_schema import (
    SURVEY_PROMPT as SURVEY_PROMPT,
)
from bridge.extraction.gemini_schema import (
    SURVEY_SCHEMA as SURVEY_SCHEMA,
)
from bridge.extraction.gemini_schema import (
    Prepass as Prepass,
)
from bridge.extraction.gemini_schema import (
    Resolver as Resolver,
)
from bridge.extraction.gemini_schema import (
    _coerce_resolved as _coerce_resolved,
)
from bridge.extraction.gemini_schema import (
    _corpus_resolver as _corpus_resolver,
)
from bridge.extraction.gemini_schema import (
    _int_setting as _int_setting,
)
from bridge.extraction.gemini_schema import (
    _is_truncated as _is_truncated,
)
from bridge.extraction.gemini_schema import (
    _mime_for as _mime_for,
)
from bridge.extraction.gemini_schema import (
    _parse_extraction as _parse_extraction,
)
from bridge.extraction.gemini_schema import (
    _prune_nulls as _prune_nulls,
)
from bridge.extraction.gemini_schema import (
    _tables_union as _tables_union,
)
from bridge.extraction.gemini_schema import (
    _Truncated as _Truncated,
)
from bridge.extraction.gemini_schema import (
    artifact_resolver as artifact_resolver,
)
from bridge.extraction.gemini_schema import (
    build_extraction_prompt as build_extraction_prompt,
)
from bridge.extraction.gemini_schema import (
    build_response_schema as build_response_schema,
)
from bridge.extraction.gemini_schema import (
    build_retry_correction as build_retry_correction,
)
from bridge.extraction.gemini_schema import (
    build_window_instruction as build_window_instruction,
)
from bridge.extraction.gemini_windows import (
    GeminiWindowMixin as GeminiWindowMixin,
)
from bridge.extraction.gemini_windows import (
    _gather_ordered as _gather_ordered,
)
from bridge.extraction.gemini_windows import (
    _GenAiClientLike,
)
from bridge.extraction.gemini_windows import (
    _leaves as _leaves,
)
from bridge.extraction.resolve import (
    DEFAULT_CORPUS_ROOT,
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


class GeminiExtractor(GeminiWindowMixin):
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

    def __init__(  # noqa: PLR0913 - keyword-only injectable extractor config
        self,
        *,
        client: _GenAiClientLike | None = None,
        resolver: Resolver | None = None,
        model: str | None = None,
        corpus_root: Path | str = DEFAULT_CORPUS_ROOT,
        schemas: dict[str, dict[str, Any]] | None = None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        prepass: Prepass | None = None,
        max_output_tokens: int | None = None,
        chunk_pages: int | None = None,
        chunk_concurrency: int | None = None,
        max_windows: int = DEFAULT_MAX_WINDOWS,
        agent_gym: object = None,
        target_doctype: str | None = None,
        candidate_doctypes: Sequence[str] | None = None,
        context_set: str | None = None,
        log: logging.Logger = _LOG,
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
        self._target_doctype = target_doctype
        self._candidate_doctypes = (
            tuple(candidate_doctypes) if candidate_doctypes is not None else None
        )
        self._context_set = context_set
        self._log = log
        # Injected catalog wins; else the registry is read per extract (see _catalog).
        self._schemas_fixed = schemas
        self._schemas: dict[str, dict[str, Any]] | None = None
        self._hint_ids: tuple[str, ...] = ()
        self._prompt: str | None = None
        self._response_schema: dict[str, Any] | None = None
        self.last_pinned_ontology_revision_id: str = "ont-rev-v1-1-baseline"

    def _catalog(  # noqa: PLR0913 - keyword-only ontology catalog scoping
        self,
        *,
        target_doctype: str | None = None,
        candidate_doctypes: Sequence[str] | None = None,
        requested_fields: Sequence[str] | None = None,
        context_set: str | None = None,
        revision: _OntologyRevisionLike | None = None,
        ontology_revision: str | None = None,
        catalog_order: Sequence[str] | None = None,
    ) -> tuple[str, dict[str, Any] | None]:
        """Prompt + pinned response schema — rebuilt only when the catalog changed.

        The only place the extraction path reads the ``skill_registry`` port. It is
        read on each ``extract`` (not at construction), because one extractor outlives
        the app's start and a doctype skill dropped into ``BRIDGE_SKILLS_DIR`` after
        the first extract must reach the next prompt and response schema (CUJ-9). When
        ``target_doctype``, ``candidate_doctypes``, or ``context_set`` is active, slices
        the ontology to the target doctype(s) + shared spine + 1-hop neighborhood.
        A ``catalog_order`` (perturbation-stability assay) bypasses the cache.
        """
        schemas = (
            self._schemas_fixed
            if self._schemas_fixed is not None
            else doctype_schemas()
        )
        eff_target = (
            target_doctype if target_doctype is not None else self._target_doctype
        )
        eff_candidates = (
            candidate_doctypes
            if candidate_doctypes is not None
            else self._candidate_doctypes
        )
        eff_context = context_set if context_set is not None else self._context_set

        pinned_rev = ontology_revision or "ont-rev-v1-1-baseline"
        rev_id_attr = getattr(revision, "revision_id", None)
        if revision is not None and rev_id_attr:
            pinned_rev = str(rev_id_attr)
        elif not ontology_revision:
            for spec in schemas.values():
                if isinstance(spec, dict) and spec.get("x_okf_revision_id"):
                    pinned_rev = str(spec["x_okf_revision_id"])
                    break
        self.last_pinned_ontology_revision_id = pinned_rev
        get_hints = getattr(self._agent_gym, "get_extraction_hints", None)
        hints: list[object] = list(get_hints()) if callable(get_hints) else []

        if (
            eff_target
            or eff_candidates
            or eff_context
            or revision is not None
            or requested_fields
        ):
            scoped = (
                slice_ontology_for_extraction(
                    target_doctype=eff_target,
                    candidate_doctypes=eff_candidates,
                    revision=revision,
                    context_set=eff_context,
                    schemas=schemas,
                )
                if (eff_target or eff_candidates or eff_context or revision is not None)
                else schemas
            )
            return (
                build_extraction_prompt(
                    scoped,
                    extraction_hints=hints,
                    target_doctype=eff_target,
                    candidate_doctypes=eff_candidates,
                    requested_fields=requested_fields,
                    context_set=eff_context,
                    revision=revision,
                    catalog_order=catalog_order,
                ),
                build_response_schema(
                    scoped,
                    requested_fields=requested_fields,
                    catalog_order=catalog_order,
                ),
            )

        if catalog_order is not None:
            return (
                build_extraction_prompt(
                    schemas, extraction_hints=hints, catalog_order=catalog_order
                ),
                build_response_schema(schemas, catalog_order=catalog_order),
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

    def _get_client(self) -> _GenAiClientLike:
        # Lazy: importing this module must not pull google.genai / build a real
        # Client (no creds). An injected client (the credential-free tests) wins.
        if self._client is None:
            from google import genai

            self._client = cast(_GenAiClientLike, genai.Client())
        return self._client

    def _finalize_result(
        self,
        result: ExtractionResult,
        *,
        requested_fields: Sequence[str] | None = None,
        ontology_revision: str | None = None,
        revision: object = None,
    ) -> ExtractionResult:
        """Apply Stage-B deterministic derivations and matching Agent Gym hints."""
        active_hints: list[object] = []
        get_hints = getattr(self._agent_gym, "get_extraction_hints", None)
        if callable(get_hints):
            try:
                active_hints = list(
                    get_hints(
                        doctype=result.fields.doctype,
                        issuer=result.fields.issuer or "*",
                    )
                )
            except TypeError:
                active_hints = list(get_hints())
        rev_id = ontology_revision or (
            self.last_pinned_ontology_revision_id if revision is not None else None
        )
        return apply_result_derivations(
            result,
            active_hints=active_hints,
            requested_fields=requested_fields,
            ontology_revision_id=rev_id,
        )

    async def extract(  # noqa: PLR0913 - public ExtractionPort + ontology scoping contract
        self,
        artifact_name: str,
        *,
        target_doctype: str | None = None,
        candidate_doctypes: Sequence[str] | None = None,
        requested_fields: Sequence[str] | None = None,
        context_set: str | None = None,
        revision: _OntologyRevisionLike | None = None,
        ontology_revision: str | None = None,
        catalog_order: Sequence[str] | None = None,
    ) -> ExtractionResult:
        """Extract normalized fields + disposition signals via a Gemini call.

        Resolves ``artifact_name`` — which is the URI the counterparty put on the
        wire, not a local filename — through the injectable ``resolver`` (sync or
        async, ``ResolvedDocument`` or a legacy ``(bytes, mime)`` tuple), sends the
        document + the schema-driven extraction prompt to Gemini with JSON output
        and the pinned structured-output schema (the installed doctypes' field union)
        on the request, and parses the response into an ``ExtractionResult`` (pure
        ``_parse_extraction``). No installed doctype declares fields → JSON mime only,
        never an empty-properties schema that would forbid every field.
        """
        prompt, response_schema = self._catalog(
            target_doctype=target_doctype,
            candidate_doctypes=candidate_doctypes,
            requested_fields=requested_fields,
            context_set=context_set,
            revision=revision,
            ontology_revision=ontology_revision,
            catalog_order=catalog_order,
        )
        resolved = _coerce_resolved(await maybe_await(self._resolver(artifact_name)))
        client = self._get_client()
        document = self._document_part(resolved, client=client)
        config: dict[str, Any] = {"response_mime_type": "application/json"}
        if response_schema is not None:
            config["response_schema"] = response_schema
        config["max_output_tokens"] = self._max_output_tokens
        if "flash" in self._model.lower():
            config["thinking_config"] = {"thinking_budget": 0}

        base: list[object] = [prompt, document]
        if self._prepass is not None:
            # Once per extraction, never per retry or per window; an error is not caught.
            ocr = await maybe_await(self._prepass(resolved))
            if ocr:
                base.append(build_ocr_context(ocr))

        is_pdf = resolved.mime_type == "application/pdf"
        pages = (
            pdf_page_count(resolved.data)
            if is_pdf and resolved.data is not None
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
            win_result = await self._extract_windows(
                client, base, config, windows, total=pages, label=artifact_name
            )
            return self._finalize_result(
                win_result,
                requested_fields=requested_fields,
                ontology_revision=ontology_revision,
                revision=revision,
            )

        try:
            single_result = await self._generate_validated(
                client, base, config, label=artifact_name
            )
            return self._finalize_result(
                single_result,
                requested_fields=requested_fields,
                ontology_revision=ontology_revision,
                revision=revision,
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
        bisected_result = await self._extract_windows(
            client,
            base,
            config,
            list(bisect_window(1, pages)),
            total=pages,
            label=artifact_name,
        )
        return self._finalize_result(
            bisected_result,
            requested_fields=requested_fields,
            ontology_revision=ontology_revision,
            revision=revision,
        )

    async def _call(
        self, client: _GenAiClientLike, contents: list[object], config: dict[str, Any]
    ) -> object:
        """One ``generate_content`` call, OFF the event loop (``asyncio.to_thread``).

        The genai call is sync; run inline it would block the shared loop (SSE /
        dashboard / portal on the same Cloud Run instance) for its whole latency. Every
        test double implements only ``.models.generate_content``, so the thread hop —
        not ``client.aio`` — is the port-neutral fix.
        """
        mode_tag = "PDF_PATH_B_ARGON" if "argon" in self._model else "PDF_PATH_B_FLASH"
        with trace_step(
            "docubridge.extract.call_llm",
            {
                "docubridge.zone": "2_extraction_quarantine",
                "source_mode": mode_tag,
                "gen_ai.system": "gemini_platform",
                "gen_ai.operation.name": "call_llm",
                "gen_ai.agent.name": "extraction_agent",
                "gen_ai.request.model": self._model,
                "gen_ai.response.model": self._model,
                "finops.traffic_type": "production_inference",
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
                thoughts_tok = getattr(usage, "thoughts_token_count", None)
                if in_tok is not None:
                    span.set_attribute("gen_ai.usage.input_tokens", int(in_tok))
                if out_tok is not None:
                    span.set_attribute("gen_ai.usage.output_tokens", int(out_tok))
                if tot_tok is not None:
                    span.set_attribute("gen_ai.usage.total_tokens", int(tot_tok))
                if cached_tok is not None:
                    span.set_attribute("gen_ai.usage.cached_tokens", int(cached_tok))
                    span.set_attribute(
                        "gen_ai.usage.cache_read.input_tokens", int(cached_tok)
                    )
                if thoughts_tok is not None:
                    span.set_attribute(
                        "gen_ai.usage.reasoning.output_tokens", int(thoughts_tok)
                    )
            return response

    async def _generate_validated(
        self,
        client: _GenAiClientLike,
        base: list[object],
        config: dict[str, Any],
        *,
        label: str,
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
        if last_error is None:
            raise RuntimeError(
                f"extraction of {label!r} exhausted attempts without exception"
            )
        last_error.add_note(
            f"bridge extraction: {self._max_attempts} schema-validated attempt(s) "
            f"rejected for artifact {label!r}; raising the last validation "
            f"error — there is deliberately no fixture fallback (a fallback would "
            f"launder hand-authored values in as if extracted)."
        )
        raise last_error


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
    "ExtractionTruncated",
    "GeminiExtractor",
    "GeminiWindowMixin",
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
    "merge_window_results",
]
