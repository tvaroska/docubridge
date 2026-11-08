"""Durable SQL-backed Knowledge Catalog & Ontology Graph store (AlloyDB / Cloud SQL Postgres / SQLite)."""

from __future__ import annotations

import asyncio
import os
import threading
import uuid
from collections.abc import Coroutine
from typing import Any

from bridge.identity import SpiffeAgentPolicy
from bridge.knowledge_catalog.catalog import (
    InMemoryKnowledgeCatalog,
    KnowledgeCatalog,
    _normalize_field_lookup_key,
)
from bridge.knowledge_catalog.models import (
    DEFAULT_GLOSSARY_TERMS,
    DEFAULT_SYSTEM_ASSETS,
    AccessPolicy,
    AssetLocationZone,
    BusinessGlossaryTerm,
    CatalogAsset,
    CatalogRole,
    LineageTrace,
)
from bridge.operational_store import (
    ConsolidatedFact,
    OperationalStore,
    operational_store_from_env,
)
from contract.refinery import (
    OntologyEdge,
    OntologyRevision,
    OntologyRevisionState,
)
from contract.wire import LedgerEntry

CATALOG_DB_ENV = "BRIDGE_CATALOG_DB_URL"
CATALOG_REVISIONS_TABLE_NAME = "catalog_ontology_revisions"
CATALOG_EDGES_TABLE_NAME = "catalog_ontology_edges"
CATALOG_ASSETS_TABLE_NAME = "catalog_assets"
CATALOG_LINEAGE_TABLE_NAME = "catalog_lineage_traces"
_OP_TIMEOUT_S = 30.0


