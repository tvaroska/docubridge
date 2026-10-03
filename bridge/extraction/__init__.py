"""Extraction engine for document field extraction and signal generation.

Extraction is the **capability** axis (``fixture|gemini|docai``, selected by config),
not a local↔gcp store swap (design/seams.md). This layer provides the fixture
(deterministic corpus-based) engine (P1-2), the Gemini engine (P7-6, the deployed
schema-driven engine) and the Document AI OCR pre-pass that turns the Gemini engine
into the ``docai`` engine for scanned/raster documents (design/docai-prepass.md).
"""

from .docai import DocAIPrepass
from .fixture import DEFAULT_CORPUS, FixtureExtractor
from .gemini import GeminiExtractor, artifact_resolver
from .resolve import ResolvedDocument, resolve_document

__all__ = [
    "DEFAULT_CORPUS",
    "DocAIPrepass",
    "FixtureExtractor",
    "GeminiExtractor",
    "ResolvedDocument",
    "artifact_resolver",
    "resolve_document",
]
