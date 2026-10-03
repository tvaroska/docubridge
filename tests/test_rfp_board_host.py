"""The deployed RFP board host (S0-arch-17) + the processor's cross-replica safety.

The host (``bridge.dashboard.board_host.build_rfp_board_app``) owns ONE ``RfpBoard`` over
the durable shared exchange store and binds the email processor from env; Cloud Scheduler
(here: a POST / the CLI ``drain --url``) drives ``/email/drain``. Proves: the acceptance
path (a SEPARATE writer opens the RFP onto a shared sqlite store; the host files a landed
reply onto it; a third board over the same DSN sees it; the store closes on shutdown); the
process comes from env (never a core literal); a processor never binds over an in-memory
store; the poll loop runs once on the outer app; and the processor's phase-3 safety — no
duplicate version after a crash between put and marker, a CAS retry keeps both writes, an
exhausted retry leaves the key pending (409 on the host) — plus the chase / upload 409.

Synthetic data only (``tests/email_fixtures.py``).
"""

from __future__ import annotations

import asyncio
import time
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from google.adk.artifacts import InMemoryArtifactService

from bridge.dashboard.board import RfpBoard
from bridge.dashboard.board_host import RFP_PROCESS_ENV, build_rfp_board_app
from bridge.dashboard.server import build_dashboard_app
from bridge.edge.email_ingress import EmailProcessor
from bridge.email_ingress.__main__ import main as cli
from bridge.email_ingress.correlation import IntakeAddressing
from bridge.email_ingress.mime import landing_key
from bridge.exchange_store import (
    ExchangeStore,
    InMemoryExchangeStore,
    SqlExchangeStore,
    StaleExchangeWrite,
)
from bridge.extraction.fixture import FixtureExtractor
from bridge.outbound import FakeTransport, OutboundClient
from bridge.portal.server import build_portal_app
from bridge.rfp import load_rfp_policy, open_rfp, record_chase
from contract.enums import LifecycleStatus, RequirementStatus, RfpItemStatus
from contract.wire import CollectRequest, Requirement, RequirementsList
from tests.email_fixtures import (
    BASE_URL,
    CI_PDF,
    CONFORMANCE,
    DUE,
    MAILBOX,
    NOW,
    PROCESS,
    RFP_ID,
    SECRET,
    compose,
    extractor,
    make_board,
    quote_attachments,
)

ENV_SECRET = "board-host-test-secret"
CARRIERS = ("carrier-a", "carrier-b", "carrier-c")
PRODUCT_LINES = ("CRITICAL_ILLNESS", "HOSPITAL_INDEMNITY")
HOST_ENV = (
    "BRIDGE_EMAIL_DIR",
    "BRIDGE_EMAIL_BUCKET",
    "BRIDGE_EMAIL_MAILBOX",
    "BRIDGE_EMAIL_TOKEN_SECRET",
    "BRIDGE_EMAIL_DRAIN_INTERVAL",
    "BRIDGE_EMAIL_DRAIN_URL",
    "BRIDGE_EMAIL_MAX_BYTES",
    "BRIDGE_SEAM_EMAIL_INGRESS",
    "BRIDGE_SEAM_EXTRACTION",
    "BRIDGE_SEAM_EXCHANGE_STORE",
    "BRIDGE_SEAM_MODE",
    "BRIDGE_ARTIFACT_BUCKET",
    "BRIDGE_PUBLIC_URL",
    "BRIDGE_EXCHANGE_DB_URL",
    "DATABASE_URL",
    RFP_PROCESS_ENV,
)


@pytest.fixture
def clean_env(monkeypatch):
    for name in HOST_ENV:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


@pytest.fixture
def dsn(tmp_path) -> str:
    return f"sqlite+aiosqlite:///{tmp_path}/rfp.db"


