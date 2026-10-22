"""Durable SQL-backed Agent Gym store (AlloyDB / Cloud SQL Postgres / SQLite)."""

from __future__ import annotations

import asyncio
import os
import threading
from collections.abc import Coroutine
from typing import Any

from bridge.agent_gym.engine import AgentGymEngine, InMemoryAgentGymEngine
from bridge.agent_gym.models import (
    _OP_TIMEOUT_S,
    DATABASE_URL_ENV,
    DEFAULT_TRUST_POLICIES,
    GYM_DB_ENV,
    OPERATIONAL_DB_ENV,
    PROCESS_SKILL_LINEAGE_TABLE_NAME,
    REPORTS_TABLE_NAME,
    RULES_TABLE_NAME,
    SHADOW_RECORDS_TABLE_NAME,
    SIGNALS_TABLE_NAME,
    SKILL_REVISIONS_TABLE_NAME,
    TRUST_POLICIES_TABLE_NAME,
    ExpertFeedbackSignal,
    GymReportCard,
    ProcessSkillLineageRecord,
    ProgrammableTrustPolicy,
    ShadowComparisonRecord,
    SkillRevisionRecord,
    TribalKnowledgeRule,
)


class SqlAgentGymEngine(AgentGymEngine):
    """AlloyDB / Cloud SQL Postgres / SQLite durable Agent Gym engine.

    Persists signals, Tribal Knowledge Rules, Quality Report Cards, Programmable
    Trust policies, and Shadow Mode comparisons in shared SQL tables so that
    Cloud Run UI/API replicas and Vertex AI Agent Engine (`reasoningEngines`)
    workers share a single consistent source of truth across processes and restarts.
    """

    def __init__(
        self,
        dsn: str,
        *,
        initial_trust_policies: tuple[
            ProgrammableTrustPolicy, ...
        ] = DEFAULT_TRUST_POLICIES,
    ) -> None:
        cleaned = (dsn or "").strip()
        if not cleaned:
            raise ValueError("SqlAgentGymEngine requires a non-empty database DSN")
        if ":memory:" in cleaned or "mode=memory" in cleaned:
            raise ValueError(
                "SqlAgentGymEngine refuses an in-memory SQLite DSN; use "
                "InMemoryAgentGymEngine or a file/Postgres DSN"
            )
        self.dsn = cleaned
        self._initial_trust_policies = initial_trust_policies
        self._init_lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._engine: Any = None
        self._signals_table: Any = None
        self._rules_table: Any = None
        self._reports_table: Any = None
        self._trust_table: Any = None
        self._shadow_table: Any = None
        self._skill_revisions_table: Any = None
        self._process_skill_lineage_table: Any = None

    def _ensure_ready(self) -> None:
        if self._engine is not None:
            return
        with self._init_lock:
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
            from sqlalchemy.ext.asyncio import create_async_engine

            meta = MetaData()
            self._signals_table = Table(
                SIGNALS_TABLE_NAME,
                meta,
                Column("signal_id", String(64), primary_key=True),
                Column("entity_id", String(128), nullable=False, index=True),
                Column("payload_json", Text, nullable=False),
            )
            self._rules_table = Table(
                RULES_TABLE_NAME,
                meta,
                Column("rule_id", String(64), primary_key=True),
                Column("category", String(64), nullable=False, index=True),
                Column("status", String(64), nullable=False, index=True),
                Column("issuer_pattern", String(128), nullable=False, index=True),
                Column("doctype", String(128), nullable=False, index=True),
                Column("payload_json", Text, nullable=False),
            )
            self._reports_table = Table(
                REPORTS_TABLE_NAME,
                meta,
                Column("report_id", String(64), primary_key=True),
                Column("rule_id", String(64), nullable=False, index=True),
                Column("payload_json", Text, nullable=False),
            )
            self._trust_table = Table(
                TRUST_POLICIES_TABLE_NAME,
                meta,
                Column("slice_id", String(128), primary_key=True),
                Column("autonomy_level", String(64), nullable=False, index=True),
                Column("payload_json", Text, nullable=False),
            )
            self._shadow_table = Table(
                SHADOW_RECORDS_TABLE_NAME,
                meta,
                Column("seq", Integer, primary_key=True, autoincrement=True),
                Column("comparison_id", String(64), unique=True, nullable=False),
                Column("slice_id", String(128), nullable=False, index=True),
                Column("payload_json", Text, nullable=False),
            )
            self._skill_revisions_table = Table(
                SKILL_REVISIONS_TABLE_NAME,
                meta,
                Column("revision_id", String(128), primary_key=True),
                Column("skill_name", String(128), nullable=False, index=True),
                Column("payload_json", Text, nullable=False),
            )
            self._process_skill_lineage_table = Table(
                PROCESS_SKILL_LINEAGE_TABLE_NAME,
                meta,
                Column("seq", Integer, primary_key=True, autoincrement=True),
                Column("lineage_id", String(64), unique=True, nullable=False),
                Column("context_id", String(128), nullable=False, index=True),
                Column("entity_id", String(128), nullable=False, index=True),
                Column("payload_json", Text, nullable=False),
            )

            loop = asyncio.new_event_loop()
            thread = threading.Thread(
                target=loop.run_forever,
                name="bridge-agent-gym-store",
                daemon=True,
            )
            thread.start()
            engine = create_async_engine(self.dsn)
            trust_table = self._trust_table
            seed_policies = self._initial_trust_policies

            async def _create_and_seed() -> None:

                async with engine.begin() as conn:
                    await conn.run_sync(meta.create_all)
                    existing_res = await conn.execute(
                        select(trust_table.c.slice_id, trust_table.c.payload_json)
                    )
                    existing_rows = {r[0]: r[1] for r in existing_res}
                    for p in seed_policies:
                        raw_json = existing_rows.get(p.slice_id)
                        if raw_json is None:
                            await conn.execute(
                                trust_table.insert().values(
                                    slice_id=p.slice_id,
                                    autonomy_level=p.autonomy_level.value,
                                    payload_json=p.model_dump_json(),
                                )
                            )

            fut = asyncio.run_coroutine_threadsafe(_create_and_seed(), loop)
            fut.result(timeout=_OP_TIMEOUT_S)
            self._loop = loop
            self._thread = thread
            self._engine = engine

    def _run(self, coro: Coroutine[Any, Any, Any]) -> Any:
        self._ensure_ready()
        assert self._loop is not None
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return fut.result(timeout=_OP_TIMEOUT_S)

    def _save_signal(self, signal: ExpertFeedbackSignal) -> None:
        self._ensure_ready()
        table = self._signals_table
        engine = self._engine

        async def _op() -> None:
            from sqlalchemy import delete

            async with engine.begin() as conn:
                await conn.execute(
                    delete(table).where(table.c.signal_id == signal.signal_id)
                )
                await conn.execute(
                    table.insert().values(
                        signal_id=signal.signal_id,
                        entity_id=signal.entity_id,
                        payload_json=signal.model_dump_json(),
                    )
                )

        self._run(_op())

    def _get_signal(self, signal_id: str) -> ExpertFeedbackSignal | None:
        self._ensure_ready()
        table = self._signals_table
        engine = self._engine

        async def _op() -> ExpertFeedbackSignal | None:
            from sqlalchemy import select

            async with engine.connect() as conn:
                res = await conn.execute(
                    select(table.c.payload_json).where(table.c.signal_id == signal_id)
                )
                row = res.first()
                if row is None:
                    return None
                return ExpertFeedbackSignal.model_validate_json(row[0])

        return self._run(_op())

    def _save_rule(self, rule: TribalKnowledgeRule) -> None:
        self._ensure_ready()
        table = self._rules_table
        engine = self._engine

        async def _op() -> None:
            from sqlalchemy import delete

            async with engine.begin() as conn:
                await conn.execute(delete(table).where(table.c.rule_id == rule.rule_id))
                await conn.execute(
                    table.insert().values(
                        rule_id=rule.rule_id,
                        category=rule.category.value,
                        status=rule.status.value,
                        issuer_pattern=rule.issuer_pattern,
                        doctype=rule.doctype,
                        payload_json=rule.model_dump_json(),
                    )
                )

        self._run(_op())

    def _get_rule(self, rule_id: str) -> TribalKnowledgeRule | None:
        self._ensure_ready()
        table = self._rules_table
        engine = self._engine

        async def _op() -> TribalKnowledgeRule | None:
            from sqlalchemy import select

            async with engine.connect() as conn:
                res = await conn.execute(
                    select(table.c.payload_json).where(table.c.rule_id == rule_id)
                )
                row = res.first()
                if row is None:
                    return None
                return TribalKnowledgeRule.model_validate_json(row[0])

        return self._run(_op())

    def _list_all_rules(self) -> list[TribalKnowledgeRule]:
        self._ensure_ready()
        table = self._rules_table
        engine = self._engine

        async def _op() -> list[TribalKnowledgeRule]:
            from sqlalchemy import select

            async with engine.connect() as conn:
                res = await conn.execute(
                    select(table.c.payload_json).order_by(table.c.rule_id.asc())
                )
                return [TribalKnowledgeRule.model_validate_json(row[0]) for row in res]

        return self._run(_op())

    def _save_report(self, report: GymReportCard) -> None:
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
                        rule_id=report.rule_id,
                        payload_json=report.model_dump_json(),
                    )
                )

        self._run(_op())

    def _save_trust_policy(self, policy: ProgrammableTrustPolicy) -> None:
        self._ensure_ready()
        table = self._trust_table
        engine = self._engine

        async def _op() -> None:
            from sqlalchemy import delete

            async with engine.begin() as conn:
                await conn.execute(
                    delete(table).where(table.c.slice_id == policy.slice_id)
                )
                await conn.execute(
                    table.insert().values(
                        slice_id=policy.slice_id,
                        autonomy_level=policy.autonomy_level.value,
                        payload_json=policy.model_dump_json(),
                    )
                )

        self._run(_op())

    def _get_trust_policy(self, slice_id: str) -> ProgrammableTrustPolicy | None:
        self._ensure_ready()
        table = self._trust_table
        engine = self._engine

        async def _op() -> ProgrammableTrustPolicy | None:
            from sqlalchemy import select

            async with engine.connect() as conn:
                res = await conn.execute(
                    select(table.c.payload_json).where(table.c.slice_id == slice_id)
                )
                row = res.first()
                if row is None:
                    return None
                return ProgrammableTrustPolicy.model_validate_json(row[0])

        return self._run(_op())

    def _list_all_trust_policies(self) -> list[ProgrammableTrustPolicy]:
        self._ensure_ready()
        table = self._trust_table
        engine = self._engine

        async def _op() -> list[ProgrammableTrustPolicy]:
            from sqlalchemy import select

            async with engine.connect() as conn:
                res = await conn.execute(
                    select(table.c.payload_json).order_by(table.c.slice_id.asc())
                )
                return [
                    ProgrammableTrustPolicy.model_validate_json(row[0]) for row in res
                ]

        return self._run(_op())

    def _append_shadow_record(self, record: ShadowComparisonRecord) -> None:
        self._ensure_ready()
        table = self._shadow_table
        engine = self._engine

        async def _op() -> None:
            async with engine.begin() as conn:
                await conn.execute(
                    table.insert().values(
                        comparison_id=record.comparison_id,
                        slice_id=record.slice_id,
                        payload_json=record.model_dump_json(),
                    )
                )

        self._run(_op())

    def _save_skill_revision(self, revision: SkillRevisionRecord) -> None:
        self._ensure_ready()
        table = self._skill_revisions_table
        engine = self._engine

        async def _op() -> None:
            from sqlalchemy import delete

            async with engine.begin() as conn:
                await conn.execute(
                    delete(table).where(table.c.revision_id == revision.revision_id)
                )
                await conn.execute(
                    table.insert().values(
                        revision_id=revision.revision_id,
                        skill_name=revision.skill_name,
                        payload_json=revision.model_dump_json(),
                    )
                )

        self._run(_op())

    def _list_all_skill_revisions(self) -> list[SkillRevisionRecord]:
        self._ensure_ready()
        table = self._skill_revisions_table
        engine = self._engine

        async def _op() -> list[SkillRevisionRecord]:
            from sqlalchemy import select

            async with engine.connect() as conn:
                res = await conn.execute(
                    select(table.c.payload_json).order_by(table.c.revision_id.asc())
                )
                return [SkillRevisionRecord.model_validate_json(row[0]) for row in res]

        return self._run(_op())

    def _append_process_skill_lineage(self, record: ProcessSkillLineageRecord) -> None:
        self._ensure_ready()
        table = self._process_skill_lineage_table
        engine = self._engine

        async def _op() -> None:
            async with engine.begin() as conn:
                await conn.execute(
                    table.insert().values(
                        lineage_id=record.lineage_id,
                        context_id=record.context_id,
                        entity_id=record.entity_id,
                        payload_json=record.model_dump_json(),
                    )
                )

        self._run(_op())

    def _list_all_process_skill_lineage(self) -> list[ProcessSkillLineageRecord]:
        self._ensure_ready()
        table = self._process_skill_lineage_table
        engine = self._engine

        async def _op() -> list[ProcessSkillLineageRecord]:
            from sqlalchemy import select

            async with engine.connect() as conn:
                res = await conn.execute(
                    select(table.c.payload_json).order_by(table.c.seq.asc())
                )
                return [
                    ProcessSkillLineageRecord.model_validate_json(row[0]) for row in res
                ]

        return self._run(_op())

    def _clear_rules_and_signals(self) -> None:
        self._ensure_ready()
        signals_table = self._signals_table
        rules_table = self._rules_table
        reports_table = self._reports_table
        shadow_table = self._shadow_table
        skill_revisions_table = self._skill_revisions_table
        process_skill_lineage_table = self._process_skill_lineage_table
        engine = self._engine

        async def _op() -> None:
            from sqlalchemy import delete

            async with engine.begin() as conn:
                await conn.execute(delete(reports_table))
                await conn.execute(delete(rules_table))
                await conn.execute(delete(signals_table))
                await conn.execute(delete(shadow_table))
                await conn.execute(delete(skill_revisions_table))
                await conn.execute(delete(process_skill_lineage_table))

        self._run(_op())

    def close(self) -> None:
        """Dispose the async SQLAlchemy engine and stop the background event loop thread."""
        with self._init_lock:
            if self._engine is None:
                return
            engine, loop, thread = self._engine, self._loop, self._thread
            self._engine = None
            self._loop = None
            self._thread = None
        if loop is not None:
            fut = asyncio.run_coroutine_threadsafe(engine.dispose(), loop)
            fut.result(timeout=_OP_TIMEOUT_S)
            loop.call_soon_threadsafe(loop.stop)
        if thread is not None:
            thread.join(timeout=_OP_TIMEOUT_S)
        _SQL_GYM_CACHE.pop(self.dsn, None)


