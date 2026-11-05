"""Downstream showcase contract over A2A (P9-3).

Boots the core from a throwaway tree holding ONE scenario folder, the skill bundles and
OKF bundles that scenario references, and the console static files — nothing else from
the repo — and checks what a downstream showcase relies on: the Agent Card, one Collect
round-trip, the scenario listing, ``/demo``, the standalone example consumer, and the
documentation that names the knobs.
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from types import ModuleType

import httpx
import pytest
import yaml
from a2a.client import ClientConfig, ClientFactory
from a2a.helpers import proto_helpers as ph
from a2a.types import GetTaskRequest, Message, Role, SendMessageRequest, TaskState
from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH, TransportProtocol
from fastapi.testclient import TestClient
from google.protobuf import struct_pb2
from google.protobuf.json_format import ParseDict

from bridge.collect import Collector
from bridge.dashboard.server import build_dashboard_app
from bridge.edge import CollectExecutor, build_a2a_app, encode_collect_request
from bridge.edge.skills import loaded_skills
from bridge.extraction.fixture import FixtureExtractor
from bridge.refinery.okf_loader import OKF_BUNDLE_SUFFIX, OKF_DOCTYPES_DIR_ENV
from bridge.showcase.loader import (
    DEMO_STATIC_DIR,
    DEMO_STATIC_DIR_ENV,
    MANIFEST_FILENAME,
    SCENARIOS_DIR,
    SCENARIOS_DIR_ENV,
    demo_static_root,
)
from contract.enums import Disposition
from contract.wire import CollectRequest, ExchangeTurn

REPO_ROOT = Path(__file__).resolve().parents[1]
SCENARIO_ID = "loan-meridian-001"
BASE = "http://test"
URL = "http://test/"
CONSUMER_DOC = REPO_ROOT / "docs" / "consume-over-a2a.md"
CONSUMER_EXAMPLE = REPO_ROOT / "examples" / "a2a_consumer" / "consume_bridge.py"
DOCUMENTED_KNOBS = (
    "BRIDGE_SKILLS_DIR",
    "BRIDGE_SKILLS_BUCKET",
    OKF_DOCTYPES_DIR_ENV,
    SCENARIOS_DIR_ENV,
    "BRIDGE_DEFAULT_DEMO_DOMAIN",
    DEMO_STATIC_DIR_ENV,
)


class ShowcaseTree:
    """The downstream-showcase tree: one scenario, its skills/bundles, console files."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.skills = root / "skills"
        self.doctypes = root / "catalog" / "doctypes"
        self.scenarios = root / "scenarios"
        self.static = root / "demo"
        self.corpus = root / "fixture-corpus.json"
        self.process: str = ""
        self.installed_doctypes: tuple[str, ...] = ()


def _copy_tree(src: Path, dst: Path) -> None:
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__"))


def _fixture_corpus(tree: ShowcaseTree, doctype: str) -> tuple[str, str]:
    """One corpus document whose extraction satisfies ``doctype``'s required keys."""
    schema = json.loads(
        (tree.skills / doctype / "assets" / "schema.json").read_text(encoding="utf-8")
    )
    required = schema["properties"]["key_fields"]["required"]
    key_fields = {name: f"fixture-{name}" for name in required}
    key_fields.update({k: v for k, v in {"currency": "USD"}.items() if k in required})
    doc_id = f"{doctype}-fixture"
    corpus = {
        "documents": [
            {
                "id": doc_id,
                "extraction": {
                    "fields": {
                        "doctype": doctype,
                        "issuer": "Fixture Issuer",
                        "key_fields": key_fields,
                    },
                    "overall_confidence": 0.97,
                    "field_confidence": dict.fromkeys(required, 0.97),
                    "legible": True,
                    "flagged_fields": [],
                },
            }
        ]
    }
    tree.corpus.write_text(json.dumps(corpus), encoding="utf-8")
    return doc_id, f"vault/{doc_id}.pdf"