class SqlKnowledgeCatalog(InMemoryKnowledgeCatalog):
    """Durable SQL-backed Zone 6 Knowledge Catalog & TBox Ontology Graph multiplexed on ``DsnSqlRuntime``."""

    def __init__(
        self,
        dsn: str,
        *,
        glossary_terms: tuple[BusinessGlossaryTerm, ...] = DEFAULT_GLOSSARY_TERMS,
        system_assets: tuple[CatalogAsset, ...] = DEFAULT_SYSTEM_ASSETS,
        access_policies: dict[CatalogRole, AccessPolicy] | None = None,
        spiffe_policies: dict[str, SpiffeAgentPolicy] | None = None,
        operational_store: OperationalStore | None = None,
    ) -> None:
        cleaned = (dsn or "").strip()
        if not cleaned:
            raise ValueError("SqlKnowledgeCatalog requires a non-empty database DSN")
        if ":memory:" in cleaned or "mode=memory" in cleaned:
            raise ValueError(
                "SqlKnowledgeCatalog refuses an in-memory SQLite DSN; use "
                "InMemoryKnowledgeCatalog or a file/Postgres DSN"
            )
        super().__init__(
            glossary_terms=glossary_terms,
            system_assets=system_assets,
            access_policies=access_policies,
            spiffe_policies=spiffe_policies,
            operational_store=operational_store,
        )
        self.dsn = cleaned
        self._init_lock = threading.Lock()
        self._runtime: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._engine: Any = None
        self._closed = False
        self._revisions_table: Any = None
        self._edges_table: Any = None
        self._assets_table: Any = None
        self._lineage_table: Any = None
        self._revisions: list[OntologyRevision] = []
        self._active_revision: OntologyRevision | None = None

    def _ensure_ready(self) -> None:
        if self._engine is not None:
            return
        with self._init_lock:
            if self._closed:
                raise RuntimeError("the knowledge catalog SQL store is closed")
            if self._engine is not None:
                return
            from sqlalchemy import (
                Column,
                Integer,
                MetaData,
                String,
                Table,
                Text,
                select,
            )

            from bridge.refinery.okf_loader import build_baseline_ontology_revision
            from bridge.sql_runtime import acquire_dsn_runtime

            meta = MetaData()
            self._revisions_table = Table(
                CATALOG_REVISIONS_TABLE_NAME,
                meta,
                Column("seq", Integer, primary_key=True, autoincrement=True),
                Column(
                    "revision_id", String(128), nullable=False, unique=True, index=True
                ),
                Column("state", String(64), nullable=False, index=True),
                Column("version", String(64), nullable=False),
                Column("content_sha256", String(128), nullable=False),
                Column("payload_json", Text, nullable=False),
            )
            self._edges_table = Table(
                CATALOG_EDGES_TABLE_NAME,
                meta,
                Column("edge_pk", String(256), primary_key=True),
                Column("revision_id", String(128), nullable=False, index=True),
                Column("edge_id", String(128), nullable=False, index=True),
                Column("source_id", String(128), nullable=False, index=True),
                Column("relation", String(64), nullable=False, index=True),
                Column("target_id", String(128), nullable=False, index=True),
                Column("priority_rank", Integer, nullable=True),
                Column("payload_json", Text, nullable=False),
            )
            self._assets_table = Table(
                CATALOG_ASSETS_TABLE_NAME,
                meta,
                Column("asset_id", String(128), primary_key=True),
                Column("zone", String(64), nullable=False, index=True),
                Column("classification", String(64), nullable=False, index=True),
                Column("payload_json", Text, nullable=False),
            )
            self._lineage_table = Table(
                CATALOG_LINEAGE_TABLE_NAME,
                meta,
                Column("trace_id", String(128), primary_key=True),
                Column("entity_id", String(128), nullable=False, index=True),
                Column("field_path", String(128), nullable=False, index=True),
                Column("ontology_revision_id", String(128), nullable=False, index=True),
                Column("payload_json", Text, nullable=False),
            )
            revisions_table = self._revisions_table
            edges_table = self._edges_table
            baseline = build_baseline_ontology_revision()
            seed_rows: list[str] = []

            async def _create_and_seed(engine: Any) -> None:
                async with engine.begin() as conn:
                    await conn.run_sync(meta.create_all)
                    res = await conn.execute(
                        select(revisions_table.c.payload_json).order_by(
                            revisions_table.c.seq.asc()
                        )
                    )
                    rows = [r[0] for r in res]
                    if not rows:
                        await conn.execute(
                            revisions_table.insert().values(
                                revision_id=baseline.revision_id,
                                state=baseline.state.value,
                                version=baseline.version,
                                content_sha256=baseline.content_sha256,
                                payload_json=baseline.model_dump_json(),
                            )
                        )
                        for edge in baseline.edges:
                            await conn.execute(
                                edges_table.insert().values(
                                    edge_pk=f"{baseline.revision_id}:{edge.edge_id}",
                                    revision_id=baseline.revision_id,
                                    edge_id=edge.edge_id,
                                    source_id=edge.source_id,
                                    relation=edge.relation.value
                                    if hasattr(edge.relation, "value")
                                    else str(edge.relation),
                                    target_id=edge.target_id,
                                    priority_rank=edge.priority_rank,
                                    payload_json=edge.model_dump_json(),
                                )
                            )
                        seed_rows.append(baseline.model_dump_json())
                    else:
                        seed_rows.extend(rows)

            rt = acquire_dsn_runtime(
                self.dsn,
                init_coro_factory=_create_and_seed,
                thread_name="bridge-knowledge-catalog-store",
                timeout_s=_OP_TIMEOUT_S,
            )
            self._runtime = rt
            self._loop = rt.loop
            self._thread = rt.thread
            self._engine = rt.engine
            self._apply_loaded_revisions(
                [OntologyRevision.model_validate_json(raw) for raw in seed_rows]
            )

    def _apply_loaded_revisions(self, revs: list[OntologyRevision]) -> None:
        if not revs:
            from bridge.refinery.okf_loader import build_baseline_ontology_revision

            revs = [build_baseline_ontology_revision()]
        self._revisions = list(revs)
        active = next(
            (r for r in reversed(revs) if r.state == OntologyRevisionState.ACTIVE),
            revs[-1],
        )
        self._active_revision = active
        super().register_ontology_revision(
            active, activate=(active.state == OntologyRevisionState.ACTIVE)
        )

    def _run(self, coro: Coroutine[Any, Any, Any]) -> Any:
        self._ensure_ready()
        assert self._loop is not None
        if self._thread is not None and threading.current_thread() is self._thread:
            raise RuntimeError(
                "SqlKnowledgeCatalog synchronous API called from its own event loop thread"
            )
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return fut.result(timeout=_OP_TIMEOUT_S)

    def close(self) -> None:
        """Release the shared SQL runtime handle."""
        with self._init_lock:
            if self._closed:
                return
            self._closed = True
            rt = self._runtime
            self._runtime = None
            self._engine = None
            self._loop = None
            self._thread = None
        if rt is not None:
            from bridge.sql_runtime import release_dsn_runtime

            release_dsn_runtime(self.dsn, rt, timeout_s=_OP_TIMEOUT_S)
        _SQL_CATALOG_CACHE.pop(self.dsn, None)

    def _sync_revisions_from_sql(self) -> list[OntologyRevision]:
        self._ensure_ready()
        revisions_table = self._revisions_table
        engine = self._engine

        async def _op() -> list[OntologyRevision]:
            from sqlalchemy import select

            async with engine.connect() as conn:
                res = await conn.execute(
                    select(revisions_table.c.payload_json).order_by(
                        revisions_table.c.seq.asc()
                    )
                )
                return [OntologyRevision.model_validate_json(r[0]) for r in res]

        revs = self._run(_op())
        self._apply_loaded_revisions(revs)
        return list(self._revisions)

    def get_active_ontology_revision(self) -> OntologyRevision:
        """Return the currently active OntologyRevision in Zone 6 (synced across SQL replicas)."""
        self._sync_revisions_from_sql()
        assert self._active_revision is not None
        return self._active_revision

    def list_ontology_revisions(self) -> list[OntologyRevision]:
        """Return all registered OntologyRevision snapshots in chronological order."""
        return self._sync_revisions_from_sql()

    def register_ontology_revision(
        self,
        revision: OntologyRevision,
        *,
        activate: bool = True,
    ) -> OntologyRevision:
        """Register a new OntologyRevision snapshot + its graph edges in SQL."""
        current = self._sync_revisions_from_sql()
        updated: list[OntologyRevision] = []
        replaced = False
        for existing in current:
            if existing.revision_id == revision.revision_id:
                target_rev = (
                    revision.model_copy(update={"state": OntologyRevisionState.ACTIVE})
                    if activate
                    else revision
                )
                updated.append(target_rev)
                revision = target_rev
                replaced = True
            elif activate and existing.state == OntologyRevisionState.ACTIVE:
                updated.append(
                    existing.model_copy(
                        update={
                            "state": OntologyRevisionState.DEPRECATED,
                            "effective_to": revision.effective_from,
                        }
                    )
                )
            else:
                updated.append(existing)
        if not replaced:
            target_rev = (
                revision.model_copy(update={"state": OntologyRevisionState.ACTIVE})
                if activate
                else revision
            )
            updated.append(target_rev)
            revision = target_rev

        revisions_table = self._revisions_table
        edges_table = self._edges_table
        engine = self._engine

        async def _op() -> None:
            from sqlalchemy import delete

            async with engine.begin() as conn:
                await conn.execute(delete(edges_table))
                await conn.execute(delete(revisions_table))
                for rev in updated:
                    await conn.execute(
                        revisions_table.insert().values(
                            revision_id=rev.revision_id,
                            state=rev.state.value,
                            version=rev.version,
                            content_sha256=rev.content_sha256,
                            payload_json=rev.model_dump_json(),
                        )
                    )
                    for edge in rev.edges:
                        await conn.execute(
                            edges_table.insert().values(
                                edge_pk=f"{rev.revision_id}:{edge.edge_id}",
                                revision_id=rev.revision_id,
                                edge_id=edge.edge_id,
                                source_id=edge.source_id,
                                relation=edge.relation.value
                                if hasattr(edge.relation, "value")
                                else str(edge.relation),
                                target_id=edge.target_id,
                                priority_rank=edge.priority_rank,
                                payload_json=edge.model_dump_json(),
                            )
                        )

        self._run(_op())
        self._apply_loaded_revisions(updated)
        return revision

    def reset_ontology_revisions(self) -> OntologyRevision:
        """Reset the Zone 6 Ontology Graph back to the baseline revision in SQL."""
        from bridge.refinery.okf_loader import build_baseline_ontology_revision

        baseline = build_baseline_ontology_revision()
        self._ensure_ready()
        revisions_table = self._revisions_table
        edges_table = self._edges_table
        engine = self._engine

        async def _op() -> None:
            from sqlalchemy import delete

            async with engine.begin() as conn:
                await conn.execute(delete(edges_table))
                await conn.execute(delete(revisions_table))
                await conn.execute(
                    revisions_table.insert().values(
                        revision_id=baseline.revision_id,
                        state=baseline.state.value,
                        version=baseline.version,
                        content_sha256=baseline.content_sha256,
                        payload_json=baseline.model_dump_json(),
                    )
                )
                for edge in baseline.edges:
                    await conn.execute(
                        edges_table.insert().values(
                            edge_pk=f"{baseline.revision_id}:{edge.edge_id}",
                            revision_id=baseline.revision_id,
                            edge_id=edge.edge_id,
                            source_id=edge.source_id,
                            relation=edge.relation.value
                            if hasattr(edge.relation, "value")
                            else str(edge.relation),
                            target_id=edge.target_id,
                            priority_rank=edge.priority_rank,
                            payload_json=edge.model_dump_json(),
                        )
                    )

        self._run(_op())
        super().reset_ontology_revisions()
        self._apply_loaded_revisions([baseline])
        return baseline

    def list_ontology_edges(
        self,
        *,
        relation: str | None = None,
        source_id: str | None = None,
    ) -> list[OntologyEdge]:
        """Return OntologyEdge records from the active OntologyRevision in SQL."""
        active = self.get_active_ontology_revision()
        edges = list(active.edges)
        if relation is not None:
            edges = [e for e in edges if e.relation == relation]
        if source_id is not None:
            edges = [e for e in edges if e.source_id == source_id]
        return edges

    def register_asset(self, asset: CatalogAsset) -> None:
        """Register or update a catalog asset in SQL."""
        super().register_asset(asset)
        self._ensure_ready()
        assets_table = self._assets_table
        engine = self._engine

        async def _op() -> None:
            from sqlalchemy import delete

            async with engine.begin() as conn:
                await conn.execute(
                    delete(assets_table).where(
                        assets_table.c.asset_id == asset.asset_id
                    )
                )
                await conn.execute(
                    assets_table.insert().values(
                        asset_id=asset.asset_id,
                        zone=asset.zone.value
                        if hasattr(asset.zone, "value")
                        else str(asset.zone),
                        classification=asset.classification,
                        payload_json=asset.model_dump_json(),
                    )
                )

        self._run(_op())

    def list_assets(
        self,
        *,
        zone: AssetLocationZone | None = None,
        external_safe_only: bool = False,
    ) -> list[CatalogAsset]:
        """Return catalog assets synced from SQL."""
        self._ensure_ready()
        assets_table = self._assets_table
        engine = self._engine

        async def _op() -> list[CatalogAsset]:
            from sqlalchemy import select

            async with engine.connect() as conn:
                res = await conn.execute(
                    select(assets_table.c.payload_json).order_by(
                        assets_table.c.asset_id.asc()
                    )
                )
                return [CatalogAsset.model_validate_json(r[0]) for r in res]

        for item in self._run(_op()):
            self._assets[item.asset_id] = item
        return super().list_assets(zone=zone, external_safe_only=external_safe_only)

    def register_submission_lineage(
        self,
        entry: LedgerEntry,
        facts: list[ConsolidatedFact],
    ) -> list[LineageTrace]:
        """Record end-to-end W3C PROV-O lineage edges in SQL."""
        traces = super().register_submission_lineage(entry, facts)
        self._ensure_ready()
        lineage_table = self._lineage_table
        engine = self._engine

        async def _op() -> None:
            async with engine.begin() as conn:
                for trace in traces:
                    await conn.execute(
                        lineage_table.insert().values(
                            trace_id=f"tr-{uuid.uuid4().hex}",
                            entity_id=trace.entity_id,
                            field_path=trace.field_path,
                            ontology_revision_id=trace.ontology_revision_id,
                            payload_json=trace.model_dump_json(),
                        )
                    )

        self._run(_op())
        return traces

    def _hydrate_sql_lineage(self, entity_id: str) -> None:
        self._ensure_ready()
        lineage_table = self._lineage_table
        engine = self._engine

        async def _op() -> list[LineageTrace]:
            from sqlalchemy import select

            async with engine.connect() as conn:
                res = await conn.execute(
                    select(lineage_table.c.payload_json).where(
                        lineage_table.c.entity_id == entity_id
                    )
                )
                return [LineageTrace.model_validate_json(r[0]) for r in res]

        for trace in self._run(_op()):
            self._lineage.setdefault((trace.entity_id, trace.field_path), []).append(
                trace
            )
            leaf = trace.field_path.split(".")[-1]
            if leaf != trace.field_path:
                self._lineage.setdefault((trace.entity_id, leaf), []).append(trace)

    def trace_lineage(
        self, entity_id: str, field_name_or_path: str
    ) -> LineageTrace | None:
        """Trace upstream and downstream W3C PROV-O lineage edges from SQL or OperationalStore."""
        key = _normalize_field_lookup_key(field_name_or_path)
        if not self._lineage.get((entity_id, key)):
            self._hydrate_sql_lineage(entity_id)
        return super().trace_lineage(entity_id, field_name_or_path)