@pytest.fixture
def host_env(clean_env, tmp_path, dsn):
    """The deployed shape locally: process + durable DSN + email intake, on-demand drain."""
    clean_env.setenv(RFP_PROCESS_ENV, PROCESS)
    clean_env.setenv("BRIDGE_EXCHANGE_DB_URL", dsn)
    clean_env.setenv("BRIDGE_EMAIL_DIR", str(tmp_path / "mail"))
    clean_env.setenv("BRIDGE_EMAIL_MAILBOX", MAILBOX)
    clean_env.setenv("BRIDGE_EMAIL_TOKEN_SECRET", ENV_SECRET)
    clean_env.setenv("BRIDGE_EMAIL_DRAIN_INTERVAL", "0")
    return clean_env


def _open_request() -> CollectRequest:
    return CollectRequest(
        process=PROCESS,
        party="acme-broker",
        context_id="rfp-ctx",
        due_at=DUE,
        requirements=RequirementsList(
            requirements=[
                Requirement(
                    item=line,
                    party=party,
                    status=RequirementStatus.required,
                    doctype_hint="benefits-quote",
                )
                for party in CARRIERS
                for line in PRODUCT_LINES
            ]
        ),
    )


def _open_on(store: ExchangeStore) -> None:
    """Another writer (not the host) opens the RFP onto the shared store."""
    writer = RfpBoard(policy=load_rfp_policy(PROCESS), store=store)
    writer.put(open_rfp(_open_request(), now=NOW - timedelta(days=1), rfp_id=RFP_ID))


def _reply_address(client: TestClient, party: str) -> str:
    body = client.get(f"/rfps/{RFP_ID}").json()
    return next(c["reply_address"] for c in body["carriers"] if c["party"] == party)


def _cells(client: TestClient) -> dict[tuple[str, str], str]:
    body = client.get(f"/rfps/{RFP_ID}").json()
    return {(c["party"], c["product_line"]): c["status"] for c in body["cells"]}


def _wait_for(predicate, *, timeout: float = 5.0, step: float = 0.05) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(step)
    return predicate()


# --------------------------------------------------------------------------- #
# 1. The acceptance path                                                      #
# --------------------------------------------------------------------------- #
def test_deployed_board_host_drains_onto_a_shared_durable_store(
    host_env, tmp_path, dsn, capsys
):
    seed_store = SqlExchangeStore(dsn)
    _open_on(seed_store)
    seed_store.close()

    app = build_rfp_board_app(extraction=extractor(), clock=lambda: NOW)
    proc = app.state.email_processor
    assert isinstance(proc, EmailProcessor)  # built by the host from env
    assert proc.board is app.state.rfps
    assert isinstance(app.state.rfps.store, SqlExchangeStore)

    with TestClient(app) as client:
        body = client.get(f"/rfps/{RFP_ID}").json()
        assert [c["party"] for c in body["carriers"]] == list(CARRIERS)  # not seeded
        assert all(
            c["reply_address"].startswith("rfp-quotes+") for c in body["carriers"]
        )
        eml = tmp_path / "reply.eml"
        eml.write_bytes(
            compose(
                _reply_address(client, "carrier-c"),
                message_id="<quote-1@carrier-c.example>",
                sender="quotes@carrier-c.example",
                attachments=quote_attachments(),
            )
        )
        assert cli(["land", str(eml)]) == 0
        key = landing_key(eml.read_bytes())
        assert capsys.readouterr().out.strip() == f"landed {key}"

        assert cli(["drain", "--url", "http://testserver"], client=client) == 0
        assert capsys.readouterr().out.strip().splitlines() == [
            f"filed {key} {RFP_ID} carrier-c filed=2 unreadable=1",
            "drained 1",
        ]
        cells = _cells(client)
        assert cells[("carrier-c", "CRITICAL_ILLNESS")] == "received"
        assert cells[("carrier-c", "HOSPITAL_INDEMNITY")] == "incomplete"
        marker = client.get(f"/email/outcomes/{key}").json()
        assert (marker["status"], marker["rfp_id"], marker["party"]) == (
            "filed",
            RFP_ID,
            "carrier-c",
        )
        assert [u["reason"] for u in marker["unreadable"]] == ["unsupported_media_type"]

        # Cross-process durability: a THIRD board over the same DSN sees the filing.
        reader = SqlExchangeStore(dsn)
        try:
            third = RfpBoard(policy=load_rfp_policy(PROCESS), store=reader)
            cell = third.rfps[RFP_ID].cell("carrier-c", "CRITICAL_ILLNESS")
            assert cell.status is RfpItemStatus.received
        finally:
            reader.close()

        assert cli(["drain", "--url", "http://testserver"], client=client) == 0
        assert capsys.readouterr().out.strip().splitlines() == ["drained 0"]

    # Clean shutdown closed the store (a closed SqlExchangeStore refuses further use).
    with pytest.raises(RuntimeError, match="closed"):
        app.state.rfps.store.list("rfp")


