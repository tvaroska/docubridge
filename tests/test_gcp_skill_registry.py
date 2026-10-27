"""GCP skill_registry adapter unit tests (P7-3).

These run ALWAYS (no ``adapter`` fixture, no BRIDGE_TEST_GCP gate, no
google-cloud-storage installed): they exercise the credential-free surface of the
GCS ``skill_registry`` adapter — the refuse-without-bucket guard, the S0-sec-1
traversal defense (which MUST fire before any storage client is built), and the
download-to-temp + reuse-``skill_meta_from_dir`` list/get/install logic against an
in-memory fake bucket. The live GCS roundtrip is gated on ``BRIDGE_TEST_GCP`` +
``BRIDGE_SKILLS_BUCKET`` in ``test_skill_registry_port.py`` (P7-9).
"""

from __future__ import annotations

import logging
import sys

import pytest

from bridge.ports import Port, PortMode, get_adapter
from bridge.ports.gcp import SKILLS_BUCKET_ENV, GcpSkillRegistry, _resolve_bucket
from tests._fake_gcs import FakeBucket

_SKILL_MD = (
    "---\n"
    "name: pay-stub\n"
    "description: A pay stub used as proof of income.\n"
    "metadata:\n"
    "  bridge-kind: doctype\n"
    '  bridge-tags: "doctype income"\n'
    "---\n\n"
    "Extract issuer, employee, gross pay, pay period.\n"
)


def _reg_with_fake(monkeypatch) -> tuple[GcpSkillRegistry, FakeBucket]:
    reg = GcpSkillRegistry("test-bucket")
    fake = FakeBucket()

    def _bucket():
        fake.built += 1
        return fake

    monkeypatch.setattr(reg, "_bucket", _bucket)
    return reg, fake


# --- wired, not stubbed ----------------------------------------------------
def test_get_adapter_returns_gcp_skill_registry():
    """gcp skill_registry no longer raises NotImplementedError (P7-3)."""
    adapter = get_adapter(Port.skill_registry, PortMode.gcp)
    assert isinstance(adapter, GcpSkillRegistry)
    assert adapter.port is Port.skill_registry


# --- refuse-without-bucket is loud -----------------------------------------
def test_refuses_without_bucket_env(monkeypatch):
    """No bucket arg + no BRIDGE_SKILLS_BUCKET env → RuntimeError naming the env."""
    monkeypatch.delenv(SKILLS_BUCKET_ENV, raising=False)
    with pytest.raises(RuntimeError, match=SKILLS_BUCKET_ENV):
        get_adapter(Port.skill_registry, PortMode.gcp).list_skills()


def test_resolve_bucket_arg_wins_over_env(monkeypatch):
    monkeypatch.setenv(SKILLS_BUCKET_ENV, "env-bucket")
    assert _resolve_bucket("arg-bucket") == "arg-bucket"
    assert _resolve_bucket() == "env-bucket"


# --- traversal defense fires BEFORE any storage I/O ------------------------
def test_install_rejects_traversal_before_any_io(monkeypatch):
    """A traversing name/asset raises ValueError before _bucket() is even called
    (no client built, no network) — even with no bucket configured."""
    reg = GcpSkillRegistry()  # no bucket → _bucket() would RuntimeError if reached

    def _boom():
        raise AssertionError("_bucket() must not be called for a bad request")

    monkeypatch.setattr(reg, "_bucket", _boom)

    for bad in ("../evil", "../../etc/x", "with/slash", "Upper", "sp ace", ""):
        with pytest.raises(ValueError):
            reg.install_skill(bad, _SKILL_MD)

    # A valid name but an escaping asset is rejected too (before I/O).
    with pytest.raises(ValueError):
        reg.install_skill("ok-skill", _SKILL_MD, assets={"../../pwned": "x"})


# --- same format, different store: install → list → get --------------------
def test_install_list_get_roundtrip_reuses_parser(monkeypatch):
    """install_skill uploads <name>/SKILL.md + assets; list/get download-to-temp
    and reuse skill_meta_from_dir (the same parser as local — format parity)."""
    reg, fake = _reg_with_fake(monkeypatch)

    assert reg.list_skills() == []  # empty bucket

    meta = reg.install_skill("pay-stub", _SKILL_MD, assets={"schema.json": "{}"})
    assert meta.name == "pay-stub"
    assert meta.kind == "doctype"
    assert meta.tags == ("doctype", "income")

    # Objects laid out in the <name>/… folder format (the port contract).
    assert fake.objects["pay-stub/SKILL.md"].decode() == _SKILL_MD
    assert fake.objects["pay-stub/schema.json"] == b"{}"

    listed = {s.name for s in reg.list_skills()}
    assert listed == {"pay-stub"}
    assert reg.get_skill("pay-stub").name == "pay-stub"


