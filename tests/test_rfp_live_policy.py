"""A live-added RFP process is served per RFP, through the skill_registry seam (S4-usecase-4).

Unit tests for the core change behind the CUJ-9 board companion Driver
(``tests/test_live_rfp_use_case_add.py``):

- the pure RFP-policy parser (``bridge.rfp.rfp_policy_from_docs``) is the one validator of
  both the build-time filesystem loader and the live seam reader, with the check order kept;
- ``bridge.edge.skills.rfp_policy_for`` reads the live registry on every call, through the
  seam (credential-free gcp proof: a fake bucket, an empty filesystem registry), and returns
  the narrowed label space;
- ``RfpBoard`` live mode: home process as-is, live processes resolved per RFP, an RFP whose
  skill was removed is skipped on the listing / 404 on a single read;
- email intake quarantines ``process_unavailable`` before any extraction; the opener refuses
  a request of another process on an existing RFP id.

Synthetic data only (suppliers ``supplier-x`` / ``supplier-y``; ``acme-buyer``). Skills are
copied into ``tmp_path`` (never written into ``skills/``).
"""

from __future__ import annotations

import logging
import shutil
from datetime import UTC, datetime, timedelta

import pytest
from google.adk.artifacts import InMemoryArtifactService

import bridge.dashboard.board as board_module
from bridge.dashboard.board import RfpBoard
from bridge.edge.email_ingress import REASON_PROCESS_UNAVAILABLE, EmailProcessor
from bridge.edge.rfp_open import open_or_update_rfp
from bridge.edge.skills import rfp_policy_for
from bridge.email_ingress.mime import landing_key
from bridge.rfp import load_rfp_policy, open_rfp, rfp_policy_from_docs
from bridge.seams import Seam, SeamMode, get_adapter, resolve_mode
from bridge.seams.gcp import GcpSkillRegistry
from bridge.seams.local import LocalEmailIngress
from bridge.sla import SKILLS_DIR, _load_policy_doc, _load_skill_frontmatter
from contract.enums import RequirementStatus
from contract.wire import CollectRequest, Requirement, RequirementsList
from tests._a2a_live import ENV_TO_CLEAR
from tests._fake_gcs import FakeBucket, seed_from_dir
from tests._skill_fixtures import (
    CANDIDATES,
    PROCESS,
    SUPPLIER_ONBOARDING_SKILLS,
    StubExtraction,
    install_supplier_onboarding,
    supplier_result,
    write_skill,
)
from tests.email_fixtures import compose

HOME = "benefits-rfp"
REPO_SKILLS = (
    "address-proof",
    "gov-id",
    "utility-bill",
    "benefits-rfp",
    "benefits-quote",
    "benefits-procurement",
)
SUPPLIERS = ("supplier-x", "supplier-y")
ITEMS = ("W9", "COI")
NOW = datetime(2026, 10, 3, 9, 0, tzinfo=UTC)
DUE = NOW + timedelta(days=14)
MAILBOX = "onboarding@buyer.example"
SECRET = b"synthetic-live-rfp-secret"
BOARD_LOGGER = "bridge.dashboard.board"


@pytest.fixture
def skills(tmp_path, monkeypatch):
    """A tmp registry with the repo skills (no supplier-onboarding); env unsteerable."""
    for name in ENV_TO_CLEAR:
        monkeypatch.delenv(name, raising=False)
    root = tmp_path / "skills"
    root.mkdir()
    for name in REPO_SKILLS:
        shutil.copytree(SKILLS_DIR / name, root / name)
    monkeypatch.setenv("BRIDGE_SKILLS_DIR", str(root))
    return root


def _registry():
    return get_adapter(Seam.skill_registry, resolve_mode(Seam.skill_registry))


def _supplier_request(**kw) -> CollectRequest:
    fields = {
        "process": PROCESS,
        "party": "acme-buyer",
        "context_id": "onboard-ctx",
        "due_at": DUE,
        "requirements": RequirementsList(
            requirements=[
                Requirement(item=item, party=s, status=RequirementStatus.required)
                for s in SUPPLIERS
                for item in ITEMS
            ]
        ),
    }
    fields.update(kw)
    return CollectRequest(**fields)


