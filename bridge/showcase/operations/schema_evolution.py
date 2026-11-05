"""Schema-evolution beat operations and the bi-temporal ontology view builder.

The refinery does the real work (assay, promotion); the manifest's `view` block
supplies every checklist row, graph edge, banner and metric, keyed by beat via
`by_beat` / `from_beat` / `until_beat` (see `bridge.showcase.context.resolve_by_beat`).
"""

from __future__ import annotations

import copy
from typing import Any

from bridge.showcase.context import OperationContext, resolve_by_beat
from bridge.showcase.operations import operation, view_builder


def _refinery(ctx: OperationContext) -> Any:
    if ctx.refinery is None:
        raise RuntimeError(
            f"scenario {ctx.manifest.id!r} needs a SchemaRefineryEngine but none was bound"
        )
    return ctx.refinery


@operation("refinery_reset")
def refinery_reset(ctx: OperationContext, params: dict[str, Any]) -> dict[str, Any]:
    """Return the Zone 6 ontology to its baseline revision (the stage's Beat 1)."""
    del params
    baseline = _refinery(ctx).reset()
    ctx.state["last_proposal_id"] = None
    return {"active_revision": baseline.revision_id}


@operation("refinery_run_assay")
def refinery_run_assay(ctx: OperationContext, params: dict[str, Any]) -> dict[str, Any]:
    """Run the taxonomy / sparsity assay once per scenario and remember the proposal."""
    refinery = _refinery(ctx)
    prop_id = ctx.state.get("last_proposal_id")
    prop = refinery.get_proposal(prop_id) if prop_id else None
    domain = params.get("domain", ctx.manifest.domain.value)
    if prop is None or prop.domain != domain:
        prop, _report, _candidate = refinery.run_taxonomy_assay(domain=domain)
        ctx.state["last_proposal_id"] = prop.proposal_id
    return {"proposal_id": prop.proposal_id}


@operation("refinery_promote")
def refinery_promote(ctx: OperationContext, params: dict[str, Any]) -> dict[str, Any]:
    """Promote the remembered proposal into the manifest's named revision."""
    refinery = _refinery(ctx)
    prop_id = ctx.state.get("last_proposal_id")
    prop = refinery.get_proposal(prop_id) if prop_id else None
    active = refinery.get_active_revision()
    if prop is not None and active.version == params.get(
        "from_version", active.version
    ):
        refinery.promote_proposal(
            prop.proposal_id,
            revision_id=params["revision_id"],
            version=params["version"],
            action_inbox=ctx.inbox,
        )
    return {"active_revision": refinery.get_active_revision().revision_id}


def _agent_block(
    ctx: OperationContext, spec: dict[str, Any], beat_copy: dict[str, Any]
) -> dict[str, Any]:
    block = dict(ctx.render(spec))
    block["headline"] = ctx.render(beat_copy.get("headline", ""))
    block["detail"] = ctx.render(beat_copy.get("detail", ""))
    return block


@view_builder("schema_evolution")
def schema_evolution_view(ctx: OperationContext) -> dict[str, Any]:
    refinery = _refinery(ctx)
    beat = int(ctx.state.get("beat", 0))
    view = ctx.manifest.stage.view if ctx.manifest.stage else {}

    active = refinery.get_active_revision()
    ctx.vars["active_revision"] = active.revision_id
    ctx.vars["active_version"] = active.version
    prop_id = ctx.state.get("last_proposal_id")
    prop = refinery.get_proposal(prop_id) if prop_id else None
    if prop is None:
        proposals = refinery.list_proposals(domain=ctx.manifest.domain.value)
        prop = proposals[-1] if proposals else None
    reports = refinery.list_assay_reports()
    report = reports[-1] if reports else None

    materialised = resolve_by_beat(copy.deepcopy(view), beat)
    beat_copy = next(
        (b for b in materialised.get("beats", []) if int(b.get("index", -1)) == beat),
        {},
    )
    payload: dict[str, Any] = {
        "scenario_domain": ctx.manifest.domain.value,
        "entity_id": ctx.manifest.entity_id,
        "client_name": ctx.manifest.display.client,
        "program_title": ctx.manifest.display.program,
        "beat": beat,
        "done": beat >= int(view.get("done_from_beat", 3)),
        "active_ontology_revision": active.revision_id,
        "active_ontology_version": active.version,
        "entity_count": len(active.entities),
        "edge_count": len(active.edges),
        "revision_history": [
            {
                "revision_id": r.revision_id,
                "version": r.version,
                "state": r.state.value,
                "entity_count": len(r.entities),
                "edge_count": len(r.edges),
            }
            for r in refinery.list_revisions()
        ],
        "financial_summary": ctx.render(materialised.get("financial_summary", {})),
        "placement_checklist": ctx.render(materialised.get("checklist", [])),
        "graph_edges_preview": ctx.render(materialised.get("graph_edges", [])),
        "banner": ctx.render(beat_copy.get("banner", {})),
        "agent1_status": _agent_block(
            ctx, view.get("agent1", {}), beat_copy.get("agent1", {})
        ),
        "agent2_status": _agent_block(
            ctx, view.get("agent2", {}), beat_copy.get("agent2", {})
        ),
        "proposal": prop.model_dump(mode="json") if prop else None,
        "assay_report": report.model_dump(mode="json") if report else None,
        "day2_payoff": ctx.render(materialised.get("day2_payoff")),
    }
    if "socratic_challenge" in view:
        payload["socratic_challenge"] = ctx.render(view["socratic_challenge"])
    return payload
