"""The conformance journey harness + a runnable T2 driver (S0-arch-9).

``uv run python -m tests.conformance.journey`` runs every record of
``wiki/evals/benefits/conformance.json`` over both legs (the fixture engine and the
gemini engine replaying the oracle) crossed with both by-reference shapes (a loopback
``http://`` URL, a ``gs://`` object), prints one line per run, the mapped consumer
quote per record, the parity verdict per record and the expected ``ConformanceGap`` of
a mutated rate sheet, and exits non-zero on any mismatch.

The pieces are reusable by ``test_benefits_conformance.py``: :func:`serve_corpus` (a
threaded loopback server over the committed PDFs), :class:`ReplayClient` (a genai
client double answering with a record's oracle extraction), :func:`build_app` (the real
A2A edge over a chosen engine) and :func:`send_by_reference` (one collect turn over the
A2A client, returning the decoded ``ExchangeTurn``).

Parity is **terminal-outcome, never ledger-identical**: :func:`terminal_outcome` holds no
confidence float and no source uri.
"""

from __future__ import annotations

import asyncio
import copy
import http.server
import json
import os
import sys
import threading
import uuid
from collections.abc import Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from a2a.client import ClientConfig, ClientFactory
from a2a.helpers import proto_helpers as ph
from a2a.types import GetTaskRequest, Message, Role, SendMessageRequest, Task
from a2a.utils.constants import TransportProtocol
from google.adk.models import BaseLlm
from google.protobuf import struct_pb2
from google.protobuf.json_format import MessageToDict, ParseDict

from bridge.collect import Collector
from bridge.edge import CollectExecutor, build_a2a_app, encode_collect_request
from bridge.edge.pathroute import EdgeMode, resolve_edge_mode
from bridge.edge.wire import KIND_EXCHANGE_TURN, META_CONTRACT_VERSION, META_KIND
from bridge.extraction.corpus_pdf import resolve_corpus_pdf_bytes
from bridge.extraction.fixture import FixtureExtractor
from bridge.extraction.gemini import GeminiExtractor
from contract.wire import CollectRequest, ExchangeTurn, LedgerEntry, decode_wire
from tests.conformance.consumer import (
    BenefitsQuote,
    ConformanceGap,
    ConsumerIntake,
    map_entry_to_quote,
)
from tests.corpus_pdf import BENEFITS_ROOT, CONFORMANCE_PATH

BASE = "http://test"
URL = "http://test/"
PROCESS = "benefits-procurement"
GS_BUCKET = "gs://conformance-bucket"
LEGS = ("fixture", "gemini")
REFS = ("http", "gs")

RECORDS: dict[str, dict] = {
    record["id"]: record
    for record in json.loads(CONFORMANCE_PATH.read_text(encoding="utf-8"))["documents"]
}


# --------------------------------------------------------------------------- #
# The loopback corpus server                                                   #
# --------------------------------------------------------------------------- #
@dataclass
class CorpusServer:
    """A running loopback server: its base URL and every path it was asked for."""

    base_url: str
    seen: list[str] = field(default_factory=list)

    @property
    def host_port(self) -> str:
        """The ``host:port`` to put in ``BRIDGE_FETCH_ALLOWLIST``."""
        return self.base_url.removeprefix("http://")

    def url_for(self, record_id: str) -> str:
        return f"{self.base_url}/{RECORDS[record_id]['artifact']}"