_SQL_CATALOG_CACHE: dict[str, SqlKnowledgeCatalog] = {}
_SQL_CATALOG_LOCK = threading.Lock()
_DEFAULT_CATALOG: InMemoryKnowledgeCatalog | None = None


def build_sql_or_memory_catalog(
    dsn: str | None = None,
    *,
    operational_store: OperationalStore | None = None,
) -> KnowledgeCatalog:
    """Resolve a SQL-backed or in-memory KnowledgeCatalog from DSN or environment."""
    global _DEFAULT_CATALOG
    store = operational_store or operational_store_from_env()
    effective_dsn = (
        (dsn or "").strip()
        or os.environ.get(CATALOG_DB_ENV, "").strip()
        or os.environ.get("BRIDGE_REFINERY_DB_URL", "").strip()
        or os.environ.get("BRIDGE_OPERATIONAL_DB_URL", "").strip()
        or os.environ.get("DATABASE_URL", "").strip()
    )
    if (
        effective_dsn
        and ":memory:" not in effective_dsn
        and "mode=memory" not in effective_dsn
    ):
        with _SQL_CATALOG_LOCK:
            cached = _SQL_CATALOG_CACHE.get(effective_dsn)
            if cached is None or cached._closed:
                cached = SqlKnowledgeCatalog(effective_dsn, operational_store=store)
                _SQL_CATALOG_CACHE[effective_dsn] = cached
            else:
                cached.bind_operational_store(store)
            return cached
    if _DEFAULT_CATALOG is None:
        _DEFAULT_CATALOG = InMemoryKnowledgeCatalog(operational_store=store)
    else:
        _DEFAULT_CATALOG.bind_operational_store(store)
    return _DEFAULT_CATALOG
