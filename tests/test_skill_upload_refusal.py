"""A bad live upload is refused, not propagated (S4-usecase-3) — the unit layer.

Two layers of refusal, both under test here (the Driver-level proof is
``tests/test_live_use_case_add.py::test_bad_live_upload_is_refused_not_propagated``):

- **(a) structural, per folder** — ``skill_meta_from_dir`` is the strict validator; both
  adapters' ``list_skills`` skip a malformed folder with a WARNING on logger
  ``bridge.edge.skills`` and keep listing, and ``install_skill`` refuses a malformed upload
  with ``ValueError`` BEFORE anything is written;
- **(b) cross-skill, per listing** — ``resolve_label_spaces`` (applied by
  ``loaded_skills``) narrows a process's label space to its INSTALLED candidate doctypes
  and drops a process none of whose candidates is installed (S0-arch-18: never
  dispositioned against another process's label space).

Synthetic data only; every bad folder lives in a tmp ``BRIDGE_SKILLS_DIR``.
"""

from __future__ import annotations

import logging
import shutil

import pytest

from bridge.doctypes import doctype_schemas
from bridge.edge.intake import parse_text_request
from bridge.edge.skills import (
    SkillMeta,
    candidate_doctypes_for,
    hitl_mode_for,
    loaded_skills,
    resolve_label_spaces,
)
from bridge.seams.local import LocalSkillRegistry
from bridge.sla import SKILLS_DIR
from tests._skill_fixtures import (
    BROKEN_POLICY_YAML,
    CORRUPT_SCHEMA_JSON,
    INSURANCE_CERT_SKILL_MD,
    KYC_SKILL_MD,
    POLICY_YAML,
    PROCESS,
    SKILL_MD,
    TRUNCATED_W9_SKILL_MD,
    UNKNOWN_KIND_INSURANCE_CERT_SKILL_MD,
    W9_SKILL_MD,
    doctype_schema_json,
    write_skill,
)

LOGGER = "bridge.edge.skills"
ADDRESS_SKILLS = ("address-proof", "gov-id", "utility-bill")
SCHEMA = {"assets/schema.json": doctype_schema_json("w9-form")}

# A good doctype SKILL.md for "bank-statement" (fence + all keys), mutated per case below.
BANK_MD = W9_SKILL_MD.replace("name: w9-form", "name: bank-statement")

# (folder, SKILL.md, assets, reason keyword the WARNING must carry)
BAD_FOLDERS = [
    pytest.param(
        "w9-form", TRUNCATED_W9_SKILL_MD, SCHEMA, "frontmatter", id="no-fence"
    ),
    pytest.param(
        "bank-statement",
        BANK_MD.replace("description: ", "description: [unclosed "),
        SCHEMA,
        "frontmatter",
        id="frontmatter-yaml-error",
    ),
    pytest.param(
        "bank-statement",
        "---\n- name\n- description\n---\nbody\n",
        SCHEMA,
        "frontmatter",
        id="frontmatter-is-a-list",
    ),
    pytest.param(
        "bank-statement",
        "\n".join(
            ln for ln in BANK_MD.splitlines() if not ln.startswith("description:")
        )
        + "\n",
        SCHEMA,
        "description",
        id="missing-description",
    ),
    pytest.param(
        "bank-statement",
        BANK_MD.replace("  bridge-kind: doctype\n", ""),
        SCHEMA,
        "bridge-kind",
        id="missing-bridge-kind",
    ),
    pytest.param(
        "insurance-cert",
        UNKNOWN_KIND_INSURANCE_CERT_SKILL_MD,
        SCHEMA,
        "bridge-kind",
        id="unknown-kind",
    ),
    pytest.param(
        "bank-statement-bak", BANK_MD, SCHEMA, "name", id="name-differs-from-folder"
    ),
    pytest.param(
        "kyc-intake",
        KYC_SKILL_MD,
        {"assets/policy.yaml": BROKEN_POLICY_YAML},
        "policy",
        id="corrupt-policy",
    ),
    pytest.param(
        "kyc-intake",
        KYC_SKILL_MD,
        {"assets/policy.yaml": "- just\n- a list\n"},
        "policy",
        id="policy-not-a-mapping",
    ),
    pytest.param(
        "bank-statement",
        BANK_MD,
        {"assets/schema.json": CORRUPT_SCHEMA_JSON},
        "schema",
        id="corrupt-schema",
    ),
]


