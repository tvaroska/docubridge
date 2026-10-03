"""Disposition-threshold calibration against the eval corpus (operator + test tool).

Every gate-bearing corpus record bounds the gate thresholds through the gate's own
precedence; the legal region is their intersection, the *corridor*
(``bridge.disposition.threshold_corridor``). This module reads the corpus and the
process skills' ``policy.yaml`` ``disposition:`` blocks and checks that the code
defaults and every process policy lie inside the corridor and agree with each other.

    uv run python -m bridge.calibration
    uv run python -m bridge.calibration --auto-approve-at 0.9 --resubmit-below 0.55

Exit 0 iff the corridor is feasible, ``DEFAULT_THRESHOLDS`` is inside it, every
process policy is inside it and equal to the defaults, and the candidate pair (if
given) is inside it. Normative text: design/disposition.md, "The review contract".

Imports ``bridge.*`` + ``contract.*`` + stdlib/``pyyaml`` only — NEVER ``agents/``.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path

import yaml

from bridge.disposition import (
    DEFAULT_THRESHOLDS,
    Bound,
    CorridorPoint,
    DispositionThresholds,
    ThresholdCorridor,
    threshold_corridor,
)
from bridge.policy import load_disposition_policy
from bridge.signals import signals_from_result
from bridge.sla import SKILLS_DIR
from contract.enums import Gate
from contract.extraction import ExtractionResult

REPO_ROOT = Path(__file__).resolve().parents[1]

# The gate-bearing corpora: (path relative to the repo root, the process's candidate
# doctypes). ``wiki/evals/benefits/expected.json`` and ``wiki/evals/rfp/expected.json``
# carry no expected gate, so they cannot bound the thresholds and are excluded on purpose.
GATE_CORPORA: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("wiki/evals/address/expected.json", ("gov-id", "utility-bill")),
    ("wiki/evals/benefits/conformance.json", ("benefits-quote",)),
)


def _expected_gate(doc: dict) -> Gate:
    raw = doc.get("expected_gate") or doc["expected_terminal"]["gate"]
    return Gate(raw)


def corpus_points(
    corpora: Iterable[tuple[str, Sequence[str]]] = GATE_CORPORA,
    root: Path = REPO_ROOT,
) -> list[CorridorPoint]:
    """Project every gate-bearing corpus record onto the signals the gate reads.

    Flags come from ``signals_from_result`` (raw ``flagged_fields`` or the
    ``field_reviews`` fallback) — exactly what the live gate sees.
    """
    points: list[CorridorPoint] = []
    for rel, cands in corpora:
        corpus = json.loads((root / rel).read_text())
        for doc in corpus["documents"]:
            result = ExtractionResult.model_validate(doc["extraction"])
            s = signals_from_result(result, candidate_doctypes=list(cands))
            points.append(
                CorridorPoint(
                    record_id=doc["id"],
                    overall_confidence=s.overall_confidence,
                    legible=s.legible,
                    in_label_space=s.type_match,
                    flagged=bool(s.flagged_fields),
                    expected_gate=_expected_gate(doc),
                )
            )
    return points


def process_policy_thresholds(
    skills_dir: Path = SKILLS_DIR,
) -> dict[str, DispositionThresholds]:
    """Every process skill whose ``policy.yaml`` declares a ``disposition:`` block,
    mapped to its thresholds (loaded through ``load_disposition_policy``)."""
    out: dict[str, DispositionThresholds] = {}
    for policy_path in sorted(skills_dir.glob("*/assets/policy.yaml")):
        doc = yaml.safe_load(policy_path.read_text()) or {}
        if "disposition" not in doc:
            continue
        name = policy_path.parent.parent.name
        out[name] = load_disposition_policy(name, skills_dir=skills_dir).thresholds()
    return out


def _fmt_bound(name: str, b: Bound, *, no_lower: str) -> str:
    lo = f"{b.gt:.2f} (by {b.gt_by})" if b.gt is not None else "unbounded"
    hi = f"{b.le:.2f} (by {b.le_by})" if b.le is not None else "unbounded"
    line = f"  {name:<16} ({lo}, {hi}]"
    if b.gt is None:
        line += f"\n  {'':<16} no lower bound: {no_lower}"
    return line


def _fmt_t(t: DispositionThresholds) -> str:
    return f"auto_approve_at={t.auto_approve_at:.2f} resubmit_below={t.resubmit_below:.2f}"


def _verdict(c: ThresholdCorridor, t: DispositionThresholds) -> str:
    return "inside" if c.contains(t) else "OUTSIDE"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m bridge.calibration",
        description="Check the disposition thresholds against the eval-corpus corridor.",
    )
    parser.add_argument("--auto-approve-at", type=float, help="candidate auto_approve_at")
    parser.add_argument("--resubmit-below", type=float, help="candidate resubmit_below")
    args = parser.parse_args(argv)

    candidate: DispositionThresholds | None = None
    if (args.auto_approve_at is None) != (args.resubmit_below is None):
        parser.error("--auto-approve-at and --resubmit-below go together")
    if args.auto_approve_at is not None:
        try:
            candidate = DispositionThresholds(
                auto_approve_at=args.auto_approve_at,
                resubmit_below=args.resubmit_below,
            )
        except ValueError as e:
            print(f"candidate invalid: {e}", file=sys.stderr)
            return 1

    per_corpus = [(rel, corpus_points([(rel, cands)])) for rel, cands in GATE_CORPORA]
    corridor = threshold_corridor(p for _, pts in per_corpus for p in pts)
    ok = corridor.feasible

    print(f"corpus points: {corridor.points}")
    for rel, pts in per_corpus:
        print(f"  {rel}: {len(pts)}")
    print(f"corridor ({'feasible' if corridor.feasible else 'INFEASIBLE'}):")
    print(
        _fmt_bound(
            "resubmit_below",
            corridor.resubmit_below,
            no_lower="no legible record expects resubmit (illegible records are "
            "caught by the legibility gate first)",
        )
    )
    print(
        _fmt_bound(
            "auto_approve_at",
            corridor.auto_approve_at,
            no_lower="no unflagged legible record expects hitl_review",
        )
    )

    v = _verdict(corridor, DEFAULT_THRESHOLDS)
    ok = ok and v == "inside"
    print(f"DEFAULT_THRESHOLDS  {_fmt_t(DEFAULT_THRESHOLDS)}  {v}")

    policies = process_policy_thresholds()
    if not policies:
        print("process policies: none found")
        ok = False
    for name, t in policies.items():
        v = _verdict(corridor, t)
        eq = t == DEFAULT_THRESHOLDS
        ok = ok and v == "inside" and eq
        print(
            f"policy {name:<22} {_fmt_t(t)}  {v}  "
            f"{'== defaults' if eq else '!= DEFAULTS'}"
        )

    if candidate is not None:
        v = _verdict(corridor, candidate)
        ok = ok and v == "inside"
        print(f"candidate           {_fmt_t(candidate)}  {v}")

    print("OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
