"""Dynamic Agent Card for the Bridge A2A edge (WIRE-4, WIRE-5).

**Scope:** The Agent Card is **generated from skill metadata** (``loaded_skills()``),
one ``AgentSkill`` per **process** skill, built from the skill's ``name``/``description``.
Adding a skill folder regenerates the card with no code change. The metadata source
(``bridge.edge.skills.loaded_skills``) is the *resolved* live ``skill_registry`` view: a
malformed folder is already skipped, a process's candidates are narrowed to the installed
doctypes, and a process with none installed is not advertised (S4-usecase-3). The
card-generation logic (``agent_card_from_skills``) is stable and pure.

**WIRE-5 (dual-mode intake):** The card advertises ``input_modes`` from each
process skill's metadata (JSON + text/plain); ``default_input_modes`` is the
de-duplicated union of all process skills' modes. Content routing is handled at
the edge (``bridge.edge.intake``); the card advertises the capability.

A2A SDK note: types are proto-based (1.x). ``protocol_version`` lives on the
per-interface ``AgentInterface`` (there is no ``AgentCard.protocol_version``
field). The card is served at ``/.well-known/agent-card.json`` by the server.
"""
from __future__ import annotations

import os

from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
)
from a2a.utils.constants import PROTOCOL_VERSION_1_0, TransportProtocol

from bridge.edge.skills import SkillMeta, loaded_skills
from contract.wire import CONTRACT_VERSION

# Default rpc endpoint the card advertises when none is supplied.
DEFAULT_URL = "http://localhost:8000/"

# The env the runtime injects (P7-5) with the Gateway-authorized public URL. The
# deployed Bridge MUST advertise its card ``url`` at this origin so a consumer's
# ``RemoteA2aAgent`` passes the ``google-adk >=2.7`` same-origin card validation
# (``_validate_card_rpc_targets``: a network-fetched card may only aim RPC traffic at
# the origin it was fetched from). This is the served-card half of the P7-5
# same-origin fix (the outbound half is ``bridge.seams.gcp.reconcile_card_host``).
PUBLIC_URL_ENV = "BRIDGE_PUBLIC_URL"


def resolved_card_url(explicit: str | None = None) -> str:
    """Resolve the URL the Agent Card advertises: explicit arg wins, else the
    ``BRIDGE_PUBLIC_URL`` env (the Gateway-authorized origin, P7-5), else ``DEFAULT_URL``.

    A deployed container with ``BRIDGE_PUBLIC_URL`` set advertises the Gateway URL as
    both the card ``url`` and the executor ``base_url`` (same-origin, adk >=2.7). Env
    unset → ``DEFAULT_URL``, so local/test behavior is unchanged.
    """
    if explicit is not None:
        return explicit
    return os.environ.get(PUBLIC_URL_ENV, "").strip() or DEFAULT_URL


def agent_card_from_skills(url: str, skills: list[SkillMeta]) -> AgentCard:
    """Build the dynamic Agent Card from skill metadata (WIRE-4).

    Generates the ``skills=[...]`` list by mapping each **process** skill's
    metadata to an ``AgentSkill``. Doctype skills are label space only (not
    advertised as separate ``AgentSkill``s per Decision 4).

    The card-level fields (name, version, interface, capabilities, input/output
    modes) are stable; only the ``skills`` list is derived from metadata.
    """
    # Filter to process skills only (Decision 4: one AgentSkill per process)
    process_skills = [s for s in skills if s.kind == "process"]

    # A candidate doctype only surfaces on the card once its doctype skill is
    # installed (SKILL-4 §2b): this is what makes a runtime doctype upload visibly
    # change the card (no redeploy) rather than advertising an absent capability.
    installed_doctypes = {s.name for s in skills if s.kind == "doctype"}

    # Generate AgentSkill for each process skill
    agent_skills = []
    for skill in process_skills:
        # Build tags: skill tags + doctype tags + contract version
        tags = list(skill.tags)
        # Add doctype: prefix tags only for candidates with an installed doctype skill
        for doctype in skill.candidate_doctypes:
            if doctype in installed_doctypes:
                tags.append(f"doctype:{doctype}")
        tags.append(f"contract-v{CONTRACT_VERSION}")

        # Title-case the name for display
        name_display = skill.name.replace("-", " ").title()

        agent_skills.append(
            AgentSkill(
                id=skill.name,
                name=name_display,
                description=skill.description,
                tags=tags,
                input_modes=list(skill.input_modes),
            )
        )

    # Compute default_input_modes as the de-duplicated union of all process skills'
    # input_modes (Decision 4). Preserve order: JSON first, then text.
    seen = set()
    default_input_modes = []
    for skill in process_skills:
        for mode in skill.input_modes:
            if mode not in seen:
                seen.add(mode)
                default_input_modes.append(mode)
    # Fallback to JSON-only if no process skills (shouldn't happen, but defensive)
    if not default_input_modes:
        default_input_modes = ["application/json"]

    return AgentCard(
        name="Document Bridge",
        description=(
            "Deterministic document-collection edge: send a CollectRequest plus "
            "by-reference documents, receive an ExchangeTurn (classified ledger "
            "projection) inside a standard A2A task artifact."
        ),
        version="0.1.0",
        supported_interfaces=[
            AgentInterface(
                url=url,
                protocol_binding=TransportProtocol.JSONRPC.value,
                protocol_version=PROTOCOL_VERSION_1_0,
            )
        ],
        capabilities=AgentCapabilities(streaming=True, push_notifications=True),
        default_input_modes=default_input_modes,
        default_output_modes=["application/json"],
        skills=agent_skills,
    )


def build_agent_card(
    url: str = DEFAULT_URL, skills: list[SkillMeta] | None = None
) -> AgentCard:
    """Build the dynamic Bridge Agent Card advertising the collect edge.

    ``url`` is the JSON-RPC endpoint (also the base the well-known card is served
    under). ``skills`` defaults to the *resolved* live registry (``loaded_skills()``:
    malformed folders skipped, label spaces narrowed to installed doctypes). An
    explicit ``skills=`` list is taken as given; the doctype-tag filter in
    ``agent_card_from_skills`` still applies to it.

    Input modes advertise dual-mode intake (``application/json`` + ``text/plain``,
    WIRE-5); output modes remain ``application/json`` only.
    """
    if skills is None:
        skills = loaded_skills()
    return agent_card_from_skills(url, skills)