@pytest.fixture
def root(tmp_path, monkeypatch):
    """A tmp registry seeded with the Address skills; the ambient seam env cleared."""
    for name in ("BRIDGE_SEAM_MODE", "BRIDGE_SEAM_SKILL_REGISTRY"):
        monkeypatch.delenv(name, raising=False)
    skills = tmp_path / "skills"
    skills.mkdir()
    for name in ADDRESS_SKILLS:
        shutil.copytree(SKILLS_DIR / name, skills / name)
    monkeypatch.setenv("BRIDGE_SKILLS_DIR", str(skills))
    return skills


def _warned(caplog, *needles: str) -> bool:
    return any(
        r.name == LOGGER
        and r.levelno == logging.WARNING
        and all(n in r.getMessage() for n in needles)
        for r in caplog.records
    )


# --------------------------------------------------------------------------- #
# Layer (a): a malformed folder is skipped with a logged reason               #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(("folder", "skill_md", "assets", "reason"), BAD_FOLDERS)
def test_malformed_folder_is_skipped_with_a_logged_reason(
    root, caplog, folder, skill_md, assets, reason
):
    write_skill(root, folder, skill_md, assets)
    reg = LocalSkillRegistry()

    with caplog.at_level(logging.WARNING, logger=LOGGER):
        names = {s.name for s in reg.list_skills()}

    assert folder not in names
    assert set(ADDRESS_SKILLS) <= names
    assert _warned(caplog, repr(folder), reason), [
        r.getMessage() for r in caplog.records
    ]
    # An explicit lookup stays strict.
    with pytest.raises(ValueError):
        reg.get_skill(folder)


def test_absent_assets_stay_legitimate(root):
    """Absent policy / schema files are a legitimate (half-installed) state, not a refusal."""
    write_skill(root, "kyc-intake", KYC_SKILL_MD, {})
    write_skill(root, "w9-form", W9_SKILL_MD, {})
    names = {s.name for s in LocalSkillRegistry().list_skills()}
    assert {"kyc-intake", "w9-form"} <= names


def test_policy_or_schema_pointer_escaping_the_skill_dir_is_refused(root, caplog):
    write_skill(
        root,
        "kyc-intake",
        KYC_SKILL_MD.replace(
            "assets/policy.yaml", "../address-proof/assets/policy.yaml"
        ),
        {},
    )
    write_skill(
        root,
        "w9-form",
        W9_SKILL_MD.replace("assets/schema.json", "../gov-id/assets/schema.json"),
        {},
    )
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        names = {s.name for s in LocalSkillRegistry().list_skills()}
    assert not {"kyc-intake", "w9-form"} & names
    assert _warned(caplog, "'kyc-intake'", "escapes")
    assert _warned(caplog, "'w9-form'", "escapes")


def test_repaired_folder_is_listed_on_the_next_read(root):
    reg = LocalSkillRegistry()
    write_skill(root, "w9-form", TRUNCATED_W9_SKILL_MD, SCHEMA)
    assert "w9-form" not in {s.name for s in reg.list_skills()}

    write_skill(root, "w9-form", W9_SKILL_MD, SCHEMA)
    assert "w9-form" in {s.name for s in reg.list_skills()}


def test_corrupt_doctype_schema_no_longer_breaks_every_other_doctype(root, caplog):
    """One corrupt schema used to make doctype_schemas() raise for EVERY doctype."""
    write_skill(
        root, "bank-statement", BANK_MD, {"assets/schema.json": CORRUPT_SCHEMA_JSON}
    )
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        schemas = doctype_schemas()
    assert set(schemas) == {"gov-id", "utility-bill"}
    assert schemas["gov-id"]["properties"]["doctype"]["enum"] == ["gov-id"]
    assert _warned(caplog, "'bank-statement'", "schema")


# --------------------------------------------------------------------------- #
# Layer (b): label space = declared candidates that are installed             #
# --------------------------------------------------------------------------- #
def _process(name: str, *candidates: str) -> SkillMeta:
    return SkillMeta(
        name=name, description="d", kind="process", candidate_doctypes=candidates
    )


def _doctype(name: str) -> SkillMeta:
    return SkillMeta(name=name, description="d", kind="doctype")


def test_resolve_drops_a_process_with_no_installed_candidate(caplog):
    skills = [_process("p", "a", "b"), _doctype("c")]
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        resolved = resolve_label_spaces(skills)
    assert resolved == [_doctype("c")]
    assert _warned(caplog, "'p'", "none of its candidate doctypes", "a, b")


