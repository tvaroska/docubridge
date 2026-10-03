"""A2A server assembly for the simulated carrier agents (S1-benefits-6).

Re-implements the minimal a2a-sdk 1.1.x server assembly (Agent Card routes +
JSON-RPC routes over a ``DefaultRequestHandler`` + ``InMemoryTaskStore`` on a
``Starlette`` app) that ``bridge/edge/server.py`` shows — but **self-contained** in
the external ``agents/`` zone, because ``agents/`` must never import ``bridge/`` (the
reverse isolation guard). The carrier apps are genuine external-zone A2A servers the
Bridge (or the a2a-sdk conformant client) dials over the wire.

Loopback serve default per DECISIONS S0-sec-3 (``serve_carrier`` binds 127.0.0.1).
"""

from __future__ import annotations

from a2a.server.agent_execution import AgentExecutor
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskStore
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
)
from a2a.utils.constants import PROTOCOL_VERSION_1_0, TransportProtocol
from starlette.applications import Starlette

# The JSON-RPC endpoint path (the card's interface url points here).
RPC_PATH = "/"

# The one skill both carriers advertise (mirrors the benefits-quote doctype label).
CARRIER_SKILL_ID = "benefits-quote"


def build_carrier_card(
    url: str,
    *,
    party: str,
    process: str = "benefits-procurement",
) -> AgentCard:
    """Build a carrier's Agent Card advertising the ``benefits-quote`` skill.

    ``streaming=True``; one JSON-RPC ``AgentInterface`` at ``url``; one
    ``AgentSkill(id="benefits-quote", …)``; dual input modes
    (``application/json`` + ``text/plain``) so a text quote-request or a structured
    ``CollectRequest`` both reach the carrier.
    """
    return AgentCard(
        name=f"Benefits Carrier ({party})",
        description=(
            f"Simulated benefits carrier {party!r} for the {process!r} program: "
            "send a quote request, receive a benefits-quote extraction inside a "
            "standard A2A task artifact. Revise pushback returns a revised quote."
        ),
        version="0.1.0",
        supported_interfaces=[
            AgentInterface(
                url=url,
                protocol_binding=TransportProtocol.JSONRPC.value,
                protocol_version=PROTOCOL_VERSION_1_0,
            )
        ],
        capabilities=AgentCapabilities(streaming=True),
        default_input_modes=["application/json", "text/plain"],
        default_output_modes=["application/json"],
        skills=[
            AgentSkill(
                id=CARRIER_SKILL_ID,
                name="Benefits Quote",
                description=(
                    f"Return {party!r}'s benefits quote for the program, and a "
                    "revised quote on pushback."
                ),
                tags=[f"process:{process}", f"party:{party}"],
                input_modes=["application/json", "text/plain"],
            )
        ],
    )


def build_carrier_app(
    url: str,
    executor: AgentExecutor,
    card: AgentCard,
    task_store: TaskStore | None = None,
) -> Starlette:
    """Assemble a carrier's A2A Starlette app (the minimal 1.1.x assembly).

    Same route wiring as the Bridge edge, but with the supplied carrier executor and
    no ``/artifacts`` mount (carriers serve quotes, not documents — bytes never travel
    on the wire). ``task_store`` is injectable (mirrors ``bridge/edge/server.py``): the
    serving surface owns durability, so a deployed carrier can swap in a durable store;
    the default ``InMemoryTaskStore`` is sufficient for the in-process demo/interop.
    """
    handler = DefaultRequestHandler(
        agent_executor=executor,
        task_store=task_store or InMemoryTaskStore(),
        agent_card=card,
    )
    routes = create_agent_card_routes(card) + create_jsonrpc_routes(handler, RPC_PATH)
    return Starlette(routes=routes)


def serve_carrier(
    app: Starlette, host: str = "127.0.0.1", port: int = 8100
) -> None:  # pragma: no cover
    """Run a carrier app with uvicorn for a real deployed demo (loopback default)."""
    import uvicorn

    uvicorn.run(app, host=host, port=port)