@contextmanager
def serve_corpus() -> Iterator[CorpusServer]:
    """Serve ``BENEFITS_ROOT/images/*.pdf`` over loopback HTTP; 404 anything else.

    The egress guard refuses loopback unless its ``host:port`` is in
    ``BRIDGE_FETCH_ALLOWLIST`` — that allowlisting is the caller's job (and the feature
    under test), not a workaround.
    """
    images = (BENEFITS_ROOT / "images").resolve()
    server = CorpusServer(base_url="")

    class _Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):  # keep the output clean
            pass

        def do_GET(self):
            server.seen.append(self.path)
            rel = self.path.split("?", 1)[0].lstrip("/")
            target = (BENEFITS_ROOT / rel).resolve()
            synthesized = (
                resolve_corpus_pdf_bytes(BENEFITS_ROOT, rel)
                if rel.startswith("images/") and target.suffix == ".pdf"
                else None
            )
            if (
                not rel.startswith("images/")
                or target.parent != images
                or target.suffix != ".pdf"
                or (not target.is_file() and synthesized is None)
            ):
                self.send_response(404)
                self.end_headers()
                return
            body = target.read_bytes() if target.is_file() else synthesized
            assert body is not None
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.base_url = f"http://127.0.0.1:{srv.server_address[1]}"
    # A short poll interval keeps shutdown (once per test) from costing 0.5s each.
    threading.Thread(
        target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    ).start()
    try:
        yield server
    finally:
        srv.shutdown()
        srv.server_close()


def reference_for(record_id: str, ref: str, server: CorpusServer | None) -> str:
    """The by-reference URI a consumer puts on the wire for ``record_id``."""
    if ref == "http":
        if server is None:
            raise ValueError("the http reference shape needs a running corpus server")
        return server.url_for(record_id)
    if ref == "gs":
        return f"{GS_BUCKET}/{RECORDS[record_id]['artifact']}"
    raise ValueError(f"unknown reference shape {ref!r}")


# --------------------------------------------------------------------------- #
# The gemini replay double                                                     #
# --------------------------------------------------------------------------- #
class _ReplayModels:
    def __init__(self, text: str) -> None:
        self._text = text
        self.calls: list[dict[str, Any]] = []

    def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        return type("_Response", (), {"text": self._text})()


class ReplayClient:
    """A genai ``Client`` double answering with ``record``'s oracle extraction.

    ``vertexai = True`` because the engine refuses a ``gs://`` reference on an explicit
    non-Vertex client. ``payload`` overrides the answer (mutation tests).
    """

    vertexai = True

    def __init__(self, record: dict, *, payload: dict | None = None) -> None:
        answer = record["extraction"] if payload is None else payload
        self.models = _ReplayModels(json.dumps(answer))

    @property
    def calls(self) -> list[dict[str, Any]]:
        return self.models.calls


# --------------------------------------------------------------------------- #
# The A2A edge over a chosen engine                                            #
# --------------------------------------------------------------------------- #
@dataclass
class EdgeConfig:
    """The edge routing mode every :func:`build_app` call defaults to (S3-edge-3).

    ``mode=None`` → ``build_a2a_app`` resolves ``BRIDGE_EDGE_MODE`` (unset →
    deterministic). A hybrid edge runs its LLM leg on ``llm`` — required, so the suite
    can never silently reach a real model.
    """

    mode: str | None = None
    llm: BaseLlm | None = None


# Read at call time (module attribute), so every build_app caller honours it — the
# conformance conftest's ``edge_mode`` fixture swaps it per test.
EDGE = EdgeConfig()


def build_app(
    leg: str,
    *,
    record_id: str | None = None,
    client=None,
    edge_mode: str | None = None,
    llm: BaseLlm | None = None,
):
    """The real Bridge A2A app over ``leg``'s engine.

    ``gemini`` passes **no** ``schemas=``: the prompt and pinned response schema are
    built from the installed ``skills/benefits-quote/assets/schema.json`` read through
    the ``skill_registry`` port — that is what "extracted against the skill schema"
    means here. ``live`` is the real engine (credentials required).

    ``edge_mode`` / ``llm`` default to the module-level :data:`EDGE`. In hybrid mode
    the LLM leg is built over ``llm`` (a ``ScriptedLlm`` in the tests); the extraction
    engine is unaffected.
    """
    if leg == "fixture":
        extractor = FixtureExtractor(corpus_path=CONFORMANCE_PATH)
    elif leg == "gemini":
        if client is None:
            if record_id is None:
                raise ValueError("the gemini replay leg needs a record_id or a client")
            client = ReplayClient(RECORDS[record_id])
        extractor = GeminiExtractor(client=client, corpus_root=BENEFITS_ROOT)
    elif leg == "live":
        extractor = GeminiExtractor(corpus_root=BENEFITS_ROOT)
    else:
        raise ValueError(f"unknown leg {leg!r}")
    executor = CollectExecutor(collector=Collector(extractor))
    mode = edge_mode if edge_mode is not None else EDGE.mode
    llm = llm if llm is not None else EDGE.llm
    llm_leg = None
    if resolve_edge_mode(mode) is EdgeMode.hybrid:
        if llm is None:
            raise ValueError("a hybrid conformance edge needs an llm (no real model)")
        from bridge.edge.llm_leg import LlmLeg

        llm_leg = LlmLeg(model=llm)
    return build_a2a_app(url=URL, executor=executor, edge_mode=mode, llm_leg=llm_leg)


