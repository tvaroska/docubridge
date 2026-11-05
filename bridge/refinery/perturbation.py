"""Extraction perturbation stability for the Refinery assay gate (Tier 3).

Krippendorff's alpha and Gwet's AC1/AC2 are computed from real extraction runs
only: every sample document is extracted once per catalog permutation, the
permutations act as raters, and each ``(document, field)`` pair is one item.
Nothing here synthesizes a rating; when no extractor is available the result is
reported as ``evaluated=False`` with ``alpha`` / ``ac2`` left ``None``.
"""

from __future__ import annotations

import asyncio
import inspect
import os
import random
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from pydantic import BaseModel, ConfigDict

from bridge.calibration import gwets_ac2, krippendorff_alpha
from bridge.doctypes import doctype_schemas

LIVE_LLM_ENV = "BRIDGE_REFINERY_LIVE_LLM"
PERTURBATION_DOCS_ENV = "BRIDGE_REFINERY_PERTURBATION_DOCS"
DEFAULT_PERMUTATIONS = 3
DEFAULT_SHUFFLE_SEED = 20260101
STABILITY_THRESHOLD = 0.80
# Absence is its own nominal category: a field one permutation extracts and
# another omits is an instability, not a missing rating to be skipped.
ABSENT = "<absent>"


class FieldDisagreement(BaseModel):
    """One ``(document, field)`` item whose value differed across permutations."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    document: str
    field: str
    values: tuple[str | None, ...]


class PerturbationStabilityResult(BaseModel):
    """Nominal alpha / AC1 over the permutation-by-item extraction matrix."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    evaluated: bool = False
    alpha: float | None = None
    ac2: float | None = None
    n_items: int = 0
    n_permutations: int = 0
    n_documents: int = 0
    disagreements: tuple[FieldDisagreement, ...] = ()
    reason: str = ""

    @property
    def passes(self) -> bool:
        """True iff evaluated and both agreement coefficients clear the threshold."""
        return (
            self.evaluated
            and self.alpha is not None
            and self.ac2 is not None
            and self.alpha >= STABILITY_THRESHOLD
            and self.ac2 >= STABILITY_THRESHOLD
        )

    def describe(self, *, max_unstable: int = 5) -> str:
        """One clause for the assay summary; never prints a number it did not measure."""
        if not self.evaluated or self.alpha is None or self.ac2 is None:
            return f"perturbation stability: not evaluated ({self.reason})"
        unstable = ", ".join(
            f"{d.document}:{d.field}" for d in self.disagreements[:max_unstable]
        )
        return (
            f"perturbation stability: alpha={self.alpha:.2f}, AC2={self.ac2:.2f} "
            f"over {self.n_items} document x field items x {self.n_permutations} "
            "catalog permutations" + (f" (unstable: {unstable})" if unstable else "")
        )


def not_evaluated(reason: str) -> PerturbationStabilityResult:
    """The honest default: no coefficient, with the reason it was not measured."""
    return PerturbationStabilityResult(evaluated=False, reason=reason)


def build_catalog_permutations(
    doctypes: Sequence[str],
    *,
    permutations: int = DEFAULT_PERMUTATIONS,
    seed: int = DEFAULT_SHUFFLE_SEED,
) -> tuple[tuple[str, ...], ...]:
    """Forward, reversed, then seeded shuffles — distinct orders, fixed across runs."""
    forward = tuple(sorted(doctypes))
    if permutations < 2:
        raise ValueError("permutations must be >= 2 to form a rater panel")
    orders: list[tuple[str, ...]] = [forward, tuple(reversed(forward))]
    rng = random.Random(seed)
    attempts = 0
    while len(orders) < permutations and attempts < permutations * 20:
        attempts += 1
        shuffled = list(forward)
        rng.shuffle(shuffled)
        candidate = tuple(shuffled)
        if candidate not in orders or len(forward) < 3:
            orders.append(candidate)
    return tuple(orders[:permutations])


def normalize_value(value: Any) -> str | None:
    """String-equal after strip/casefold; ``None`` marks an absent field."""
    if value is None:
        return None
    text = str(value).strip().casefold()
    return text if text else None


def flatten_extraction(result: Any) -> dict[str, str | None]:
    """``doctype``, ``issuer`` and every ``key_fields`` entry of one extraction."""
    fields = getattr(result, "fields", None)
    if fields is None and isinstance(result, dict):
        fields = result.get("fields")
    if fields is None:
        return {}
    get = (
        fields.get
        if isinstance(fields, dict)
        else lambda name, default=None: getattr(fields, name, default)
    )
    flat: dict[str, str | None] = {
        "doctype": normalize_value(get("doctype")),
        "issuer": normalize_value(get("issuer")),
    }
    key_fields = get("key_fields") or {}
    for name, value in dict(key_fields).items():
        flat[f"key_fields.{name}"] = normalize_value(value)
    return flat


