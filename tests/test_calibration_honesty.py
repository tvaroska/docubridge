"""Calibration honesty guard: agreement coefficients come from data, never constants.

``krippendorff_alpha`` / ``gwets_ac2`` in ``bridge/`` must be fed a ratings matrix
that was observed (extraction runs, reviewer labels), not a literal list/tuple
or a module-level constant of lists. A constant matrix yields a constant alpha,
which reads as a measurement but is a number typed into the source.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BRIDGE_ROOT = REPO_ROOT / "bridge"
AGREEMENT_FUNCTIONS = frozenset({"krippendorff_alpha", "gwets_ac2"})


def _module_constant_matrices(tree: ast.Module) -> set[str]:
    """Names bound at module level to a literal list/tuple (possibly starred) of rows."""
    names: set[str] = set()
    for node in tree.body:
        targets: list[ast.expr] = []
        value: ast.expr | None = None
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        if not isinstance(value, ast.List | ast.Tuple):
            continue
        if not any(
            isinstance(elt, ast.List | ast.Tuple | ast.Starred) for elt in value.elts
        ):
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                names.add(target.id)
    return names


def _called_name(call: ast.Call) -> str | None:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def literal_matrix_calls(source: str, filename: str = "<memory>") -> list[str]:
    """``file:line`` for every agreement call whose matrix argument is a constant."""
    tree = ast.parse(source, filename=filename)
    constants = _module_constant_matrices(tree)
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _called_name(node) not in (
            AGREEMENT_FUNCTIONS
        ):
            continue
        matrix: ast.expr | None = node.args[0] if node.args else None
        if matrix is None:
            for kw in node.keywords:
                if kw.arg == "ratings_matrix":
                    matrix = kw.value
        if matrix is None:
            continue
        is_literal = isinstance(matrix, ast.List | ast.Tuple)
        is_constant_name = isinstance(matrix, ast.Name) and matrix.id in constants
        if is_literal or is_constant_name:
            offenders.append(f"{filename}:{node.lineno}")
    return offenders


def test_bridge_never_feeds_a_constant_matrix_to_agreement_coefficients() -> None:
    """No module under bridge/ passes a literal or constant matrix to alpha / AC2."""
    offenders: list[str] = []
    for py_path in sorted(BRIDGE_ROOT.rglob("*.py")):
        source = py_path.read_text(encoding="utf-8")
        offenders.extend(
            literal_matrix_calls(source, str(py_path.relative_to(REPO_ROOT)))
        )
    assert offenders == [], (
        "agreement coefficients computed from constants (report 'not evaluated' "
        f"instead): {offenders}"
    )


@pytest.mark.parametrize(
    "snippet",
    [
        "from bridge.calibration import krippendorff_alpha\n"
        "a = krippendorff_alpha([(1.0, 1.0), (0.0, 0.0)], metric='nominal')\n",
        "from bridge.calibration import gwets_ac2\n"
        "M: tuple[tuple[float, ...], ...] = (*([(1.0, 1.0)] * 3), (0.0, 1.0))\n"
        "a = gwets_ac2(M, scale_values=(0.0, 1.0))\n",
        "import bridge.calibration as cal\n"
        "ROWS = [[1, 1], [0, 0]]\n"
        "a = cal.krippendorff_alpha(ratings_matrix=ROWS)\n",
    ],
)
def test_constant_matrix_detector_positive_control(snippet: str) -> None:
    assert literal_matrix_calls(snippet) != []


def test_constant_matrix_detector_accepts_observed_matrices() -> None:
    snippet = (
        "from bridge.calibration import krippendorff_alpha\n"
        "def score(rows):\n"
        "    matrix = [[r.a, r.b] for r in rows]\n"
        "    return krippendorff_alpha(matrix, metric='nominal')\n"
    )
    assert literal_matrix_calls(snippet) == []


def test_refinery_report_defaults_are_not_evaluated() -> None:
    """The wire model never fails open: no alpha, not passed, until the assay says so."""
    from contract.refinery import RefineryAssayReport

    report = RefineryAssayReport(
        report_id="r",
        proposal_id="p",
        candidate_revision_id="c",
        baseline_revision_id="b",
    )
    assert report.perturbation_evaluated is False
    assert report.perturbation_krippendorff_alpha is None
    assert report.workhorse_teacher_ac2 is None
    assert report.passed is False


def test_bridge_never_feeds_synthetic_vectors_to_rair_rsr() -> None:
    """No module under bridge/ calls compute_rair_rsr with repeated constant vectors."""
    offenders: list[str] = []
    for py_path in sorted(BRIDGE_ROOT.rglob("*.py")):
        source = py_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(py_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _called_name(node) == "compute_rair_rsr":
                for arg in [*node.args, *(kw.value for kw in node.keywords)]:
                    if isinstance(arg, ast.List | ast.Tuple) or (
                        isinstance(arg, ast.BinOp)
                        and isinstance(arg.op, ast.Mult)
                        and isinstance(arg.left, ast.List | ast.Tuple)
                    ):
                        offenders.append(
                            f"{py_path.relative_to(REPO_ROOT)}:{node.lineno}"
                        )
    assert offenders == [], (
        f"compute_rair_rsr called with synthetic/constant vectors: {offenders}"
    )


def test_expert_feedback_signal_defaults_fail_closed() -> None:
    """ExpertFeedbackSignal defaults never fail open on citation, dwell, or RSR score."""
    from bridge.agent_gym.models import ExpertFeedbackSignal, SignalSourceZone

    sig = ExpertFeedbackSignal(
        signal_id="sig-default-check",
        source_zone=SignalSourceZone.REVIEW_QUARANTINE,
        entity_id="ENT-1",
        doc_id="doc-1",
        doctype="quote",
        issuer="Acme",
        field_path="deductible",
        before_value="100",
        after_value="200",
        expert_memo="check defaults",
        actor_principal="reviewer@example.com",
        actor_role="broker",
    )
    assert sig.citation_verified is False
    assert sig.review_dwell_ms == 0
    assert sig.oversight_rsr_score == 0.0


def test_distill_without_oversight_evidence_is_rejected() -> None:
    """Omitting citation/dwell oversight evidence fails closed across engine, schemas, and shadow mode (P13-7)."""
    from bridge.agent_gym import (
        InMemoryAgentGymEngine,
        ProgrammableTrustPolicy,
        TribalRuleStatus,
    )
    from bridge.telemetry import clear_recorded_spans, get_recorded_spans
    from bridge.user_interaction.schemas import (
        DistillQuarantineFixRequest,
        RecordShadowComparisonRequest,
    )
    from bridge.vault import GcpAuditVaultStore

    # Request models fail closed by default
    req = DistillQuarantineFixRequest(
        entity_id="ENT-OV",
        doc_id="doc-ov",
        doctype="quote",
        issuer="Acme",
        field_path="deductible",
        before_value="$500.00",
        after_value="$1,000.00",
        expert_memo="No oversight evidence supplied.",
    )
    assert req.citation_verified is False
    assert req.review_dwell_ms == 0

    shadow_req = RecordShadowComparisonRequest(
        entity_id="ENT-OV",
        doc_id="doc-ov",
        issuer="Acme",
        field_path="deductible",
        ai_value="$500.00",
        human_value="$500.00",
    )
    assert shadow_req.citation_verified is False
    assert shadow_req.review_dwell_ms == 0

    # Engine distill defaults reject rule when oversight evidence is omitted
    clear_recorded_spans()
    gym = InMemoryAgentGymEngine()
    sig, rule = gym.distill_from_quarantine_correction(
        entity_id="ENT-OV",
        doc_id="doc-ov",
        doctype="quote",
        issuer="Acme",
        field_path="deductible",
        before_value="$500.00",
        after_value="$1,000.00",
        expert_memo="No oversight evidence supplied.",
        actor_principal="reviewer@docubridge.io",
    )
    assert sig.citation_verified is False
    assert sig.review_dwell_ms == 0
    assert sig.oversight_rsr_score < 0.80
    assert rule.status == TribalRuleStatus.REJECTED

    # Offline distillation emits no fabricated token counts
    distill_spans = [
        s
        for s in get_recorded_spans()
        if s["name"] == "docubridge.agent_gym.distill_signal"
    ]
    assert distill_spans
    assert "gen_ai.usage.input_tokens" not in distill_spans[-1]["attributes"]
    assert "gen_ai.usage.output_tokens" not in distill_spans[-1]["attributes"]

    # record_shadow_comparison defaults do not advance consecutive_matches without oversight
    rec, policy = gym.record_shadow_comparison(
        slice_id="slice:unverified:test",
        entity_id="ENT-OV",
        doc_id="doc-ov",
        issuer="Acme",
        doctype="quote",
        field_path="deductible",
        ai_value="$500.00",
        human_value="$500.00",
    )
    assert rec.citation_verified is False
    assert rec.oversight_rsr_score < 0.80
    assert policy.consecutive_matches == 0

    # Zero-evaluation trust policy reports None (not 1.0)
    fresh_policy = ProgrammableTrustPolicy(
        slice_id="slice:fresh:none",
        issuer_pattern="Acme*",
        doctype="quote",
    )
    assert fresh_policy.accuracy_rate is None

    # Offline evaluation reports None for unmeasured judge model & autorater score
    _, valid_rule = gym.distill_from_quarantine_correction(
        entity_id="ENT-OV2",
        doc_id="doc-ov2",
        doctype="quote",
        issuer="Acme",
        field_path="deductible",
        before_value="$500.00",
        after_value="$1,000.00",
        expert_memo="Verified against Schedule B.",
        actor_principal="reviewer@docubridge.io",
        citation_verified=True,
        review_dwell_ms=4200,
    )
    report = gym.evaluate_candidate_rule(valid_rule.rule_id)
    assert report.passed_gate is True
    assert report.judge_model is None
    assert report.tier2_autorater_score is None

    # VaultRecord.retention_locked reflects whether locked retention was actually applied
    from tests.test_single_bucket_gcp import _SharedFakeBucket, _SharedFakeClient

    bucket = _SharedFakeBucket("honesty-vault-bucket")
    client = _SharedFakeClient(bucket)
    unretained = GcpAuditVaultStore("honesty-vault-bucket", client=client)
    rec_no_ret = unretained.lock_document(
        context_id="ctx-r1",
        doc_id="doc-r1",
        version=1,
        filename="a.pdf",
        data=b"%PDF-1.7 a",
    )
    assert rec_no_ret.retention_locked is False

    unlocked_ret = GcpAuditVaultStore(
        "honesty-vault-bucket",
        retention_days=30,
        retention_mode="Unlocked",
        client=client,
    )
    rec_unlocked = unlocked_ret.lock_document(
        context_id="ctx-r2",
        doc_id="doc-r2",
        version=1,
        filename="b.pdf",
        data=b"%PDF-1.7 b",
    )
    assert rec_unlocked.retention_locked is False

    locked_ret = GcpAuditVaultStore(
        "honesty-vault-bucket",
        retention_days=2555,
        retention_mode="Locked",
        client=client,
    )
    rec_locked = locked_ret.lock_document(
        context_id="ctx-r3",
        doc_id="doc-r3",
        version=1,
        filename="c.pdf",
        data=b"%PDF-1.7 c",
    )
    assert rec_locked.retention_locked is True


def test_inbox_audit_defaults_fail_closed_and_live_judge_rejection_blocks_gate() -> (
    None
):
    """Inbox resolution without telemetry fails closed; a live judge rejection fails the gate (P13-7)."""
    from bridge.a2ui_inbox import A2uiClientActionRequest, InMemoryActionInboxStore
    from bridge.agent_gym import InMemoryAgentGymEngine, TribalRuleStatus

    inbox = InMemoryActionInboxStore()
    item = inbox.create_action(
        entity_id="ENT-INBOX-FC",
        title="Verify fee",
        hint="Check page 2",
        pause_for_review=True,
        data_model={"resolution": {"selectedValue": "$50,000.00"}},
    )
    inbox.resolve_action(
        item.item_id,
        action_request=A2uiClientActionRequest(
            action={
                "name": "edit_and_approve",
                "context": {
                    "decision": "edit",
                    "resolution": {"selectedValue": "$75,000.00"},
                    "review": {"memo": "Updated fee"},
                },
            }
        ),
        actor_principal="reviewer@docubridge.io",
        actor_role="underwriter",
        channel="gemini_app",
    )
    audit = inbox.list_audit_records(item.item_id)[0]
    assert audit.citation_opened is False
    assert audit.review_dwell_ms == 0

    gym = InMemoryAgentGymEngine()
    sig, rule = gym.distill_from_inbox_audit(
        audit,
        doc_id="doc-fc",
        doctype="closing-disclosure",
        issuer="Acme Bank",
        field_path="origination_fee",
    )
    assert sig.citation_verified is False
    assert sig.review_dwell_ms == 0
    assert rule.status == TribalRuleStatus.REJECTED

    # Live judge returning REJECT/FAIL must block the gate instead of being discarded
    class _Usage:
        prompt_token_count = 300
        candidates_token_count = 40

    class _RejectResp:
        text = "REJECT: unsafe rule override"
        usage_metadata = _Usage()

    class _RejectModels:
        def generate_content(self, **_kwargs: object) -> _RejectResp:
            return _RejectResp()

    class _RejectClient:
        models = _RejectModels()

    gym_live = InMemoryAgentGymEngine()
    gym_live._genai_client = _RejectClient()  # type: ignore[attr-defined]
    _, cand = gym_live.distill_from_quarantine_correction(
        entity_id="ENT-LJ",
        doc_id="doc-lj",
        doctype="quote",
        issuer="Acme",
        field_path="deductible",
        before_value="$500.00",
        after_value="$1,000.00",
        expert_memo="Schedule B override",
        actor_principal="reviewer@docubridge.io",
        citation_verified=True,
        review_dwell_ms=4200,
    )
    report = gym_live.evaluate_candidate_rule(cand.rule_id, auto_promote=True)
    assert report.passed_gate is False
    assert report.tier2_autorater_score == 0.0
    assert "live_judge_rejected" in report.historical_regressions
    reloaded = next(
        (r for r in gym_live.list_rules() if r.rule_id == cand.rule_id), None
    )
    assert reloaded is not None and reloaded.status == TribalRuleStatus.REJECTED

    # Negated pass wording ("NOT APPROVED") fails closed, and prose integers do not mask decimal score
    from bridge.agent_gym.rule_evaluator import _parse_live_judge_verdict

    assert _parse_live_judge_verdict("NOT APPROVED: overbroad target") == (
        False,
        0.0,
        "live_judge_rejected",
    )
    assert _parse_live_judge_verdict("DO NOT PASS") == (
        False,
        0.0,
        "live_judge_rejected",
    )
    assert _parse_live_judge_verdict("PASS: 1 rule verified, score 0.85") == (
        True,
        0.85,
        None,
    )
