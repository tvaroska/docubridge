"""The mock Bridge contract double (WIRE-6) — a *maintained* A2A double.

**What this is (and is not).** This is NOT a throwaway stub. It is a first-class,
contract-checked Bridge double that serves the *same Agent Card* and the *same six
spec ops over the same wire* as the real edge (``bridge.edge.server.build_a2a_app``).
The mock→real swap is only the card URL. Its distinct value over the real P2 edge is
two things the real edge cannot yet produce, and which the P3 consumer loop is built
entirely around:

1. **Multi-turn / non-terminal turns.** The P1-4 ``Collector`` (which the real edge
   wraps) always returns ``ExchangeTurn(terminal=True)`` in one turn. The Address
   ``Workflow`` (P3-1) is a ``collect → gate → collect`` conditional cycle that only
   exercises if the collect node can come back *not done* — an ``input-required``
   park with outstanding requirements — loop, and later complete. The mock scripts
   those multi-turn sequences so P3 can build/test the loop, the gate, and
   park/resume **before** the durable park (P4) exists.
2. **A permanent, contract-checked double.** "Maintained, not throwaway" has a
   *mechanical* meaning: the mock constructs genuine ``contract.wire.ExchangeTurn``
   objects and encodes them through ``bridge.edge.wire.encode_exchange_turn`` — the
   **same** encode path as the real edge. Any drift in the ``ExchangeTurn`` /
   ``CollectionStatus`` contract breaks the mock at Pydantic-validation/encode time,
   so it can never silently diverge from the wire. That is the whole justification
   for reusing ``wire.py`` rather than hand-building parts.

**Decision 1 — same wire, executor-only swap.** Reuse ``card.build_agent_card`` and
the ``server`` route factories (``create_agent_card_routes`` + ``create_jsonrpc_routes``
over a ``DefaultRequestHandler`` + ``InMemoryTaskStore``). The ONLY substituted part
is the ``AgentExecutor``: ``ScriptedBridgeExecutor`` replaces ``CollectExecutor``.
The real edge's ``build_a2a_app`` is left byte-identical.

**Decision 7 — script-driven only.** The mock does NOT touch the extraction seam, the
artifact store, or the ``Collector``. It has no ``/artifacts`` fetch mount and never
calls ``save_artifact``; a scenario turn that carries an ``artifact`` uses a plain
``ArtifactRef(uri="mock://...")`` literal. That is the point: P3 can run the full
collect→gate→collect loop without standing up the Bridge core.

**Isolation guard.** This lives under ``bridge/`` and must NEVER import ``agents/``
(``tests/test_isolation_guard.py``). It imports only ``contract/``, ``bridge.*``,
``a2a.*``. The consumer reaches this mock over the wire by card URL, never by import.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from a2a.helpers import proto_helpers as ph
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskStore, TaskUpdater
from a2a.types import AgentCard, TaskState

if TYPE_CHECKING:
    import httpx
    from a2a.server.tasks import PushNotificationConfigStore, PushNotificationSender
from pydantic import BaseModel, ConfigDict
from starlette.applications import Starlette

from bridge.edge.card import DEFAULT_URL, build_agent_card
from bridge.edge.intake import decode_intake
from bridge.edge.pushguard import guarded_push_config_store
from bridge.edge.server import RPC_PATH
from bridge.edge.wire import encode_exchange_turn, status_message_for
from bridge.offscript import REASON_ESCALATED
from bridge.pathb import ESCALATE_MESSAGE
from contract.enums import Disposition, Gate, LifecycleStatus, RequirementStatus
from contract.wire import (
    ArtifactRef,
    CollectionStatus,
    CollectRequest,
    ExchangeTurn,
    LedgerEntry,
    Requirement,
    RequirementsList,
)

# Placeholder ``context_id`` on the scenario prototypes; re-stamped with the live
# ``context.context_id`` at emit time (ExchangeTurn.context_id is required/non-empty).
_PROTO_CTX = "mock-context"

# Marker the consumer/test supplies to pick a scenario (Decision 5): a
# ``scenario:<name>`` token in ``CollectRequest.message``. Selection is explicit and
# deterministic — never inferred from submissions.
_SCENARIO_MARKER = "scenario:"
DEFAULT_SCENARIO = "path-a-instant"


class Scenario(BaseModel):
    """An ordered list of ``ExchangeTurn`` prototypes mirroring corpus dispositions.

    Each step is a fully-built ``ExchangeTurn`` (so the registry validates via
    Pydantic at import time — the maintained-double contract check). At emit time
    the executor deep-copies the step and stamps the live ``context_id``.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    steps: list[ExchangeTurn]


