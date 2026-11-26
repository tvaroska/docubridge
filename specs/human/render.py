"""Serialization projections from HumanBlueprint to arch-floorplan, Atlas, and Cockpit."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from specs.human.contract import HumanBlueprint, JourneySpec


def _serialize_overview(spec: HumanBlueprint, hero_svg: str) -> dict[str, Any]:
    """Serialize Overview tab structures for arch-floorplan compilation."""
    return {
        "pitchHeadline": spec.pitch_headline,
        "pitchProblem": spec.pitch_problem,
        "pitchLayers": [
            {"label": layer.label, "text": layer.text} for layer in spec.pitch_layers
        ],
        "sampleJourneyId": spec.sample_journey_id,
        "sampleButtonLabel": spec.sample_button_label,
        "heroSvg": hero_svg,
        "principlesHeading": spec.principles_heading,
        "principlesSub": spec.principles_sub,
        "principles": [
            {"title": item.title, "body": item.body} for item in spec.principles
        ],
        "kpis": [
            {"label": item.label, "value": item.value, "caption": item.caption}
            for item in spec.kpis
        ],
        "stagesHeading": spec.stages_heading,
        "stagesSub": spec.stages_sub,
        "stages": [
            {
                "stage": item.stage,
                "role": item.role,
                "contents": item.contents,
                "boundary": item.boundary,
            }
            for item in spec.stages
        ],
        "diffHeading": spec.diff_heading,
        "diffSub": spec.diff_sub,
        "beforeLabel": spec.before_label,
        "afterLabel": spec.after_label,
        "themeDiffs": [
            {"theme": item.theme, "before": item.before, "after": item.after}
            for item in spec.theme_diffs
        ],
        "journeysHeading": spec.journeys_heading,
        "journeysSub": spec.journeys_sub,
    }


def _serialize_journey(journey: JourneySpec) -> dict[str, Any]:
    """Serialize one Critical User Journey walkthrough for arch-floorplan."""
    return {
        "id": journey.journey_id,
        "fitArch": journey.fit_arch,
        "fitSecondary": journey.fit_secondary,
        "tier": journey.tier,
        "plainName": journey.plain_name,
        "specName": journey.spec_name,
        "tagline": journey.tagline,
        "worksFromCore": journey.works_from_core,
        "externalBuild": journey.external_build,
        "blockingGap": journey.blocking_gap,
        "matrix": dict(journey.matrix),
        "steps": [
            {
                "node": step.node_id,
                "edge": step.edge,
                "plainAction": step.plain_action,
                "specAction": step.spec_action,
                "deltaNote": step.delta_note,
                "adrs": list(step.adrs),
                "gap": step.gap,
            }
            for step in journey.steps
        ],
    }


def to_blueprint_dict(spec: HumanBlueprint, repo_root: Path) -> dict[str, Any]:
    """Render an arch-floorplan compatible blueprint dict with embedded SVG."""
    hero_svg = (repo_root / spec.hero_svg_path).read_text(encoding="utf-8").strip()
    return {
        "meta": {
            "slug": spec.slug,
            "title": spec.title,
            "subtitle": spec.subtitle,
            "repoBadge": spec.repo_badge,
            "gradeBadge": spec.grade_badge,
            "recentToggleLabel": spec.recent_toggle_label,
            "versionLabel": spec.version_label,
            "secondaryFitLabel": spec.secondary_fit_label,
        },
        "overview": _serialize_overview(spec, hero_svg),
        "floorplan": {
            "layout": "c4",
            "adrLedgerLabel": spec.adr_ledger_label,
            "matrixLabel": spec.matrix_label,
            "reviewLabel": spec.review_label,
            "probesHeading": spec.probes_heading,
            "findingsHeading": spec.findings_heading,
            "openChecksHeading": spec.open_checks_heading,
            "defaultNodeId": spec.default_node_id,
        },
        "columns": [],
        "zones": [
            {
                "id": zone.zone_id,
                "row": zone.row,
                "x": zone.x_pos,
                "w": zone.width,
                "innerCols": zone.inner_cols,
                "plainTitle": zone.plain_title,
                "plainSub": zone.plain_sub,
                "specTitle": zone.spec_title,
                "specSub": zone.spec_sub,
                "summary": zone.summary,
                "nodes": list(zone.node_ids),
            }
            for zone in spec.zones
        ],
        "macroEdges": [
            {
                "id": edge.edge_id,
                "from": edge.source_zone,
                "to": edge.target_zone,
                "slot": edge.slot,
                "labelPlain": edge.label_plain,
                "labelSpec": edge.label_spec,
            }
            for edge in spec.macro_edges
        ],
        "nodes": [
            {
                "id": node.node_id,
                "h": node.height,
                "span": node.span,
                "status": node.status,
                "recent": node.recent,
                "hasGap": node.has_gap,
                "plainTitle": node.plain_title,
                "plainSub": node.plain_sub,
                "specTitle": node.spec_title,
                "specSub": node.spec_sub,
                "adrs": list(node.adrs),
                "descPlain": node.desc_plain,
                "descSpec": node.desc_spec,
                "portsContract": node.ports_contract,
                "gapNote": node.gap_note,
            }
            for node in spec.nodes
        ],
        "edges": [
            {"id": edge.edge_id, "from": edge.source_node, "to": edge.target_node}
            for edge in spec.edges
        ],
        "adrs": [
            {
                "id": adr.adr_id,
                "status": adr.status,
                "evidence": adr.evidence,
                "title": adr.title,
                "delta": adr.delta,
                "nodes": list(adr.nodes),
                "journeys": list(adr.journeys),
            }
            for adr in spec.adrs
        ],
        "journeys": [_serialize_journey(journey) for journey in spec.journeys],
        "matrixColumns": list(spec.matrix_columns),
        "probes": [
            {"status": probe.status, "title": probe.title, "detail": probe.detail}
            for probe in spec.probes
        ],
        "findings": [
            {
                "id": finding.finding_id,
                "sev": finding.severity,
                "title": finding.title,
                "body": finding.body,
                "targetNode": finding.target_node,
                "targetJourney": finding.target_journey,
            }
            for finding in spec.findings
        ],
        "openChecks": [
            {
                "id": check.check_id,
                "status": check.status,
                "check": check.check,
                "scope": check.scope,
                "gap": check.gap,
            }
            for check in spec.open_checks
        ],
    }


def to_atlas_dict(spec: HumanBlueprint) -> dict[str, Any]:
    """Render L0-L3 Atlas metadata consumed by external workspace hubs."""
    atlas = spec.atlas
    return {
        "id": atlas.repo_id,
        "domain": "projects",
        "workspace_prefix": "projects",
        "name": atlas.name,
        "short_name": atlas.short_name,
        "reporting_id": f"projects/{atlas.repo_id}",
        "segment": atlas.segment,
        "color": atlas.color,
        "helicopter_bluf": atlas.helicopter_bluf,
        "headline_metrics": [dict(metric) for metric in atlas.headline_metrics],
        "guardrails": list(atlas.guardrails),
        "notes_files": [
            {"title": title, "path": f"projects/{atlas.repo_id}/{rel_path}"}
            for title, rel_path in atlas.notes_files
        ],
        "drive_links": [],
        "pillars": [dict(pillar) for pillar in atlas.pillars],
        "edges": [dict(edge) for edge in atlas.atlas_edges],
    }


def to_cockpit_entry(spec: HumanBlueprint) -> dict[str, Any]:
    """Render Home Cockpit Zone 4 summary card directly from the human spec."""
    atlas = spec.atlas
    actors = len(spec.zones)
    nodes = len(spec.nodes)
    journeys = len(spec.journeys)
    return {
        "slug": spec.slug,
        "title": atlas.short_name,
        "group_id": atlas.cockpit_group,
        "path": atlas.cockpit_path,
        "href": f".{atlas.cockpit_path}",
        "sub_group": atlas.cockpit_sub_group,
        "role": atlas.cockpit_role,
        "grade": atlas.cockpit_grade,
        "stats": f"{actors} actors · {nodes} nodes · {journeys} journeys",
        "desc": spec.pitch_headline,
        "keywords": atlas.cockpit_keywords,
        "from_human_spec": True,
        "ports_count": len(spec.ports),
        "adrs_count": len(spec.adrs),
    }
