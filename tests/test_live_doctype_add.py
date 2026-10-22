"""Live doctype-add over the HTTP edge (P6-6, SKILL-4, the CUJ-8 beat).

Proves the no-redeploy claim: seed a temp registry with a *subset* (address-proof +
utility-bill only); the running edge app's Agent Card advertises
``doctype:utility-bill`` but **not** ``doctype:gov-id``. After the ``gov-id`` doctype
skill folder is **dropped into** ``BRIDGE_SKILLS_DIR`` at runtime (via the
skill_registry seam), a GET of the **same app's** card now advertises
``doctype:gov-id`` — no app rebuild between the two GETs, no HTTP write surface
(the unauthenticated install endpoint was removed, S0-sec-1).
"""

from __future__ import annotations

import shutil
from contextlib import asynccontextmanager

import httpx
from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH

from bridge.edge import build_a2a_app, build_agent_card
from bridge.seams import Seam, get_adapter, resolve_mode
from bridge.sla import SKILLS_DIR

BASE = "http://test"
URL = "http://test/"


@asynccontextmanager
async def _http(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as hc:
        yield hc


def _seed_subset(tmp_path):
    """Copy address-proof + utility-bill (NOT gov-id) into a temp registry dir."""
    for name in ("address-proof", "utility-bill"):
        shutil.copytree(SKILLS_DIR / name, tmp_path / name)


def _address_proof_tags(card_body: dict) -> list[str]:
    skill = next(s for s in card_body["skills"] if s["id"] == "address-proof")
    return skill["tags"]


async def test_live_doctype_add_regenerates_card(tmp_path, monkeypatch):
    """seed subset → card lacks doctype:gov-id → drop gov-id folder → GET → appears."""
    monkeypatch.setenv("BRIDGE_SKILLS_DIR", str(tmp_path))
    _seed_subset(tmp_path)

    app = build_a2a_app(url=URL)

    async with _http(app) as hc:
        # Run-1: gov-id doctype skill is NOT installed.
        resp = await hc.get(AGENT_CARD_WELL_KNOWN_PATH)
        assert resp.status_code == 200
        tags = _address_proof_tags(resp.json())
        assert "doctype:utility-bill" in tags
        assert "doctype:gov-id" not in tags

        # Drop the gov-id doctype skill into the live registry at runtime, via the
        # skill_registry seam (the filesystem-backed live doctype-add mechanism).
        src = SKILLS_DIR / "gov-id"
        reg = get_adapter(Seam.skill_registry, resolve_mode(Seam.skill_registry))
        installed = reg.install_skill(
            "gov-id",
            (src / "SKILL.md").read_text(),
            assets={
                "assets/schema.json": (src / "assets" / "schema.json").read_text(),
                "assets/validation.yaml": (
                    src / "assets" / "validation.yaml"
                ).read_text(),
            },
        )
        assert installed.name == "gov-id"
        assert installed.kind == "doctype"

        # Run-2 on the SAME app/client — no rebuild between GETs. The card regenerated.
        resp2 = await hc.get(AGENT_CARD_WELL_KNOWN_PATH)
        assert resp2.status_code == 200
        tags2 = _address_proof_tags(resp2.json())
        assert "doctype:gov-id" in tags2
        assert "doctype:utility-bill" in tags2


def test_install_then_build_agent_card_reflects_it(tmp_path, monkeypatch):
    """Direct adapter + build_agent_card agree: install → list_skills → card reflects it."""
    monkeypatch.setenv("BRIDGE_SKILLS_DIR", str(tmp_path))
    _seed_subset(tmp_path)

    card = build_agent_card(URL)
    ap = next(s for s in card.skills if s.id == "address-proof")
    assert "doctype:gov-id" not in ap.tags

    src = SKILLS_DIR / "gov-id"
    reg = get_adapter(Seam.skill_registry, resolve_mode(Seam.skill_registry))
    reg.install_skill("gov-id", (src / "SKILL.md").read_text())
    assert "gov-id" in {s.name for s in reg.list_skills()}

    card2 = build_agent_card(URL)
    ap2 = next(s for s in card2.skills if s.id == "address-proof")
    assert "doctype:gov-id" in ap2.tags