def _turn(
    *,
    entries: list[LedgerEntry],
    outstanding: RequirementsList | None,
    terminal: bool,
    lifecycle: LifecycleStatus = LifecycleStatus.on_track,
) -> ExchangeTurn:
    return ExchangeTurn(
        context_id=_PROTO_CTX,
        status=CollectionStatus(entries=entries, outstanding=outstanding),
        terminal=terminal,
        lifecycle=lifecycle,
    )


# --------------------------------------------------------------------------- #
# Scenario registry (Decision 5) — corpus-mirroring literals.                  #
# Canonical issuer strings (``power-co``, ``aqua-util``) and doc ids come from #
# wiki/evals/address/expected.json + timeline.json so terminal outcomes match  #
# the real Bridge (parity = terminal reason + accepted-issuer set).            #
# --------------------------------------------------------------------------- #
SCENARIOS: dict[str, Scenario] = {
    # CUJ-1 fast path: instant accept of a clean gov-id, one terminal turn.
    "path-a-instant": Scenario(
        name="path-a-instant",
        steps=[
            _turn(
                entries=[
                    LedgerEntry(
                        doc_id="gov-id-clean",
                        doctype="gov-id",
                        disposition=Disposition.accepted,
                        gate=Gate.auto_approve,
                    )
                ],
                outstanding=RequirementsList(done=True),
                terminal=True,
            )
        ],
    ),
    # CUJ-10 distinct-issuer accept (mirrors timeline exchange-2-path-b-two-bills):
    # step 1 one accepted PowerCo bill + a park for a second distinct issuer;
    # step 2 (resume) two accepted entries across distinct issuers → terminal.
    "path-b-two-bills": Scenario(
        name="path-b-two-bills",
        steps=[
            _turn(
                entries=[
                    LedgerEntry(
                        doc_id="bill-powerco-clean",
                        doctype="utility-bill",
                        disposition=Disposition.accepted,
                        gate=Gate.auto_approve,
                        issuer="power-co",
                    )
                ],
                outstanding=RequirementsList(
                    requirements=[
                        Requirement(
                            item="second distinct issuer",
                            status=RequirementStatus.required,
                            doctype_hint="utility-bill",
                            message="need a utility bill from a different company",
                        )
                    ],
                    done=False,
                ),
                terminal=False,
            ),
            _turn(
                entries=[
                    LedgerEntry(
                        doc_id="bill-powerco-clean",
                        doctype="utility-bill",
                        disposition=Disposition.accepted,
                        gate=Gate.auto_approve,
                        issuer="power-co",
                    ),
                    LedgerEntry(
                        doc_id="bill-aquautil-clean",
                        doctype="utility-bill",
                        disposition=Disposition.accepted,
                        gate=Gate.auto_approve,
                        issuer="aqua-util",
                    ),
                ],
                outstanding=RequirementsList(done=True),
                terminal=True,
            ),
        ],
    ),
    # CUJ-4 resubmit (mirrors the blurry→clear AquaUtil corpus records):
    # step 1 a rejected blurry bill (resubmit gate) + a resubmit park;
    # step 2 (resume) a clean accepted bill → terminal.
    "resubmit": Scenario(
        name="resubmit",
        steps=[
            _turn(
                entries=[
                    LedgerEntry(
                        doc_id="bill-aquautil-blurry",
                        doctype="utility-bill",
                        disposition=Disposition.rejected,
                        gate=Gate.resubmit,
                        issuer="aqua-util",
                        reason_code="illegible",
                        message="bill is blurry; please resubmit a clearer copy",
                        artifact=ArtifactRef(uri="mock://bill-aquautil-blurry.svg"),
                    )
                ],
                outstanding=RequirementsList(
                    requirements=[
                        Requirement(
                            item="resubmit clear utility bill",
                            status=RequirementStatus.required,
                            doctype_hint="utility-bill",
                            reason_code="illegible",
                        )
                    ],
                    done=False,
                ),
                terminal=False,
            ),
            _turn(
                entries=[
                    LedgerEntry(
                        doc_id="bill-aquautil-clean",
                        doctype="utility-bill",
                        disposition=Disposition.accepted,
                        gate=Gate.auto_approve,
                        issuer="aqua-util",
                        artifact=ArtifactRef(uri="mock://bill-aquautil-clean.svg"),
                    )
                ],
                outstanding=RequirementsList(done=True),
                terminal=True,
            ),
        ],
    ),
    # CUJ-4 cap→escalate (S0-debt-1): the resubmission ladder crosses the ≤3 cap over
    # the A2A journey. Mirrors bridge.pathb §2b with max_resubmissions=3 — three blurry
    # resubmit parks (attempts 1,2,3 at the cap) then a terminal ESCALATE (attempts 4
    # exceeds the cap → route-to-human, disposition PENDING, NOT rejected §2a). This is
    # the A2A-wire counterpart to the unit-tested pathb escalate boundary: the executor
    # advances one step per send and only emits the escalate step after the three
    # resubmit parks, so the escalate turn is itself proof the cap was crossed (see the
    # test_golden.py::test_cuj4_resubmit_to_escalate_boundary_mock golden run).
    "resubmit-escalate": Scenario(
        name="resubmit-escalate",
        steps=[
            # attempts 1, 2, 3 — each a blurry bill that fails the quality gate and
            # parks for a fresh copy (still at/under the cap → RESUBMIT, non-terminal).
            *[
                _turn(
                    entries=[
                        LedgerEntry(
                            doc_id="bill-aquautil-blurry",
                            doctype="utility-bill",
                            disposition=Disposition.rejected,
                            gate=Gate.resubmit,
                            issuer="aqua-util",
                            reason_code="illegible",
                            message="bill is blurry; please resubmit a clearer copy",
                            artifact=ArtifactRef(uri="mock://bill-aquautil-blurry.svg"),
                        )
                    ],
                    outstanding=RequirementsList(
                        requirements=[
                            Requirement(
                                item="resubmit clear utility bill",
                                status=RequirementStatus.required,
                                doctype_hint="utility-bill",
                                reason_code="illegible",
                            )
                        ],
                        done=False,
                    ),
                    terminal=False,
                )
                for _ in range(3)
            ],
            # attempts 4 — cap exceeded. The document was never judged wrong; the Bridge
            # just could not read it, so it routes to a human: PENDING (not rejected),
            # escalated lifecycle, REASON_ESCALATED. Terminal for the automated leg.
            _turn(
                entries=[
                    LedgerEntry(
                        doc_id="bill-aquautil-blurry",
                        doctype="utility-bill",
                        disposition=Disposition.pending,
                        gate=Gate.resubmit,
                        issuer="aqua-util",
                        reason_code=REASON_ESCALATED,
                        message=ESCALATE_MESSAGE,
                        artifact=ArtifactRef(uri="mock://bill-aquautil-blurry.svg"),
                    )
                ],
                outstanding=RequirementsList(
                    requirements=[
                        Requirement(
                            item="human review of unreadable utility bill",
                            status=RequirementStatus.required,
                            doctype_hint="utility-bill",
                            reason_code=REASON_ESCALATED,
                        )
                    ],
                    done=False,
                ),
                terminal=True,
                lifecycle=LifecycleStatus.escalated,
            ),
        ],
    ),
    # P4-5 escalated stall: a non-terminal turn with escalated lifecycle status
    # (DUR-6 / "only app cancel terminates"). The task holds at escalated with
    # an outstanding requirement; only tasks/cancel transitions it to terminal.
    "escalated-stall": Scenario(
        name="escalated-stall",
        steps=[
            _turn(
                entries=[
                    LedgerEntry(
                        doc_id="bill-powerco-clean",
                        doctype="utility-bill",
                        disposition=Disposition.accepted,
                        gate=Gate.auto_approve,
                        issuer="power-co",
                    )
                ],
                outstanding=RequirementsList(
                    requirements=[
                        Requirement(
                            item="second distinct issuer",
                            status=RequirementStatus.required,
                            doctype_hint="utility-bill",
                            message="escalated: need a utility bill from a different company",
                        )
                    ],
                    done=False,
                ),
                terminal=False,
                lifecycle=LifecycleStatus.escalated,
            ),
        ],
    ),
}