@asynccontextmanager
async def a2a_client(app):
    """A conformant A2A client over the app in-process (JSON-RPC, streaming)."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as hc:
        config = ClientConfig(
            httpx_client=hc,
            streaming=True,
            supported_protocol_bindings=[TransportProtocol.JSONRPC.value],
        )
        yield await ClientFactory(config).create_from_url(URL)


@dataclass
class JourneyRun:
    """One collect turn's observable result, as the consumer reads it."""

    task: Task
    turn: ExchangeTurn
    part_contract_version: str | None  # the exchange_turn part's metadata stamp

    @property
    def entry(self) -> LedgerEntry:
        (entry,) = self.turn.status.entries
        return entry


def _url_part(record_id: str, uri: str):
    part = ph.new_url_part(uri, media_type="application/pdf", filename=Path(uri).name)
    part.metadata.CopyFrom(ParseDict({"doc_id": record_id}, struct_pb2.Struct()))
    return part


async def send_by_reference(
    app, *, record_id: str, uri: str, context_id: str | None = None
) -> JourneyRun:
    """Send one collect turn carrying ``uri`` BY REFERENCE; decode the ExchangeTurn.

    The exchange-turn part is selected by its ``metadata.kind``, never by index.
    ``context_id`` rides the ``CollectRequest`` (``None`` → the Collector mints one).
    """
    record = RECORDS[record_id]
    parts = encode_collect_request(
        CollectRequest(process=PROCESS, party=record["party"], context_id=context_id)
    )
    parts.append(_url_part(record_id, uri))
    msg = Message(role=Role.ROLE_USER, message_id=uuid.uuid4().hex, parts=parts)
    async with a2a_client(app) as client:
        task_id = None
        async for ev in client.send_message(SendMessageRequest(message=msg)):
            if ev.HasField("task"):
                task_id = ev.task.id
            elif ev.HasField("status_update"):
                task_id = ev.status_update.task_id
            elif ev.HasField("artifact_update"):
                task_id = ev.artifact_update.task_id
        task = await client.get_task(GetTaskRequest(id=task_id))
    for artifact in task.artifacts:
        for part in artifact.parts:
            meta = MessageToDict(part.metadata) if part.HasField("metadata") else {}
            if part.HasField("data") and meta.get(META_KIND) == KIND_EXCHANGE_TURN:
                turn = decode_wire(ExchangeTurn, MessageToDict(part.data))
                return JourneyRun(task, turn, meta.get(META_CONTRACT_VERSION))
    raise AssertionError("no exchange_turn artifact part on the task")


def intake_for(record_id: str) -> ConsumerIntake:
    return ConsumerIntake.model_validate(RECORDS[record_id]["consumer_intake"])


def terminal_outcome(entry: LedgerEntry, quote: BenefitsQuote) -> dict:
    """The parity key: dispositions, review paths and the mapped quote — no floats.

    Confidences and the source uri (which differs per leg and reference shape) are
    deliberately excluded: parity is terminal-outcome, never ledger-identical.
    """
    return {
        "doctype": entry.doctype,
        "disposition": entry.disposition.value,
        "gate": entry.gate.value,
        "reason_code": entry.reason_code,
        "fields_requiring_review": [r.path for r in entry.field_reviews],
        "quote": quote.model_dump(
            mode="json", exclude={"source", "extraction_confidence"}
        ),
    }