# --------------------------------------------------------------------------- #
# 2. The process comes from env (never a core literal)                         #
# --------------------------------------------------------------------------- #
def test_process_is_required(clean_env):
    with pytest.raises(RuntimeError, match=RFP_PROCESS_ENV):
        build_rfp_board_app()
    clean_env.setenv(RFP_PROCESS_ENV, "   ")
    with pytest.raises(RuntimeError, match=RFP_PROCESS_ENV):
        build_rfp_board_app()


def test_a_non_emergent_collect_skill_fails_at_build(clean_env):
    with pytest.raises(ValueError, match="address-proof"):
        build_rfp_board_app(process="address-proof")


def test_explicit_process_beats_env(clean_env):
    clean_env.setenv(RFP_PROCESS_ENV, "address-proof")
    app = build_rfp_board_app(process=PROCESS)
    assert app.state.rfps.policy.process == PROCESS


# --------------------------------------------------------------------------- #
# 3. Never a processor over an in-memory board                                 #
# --------------------------------------------------------------------------- #
def test_email_env_without_a_durable_store_is_refused(host_env):
    host_env.delenv("BRIDGE_EXCHANGE_DB_URL")
    with pytest.raises(RuntimeError, match="BRIDGE_EXCHANGE_DB_URL"):
        build_rfp_board_app(extraction=extractor())
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        build_rfp_board_app(exchange_store=InMemoryExchangeStore())


def test_no_email_env_serves_the_read_api_over_in_memory(clean_env):
    clean_env.setenv(RFP_PROCESS_ENV, PROCESS)
    app = build_rfp_board_app()
    assert app.state.email_processor is None
    assert isinstance(app.state.rfps.store, InMemoryExchangeStore)
    with TestClient(app) as client:
        assert client.get("/rfps").json() == []
        assert client.post("/email/drain").status_code == 404


# --------------------------------------------------------------------------- #
# 4-5. Lifespan: the poll loop runs once on the OUTER app; the store closes     #
# --------------------------------------------------------------------------- #
def test_poll_loop_files_a_landed_reply_with_no_explicit_drain(host_env, dsn):
    seed = SqlExchangeStore(dsn)
    _open_on(seed)
    seed.close()
    app = build_rfp_board_app(
        extraction=extractor(), clock=lambda: NOW, email_drain_interval=0.05
    )
    proc = app.state.email_processor
    drains = 0
    real_drain = proc.drain

    async def counting_drain():
        nonlocal drains
        drains += 1
        return await real_drain()

    proc.drain = counting_drain
    with TestClient(app) as client:
        raw = compose(
            _reply_address(client, "carrier-b"),
            message_id="<quote-1@carrier-b.example>",
            sender="quotes@carrier-b.example",
            attachments=[(CI_PDF.name, CI_PDF.read_bytes(), "application/pdf")],
        )
        key = landing_key(raw)
        assert proc.seam.land(key, raw)
        assert _wait_for(
            lambda: _cells(client)[("carrier-b", "CRITICAL_ILLNESS")] == "received"
        )
        # One loop: ~interval-spaced ticks, not two loops racing (the mounted
        # dashboard's own lifespan never runs).
        start, t0 = drains, time.monotonic()
        time.sleep(0.5)
        rate = (drains - start) / (time.monotonic() - t0)
        assert rate < 1 / 0.05 * 1.5
    assert proc.seam.processed(key)["status"] == "filed"


