"""Program comparison view host acceptance suite (S1-benefits-7, beat 4 UI · CUJ-11).

The hard ``uv run pytest`` gate for the program comparison read-model surface: drives
``build_dashboard_app`` in-process over ``httpx.ASGITransport`` against a fresh injectable
``ProgramBoard``, proving the dashboard host serves N legs side by side in ONE
currency/schema with gaps flagged, and **recomputes live as revised versions land** (a
``revise`` → ``board.put`` surfaces on the next read, over REST and over SSE). Builds the
negotiation exactly as ``tests/test_cuj11_negotiate_bind_rollup.py`` does (that file is the
reference idiom). ``asyncio_mode="auto"`` (no marker on ``async def test_*``).

Gates:
- A — side-by-side, one basis, gap flagged (GET /programs).
- B — recomputes live as revisions land (revise → board.put → GET /programs).
- C — single + 404 (GET /programs/{id}, GET /programs on a board=None app).
- D — SSE reflects the recompute (GET /events carries a ``programs`` key; flips live).
- E — existing dashboard suite unchanged (the board=None path; asserted in test_dashboard.py).
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

import httpx

from bridge.dashboard import build_dashboard_app
from bridge.dashboard.board import ProgramBoard
from bridge.extraction.fixture import FixtureExtractor
from bridge.negotiate import load_negotiate_policy, open_negotiation, revise
from bridge.normalize import normalize_from_extraction
from bridge.program import fan_out
from bridge.standard import load_program_standard

BASE = "http://dash"
BENEFITS_CORPUS = (
    Path(__file__).resolve().parents[1] / "wiki/evals/benefits/expected.json"
)
PROCESS = "benefits-procurement"
PROGRAM_ID = "acme-2026"

CORPUS_DOC = {
    "carrier-a": "benefits-carrier-a-usd-en",
    "carrier-b": "benefits-carrier-b-usd-en",
    "carrier-c": "benefits-carrier-c-eur-fr",
    "carrier-d": "benefits-carrier-d-gbp-de",
}
REVISED_CARRIER_B = "benefits-carrier-b-usd-en-v2"
PARTIES = ["carrier-a", "carrier-b", "carrier-c", "carrier-d"]


@asynccontextmanager
async def _client(board=None, max_ticks: int = 50):
    """An httpx client bound to a fresh dashboard app over an injectable program board."""
    app = build_dashboard_app(board=board, max_ticks=max_ticks)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as hc:
        yield hc


async def _open_negotiation():
    """Fan out the 4-carrier program, normalize v0 quotes, open the negotiation."""
    extractor = FixtureExtractor(corpus_path=BENEFITS_CORPUS)
    standard = load_program_standard(PROCESS)
    policy = load_negotiate_policy(PROCESS)

    program = fan_out(PROGRAM_ID, PARTIES)
    v0 = {
        leg.party: normalize_from_extraction(
            leg.party, await extractor.extract(CORPUS_DOC[leg.party])
        )
        for leg in program.legs
    }
    neg = open_negotiation(
        PROGRAM_ID, [(leg, v0[leg.party]) for leg in program.legs], standard
    )
    return extractor, standard, policy, neg


async def _revise_carrier_b(extractor, standard, policy, neg):
    """Land carrier-b's revised (gap-closed) v2 quote as a new version on the leg."""
    revised = normalize_from_extraction(
        "carrier-b", await extractor.extract(REVISED_CARRIER_B)
    )
    return revise(neg, "carrier-b", revised, standard, policy=policy)


def _leg(program: dict, party: str) -> dict:
    return next(leg for leg in program["legs"] if leg["party"] == party)


# --------------------------------------------------------------------------- #
# Gate A: side-by-side, one basis, gap flagged.                               #
# --------------------------------------------------------------------------- #
async def test_programs_side_by_side_one_basis_gap_flagged():
    """GET /programs → 1 program, 4 legs, every leg USD/en; carrier-b flagged with
    the no_orthodontics_exclusion gap; carrier-a/c/d conformant (route none, no gaps)."""
    _extractor, standard, _policy, neg = await _open_negotiation()
    board = ProgramBoard(standard=standard)
    board.put(neg)

    async with _client(board=board) as hc:
        resp = await hc.get("/programs")
        assert resp.status_code == 200, resp.text
        programs = resp.json()
        assert len(programs) == 1

        program = programs[0]
        assert program["program_id"] == PROGRAM_ID
        assert program["currency"] == "USD"
        assert program["language"] == "en"
        assert [leg["party"] for leg in program["legs"]] == PARTIES

        # One basis: every leg is normalized to USD/en.
        for leg in program["legs"]:
            assert leg["quote"]["currency"] == "USD"
            assert leg["quote"]["language"] == "en"

        # carrier-b is the sole flagged leg, with the coverage-gap rule verbatim.
        carrier_b = _leg(program, "carrier-b")
        assert carrier_b["verdict"]["route"] == "negotiate"
        assert carrier_b["verdict"]["conformant"] is False
        gap_ids = [g["rule_id"] for g in carrier_b["verdict"]["gaps"]]
        assert "no_orthodontics_exclusion" in gap_ids
        # The gap message rides verbatim (content-not-pixels).
        assert carrier_b["verdict"]["gaps"][0]["message"]

        # carrier-a/c/d are conformant (route none, no gaps).
        for party in ("carrier-a", "carrier-c", "carrier-d"):
            leg = _leg(program, party)
            assert leg["verdict"]["route"] == "none"
            assert leg["verdict"]["gaps"] == []