@pytest.fixture
def tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ShowcaseTree:
    tree = ShowcaseTree(tmp_path)
    manifest = yaml.safe_load(
        (SCENARIOS_DIR / SCENARIO_ID / MANIFEST_FILENAME).read_text(encoding="utf-8")
    )
    tree.process = manifest["process_skill"]
    repo_skills = {s.name: s for s in loaded_skills()}
    tree.installed_doctypes = repo_skills[tree.process].candidate_doctypes

    _copy_tree(SCENARIOS_DIR / SCENARIO_ID, tree.scenarios / SCENARIO_ID)
    for name in (tree.process, *tree.installed_doctypes):
        _copy_tree(REPO_ROOT / "skills" / name, tree.skills / name)
    tree.doctypes.mkdir(parents=True)
    for name in tree.installed_doctypes:
        bundle = REPO_ROOT / "catalog" / "doctypes" / f"{name}{OKF_BUNDLE_SUFFIX}"
        if bundle.is_file():
            shutil.copy(bundle, tree.doctypes / bundle.name)
    tree.static.mkdir()
    for asset in DEMO_STATIC_DIR.iterdir():
        if asset.suffix in {".html", ".js", ".css", ".svg"}:
            shutil.copy(asset, tree.static / asset.name)

    monkeypatch.setenv("BRIDGE_SKILLS_DIR", str(tree.skills))
    monkeypatch.setenv(OKF_DOCTYPES_DIR_ENV, str(tree.doctypes))
    monkeypatch.setenv(SCENARIOS_DIR_ENV, str(tree.scenarios))
    monkeypatch.setenv(DEMO_STATIC_DIR_ENV, str(tree.static))
    monkeypatch.setenv("BRIDGE_DEFAULT_DEMO_DOMAIN", manifest["domain"])
    monkeypatch.delenv("BRIDGE_PUBLIC_URL", raising=False)
    return tree


def _core_app(tree: ShowcaseTree):
    executor = CollectExecutor(collector=Collector(FixtureExtractor(tree.corpus)))
    return build_a2a_app(url=URL, executor=executor, edge_mode="deterministic")


@asynccontextmanager
async def _client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as hc:
        config = ClientConfig(
            httpx_client=hc,
            streaming=True,
            supported_protocol_bindings=[TransportProtocol.JSONRPC.value],
        )
        yield hc, await ClientFactory(config).create_from_url(URL)