def test_lifespan_closes_the_store(clean_env):
    clean_env.setenv(RFP_PROCESS_ENV, PROCESS)

    class SpyStore(InMemoryExchangeStore):
        closed = 0

        def close(self) -> None:
            SpyStore.closed += 1

    store = SpyStore()
    app = build_rfp_board_app(exchange_store=store)
    with TestClient(app) as client:
        assert client.get("/rfps").status_code == 200
        assert SpyStore.closed == 0
    assert SpyStore.closed == 1


# --------------------------------------------------------------------------- #
# 7-9. Processor phase-3 safety across replicas                                #
# --------------------------------------------------------------------------- #
class _Wrapping(ExchangeStore):
    """Delegates to ``inner``; ``before_put`` runs first and may raise / race."""

    def __init__(self, inner: ExchangeStore, before_put=None) -> None:
        self.inner = inner
        self.before_put = before_put
        self.puts = 0

    def get(self, kind, ref):
        return self.inner.get(kind, ref)

    def list(self, kind):
        return self.inner.list(kind)

    def put(self, kind, ref, *, context_id, doc, expected_version):
        self.puts += 1
        if self.before_put is not None:
            self.before_put(self.puts)
        return self.inner.put(
            kind, ref, context_id=context_id, doc=doc, expected_version=expected_version
        )


def _replica(store: ExchangeStore, mail_dir) -> EmailProcessor:
    """One replica: its own board + seam handle over the SHARED store and landing dir."""
    from bridge.seams.local import LocalEmailIngress

    board = RfpBoard(policy=load_rfp_policy(PROCESS), clock=lambda: NOW, store=store)
    return EmailProcessor(
        board=board,
        seam=LocalEmailIngress(mail_dir),
        mailbox=MAILBOX,
        secret=SECRET,
        extraction=extractor(),
        artifact_store=InMemoryArtifactService(),
        base_url=BASE_URL,
    )


def _land_ci_reply(proc: EmailProcessor, party: str = "carrier-a") -> str:
    raw = compose(
        proc.reply_address(RFP_ID, party),
        attachments=[(CI_PDF.name, CI_PDF.read_bytes(), "application/pdf")],
    )
    key = landing_key(raw)
    assert proc.seam.land(key, raw)
    return key


def test_crash_between_put_and_marker_files_no_duplicate_version(tmp_path):
    store = InMemoryExchangeStore()
    _open_on(store)
    a, b = _replica(store, tmp_path), _replica(store, tmp_path)
    key = _land_ci_reply(a)

    def crash(*_args, **_kwargs):
        raise OSError("instance killed before the marker")

    a.seam.mark_processed = crash
    with pytest.raises(OSError):
        asyncio.run(a.drain())
    rfp = b.board.rfps[RFP_ID]
    assert rfp.cell("carrier-a", "CRITICAL_ILLNESS").versions == [f"email-{key}-0"]

    (outcome,) = asyncio.run(b.drain())
    assert outcome.status == "filed"
    assert [f.doc_id for f in outcome.filed] == [f"email-{key}-0"]
    assert outcome.filed[0].product_line == "CRITICAL_ILLNESS"
    assert outcome.filed[0].artifact_uri
    rfp = b.board.rfps[RFP_ID]
    assert rfp.cell("carrier-a", "CRITICAL_ILLNESS").versions == [f"email-{key}-0"]
    doc_ids = [e.doc_id for e in rfp.entries]
    assert len(doc_ids) == len(set(doc_ids)) == 1
    assert b.seam.processed(key)["status"] == "filed"
    assert b.seam.pending() == []


