"""Document AI OCR pre-pass — the ``docai`` extraction engine's front half.

The ``docai`` engine is **not a second extractor**. It is the schema-driven
:class:`~bridge.extraction.gemini.GeminiExtractor` with this pre-pass bolted on in
front (``GeminiExtractor(prepass=DocAIPrepass())``): the client's **existing** Document
AI processor (OCR, Layout Parser or Form Parser) reads the document once, and its plain
text layer rides into the Gemini request as an extra grounding part next to the
original document. Gemini still classifies, maps carrier terms onto the installed
doctype's fields through the skill catalog, and answers under the pinned response
schema — everything schema-driven (catalog, retry, null-pruning, by-reference) is
inherited unchanged. That is the "build it once" shape (design/docai-prepass.md).

**The pre-pass contract.**

* *Input envelope:* inline bytes **or** a ``gs://`` reference, plus a mime type. Only
  the mimes Document AI accepts (:data:`DOCAI_MIME_TYPES`) are sent; anything else —
  notably the Address corpus's text SVGs — returns ``None`` with **no** Document AI
  call, and the Gemini request is exactly what the plain ``gemini`` engine sends. A
  ``gs://`` reference is handed to Document AI by reference (``gcs_document``) — the
  Bridge downloads nothing, mirroring Gemini's ``Part.from_uri``.
* *Output:* the plain OCR text (``document.text``; a Layout Parser response whose
  ``text`` is empty is flattened from ``document_layout.blocks``). Form Parser
  key/value pairs are already inside ``document.text``; no entity map is built — the
  pre-pass is doctype-agnostic because doctype is an *output* of extraction.
* *Once per extraction:* the caller runs it before the schema-validated retry loop;
  a retry reuses the text and never re-OCRs.
* *No fallback:* a Document AI error propagates unchanged. Silently dropping to plain
  Gemini would be a capability downgrade nobody asked for (a silent cap). Empty text
  (a blank page) is not an error: a warning is logged and no OCR part is added —
  Gemini still sees the document and reports ``legible``.

**Processor by resource name.** ``BRIDGE_DOCAI_PROCESSOR`` is the full resource name
``projects/{project}/locations/{location}/processors/{id}`` (optionally
``/processorVersions/{v}``) of the processor the client already runs, usually in the
client's own project (cross-project IAM: ``roles/documentai.apiUser`` for the runtime
identity on that project — terraform ``modules/iam``). It is validated when the engine
is resolved, so a misconfigured deploy fails before any fetch. Calls go to the
processor's **regional** endpoint (``{location}-documentai.googleapis.com``) — the
default global endpoint answers "processor not found" for an ``eu`` processor.

**Portable by construction.** Only the stdlib, a lazy ``google.cloud.documentai``
import, and ``ResolvedDocument`` (used by the thin ``__call__`` adapter alone). The
builders and the flattener take primitives (``mime_type``, ``data``, ``uri``), so this
module can be vendored by a consumer engine (the consumer's ``ADP_DOCAI_PROCESSOR`` is the same
setting). No module-level ``google*`` import — a structural test enforces it.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass

from bridge.extraction.resolve import ResolvedDocument, maybe_await
from contract.extraction import SourceAnchor

_LOG = logging.getLogger("bridge.extraction.docai")

DOCAI_PROCESSOR_ENV = "BRIDGE_DOCAI_PROCESSOR"

# The mimes Document AI's online processing accepts. Deliberately NO image/svg+xml:
# the Address corpus is text SVG, and it must keep the plain-Gemini request shape.
DOCAI_MIME_TYPES: frozenset[str] = frozenset(
    {
        "application/pdf",
        "image/png",
        "image/jpeg",
        "image/tiff",
        "image/gif",
        "image/bmp",
        "image/webp",
    }
)

# Upper bound on the OCR text carried into the Gemini request (a 15-page scan is far
# below it); a runaway text layer is truncated and the part says so — the same
# bounded-echo idea as ``gemini.build_retry_correction``.
MAX_OCR_CHARS = 100_000

_PROCESSOR_RE = re.compile(
    r"^projects/(?P<project>[^/\s]+)/locations/(?P<location>[a-z0-9-]+)"
    r"/processors/(?P<processor>[^/\s]+)(?:/processorVersions/[^/\s]+)?$"
)

_EXPECTED_SHAPE = (
    "projects/{project}/locations/{location}/processors/{id}"
    "[/processorVersions/{version}]"
)

OCR_CONTEXT = """Document AI OCR text layer of the attached document (machine-read{truncated}). Use it to read text the image makes hard to see; the attached document stays authoritative for layout, legibility and what is actually printed. Never copy a value that appears only here and not on the document.
<<<OCR
{text}
OCR>>>"""


@dataclass(frozen=True)
class ProcessorRef:
    """A parsed processor resource name: the full ``name`` plus where it lives."""

    name: str
    project: str
    location: str

    @property
    def endpoint(self) -> str:
        """The processor's regional API endpoint (``us``/``eu`` → ``{loc}-documentai``)."""
        return f"{self.location}-documentai.googleapis.com"