def _run_extract(extractor: Any, document: str, order: Sequence[str]) -> Any:
    value = extractor.extract(document, catalog_order=tuple(order))
    if not inspect.isawaitable(value):
        return value
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(value)
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, value).result()


def stability_from_observations(
    observations: Sequence[Sequence[dict[str, str | None]]],
    *,
    documents: Sequence[str],
) -> PerturbationStabilityResult:
    """Pure half: ``observations[doc][permutation]`` → nominal alpha / AC1."""
    if not observations:
        return not_evaluated("no sample documents")
    n_perm = len(observations[0])
    if n_perm < 2:
        return not_evaluated("fewer than 2 permutations")

    rows: list[tuple[str, str, tuple[str | None, ...]]] = []
    for doc_name, per_perm in zip(documents, observations, strict=True):
        fields = sorted({name for flat in per_perm for name in flat})
        for field in fields:
            rows.append((doc_name, field, tuple(flat.get(field) for flat in per_perm)))
    if not rows:
        return not_evaluated("extractions produced no fields")

    codes: dict[str, float] = {}
    matrix: list[list[float]] = []
    for _, _, values in rows:
        matrix.append(
            [
                codes.setdefault(ABSENT if v is None else v, float(len(codes)))
                for v in values
            ]
        )
    scale = sorted(codes.values())
    if len(scale) < 2:
        alpha, ac2 = 1.0, 1.0
    else:
        alpha = krippendorff_alpha(matrix, metric="nominal", scale_values=scale)
        ac2 = gwets_ac2(matrix, scale_values=scale, metric="nominal")
    disagreements = tuple(
        FieldDisagreement(document=doc, field=field, values=values)
        for doc, field, values in rows
        if len(set(values)) > 1
    )
    return PerturbationStabilityResult(
        evaluated=True,
        alpha=round(alpha, 4),
        ac2=round(ac2, 4),
        n_items=len(rows),
        n_permutations=n_perm,
        n_documents=len(observations),
        disagreements=disagreements,
    )


def compute_perturbation_stability(
    extractor: Any,
    documents: Sequence[str],
    *,
    permutations: int = DEFAULT_PERMUTATIONS,
    doctypes: Sequence[str] | None = None,
    seed: int = DEFAULT_SHUFFLE_SEED,
) -> PerturbationStabilityResult:
    """Extract every document under each catalog permutation and score agreement.

    ``extractor`` is anything exposing ``extract(artifact_name, *, catalog_order=...)``
    (sync or async, returning an ``ExtractionResult``-shaped object).
    """
    if extractor is None:
        return not_evaluated("no live extractor")
    docs = [d for d in documents if str(d).strip()]
    if not docs:
        return not_evaluated("no sample documents")
    names = list(doctypes) if doctypes is not None else sorted(doctype_schemas())
    orders = build_catalog_permutations(names, permutations=permutations, seed=seed)
    observations = [
        [flatten_extraction(_run_extract(extractor, doc, order)) for order in orders]
        for doc in docs
    ]
    return stability_from_observations(observations, documents=docs)


def resolve_perturbation_inputs(
    extractor: Any = None,
    documents: Sequence[str] = (),
) -> tuple[Any, tuple[str, ...]]:
    """Injected extractor/documents win; else the live-LLM env opt-in builds one.

    Mirrors the onboarding inducer: nothing is instantiated unless an extractor is
    injected or ``BRIDGE_REFINERY_LIVE_LLM=1`` is set, so offline runs never touch
    credentials and report the tier as not evaluated.
    """
    docs = tuple(d for d in documents if str(d).strip())
    if not docs:
        raw = os.environ.get(PERTURBATION_DOCS_ENV, "")
        docs = tuple(part.strip() for part in raw.split(",") if part.strip())
    if extractor is not None:
        return extractor, docs
    if os.environ.get(LIVE_LLM_ENV, "").strip() != "1" or not docs:
        return None, docs
    from bridge.extraction.gemini import GeminiExtractor

    return GeminiExtractor(), docs


__all__ = [
    "ABSENT",
    "DEFAULT_PERMUTATIONS",
    "LIVE_LLM_ENV",
    "PERTURBATION_DOCS_ENV",
    "STABILITY_THRESHOLD",
    "FieldDisagreement",
    "PerturbationStabilityResult",
    "build_catalog_permutations",
    "compute_perturbation_stability",
    "flatten_extraction",
    "normalize_value",
    "not_evaluated",
    "resolve_perturbation_inputs",
    "stability_from_observations",
]