def test_cas_conflict_is_retried_and_keeps_both_writes(tmp_path, caplog):
    inner = InMemoryExchangeStore()
    _open_on(inner)
    other = RfpBoard(policy=load_rfp_policy(PROCESS), store=inner)

    def race(n: int) -> None:
        if n == 1:  # another replica commits a chase between our read and our put
            other.put(
                record_chase(
                    other.rfps[RFP_ID],
                    "carrier-b",
                    now=NOW,
                    step=LifecycleStatus.on_track,
                    channel="email",
                )
            )

    store = _Wrapping(inner, race)
    proc = _replica(store, tmp_path)
    key = _land_ci_reply(proc)
    with caplog.at_level("INFO", logger="bridge.edge.email_ingress"):
        (outcome,) = asyncio.run(proc.drain())
    assert outcome.status == "filed"
    assert store.puts == 2
    assert "changed concurrently; retrying" in caplog.text
    rfp = RfpBoard(policy=load_rfp_policy(PROCESS), store=inner).rfps[RFP_ID]
    assert rfp.leg_for("carrier-b").chases == 1  # the concurrent write survived
    cell = rfp.cell("carrier-a", "CRITICAL_ILLNESS")
    assert (cell.status, cell.versions) == (RfpItemStatus.received, [f"email-{key}-0"])


def _always_stale(n: int) -> None:
    raise StaleExchangeWrite("rfp", RFP_ID, n)


def test_exhausted_cas_retry_leaves_the_mail_pending(tmp_path):
    inner = InMemoryExchangeStore()
    _open_on(inner)
    store = _Wrapping(inner, _always_stale)
    proc = _replica(store, tmp_path)
    key = _land_ci_reply(proc)
    with pytest.raises(StaleExchangeWrite):
        asyncio.run(proc.drain())
    assert store.puts == 3
    assert proc.seam.processed(key) is None
    assert proc.seam.pending() == [key]
    cell = RfpBoard(policy=load_rfp_policy(PROCESS), store=inner).rfps[RFP_ID].cell(
        "carrier-a", "CRITICAL_ILLNESS"
    )
    assert cell.status is RfpItemStatus.invited


def test_exhausted_cas_retry_is_a_409_on_the_host(host_env, tmp_path):
    inner = InMemoryExchangeStore()
    _open_on(inner)
    app = build_rfp_board_app(
        exchange_store=_Wrapping(inner, _always_stale), extraction=extractor()
    )
    proc = app.state.email_processor
    key = _land_ci_reply(proc)
    with TestClient(app) as client:
        resp = client.post("/email/drain")
        assert resp.status_code == 409
        assert "changed concurrently" in resp.json()["detail"]
    assert proc.seam.pending() == [key]


# --------------------------------------------------------------------------- #
# 10. StaleExchangeWrite → 409 on the chase and the carrier upload             #
# --------------------------------------------------------------------------- #
def _stale_board() -> RfpBoard:
    board = make_board()
    return RfpBoard(
        policy=board.policy,
        clock=lambda: NOW,
        store=_Wrapping(board.store, _always_stale),
    )


def test_chase_conflict_is_a_409(clean_env):
    board = _stale_board()
    app = build_dashboard_app(
        rfps=board,
        intake=IntakeAddressing(MAILBOX, SECRET),
        outbound=OutboundClient(FakeTransport()),
    )
    resp = TestClient(app).post(f"/rfps/{RFP_ID}/chase", json={"party": "carrier-b"})
    assert resp.status_code == 409
    assert resp.json() == {"detail": f"RFP {RFP_ID!r} changed concurrently; retry"}


def test_upload_conflict_is_a_409(clean_env):
    board = _stale_board()
    app = build_portal_app(
        rfps=board,
        intake=IntakeAddressing(MAILBOX, SECRET),
        extraction=FixtureExtractor(corpus_path=CONFORMANCE),
    )
    ctx = board.rfps[RFP_ID].leg_for("carrier-a").context_id
    resp = TestClient(app).post(
        f"/rfp/legs/{ctx}/uploads",
        json={
            "doc_id": "a-ci-1",
            "artifact_name": "benefits-conformance-ci-usd",
            "product_line": "CRITICAL_ILLNESS",
        },
    )
    assert resp.status_code == 409
    assert "changed concurrently" in resp.json()["detail"]
