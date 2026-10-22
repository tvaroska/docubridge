"""Durable SQL-backed Agent Gym store (AlloyDB / Cloud SQL Postgres / SQLite)."""

from __future__ import annotations

import asyncio
import json
import os
import threading
from collections.abc import Coroutine
from typing import Any

from bridge.agent_gym.engine import AgentGymEngine, InMemoryAgentGymEngine
from bridge.agent_gym.models import (
    _OP_TIMEOUT_S,
    DEFAULT_TRUST_POLICIES,
    PROCESS_SKILL_LINEAGE_TABLE_NAME,
    REGISTRY_BINDINGS_TABLE_NAME,
    REGISTRY_SERVICES_TABLE_NAME,
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
        self._runtime: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._engine: Any = None
        self._closed = False
        self._signals_table: Any = None
        self._rules_table: Any = None
        self._reports_table: Any = None
        self._trust_table: Any = None
        self._shadow_table: Any = None
        self._skill_revisions_table: Any = None
        self._process_skill_lineage_table: Any = None
        self._registry_services_table: Any = None
        self._registry_bindings_table: Any = None

    def _ensure_ready(self) -> None:
        if self._engine is not None:
            return
        with self._init_lock:
            if self._closed:
                raise RuntimeError("the agent gym store is closed")
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

            from bridge.sql_runtime import acquire_dsn_runtime

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
            self._registry_services_table = Table(
                REGISTRY_SERVICES_TABLE_NAME,
                meta,
                Column("service_id", String(128), primary_key=True),
                Column("component_type", String(64), nullable=False, index=True),
                Column("payload_json", Text, nullable=False),
            )
            self._registry_bindings_table = Table(
                REGISTRY_BINDINGS_TABLE_NAME,
                meta,
                Column("binding_id", String(128), primary_key=True),
                Column("source_agent", String(256), nullable=False, index=True),
                Column("payload_json", Text, nullable=False),
            )

            trust_table = self._trust_table
            seed_policies = self._initial_trust_policies

            async def _create_and_seed(engine: Any) -> None:
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

            runtime = acquire_dsn_runtime(
                self.dsn,
                init_coro_factory=_create_and_seed,
                thread_name="bridge-agent-gym-store",
                timeout_s=_OP_TIMEOUT_S,
            )
            self._runtime = runtime
            self._loop = runtime.loop
            self._thread = runtime.thread
            self._engine = runtime.engine

    def close(self) -> None:
        """Release the underlying per-DSN SQLAlchemy runtime lease."""
        with self._init_lock:
            if self._closed:
                return
            self._closed = True
            runtime = self._runtime
            self._runtime = self._loop = self._thread = self._engine = None
        if runtime is None:
            return
        from bridge.sql_runtime import release_dsn_runtime

        release_dsn_runtime(self.dsn, runtime, timeout_s=_OP_TIMEOUT_S)
        _SQL_GYM_CACHE.pop(self.dsn, None)

    def _run(self, coro: Coroutine[Any, Any, Any]) -> Any:
        self._ensure_ready()
        assert self._runtime is not None
        return self._runtime.run(coro, timeout_s=_OP_TIMEOUT_S)

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

    def _save_registry_service(self, entry: dict[str, Any]) -> None:
        self._ensure_ready()
        table = self._registry_services_table
        engine = self._engine
        svc_id = str(entry["service_id"])
        comp_type = str(entry["component_type"])
        payload_str = json.dumps(entry, sort_keys=True)

        async def _op() -> None:
            from sqlalchemy import delete

            async with engine.begin() as conn:
                await conn.execute(delete(table).where(table.c.service_id == svc_id))
                await conn.execute(
                    table.insert().values(
                        service_id=svc_id,
                        component_type=comp_type,
                        payload_json=payload_str,
                    )
                )

        self._run(_op())

    def _list_registry_services(self) -> list[dict[str, Any]]:
        self._ensure_ready()
        table = self._registry_services_table
        engine = self._engine

        async def _op() -> list[dict[str, Any]]:
            from sqlalchemy import select

            async with engine.connect() as conn:
                res = await conn.execute(
                    select(table.c.payload_json).order_by(table.c.service_id.asc())
                )
                return [json.loads(row[0]) for row in res]

        return self._run(_op())

    def _save_registry_binding(self, binding: dict[str, Any]) -> None:
        self._ensure_ready()
        table = self._registry_bindings_table
        engine = self._engine
        bind_id = str(binding["binding_id"])
        src_agent = str(binding["source_agent"])
        payload_str = json.dumps(binding, sort_keys=True)

        async def _op() -> None:
            from sqlalchemy import delete

            async with engine.begin() as conn:
                await conn.execute(delete(table).where(table.c.binding_id == bind_id))
                await conn.execute(
                    table.insert().values(
                        binding_id=bind_id,
                        source_agent=src_agent,
                        payload_json=payload_str,
                    )
                )

        self._run(_op())

    def _list_registry_bindings(self) -> list[dict[str, Any]]:
        self._ensure_ready()
        table = self._registry_bindings_table
        engine = self._engine

        async def _op() -> list[dict[str, Any]]:
            from sqlalchemy import select

            async with engine.connect() as conn:
                res = await conn.execute(
                    select(table.c.payload_json).order_by(table.c.binding_id.asc())
                )
                return [json.loads(row[0]) for row in res]

        return self._run(_op())

    def _clear_rules_and_signals(self) -> None:
        self._ensure_ready()
        signals_table = self._signals_table
        rules_table = self._rules_table
        reports_table = self._reports_table
        shadow_table = self._shadow_table
        skill_revisions_table = self._skill_revisions_table
        process_skill_lineage_table = self._process_skill_lineage_table
        registry_services_table = self._registry_services_table
        registry_bindings_table = self._registry_bindings_table
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
                await conn.execute(delete(registry_services_table))
                await conn.execute(delete(registry_bindings_table))

        self._run(_op())


_SQL_GYM_CACHE: dict[str, SqlAgentGymEngine] = {}
_SQL_GYM_LOCK = threading.Lock()
_DEFAULT_GYM_ENGINE: InMemoryAgentGymEngine | None = None


def agent_gym_from_env(
    existing: AgentGymEngine | None = None,
) -> AgentGymEngine:
    """Resolve the Agent Gym engine via the ``Port.agent_gym`` port adapter."""
    if existing is not None:
        return existing
    from bridge.ports import Port, PortMode, get_adapter, resolve_mode

    mode = resolve_mode(Port.agent_gym)
    if (
        mode is PortMode.local
        and os.environ.get("ADP_AGENT_RUNTIME", "").strip().lower() == "gcp"
    ):
        mode = PortMode.gcp
    adapter = get_adapter(Port.agent_gym, mode)
    return adapter.build_agent_gym()
