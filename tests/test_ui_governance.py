"""Tier-2 ui-design governance, offline fonts, favicons, MSVG SVG & static route tests (P12-1)."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from bridge.a2ui_inbox import InMemoryActionInboxStore
from bridge.agent_gym import InMemoryAgentGymEngine
from bridge.knowledge_catalog import InMemoryKnowledgeCatalog
from bridge.operational_store import InMemoryOperationalStore
from bridge.user_interaction import build_user_interaction_app

REPO_ROOT = Path(__file__).resolve().parents[1]
VITE_APPS = ("agent-console", "ops-dashboard", "provider-portal", "timewarp")
REQUIRED_GM3_TOKENS = (
    "--surface:",
    "--surface-container:",
    "--on-surface:",
    "--on-surface-variant:",
    "--outline-variant:",
    "--primary:",
)


def test_stitch_design_contract_present_and_complete() -> None:
    """`.stitch/DESIGN.md` locks Tier-2 GM3 Light tokens, MUI non-rewrite note, and Do Not Generate."""
    design_path = REPO_ROOT / ".stitch" / "DESIGN.md"
    assert design_path.is_file()
    text = design_path.read_text(encoding="utf-8")
    assert "GM3" in text
    assert "#f8f9fa" in text
    assert "#1a73e8" in text
    assert "MUI v6" in text
    assert "/ebc" in text
    assert "## Do Not Generate" in text
    for token in (
        "--surface",
        "--surface-container",
        "--on-surface",
        "--on-surface-variant",
        "--outline-variant",
        "--primary",
    ):
        assert token in text, f".stitch/DESIGN.md missing semantic token {token}"


def test_zero_external_font_cdn_references_and_offline_fonts_bundled() -> None:
    """No file in `demo/` or `frontend/` loads Google Fonts CDN; offline font bundle exists."""
    scan_suffixes = {".html", ".css", ".js", ".mjs", ".ts", ".tsx"}
    skip_dirs = {"node_modules", "dist", ".vite", "coverage"}
    offenders: list[str] = []
    for root_name in ("demo", "frontend"):
        base = REPO_ROOT / root_name
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.suffix not in scan_suffixes:
                continue
            if any(part in skip_dirs for part in path.parts):
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for forbidden in ("fonts.googleapis.com", "fonts.gstatic.com"):
                if forbidden in text:
                    offenders.append(f"{path.relative_to(REPO_ROOT)}: {forbidden}")
    assert not offenders, f"External font CDN references found: {offenders}"

    ebc_html = (REPO_ROOT / "demo" / "ebc-live.html").read_text(encoding="utf-8")
    assert "/demo/fonts/fonts.css" in ebc_html

    fonts_dir = REPO_ROOT / "demo" / "fonts"
    fonts_css = fonts_dir / "fonts.css"
    assert fonts_css.is_file(), "demo/fonts/fonts.css missing"
    css_text = fonts_css.read_text(encoding="utf-8")
    assert "Inter Variable" in css_text
    assert "JetBrains Mono Variable" in css_text
    woff2_files = list(fonts_dir.glob("*.woff2"))
    assert len(woff2_files) >= 2, "Expected bundled Inter & JetBrains Mono .woff2 files"


def test_all_four_vite_apps_ship_distinct_svg_favicons() -> None:
    """All 4 Vite `frontend/*/index.html` apps declare `/favicon.svg` backed by `public/favicon.svg`."""
    seen_svgs: set[str] = set()
    for app_name in VITE_APPS:
        app_dir = REPO_ROOT / "frontend" / app_name
        index_html = (app_dir / "index.html").read_text(encoding="utf-8")
        assert '<link rel="icon"' in index_html
        assert 'href="/favicon.svg"' in index_html

        favicon = app_dir / "public" / "favicon.svg"
        assert favicon.is_file(), f"Missing {favicon.relative_to(REPO_ROOT)}"
        svg_text = favicon.read_text(encoding="utf-8")
        assert "<svg" in svg_text
        seen_svgs.add(svg_text.strip())
    assert len(seen_svgs) == len(VITE_APPS), (
        "Each Vite zone app must have a distinct favicon"
    )


def test_gm3_tokens_tabular_nums_and_reduced_motion_present() -> None:
    """Standalone demo CSS and `@docubridge/shared` theme include GM3 tokens, tabular-nums, and reduced-motion."""
    targets = (
        REPO_ROOT / "demo" / "enterprise-demo.css",
        REPO_ROOT / "demo" / "ebc-live.css",
        REPO_ROOT / "frontend" / "shared" / "src" / "theme.ts",
    )
    for path in targets:
        text = path.read_text(encoding="utf-8")
        for token in REQUIRED_GM3_TOKENS:
            assert token in text, f"{path.relative_to(REPO_ROOT)} missing {token}"
        assert "tabular-nums" in text, (
            f"{path.relative_to(REPO_ROOT)} missing tabular-nums"
        )
        assert "prefers-reduced-motion: reduce" in text, (
            f"{path.relative_to(REPO_ROOT)} missing prefers-reduced-motion"
        )


def test_architecture_card_svg_is_msvg_safe() -> None:
    """`docs/assets/architecture_card.svg` uses inline `<text>` attributes with zero `<style>`/`<linearGradient>`/`<marker>`."""
    svg_path = REPO_ROOT / "docs" / "assets" / "architecture_card.svg"
    raw = svg_path.read_text(encoding="utf-8")
    root = ET.fromstring(raw)  # noqa: S314 - parsing static repository SVG asset
    assert root.attrib.get("viewBox") == "0 0 1600 940"

    def _local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    tags = [_local(el.tag) for el in root.iter()]
    assert "style" not in tags, (
        "MSVG ignores <style> classes; inline attributes on <text>"
    )
    assert "linearGradient" not in tags
    assert "marker" not in tags

    texts = [el for el in root.iter() if _local(el.tag) == "text"]
    assert len(texts) >= 20
    for node in texts:
        assert "class" not in node.attrib, f"SVG <text> still uses class: {node.text!r}"
        for attr in ("font-family", "font-size", "font-weight", "fill"):
            assert node.attrib.get(attr), (
                f"SVG <text> {node.text!r} missing inline attribute {attr!r}"
            )


def test_cloudbuild_executes_playwright_tests() -> None:
    """`cloudbuild.yaml` runs Playwright tests directly (never `--list`) in a Playwright container."""
    cb = yaml.safe_load((REPO_ROOT / "cloudbuild.yaml").read_text(encoding="utf-8"))
    steps = {s["id"]: s for s in cb["steps"]}
    fe = steps["frontend-tests-and-typecheck"]
    assert fe["name"].startswith("mcr.microsoft.com/playwright:")
    args_text = " ".join(fe["args"])
    assert "playwright test --list" not in args_text
    assert "playwright test" in args_text


def test_demo_fonts_and_modules_static_routes_with_path_traversal_defense(
    tmp_path: Path, monkeypatch
) -> None:
    """`/demo/fonts/*` and `/demo/modules/*` serve valid assets and block traversal / disallowed types."""
    op_store = InMemoryOperationalStore()
    app = build_user_interaction_app(
        operational_store=op_store,
        knowledge_catalog=InMemoryKnowledgeCatalog(operational_store=op_store),
        inbox_store=InMemoryActionInboxStore(),
        agent_gym=InMemoryAgentGymEngine(),
    )
    client = TestClient(app)

    fonts_css = client.get("/demo/fonts/fonts.css")
    assert fonts_css.status_code == 200
    assert "text/css" in fonts_css.headers["content-type"]

    woff2_files = list((REPO_ROOT / "demo" / "fonts").glob("*.woff2"))
    assert woff2_files
    font_bin = client.get(f"/demo/fonts/{woff2_files[0].name}")
    assert font_bin.status_code == 200
    assert font_bin.headers["content-type"] == "font/woff2"
    assert "immutable" in font_bin.headers.get("cache-control", "")

    for bad_path in (
        "/demo/fonts/%2e%2e/enterprise-demo.html",
        "/demo/fonts/..%5Centerprise-demo.html",
        "/demo/fonts/nonexistent.html",
        "/demo/modules/%2e%2e/ebc-live.js",
        "/demo/modules/..%5Centerprise-demo.js",
        "/demo/modules/nonexistent.css",
    ):
        resp = client.get(bad_path)
        assert resp.status_code in (400, 404), (
            f"{bad_path} unexpectedly returned {resp.status_code}"
        )

    demo_dir = tmp_path / "demo"
    (demo_dir / "modules").mkdir(parents=True)
    (demo_dir / "modules" / "probe.js").write_text(
        "export const ok = true;\n", encoding="utf-8"
    )
    (demo_dir / "secret.js").write_text("export const secret = 1;\n", encoding="utf-8")
    (demo_dir / "modules" / "escape.js").symlink_to(demo_dir / "secret.js")
    monkeypatch.setattr(
        "bridge.user_interaction.intake_routes.demo_static_root", lambda: demo_dir
    )

    ok_mod = client.get("/demo/modules/probe.js")
    assert ok_mod.status_code == 200
    assert "javascript" in ok_mod.headers["content-type"]
    assert "export const ok = true;" in ok_mod.text

    escaped = client.get("/demo/modules/escape.js")
    assert escaped.status_code == 400