def _load_example() -> ModuleType:
    spec = importlib.util.spec_from_file_location("consume_bridge", CONSUMER_EXAMPLE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------- #
# (a) the core booted from the downstream tree                                 #
# --------------------------------------------------------------------------- #
async def test_agent_card_advertises_exactly_the_installed_process_skills(
    tree: ShowcaseTree,
) -> None:
    async with _client(_core_app(tree)) as (hc, _client_obj):
        body = (await hc.get(AGENT_CARD_WELL_KNOWN_PATH)).json()
    assert [s["id"] for s in body["skills"]] == [tree.process]
    tags = set(body["skills"][0]["tags"])
    assert {f"doctype:{d}" for d in tree.installed_doctypes} <= tags
    assert "application/json" in body["defaultInputModes"]
    assert body["defaultOutputModes"] == ["application/json"]
    assert body["capabilities"]["streaming"] is True
    assert body["capabilities"]["pushNotifications"] is True
    assert body["supportedInterfaces"][0]["url"] == URL


async def test_one_collect_round_trip_reaches_a_terminal_disposition(
    tree: ShowcaseTree,
) -> None:
    doc_id, uri = _fixture_corpus(tree, tree.installed_doctypes[0])
    parts = encode_collect_request(CollectRequest(process=tree.process))
    part = ph.new_url_part(uri, media_type="application/pdf")
    part.metadata.CopyFrom(ParseDict({"doc_id": doc_id}, struct_pb2.Struct()))
    parts.append(part)
    message = Message(role=Role.ROLE_USER, message_id=uuid.uuid4().hex, parts=parts)

    async with _client(_core_app(tree)) as (_hc, client):
        task_id = None
        async for ev in client.send_message(SendMessageRequest(message=message)):
            if ev.HasField("task"):
                task_id = ev.task.id
            elif ev.HasField("status_update"):
                task_id = ev.status_update.task_id
        assert task_id is not None
        task = await client.get_task(GetTaskRequest(id=task_id))

    assert task.status.state == TaskState.TASK_STATE_COMPLETED
    turn = _load_example().exchange_turn_from(task)
    assert isinstance(turn, ExchangeTurn) and turn.terminal is True
    [entry] = turn.status.entries
    assert entry.doc_id == doc_id
    assert entry.doctype == tree.installed_doctypes[0]
    assert entry.disposition is Disposition.accepted


def test_demo_surface_lists_exactly_the_installed_scenario(tree: ShowcaseTree) -> None:
    assert demo_static_root() == tree.static
    client = TestClient(build_dashboard_app())
    listed = client.get("/v1/demo/scenarios")
    assert listed.status_code == 200
    assert [s["id"] for s in listed.json()["scenarios"]] == [SCENARIO_ID]
    assert client.get(f"/v1/demo/scenarios/{SCENARIO_ID}/state").status_code == 200

    demo = client.get("/demo")
    assert demo.status_code == 200
    assert "text/html" in demo.headers["content-type"]
    for asset in ("/demo/enterprise-demo.js", "/demo/enterprise-demo.css", "/ebc"):
        assert client.get(asset).status_code == 200, asset

    (tree.static / "enterprise-demo.html").unlink()
    assert client.get("/demo").status_code == 404


# --------------------------------------------------------------------------- #
# (b) the standalone example consumer                                          #
# --------------------------------------------------------------------------- #
async def test_example_consumer_prints_a_disposition(
    tree: ShowcaseTree, capsys: pytest.CaptureFixture[str]
) -> None:
    doc_id, uri = _fixture_corpus(tree, tree.installed_doctypes[0])
    example = _load_example()
    transport = httpx.ASGITransport(app=_core_app(tree))
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as hc:
        turn = await example.run(URL, tree.process, [(doc_id, uri)], httpx_client=hc)
    out = capsys.readouterr().out
    assert turn.status.entries[0].disposition is Disposition.accepted
    assert f"skills=['{tree.process}']" in out
    assert f"{doc_id}: accepted" in out
    assert "terminal=True" in out


async def test_example_consumer_refuses_an_unadvertised_process(
    tree: ShowcaseTree,
) -> None:
    example = _load_example()
    transport = httpx.ASGITransport(app=_core_app(tree))
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as hc:
        with pytest.raises(SystemExit):
            await example.run(URL, "not-installed", [], httpx_client=hc)


# --------------------------------------------------------------------------- #
# (c) the documentation names every knob and nothing undocumented              #
# --------------------------------------------------------------------------- #
def test_consume_doc_names_every_knob_and_env_example_has_them() -> None:
    assert CONSUMER_DOC.is_file()
    text = CONSUMER_DOC.read_text(encoding="utf-8")
    assert len(text.splitlines()) <= 150
    for knob in DOCUMENTED_KNOBS:
        assert knob in text, f"{knob} missing from {CONSUMER_DOC.name}"
    env_example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    for var in sorted(set(re.findall(r"\bBRIDGE_[A-Z0-9_]+\b", text))):
        assert var in env_example, (
            f"{var} named in the doc but absent from .env.example"
        )
    for anchor in (
        "/.well-known/agent-card.json",
        "/v1/demo/scenarios",
        "CollectRequest",
        "LedgerEntry",
        "examples/a2a_consumer/consume_bridge.py",
        "bridge/edge/card.py",
    ):
        assert anchor in text, f"{anchor} missing from {CONSUMER_DOC.name}"
    for link in re.findall(r"\]\(([^)#]+)(?:#[^)]*)?\)", text):
        if "://" in link:
            continue
        assert (CONSUMER_DOC.parent / link).resolve().exists(), link
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    contributing = (REPO_ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    assert readme.count("docs/consume-over-a2a.md") >= 2
    assert "docs/consume-over-a2a.md" in contributing