def gap_payload(record_id: str) -> dict:
    """``record_id``'s oracle extraction with two cells outside the consumer's vocabulary:
    row 0 billed ``WEEKLY`` and row 1 priced ``PER_1000``."""
    payload = copy.deepcopy(RECORDS[record_id]["extraction"])
    rates = payload["fields"]["tables"]["rates"]
    rates[0]["frequency"] = "WEEKLY"
    rates[1]["rate_basis"] = "PER_1000"
    return payload


# --------------------------------------------------------------------------- #
# The T2 driver                                                                #
# --------------------------------------------------------------------------- #
async def _run_all(server: CorpusServer) -> bool:
    ok = True
    for record_id, record in RECORDS.items():
        expected = record["expected_terminal"]
        outcomes: list[dict] = []
        quote_json: str | None = None
        for leg in LEGS:
            for ref in REFS:
                app = build_app(leg, record_id=record_id)
                uri = reference_for(record_id, ref, server)
                run = await send_by_reference(app, record_id=record_id, uri=uri)
                entry = run.entry
                review = [r.path for r in entry.field_reviews]
                print(
                    f"{leg:<7} {ref:<4} {record_id:<38} "
                    f"{entry.disposition.value}/{entry.gate.value} "
                    f"contract_version={run.part_contract_version} review={review}"
                )
                got = {
                    "disposition": entry.disposition.value,
                    "gate": entry.gate.value,
                    "reason_code": entry.reason_code,
                    "fields_requiring_review": review,
                }
                if got != expected:
                    print(f"  MISMATCH expected_terminal: {expected} != {got}")
                    ok = False
                quote = map_entry_to_quote(entry, intake_for(record_id))
                outcomes.append(terminal_outcome(entry, quote))
                if quote_json is None:
                    quote_json = quote.model_dump_json(indent=2)
                    dumped = quote.model_dump(mode="json", exclude={"source"})
                    if dumped != record["expected_consumer_quote"]:
                        print("  MISMATCH expected_consumer_quote")
                        ok = False
        print(f"consumer quote for {record_id}:\n{quote_json}")
        parity = all(o == outcomes[0] for o in outcomes)
        print(f"PARITY {'OK' if parity else 'FAIL'} {record_id} ({len(outcomes)} runs)")
        ok = ok and parity

    record_id = next(iter(RECORDS))
    client = ReplayClient(RECORDS[record_id], payload=gap_payload(record_id))
    app = build_app("gemini", client=client)
    run = await send_by_reference(
        app, record_id=record_id, uri=reference_for(record_id, "http", server)
    )
    print(
        f"gap run: bridge side {run.entry.disposition.value}/{run.entry.gate.value} "
        f"(cells verbatim: {[r['frequency'] for r in run.entry.tables['rates']]})"
    )
    try:
        map_entry_to_quote(run.entry, intake_for(record_id))
    except ConformanceGap as gap:
        print(f"ConformanceGap (expected): {gap}")
        named = " ".join(gap.gaps)
        if (
            "rates[0].frequency=WEEKLY" not in named
            or "rates[1].rate_basis=PER_1000" not in named
        ):
            print("  MISMATCH: the gap does not name both offending cells")
            ok = False
    else:
        print("  MISMATCH: the WEEKLY / PER_1000 mutation mapped without a gap")
        ok = False
    return ok


def main() -> int:
    with serve_corpus() as server:
        os.environ["BRIDGE_FETCH_ALLOWLIST"] = server.host_port
        ok = asyncio.run(_run_all(server))
    print("CONFORMANCE OK" if ok else "CONFORMANCE FAIL")
    return 0 if ok else 1


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    sys.exit(main())