def select_scenario(
    request: CollectRequest, scenarios: dict[str, Scenario]
) -> Scenario:
    """Resolve a scenario from an explicit ``scenario:<name>`` marker (Decision 5).

    Reads a ``scenario:<name>`` token from ``request.message``; falls back to
    ``DEFAULT_SCENARIO`` when absent. Raises ``ValueError`` on an unknown explicit
    marker (fail loud, not silent) so a typo can never quietly pick the default.
    """
    marker: str | None = None
    for token in (request.message or "").split():
        if token.startswith(_SCENARIO_MARKER):
            marker = token[len(_SCENARIO_MARKER) :]
            break
    if marker is None or marker == "":
        return scenarios[DEFAULT_SCENARIO]
    if marker not in scenarios:
        raise ValueError(
            f"unknown scenario marker {marker!r}; known scenarios: {sorted(scenarios)}"
        )
    return scenarios[marker]


class ScriptedBridgeExecutor(AgentExecutor):
    """A2A executor that emits scripted ``ExchangeTurn``s (WIRE-6).

    Stateful across ``message/send`` calls that share an exchange: step state is
    keyed by ``context_id`` and held in-memory (Decision 4). No ``Collector``,
    extraction, or artifact persistence (Decision 7).
    """

    def __init__(self, scenarios: dict[str, Scenario] | None = None) -> None:
        self._scenarios = scenarios if scenarios is not None else SCENARIOS
        # context_id -> next step index.
        self._steps: dict[str, int] = {}
        # context_id -> pinned scenario name (resolved on the first turn; a resume
        # turn may omit the marker but must continue the same scenario).
        self._scenario_for_ctx: dict[str, str] = {}

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        # Reuse the edge's dual-mode intake so the mock accepts exactly what the
        # real edge accepts (JSON CollectRequest or free text). Submissions are
        # intentionally ignored — scenarios are script-driven (Decision 5).
        """Execute a scripted mock A2A bridge turn for deterministic conformance testing."""
        request = decode_intake(context.message)
        context_id = context.context_id

        # Pin the scenario per context on the first turn; continue it on resume.
        pinned = self._scenario_for_ctx.get(context_id)
        if pinned is None:
            scenario = select_scenario(request, self._scenarios)
            self._scenario_for_ctx[context_id] = scenario.name
        else:
            scenario = self._scenarios[pinned]

        idx = self._steps.get(context_id, 0)
        # Clamp so an over-run just repeats the terminal turn.
        step = scenario.steps[min(idx, len(scenario.steps) - 1)]
        self._steps[context_id] = idx + 1

        # Build the concrete turn for this step (stamp the live context_id).
        turn = step.model_copy(deep=True)
        turn.context_id = context_id

        # 1.x requires a brand-new task's first enqueued event to be a Task, else
        # the server raises InvalidAgentResponseError (Decision 3).
        if context.current_task is None:
            await event_queue.enqueue_event(
                ph.new_task(
                    context.task_id,
                    context.context_id,
                    TaskState.TASK_STATE_SUBMITTED,
                    history=[context.message] if context.message is not None else [],
                )
            )

        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        # last_chunk=True matters (D4): a *partial* artifact event streams but is
        # never persisted, so a downstream gate reading the session can't see it.
        await updater.add_artifact(
            encode_exchange_turn(turn),
            name="exchange_turn",
            last_chunk=True,
        )

        # Non-empty status.message on every status update (D3): an empty proto
        # Message is silently dropped by ADK's a2a converter.
        status_message = status_message_for(turn)
        if turn.terminal:
            await updater.complete(message=status_message)
        else:
            # Non-terminal park: the resumable INPUT_REQUIRED signal the P3 loop
            # consumes. The task stays non-terminal; the consumer resumes by
            # sending another message on the same context_id (+ task_id).
            await updater.update_status(
                TaskState.TASK_STATE_INPUT_REQUIRED, message=status_message
            )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        # P4-5: tasks/cancel is the app's explicit terminal close (DUR-6) —
        # transitions any non-terminal task (WORKING/INPUT_REQUIRED, including an
        # escalated stall) to CANCELED; an already-terminal task is not cancelable
        # (the SDK handler enforces both). Mirror CollectExecutor.cancel.
        """Cancel an active scripted mock bridge execution."""
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        await updater.cancel()


