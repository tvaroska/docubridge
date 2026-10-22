"""Documentation contract, shared SQL runtime multiplexing, and CI/CD pipeline tests.

Verifies the three showcase-readiness quality pillars:
1. Documentation in ``README.md``, ``BANKING.md``, ``DECISIONS.md``, ``.env.example``,
   and ``docs/demo-click-path.md`` stays 100% synchronized with live FastAPI routes,
   uvicorn app factories, seeded showcase entities, and glossary/trust-slice IDs.
2. All four SQL-backed stores (``SqlExchangeStore``, ``SqlOperationalStore``,
   ``SqlAgentGymEngine``, ``SqlActionInboxStore``) multiplex over a single refcounted
   ``DsnSqlRuntime`` (one daemon event-loop thread and one ``AsyncEngine`` pool per DSN).
3. ``Dockerfile`` enforces non-root execution (``USER appuser``) and ``cloudbuild.yaml``
   maintains a parallel multi-track DAG with full frontend workspace coverage and an
   85%+ Python coverage gate before pushing images.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import yaml

from bridge.a2ui_inbox import SqlActionInboxStore
from bridge.agent_gym.models import DEFAULT_TRUST_POLICIES
from bridge.agent_gym.sql_store import SqlAgentGymEngine
from bridge.dashboard.server import build_dashboard_app
from bridge.exchange_store import SqlExchangeStore
from bridge.knowledge_catalog import InMemoryKnowledgeCatalog
from bridge.operational_store import (
    ConsolidatedFact,
    InMemoryOperationalStore,
    SqlOperationalStore,
)
from bridge.sql_runtime import get_active_dsn_runtime
from bridge.user_interaction.demo_seed import seed_demo_scenarios

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_uvicorn_factories_in_docs_are_importable_callables() -> None:
    """Every ``uvicorn <module>:<factory>`` command in markdown docs resolves cleanly."""
    pattern = re.compile(r"uvicorn\s+([a-zA-Z0-9_.]+):([a-zA-Z0-9_]+)")
    checked = 0
    for doc_rel in ("README.md", "BANKING.md", "docs/demo-click-path.md"):
        text = (REPO_ROOT / doc_rel).read_text(encoding="utf-8")
        for mod_name, attr_name in pattern.findall(text):
            mod = importlib.import_module(mod_name)
            factory = getattr(mod, attr_name, None)
            assert callable(factory), (
                f"{doc_rel} references non-callable {mod_name}:{attr_name}"
            )
            checked += 1
    assert checked >= 1


def test_documented_http_endpoints_exist_on_dashboard_app() -> None:
    """Every ``/v1/...`` route cited in ``BANKING.md`` and ``README.md`` exists."""
    app = build_dashboard_app()
    openapi_paths = list(app.openapi()["paths"].keys())
    route_regexes = [
        re.compile("^" + re.sub(r"\{[^/]+\}", r"[^/?]+", path) + "$")
        for path in openapi_paths
    ]

    for doc_rel in ("README.md", "BANKING.md", "docs/demo-click-path.md"):
        text = (REPO_ROOT / doc_rel).read_text(encoding="utf-8")
        assert "/api/v1/" not in text, (
            f"{doc_rel} contains non-existent '/api/v1/' route prefix; use '/v1/'"
        )
        for match in re.finditer(
            r"\b(?:GET|POST|PUT|DELETE)\s+(/v1/[a-zA-Z0-9_./{}-]+)", text
        ):
            raw_path = match.group(1)
            normalized = re.sub(r"\{[^/]+\}", "sample-id", raw_path)
            assert any(r.match(normalized) for r in route_regexes), (
                f"{doc_rel} documents unmounted route {raw_path!r}"
            )


def test_banking_md_entities_glossary_and_slices_match_codebase() -> None:
    """``BANKING.md`` entity IDs, glossary terms, and trust slices match live seeds."""
    banking_text = (REPO_ROOT / "BANKING.md").read_text(encoding="utf-8")

    mem_store = InMemoryOperationalStore()
    catalog = InMemoryKnowledgeCatalog(operational_store=mem_store)
    seeded = seed_demo_scenarios(
        operational_store=mem_store,
        knowledge_catalog=catalog,
    )

    for entity_id in (
        "LOAN-MERIDIAN-001",
        "MORTGAGE-WESTBROOK-2026",
        "terrepower-coastal-2027",
    ):
        assert entity_id in banking_text, (
            f"Seeded entity {entity_id!r} missing from BANKING.md"
        )
        assert mem_store.list_facts(entity_id=entity_id), (
            f"Seeded entity {entity_id!r} has no facts in seed_demo_scenarios()"
        )
    assert seeded is not None

    glossary_ids = {t.term_id for t in catalog.list_glossary_terms()}
    for term_id in re.findall(r"\bterm\.[a-z0-9_]+\b", banking_text):
        assert term_id in glossary_ids, (
            f"BANKING.md references unknown glossary term {term_id!r}"
        )

    policy_slices = {p.slice_id for p in DEFAULT_TRUST_POLICIES}
    for slice_id in re.findall(r"\bslice:[a-zA-Z0-9_:-]+\b", banking_text):
        assert slice_id in policy_slices, (
            f"BANKING.md references unknown trust policy slice {slice_id!r}"
        )


def test_decisions_md_and_env_example_match_architecture() -> None:
    """``DECISIONS.md`` and ``.env.example`` reflect current GCP architecture & models."""
    decisions = (REPO_ROOT / "DECISIONS.md").read_text(encoding="utf-8")
    assert "Firestore" not in decisions
    assert "contract/rfp.py" not in decisions
    assert "contract/quote.py" not in decisions
    for anchor in (
        "2026-08-24",
        "2026-09-07",
        "S0-arch-13",
        "S0-arch-14",
        "S0-arch-17",
        "P7-2",
        "P7-4",
        "P7-5",
        "P7-9",
    ):
        assert anchor in decisions, f"Historical decision anchor {anchor!r} missing"

    env_example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    for expected in (
        "BRIDGE_MODEL=gemini-3.8-flash",
        "BRIDGE_REASONING_MODEL=gemini-4-argon",
        "BRIDGE_INBOX_DB_URL=",
        "BRIDGE_GYM_DB_URL=",
        "BRIDGE_SPIFFE_SVID_SECRET=",
        "BRIDGE_TELEMETRY_BQ_TABLE=",
    ):
        assert expected in env_example, f"{expected!r} missing from .env.example"


def test_four_sql_stores_share_one_dsn_runtime_and_engine(tmp_path: Path) -> None:
    """All 4 SQL stores on one DSN share 1 thread, 1 event loop, and 1 AsyncEngine."""
    dsn = f"sqlite+aiosqlite:///{tmp_path}/shared_runtime.db"
    ex = SqlExchangeStore(dsn)
    op = SqlOperationalStore(dsn)
    gym = SqlAgentGymEngine(dsn)
    inbox = SqlActionInboxStore(dsn)

    try:
        ex.put("rfp", "r-1", context_id="ctx-1", doc={"ok": True}, expected_version=0)
        op.append_facts(
            [
                ConsolidatedFact(
                    fact_id="fact-shared-1",
                    entity_id="LOAN-MERIDIAN-001",
                    context_id="ctx-1",
                    doc_id="doc-1",
                    doctype="loan-application",
                    issuer="meridian-bank",
                    field_path="loan_amount",
                    field_value="45000000.00",
                    numeric_value="45000000.00",
                    currency="USD",
                    source_priority=10,
                    recorded_at="2026-10-22T00:00:00Z",
                    effective_from="2026-10-22",
                    version=1,
                )
            ]
        )
        assert len(gym.list_trust_policies()) >= 1
        created_item = inbox.create_action(
            entity_id="LOAN-MERIDIAN-001",
            context_id="ctx-1",
            title="Verify Fee Override",
            hint="Check Page 9 addendum",
        )

        rt = get_active_dsn_runtime(dsn)
        assert rt is not None
        assert rt.refcount == 4
        assert ex._engine is op._engine is gym._engine is inbox._engine is rt.engine
        assert ex._thread is op._thread is gym._thread is inbox._thread is rt.thread
        shared_thread = rt.thread
        assert shared_thread.is_alive()

        # Closing 3 of the 4 stores keeps the runtime alive for the remaining store.
        ex.close()
        op.close()
        gym.close()
        assert rt.refcount == 1
        assert shared_thread.is_alive()
        assert inbox.get_item(created_item.item_id) is not None

        # Closing the final store disposes the shared engine and stops the thread.
        inbox.close()
        assert get_active_dsn_runtime(dsn) is None
        assert not shared_thread.is_alive()
    finally:
        ex.close()
        op.close()
        gym.close()
        inbox.close()


def test_dockerfile_and_cloudbuild_parallel_dag_invariants() -> None:
    """``Dockerfile`` runs as non-root and ``cloudbuild.yaml`` uses a parallel DAG."""
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "USER appuser" in dockerfile
    assert "COPY agents/" not in dockerfile
    assert "ghcr.io/astral-sh/uv:0.6-python3.12-bookworm-slim" in dockerfile

    cb = yaml.safe_load((REPO_ROOT / "cloudbuild.yaml").read_text(encoding="utf-8"))
    steps = {s["id"]: s for s in cb["steps"]}

    # Four tracks start immediately at t=0 (waitFor: ["-"]).
    for root_step in (
        "resolve-git-metadata",
        "python-env-and-lint",
        "frontend-tests-and-typecheck",
        "terraform-plan-tests",
    ):
        assert steps[root_step].get("waitFor") == ["-"], (
            f"{root_step} must start at t=0 with waitFor: ['-']"
        )

    # Both Python test shards fan out in parallel after python-env-and-lint.
    assert steps["python-core-and-enterprise-tests"]["waitFor"] == [
        "python-env-and-lint"
    ]
    assert steps["python-agent-and-golden-tests"]["waitFor"] == ["python-env-and-lint"]

    # Coverage gate combines both shards and enforces >=85%.
    cov_step = steps["python-coverage-gate"]
    assert set(cov_step["waitFor"]) == {
        "python-core-and-enterprise-tests",
        "python-agent-and-golden-tests",
    }
    assert "--fail-under=85" in " ".join(cov_step["args"])

    # Docker build runs in parallel with test suites; push waits for all gates.
    assert steps["docker-build-image"]["waitFor"] == ["resolve-git-metadata"]
    assert set(steps["docker-push-image"]["waitFor"]) == {
        "docker-build-image",
        "python-coverage-gate",
        "frontend-tests-and-typecheck",
        "terraform-plan-tests",
    }
