"""Seam-parity test for the skill_registry seam (P6-6, SKILL-4; gcp P7-3).

The local adapter is directory-backed (a directory of Agent Skills folders rooted
at ``BRIDGE_SKILLS_DIR`` / the repo ``skills/`` dir): ``list_skills`` returns the
three authored skills, ``get_skill`` returns one, ``install_skill`` writes a folder
at runtime. The gcp adapter (P7-3) is the SAME folder format over a GCS bucket; its
live roundtrip is gated on ``BRIDGE_TEST_GCP`` (the ``adapter`` fixture) AND a
provisioned ``BRIDGE_SKILLS_BUCKET`` (a P7-9 input) — skipped without it. The
credential-free structural surface is in ``tests/test_gcp_skill_registry.py``.
"""

import json
import logging
import os

import pytest

from bridge.seams import Seam, SeamMode, get_adapter

# The GCS bucket env the gcp param of the roundtrip reads (a provisioned bucket, a
# P7-9 input). Absent → skip even under BRIDGE_TEST_GCP=1 (mirrors
# ``_gcp_dsn_or_skip`` in test_durable_seams.py).
_GCP_SKILLS_BUCKET_ENV = "BRIDGE_SKILLS_BUCKET"


def _gcp_bucket_or_skip() -> str:
    bucket = os.environ.get(_GCP_SKILLS_BUCKET_ENV, "").strip()
    if not bucket:
        pytest.skip(
            f"set {_GCP_SKILLS_BUCKET_ENV}=<provisioned skills bucket> (a P7-9 "
            "input) to run the gcp skill_registry roundtrip param"
        )
    return bucket


@pytest.mark.seam("skill_registry")
def test_skill_registry_lists_authored_skills(adapter):
    """Local lists the three authored skills; gcp reproduces the same format
    (roundtrip skipped without a provisioned bucket, P7-9)."""
    if adapter is SeamMode.gcp:
        _gcp_bucket_or_skip()
        reg = get_adapter(Seam.skill_registry, adapter)
        # A provisioned bucket must at least list without raising (terminal-outcome
        # parity is exercised end-to-end under BRIDGE_TEST_GCP at P7-9).
        assert isinstance(reg.list_skills(), list)
        return

    reg = get_adapter(Seam.skill_registry, adapter)
    names = {s.name for s in reg.list_skills()}
    assert {"address-proof", "gov-id", "utility-bill"} <= names

    gov_id = reg.get_skill("gov-id")
    assert gov_id.kind == "doctype"
    assert reg.get_skill("address-proof").kind == "process"


@pytest.mark.seam("skill_registry")
def test_skill_registry_read_asset(adapter):
    """read_asset returns a skill-relative asset's text through the seam (S0-arch-5).

    The extraction engine resolves each doctype's ``bridge-schema`` this way, so the
    same traversal defense install_skill applies must hold on the read side too.
    """
    if adapter is SeamMode.gcp:
        _gcp_bucket_or_skip()
        reg = get_adapter(Seam.skill_registry, adapter)
        # A provisioned bucket must read a seeded skill's schema without raising
        # (terminal-outcome parity, not byte-identity with the local tree).
        assert isinstance(reg.read_asset("gov-id", "assets/schema.json"), str)
        return

    reg = get_adapter(Seam.skill_registry, adapter)
    schema = json.loads(reg.read_asset("gov-id", "assets/schema.json"))
    assert schema["properties"]["doctype"]["enum"] == ["gov-id"]

    with pytest.raises(KeyError):
        reg.read_asset("gov-id", "assets/no-such-asset.json")
    with pytest.raises(KeyError):
        reg.read_asset("no-such-skill", "assets/schema.json")

    # S0-sec-1 traversal defense, on the read side.
    for bad_rel in ("../../etc/passwd", "../utility-bill/assets/schema.json", ""):
        with pytest.raises(ValueError):
            reg.read_asset("gov-id", bad_rel)
    for bad_name in ("../evil", "with/slash", "Upper", ""):
        with pytest.raises(ValueError):
            reg.read_asset(bad_name, "assets/schema.json")