def build_mock_bridge_app(
    *,
    url: str = DEFAULT_URL,
    executor: ScriptedBridgeExecutor | None = None,
    task_store: TaskStore | None = None,
    agent_card: AgentCard | None = None,
    push_config_store: PushNotificationConfigStore | None = None,
    push_sender: PushNotificationSender | None = None,
    push_httpx_client: httpx.AsyncClient | None = None,
) -> Starlette:
    """Build the mock Bridge A2A Starlette app (same wire, scripted executor).

    Same route wiring as ``server.build_a2a_app`` but with the scripted executor
    and **no** ``/artifacts`` fetch mount (the mock has no artifact store —
    Decision 7). Reuses ``build_agent_card(url)`` so the card is identical to the
    real edge's (mock→real swap is only the URL — Decision 1).

    **P4-4:** Accepts push_config_store / push_sender / push_httpx_client for
    webhook emission tests (mirrors ``server.build_a2a_app``).
    """
    from bridge.seams import Seam, get_adapter, resolve_mode

    card = agent_card or build_agent_card(url)
    active_executor = executor or ScriptedBridgeExecutor()
    active_task_store = task_store or InMemoryTaskStore()

    # P4-4: Build push config store and sender (same logic as server.py)
    if push_config_store is None and push_sender is None:
        # Build via seam (though for mock, in-memory is most common)
        store_seam = get_adapter(Seam.task_store, resolve_mode(Seam.task_store))
        active_push_config_store = store_seam.build_push_config_store()
    else:
        active_push_config_store = push_config_store

    # S0-sec-2: same webhook SSRF guard as the real edge, so the mock double
    # exercises the guarded push path (parity with build_a2a_app).
    if active_push_config_store is not None:
        active_push_config_store = guarded_push_config_store(active_push_config_store)

    if push_sender is None and active_push_config_store is not None:
        from a2a.server.tasks import BasePushNotificationSender

        client = push_httpx_client
        if client is None:
            import httpx

            client = httpx.AsyncClient()
        active_push_sender = BasePushNotificationSender(
            httpx_client=client, config_store=active_push_config_store
        )
    else:
        active_push_sender = push_sender

    handler = DefaultRequestHandler(
        agent_executor=active_executor,
        task_store=active_task_store,
        agent_card=card,
        push_config_store=active_push_config_store,
        push_sender=active_push_sender,
    )
    routes = create_agent_card_routes(card) + create_jsonrpc_routes(handler, RPC_PATH)
    return Starlette(routes=routes)


def serve_mock(host: str = "127.0.0.1", port: int = 8001) -> None:  # pragma: no cover
    """Run the mock with uvicorn so P3 can point a card URL at a running mock.

    Uses a different default port than the real edge's 8000 so both can run.
    """
    import uvicorn

    url = f"http://{host}:{port}/"
    uvicorn.run(build_mock_bridge_app(url=url), host=host, port=port)


if __name__ == "__main__":  # pragma: no cover
    serve_mock()
