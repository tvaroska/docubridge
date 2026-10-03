"""Shared corpus loaders for address eval parity tests.

See wiki/evals/address/description.md — this module provides the
design-doc-named helpers: load_corpus, build_extraction_table, load_timeline.
"""
import json
from pathlib import Path

from contract.extraction import ExtractionResult

# Corpus paths
CORPUS_PATH = Path(__file__).resolve().parents[2] / "wiki/evals/address/expected.json"
TIMELINE_PATH = Path(__file__).resolve().parents[2] / "wiki/evals/address/timeline.json"

# Address-proof process label space (skill-owned from P3-3)
CANDIDATE_DOCTYPES: list[str] = ["gov-id", "utility-bill"]

# Corpus party and synthetic identity
PARTY = "jordan-lee"
SYNTHETIC_IDENTITY = "Jordan Lee"


def load_corpus() -> dict:
    """Load and return the full expected.json object."""
    with open(CORPUS_PATH) as f:
        return json.load(f)


def corpus_records() -> list[dict]:
    """Return the list of document records from the corpus."""
    return load_corpus()["documents"]


def build_extraction_table() -> dict[str, ExtractionResult]:
    """Build a mapping from document id to ExtractionResult.

    Mirrors bridge.extraction.fixture._load_corpus structure.
    """
    return {
        record["id"]: ExtractionResult.model_validate(record["extraction"])
        for record in corpus_records()
    }


def load_timeline() -> dict:
    """Load and return the timeline.json object."""
    with open(TIMELINE_PATH) as f:
        return json.load(f)


def record_by_id(doc_id: str) -> dict:
    """Return the corpus record for ``doc_id``; an unknown id is a test error."""
    for record in corpus_records():
        if record["id"] == doc_id:
            return record
    raise AssertionError(f"corpus has no document {doc_id!r}")


def hitl_record_ids() -> list[str]:
    """Return the ids of the records whose expected gate is ``hitl_review``."""
    return [r["id"] for r in corpus_records() if r["expected_gate"] == "hitl_review"]


def timeline_hitl_resolutions() -> dict[str, str]:
    """Return ``{document: resolution}`` for every ``hitl_resolved`` timeline beat."""
    return {
        beat["document"]: beat["resolution"]
        for exchange in load_timeline()["exchanges"]
        for beat in exchange["beats"]
        if beat["event"] == "hitl_resolved"
    }