def _live_board(skills) -> RfpBoard:
    return RfpBoard(
        policy=load_rfp_policy(HOME, skills_dir=skills),
        clock=lambda: NOW,
        live_policy=rfp_policy_for,
    )


# --------------------------------------------------------------------------- #
# The pure parser: one validator for the filesystem loader and the seam reader #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", [HOME, PROCESS])
def test_rfp_policy_from_docs_equals_the_filesystem_loader(skills, name):
    if name == PROCESS:
        install_supplier_onboarding(_registry())
    frontmatter = _load_skill_frontmatter(name, skills_dir=skills)
    calls: list[int] = []

    def load_doc():
        calls.append(1)
        return _load_policy_doc(name, skills_dir=skills)

    assert rfp_policy_from_docs(name, frontmatter, load_doc) == load_rfp_policy(
        name, skills_dir=skills
    )
    assert calls == [1]  # the policy doc is read ONCE (rfp, sla and disposition)


def test_check_order_kept_pattern_fails_before_the_policy_file(skills):
    md = (
        "---\nname: haggle\ndescription: A negotiate process with no policy file.\n"
        "metadata:\n  bridge-kind: process\n  bridge-pattern: negotiate\n"
        '  bridge-candidate-doctypes: "benefits-quote"\n---\n\nbody\n'
    )
    write_skill(skills, "haggle", md, {})
    with pytest.raises(ValueError, match=r"haggle.*pattern 'negotiate'"):
        load_rfp_policy("haggle", skills_dir=skills)

    def never():
        raise AssertionError("the policy doc must not be read")

    frontmatter = _load_skill_frontmatter("haggle", skills_dir=skills)
    with pytest.raises(ValueError, match="pattern 'negotiate'"):
        rfp_policy_from_docs("haggle", frontmatter, never)


# --------------------------------------------------------------------------- #
# rfp_policy_for: the live registry reader                                      #
# --------------------------------------------------------------------------- #
def test_rfp_policy_for_reads_the_live_registry_per_call(skills):
    with pytest.raises(ValueError, match="not an installed process skill"):
        rfp_policy_for(PROCESS)
    install_supplier_onboarding(_registry())  # no re-import, no rebuild
    policy = rfp_policy_for(PROCESS)
    assert policy.process == PROCESS
    assert policy.item_field == "credential"
    assert policy.candidate_doctypes == CANDIDATES
    assert policy.tick == timedelta(days=1)
    assert policy.max_resubmissions == 3
    assert policy == load_rfp_policy(PROCESS, skills_dir=skills)
    assert rfp_policy_for(HOME).item_field == "product_line"


def test_rfp_policy_for_refuses_non_rfp_processes(skills):
    with pytest.raises(ValueError, match="not an installed process skill"):
        rfp_policy_for("no-such-skill")
    with pytest.raises(ValueError, match=r"address-proof.*missing an 'rfp:' block"):
        rfp_policy_for("address-proof")
    with pytest.raises(ValueError, match=r"benefits-procurement.*pattern 'negotiate'"):
        rfp_policy_for("benefits-procurement")
    with pytest.raises(ValueError, match="not an installed process skill"):
        rfp_policy_for("gov-id")  # a doctype is not a process


def test_rfp_policy_for_uses_the_narrowed_label_space(skills):
    registry = _registry()
    w9_md, w9_assets = SUPPLIER_ONBOARDING_SKILLS["w9-form"]
    proc_md, proc_assets = SUPPLIER_ONBOARDING_SKILLS[PROCESS]
    registry.install_skill(PROCESS, proc_md, assets=proc_assets)
    # Process only, no candidate installed: not loaded, so not an RFP process.
    with pytest.raises(ValueError, match="not an installed process skill"):
        rfp_policy_for(PROCESS)
    registry.install_skill("w9-form", w9_md, assets=w9_assets)
    policy = rfp_policy_for(PROCESS)
    assert policy.candidate_doctypes == ("w9-form",)
    assert policy.doctype_hint == "w9-form"
    # The filesystem loader stays un-narrowed (the home path's documented residual).
    assert load_rfp_policy(PROCESS, skills_dir=skills).candidate_doctypes == CANDIDATES