def parse_processor(name: str) -> ProcessorRef:
    """Parse and validate a processor resource name; ``ValueError`` when malformed."""
    value = (name or "").strip()
    match = _PROCESSOR_RE.match(value)
    if not match:
        raise ValueError(
            f"malformed Document AI processor name {value!r}: expected "
            f"{_EXPECTED_SHAPE} (the full resource name of the client's processor)"
        )
    return ProcessorRef(
        name=value, project=match["project"], location=match["location"]
    )


def resolve_processor(explicit: str | None = None) -> ProcessorRef:
    """The processor to call: ``explicit`` wins, else ``BRIDGE_DOCAI_PROCESSOR``.

    Read at CALL time, never import time. Neither set → ``RuntimeError`` naming the
    env: the ``docai`` engine refuses to run without a processor rather than quietly
    becoming the plain Gemini engine.
    """
    value = (explicit or os.environ.get(DOCAI_PROCESSOR_ENV, "")).strip()
    if not value:
        raise RuntimeError(
            f"the docai extraction engine requires the Document AI processor resource "
            f"name ({DOCAI_PROCESSOR_ENV} env or the processor arg, "
            f"{_EXPECTED_SHAPE}); refusing a silent fallback to plain Gemini."
        )
    return parse_processor(value)


def build_process_request(
    processor: str,
    *,
    mime_type: str,
    data: bytes | None = None,
    uri: str | None = None,
) -> dict | None:
    """The ``process_document`` request dict, or ``None`` for an unsupported mime.

    Pure. Exactly one of ``data``/``uri``. A ``gs://`` uri rides by reference
    (``gcs_document`` — Document AI reads the object, nothing is downloaded); inline
    bytes ride as ``raw_document``. A plain dict because the proto-plus client accepts
    one, which is what lets a credential-free fake stand in without the SDK.
    """
    if (data is None) == (uri is None):
        raise ValueError("build_process_request needs exactly one of data= or uri=")
    if mime_type not in DOCAI_MIME_TYPES:
        return None
    if uri is not None:
        if not uri.startswith("gs://"):
            raise ValueError(
                f"Document AI reads only gs:// references by reference; got {uri!r}"
            )
        return {
            "name": processor,
            "gcs_document": {"gcs_uri": uri, "mime_type": mime_type},
        }
    return {
        "name": processor,
        "raw_document": {"content": data, "mime_type": mime_type},
    }


def _block_text(block) -> list[str]:
    """Text of one Layout Parser block, depth-first, in reading order."""
    out: list[str] = []
    text_block = getattr(block, "text_block", None)
    if text_block is not None:
        text = getattr(text_block, "text", "") or ""
        if text.strip():
            out.append(text.strip())
        for child in getattr(text_block, "blocks", None) or []:
            out.extend(_block_text(child))
    table_block = getattr(block, "table_block", None)
    if table_block is not None:
        rows = [
            *(getattr(table_block, "header_rows", None) or []),
            *(getattr(table_block, "body_rows", None) or []),
        ]
        for row in rows:
            cells: list[str] = []
            for cell in getattr(row, "cells", None) or []:
                for child in getattr(cell, "blocks", None) or []:
                    cells.extend(_block_text(child))
            if cells:
                out.append(" | ".join(cells))
    list_block = getattr(block, "list_block", None)
    if list_block is not None:
        for entry in getattr(list_block, "list_entries", None) or []:
            for child in getattr(entry, "blocks", None) or []:
                out.extend(_block_text(child))
    return out


def document_text(document) -> str:
    """The plain text layer of a Document AI ``Document``.

    OCR and Form Parser fill ``document.text``. A Layout Parser response leaves it
    empty and carries ``document_layout.blocks`` instead; those are flattened
    recursively (text blocks and their children, then best-effort table rows and list
    entries). Attribute-tolerant, so it reads a proto-plus object or a plain test
    double alike.
    """
    text = getattr(document, "text", "") or ""
    if text.strip():
        return text
    layout = getattr(document, "document_layout", None)
    parts: list[str] = []
    for block in getattr(layout, "blocks", None) or []:
        parts.extend(_block_text(block))
    return "\n".join(parts)


def build_ocr_context(text: str) -> str:
    """The OCR grounding part added to the Gemini request (pure, bounded)."""
    body = (text or "").strip()
    truncated = ""
    if len(body) > MAX_OCR_CHARS:
        body = body[:MAX_OCR_CHARS]
        truncated = f"; truncated to the first {MAX_OCR_CHARS} characters"
    return OCR_CONTEXT.format(truncated=truncated, text=body)


