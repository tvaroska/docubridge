"""Fixture extraction engine — deterministic corpus-based extraction (PORT-8).

This module provides the pure extraction engine with no ADK or port imports,
avoiding import cycles.
"""

import json
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path

from bridge.extraction.derivations import apply_result_derivations
from contract.extraction import ExtractionResult

# Default corpus path (from bridge/extraction/fixture.py: parents[0]=extraction, [1]=bridge, [2]=repo root)
DEFAULT_CORPUS = (
    Path(__file__).resolve().parents[2] / "wiki/evals/address/expected.json"
)

# Supported image/document suffixes for key normalization
_KEY_SUFFIXES = (".svg", ".png", ".jpg", ".jpeg", ".pdf")


@lru_cache
def _load_corpus(corpus_path: str) -> dict[str, ExtractionResult]:
    """Load and cache the extraction corpus.

    Args:
        corpus_path: Path to the corpus JSON file.

    Returns:
        A dict mapping document id to ExtractionResult.
    """
    with open(corpus_path) as f:
        data = json.load(f)

    return {
        record["id"]: ExtractionResult.model_validate(record["extraction"])
        for record in data["documents"]
    }


def _normalize_key(artifact_name: str) -> str:
    """Normalize an artifact name to a lookup key.

    Strips directory path and trailing image suffixes.

    Args:
        artifact_name: The artifact filename or path (e.g., "images/gov-id-clean.svg" or "gov-id-clean").

    Returns:
        The normalized key (e.g., "gov-id-clean").
    """
    key = Path(artifact_name).name
    for suffix in _KEY_SUFFIXES:
        if key.endswith(suffix):
            key = key[: -len(suffix)]
            break
    return key


class FixtureExtractor:
    """Deterministic fixture extraction engine.

    Loads extraction results from the eval corpus and returns them
    deterministically for known document ids.
    """

    def __init__(self, corpus_path: Path | str = DEFAULT_CORPUS) -> None:
        """Initialize the fixture extractor.

        Args:
            corpus_path: Path to the corpus JSON file (defaults to the address eval corpus).
        """
        self._corpus_path = str(corpus_path)

    async def extract(
        self,
        artifact_name: str,
        *,
        target_doctype: str | None = None,
        candidate_doctypes: Sequence[str] | None = None,
        requested_fields: Sequence[str] | None = None,
        context_set: str | None = None,
        ontology_revision: str | None = None,
    ) -> ExtractionResult:
        """Extract fields and signals for a document artifact.

        Args:
            artifact_name: The artifact filename or path.
            target_doctype: Optional single expected doctype hint.
            candidate_doctypes: Optional candidate doctype hints.
            requested_fields: Optional explicit field subset to extract.
            context_set: Optional domain context set name.
            ontology_revision: Optional ontology revision ID to pin.

        Returns:
            The extraction result for the document.

        Raises:
            KeyError: If the artifact is not found in the corpus.
        """
        _ = (target_doctype, candidate_doctypes, context_set)
        key = _normalize_key(artifact_name)
        corpus = _load_corpus(self._corpus_path)

        if key not in corpus:
            known_ids = sorted(corpus.keys())
            raise KeyError(
                f"no fixture extraction for {artifact_name!r} (key={key!r}); "
                f"known ids: {known_ids}"
            )

        base_result = corpus[key]
        if requested_fields is not None or ontology_revision is not None:
            return apply_result_derivations(
                base_result,
                requested_fields=requested_fields,
                ontology_revision_id=ontology_revision,
            )
        return base_result
