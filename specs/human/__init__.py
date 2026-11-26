"""Public executable /human specification entrypoint for docubridge."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from specs.human.contract import HumanBlueprint, verify_contract
from specs.human.decisions import ADRS, PORTS
from specs.human.journeys_core import CORE_JOURNEYS
from specs.human.journeys_enterprise import (
    ENTERPRISE_JOURNEYS,
    MATRIX_COLUMNS,
)
from specs.human.overview import (
    KPIS,
    PITCH_LAYERS,
    PRINCIPLES,
    STAGES,
    THEME_DIFFS,
)
from specs.human.render import (
    to_atlas_dict,
    to_blueprint_dict,
    to_cockpit_entry,
)
from specs.human.reviews import (
    ATLAS_SUMMARY,
    FINDINGS,
    OPEN_CHECKS,
    PROBES,
)
from specs.human.topology import (
    EDGES,
    MACRO_EDGES,
    NODES,
    ZONES,
)

BLUEPRINT = HumanBlueprint(
    slug="docubridge",
    title="DocuBridge — Architecture Blueprint & Use-Case Journeys",
    subtitle=(
        "Plain-English Overview · 5-Actor Zoomable Floorplan · 9 Workload "
        "Journeys (CUJ-1..15 · ADR-01..ADR-16)"
    ),
    repo_badge="docubridge",
    grade_badge="Audits: 5.0-6.2/10 (P13/P14 fixed · P15-16 open)",
    recent_toggle_label="ADR-12-16 & P13/P14 Fixes",
    version_label="ADR-16",
    secondary_fit_label="Core Fit",
    hero_svg_path="docs/assets/architecture_card.svg",
    pitch_headline=(
        "Stop letting prompts reconcile documents: one code-only mediation "
        "service behind A2A."
    ),
    pitch_problem=(
        "Multi-document workflows break when LLMs guess which late addendum wins, "
        "drift on floating-point dollars, or approve blurry scans. DocuBridge "
        "absorbs the slow, messy chase-and-verify loop behind one A2A boundary so "
        "calling agents keep business policy and never touch raw paperwork."
    ),
    pitch_layers=PITCH_LAYERS,
    sample_journey_id="J6",
    sample_button_label="Walk Through Commercial Loan Consolidation (J6) ->",
    principles_heading="Three Load-Bearing Boundaries",
    principles_sub=(
        "Every decision keeps extraction, deterministic facts, and caller "
        "business policy cleanly separated."
    ),
    principles=PRINCIPLES,
    kpis=KPIS,
    stages_heading="The 5 Top-Level Actors & Loops (Click Any Box on Tab 2 to Unpack)",
    stages_sub=(
        "External Parties <-> DocuBridge Agent <-> Use-Case Agents on top, with "
        "Human Review & Day-2 Learning below."
    ),
    stages=STAGES,
    diff_heading="Evolution Snapshot: Initial Showcase -> ADR-16 & P13/P14 Hardening",
    diff_sub=(
        "Structural upgrades separating early prototypes from the hardened "
        "M14-P14 architecture on main."
    ),
    before_label="Earlier Prototype",
    after_label="Current Main (ADR-16 / P14)",
    theme_diffs=THEME_DIFFS,
    journeys_heading="All 9 Architecture Journeys — Covering CUJ-1..15 & 3 FSI Domains",
    journeys_sub=(
        "Click any card to watch that workload traverse the 5-actor map and "
        "unpack only the subsystems involved."
    ),
    adr_ledger_label="16 ADR Ledger (ADR-01-ADR-16)",
    matrix_label="J1-J9 x Capability Matrix",
    review_label="2026-11 Reviews, P13-P16 & Open Gaps",
    probes_heading="Executed Local Verification vs. Synthetic & Runtime Gaps",
    findings_heading="2026-11 Audits, Fixed Hardening & Open P15-P16 Findings",
    open_checks_heading="Open Remediation Items (P15 / P16) & Backlog",
    default_node_id="db_intake_gate",
    matrix_columns=MATRIX_COLUMNS,
    zones=ZONES,
    macro_edges=MACRO_EDGES,
    nodes=NODES,
    edges=EDGES,
    ports=PORTS,
    adrs=ADRS,
    journeys=CORE_JOURNEYS + ENTERPRISE_JOURNEYS,
    probes=PROBES,
    findings=FINDINGS,
    open_checks=OPEN_CHECKS,
    atlas=ATLAS_SUMMARY,
)


def verify_human_contract(repo_root: Path) -> list[str]:
    """Verify all files, symbols, ports, ADRs, roadmap refs, and graph links."""
    return verify_contract(BLUEPRINT, repo_root)


def build_blueprint_dict(repo_root: Path) -> dict[str, Any]:
    """Compile the self-contained arch-floorplan JSON directly from specs/human/."""
    return to_blueprint_dict(BLUEPRINT, repo_root)


def build_atlas_project() -> dict[str, Any]:
    """Compile L0-L3 Customer Atlas metadata directly from specs/human/."""
    return to_atlas_dict(BLUEPRINT)


def build_cockpit_entry() -> dict[str, Any]:
    """Compile the Home Cockpit Zone 4 card entry directly from specs/human/."""
    return to_cockpit_entry(BLUEPRINT)
