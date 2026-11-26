"""Executable /human specification contracts and live codebase parity checks."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

_FORBIDDEN_SVG_RE = re.compile(
    r"(?:<(?:script|foreignobject|iframe|object|embed|lineargradient|radialgradient|"
    r"marker|style)\b|\bon[a-z]+\s*=|javascript:|data:text/html)",
    re.IGNORECASE,
)


class AtlasPillarNode(TypedDict, total=False):
    """Typed L2/L3 pillar tree node compatible with Customer Atlas payloads."""

    id: str
    title: str
    category: str
    badge: str
    summary: str
    note_path: str
    children: list[AtlasPillarNode]


@dataclass(frozen=True)
class PitchLayerSpec:
    """High-level summary layer rendered in the blueprint Overview tab."""

    label: str
    text: str


@dataclass(frozen=True)
class PrincipleSpec:
    """Load-bearing architectural boundary rendered on the blueprint Overview tab."""

    title: str
    body: str


@dataclass(frozen=True)
class KpiSpec:
    """Measured KPI card displayed on the blueprint Overview tab."""

    label: str
    value: str
    caption: str


@dataclass(frozen=True)
class ActorStageSpec:
    """Top-level actor stage card matching the interactive floorplan zones."""

    stage: str
    role: str
    contents: str
    boundary: str


@dataclass(frozen=True)
class ThemeDiffSpec:
    """Before-and-after architectural evolution row grounded in repo history."""

    theme: str
    before: str
    after: str


@dataclass(frozen=True)
class ActorZoneSpec:
    """C4 boundary box grouping subsystems on the interactive floorplan."""

    zone_id: str
    row: int
    x_pos: int
    width: int
    inner_cols: int
    plain_title: str
    plain_sub: str
    spec_title: str
    spec_sub: str
    summary: str
    node_ids: tuple[str, ...]


@dataclass(frozen=True)
class MacroEdgeSpec:
    """Directed flow arrow connecting two C4 actor zones."""

    edge_id: str
    source_zone: str
    target_zone: str
    slot: str
    label_plain: str
    label_spec: str


@dataclass(frozen=True)
class NodeEdgeSpec:
    """Directed dependency arrow between two subsystems inside the floorplan."""

    edge_id: str
    source_node: str
    target_node: str


@dataclass(frozen=True)
class SubsystemSpec:
    """Executable component spec pinned to live repo files and AST symbols."""

    node_id: str
    height: int
    span: int
    status: str
    recent: bool
    has_gap: bool
    plain_title: str
    plain_sub: str
    spec_title: str
    spec_sub: str
    adrs: tuple[str, ...]
    desc_plain: str
    desc_spec: str
    ports_contract: str
    gap_note: str | None
    code_files: tuple[str, ...]
    code_symbols: tuple[tuple[str, str], ...] = ()
    pillar_category: str = "arch"


@dataclass(frozen=True)
class PortContractSpec:
    """Formal Ports, Adapters and Contracts entry pinned to repo files."""

    port_id: str
    title: str
    kind: str
    contract_paths: tuple[str, ...]
    adapter_paths: tuple[str, ...]
    test_paths: tuple[str, ...]
    summary: str


@dataclass(frozen=True)
class DecisionSpec:
    """Architectural Decision Record entry synced with DECISIONS.md headings."""

    adr_id: str
    status: str
    evidence: str
    title: str
    delta: str
    nodes: tuple[str, ...]
    journeys: tuple[str, ...]


@dataclass(frozen=True)
class JourneyStepSpec:
    """Single hop inside an end-to-end Critical User Journey walkthrough."""

    node_id: str
    edge: str | None
    plain_action: str
    spec_action: str
    delta_note: str
    adrs: tuple[str, ...]
    gap: str | None = None


@dataclass(frozen=True)
class JourneySpec:
    """Complete end-to-end capability walkthrough across zones and ports."""

    journey_id: str
    fit_arch: str
    fit_secondary: str
    tier: str
    plain_name: str
    spec_name: str
    tagline: str
    works_from_core: str
    external_build: str
    blocking_gap: str
    matrix: dict[str, str]
    steps: tuple[JourneyStepSpec, ...]


@dataclass(frozen=True)
class ProbeSpec:
    """Verified check or open execution gap linked directly to codebase targets."""

    status: str
    title: str
    detail: str
    target_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class FindingSpec:
    """Audit or test finding anchored to a C4 node and journey."""

    finding_id: str
    severity: str
    title: str
    body: str
    target_node: str
    target_journey: str


@dataclass(frozen=True)
class OpenCheckSpec:
    """Open remediation check optionally linked to a docs/roadmap.md marker."""

    check_id: int
    status: str
    check: str
    scope: str
    gap: str
    roadmap_ref: str | None = None


@dataclass(frozen=True)
class AtlasSummarySpec:
    """Metadata projecting the repo into portfolio summary and Architecture Hub views."""

    repo_id: str
    name: str
    short_name: str
    segment: str
    color: str
    helicopter_bluf: str
    headline_metrics: tuple[dict[str, str], ...]
    guardrails: tuple[str, ...]
    notes_files: tuple[tuple[str, str], ...]
    pillars: tuple[AtlasPillarNode, ...]
    atlas_edges: tuple[dict[str, str], ...]
    cockpit_group: str
    cockpit_sub_group: str
    cockpit_role: str
    cockpit_grade: str
    cockpit_path: str
    cockpit_keywords: str


@dataclass(frozen=True)
class HumanBlueprint:
    """Single executable specification for verification, blueprints, and Hub."""

    slug: str
    title: str
    subtitle: str
    repo_badge: str
    grade_badge: str
    recent_toggle_label: str
    version_label: str
    secondary_fit_label: str
    hero_svg_path: str
    pitch_headline: str
    pitch_problem: str
    pitch_layers: tuple[PitchLayerSpec, ...]
    sample_journey_id: str
    sample_button_label: str
    principles_heading: str
    principles_sub: str
    principles: tuple[PrincipleSpec, ...]
    kpis: tuple[KpiSpec, ...]
    stages_heading: str
    stages_sub: str
    stages: tuple[ActorStageSpec, ...]
    diff_heading: str
    diff_sub: str
    before_label: str
    after_label: str
    theme_diffs: tuple[ThemeDiffSpec, ...]
    journeys_heading: str
    journeys_sub: str
    adr_ledger_label: str
    matrix_label: str
    review_label: str
    probes_heading: str
    findings_heading: str
    open_checks_heading: str
    default_node_id: str
    matrix_columns: tuple[str, ...]
    zones: tuple[ActorZoneSpec, ...]
    macro_edges: tuple[MacroEdgeSpec, ...]
    nodes: tuple[SubsystemSpec, ...]
    edges: tuple[NodeEdgeSpec, ...]
    ports: tuple[PortContractSpec, ...]
    adrs: tuple[DecisionSpec, ...]
    journeys: tuple[JourneySpec, ...]
    probes: tuple[ProbeSpec, ...]
    findings: tuple[FindingSpec, ...]
    open_checks: tuple[OpenCheckSpec, ...]
    atlas: AtlasSummarySpec
    decisions_doc: str = "DECISIONS.md"
    roadmap_doc: str = "docs/roadmap.md"


@dataclass(frozen=True)
class _AnchorCheckState:
    repo_root: Path
    symbol_cache: dict[str, set[str]]
    errors: list[str]


def _extract_python_symbols(file_path: Path) -> set[str]:
    """Return top-level and class member symbol names parsed via stdlib AST."""
    source_text = file_path.read_text(encoding="utf-8")
    tree = ast.parse(source_text, filename=str(file_path))
    symbols: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            symbols.add(node.name)
            if isinstance(node, ast.ClassDef):
                for member in node.body:
                    if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        symbols.add(f"{node.name}.{member.name}")
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    symbols.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            symbols.add(node.target.id)
    return symbols


def _verify_hero_svg(spec: HumanBlueprint, repo_root: Path, errors: list[str]) -> None:
    """Verify hero SVG exists, uses safe root tags, and stays concise (<45 words)."""
    svg_path = repo_root / spec.hero_svg_path
    if not svg_path.is_file():
        errors.append(f"Missing hero SVG: {spec.hero_svg_path}")
        return
    svg_text = svg_path.read_text(encoding="utf-8").strip()
    if not svg_text.startswith("<svg") or not svg_text.endswith("</svg>"):
        errors.append(f"Invalid SVG root wrapper in {spec.hero_svg_path}")
        return
    if svg_text.count("<svg") != 1 or svg_text.count("</svg>") != 1:
        errors.append(f"Expected single root SVG element in {spec.hero_svg_path}")
    if 'viewBox="0 0 1600 900"' not in svg_text:
        errors.append(f"Hero SVG must declare 1600x900 viewBox: {spec.hero_svg_path}")
    if _FORBIDDEN_SVG_RE.search(svg_text):
        errors.append(f"Forbidden active/gradient tag in SVG: {spec.hero_svg_path}")
    plain_words = re.sub(r"<[^>]+>", " ", svg_text).split()
    if len(plain_words) > 45:
        errors.append(
            f"Hero SVG exceeds 45 words ({len(plain_words)}): {spec.hero_svg_path}"
        )


def _verify_symbol_anchor(
    state: _AnchorCheckState, node_id: str, rel_file: str, sym_name: str
) -> None:
    """Check one AST symbol or anchor string against its repository file."""
    full_path = state.repo_root / rel_file
    if not full_path.is_file():
        state.errors.append(f"Node {node_id} missing symbol file: {rel_file}")
        return
    if full_path.suffix == ".py":
        if rel_file not in state.symbol_cache:
            state.symbol_cache[rel_file] = _extract_python_symbols(full_path)
        if sym_name not in state.symbol_cache[rel_file]:
            state.errors.append(
                f"Node {node_id} missing symbol {sym_name!r} in {rel_file}"
            )
    elif sym_name not in full_path.read_text(encoding="utf-8"):
        state.errors.append(f"Node {node_id} missing anchor {sym_name!r} in {rel_file}")


def _verify_code_anchors(
    spec: HumanBlueprint, repo_root: Path, errors: list[str]
) -> None:
    """Verify every declared subsystem file, symbol, and port path is a file."""
    state = _AnchorCheckState(repo_root=repo_root, symbol_cache={}, errors=errors)
    for subsystem in spec.nodes:
        for rel_path in subsystem.code_files:
            if not (repo_root / rel_path).is_file():
                errors.append(f"Node {subsystem.node_id} missing code_file: {rel_path}")
        for rel_file, sym_name in subsystem.code_symbols:
            _verify_symbol_anchor(state, subsystem.node_id, rel_file, sym_name)

    for port in spec.ports:
        port_groups = (
            ("contract", port.contract_paths),
            ("adapter", port.adapter_paths),
            ("test", port.test_paths),
        )
        for group_name, rel_group in port_groups:
            for rel_path in rel_group:
                if not (repo_root / rel_path).is_file():
                    errors.append(
                        f"Port {port.port_id} missing {group_name} path: {rel_path}"
                    )


def _collect_pillar_ids(
    pillars: tuple[AtlasPillarNode, ...],
    prefix: str,
    repo_root: Path,
    errors: list[str],
) -> set[str]:
    """Recursively verify pillar note paths and collect unique pillar IDs."""
    pillar_ids: set[str] = set()
    for pillar in pillars:
        pid = str(pillar.get("id") or "")
        if pid in pillar_ids:
            errors.append(f"Duplicate Atlas pillar id: {pid}")
        pillar_ids.add(pid)
        note_path = str(pillar.get("note_path") or "")
        if note_path.startswith(prefix):
            rel_note = note_path[len(prefix) :]
            if not (repo_root / rel_note).is_file():
                errors.append(f"Pillar {pid} missing note_path: {rel_note}")
        elif note_path:
            errors.append(f"Pillar {pid} note_path must start with {prefix!r}")
        children = pillar.get("children") or []
        if children:
            pillar_ids.update(
                _collect_pillar_ids(tuple(children), prefix, repo_root, errors)
            )
    return pillar_ids


def _verify_markdown_and_probes(
    spec: HumanBlueprint, repo_root: Path, errors: list[str]
) -> None:
    """Verify DECISIONS.md ADR headings, roadmap refs, and probe file paths."""
    decisions_path = repo_root / spec.decisions_doc
    if not decisions_path.is_file():
        errors.append(f"Missing decisions document: {spec.decisions_doc}")
    else:
        decisions_text = decisions_path.read_text(encoding="utf-8")
        for adr in spec.adrs:
            if not re.search(rf"(?m)^##\s+{re.escape(adr.adr_id)}\b", decisions_text):
                errors.append(
                    f"ADR {adr.adr_id} missing heading in {spec.decisions_doc}"
                )

    roadmap_path = repo_root / spec.roadmap_doc
    roadmap_text = (
        roadmap_path.read_text(encoding="utf-8") if roadmap_path.is_file() else ""
    )
    for check in spec.open_checks:
        if check.roadmap_ref and check.roadmap_ref not in roadmap_text:
            errors.append(
                f"Check {check.check_id} ref {check.roadmap_ref!r} missing in "
                f"{spec.roadmap_doc}"
            )
    for probe in spec.probes:
        for rel_path in probe.target_paths:
            if not (repo_root / rel_path).is_file():
                errors.append(f"Probe {probe.title!r} missing target_path: {rel_path}")


def _verify_docs_and_links(
    spec: HumanBlueprint, repo_root: Path, errors: list[str]
) -> None:
    """Verify documentation files, probes, and Atlas metadata invariants."""
    _verify_markdown_and_probes(spec, repo_root, errors)
    for _title, note_rel in spec.atlas.notes_files:
        if not (repo_root / note_rel).is_file():
            errors.append(f"Atlas notes_file missing on disk: {note_rel}")
    if spec.atlas.repo_id != spec.slug:
        errors.append(f"Atlas repo_id {spec.atlas.repo_id!r} != slug {spec.slug!r}")
    cpath = spec.atlas.cockpit_path
    if not (cpath.startswith("/") and cpath.endswith("/")):
        errors.append(f"Atlas cockpit_path must start/end with '/': {cpath!r}")
    pillar_ids = _collect_pillar_ids(
        spec.atlas.pillars, f"projects/{spec.slug}/", repo_root, errors
    )
    for atlas_edge in spec.atlas.atlas_edges:
        src_id = atlas_edge.get("from", "")
        dst_id = atlas_edge.get("to", "")
        if src_id not in pillar_ids or dst_id not in pillar_ids:
            errors.append(f"Atlas edge references unknown pillar: {src_id} -> {dst_id}")


def _collect_unique_ids(kind: str, raw_ids: list[str], errors: list[str]) -> set[str]:
    seen: set[str] = set()
    for item_id in raw_ids:
        if item_id in seen:
            errors.append(f"Duplicate {kind} id: {item_id}")
        seen.add(item_id)
    return seen


def _verify_zone_and_edge_links(
    spec: HumanBlueprint,
    node_ids: set[str],
    zone_ids: set[str],
    errors: list[str],
) -> None:
    """Verify exclusive zone node coverage and zone/node edge endpoints."""
    seen_in_zones: set[str] = set()
    for zone in spec.zones:
        for node_id in zone.node_ids:
            if node_id not in node_ids:
                errors.append(f"Zone {zone.zone_id} references unknown node: {node_id}")
            elif node_id in seen_in_zones:
                errors.append(f"Subsystem assigned to multiple zones: {node_id}")
            seen_in_zones.add(node_id)
    unassigned = sorted(node_ids - seen_in_zones)
    if unassigned:
        errors.append(f"Subsystems not assigned to any zone: {unassigned}")
    for macro_edge in spec.macro_edges:
        if (
            macro_edge.source_zone not in zone_ids
            or macro_edge.target_zone not in zone_ids
        ):
            errors.append(f"MacroEdge {macro_edge.edge_id} references unknown zone")
    for edge in spec.edges:
        if edge.source_node not in node_ids or edge.target_node not in node_ids:
            errors.append(f"Edge {edge.edge_id} references unknown node")


def _verify_adr_links(
    spec: HumanBlueprint,
    node_ids: set[str],
    adr_ids: set[str],
    journey_ids: set[str],
    errors: list[str],
) -> None:
    """Verify node-to-ADR and ADR-to-node/journey cross-references."""
    for node in spec.nodes:
        for adr_id in node.adrs:
            if adr_id not in adr_ids:
                errors.append(f"Node {node.node_id} references unknown ADR {adr_id}")
    for adr in spec.adrs:
        for node_id in adr.nodes:
            if node_id not in node_ids:
                errors.append(f"ADR {adr.adr_id} references unknown node {node_id}")
        for journey_id in adr.journeys:
            if journey_id not in journey_ids:
                errors.append(
                    f"ADR {adr.adr_id} references unknown journey {journey_id}"
                )


def _verify_journey_and_finding_links(
    spec: HumanBlueprint,
    node_ids: set[str],
    adr_ids: set[str],
    valid_edges: set[str],
    errors: list[str],
) -> None:
    """Verify journey steps and findings resolve to declared nodes, edges, and ADRs."""
    journey_ids = {jrn.journey_id for jrn in spec.journeys}
    for journey in spec.journeys:
        for step in journey.steps:
            if step.node_id not in node_ids:
                errors.append(
                    f"Journey {journey.journey_id} unknown node {step.node_id}"
                )
            if step.edge and step.edge not in valid_edges:
                errors.append(f"Journey {journey.journey_id} unknown edge {step.edge}")
            for adr_id in step.adrs:
                if adr_id not in adr_ids:
                    errors.append(
                        f"Journey {journey.journey_id} step has unknown ADR {adr_id}"
                    )
    for finding in spec.findings:
        if (
            finding.target_node not in node_ids
            or finding.target_journey not in journey_ids
        ):
            errors.append(f"Finding {finding.finding_id} references unknown target")


def _verify_graph_integrity(spec: HumanBlueprint, errors: list[str]) -> None:
    """Verify uniqueness and foreign keys across zones, nodes, ADRs, and journeys."""
    node_ids = _collect_unique_ids(
        "node", [node.node_id for node in spec.nodes], errors
    )
    zone_ids = _collect_unique_ids(
        "zone", [zone.zone_id for zone in spec.zones], errors
    )
    adr_ids = _collect_unique_ids("ADR", [adr.adr_id for adr in spec.adrs], errors)
    journey_ids = _collect_unique_ids(
        "journey", [jrn.journey_id for jrn in spec.journeys], errors
    )
    edge_ids = _collect_unique_ids(
        "edge", [edge.edge_id for edge in spec.edges], errors
    )
    macro_ids = _collect_unique_ids(
        "macro_edge", [edge.edge_id for edge in spec.macro_edges], errors
    )
    _collect_unique_ids("port", [port.port_id for port in spec.ports], errors)
    _collect_unique_ids(
        "open_check", [str(chk.check_id) for chk in spec.open_checks], errors
    )

    if spec.default_node_id not in node_ids:
        errors.append(f"Unknown default_node_id: {spec.default_node_id}")
    if spec.sample_journey_id not in journey_ids:
        errors.append(f"Unknown sample_journey_id: {spec.sample_journey_id}")

    _verify_zone_and_edge_links(spec, node_ids, zone_ids, errors)
    _verify_adr_links(spec, node_ids, adr_ids, journey_ids, errors)
    _verify_journey_and_finding_links(
        spec, node_ids, adr_ids, edge_ids | macro_ids, errors
    )


def verify_contract(spec: HumanBlueprint, repo_root: Path) -> list[str]:
    """Run all executable filesystem, AST symbol, doc, SVG, and graph checks."""
    errors: list[str] = []
    _verify_hero_svg(spec, repo_root, errors)
    _verify_code_anchors(spec, repo_root, errors)
    _verify_docs_and_links(spec, repo_root, errors)
    _verify_graph_integrity(spec, errors)
    return errors
