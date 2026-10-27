"""P7-9 deployed-path Address golden runs — the second P7 exit criterion.

The P3-5 golden runs (`tests/address/test_golden.py`) drive the Address consumer against
an **in-process** real edge over an httpx ASGI transport. This harness runs the SAME
consumer loop (`run_collection`, the real `agents/address/driver.py`) against the
**deployed** edge — the Cloud Run runtime behind the Gateway — and asserts the corpus's
**terminal outcomes** reproduce on the deployed path.

**Parity is terminal-outcome, NEVER ledger-identical** (design/ports.md; every P7
DECISIONS entry). The deployed edge runs real Gemini extraction over a real Cloud SQL
task store with real timestamps, so it will NOT byte-match the fixture. These assertions
pin only accept / reject / satisfied — the disposition/gate/satisfaction the corpus
oracle names — not confidence floats or row contents.

**Auth (Agent Identity, P7-5).** The deployed edge sits behind the Gateway with per-party
`roles/run.invoker`. The consumer presents a per-call OIDC **ID token** (audience = the
service origin) as an `Authorization: Bearer` header — the same Agent-Identity pattern as
`bridge.ports.gcp.GcpA2AClient` (a per-call OIDC id token, never a message bearer field).
The token rides an injected httpx client used by the collect node's `RemoteA2aAgent` for
BOTH the card fetch and the RPC send, so the served card's Gateway-authorized origin also
passes the adk >=2.7 same-origin validation.

**Gated.** Runs only when `BRIDGE_TEST_GCP=1` AND `BRIDGE_DEPLOYED_EDGE_URL=<gateway url>`
are both set (a provisioned project + a deployed edge — a P7-9 operator input). Credential
-free it skips cleanly, naming both envs (`uv run pytest tests/test_deployed_parity.py -rs`).
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

import httpx
import pytest
from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH
from google.adk.sessions import InMemorySessionService

from agents.address.driver import run_collection
from agents.address.gate import is_satisfied
from agents.address.wire import DocRef
from contract.enums import Disposition, Gate
from tests.address._corpus import corpus_records

# The deployed edge base URL (the Gateway-authorized public origin) — a P7-9 input.
_EDGE_URL_ENV = "BRIDGE_DEPLOYED_EDGE_URL"
_GCP = os.environ.get("BRIDGE_TEST_GCP") == "1"
_EDGE_URL = os.environ.get(_EDGE_URL_ENV, "").strip()

# Both gates must be satisfied to run against a real deployed edge. Skip is the
# credential-free default (naming both envs so the reason is actionable).
pytestmark = pytest.mark.skipif(
    not (_GCP and _EDGE_URL),
    reason=(
        "set BRIDGE_TEST_GCP=1 and BRIDGE_DEPLOYED_EDGE_URL=<gateway url> (a "
        "provisioned deployed edge, a P7-9 input) to run deployed-path parity"
    ),
)


# --------------------------------------------------------------------------- #
# Corpus-grounded document inputs (ids + artifact refs from expected.json).    #
# --------------------------------------------------------------------------- #
def _doc(doc_id: str) -> DocRef:
    """Build a ``DocRef`` from the corpus record (grounds ids/uris, not literals)."""
    for record in corpus_records():
        if record["id"] == doc_id:
            return DocRef(doc_id=doc_id, uri=record["artifact"])
    raise AssertionError(f"corpus has no document {doc_id!r}")


def _card_url() -> str:
    return _EDGE_URL.rstrip("/") + AGENT_CARD_WELL_KNOWN_PATH


class _OidcAuth(httpx.Auth):
    """Attach a per-request ``Authorization: Bearer <OIDC id token>`` header.

    Mirrors ``GcpA2AClient._fetch_id_token``: mint a workload OIDC ID token with
    ``audience == the deployed service origin`` via ``google.oauth2.id_token`` and
    present it as a bearer header. Lazy imports (like the port adapter) so importing
    this test module credential-free pulls no google.auth machinery.
    """

    def __init__(self, audience: str) -> None:
        self._audience = audience

    def auth_flow(self, request):
        import google.auth.transport.requests
        from google.oauth2 import id_token

        token = id_token.fetch_id_token(
            google.auth.transport.requests.Request(), self._audience
        )
        request.headers["Authorization"] = f"Bearer {token}"
        yield request


@asynccontextmanager
async def _authed_client():
    """Yield an OIDC-authenticated httpx client for the deployed edge (caller owns it)."""
    audience = _EDGE_URL.rstrip("/")
    hc = httpx.AsyncClient(auth=_OidcAuth(audience), timeout=600.0)
    try:
        yield hc
    finally:
        await hc.aclose()


async def _run(**kwargs):
    """Drive one collection against the deployed edge; return ``(turn, gate_rounds)``."""
    from agents.address.gate import STATE_GATE_ROUNDS

    session_service = InMemorySessionService()
    session_id = "deployed-parity"
    async with _authed_client() as hc:
        turn = await run_collection(
            _card_url(),
            httpx_client=hc,
            session_service=session_service,
            session_id=session_id,
            **kwargs,
        )
    session = await session_service.get_session(
        app_name="address", user_id="address-driver", session_id=session_id
    )
    gate_rounds = session.state.get(STATE_GATE_ROUNDS)
    return turn, gate_rounds


def _accepted(turn):
    return [e for e in turn.status.entries if e.disposition is Disposition.accepted]


# --------------------------------------------------------------------------- #
# CUJ-1 — Path-A instant accept on the DEPLOYED edge.                          #
# --------------------------------------------------------------------------- #
async def test_cuj1_path_a_instant_accept_deployed():
    """CUJ-1 reaches the corpus terminal outcome (accepted + satisfied) on the
    deployed edge — the mock→real→deployed parity through-line."""
    turn, gate_rounds = await _run(
        scenario="path-a-instant", documents=[_doc("gov-id-clean")]
    )

    assert turn is not None
    assert turn.terminal is True
    accepted = _accepted(turn)
    assert len(accepted) == 1
    entry = accepted[0]
    assert entry.doc_id == "gov-id-clean"
    assert entry.doctype == "gov-id"
    assert entry.gate is Gate.auto_approve
    assert is_satisfied(turn.status) is True
    assert gate_rounds == 1


# --------------------------------------------------------------------------- #
# CUJ-10 — distinct-issuer accept + same-issuer reject on the DEPLOYED edge.   #
# --------------------------------------------------------------------------- #
async def test_cuj10_distinct_issuer_accept_deployed():
    """Deployed edge, two distinct-issuer bills → satisfied (the accept half)."""
    turn, gate_rounds = await _run(
        documents=[_doc("bill-powerco-clean"), _doc("bill-aquautil-clean")]
    )

    assert turn is not None
    assert turn.terminal is True
    accepted = _accepted(turn)
    issuers = {e.issuer for e in accepted if e.doctype == "utility-bill"}
    assert issuers == {"power-co", "aqua-util"}
    assert is_satisfied(turn.status) is True
    assert gate_rounds == 1


async def test_cuj10_same_issuer_reject_deployed():
    """Deployed edge, two SAME-issuer bills → the gate never mints satisfaction."""
    turn, gate_rounds = await _run(
        documents=[_doc("bill-powerco-clean"), _doc("bill-powerco-clean-2")],
        max_rounds=2,
    )

    assert turn is not None
    assert gate_rounds == 2
    accepted = _accepted(turn)
    assert len(accepted) == 2
    issuers = {e.issuer for e in accepted if e.doctype == "utility-bill"}
    assert issuers == {"power-co"}
    assert is_satisfied(turn.status) is False


# --------------------------------------------------------------------------- #
# CUJ-4 — resubmit: blurry bill first-turn reject on the DEPLOYED edge.        #
# --------------------------------------------------------------------------- #
async def test_cuj4_resubmit_first_turn_reject_deployed():
    """Deployed edge, blurry bill → a first-turn reject/resubmit disposition.

    ``max_rounds=1`` terminates at the ceiling in one round (the blurry bill never
    satisfies the gate). Pins the reject terminal outcome: one rejected entry with
    ``gate=resubmit`` and ``is_satisfied`` False (the resubmit loop's first leg — the
    full blurry→clear acceptance is the mock-driven multi-turn golden run)."""
    turn, gate_rounds = await _run(
        scenario="resubmit",
        documents=[_doc("bill-aquautil-blurry")],
        max_rounds=1,
    )

    assert turn is not None
    assert gate_rounds == 1
    rejected = [e for e in turn.status.entries if e.disposition is Disposition.rejected]
    assert len(rejected) == 1
    entry = rejected[0]
    assert entry.doc_id == "bill-aquautil-blurry"
    assert entry.gate is Gate.resubmit
    assert entry.reason_code
    assert is_satisfied(turn.status) is False
