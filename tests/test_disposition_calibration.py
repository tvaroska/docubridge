"""Disposition-threshold calibration: the eval-corpus corridor + the review contract.

Pins the corridor the gate-bearing corpus implies (a deliberate tripwire: a corpus
change that moves it must update this test and the docs table together), proves the
corridor is exact (inside → every corpus gate reproduces; just outside → some gate
flips), and guards that every process skill's policy thresholds equal the code
defaults and lie inside the corridor (design/disposition.md, "The review contract").
"""

import json
import math

import pytest

from bridge.calibration import (
    GATE_CORPORA,
    REPO_ROOT,
    corpus_points,
    main,
    process_policy_thresholds,
)
from bridge.disposition import (
    DEFAULT_THRESHOLDS,
    Bound,
    CorridorPoint,
    DispositionThresholds,
    threshold_corridor,
)
from bridge.signals import Signals, dispose_signals, signals_from_result
from contract.enums import Gate
from contract.extraction import ExtractionResult


def _corpus_signals() -> list[tuple[str, Signals, Gate]]:
    """(record id, the live gate's Signals, expected gate) for every gate-bearing record."""
    out = []
    for rel, cands in GATE_CORPORA:
        for doc in json.loads((REPO_ROOT / rel).read_text())["documents"]:
            r = ExtractionResult.model_validate(doc["extraction"])
            s = signals_from_result(r, candidate_doctypes=list(cands))
            gate = doc.get("expected_gate") or doc["expected_terminal"]["gate"]
            out.append((doc["id"], s, Gate(gate)))
    return out


def _pt(rid, c, gate, *, legible=True, in_label_space=True, flagged=False):
    return CorridorPoint(
        record_id=rid,
        overall_confidence=c,
        legible=legible,
        in_label_space=in_label_space,
        flagged=flagged,
        expected_gate=gate,
    )


# --- the corpus corridor ---------------------------------------------------------


def test_corpus_corridor_values():
    """The tripwire: 10 points; resubmit_below in (-, 0.72]; auto_approve_at in (0.72, 0.94]."""
    c = threshold_corridor(corpus_points())
    assert c.points == 10
    assert c.resubmit_below == Bound(gt=None, le=0.72, le_by="bill-aquautil-clear")
    assert c.auto_approve_at == Bound(
        gt=0.72,
        gt_by="bill-aquautil-clear",
        le=0.94,
        le_by="benefits-conformance-ci-usd",
    )
    assert c.feasible


def test_hi_conformance_record_is_flagged_via_field_reviews():
    """The HI record has flagged_fields == [] and is flagged only through field_reviews;
    reading raw flagged_fields would shrink the corridor to (0.90, 0.94]."""
    pts = {p.record_id: p for p in corpus_points()}
    hi = pts["benefits-conformance-hi-usd-flagged"]
    assert hi.flagged is True
    assert hi.expected_gate is Gate.hitl_review


def test_default_thresholds_inside_corridor():
    assert threshold_corridor(corpus_points()).contains(DEFAULT_THRESHOLDS)


def test_every_process_policy_equals_defaults_and_is_inside():
    """Every process skill's disposition: block == the code defaults, inside the corridor."""
    policies = process_policy_thresholds()
    assert set(policies) == {
        "address-proof",
        "benefits-procurement",
        "benefits-rfp",
        "commercial-loan-underwriting",
    }
    corridor = threshold_corridor(corpus_points())
    for name, t in policies.items():
        assert t == DEFAULT_THRESHOLDS, name
        assert corridor.contains(t), name


@pytest.mark.parametrize("auto", [0.73, 0.85, 0.94])
@pytest.mark.parametrize("resubmit", [0.0, 0.55, 0.72])
def test_corridor_inside_reproduces_every_corpus_gate(auto, resubmit):
    """Sufficiency: any thresholds inside the corridor reproduce every expected gate."""
    t = DispositionThresholds(auto_approve_at=auto, resubmit_below=resubmit)
    assert threshold_corridor(corpus_points()).contains(t)
    for rid, s, gate in _corpus_signals():
        assert dispose_signals(s, t).gate is gate, (rid, t)


@pytest.mark.parametrize(
    "auto,resubmit",
    [(0.72, 0.55), (0.95, 0.55), (0.85, 0.73)],
)
def test_corridor_just_outside_flips_some_corpus_gate(auto, resubmit):
    """Exactness: just outside the corridor, at least one corpus gate changes."""
    t = DispositionThresholds(auto_approve_at=auto, resubmit_below=resubmit)
    assert not threshold_corridor(corpus_points()).contains(t)
    flipped = [
        rid
        for rid, s, gate in _corpus_signals()
        if dispose_signals(s, t).gate is not gate
    ]
    assert flipped, t


# --- threshold_corridor on synthetic points ----------------------------------------