# --------------------------------------------------------------------------- #
# Gate B: recomputes live as revisions land.                                  #
# --------------------------------------------------------------------------- #
async def test_programs_recompute_live_after_revise():
    """After revise(carrier-b, v2) + board.put, GET /programs recomputes to all four
    route == none (carrier-b's gap closed) — the live-recompute requirement."""
    extractor, standard, policy, neg = await _open_negotiation()
    board = ProgramBoard(standard=standard)
    board.put(neg)

    async with _client(board=board) as hc:
        before = (await hc.get("/programs")).json()[0]
        assert _leg(before, "carrier-b")["verdict"]["route"] == "negotiate"

        # Land carrier-b's revised quote and re-put onto the board.
        revised_neg = await _revise_carrier_b(extractor, standard, policy, neg)
        board.put(revised_neg)

        after = (await hc.get("/programs")).json()[0]
        assert [leg["verdict"]["route"] for leg in after["legs"]] == ["none"] * 4
        assert _leg(after, "carrier-b")["verdict"]["gaps"] == []


# --------------------------------------------------------------------------- #
# Gate C: single + 404.                                                       #
# --------------------------------------------------------------------------- #
async def test_single_program_and_404():
    """GET /programs/{id} returns the one program; unknown → 404."""
    _extractor, standard, _policy, neg = await _open_negotiation()
    board = ProgramBoard(standard=standard)
    board.put(neg)

    async with _client(board=board) as hc:
        resp = await hc.get(f"/programs/{PROGRAM_ID}")
        assert resp.status_code == 200, resp.text
        assert resp.json()["program_id"] == PROGRAM_ID

        missing = await hc.get("/programs/nope")
        assert missing.status_code == 404, missing.text


async def test_no_board_programs_empty_and_single_404():
    """With no board injected, GET /programs → [] and GET /programs/{id} → 404
    (the board=None posture — the P6-4 dashboard stays byte-identical elsewhere)."""
    async with _client(board=None) as hc:
        resp = await hc.get("/programs")
        assert resp.status_code == 200, resp.text
        assert resp.json() == []

        single = await hc.get(f"/programs/{PROGRAM_ID}")
        assert single.status_code == 404, single.text


# --------------------------------------------------------------------------- #
# Gate D: SSE reflects the recompute.                                         #
# --------------------------------------------------------------------------- #
async def test_sse_carries_programs_and_reflects_recompute():
    """GET /events is text/event-stream; the first frame carries a ``programs`` key with
    carrier-b flagged; after a board.put(revised) a later frame shows carrier-b conformant."""
    extractor, standard, policy, neg = await _open_negotiation()
    board = ProgramBoard(standard=standard)
    board.put(neg)

    async with _client(board=board, max_ticks=200) as hc:
        revised_neg = await _revise_carrier_b(extractor, standard, policy, neg)

        # Land the revision on the board partway through the stream's lifetime (a concurrent
        # task, since ASGITransport buffers the streamed response until the generator
        # completes — the diff-poll picks the mutation up on the next tick).
        async def _land_revision() -> None:
            await asyncio.sleep(0.1)
            board.put(revised_neg)

        landing = asyncio.create_task(_land_revision())
        try:
            async with hc.stream("GET", "/events") as resp:
                assert resp.status_code == 200, resp.text
                assert resp.headers["content-type"].startswith("text/event-stream")

                frames: list[dict] = []
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    frame = json.loads(line[6:])
                    frames.append(frame)
                    # The snapshot always carries a programs key when a board is injected.
                    assert "programs" in frame
        finally:
            await landing

    # The first frame shows carrier-b flagged; a later frame shows it conformant (the flip
    # surfaced live over SSE).
    routes_seen = [
        [leg["verdict"]["route"] for leg in frame["programs"][0]["legs"]]
        for frame in frames
        if frame["programs"]
    ]
    assert routes_seen[0] == ["none", "negotiate", "none", "none"]
    assert ["none"] * 4 in routes_seen
