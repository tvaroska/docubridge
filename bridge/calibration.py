"""Disposition-threshold corridor & Statistical Autorater / HITL Calibration Math.

Provides:
1. Disposition-threshold calibration against the eval corpus (operator + test tool):
   Every gate-bearing corpus record bounds the gate thresholds through the gate's own
   precedence; the legal region is their intersection, the *corridor*
   (``bridge.disposition.threshold_corridor``). This module reads the corpus and the
   process skills' ``policy.yaml`` ``disposition:`` blocks and checks that the code
   defaults and every process policy lie inside the corridor and agree with each other.
2. Pure-Python Statistical Calibration, Inter-Rater Reliability (IRR), and Appropriate
   Reliance Math (ported from ``agent_evals.calibration``):
   - Krippendorff's alpha (nominal, ordinal, and interval distance metrics)
   - Gwet's AC1 / AC2 (prevalence-robust chance-corrected agreement; arXiv:2606.19544)
   - Rogan-Gladen / Prediction-Powered Inference (PPI) debiased prevalence estimator (arXiv:2511.21140)
   - Effective Panel Size under correlated judge errors (arXiv:2605.29800)
   - Human/Agent Swap alignment test (symmetrical rater-swap test)
   - Agreement^k, pass@k, and pass^k (tau-bench / StochBench)
   - Item-level Negative Flip Rate (NFR; arXiv:2608.13607)
   - Two-Sided Appropriate Reliance (RAIR & RSR; Schemmer et al. 2023)
   - Stochastic Threshold Corridor (mean / std / p05 / p95 assertion helper)

    uv run python -m bridge.calibration
    uv run python -m bridge.calibration --auto-approve-at 0.9 --resubmit-below 0.55

Exit 0 iff the corridor is feasible, ``DEFAULT_THRESHOLDS`` is inside it, every
process policy is inside it and equal to the defaults, and the candidate pair (if
given) is inside it. Normative text: design/disposition.md, "The review contract".

Imports ``bridge.*`` + ``contract.*`` + ``pydantic`` + stdlib/``pyyaml`` only — NEVER ``agents/``.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

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

# Canonical blind SME calibration set (200 positive + 50 negative paired human/autorater
# labels) used for Rogan-Gladen / PPI bias correction.
CALIBRATION_HUMAN_LABELS: tuple[int, ...] = (
    *([1] * 197),
    *([1] * 3),
    *([0] * 48),
    *([0] * 2),
)
CALIBRATION_JUDGE_LABELS: tuple[int, ...] = (
    *([1] * 197),
    *([0] * 3),
    *([0] * 48),
    *([1] * 2),
)


class RoganGladenResult(BaseModel):
    """Debiased pass-rate estimate and 95% CI using the Rogan-Gladen / PPI estimator."""

    raw_judge_pass_rate: float = Field(
        description="Uncalibrated judge pass rate p_hat_judge"
    )
    debiased_pass_rate: float = Field(
        description="Clipped Rogan-Gladen prevalence estimate theta_hat"
    )
    tpr_sensitivity: float = Field(description="Judge sensitivity P(Judge=1 | Human=1)")
    tnr_specificity: float = Field(description="Judge specificity P(Judge=0 | Human=0)")
    youden_j: float = Field(description="Youden's J statistic (TPR + TNR - 1)")
    ci_95_lower: float = Field(description="95% confidence interval lower bound")
    ci_95_upper: float = Field(description="95% confidence interval upper bound")
    n_unlabeled: int = Field(
        description="Number of production traces evaluated by the judge"
    )
    n_calibration_pos: int = Field(
        description="Number of human-positive calibration items"
    )
    n_calibration_neg: int = Field(
        description="Number of human-negative calibration items"
    )


class HumanAgentSwapResult(BaseModel):
    """Result of replacing each human rater with the autorater in an M-rater panel."""

    human_baseline_alpha: float = Field(
        description="Krippendorff's alpha among the M human raters"
    )
    mean_swapped_alpha: float = Field(
        description="Mean alpha when swapping 1 human with the autorater"
    )
    min_swapped_alpha: float = Field(
        description="Minimum alpha across all M single-rater swaps"
    )
    swap_delta: float = Field(description="mean_swapped_alpha - human_baseline_alpha")
    passes_parity_gate: bool = Field(
        description="True if mean_swapped_alpha >= 0.80 and swap_delta >= -0.03"
    )


class AppropriateRelianceResult(BaseModel):
    """Two-sided human-in-the-loop oversight metrics (Schemmer et al. 2023)."""

    rair: float = Field(
        description="Relative AI Reliance: P(Correct | AI Correct, Human Initial Wrong)"
    )
    rsr: float = Field(
        description="Relative Self-Reliance: P(Correct | AI Wrong, Human Initial Correct)"
    )
    over_reliance_rate: float = Field(
        description="1 - RSR: rate at which human rubber-stamps wrong AI outputs"
    )
    under_reliance_rate: float = Field(
        description="1 - RAIR: rate at which human overrides correct AI outputs"
    )
    passes_oversight_gate: bool = Field(
        description="True if both RAIR >= 0.80 and RSR >= 0.80"
    )


class CorridorCheckResult(BaseModel):
    """Evaluation result for stochastic metric corridors across N trials."""

    n_trials: int
    sample_mean: float
    sample_std: float
    p05: float
    p95: float
    passes: bool
    failures: list[str] = Field(default_factory=list)


class CalibrationSummary(BaseModel):
    """Unified calibration report combining IRR, prevalence-robust AC2, and directional bias."""

    n_items: int
    krippendorff_alpha_ordinal: float
    gwets_ac2_ordinal: float
    mean_directional_bias: float = Field(
        description="Mean(judge - human); positive means judge is lenient"
    )
    mae: float
    passes_production_gate: bool = Field(
        description="True if (alpha >= 0.80 or ac2 >= 0.80) and abs(mean_directional_bias) <= 0.15"
    )


def _metric_distance_sq(
    v1: float,
    v2: float,
    metric: str,
    scale_values: Sequence[float],
) -> float:
    """Compute squared difference d^2(v1, v2) for nominal, interval, or ordinal scales."""
    if v1 == v2:
        return 0.0
    if metric == "nominal":
        return 1.0
    if metric == "interval":
        return float((v1 - v2) ** 2)
    if metric == "ordinal":
        sorted_vals = sorted(scale_values)
        max_span = sorted_vals[-1] - sorted_vals[0]
        if max_span == 0:
            return 0.0
        return float(((v1 - v2) / max_span) ** 2)
    raise ValueError(
        f"Unsupported metric '{metric}'. Expected 'nominal', 'ordinal', or 'interval'."
    )


def krippendorff_alpha(
    ratings_matrix: Sequence[Sequence[float | None]],
    metric: str = "ordinal",
    scale_values: Sequence[float] | None = None,
) -> float:
    """Compute Krippendorff's alpha for N items x M raters (supports missing values as None).

    Formula:
        alpha = 1 - (D_o / D_e)
    where D_o is observed disagreement within items and D_e is expected disagreement
    across all pairable values in the reliability matrix.
    """
    valid_units: list[list[float]] = []
    all_values: list[float] = []
    for row in ratings_matrix:
        unit_vals = [float(v) for v in row if v is not None]
        if len(unit_vals) >= 2:
            valid_units.append(unit_vals)
            all_values.extend(unit_vals)

    n_total = len(all_values)
    if n_total < 2:
        return 0.0

    inferred_scale = sorted(
        set(scale_values if scale_values is not None else all_values)
    )
    if len(inferred_scale) <= 1:
        return 1.0

    observed_sum = 0.0
    for unit in valid_units:
        m_u = len(unit)
        unit_disagreement = 0.0
        for i in range(m_u):
            for j in range(i + 1, m_u):
                unit_disagreement += 2.0 * _metric_distance_sq(
                    unit[i], unit[j], metric, inferred_scale
                )
        observed_sum += unit_disagreement / (m_u - 1.0)
    d_o = observed_sum / n_total

    expected_sum = 0.0
    for i in range(n_total):
        for j in range(i + 1, n_total):
            expected_sum += 2.0 * _metric_distance_sq(
                all_values[i], all_values[j], metric, inferred_scale
            )
    d_e = expected_sum / (n_total * (n_total - 1.0))

    if d_e == 0.0:
        return 1.0
    return float(1.0 - (d_o / d_e))


def gwets_ac2(
    ratings_matrix: Sequence[Sequence[float | None]],
    scale_values: Sequence[float],
    metric: str = "ordinal",
) -> float:
    """Compute Gwet's AC1 (nominal) or AC2 (ordinal/interval) chance-corrected agreement.

    Unlike Cohen's kappa and Krippendorff's alpha, Gwet's AC1/AC2 remains stable
    under high-prevalence distributions (e.g. 90%+ pass rates; arXiv:2606.19544).
    """
    q = len(scale_values)
    if q < 2:
        raise ValueError("scale_values must contain at least 2 distinct categories.")
    sorted_scale = sorted(float(v) for v in scale_values)

    valid_units: list[list[float]] = []
    category_counts: dict[float, float] = {v: 0.0 for v in sorted_scale}
    for row in ratings_matrix:
        unit_vals = [float(v) for v in row if v is not None]
        if len(unit_vals) >= 2:
            valid_units.append(unit_vals)
            for v in unit_vals:
                if v not in category_counts:
                    raise ValueError(
                        f"Rating {v} not present in scale_values {sorted_scale}"
                    )
                category_counts[v] += 1.0 / len(unit_vals)

    n_units = len(valid_units)
    if n_units == 0:
        return 0.0

    d_o_sum = 0.0
    for unit in valid_units:
        m_u = len(unit)
        pair_disagreement = 0.0
        for i in range(m_u):
            for j in range(i + 1, m_u):
                pair_disagreement += 2.0 * _metric_distance_sq(
                    unit[i], unit[j], metric, sorted_scale
                )
        d_o_sum += pair_disagreement / (m_u * (m_u - 1.0))
    d_o = d_o_sum / n_units
    p_a = 1.0 - d_o

    pi = {k: category_counts[k] / n_units for k in sorted_scale}
    total_agreement_weight = 0.0
    for k1 in sorted_scale:
        for k2 in sorted_scale:
            total_agreement_weight += 1.0 - _metric_distance_sq(
                k1, k2, metric, sorted_scale
            )

    pi_complement_sum = sum(pi[k] * (1.0 - pi[k]) for k in sorted_scale)
    p_e = (total_agreement_weight / (q * (q - 1.0))) * pi_complement_sum
    if p_e >= 1.0:
        return 1.0
    return float((p_a - p_e) / (1.0 - p_e))


def rogan_gladen_debiased_pass_rate(
    judge_pass_count: int,
    n_unlabeled: int,
    calibration_human_labels: Sequence[int] = CALIBRATION_HUMAN_LABELS,
    calibration_judge_labels: Sequence[int] = CALIBRATION_JUDGE_LABELS,
) -> RoganGladenResult:
    """Compute the Rogan-Gladen / PPI debiased pass-rate estimate and 95% confidence interval.

    Reference:
        How to Correctly Report LLM-as-a-Judge Evaluations (arXiv:2511.21140)

    Formula:
        theta_hat = clip_[0, 1]( (p_hat_judge + TNR_hat - 1) / (TPR_hat + TNR_hat - 1) )
    """
    if n_unlabeled <= 0:
        raise ValueError("n_unlabeled must be positive.")
    if len(calibration_human_labels) != len(calibration_judge_labels):
        raise ValueError(
            "calibration_human_labels and calibration_judge_labels must have equal length."
        )
    if len(calibration_human_labels) == 0:
        raise ValueError("Calibration set cannot be empty.")

    p_hat_judge = judge_pass_count / n_unlabeled

    human_pos_indices = [i for i, y in enumerate(calibration_human_labels) if y == 1]
    human_neg_indices = [i for i, y in enumerate(calibration_human_labels) if y == 0]
    n_pos = len(human_pos_indices)
    n_neg = len(human_neg_indices)
    if n_pos == 0 or n_neg == 0:
        raise ValueError(
            "Calibration set must include both positive (1) and negative (0) human labels."
        )

    tpr = sum(calibration_judge_labels[i] for i in human_pos_indices) / n_pos
    tnr = sum(1 - calibration_judge_labels[i] for i in human_neg_indices) / n_neg
    youden_j = tpr + tnr - 1.0

    if youden_j <= 1e-6:
        raise ValueError(
            f"Judge sensitivity + specificity ({tpr + tnr:.3f}) <= 1.0; judge is no better than random."
        )

    raw_theta = (p_hat_judge + tnr - 1.0) / youden_j
    theta_hat = max(0.0, min(1.0, raw_theta))

    var_p_judge = (p_hat_judge * (1.0 - p_hat_judge)) / n_unlabeled
    var_tpr = (tpr * (1.0 - tpr)) / n_pos
    var_tnr = (tnr * (1.0 - tnr)) / n_neg

    var_theta = (
        var_p_judge + (theta_hat**2) * var_tpr + ((1.0 - theta_hat) ** 2) * var_tnr
    ) / (youden_j**2)
    se_theta = math.sqrt(max(0.0, var_theta))

    ci_lower = max(0.0, theta_hat - 1.96 * se_theta)
    ci_upper = min(1.0, theta_hat + 1.96 * se_theta)

    return RoganGladenResult(
        raw_judge_pass_rate=p_hat_judge,
        debiased_pass_rate=theta_hat,
        tpr_sensitivity=tpr,
        tnr_specificity=tnr,
        youden_j=youden_j,
        ci_95_lower=ci_lower,
        ci_95_upper=ci_upper,
        n_unlabeled=n_unlabeled,
        n_calibration_pos=n_pos,
        n_calibration_neg=n_neg,
    )


def effective_panel_size(n_judges: int, pairwise_error_correlation: float) -> float:
    """Compute effective panel size J_eff under correlated judge errors (arXiv:2605.29800).

    Formula:
        J_eff = J / (1 + (J - 1) * rho)
    """
    if n_judges < 1:
        raise ValueError("n_judges must be >= 1.")
    if not (0.0 <= pairwise_error_correlation <= 1.0):
        raise ValueError("pairwise_error_correlation must be in [0.0, 1.0].")
    return float(n_judges / (1.0 + (n_judges - 1) * pairwise_error_correlation))


def human_agent_swap(
    human_ratings_matrix: Sequence[Sequence[float | None]],
    autorater_ratings: Sequence[float | None],
    metric: str = "ordinal",
    scale_values: Sequence[float] | None = None,
) -> HumanAgentSwapResult:
    """Execute the Human/Agent Swap alignment test across an M-rater human panel."""
    if len(human_ratings_matrix) != len(autorater_ratings):
        raise ValueError(
            "human_ratings_matrix and autorater_ratings must have the same number of items."
        )
    if not human_ratings_matrix:
        raise ValueError("human_ratings_matrix cannot be empty.")

    m_raters = len(human_ratings_matrix[0])
    if m_raters < 2:
        raise ValueError("At least 2 human raters are required for Human/Agent Swap.")

    baseline_alpha = krippendorff_alpha(
        human_ratings_matrix, metric=metric, scale_values=scale_values
    )

    swapped_alphas: list[float] = []
    for m in range(m_raters):
        swapped_matrix: list[list[float | None]] = []
        for i, row in enumerate(human_ratings_matrix):
            new_row = list(row)
            new_row[m] = autorater_ratings[i]
            swapped_matrix.append(new_row)
        swapped_alphas.append(
            krippendorff_alpha(swapped_matrix, metric=metric, scale_values=scale_values)
        )

    mean_swapped = sum(swapped_alphas) / len(swapped_alphas)
    min_swapped = min(swapped_alphas)
    delta = mean_swapped - baseline_alpha

    return HumanAgentSwapResult(
        human_baseline_alpha=baseline_alpha,
        mean_swapped_alpha=mean_swapped,
        min_swapped_alpha=min_swapped,
        swap_delta=delta,
        passes_parity_gate=(mean_swapped >= 0.80 and delta >= -0.03),
    )


def agreement_at_k(verdicts_across_k_samples: Sequence[int | str | float]) -> float:
    """Compute Agreement^k: probability that all k independent judge samples agree identically."""
    n = len(verdicts_across_k_samples)
    if n < 2:
        raise ValueError("At least 2 samples are required to compute agreement_at_k.")
    counts: dict[int | str | float, int] = {}
    for v in verdicts_across_k_samples:
        counts[v] = counts.get(v, 0) + 1
    modal_count = max(counts.values())
    if modal_count == n:
        return 1.0
    return float((modal_count / n) ** n)


def pass_at_k(n_trials: int, c_successes: int, k: int) -> float:
    """Compute unbiased pass@k (probability of at least 1 success in k trials; Codex / Chen et al.)."""
    if k > n_trials or n_trials <= 0 or k <= 0:
        raise ValueError("Require 1 <= k <= n_trials.")
    if n_trials - c_successes < k:
        return 1.0
    return float(1.0 - (math.comb(n_trials - c_successes, k) / math.comb(n_trials, k)))


def pass_hat_k(n_trials: int, c_successes: int, k: int) -> float:
    """Compute pass^k (probability that ALL k trials succeed; tau-bench / Yao et al.)."""
    if k > n_trials or n_trials <= 0 or k <= 0:
        raise ValueError("Require 1 <= k <= n_trials.")
    if c_successes < k:
        return 0.0
    return float(math.comb(c_successes, k) / math.comb(n_trials, k))


def negative_flip_rate(
    old_model_verdicts: Sequence[bool | int],
    new_model_verdicts: Sequence[bool | int],
) -> float:
    """Compute Item-Level Negative Flip Rate NFR(M_old -> M_new) (arXiv:2608.13607)."""
    if len(old_model_verdicts) != len(new_model_verdicts) or not old_model_verdicts:
        raise ValueError(
            "old_model_verdicts and new_model_verdicts must be non-empty and equal length."
        )
    flips = sum(
        1
        for old_v, new_v in zip(old_model_verdicts, new_model_verdicts, strict=True)
        if bool(old_v) and not bool(new_v)
    )
    return float(flips / len(old_model_verdicts))


def compute_rair_rsr(
    human_initial_correct: Sequence[bool | int],
    ai_correct: Sequence[bool | int],
    final_decision_correct: Sequence[bool | int],
) -> AppropriateRelianceResult:
    """Compute Relative AI Reliance (RAIR) and Relative Self-Reliance (RSR) (Schemmer et al. 2023).

    - RAIR = P(Final Correct | AI Correct, Human Initial Wrong)
    - RSR  = P(Final Correct | AI Wrong, Human Initial Correct)
    """
    n = len(human_initial_correct)
    if not (n == len(ai_correct) == len(final_decision_correct)) or n == 0:
        raise ValueError(
            "All three sequences must be non-empty and have identical length."
        )

    rair_denom = 0
    rair_num = 0
    rsr_denom = 0
    rsr_num = 0

    for h_init, ai_ok, final_ok in zip(
        human_initial_correct, ai_correct, final_decision_correct, strict=True
    ):
        h_b = bool(h_init)
        ai_b = bool(ai_ok)
        f_b = bool(final_ok)
        if not h_b and ai_b:
            rair_denom += 1
            if f_b:
                rair_num += 1
        elif h_b and not ai_b:
            rsr_denom += 1
            if f_b:
                rsr_num += 1

    rair = float(rair_num / rair_denom) if rair_denom > 0 else 1.0
    rsr = float(rsr_num / rsr_denom) if rsr_denom > 0 else 1.0

    return AppropriateRelianceResult(
        rair=rair,
        rsr=rsr,
        over_reliance_rate=1.0 - rsr,
        under_reliance_rate=1.0 - rair,
        passes_oversight_gate=(rair >= 0.80 and rsr >= 0.80),
    )


def stochastic_threshold_corridor(
    scores: Sequence[float],
    min_mean: float,
    max_std: float | None = None,
    min_p05: float | None = None,
) -> CorridorCheckResult:
    """Evaluate a distribution of stochastic trial scores against mean, std, and p05 corridors."""
    n = len(scores)
    if n == 0:
        raise ValueError("scores cannot be empty.")
    sorted_scores = sorted(float(s) for s in scores)
    mean_val = sum(sorted_scores) / n
    variance = sum((s - mean_val) ** 2 for s in sorted_scores) / (n - 1 if n > 1 else 1)
    std_val = math.sqrt(variance)

    def _percentile(p: float) -> float:
        idx = p * (n - 1)
        lo = math.floor(idx)
        hi = math.ceil(idx)
        if lo == hi:
            return sorted_scores[lo]
        weight = idx - lo
        return sorted_scores[lo] * (1.0 - weight) + sorted_scores[hi] * weight

    p05_val = _percentile(0.05)
    p95_val = _percentile(0.95)

    failures: list[str] = []
    if mean_val < min_mean:
        failures.append(f"sample_mean ({mean_val:.4f}) < min_mean ({min_mean:.4f})")
    if max_std is not None and std_val > max_std:
        failures.append(f"sample_std ({std_val:.4f}) > max_std ({max_std:.4f})")
    if min_p05 is not None and p05_val < min_p05:
        failures.append(f"p05 ({p05_val:.4f}) < min_p05 ({min_p05:.4f})")

    return CorridorCheckResult(
        n_trials=n,
        sample_mean=mean_val,
        sample_std=std_val,
        p05=p05_val,
        p95=p95_val,
        passes=(len(failures) == 0),
        failures=failures,
    )


def compute_calibration_summary(
    human_scores: Sequence[float],
    judge_scores: Sequence[float],
    scale_values: Sequence[float],
) -> CalibrationSummary:
    """Compute a unified calibration summary with Krippendorff's alpha, Gwet's AC2, and bias."""
    if len(human_scores) != len(judge_scores) or not human_scores:
        raise ValueError(
            "human_scores and judge_scores must be non-empty and equal length."
        )

    matrix = [
        [float(h), float(j)] for h, j in zip(human_scores, judge_scores, strict=True)
    ]
    alpha = krippendorff_alpha(matrix, metric="ordinal", scale_values=scale_values)
    ac2 = gwets_ac2(matrix, scale_values=scale_values, metric="ordinal")

    diffs = [
        float(j) - float(h) for h, j in zip(human_scores, judge_scores, strict=True)
    ]
    mean_bias = sum(diffs) / len(diffs)
    mae = sum(abs(d) for d in diffs) / len(diffs)

    passes = (alpha >= 0.80 or ac2 >= 0.80) and abs(mean_bias) <= 0.15
    return CalibrationSummary(
        n_items=len(human_scores),
        krippendorff_alpha_ordinal=alpha,
        gwets_ac2_ordinal=ac2,
        mean_directional_bias=mean_bias,
        mae=mae,
        passes_production_gate=passes,
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
    return (
        f"auto_approve_at={t.auto_approve_at:.2f} resubmit_below={t.resubmit_below:.2f}"
    )


def _verdict(c: ThresholdCorridor, t: DispositionThresholds) -> str:
    return "inside" if c.contains(t) else "OUTSIDE"


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI calibration harness and print empirical threshold metrics."""
    parser = argparse.ArgumentParser(
        prog="python -m bridge.calibration",
        description="Check the disposition thresholds against the eval-corpus corridor.",
    )
    parser.add_argument(
        "--auto-approve-at", type=float, help="candidate auto_approve_at"
    )
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