def test_legible_resubmit_sets_lower_bound():
    c = threshold_corridor(
        [_pt("a", 0.4, Gate.resubmit), _pt("b", 0.45, Gate.resubmit)]
    )
    assert c.resubmit_below == Bound(gt=0.45, gt_by="b")
    assert c.auto_approve_at == Bound()


def test_ties_keep_first_record():
    c = threshold_corridor(
        [_pt("a", 0.9, Gate.auto_approve), _pt("b", 0.9, Gate.auto_approve)]
    )
    assert c.auto_approve_at.le_by == "a"
    assert c.resubmit_below.le_by == "a"


def test_illegible_point_constrains_nothing():
    c = threshold_corridor([_pt("x", 0.99, Gate.resubmit, legible=False)])
    assert c.resubmit_below == Bound()
    assert c.auto_approve_at == Bound()
    assert c.points == 1


def test_out_of_label_space_point_constrains_nothing():
    c = threshold_corridor([_pt("x", 0.1, Gate.unsupported, in_label_space=False)])
    assert c.resubmit_below == Bound() and c.auto_approve_at == Bound()


def test_flagged_hitl_bounds_only_resubmit():
    c = threshold_corridor([_pt("f", 0.93, Gate.hitl_review, flagged=True)])
    assert c.resubmit_below == Bound(le=0.93, le_by="f")
    assert c.auto_approve_at == Bound()


def test_flagged_auto_approve_raises():
    with pytest.raises(ValueError, match="flagged"):
        threshold_corridor([_pt("f", 0.99, Gate.auto_approve, flagged=True)])


def test_out_of_label_space_expecting_hitl_raises():
    with pytest.raises(ValueError, match="outside the label space"):
        threshold_corridor([_pt("p", 0.9, Gate.hitl_review, in_label_space=False)])


def test_in_label_space_expecting_unsupported_raises():
    with pytest.raises(ValueError, match="inside the label space"):
        threshold_corridor([_pt("p", 0.9, Gate.unsupported)])


def test_illegible_expecting_non_resubmit_raises():
    with pytest.raises(ValueError, match="illegible"):
        threshold_corridor([_pt("p", 0.9, Gate.hitl_review, legible=False)])


def test_infeasible_pair():
    """resubmit_below > 0.8 and auto_approve_at <= 0.7 cannot both hold."""
    c = threshold_corridor(
        [_pt("r", 0.8, Gate.resubmit), _pt("a", 0.7, Gate.auto_approve)]
    )
    assert c.resubmit_below.gt == 0.8
    assert c.auto_approve_at.le == 0.7
    assert c.feasible is False


def test_bound_contains_edges_and_nan():
    b = Bound(gt=0.72, le=0.94)
    assert not b.contains(0.72)
    assert b.contains(0.94)
    assert not b.contains(math.nan)
    assert Bound().contains(0.0)


# --- threshold validation ----------------------------------------------------------


@pytest.mark.parametrize(
    "auto,resubmit",
    [(0.5, 0.6), (1.1, 0.5), (0.85, -0.1), (math.nan, 0.5), (0.85, math.nan)],
)
def test_thresholds_validation_refuses(auto, resubmit):
    with pytest.raises(ValueError, match="resubmit_below <= auto_approve_at"):
        DispositionThresholds(auto_approve_at=auto, resubmit_below=resubmit)


def test_thresholds_validation_allows_equal_and_extremes():
    DispositionThresholds(0.7, 0.7)
    DispositionThresholds(1.0, 0.0)
    DispositionThresholds(0.0, 0.0)


# --- the CLI ------------------------------------------------------------------------


def test_cli_exit_zero_and_reports(capsys):
    assert main([]) == 0
    out = capsys.readouterr().out
    assert "corpus points: 10" in out
    assert "0.72 (by bill-aquautil-clear)" in out
    assert "0.94 (by benefits-conformance-ci-usd)" in out
    assert "DEFAULT_THRESHOLDS  auto_approve_at=0.85 resubmit_below=0.55  inside" in out
    for name in ("address-proof", "benefits-procurement", "benefits-rfp"):
        assert f"policy {name}" in out
    assert "OUTSIDE" not in out


def test_cli_candidate_outside_exits_one(capsys):
    assert main(["--auto-approve-at", "0.95", "--resubmit-below", "0.55"]) == 1
    assert "OUTSIDE" in capsys.readouterr().out


def test_cli_corpus_cannot_separate_085_from_090():
    """A consumer's 0.9 is inside the corridor too: the corpus cannot tell them apart."""
    assert main(["--auto-approve-at", "0.9", "--resubmit-below", "0.55"]) == 0


def test_cli_invalid_candidate_exits_one():
    assert main(["--auto-approve-at", "0.5", "--resubmit-below", "0.6"]) == 1
