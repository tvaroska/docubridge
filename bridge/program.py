"""Program aggregate — N-leg fan-out with per-leg party isolation (S1-benefits-1).

A **program** links N legs of one Negotiate exchange (``benefits-procurement``): the
broker's agent asks once, the Bridge fans out one leg per carrier, and each leg is its
own A2A context + party. The load-bearing invariant is **isolation**: competitors never
share a context — every leg has a distinct ``context_id``, and no two legs share a
``party`` or a ``context_id``. That is what lets N carriers be chased/negotiated in
parallel without one carrier ever seeing another's traffic.

**Beat-1 scope (this task).** A pure, in-memory Bridge aggregate — fan-out + isolation
only. Deliberately NOT here (deferred, not dropped):
- **Persistence / a materialized exchange record / program rollup** → S1-benefits-5.
- **``program_id`` on the wire** (adding it to ``contract.CollectRequest``) → S1-benefits-4
  (dial-out fan-out). Program identity is a Bridge-side aggregate this task; it does not
  ride the wire yet. This is a logged deferral (no silent cap — see DECISIONS.md).
- Per-leg bind (sense B) → S1-benefits-5.

Pure: imports stdlib only — NO ``google.adk``, NO ``a2a``, NEVER ``agents/`` (the
``bridge/`` isolation guard). Context ids are minted with ``uuid.uuid4().hex``, the same
edge idiom used in ``bridge.edge.executor``.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class Leg:
    """One program leg: a single carrier's isolated exchange.

    ``party`` is the carrier identity; ``context_id`` is that leg's own A2A exchange
    context. The 1:1 ``party`` ↔ ``context_id`` binding is the isolation unit — a party
    gets exactly one leg/context, and no context is shared across parties.
    """

    party: str
    context_id: str


@dataclass(frozen=True)
class Program:
    """A program linking N legs of one Negotiate exchange, one leg per carrier.

    ``program_id`` names the program (e.g. ``acme-2026``); ``legs`` is the ordered,
    immutable tuple of legs. Isolation invariant: distinct ``party`` and distinct
    ``context_id`` across all legs.
    """

    program_id: str
    legs: tuple[Leg, ...] = ()

    def leg_for(self, party: str) -> Leg:
        """Return the leg owned by ``party``; raise ``KeyError`` if the party has none."""
        for leg in self.legs:
            if leg.party == party:
                return leg
        raise KeyError(f"party {party!r} has no leg in program {self.program_id!r}")

    def parties(self) -> tuple[str, ...]:
        """The parties (carriers) with a leg, in leg order."""
        return tuple(leg.party for leg in self.legs)

    def contexts(self) -> tuple[str, ...]:
        """The per-leg context ids, in leg order (all distinct by construction)."""
        return tuple(leg.context_id for leg in self.legs)


def open_program(program_id: str) -> Program:
    """Open an empty program with no legs."""
    return Program(program_id=program_id)


def add_leg(program: Program, party: str) -> Program:
    """Return a NEW program with one leg for ``party`` appended.

    Mints a fresh ``context_id = uuid.uuid4().hex`` for the leg (the edge idiom). A
    party gets exactly one leg/context: raises ``ValueError`` if ``party`` already has a
    leg in ``program`` (the isolation invariant — competitors never share, and a party
    never doubles up).
    """
    if any(leg.party == party for leg in program.legs):
        raise ValueError(
            f"party {party!r} already has a leg in program {program.program_id!r}"
        )
    leg = Leg(party=party, context_id=uuid.uuid4().hex)
    return replace(program, legs=(*program.legs, leg))


def fan_out(program_id: str, parties: Sequence[str]) -> Program:
    """Open a program and add one isolated leg per party (fan-out convenience).

    Each party gets its own leg with a distinct minted ``context_id``. Raises
    ``ValueError`` on a duplicate party in ``parties`` (a party maps to exactly one
    leg/context — competitors never share a context).
    """
    seen: set[str] = set()
    for party in parties:
        if party in seen:
            raise ValueError(
                f"duplicate party {party!r} in fan_out for program {program_id!r}"
            )
        seen.add(party)

    program = open_program(program_id)
    for party in parties:
        program = add_leg(program, party)
    return program
