"""Provider-Portal host acceptance suite (P6-2) — the hard ``uv run pytest`` gate.

Drives ``build_portal_app`` in-process over ``httpx.ASGITransport`` (like
``tests/test_edge_artifacts.py``, but plain REST — no A2A ``ClientFactory`` dance). A
fresh ``InMemoryPortalStore`` per test. Proves the CUJ-2 multi-step flow across the HTTP
boundary, the one-step paths, the resubmit-cap → escalate invariant threaded via the
store, that ``PathBState`` persists between requests, and the A2UI-1 relay invariant
(``reason_code``/``message`` byte-identical to the graph; no pixel/style field).
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import httpx

from bridge import pathb
from bridge.extraction.fixture import FixtureExtractor
from bridge.pathb import PathBAction, PathBState
from bridge.portal import build_portal_app
from bridge.portal.store import InMemoryPortalStore
from contract.enums import Disposition

BASE = "http://test"
PROCESS = "address-proof"
DOCTYPES = ("gov-id", "utility-bill")

_PIXEL_TOKENS = ("style", "color", "css", "width", "height", "pixel", "font")


@asynccontextmanager
async def _client(store=None):
    """An httpx client bound to a fresh portal app + store."""
    app = build_portal_app(store=store or InMemoryPortalStore())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as hc:
        yield hc


async def _start(hc, process: str = PROCESS) -> tuple[str, dict]:
    resp = await hc.post("/sessions", json={"process": process})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    return body["session_id"], body["view"]


async def _upload(hc, sid: str, artifact_name: str, doc_id: str = "doc-1") -> dict:
    resp = await hc.post(
        f"/sessions/{sid}/uploads",
        json={"doc_id": doc_id, "artifact_name": artifact_name},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _no_pixel_fields(view: dict) -> None:
    """Assert no key anywhere in the transported view looks like a styling field."""

    def walk(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                lowered = k.lower()
                for token in _PIXEL_TOKENS:
                    assert token not in lowered, f"pixel-ish key {k!r} in transported view"
                walk(v)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    walk(view)


# --------------------------------------------------------------------------- #
# Start + one-step paths                                                       #
# --------------------------------------------------------------------------- #
async def test_start_session_returns_upload_prompt():
    async with _client() as hc:
        sid, view = await _start(hc)
        assert sid
        assert view["kind"] == "upload_prompt"
        upload_actions = [a for a in view["actions"] if a["kind"] == "upload"]
        assert upload_actions
        accept = upload_actions[0]["accept"]
        assert "gov-id" in accept and "utility-bill" in accept


async def test_clean_doc_accepts_one_step():
    async with _client() as hc:
        sid, _ = await _start(hc)
        view = await _upload(hc, sid, "bill-powerco-clean")
        assert view["kind"] == "party_status"
        assert len(view["accepted"]) == 1
        assert view["accepted"][0]["disposition"] == "accepted"
        assert view["rejected"] == []


async def test_gov_id_clean_accepts_one_step():
    async with _client() as hc:
        sid, _ = await _start(hc)
        view = await _upload(hc, sid, "gov-id-clean")
        assert view["kind"] == "party_status"
        assert len(view["accepted"]) == 1
        assert view["accepted"][0]["doctype"] == "gov-id"


async def test_unsupported_doctype_rejects():
    async with _client() as hc:
        sid, _ = await _start(hc)
        view = await _upload(hc, sid, "passport-unsupported")
        assert view["kind"] == "party_status"
        assert view["accepted"] == []
        assert len(view["rejected"]) == 1
        rej = view["rejected"][0]
        assert rej["disposition"] == "rejected"
        # The unsupported (wrong-doctype) reason is relayed, not host prose.
        assert rej["reason_code"]


async def test_unknown_process_has_empty_label_space():
    """S0-arch-18: an unresolved process starts a session with an empty label space,
    so even a clean gov-id upload is rejected unsupported_doctype."""
    from bridge.portal import session as engine

    store = InMemoryPortalStore()
    rec = engine.start_session(store, process="unknown-process")
    assert rec.candidate_doctypes == ()
    rec = await engine.upload(
        store, rec.session_id, doc_id="d1", artifact_name="gov-id-clean"
    )
    assert len(rec.ledger) == 1
    assert rec.ledger[0].disposition is Disposition.rejected
    assert rec.ledger[0].reason_code == "unsupported_doctype"


# --------------------------------------------------------------------------- #
# CUJ-2 multi-step: blurry -> resubmit -> clear -> HITL -> resolve accept      #
# --------------------------------------------------------------------------- #
async def test_blurry_resubmit_then_clear_hitl_then_resolve_accept():
    """Gate #2 (CUJ-2, timeline exchange-2 beat 4) end-to-end over HTTP."""
    async with _client() as hc:
        sid, _ = await _start(hc)

        # 1) blurry -> upload_prompt (resubmit, non-terminal, actionable message present)
        v1 = await _upload(hc, sid, "bill-aquautil-blurry")
        assert v1["kind"] == "upload_prompt"
        assert v1["accepted"] == []
        assert v1["outstanding"], "resubmit park must carry an outstanding requirement"
        assert v1["outstanding"][0]["message"], "resubmit message must be relayed"

        # 2) clear -> hitl_review (pending, mid-confidence)
        v2 = await _upload(hc, sid, "bill-aquautil-clear")
        assert v2["kind"] == "hitl_review"
        assert len(v2["rejected"]) == 1
        assert v2["rejected"][0]["disposition"] == "pending"
        forms = [a for a in v2["actions"] if a["kind"] == "form"]
        assert forms and "accepted" in forms[0]["accept"]

        # 3) POST /hitl accepted -> party_status with the reviewer's message verbatim
        resp = await hc.post(
            f"/sessions/{sid}/hitl",
            json={"resolution": "accepted", "message": "Reviewer approved."},
        )
        assert resp.status_code == 200, resp.text
        v3 = resp.json()
        assert v3["kind"] == "party_status"
        assert len(v3["accepted"]) == 1
        assert v3["accepted"][0]["disposition"] == "accepted"
        assert v3["accepted"][0]["message"] == "Reviewer approved."
        # The resubmit step did NOT prematurely accept the slot.
        assert v3["rejected"] == []


