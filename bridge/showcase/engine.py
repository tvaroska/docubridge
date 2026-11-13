"""`ScenarioEngine`: per-instance state for every loaded scenario (no module globals)."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from bridge.a2ui_inbox import ActionInboxStore, action_inbox_store_from_env
from bridge.agent_gym import AgentGymEngine, agent_gym_from_env
from bridge.knowledge_catalog import KnowledgeCatalog, knowledge_catalog_from_env
from bridge.operational_store import OperationalStore, operational_store_from_env
from bridge.refinery import SchemaRefineryEngine
from bridge.showcase.context import OperationContext
from bridge.showcase.loader import discover_manifests
from bridge.showcase.operations import VIEW_BUILDERS, resolve_operation
from bridge.showcase.pdf_assets import RenderedPdf, render_pdf_asset
from bridge.telemetry import (
    close_outcome,
    resume_outcome,
    start_outcome,
    without_outcome_baggage,
)
from bridge.user_interaction.surfaces import ingest_accepted_submission
from bridge.vault import AuditVaultStore
from contract.enums import Disposition, Gate
from contract.extraction import SourceAnchor
from contract.scenario import (
    ScenarioBeatResult,
    ScenarioManifest,
    ScenarioStageKind,
    ScenarioState,
)
from contract.wire import LedgerEntry


class UnknownScenarioError(KeyError):
    """No loaded scenario has the requested id."""


class ScenarioEngine:
    """Owns manifests, per-scenario beat state, rendered PDFs and uploaded PDF bytes."""

    def __init__(
        self,
        manifests: tuple[ScenarioManifest, ...] | None = None,
        *,
        store: OperationalStore | None = None,
        catalog: KnowledgeCatalog | None = None,
        inbox: ActionInboxStore | None = None,
        gym: AgentGymEngine | None = None,
        refinery: SchemaRefineryEngine | None = None,
        scenarios_dir: Path | str | None = None,
        storage_client: Any = None,
    ) -> None:
        self._scenarios_dir = scenarios_dir
        self._storage_client = storage_client
        self._manifests: dict[str, ScenarioManifest] = {
            m.id: m
            for m in (
                manifests
                if manifests is not None
                else discover_manifests(scenarios_dir, storage_client=storage_client)
            )
        }
        self.store = store or operational_store_from_env()
        self.catalog = catalog or knowledge_catalog_from_env(
            operational_store=self.store
        )
        self.inbox = action_inbox_store_from_env(inbox)
        self.gym = agent_gym_from_env(gym)
        self.refinery = refinery
        self._contexts: dict[str, OperationContext] = {}
        self._pdfs: dict[str, dict[str, RenderedPdf]] = {}
        self._custom_pdfs: dict[str, dict[str, bytes]] = {}
        # Schema-evolution scenarios share one refinery, so only one may be live at a time.
        self._active_refinery_scenario: str | None = None

    # ------------------------------------------------------------------ lookup
    def list(self) -> list[dict[str, Any]]:
        return [m.summary() for m in self._manifests.values()]

    def manifests(self) -> tuple[ScenarioManifest, ...]:
        return tuple(self._manifests.values())

    def get(self, scenario_id: str) -> ScenarioManifest:
        try:
            return self._manifests[scenario_id]
        except KeyError as exc:
            raise UnknownScenarioError(scenario_id) from exc

    def find(
        self,
        *,
        kind: ScenarioStageKind | str | None = None,
        domain: str | None = None,
    ) -> ScenarioManifest | None:
        """First manifest (by `order`) matching the stage kind and/or domain."""
        kind_value = str(kind) if kind is not None else None
        for m in self._manifests.values():
            if kind_value is not None and (
                m.stage is None or m.stage.kind != kind_value
            ):
                continue
            if domain is not None and m.domain != domain:
                continue
            return m
        return None

    # ------------------------------------------------------------------- state
    def _ctx(self, scenario_id: str) -> OperationContext:
        if scenario_id not in self._contexts:
            self._contexts[scenario_id] = self._fresh_context(self.get(scenario_id))
            self._run_setup(self._contexts[scenario_id])
        return self._contexts[scenario_id]

    def _fresh_context(self, manifest: ScenarioManifest) -> OperationContext:
        ctx = OperationContext(
            manifest=manifest,
            state={},
            store=self.store,
            catalog=self.catalog,
            inbox=self.inbox,
            gym=self.gym,
            refinery=self.refinery,
            pdf_bytes=lambda asset_id, sid=manifest.id: (
                self.pdf(sid, asset_id).pdf_bytes
            ),
            pdf_url=lambda asset_id, sid=manifest.id: self.pdf_url(sid, asset_id),
        )
        initial = manifest.stage.initial_state if manifest.stage else {}
        ctx.state = ctx.render(copy.deepcopy(initial))
        ctx.state.setdefault("beat", 0)
        ctx.state.setdefault("done", False)
        return ctx

    def _run_setup(self, ctx: OperationContext) -> None:
        if ctx.manifest.stage is None:
            return
        for op in ctx.manifest.stage.setup:
            resolve_operation(op.name)(ctx, op.params)

    def context(self, scenario_id: str) -> OperationContext:
        """The live mutable context (used by live PDF extraction)."""
        return self._ctx(scenario_id)

    def _view(self, ctx: OperationContext) -> dict[str, Any]:
        if ctx.manifest.stage is None:
            return {"beat": 0, "done": False}
        return VIEW_BUILDERS[ctx.manifest.stage.kind.value](ctx)

    def _claim_refinery(self, manifest: ScenarioManifest) -> None:
        """Make `manifest` the live refinery scenario; a sibling loses its beat state.

        Mirrors the legacy `?domain=` switch: the refinery is one shared engine, so
        reading or driving a different schema-evolution scenario restarts both sides.
        """
        if (
            manifest.stage is None
            or manifest.stage.kind != ScenarioStageKind.schema_evolution
        ):
            return
        previous = self._active_refinery_scenario
        if previous is not None and previous != manifest.id:
            self._contexts.pop(previous, None)
            self._contexts.pop(manifest.id, None)
        self._active_refinery_scenario = manifest.id

    def state(self, scenario_id: str) -> ScenarioState:
        self._claim_refinery(self.get(scenario_id))
        ctx = self._ctx(scenario_id)
        payload = self._view(ctx)
        return ScenarioState(
            scenario_id=scenario_id,
            beat=int(payload.get("beat", ctx.state.get("beat", 0))),
            done=bool(payload.get("done", ctx.state.get("done", False))),
            payload=payload,
        )

    def _close_open_outcome_if_any(
        self, ctx: OperationContext | None, status: str = "abandoned"
    ) -> None:
        if ctx is None:
            return
        outcome_id = ctx.state.get("outcome_id")
        if outcome_id and not ctx.state.get("_outcome_closed"):
            close_outcome(
                status,
                subject=ctx.entity_id,
                owner="meridian-loan-consolidation",
                outcome_id=str(outcome_id),
            )
            ctx.state["_outcome_closed"] = True
            from opentelemetry import context as otel_context

            otel_context.attach(without_outcome_baggage())

    def _ensure_outcome_active(self, ctx: OperationContext) -> str:
        owner = "meridian-loan-consolidation"
        existing = ctx.state.get("outcome_id")
        if existing:
            return resume_outcome(str(existing), owner=owner)
        minted = start_outcome(owner=owner)
        ctx.state["outcome_id"] = minted
        return minted

    def reset(self, scenario_id: str) -> ScenarioState:
        manifest = self.get(scenario_id)
        self._claim_refinery(manifest)
        old_ctx = self._contexts.pop(scenario_id, None)
        self._close_open_outcome_if_any(old_ctx, status="abandoned")
        self._custom_pdfs.pop(scenario_id, None)
        return self.state(scenario_id)

    def reset_all(self) -> None:
        for old_ctx in list(self._contexts.values()):
            self._close_open_outcome_if_any(old_ctx, status="abandoned")
        self._contexts.clear()
        self._custom_pdfs.clear()
        if self.refinery is not None:
            self.refinery.reset()
        self._active_refinery_scenario = None

    def run_beat(self, scenario_id: str, beat: int) -> None:
        """Execute one beat's operations against the live state (no ordering checks)."""
        ctx = self._ctx(scenario_id)
        self._ensure_outcome_active(ctx)
        stage = ctx.manifest.stage
        if stage is None:
            raise ValueError(f"scenario {scenario_id!r} has no stage")
        target = next((b for b in stage.beats if b.index == beat), None)
        if target is None:
            raise ValueError(f"scenario {scenario_id!r} has no beat {beat}")
        for op in target.operations:
            resolve_operation(op.name)(ctx, op.params)
        ctx.state["beat"] = max(int(ctx.state.get("beat", 0)), beat)
        if ctx.state.get("done") and not ctx.state.get("_outcome_closed"):
            self._close_open_outcome_if_any(ctx, status="succeeded")

    def advance(self, scenario_id: str, to_beat: int) -> ScenarioBeatResult:
        manifest = self.get(scenario_id)
        if manifest.stage is None:
            raise ValueError(f"scenario {scenario_id!r} has no stage to advance")
        max_beat = max((b.index for b in manifest.stage.beats), default=0)
        if not 1 <= to_beat <= max_beat:
            raise ValueError(f"beat must be in 1..{max_beat} for {scenario_id!r}")
        self._claim_refinery(manifest)
        ctx = self._ctx(scenario_id)
        current = int(ctx.state.get("beat", 0))
        if to_beat < current:
            old_ctx = self._contexts.pop(scenario_id, None)
            self._close_open_outcome_if_any(old_ctx, status="abandoned")
            ctx = self._ctx(scenario_id)
            current = 0
        self._ensure_outcome_active(ctx)
        executed: list[int] = []
        for beat in range(current + 1, to_beat + 1):
            self.run_beat(scenario_id, beat)
            executed.append(beat)
        snapshot = self.state(scenario_id)
        return ScenarioBeatResult(
            scenario_id=scenario_id,
            beat=snapshot.beat,
            done=snapshot.done,
            payload=snapshot.payload,
            executed_beats=tuple(executed),
        )

    def reload(self) -> dict[str, Any]:
        """Hot-reload scenario manifests and clear cached PDF bytes (`<2s` zero-redeploy)."""
        self.reset_all()
        self._pdfs.clear()
        self._manifests = {
            m.id: m
            for m in discover_manifests(
                self._scenarios_dir, storage_client=self._storage_client
            )
        }
        return {
            "status": "reloaded",
            "scenario_count": len(self._manifests),
            "scenario_ids": list(self._manifests),
        }

    def pdf_catalog(self, scenario_id: str) -> dict[str, RenderedPdf]:
        if scenario_id not in self._pdfs:
            manifest = self.get(scenario_id)
            assets = manifest.stage.pdf_assets if manifest.stage else ()
            self._pdfs[scenario_id] = {
                a.asset_id: render_pdf_asset(a, storage_client=self._storage_client)
                for a in assets
            }
        return self._pdfs[scenario_id]

    def pdf(self, scenario_id: str, asset_id: str) -> RenderedPdf:
        try:
            return self.pdf_catalog(scenario_id)[asset_id]
        except KeyError as exc:
            raise KeyError(
                f"scenario {scenario_id!r} has no pdf asset {asset_id!r}"
            ) from exc

    def pdf_url_base(self, scenario_id: str) -> str:
        manifest = self.get(scenario_id)
        if manifest.stage and manifest.stage.pdf_url_base:
            return manifest.stage.pdf_url_base
        return f"/v1/demo/scenarios/{scenario_id}/pdfs"

    def pdf_url(self, scenario_id: str, asset_id: str) -> str:
        return f"{self.pdf_url_base(scenario_id)}/{self.pdf(scenario_id, asset_id).filename}"

    def resolve_pdf(self, scenario_id: str, identifier: str) -> RenderedPdf | None:
        cleaned = identifier.strip()
        catalog = self.pdf_catalog(scenario_id)
        if cleaned in catalog:
            return catalog[cleaned]
        return next(
            (a for a in catalog.values() if a.filename.lower() == cleaned.lower()), None
        )

    def store_custom_pdf(
        self, scenario_id: str, filename: str, pdf_bytes: bytes
    ) -> None:
        self._custom_pdfs.setdefault(scenario_id, {})[filename] = pdf_bytes

    def custom_pdf(self, scenario_id: str, filename: str) -> bytes | None:
        return self._custom_pdfs.get(scenario_id, {}).get(filename)

    # -------------------------------------------------------------------- seed
    def seed_all(
        self,
        *,
        vault_store: AuditVaultStore | None = None,
        scenario_ids: tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """Ingest every manifest's seed facts whose entity has no facts yet (idempotent)."""
        seeded: list[str] = []
        available: list[str] = []
        for manifest in self._manifests.values():
            if manifest.seed is None:
                continue
            if scenario_ids is not None and manifest.id not in scenario_ids:
                continue
            available.append(manifest.entity_id)
            if self.store.list_facts(manifest.entity_id):
                continue
            for fact in manifest.seed.facts:
                entry = LedgerEntry(
                    doc_id=fact.doc_id,
                    doctype=fact.doctype,
                    issuer=fact.issuer,
                    disposition=Disposition.accepted,
                    gate=Gate.auto_approve,
                    overall_confidence=fact.overall_confidence,
                    message=fact.message,
                    key_fields=dict(fact.key_fields),
                    source_anchors={
                        k: SourceAnchor(
                            page_number=a.page_number,
                            bounding_box=a.bounding_box,
                            snippet=a.snippet,
                            artifact_uri=a.artifact_uri,
                        )
                        for k, a in fact.source_anchors.items()
                    },
                    recorded_at=fact.recorded_at,
                    effective_from=fact.effective_from,
                )
                ingest_accepted_submission(
                    entry,
                    entity_id=manifest.entity_id,
                    context_id=manifest.seed.context_id,
                    raw_document_bytes=fact.raw_document_marker.encode(),
                    vault_store=vault_store,
                    operational_store=self.store,
                    knowledge_catalog=self.catalog,
                    agent_gym=self.gym,
                    recorded_at=fact.recorded_at,
                    effective_from=fact.effective_from,
                )
            seeded.append(manifest.entity_id)
        return {"seeded_entities": seeded, "available_scenarios": available}


__all__ = ["ScenarioEngine", "UnknownScenarioError"]