@pytest.mark.seam("skill_registry")
def test_skill_registry_skips_malformed_folder(adapter, tmp_path, monkeypatch, caplog):
    """A malformed folder is skipped with a logged reason; the good ones are listed
    (S4-usecase-3). The gcp param never writes bad objects into a real bucket: it only
    checks a provisioned bucket still lists (the fake-bucket parity is in
    ``tests/test_gcp_skill_registry.py``)."""
    if adapter is SeamMode.gcp:
        _gcp_bucket_or_skip()
        reg = get_adapter(Seam.skill_registry, adapter)
        assert isinstance(reg.list_skills(), list)
        return

    monkeypatch.setenv("BRIDGE_SKILLS_DIR", str(tmp_path))
    reg = get_adapter(Seam.skill_registry, adapter)
    reg.install_skill(
        "pay-stub",
        "---\nname: pay-stub\ndescription: A pay stub.\n"
        "metadata:\n  bridge-kind: doctype\n---\n\nx\n",
    )
    (tmp_path / "half-copied").mkdir()
    (tmp_path / "half-copied" / "SKILL.md").write_text("x\n")  # no frontmatter fence

    with caplog.at_level(logging.WARNING, logger="bridge.edge.skills"):
        assert {s.name for s in reg.list_skills()} == {"pay-stub"}
    assert any("'half-copied'" in r.getMessage() for r in caplog.records)


def test_skill_registry_get_skill_missing_raises_keyerror(adapter):
    """get_skill on an absent name raises KeyError (local only)."""
    if adapter is SeamMode.gcp:
        return
    reg = get_adapter(Seam.skill_registry, adapter)
    with pytest.raises(KeyError):
        reg.get_skill("no-such-skill")


def test_skill_registry_install_is_directory_backed(adapter, tmp_path, monkeypatch):
    """install_skill writes a folder into BRIDGE_SKILLS_DIR; list/get reflect it."""
    if adapter is SeamMode.gcp:
        return
    monkeypatch.setenv("BRIDGE_SKILLS_DIR", str(tmp_path))
    reg = get_adapter(Seam.skill_registry, adapter)

    assert reg.list_skills() == []  # empty temp dir

    skill_md = (
        "---\n"
        "name: pay-stub\n"
        "description: A pay stub used as proof of income.\n"
        "metadata:\n"
        "  bridge-kind: doctype\n"
        "  bridge-tags: \"doctype income\"\n"
        "---\n\n"
        "Extract issuer, employee, gross pay, pay period.\n"
    )
    meta = reg.install_skill("pay-stub", skill_md, assets={"schema.json": "{}"})
    assert meta.name == "pay-stub"
    assert meta.kind == "doctype"
    assert meta.tags == ("doctype", "income")

    # Persisted to disk and reflected on subsequent reads.
    assert (tmp_path / "pay-stub" / "SKILL.md").exists()
    assert (tmp_path / "pay-stub" / "schema.json").read_text() == "{}"
    assert {s.name for s in reg.list_skills()} == {"pay-stub"}
    assert reg.get_skill("pay-stub").name == "pay-stub"


def test_skill_registry_install_rejects_path_traversal(adapter, tmp_path, monkeypatch):
    """install_skill rejects a traversing name or asset path (S0-sec-1)."""
    if adapter is SeamMode.gcp:
        return
    monkeypatch.setenv("BRIDGE_SKILLS_DIR", str(tmp_path))
    reg = get_adapter(Seam.skill_registry, adapter)

    for bad in ("../evil", "../../etc/x", "with/slash", "Upper", "sp ace"):
        with pytest.raises(ValueError):
            reg.install_skill(bad, "---\nname: x\n---\n")

    # A valid name but an asset that escapes the skill dir is rejected too.
    with pytest.raises(ValueError):
        reg.install_skill(
            "ok-skill", "---\nname: x\n---\n", assets={"../../pwned": "x"}
        )
    # Nothing escaped the temp root.
    assert not (tmp_path.parent / "pwned").exists()