def test_read_asset_roundtrip_and_traversal_defense(monkeypatch):
    """read_asset downloads <name>/<rel> as text; a traversing rel raises BEFORE any
    storage I/O (no client built), and an absent asset is a loud KeyError."""
    reg, fake = _reg_with_fake(monkeypatch)
    reg.install_skill("pay-stub", _SKILL_MD, assets={"assets/schema.json": '{"a": 1}'})
    built_after_install = fake.built

    assert reg.read_asset("pay-stub", "assets/schema.json") == '{"a": 1}'

    with pytest.raises(KeyError):
        reg.read_asset("pay-stub", "assets/missing.json")

    fake.built = 0
    for bad_rel in ("../../etc/passwd", "../other/assets/schema.json"):
        with pytest.raises(ValueError):
            reg.read_asset("pay-stub", bad_rel)
    for bad_name in ("../evil", "with/slash", "Upper", ""):
        with pytest.raises(ValueError):
            reg.read_asset(bad_name, "assets/schema.json")
    assert (
        fake.built == 0
    )  # defense fired before _bucket() — no client for a bad request
    assert built_after_install  # sanity: the good path DOES build a bucket


def test_get_skill_missing_raises_keyerror(monkeypatch):
    reg, _fake = _reg_with_fake(monkeypatch)
    with pytest.raises(KeyError):
        reg.get_skill("no-such-skill")


def test_list_skills_only_folders_with_skill_md(monkeypatch):
    """A stray object with no SKILL.md sibling is not surfaced as a skill."""
    reg, fake = _reg_with_fake(monkeypatch)
    fake.objects["orphan/notes.txt"] = b"stray"
    reg.install_skill("real-one", _SKILL_MD.replace("pay-stub", "real-one"))
    assert {s.name for s in reg.list_skills()} == {"real-one"}


# --- a bad live upload is refused, not propagated (S4-usecase-3) -----------
def test_list_skills_skips_malformed_with_a_logged_reason(monkeypatch, caplog):
    """The same tolerant listing as local: a malformed folder is skipped (WARNING on
    ``bridge.edge.skills`` naming it), the good ones are still listed."""
    reg, fake = _reg_with_fake(monkeypatch)
    fake.objects["bad/SKILL.md"] = b"Extract the fields (the frontmatter was lost).\n"
    fake.objects["odd/SKILL.md"] = (
        _SKILL_MD.replace("pay-stub", "odd")
        .replace("bridge-kind: doctype", "bridge-kind: doc-type")
        .encode()
    )
    reg.install_skill("real-one", _SKILL_MD.replace("pay-stub", "real-one"))

    with caplog.at_level(logging.WARNING, logger="bridge.edge.skills"):
        assert {s.name for s in reg.list_skills()} == {"real-one"}

    messages = [
        r.getMessage() for r in caplog.records if r.name == "bridge.edge.skills"
    ]
    assert any("'bad'" in m and "frontmatter" in m for m in messages), messages
    assert any("'odd'" in m and "bridge-kind" in m for m in messages), messages
    with pytest.raises(ValueError):
        reg.get_skill("bad")  # an explicit lookup stays strict


def test_install_refuses_malformed_before_any_io(monkeypatch):
    """A malformed upload raises ValueError before _bucket() is even called (no
    client built, nothing uploaded) — the same refusal as local."""
    reg = GcpSkillRegistry()

    def _boom():
        raise AssertionError("_bucket() must not be called for a bad request")

    monkeypatch.setattr(reg, "_bucket", _boom)

    with pytest.raises(ValueError, match="frontmatter"):
        reg.install_skill("ok-skill", "no fence")
    with pytest.raises(ValueError, match="name"):
        reg.install_skill("ok-skill", _SKILL_MD)  # frontmatter name is pay-stub
    with pytest.raises(ValueError, match="pay-stub"):
        reg.install_skill(
            "pay-stub", _SKILL_MD, assets={"assets/schema.json": "{not json"}
        )


# --- importing the gcp pkg pulls no google-cloud-storage -------------------
def test_importing_gcp_pkg_does_not_import_storage():
    """The gcp pkg lazy-imports google.cloud.storage inside methods; importing it
    (and bridge.edge.artifacts) must not pull the client (the default non-gcp
    suite runs without the gcp extra)."""
    import importlib

    importlib.import_module("bridge.edge.artifacts")
    importlib.import_module("bridge.ports.gcp")

    assert "bridge.ports.gcp" in sys.modules
    assert "google.cloud.storage" not in sys.modules
