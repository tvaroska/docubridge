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
import tomllib
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

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

    # Coverage gate combines both shards and enforces >=90%.
    cov_step = steps["python-coverage-gate"]
    assert set(cov_step["waitFor"]) == {
        "python-core-and-enterprise-tests",
        "python-agent-and-golden-tests",
    }
    assert "--fail-under=90" in " ".join(cov_step["args"])
    assert "bridge-serve" in dockerfile

    # Docker build runs in parallel with test suites; push waits for all gates.
    assert steps["docker-build-image"]["waitFor"] == ["resolve-git-metadata"]
    assert set(steps["docker-push-image"]["waitFor"]) == {
        "docker-build-image",
        "python-coverage-gate",
        "frontend-tests-and-typecheck",
        "terraform-plan-tests",
    }


def test_secondary_docs_list_all_13_hexagonal_ports() -> None:
    """``design/seams.md`` and ``TODO.md`` enumerate all 13 Hexagonal ``Port`` names."""
    from bridge.ports import Port

    seams_doc = (REPO_ROOT / "design/seams.md").read_text(encoding="utf-8")
    todo_doc = (REPO_ROOT / "TODO.md").read_text(encoding="utf-8")
    for port in Port:
        assert port.value in seams_doc, (
            f"design/seams.md missing Hexagonal Port {port.value!r}"
        )
        assert port.value in todo_doc, f"TODO.md missing Hexagonal Port {port.value!r}"

    pyproject = tomllib.loads(
        (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    assert pyproject["build-system"]["build-backend"] == "hatchling.build"
    assert pyproject["tool"]["coverage"]["report"]["fail_under"] >= 90


def test_enterprise_demo_console_assets_and_api_contract() -> None:
    """``/demo`` HTML/JS/CSS assets serve cleanly and all JS ``/v1/`` routes exist."""
    app = build_dashboard_app()
    client = TestClient(app)

    for route in ("/demo", "/console"):
        resp = client.get(route)
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")

    js_resp = client.get("/demo/enterprise-demo.js")
    assert js_resp.status_code == 200
    assert "javascript" in js_resp.headers.get("content-type", "")

    css_resp = client.get("/demo/enterprise-demo.css")
    assert css_resp.status_code == 200
    assert "text/css" in css_resp.headers.get("content-type", "")

    html_text = (REPO_ROOT / "demo/enterprise-demo.html").read_text(encoding="utf-8")
    for required_id in (
        'id="scene-portal"',
        'id="scene-intake"',
        'id="scene-consolidation"',
        'id="scene-inbox"',
        'id="scene-gym"',
        'id="scene-telemetry"',
        'id="domain-btn-banking"',
        'id="domain-btn-mortgage"',
        'id="domain-btn-insurance"',
        'id="bq-sql-tab-rule0"',
        'id="bq-sql-tab-judge"',
        'id="bq-sql-tab-rogan"',
        'id="source-proof-modal"',
    ):
        assert required_id in html_text, (
            f"demo/enterprise-demo.html missing required element {required_id}"
        )

    js_text = (REPO_ROOT / "demo/enterprise-demo.js").read_text(encoding="utf-8")
    for fn_name in (
        "window.switchDomain",
        "window.switchScene",
        "window.selectRegistryTab",
        "window.switchBqSqlQueryTab",
        "window.runBqSqlTraceEval",
        "window.openSourceProofModal",
        "window.runVerifyDay2Payoff",
    ):
        assert fn_name in js_text, (
            f"demo/enterprise-demo.js missing controller function {fn_name}"
        )

    # Every onclick="window.<fn>(...)" in enterprise-demo.html is defined in enterprise-demo.js
    onclick_fns = set(re.findall(r"window\.([a-zA-Z0-9_]+)\s*\(", html_text))
    assert len(onclick_fns) >= 8
    for fn in onclick_fns:
        assert f"window.{fn}" in js_text, (
            f"demo/enterprise-demo.html invokes undefined window.{fn}() in enterprise-demo.js"
        )

    # Every static document.getElementById("...") in enterprise-demo.js exists in HTML or JS templates
    combined_dom_source = html_text + "\n" + js_text
    dom_ids_queried = set(
        re.findall(r'document\.getElementById\(\s*"([a-zA-Z0-9_-]+)"\s*\)', js_text)
    )
    assert len(dom_ids_queried) >= 30
    for dom_id in dom_ids_queried:
        assert f'id="{dom_id}"' in combined_dom_source, (
            f"demo/enterprise-demo.js queries document.getElementById({dom_id!r}), "
            "but no matching id attribute exists in enterprise-demo.html or JS templates"
        )

    openapi_paths = list(app.openapi()["paths"].keys())
    route_regexes = [
        re.compile("^" + re.sub(r"\{[^/]+\}", r"[^/?]+", path) + "$")
        for path in openapi_paths
    ]

    # Match every `apiFetch("...")` or `apiFetch(\`...\`)` call in enterprise-demo.js
    api_calls = re.findall(r'apiFetch\(\s*[`"](/v1/[^`"]+)[`"]', js_text)
    assert len(api_calls) >= 10
    for raw_call in api_calls:
        # Strip JS string concatenation or template expressions (`${...}`) and query params
        interpolated = re.sub(r"\$\{[^}]+\}", "sample-id", raw_call)
        interpolated = re.sub(r'"\s*\+\s*[^"]+\+\s*"', "sample-id", interpolated)
        interpolated = re.sub(r'"\s*\+.*$', "sample-id", interpolated)
        path_only = interpolated.split("?", 1)[0].rstrip("/")
        assert any(r.match(path_only) for r in route_regexes), (
            f"demo/enterprise-demo.js calls unmounted API path {raw_call!r} "
            f"(normalized: {path_only!r})"
        )


def test_zero_stale_firestore_or_broken_claude_plan_references() -> None:
    """No ``Firestore`` strings in Python packages, no ``.claude/`` in docs, and ANN enforced."""
    for pkg in ("bridge", "contract", "agents"):
        for py_path in (REPO_ROOT / pkg).rglob("*.py"):
            content = py_path.read_text(encoding="utf-8")
            assert "Firestore" not in content, (
                f"{py_path.relative_to(REPO_ROOT)} contains stale 'Firestore' reference"
            )

    for md_path in REPO_ROOT.rglob("*.md"):
        if ".venv" in md_path.parts or "node_modules" in md_path.parts:
            continue
        text = md_path.read_text(encoding="utf-8")
        assert ".claude/" not in text, (
            f"{md_path.relative_to(REPO_ROOT)} contains broken '.claude/' reference"
        )

    pyproject = tomllib.loads(
        (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    ruff_select = set(pyproject["tool"]["ruff"]["lint"]["select"])
    for rule in ("ANN001", "ANN201", "ANN202"):
        assert rule in ruff_select, (
            f"pyproject.toml [tool.ruff.lint] select missing {rule!r}"
        )


def test_canonical_financial_figures_match_across_docs_and_seed() -> None:
    """``README.md``, ``BANKING.md``, and ``docs/demo-click-path.md`` match ``seed_demo_scenarios()``."""
    readme_text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    banking_text = (REPO_ROOT / "BANKING.md").read_text(encoding="utf-8")
    click_path_text = (REPO_ROOT / "docs/demo-click-path.md").read_text(
        encoding="utf-8"
    )

    canonical_figures = (
        "$45,000,000",
        "72.00% LTV",
        "$50,000.00",
        "$100,000.00",
        "$1,850,000",
        "77.08% LTV",
        "$4,250.00",
        "$8,500.00",
        "$142,500,000",
        "$418,500.00",
    )
    for fig in canonical_figures:
        assert fig in readme_text, f"Canonical figure {fig!r} missing from README.md"
        assert fig in banking_text, f"Canonical figure {fig!r} missing from BANKING.md"

    for click_fig in ("$50,000.00", "$100,000.00", "$45M", "$1.85M"):
        assert click_fig in click_path_text, (
            f"Canonical figure {click_fig!r} missing from docs/demo-click-path.md"
        )

    assert "cinco interactive scenes" not in readme_text


def test_fsi_skills_loaded_and_advertised_on_a2a_agent_card() -> None:
    """First-class FSI skills load in ``loaded_skills()`` and advertise on ``build_agent_card()``."""
    from bridge.edge.card import build_agent_card
    from bridge.edge.skills import loaded_skills

    skills = {s.name: s for s in loaded_skills()}
    assert "commercial-loan-underwriting" in skills
    assert skills["commercial-loan-underwriting"].kind == "process"
    assert "trid-closing-disclosure" in skills
    assert skills["trid-closing-disclosure"].kind == "doctype"

    card = build_agent_card("http://localhost:8080")
    card_skill_ids = {s.id for s in card.skills}
    assert "commercial-loan-underwriting" in card_skill_ids


def test_dynamic_process_skill_resolution_and_two_sided_oversight_controls() -> None:
    """Domain process skills resolve dynamically and Agent Gym v2 oversight controls gate promotion."""
    from bridge.a2ui_inbox import A2uiClientActionRequest, InMemoryActionInboxStore
    from bridge.agent_gym import InMemoryAgentGymEngine
    from bridge.operational_store import FactConflict, SourceTrace
    from bridge.user_interaction.surfaces import _resolve_process_skill_name

    assert (
        _resolve_process_skill_name(
            entity_id="MORTGAGE-WESTBROOK-2026", doctype="closing-disclosure"
        )
        == "trid-mortgage-closing"
    )
    assert (
        _resolve_process_skill_name(
            entity_id="terrepower-coastal-2027", doctype="quote"
        )
        == "commercial-property-placement"
    )
    assert (
        _resolve_process_skill_name(entity_id="LOAN-MERIDIAN-001", doctype="addendum")
        == "commercial-loan-closing"
    )

    inbox = InMemoryActionInboxStore()
    conflict = FactConflict(
        entity_id="LOAN-MERIDIAN-001",
        field_path="origination_fee",
        winning_fact_id="fact-addendum-1",
        winning_doc_id="doc-addendum",
        winning_value="100000.00",
        conflicting_fact_id="fact-term-1",
        conflicting_doc_id="doc-term",
        conflicting_value="50000.00",
        reason="Addendum overrides term sheet",
        winning_trace=SourceTrace(doc_id="doc-addendum", doctype="addendum"),
        conflicting_trace=SourceTrace(doc_id="doc-term", doctype="term-sheet"),
    )
    blind_item = inbox.create_action(
        entity_id="LOAN-MERIDIAN-001",
        title="Verify Fee Override",
        hint="Blind pre-commitment check",
        conflicts=[conflict],
        blind_pre_commitment=True,
    )
    assert blind_item.blind_pre_commitment is True
    assert blind_item.initial_data_model["resolution"]["selectedValue"] == ""

    resolved = inbox.resolve_action(
        blind_item.item_id,
        action_request=A2uiClientActionRequest(
            action={
                "name": "edit_and_approve",
                "context": {
                    "decision": "edit_and_approve",
                    "resolution": {"selectedValue": "100000.00"},
                    "review": {"memo": "Verified Schedule B Page 9."},
                    "review_dwell_ms": 4800,
                    "citation_opened": True,
                    "pre_committed": True,
                },
            }
        ),
        actor_principal="senior.credit.officer@docubridge.io",
        actor_role="underwriter",
        channel="gemini_app",
    )
    audit = inbox.list_audit_records(blind_item.item_id)[0]
    assert resolved["audit_id"] == audit.audit_id
    assert audit.review_dwell_ms == 4800
    assert audit.citation_opened is True
    assert audit.pre_committed is True

    gym = InMemoryAgentGymEngine()
    signal, rule = gym.distill_from_inbox_audit(
        audit,
        doc_id="doc-addendum",
        doctype="closing-disclosure",
        issuer="Meridian Commercial Bank",
    )
    assert signal.pre_committed is True
    assert signal.oversight_rsr_score >= 0.95
    assert rule.status.value == "candidate"

    # Rubber-stamped shadow comparison must NOT increment consecutive_matches
    slice_id = "slice:credit-agreement:addendum:MEDIUM"
    before_policy = gym.get_trust_policy(slice_id)
    assert before_policy is not None
    assert before_policy.spot_check_sample_rate == 0.05
    before_matches = before_policy.consecutive_matches

    rubber_rec, held_policy = gym.record_shadow_comparison(
        slice_id=slice_id,
        entity_id="LOAN-MERIDIAN-001",
        doc_id="doc-rubber",
        issuer="Syndicated",
        doctype="addendum",
        field_path="origination_fee",
        ai_value="100000.00",
        human_value="100000.00",
        citation_verified=False,
        review_dwell_ms=250,
    )
    assert rubber_rec.exact_match is True
    assert rubber_rec.oversight_rsr_score < 0.50
    assert held_policy.consecutive_matches == before_matches

    # Verified shadow comparison advances consecutive_matches
    verified_rec, promoted_policy = gym.record_shadow_comparison(
        slice_id=slice_id,
        entity_id="LOAN-MERIDIAN-001",
        doc_id="doc-verified",
        issuer="Syndicated",
        doctype="addendum",
        field_path="origination_fee",
        ai_value="100000.00",
        human_value="100000.00",
        citation_verified=True,
        review_dwell_ms=4200,
    )
    assert verified_rec.oversight_rsr_score >= 0.95
    assert promoted_policy.consecutive_matches == before_matches + 1
