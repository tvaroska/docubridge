"""Experimental-surface register (TEST-4).

Single source of truth for the experimental google-adk surfaces the Bridge deliberately
depends on. Pure metadata — imports no google.adk (so importing this never triggers the
SDK's experimental warnings or its heavy dependency tree) and no agents (isolation guard).
The paired test (tests/test_adk_experimental.py) verifies each surface is still experimental
in the installed SDK and that no first-party module uses an unregistered experimental surface.
"""
from __future__ import annotations

from dataclasses import dataclass

ADK_PIN = ("2.11.0", "3.0.0")  # [min_inclusive, max_exclusive) — mirrors pyproject "google-adk>=2.11.0,<3"


@dataclass(frozen=True)
class ExperimentalSurface:
    symbol: str            # fully-qualified import path, e.g. "google.adk.apps.app.ResumabilityConfig"
    decorator: str         # "experimental" | "a2a_experimental" — the token expected in getsource()
    pinned_config: dict[str, object]   # the mode/config the design pins, e.g. {"use_legacy": True}
    owner: str
    revisit_trigger: str
    requires: tuple[str, ...] = ()     # extra top-level imports the symbol needs (test importorskips these)
    introduced_in: str = ""            # phase/feature note

    @property
    def module(self) -> str:
        return self.symbol.rsplit(".", 1)[0]

    @property
    def attr(self) -> str:
        return self.symbol.rsplit(".", 1)[1]


EXPERIMENTAL_SURFACES: tuple[ExperimentalSurface, ...] = (
    ExperimentalSurface(
        symbol="google.adk.agents.remote_a2a_agent.RemoteA2aAgent",
        decorator="a2a_experimental",
        pinned_config={"use_legacy": True},
        owner="agents/ (A2A consumer)",
        revisit_trigger="re-audit on any google-adk minor bump; validate the new integration-extension "
                        "path against the mock before flipping use_legacy=False",
        requires=("a2a",),          # google-adk[a2a] extra (a2a-sdk) — arrives at P2; test skips until then
        introduced_in="P2/P3 (AGENT-2, WIRE-*)",
    ),
    ExperimentalSurface(
        symbol="google.adk.apps.app.ResumabilityConfig",
        decorator="experimental",
        pinned_config={"is_resumable": True},   # set on the App, not a bare Runner
        owner="bridge/ + agents/ (durable pause/resume)",
        revisit_trigger="re-audit on any google-adk minor bump; confirm still @experimental and still on App",
        requires=(),
        introduced_in="P4 (DUR-1..DUR-3)",
    ),
)


def registered_symbols() -> frozenset[str]:
    return frozenset(s.symbol for s in EXPERIMENTAL_SURFACES)


# Catalog of experimental google-adk symbols the project is AWARE of. The register above is the
# SANCTIONED subset (surfaces we've documented an owner/trigger for). The unregistered-use guard flags
# any first-party import that lands on a symbol in this catalog but NOT in registered_symbols().
# Seed it a little broader than the register so "we started using an experimental surface without
# cataloging it" actually bites. Keyed by fully-qualified symbol.
KNOWN_EXPERIMENTAL_ADK_SYMBOLS: frozenset[str] = frozenset({
    "google.adk.agents.remote_a2a_agent.RemoteA2aAgent",
    "google.adk.apps.app.ResumabilityConfig",
    "google.adk.apps._configs.ResumabilityConfig",   # private alias of the same class
    # A2A executor family is also @a2a_experimental; register before use if adopted:
    "google.adk.a2a.executor.a2a_agent_executor.A2aAgentExecutor",
})
