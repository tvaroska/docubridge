"""Shared Pydantic wire models for showcase scenarios (`scenarios/<name>/scenario.yaml`).

A scenario is pure data: seed facts, display copy, referenced doctypes / process
skill, and an optional staged beat script whose operations are resolved by name
inside `bridge/showcase/operations`. Nothing in these models names a concrete
scenario, so a new showcase is a folder, not a code change.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ScenarioDomain(StrEnum):
    """Industry vertical a scenario belongs to (drives the console domain switcher)."""

    banking = "banking"
    mortgage = "mortgage"
    insurance = "insurance"
    benefits = "benefits"
    other = "other"


class ScenarioStageKind(StrEnum):
    """Beat engine family a staged scenario runs on."""

    consolidation = "consolidation"
    schema_evolution = "schema_evolution"


class ScenarioDisplay(BaseModel):
    """Console copy for one scenario: client, program, and arbitrary labelled strings."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    client: str
    program: str
    labels: dict[str, str] = Field(default_factory=dict)


class SeedAnchor(BaseModel):
    """Page / bounding-box provenance for one seeded key field."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    page_number: int = Field(ge=1)
    bounding_box: tuple[float, float, float, float]
    snippet: str
    artifact_uri: str | None = None


class SeedFact(BaseModel):
    """One accepted ledger entry ingested at seed time (money stays a string)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    doc_id: str
    doctype: str
    issuer: str
    message: str
    key_fields: dict[str, str]
    source_anchors: dict[str, SeedAnchor] = Field(default_factory=dict)
    raw_document_marker: str
    recorded_at: datetime
    effective_from: datetime
    overall_confidence: float = 0.99


class SeedLineageHop(BaseModel):
    """Declared lineage hop between two seeded documents on one field."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    field: str
    from_doc_id: str
    to_doc_id: str


class ScenarioSeed(BaseModel):
    """Baseline bi-temporal facts seeded under the scenario's entity id."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    context_id: str
    facts: tuple[SeedFact, ...]
    lineage: tuple[SeedLineageHop, ...] = ()


class ScenarioOperation(BaseModel):
    """One registered beat operation invocation (`name` resolves in the registry)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    params: dict[str, Any] = Field(default_factory=dict)


class ScenarioBeat(BaseModel):
    """An ordered beat: its title and the operations executed when it is reached."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    index: int = Field(ge=1)
    title: str
    operations: tuple[ScenarioOperation, ...] = ()


class ScenarioPdfAsset(BaseModel):
    """A sample PDF rendered from a declarative page spec (`pages`) or fetched from Cloud Storage (`gcs_uri`)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    asset_id: str
    filename: str
    title: str
    subtitle: str
    batch_stage: str
    page_count: int = Field(ge=1)
    highlight_page: int = Field(ge=1)
    document_title: str = ""
    pages: tuple[dict[str, Any], ...] = ()
    gcs_uri: str | None = None
    sha256: str | None = None


class ScenarioStage(BaseModel):
    """The staged beat script plus the kind-specific data the operations read."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: ScenarioStageKind
    setup: tuple[ScenarioOperation, ...] = ()
    beats: tuple[ScenarioBeat, ...]
    initial_state: dict[str, Any] = Field(default_factory=dict)
    view: dict[str, Any] = Field(default_factory=dict)
    pdf_url_base: str | None = None
    pdf_assets: tuple[ScenarioPdfAsset, ...] = ()
    a2a_counterparties: tuple[dict[str, Any], ...] = ()
    live_extraction: dict[str, Any] = Field(default_factory=dict)


class ScenarioManifest(BaseModel):
    """The whole `scenario.yaml` document."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    domain: ScenarioDomain
    title: str
    order: int = 100
    display: ScenarioDisplay
    entity_id: str
    related_entity_ids: dict[str, str] = Field(default_factory=dict)
    process_skill: str | None = None
    doctypes: tuple[str, ...] = ()
    agents: dict[str, str] = Field(default_factory=dict)
    principals: dict[str, str] = Field(default_factory=dict)
    seed: ScenarioSeed | None = None
    stage: ScenarioStage | None = None

    def summary(self) -> dict[str, Any]:
        """The manifest without seed / stage bodies — what `GET /v1/demo/scenarios` lists."""
        return {
            "id": self.id,
            "domain": self.domain.value,
            "title": self.title,
            "order": self.order,
            "entity_id": self.entity_id,
            "related_entity_ids": dict(self.related_entity_ids),
            "process_skill": self.process_skill,
            "doctypes": list(self.doctypes),
            "agents": dict(self.agents),
            "principals": dict(self.principals),
            "display": self.display.model_dump(mode="json"),
            "seeded": self.seed is not None,
            "stage_kind": self.stage.kind.value if self.stage else None,
            "beats": [
                {"index": b.index, "title": b.title}
                for b in (self.stage.beats if self.stage else ())
            ],
            "pdf_assets": [
                {"asset_id": a.asset_id, "filename": a.filename, "title": a.title}
                for a in (self.stage.pdf_assets if self.stage else ())
            ],
        }


class ScenarioState(BaseModel):
    """Envelope returned by the generic `/v1/demo/scenarios/{id}/state` route."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scenario_id: str
    beat: int = Field(ge=0)
    done: bool
    payload: dict[str, Any]


class ScenarioBeatResult(ScenarioState):
    """State after an `advance`, annotated with the beats actually executed."""

    executed_beats: tuple[int, ...] = ()


class AdvanceScenarioRequest(BaseModel):
    """Request body for `POST /v1/demo/scenarios/{id}/advance`."""

    model_config = ConfigDict(extra="forbid")

    beat: int = Field(ge=1)


__all__ = [
    "AdvanceScenarioRequest",
    "ScenarioBeat",
    "ScenarioBeatResult",
    "ScenarioDisplay",
    "ScenarioDomain",
    "ScenarioManifest",
    "ScenarioOperation",
    "ScenarioPdfAsset",
    "ScenarioSeed",
    "ScenarioStage",
    "ScenarioStageKind",
    "ScenarioState",
    "SeedAnchor",
    "SeedFact",
    "SeedLineageHop",
]