def test_rfp_policy_for_reads_through_the_gcp_adapter(tmp_path, monkeypatch):
    """Credential-free: the gcp adapter over a fake bucket, an EMPTY filesystem registry —
    the policy can only have come through the seam."""
    for name in ENV_TO_CLEAR:
        monkeypatch.delenv(name, raising=False)
    fake = FakeBucket()
    monkeypatch.setattr(GcpSkillRegistry, "_bucket", lambda self: fake)
    monkeypatch.setenv("BRIDGE_SEAM_SKILL_REGISTRY", "gcp")
    monkeypatch.setenv("BRIDGE_SKILLS_BUCKET", "fake-skills")
    empty = tmp_path / "empty-skills"
    empty.mkdir()
    monkeypatch.setenv("BRIDGE_SKILLS_DIR", str(empty))
    seed_from_dir(fake, SKILLS_DIR, ("address-proof", "gov-id", "utility-bill"))

    with pytest.raises(ValueError, match="not an installed process skill"):
        rfp_policy_for(PROCESS)
    install_supplier_onboarding(get_adapter(Seam.skill_registry, SeamMode.gcp))
    assert f"{PROCESS}/assets/policy.yaml" in fake.objects

    policy = rfp_policy_for(PROCESS)
    assert policy.item_field == "credential"
    assert policy.candidate_doctypes == CANDIDATES
    with pytest.raises(ValueError, match="not found"):
        load_rfp_policy(PROCESS)  # the filesystem does not have it
    with pytest.raises(ValueError, match="missing an 'rfp:' block"):
        rfp_policy_for("address-proof")


# --------------------------------------------------------------------------- #
# RfpBoard: single mode unchanged, live mode per RFP                            #
# --------------------------------------------------------------------------- #
def test_single_mode_board_is_unchanged(skills):
    install_supplier_onboarding(_registry())
    board = RfpBoard(policy=load_rfp_policy(HOME, skills_dir=skills), clock=lambda: NOW)
    assert board.live_policy is None
    assert board.serves(HOME) is True
    assert board.serves(PROCESS) is False  # installed, but this board is single-process
    with pytest.raises(
        ValueError, match="runs process 'supplier-onboarding'; this board"
    ):
        board.put(open_rfp(_supplier_request(), now=NOW, rfp_id="onboard-1"))
    assert list(board.rfps) == []


def test_live_board_serves_a_live_process_with_its_own_policy(skills, monkeypatch):
    board = _live_board(skills)
    assert board.serves(PROCESS) is False  # not installed yet
    install_supplier_onboarding(_registry())
    assert board.serves(PROCESS) is True
    assert board.serves(HOME) is True
    assert board.serves("address-proof") is False  # a Collect process, not an RFP one
    assert board.policy_for(HOME) is board.policy
    assert board.policy_for(PROCESS).item_field == "credential"

    board.put(open_rfp(_supplier_request(), now=NOW, rfp_id="onboard-1"))
    home_request = _supplier_request(
        process=HOME,
        context_id="rfp-ctx",
        requirements=RequirementsList(
            requirements=[
                Requirement(
                    item="CRITICAL_ILLNESS",
                    party="carrier-a",
                    status=RequirementStatus.required,
                )
            ]
        ),
    )
    board.put(open_rfp(home_request, now=NOW, rfp_id="home-1"))

    projected: list[tuple[str, str]] = []
    real = board_module.rfp_status

    def spy(rfp, *, now, policy):
        projected.append((rfp.process, policy.process))
        return real(rfp, now=now, policy=policy)

    monkeypatch.setattr(board_module, "rfp_status", spy)
    views = board.statuses()
    assert [(v.rfp_id, v.process) for v in views] == [
        ("onboard-1", PROCESS),
        ("home-1", HOME),
    ]
    assert projected == [(PROCESS, PROCESS), (HOME, HOME)]  # each with ITS policy
    assert board.get("onboard-1").waiting == len(SUPPLIERS) * len(ITEMS)


