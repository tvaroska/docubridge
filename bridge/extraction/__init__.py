"""Extraction engine for document field extraction and signal generation.

Extraction is the **capability** axis (``fixture|gemini|docai``, selected by config),
not a local↔gcp store swap (design/ports.md). This layer provides the fixture
(deterministic corpus-based) engine (P1-2), the Gemini engine (P7-6, the deployed
schema-driven engine) and the Document AI OCR pre-pass that turns the Gemini engine
into the ``docai`` engine for scanned/raster documents (design/extraction.md).
"""

from .containers import (
    ContainerUnpackResult,
    ExtractedEnvelopeBody,
    ReplyItemAdvisory,
    UnpackedArtifact,
    apply_advisories_to_requirements,
    parse_html_reply_advisories,
    unpack_submission_container,
)
from .derivations import apply_field_derivations, apply_result_derivations
from .docai import DocAIPrepass
from .fixture import DEFAULT_CORPUS, FixtureExtractor
from .gemini import GeminiExtractor, artifact_resolver
from .office_prepass import (
    DocxRedlineExtract,
    IssuerMismatchResult,
    ResilientSheetExtract,
    ResilientWorkbookExtract,
    detect_issuer_mismatch,
    expand_bundled_extractions,
    extract_docx_with_redlines,
    extract_rtf_text,
    extract_xlsx_resilient,
)
from .resolve import ResolvedDocument, resolve_document

__all__ = [
    "DEFAULT_CORPUS",
    "ContainerUnpackResult",
    "DocAIPrepass",
    "DocxRedlineExtract",
    "ExtractedEnvelopeBody",
    "FixtureExtractor",
    "GeminiExtractor",
    "IssuerMismatchResult",
    "ReplyItemAdvisory",
    "ResilientSheetExtract",
    "ResilientWorkbookExtract",
    "ResolvedDocument",
    "UnpackedArtifact",
    "apply_advisories_to_requirements",
    "apply_field_derivations",
    "apply_result_derivations",
    "artifact_resolver",
    "detect_issuer_mismatch",
    "expand_bundled_extractions",
    "extract_docx_with_redlines",
    "extract_rtf_text",
    "extract_xlsx_resilient",
    "parse_html_reply_advisories",
    "resolve_document",
    "unpack_submission_container",
]
