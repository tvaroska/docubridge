"""Durable SQL-backed Schema Refinery store (AlloyDB / Cloud SQL Postgres / SQLite)."""

from __future__ import annotations

import asyncio
import os
import threading
from collections.abc import Coroutine
from typing import Any

from bridge.knowledge_catalog import KnowledgeCatalog, knowledge_catalog_from_env
from bridge.refinery.engine import InMemorySchemaRefineryEngine, SchemaRefineryEngine
from contract.refinery import (
    OntologyRevision,
    OntologyRevisionState,
    RefineryAssayReport,
    RefineryProposal,
    RefineryProposalStatus,
)

REFINERY_DB_ENV = "BRIDGE_REFINERY_DB_URL"
REFINERY_REVISIONS_TABLE_NAME = "refinery_ontology_revisions"
REFINERY_PROPOSALS_TABLE_NAME = "refinery_proposals"
REFINERY_REPORTS_TABLE_NAME = "refinery_assay_reports"
_OP_TIMEOUT_S = 30.0


class SqlSchemaRefineryEngine(InMemorySchemaRefineryEngine):
    """Durable SQL-backed Schema Refinery engine multiplexed on ``DsnSqlRuntime``."""

    def __init__(
        self,
        dsn: str,
        *,
        knowledge_catalog: KnowledgeCatalog | None = None,
    ) -> None:
        cleaned = (dsn or "").strip()
        if not cleaned:
            raise ValueError(
                "SqlSchemaRefineryEngine requires a non-empty database DSN"
            )
        if ":memory:" in cleaned or "mode=memory" in cleaned:
            raise ValueError(
                "SqlSchemaRefineryEngine refuses an in-memory SQLite DSN; use "
                "InMemorySchemaRefineryEngine or a file/Postgres DSN"
            )
        super().__init__(knowledge_catalog=knowledge_catalog)
        self.dsn = cleaned
        self._init_lock = threading.Lock()
        self._runtime: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._engine: Any = None
        self._closed = False
        self._revisions_table: Any = None
        self._proposals_table: Any = None
        self._reports_table: Any = None

    def _ensure_ready(self) -> None:
        if self._engine is not None:
            return
        with self._init_lock:
            if self._closed:
                raise RuntimeError("the schema refinery SQL store is closed")
            if self._engine is not None:
                return
            from sqlalchemy import Column, MetaData, String, Table, Text, select

            from bridge.sql_runtime import acquire_dsn_runtime

            meta = MetaData()
            self._revisions_table = Table(
                REFINERY_REVISIONS_TABLE_NAME,
                meta,
                Column("revision_id", String(128), primary_key=True),
                Column("state", String(64), nullable=False, index=True),
                Column("payload_json", Text, nullable=False),
            )
            self._proposals_table = Table(
                REFINERY_PROPOSALS_TABLE_NAME,
                meta,
                Column("proposal_id", String(128), primary_key=True),
                Column("domain", String(64), nullable=False, index=True),
                Column("status", String(64), nullable=False, index=True),
                Column("payload_json", Text, nullable=False),
            )
            self._reports_table = Table(
                REFINERY_REPORTS_TABLE_NAME,
                meta,
                Column("report_id", String(128), primary_key=True),
                Column("proposal_id", String(128), nullable=False, index=True),
                Column("payload_json", Text, nullable=False),
            )
            revisions_table = self._revisions_table
            baseline = self._catalog.get_active_ontology_revision()
            seed_rows: list[str] = []

            async def _create_and_seed(engine: Any) -> None:
                async with engine.begin() as conn:
                    await conn.run_sync(meta.create_all)
                    res = await conn.execute(
                        select(revisions_table.c.payload_json).order_by(
                            revisions_table.c.revision_id.asc()
                        )
                    )
                    rows = [r[0] for r in res]
                    if not rows:
                        await conn.execute(
                            revisions_table.insert().values(
                                revision_id=baseline.revision_id,
                                state=baseline.state.value,
                                payload_json=baseline.model_dump_json(),
                            )
                        )
                        seed_rows.append(baseline.model_dump_json())
                    else:
                        seed_rows.extend(rows)

            rt = acquire_dsn_runtime(
                self.dsn,
                init_coro_factory=_create_and_seed,
                thread_name="bridge-schema-refinery-store",
                timeout_s=_OP_TIMEOUT_S,
            )
            self._runtime = rt
            self._loop = rt.loop
            self._thread = rt.thread
            self._engine = rt.engine

            for raw in seed_rows:
                rev = OntologyRevision.model_validate_json(raw)
                self._catalog.register_ontology_revision(
                    rev,
                    activate=(rev.state == OntologyRevisionState.ACTIVE),
                )

    def _run(self, coro: Coroutine[Any, Any, Any]) -> Any:
        self._ensure_ready()
        assert self._loop is not None
        if self._thread is not None and threading.current_thread() is self._thread:
            raise RuntimeError(
                "SqlSchemaRefineryEngine synchronous API called from its own event loop thread"
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
        _SQL_REFINERY_CACHE.pop(self.dsn, None)

    def _save_revision(
        self,
        revision: OntologyRevision,
        *,
        activate: bool = True,
    ) -> OntologyRevision:
        saved = super()._save_revision(revision, activate=activate)
        self._ensure_ready()
        table = self._revisions_table
        engine = self._engine
        all_revs = self._catalog.list_ontology_revisions()

        async def _op() -> None:
            from sqlalchemy import delete

            async with engine.begin() as conn:
                await conn.execute(delete(table))
                for rev in all_revs:
                    await conn.execute(
                        table.insert().values(
                            revision_id=rev.revision_id,
                            state=rev.state.value,
                            payload_json=rev.model_dump_json(),
                        )
                    )

        self._run(_op())
        return saved

    def _save_proposal(self, proposal: RefineryProposal) -> None:
        super()._save_proposal(proposal)
        self._ensure_ready()
        table = self._proposals_table
        engine = self._engine

        async def _op() -> None:
            from sqlalchemy import delete

            async with engine.begin() as conn:
                await conn.execute(
                    delete(table).where(table.c.proposal_id == proposal.proposal_id)
                )
                await conn.execute(
                    table.insert().values(
                        proposal_id=proposal.proposal_id,
                        domain=proposal.domain,
                        status=proposal.status.value,
                        payload_json=proposal.model_dump_json(),
                    )
                )

        self._run(_op())

    def _save_report(self, report: RefineryAssayReport) -> None:
        super()._save_report(report)
        self._ensure_ready()
        table = self._reports_table
        engine = self._engine

        async def _op() -> None:
            from sqlalchemy import delete

            async with engine.begin() as conn:
                await conn.execute(
                    delete(table).where(table.c.report_id == report.report_id)
                )
                await conn.execute(
                    table.insert().values(
                        report_id=report.report_id,
                        proposal_id=report.proposal_id,
                        payload_json=report.model_dump_json(),
                    )
                )

        self._run(_op())

    def list_proposals(
        self,
        *,
        domain: str | None = None,
        status: RefineryProposalStatus | None = None,
    ) -> list[RefineryProposal]:
        """Return candidate or promoted RefineryProposal records from SQL."""
        self._ensure_ready()
        table = self._proposals_table
        engine = self._engine

        async def _op() -> list[RefineryProposal]:
            from sqlalchemy import select

            async with engine.connect() as conn:
                res = await conn.execute(
                    select(table.c.payload_json).order_by(table.c.proposal_id.asc())
                )
                return [RefineryProposal.model_validate_json(r[0]) for r in res]

        items = self._run(_op())
        for item in items:
            self._proposals[item.proposal_id] = item
        return super().list_proposals(domain=domain, status=status)

    def get_proposal(self, proposal_id: str) -> RefineryProposal | None:
        """Look up a single RefineryProposal by ID from SQL."""
        self.list_proposals()
        return super().get_proposal(proposal_id)

    def list_assay_reports(
        self,
        *,
        proposal_id: str | None = None,
    ) -> list[RefineryAssayReport]:
        """Return 3-Tier RefineryAssayReport scorecards from SQL."""
        self._ensure_ready()
        table = self._reports_table
        engine = self._engine

        async def _op() -> list[RefineryAssayReport]:
            from sqlalchemy import select

            async with engine.connect() as conn:
                res = await conn.execute(
                    select(table.c.payload_json).order_by(table.c.report_id.asc())
                )
                return [RefineryAssayReport.model_validate_json(r[0]) for r in res]

        items = self._run(_op())
        for item in items:
            self._reports[item.report_id] = item
        return super().list_assay_reports(proposal_id=proposal_id)

    def reset(self) -> OntologyRevision:
        """Reset all SQL ontology revisions, proposals, and assay reports back to baseline."""
        baseline = super().reset()
        self._ensure_ready()
        revisions_table = self._revisions_table
        proposals_table = self._proposals_table
        reports_table = self._reports_table
        engine = self._engine

        async def _op() -> None:
            from sqlalchemy import delete

            async with engine.begin() as conn:
                await conn.execute(delete(reports_table))
                await conn.execute(delete(proposals_table))
                await conn.execute(delete(revisions_table))
                await conn.execute(
                    revisions_table.insert().values(
                        revision_id=baseline.revision_id,
                        state=baseline.state.value,
                        payload_json=baseline.model_dump_json(),
                    )
                )

        self._run(_op())
        return baseline


_SQL_REFINERY_CACHE: dict[str, SqlSchemaRefineryEngine] = {}
_SQL_REFINERY_LOCK = threading.Lock()
_DEFAULT_REFINERY_ENGINE: InMemorySchemaRefineryEngine | None = None


def schema_refinery_from_env(
    existing: SchemaRefineryEngine | None = None,
    *,
    knowledge_catalog: KnowledgeCatalog | None = None,
) -> SchemaRefineryEngine:
    """Resolve the Schema Refinery engine from environment configuration."""
    global _DEFAULT_REFINERY_ENGINE
    if existing is not None:
        return existing
    dsn = (
        os.environ.get(REFINERY_DB_ENV, "").strip()
        or os.environ.get("BRIDGE_GYM_DB_URL", "").strip()
        or os.environ.get("BRIDGE_OPERATIONAL_DB_URL", "").strip()
        or os.environ.get("DATABASE_URL", "").strip()
    )
    if dsn and ":memory:" not in dsn and "mode=memory" not in dsn:
        with _SQL_REFINERY_LOCK:
            cached = _SQL_REFINERY_CACHE.get(dsn)
            if cached is None or cached._closed:
                cached = SqlSchemaRefineryEngine(
                    dsn,
                    knowledge_catalog=knowledge_catalog,
                )
                _SQL_REFINERY_CACHE[dsn] = cached
            return cached
    if _DEFAULT_REFINERY_ENGINE is None:
        _DEFAULT_REFINERY_ENGINE = InMemorySchemaRefineryEngine(
            knowledge_catalog=knowledge_catalog or knowledge_catalog_from_env()
        )
    return _DEFAULT_REFINERY_ENGINE