# --------------------------------------------------------------------------- #
# Resubmit cap -> escalate (never reject); state persists via the store        #
# --------------------------------------------------------------------------- #
async def test_resubmit_cap_escalates_not_rejects():
    """Gate #4: blurry x4 (each a separate HTTP call, state threaded via the store)
    → pending / REASON_ESCALATED, never rejected; uploads 1-3 return upload_prompt."""
    from bridge.offscript import REASON_ESCALATED

    async with _client() as hc:
        sid, _ = await _start(hc)

        for i in range(1, 4):  # fail#1..#3 -> resubmit park (attempts <= cap)
            v = await _upload(hc, sid, "bill-aquautil-blurry", doc_id=f"blur-{i}")
            assert v["kind"] == "upload_prompt", f"upload #{i} should re-prompt"

        # fail#4 -> escalate: pending, route-to-human, NOT rejected
        v4 = await _upload(hc, sid, "bill-aquautil-blurry", doc_id="blur-4")
        assert v4["kind"] == "party_status"
        assert v4["accepted"] == []
        assert len(v4["rejected"]) == 1
        esc = v4["rejected"][0]
        assert esc["disposition"] == "pending"
        assert esc["disposition"] != "rejected"
        assert esc["reason_code"] == REASON_ESCALATED


async def test_state_persists_between_requests():
    """Gate #6: attempts threads through the STORE, not the client. A second, separate
    client on the same store/session sees the accumulated (escalated) ledger."""
    from bridge.offscript import REASON_ESCALATED

    store = InMemoryPortalStore()
    async with _client(store=store) as hc:
        sid, _ = await _start(hc)
        for i in range(1, 5):
            await _upload(hc, sid, "bill-aquautil-blurry", doc_id=f"blur-{i}")

    # A brand-new app/client over the SAME store resumes the persisted session.
    app2 = build_portal_app(store=store)
    transport = httpx.ASGITransport(app=app2)
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as hc2:
        resp = await hc2.get(f"/sessions/{sid}/view")
        assert resp.status_code == 200
        view = resp.json()
        assert view["kind"] == "party_status"
        assert view["rejected"][0]["reason_code"] == REASON_ESCALATED


# --------------------------------------------------------------------------- #
# Relay invariant (A2UI-1) — byte-identical to the graph; no pixel field       #
# --------------------------------------------------------------------------- #
async def test_relay_message_reason_code_byte_identical():
    """Gate #5: the parked entry's reason_code/message in the view are byte-identical to
    what ``pathb.step`` produced; no key in the transported view is styling."""
    # Compute the expected values straight from the graph over the same corpus.
    expected = await pathb.step(
        PathBState(candidate_doctypes=DOCTYPES),
        doc_id="doc-1",
        artifact_name="bill-aquautil-blurry",
        extractor=FixtureExtractor(),
    )
    assert expected.action is PathBAction.RESUBMIT

    async with _client() as hc:
        sid, _ = await _start(hc)
        view = await _upload(hc, sid, "bill-aquautil-blurry")

    assert view["kind"] == "upload_prompt"
    req = view["outstanding"][0]
    assert req["reason_code"] == expected.reason_code
    assert req["message"] == expected.message
    _no_pixel_fields(view)