def test_live_board_skips_an_rfp_whose_skill_was_removed(skills, caplog):
    board = _live_board(skills)
    install_supplier_onboarding(_registry())
    board.put(open_rfp(_supplier_request(), now=NOW, rfp_id="onboard-1"))
    assert [v.rfp_id for v in board.statuses()] == ["onboard-1"]

    shutil.rmtree(skills / PROCESS)
    with caplog.at_level(logging.WARNING, logger=BOARD_LOGGER):
        assert board.statuses() == []
    assert any(
        r.name == BOARD_LOGGER
        and r.levelno == logging.WARNING
        and repr(PROCESS) in r.getMessage()
        for r in caplog.records
    )
    with pytest.raises(KeyError):
        board.get("onboard-1")
    assert "onboard-1" in board.rfps  # the record itself is untouched


def test_live_board_refuses_a_non_rfp_process(skills):
    board = _live_board(skills)
    request = _supplier_request(process="address-proof")
    with pytest.raises(ValueError, match="missing an 'rfp:' block"):
        board.put(open_rfp(request, now=NOW, rfp_id="addr-1"))
    assert list(board.rfps) == []


# --------------------------------------------------------------------------- #
# Email intake + the opener                                                    #
# --------------------------------------------------------------------------- #
async def test_email_quarantines_process_unavailable_before_extraction(
    skills, tmp_path
):
    board = _live_board(skills)
    install_supplier_onboarding(_registry())
    board.put(open_rfp(_supplier_request(), now=NOW, rfp_id="onboard-1"))
    before = board.rfps["onboard-1"]
    seam = LocalEmailIngress(tmp_path / "mail")
    extraction = StubExtraction({"w9.pdf": supplier_result("w9-form", "W9")})
    proc = EmailProcessor(
        board=board,
        seam=seam,
        mailbox=MAILBOX,
        secret=SECRET,
        extraction=extraction,
        artifact_store=InMemoryArtifactService(),
        base_url="http://bridge.test/",
    )
    raw = compose(
        proc.reply_address("onboard-1", "supplier-y"),
        message_id="<creds-1@supplier-y.example>",
        sender="ap@supplier-y.example",
        subject="Re: supplier onboarding",
        attachments=[("w9.pdf", b"%PDF-1.4 synthetic w9", "application/pdf")],
    )
    assert seam.land(landing_key(raw), raw)

    shutil.rmtree(skills / PROCESS)  # removed after the RFP opened
    (outcome,) = await proc.drain()

    assert (outcome.status, outcome.reason) == (
        "quarantined",
        REASON_PROCESS_UNAVAILABLE,
    )
    assert extraction.calls == []
    assert board.rfps["onboard-1"] == before


def test_opener_refuses_another_process_on_an_existing_rfp(skills):
    board = _live_board(skills)
    install_supplier_onboarding(_registry())
    turn = open_or_update_rfp(board, _supplier_request(), context_id="onboard-1")
    assert turn.context_id == "onboard-1"
    assert {r.reason_code for r in turn.status.outstanding.requirements} == {"invited"}
    with pytest.raises(ValueError, match="runs process 'supplier-onboarding', not"):
        open_or_update_rfp(board, CollectRequest(process=HOME), context_id="onboard-1")
    # A status read of the right process still works and uses the live policy.
    again = open_or_update_rfp(
        board, CollectRequest(process=PROCESS), context_id="onboard-1"
    )
    assert len(again.status.outstanding.requirements) == len(SUPPLIERS) * len(ITEMS)