def _bbox_from_poly(poly) -> tuple[float, float, float, float] | None:
    """Convert a Document AI ``bounding_poly.normalized_vertices`` list to ``(x0, y0, x1, y1)``."""
    verts = getattr(poly, "normalized_vertices", None) or []
    if not verts:
        return None
    xs = [max(0.0, min(1.0, float(getattr(v, "x", 0.0) or 0.0))) for v in verts]
    ys = [max(0.0, min(1.0, float(getattr(v, "y", 0.0) or 0.0))) for v in verts]
    return (min(xs), min(ys), max(xs), max(ys))


def extract_docai_source_anchors(
    document, *, artifact_uri: str | None = None
) -> dict[str, SourceAnchor]:
    """Extract ``SourceAnchor`` (page number + normalized bounding box) per field from a
    Document AI ``Document`` response (``entities`` and ``pages[*].form_fields``)."""
    if document is None:
        return {}
    anchors: dict[str, SourceAnchor] = {}

    for entity in getattr(document, "entities", None) or []:
        field_name = (
            getattr(entity, "type_", None) or getattr(entity, "type", None) or ""
        ).strip()
        if not field_name or field_name in anchors:
            continue
        snippet = (getattr(entity, "mention_text", None) or "").strip() or None
        page_number = 1
        bbox: tuple[float, float, float, float] | None = None
        page_anchor = getattr(entity, "page_anchor", None)
        page_refs = getattr(page_anchor, "page_refs", None) or []
        if page_refs:
            ref0 = page_refs[0]
            raw_page = getattr(ref0, "page", 0)
            page_number = max(1, int(raw_page or 0) + 1)
            bbox = _bbox_from_poly(getattr(ref0, "bounding_poly", None))
        anchors[field_name] = SourceAnchor(
            page_number=page_number,
            bounding_box=bbox,
            snippet=snippet,
            artifact_uri=artifact_uri,
        )

    for idx, page in enumerate(getattr(document, "pages", None) or []):
        page_num = max(1, int(getattr(page, "page_number", 0) or (idx + 1)))
        for ff in getattr(page, "form_fields", None) or []:
            name_layout = getattr(ff, "field_name", None)
            val_layout = getattr(ff, "field_value", None)
            raw_name = (
                (
                    getattr(name_layout, "content", None)
                    or getattr(
                        getattr(name_layout, "text_anchor", None), "content", None
                    )
                    or ""
                )
                .strip()
                .rstrip(":")
            )
            if not raw_name or raw_name in anchors:
                continue
            raw_val = (
                getattr(val_layout, "content", None)
                or getattr(getattr(val_layout, "text_anchor", None), "content", None)
                or ""
            ).strip() or None
            bbox = _bbox_from_poly(getattr(val_layout, "bounding_poly", None))
            anchors[raw_name] = SourceAnchor(
                page_number=page_num,
                bounding_box=bbox,
                snippet=raw_val,
                artifact_uri=artifact_uri,
            )

    return anchors


class DocAIPrepass:
    """The Document AI OCR pre-pass: ``await prepass(resolved) -> str | None``.

    The processor is resolved and validated at construction (``RuntimeError`` without
    one, ``ValueError`` when malformed) so a misconfigured engine fails on the first
    extract before any fetch. The client is injectable (the credential-free test
    seam); ``None`` → a real ``documentai.DocumentProcessorServiceClient`` pointed at
    the processor's regional endpoint, built lazily on first use.
    """

    def __init__(self, processor: str | None = None, *, client=None, log=_LOG) -> None:
        self._ref = resolve_processor(processor)
        self._client = client  # injected fake wins; else built lazily on first use
        self._log = log

    @property
    def processor(self) -> str:
        """Return the configured Document AI processor resource path."""
        return self._ref.name

    @property
    def endpoint(self) -> str:
        """Return the regional Document AI API endpoint hostname."""
        return self._ref.endpoint

    def _get_client(self):
        if self._client is None:
            from google.cloud import documentai

            self._client = documentai.DocumentProcessorServiceClient(
                client_options={"api_endpoint": self.endpoint}
            )
        return self._client

    async def __call__(self, resolved: ResolvedDocument) -> str | None:
        """OCR ``resolved`` once; ``None`` when unsupported or the page reads empty."""
        request = build_process_request(
            self.processor,
            mime_type=resolved.mime_type,
            data=None if resolved.by_reference else resolved.data,
            uri=resolved.uri if resolved.by_reference else None,
        )
        if request is None:
            self._log.debug(
                "Document AI pre-pass skipped: %s is not a Document AI input",
                resolved.mime_type,
            )
            return None
        # Not wrapped: a Document AI error propagates unchanged (no plain-Gemini
        # fallback — that would be a silent capability downgrade).
        response = await maybe_await(
            self._get_client().process_document(request=request)
        )
        text = document_text(getattr(response, "document", None)).strip()
        if not text:
            self._log.warning(
                "Document AI processor %s returned no text for a %s document; "
                "extracting from the document alone",
                self.processor,
                resolved.mime_type,
            )
            return None
        return text