async def test_rejected_entry_relays_verbatim():
    """The unsupported reject entry's reason_code/message match the graph's exactly."""
    expected = await pathb.step(
        PathBState(candidate_doctypes=DOCTYPES),
        doc_id="doc-1",
        artifact_name="passport-unsupported",
        extractor=FixtureExtractor(),
    )
    assert expected.action is PathBAction.REJECT

    async with _client() as hc:
        sid, _ = await _start(hc)
        view = await _upload(hc, sid, "passport-unsupported")

    rej = view["rejected"][0]
    assert rej["reason_code"] == expected.reason_code
    assert rej["message"] == expected.message
    _no_pixel_fields(view)


# --------------------------------------------------------------------------- #
# Error paths                                                                  #
# --------------------------------------------------------------------------- #
async def test_unknown_session_404():
    async with _client() as hc:
        resp = await hc.get("/sessions/nope/view")
        assert resp.status_code == 404
        resp2 = await hc.post(
            "/sessions/nope/uploads",
            json={"doc_id": "d", "artifact_name": "gov-id-clean"},
        )
        assert resp2.status_code == 404


async def test_hitl_without_park_409():
    async with _client() as hc:
        sid, _ = await _start(hc)
        resp = await hc.post(
            f"/sessions/{sid}/hitl",
            json={"resolution": "accepted", "message": "x"},
        )
        assert resp.status_code == 409


async def test_hitl_reviewer_can_reject():
    async with _client() as hc:
        sid, _ = await _start(hc)
        await _upload(hc, sid, "bill-aquautil-clear")  # -> HITL park
        resp = await hc.post(
            f"/sessions/{sid}/hitl",
            json={"resolution": "rejected", "message": "Reviewer declined."},
        )
        assert resp.status_code == 200
        view = resp.json()
        assert view["kind"] == "party_status"
        assert view["accepted"] == []
        assert view["rejected"][0]["disposition"] == "rejected"
        assert view["rejected"][0]["message"] == "Reviewer declined."


# --------------------------------------------------------------------------- #
# Seam usage — one extract() per upload                                        #
# --------------------------------------------------------------------------- #
async def test_seam_extracts_once_per_upload():
    """One extraction-seam call per upload (via an injected spy on the engine)."""
    from bridge.portal import session as engine

    class SpyExtractor:
        def __init__(self) -> None:
            self._inner = FixtureExtractor()
            self.calls: list[str] = []

        async def extract(self, artifact_name: str):
            self.calls.append(artifact_name)
            return await self._inner.extract(artifact_name)

    spy = SpyExtractor()
    store = InMemoryPortalStore()
    rec = engine.start_session(store, process=PROCESS)
    await engine.upload(
        store, rec.session_id, doc_id="d1", artifact_name="bill-aquautil-blurry",
        extractor=spy,
    )
    assert len(spy.calls) == 1
    await engine.upload(
        store, rec.session_id, doc_id="d1", artifact_name="bill-aquautil-clear",
        extractor=spy,
    )
    assert len(spy.calls) == 2


async def test_fresh_slot_after_terminal_accept():
    """After a terminal accept the next upload starts a fresh slot (accumulates)."""
    async with _client() as hc:
        sid, _ = await _start(hc)
        await _upload(hc, sid, "bill-powerco-clean", doc_id="d1")
        view = await _upload(hc, sid, "gov-id-clean", doc_id="d2")
        assert view["kind"] == "party_status"
        assert len(view["accepted"]) == 2
        assert {i["doctype"] for i in view["accepted"]} == {"utility-bill", "gov-id"}


def test_portal_never_imports_agents():
    """Isolation guard focus: the portal package imports no ``agents`` symbol."""
    import ast
    from pathlib import Path

    portal_dir = Path(__file__).resolve().parents[1] / "bridge" / "portal"
    for py in portal_dir.rglob("*.py"):
        tree = ast.parse(py.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                assert not node.module.startswith("agents"), f"{py} imports {node.module}"
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("agents"), f"{py} imports {alias.name}"