def test_resolve_narrows_a_partial_label_space_in_declared_order(caplog):
    skills = [_process("p", "c", "a", "b"), _doctype("b"), _doctype("c")]
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        resolved = resolve_label_spaces(skills)
    assert resolved[0] == _process("p", "c", "b")
    assert resolved[1:] == skills[1:]
    assert _warned(caplog, "'p'", "a")


def test_resolve_is_identity_when_everything_is_installed(caplog):
    skills = [_process("p", "a", "b"), _process("q"), _doctype("a"), _doctype("b")]
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        resolved = resolve_label_spaces(skills)
    assert resolved == skills
    assert all(r is s for r, s in zip(resolved, skills, strict=True))
    assert not caplog.records


def test_resolve_keeps_a_process_declaring_no_candidates():
    """S0-arch-18 case (b): a process with an empty label space is still a process."""
    skills = [_process("empty")]
    assert resolve_label_spaces(skills) == skills


def test_process_with_absent_doctypes_has_an_empty_label_space(root):
    write_skill(root, PROCESS, SKILL_MD, {"assets/policy.yaml": POLICY_YAML})
    assert PROCESS not in {s.name for s in loaded_skills()}
    assert candidate_doctypes_for(PROCESS) == []  # never Address's label space
    assert hitl_mode_for(PROCESS) == "report"

    write_skill(root, "w9-form", W9_SKILL_MD, SCHEMA)
    assert candidate_doctypes_for(PROCESS) == ["w9-form"]  # insurance-cert omitted

    write_skill(
        root,
        "insurance-cert",
        INSURANCE_CERT_SKILL_MD,
        {"assets/schema.json": doctype_schema_json("insurance-cert")},
    )
    assert candidate_doctypes_for(PROCESS) == ["w9-form", "insurance-cert"]


def test_skipped_process_does_not_defeat_the_single_process_text_fallback(root):
    """Text that names no process or tag falls back to the only USABLE process."""
    write_skill(root, PROCESS, SKILL_MD, {"assets/policy.yaml": POLICY_YAML})
    assert parse_text_request("here are my papers").process == "address-proof"


# --------------------------------------------------------------------------- #
# Install refusal: a malformed seam upload is refused before any write        #
# --------------------------------------------------------------------------- #
def test_install_refuses_a_malformed_new_skill_and_writes_nothing(root):
    with pytest.raises(ValueError, match="frontmatter"):
        LocalSkillRegistry().install_skill(
            "bank-statement", BANK_MD.split("---\n", 2)[2]
        )
    assert not (root / "bank-statement").exists()


def test_install_refuses_a_malformed_overwrite_and_keeps_the_good_skill(root):
    before = (root / "address-proof" / "SKILL.md").read_bytes()
    reg = LocalSkillRegistry()
    with pytest.raises(ValueError):
        reg.install_skill("address-proof", TRUNCATED_W9_SKILL_MD)
    assert (root / "address-proof" / "SKILL.md").read_bytes() == before
    assert "address-proof" in {s.name for s in reg.list_skills()}


def test_install_refuses_a_corrupt_schema_asset_and_writes_nothing(root):
    pay_stub = W9_SKILL_MD.replace("name: w9-form", "name: pay-stub")
    with pytest.raises(ValueError, match="pay-stub"):
        LocalSkillRegistry().install_skill(
            "pay-stub", pay_stub, assets={"assets/schema.json": CORRUPT_SCHEMA_JSON}
        )
    assert not (root / "pay-stub").exists()


def test_install_refuses_a_name_that_differs_from_the_folder(root):
    with pytest.raises(ValueError, match="name"):
        LocalSkillRegistry().install_skill("foo", W9_SKILL_MD.replace("w9-form", "bar"))
    assert not (root / "foo").exists()


def test_install_validates_every_asset_path_before_any_write(root, tmp_path):
    with pytest.raises(ValueError, match="escapes"):
        LocalSkillRegistry().install_skill(
            "w9-form", W9_SKILL_MD, assets={**SCHEMA, "../../pwned": "x"}
        )
    assert not (root / "w9-form").exists()  # not even SKILL.md
    assert not (tmp_path / "pwned").exists()
    assert not (tmp_path.parent / "pwned").exists()


def test_install_of_a_good_skill_still_writes_and_returns_its_meta(root):
    meta = LocalSkillRegistry().install_skill("w9-form", W9_SKILL_MD, assets=SCHEMA)
    assert (meta.name, meta.kind) == ("w9-form", "doctype")
    assert (root / "w9-form" / "assets" / "schema.json").exists()