_SQL_GYM_CACHE: dict[str, SqlAgentGymEngine] = {}
_SQL_GYM_LOCK = threading.Lock()
_DEFAULT_GYM_ENGINE: InMemoryAgentGymEngine | None = None


def agent_gym_from_env(
    existing: AgentGymEngine | None = None,
) -> AgentGymEngine:
    """Resolve the Agent Gym engine from environment.

    - When `BRIDGE_GYM_DB_URL`, `BRIDGE_OPERATIONAL_DB_URL`, or `DATABASE_URL` is set,
      returns a durable :class:`SqlAgentGymEngine` shared across Cloud Run replicas
      and Vertex AI Agent Engine (`reasoningEngines`) workers.
    - When `BRIDGE_SEAM_MODE=gcp` (or `ADP_AGENT_RUNTIME=gcp`) and no database DSN is
      configured, raises `RuntimeError` refusing an in-memory fallback (preventing the
      session-affinity trap).
    - Otherwise returns a local :class:`InMemoryAgentGymEngine` for unit tests.
    """
    global _DEFAULT_GYM_ENGINE
    if existing is not None:
        return existing
    dsn = (
        os.environ.get(GYM_DB_ENV, "").strip()
        or os.environ.get(OPERATIONAL_DB_ENV, "").strip()
        or os.environ.get(DATABASE_URL_ENV, "").strip()
    )
    if dsn:
        with _SQL_GYM_LOCK:
            cached = _SQL_GYM_CACHE.get(dsn)
            if cached is None:
                cached = SqlAgentGymEngine(dsn)
                _SQL_GYM_CACHE[dsn] = cached
        return cached
    seam_mode = os.environ.get("BRIDGE_SEAM_MODE", "").strip().lower()
    agent_runtime = os.environ.get("ADP_AGENT_RUNTIME", "").strip().lower()
    if seam_mode == "gcp" or agent_runtime == "gcp":
        raise RuntimeError(
            "AgentGym in GCP / Vertex AI Agent Engine mode requires a durable SQL DSN "
            "(DATABASE_URL, BRIDGE_OPERATIONAL_DB_URL, or BRIDGE_GYM_DB_URL); "
            "refusing an in-memory fallback (the session-affinity trap)."
        )
    if _DEFAULT_GYM_ENGINE is None:
        _DEFAULT_GYM_ENGINE = InMemoryAgentGymEngine()
    return _DEFAULT_GYM_ENGINE
