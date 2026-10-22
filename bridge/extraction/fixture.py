"""Fixture extraction engine — deterministic corpus-based extraction (SEAM-8).

This module provides the pure extraction engine with no ADK or seam imports,
avoiding import cycles.
"""

import json
from functools import lru_cache
from pathlib import Path

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

    async def extract(self, artifact_name: str) -> ExtractionResult:
        """Extract fields and signals for a document artifact.

        Args:
            artifact_name: The artifact filename or path.

        Returns:
            The extraction result for the document.

        Raises:
            KeyError: If the artifact is not found in the corpus.
        """
        key = _normalize_key(artifact_name)
        corpus = _load_corpus(self._corpus_path)

        if key not in corpus:
            known_ids = sorted(corpus.keys())
            raise KeyError(
                f"no fixture extraction for {artifact_name!r} (key={key!r}); "
                f"known ids: {known_ids}"
            )

        return corpus[key]
