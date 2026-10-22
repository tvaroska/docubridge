"""Negotiate + versioned revise loop → bind → rollup (S1-benefits-5, beats 5,6 · CUJ-11).

The payoff tail of the Benefits demo, built as ONE cohesive Bridge-side aggregate composing
the prior beats (``bridge.program`` fan-out, ``bridge.normalize``, ``bridge.standard``,
``bridge.comparison``):

* **Beat 5 — versioned revise loop.** Pushback on a flagged coverage gap yields a **revised
  quote** that lands as a **new version on the same leg** (attempt N is version N; v0 is the
  initial quote, v1 the first revision — ``design/artifacts.md``). The program comparison
  **recomputes** as versions land: Carrier B goes flagged → conformant once its revised
  version arrives. Versioning is modelled at the **domain-object level** (a leg holds an
  ordered tuple of quote versions) and runs entirely over the fixture corpus, exactly as
  beats 1–4 did — there is no ADK ``ArtifactService`` call here.
* **Beat 6 — bind + rollup.** The agent **binds** the winning leg — **sense B: the deal is
  the agent's; the Bridge records, never decides**. ``bind_leg`` is recording-only: no
  accept/reject gate, no conformance enforcement (a non-conformant bind is allowed), mirroring
  the sense-A/sense-B split in ``bridge.standard``. The Bridge records per-leg bind → program
  rollup → the append-only **audit trail**.

**Revise-cap → escalate, never reject.** ``revise`` enforces the per-leg ``revise_cap`` from
the ``negotiate:`` policy block. Exceeding it **escalates** (routes to human), it does not
reject and does not silently crash: it raises ``ReviseCapExceeded`` whose ``reason_code`` /
``message`` are sourced from ``bridge.offscript.REASON_ESCALATED`` /
``bridge.pathb.ESCALATE_MESSAGE`` so the wording can't drift from the Path-B engine (the same
sourcing S0-debt-1 used to prove the §2b cap→escalate boundary over the A2A journey).

**Deferrals (no silent cap):**

* **Over-the-wire pushback send → S1-benefits-6.** The demo says the agent pushes back over
  A2A. This task builds the revise-loop **machinery** Bridge-side and drives it off the corpus
  revised quote; the **real** multi-turn A2A send + a live counterparty that returns the
  revision belong to S1-benefits-6 (ADK Carrier A / LangGraph Carrier B — the LangGraph
  carrier drives the multi-turn revise loop). The pushback is deliberately NOT routed through
  ``OutboundClient.dial`` here: ``DialOut.idempotency_key`` is ``dial:{context_id}``, so a
  second dial to the same leg would dedupe as ``resent`` — wrong semantics for a distinct
  negotiate turn, and fixing it means editing the CRITICAL ``bridge/outbound.py`` dedup for
  behavior that -6 will replace.
* **Live FX / real ADK dial** are unaffected by this task (they remain as their owning modules
  left them — ``bridge.normalize`` fixed-FX table, ``bridge.outbound`` real-ADK dial → -6/P7).

Pure: imports stdlib + pydantic + ``bridge.normalize`` + ``bridge.standard`` +
``bridge.comparison`` + ``bridge.sla._load_policy_doc`` + the two escalate-vocabulary
constants (``bridge.offscript.REASON_ESCALATED`` / ``bridge.pathb.ESCALATE_MESSAGE``) only —
NO ``a2a``, NEVER ``agents/`` (the ``bridge/`` isolation guard, ``tests/test_isolation_guard.py``).
Every function is synchronous and returns NEW immutable state — it never mutates in place
(the ``bridge.program`` replace-and-return idiom).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from bridge.comparison import ProgramComparison, build_comparison
from bridge.normalize import NormalizedQuote
from bridge.offscript import REASON_ESCALATED
from bridge.pathb import ESCALATE_MESSAGE
from bridge.program import Leg
from bridge.sla import _load_policy_doc
from bridge.standard import ProgramStandard, StandardGapVerdict, flag_quote

# Audit event kinds (the append-only trail beat 6 records).
KIND_LEG_OPENED = "leg_opened"
KIND_QUOTE_REVISED = "quote_revised"
KIND_REFLAGGED = "reflagged"
KIND_LEG_BOUND = "leg_bound"


@dataclass(frozen=True)
class NegotiatePolicy:
    """The Negotiate policy read from a process skill's policy.yaml ``negotiate:`` block.

    ``revise_cap`` is the max revise rounds per leg before a further revise escalates.
    """

    revise_cap: int


class ReviseCapExceeded(ValueError):
    """Raised when a revise would exceed ``revise_cap`` — escalate, never reject.

    ``reason_code`` / ``message`` are sourced from ``bridge.offscript.REASON_ESCALATED`` /
    ``bridge.pathb.ESCALATE_MESSAGE`` so the escalate wording can't drift from the Path-B
    engine (the §2b cap→escalate boundary). This is an escalate (route to human), NOT a
    reject and NOT a silent crash.
    """

    def __init__(self, party: str, attempted: int, cap: int) -> None:
        self.party = party
        self.attempted = attempted
        self.cap = cap
        self.reason_code = REASON_ESCALATED
        self.message = ESCALATE_MESSAGE
        super().__init__(
            f"revise cap exceeded for party {party!r}: attempted revise_count {attempted} "
            f"> revise_cap {cap}; escalating for human review ({REASON_ESCALATED})"
        )


class QuoteVersion(BaseModel):
    """One version of a leg's quote — the quote + its sense-A gap verdict at that version.

    ``version`` is 0-based: v0 is the initial quote, v1 the first revision (attempt N is
    version N).
    """

    model_config = ConfigDict(extra="forbid")

    version: int
    quote: NormalizedQuote
    verdict: StandardGapVerdict


class NegotiationLeg(BaseModel):
    """One program leg carrying its ordered version history + the bound version (if any).

    ``versions`` is append-only (a revise appends; prior versions are never mutated).
    ``bound_version`` is ``None`` until the agent binds (sense B).
    """

    model_config = ConfigDict(extra="forbid")

    party: str
    context_id: str
    versions: list[QuoteVersion]
    bound_version: int | None = None

    @property
    def current(self) -> QuoteVersion:
        """The current (latest) version of this leg's quote."""
        return self.versions[-1]

    @property
    def revise_count(self) -> int:
        """How many times this leg has been revised (v0 is 0 revises)."""
        return len(self.versions) - 1


class AuditEvent(BaseModel):
    """One append-only audit trail event with a monotonic ``seq`` (0-based)."""

    model_config = ConfigDict(extra="forbid")

    seq: int
    kind: str
    party: str
    version: int | None
    detail: str


class NegotiationProgram(BaseModel):
    """A program under negotiation: N versioned legs + the append-only audit trail."""

    model_config = ConfigDict(extra="forbid")

    program_id: str
    legs: list[NegotiationLeg]
    audit: list[AuditEvent]

    def leg_for(self, party: str) -> NegotiationLeg:
        """Return the leg owned by ``party``; raise ``KeyError`` if it has none."""
        for leg in self.legs:
            if leg.party == party:
                return leg
        raise KeyError(f"party {party!r} has no leg in program {self.program_id!r}")


class BoundLeg(BaseModel):
    """A bound leg in the rollup — party, the bound version, its premium + conformance.

    ``conformant`` is an advisory flag copied from the bound version's verdict; it does NOT
    gate the bind (sense B — the Bridge records, never decides).
    """

    model_config = ConfigDict(extra="forbid")

    party: str
    version: int
    premium: str
    conformant: bool


class ProgramRollup(BaseModel):
    """The program rollup: bound legs, still-unbound parties, and the full audit trail."""

    model_config = ConfigDict(extra="forbid")

    program_id: str
    bound: list[BoundLeg]
    unbound: list[str]
    audit: list[AuditEvent]


def load_negotiate_policy(
    skill_name: str, *, skills_dir: Path | None = None
) -> NegotiatePolicy:
    """Load the Negotiate policy from a process skill's declared ``negotiate:`` block.

    Reuses ``bridge.sla._load_policy_doc`` (resolving the SKILL.md ``metadata.bridge-policy``
    pointer, default ``assets/policy.yaml``) so ``bridge/sla.py`` stays off the change
    surface. Validates ``revise_cap >= 1``. Raises ``ValueError`` (naming the skill) on a
    missing/invalid ``negotiate:`` block, mirroring ``load_sla_policy``'s error style.
    """
    doc = _load_policy_doc(skill_name, skills_dir=skills_dir)
    negotiate = doc.get("negotiate")
    if not negotiate:
        raise ValueError(
            f"skill {skill_name!r} policy.yaml missing a 'negotiate:' block"
        )

    revise_cap = negotiate.get("revise_cap")
    if (
        not isinstance(revise_cap, int)
        or isinstance(revise_cap, bool)
        or revise_cap < 1
    ):
        raise ValueError(
            f"skill {skill_name!r} negotiate.revise_cap must be an int >= 1; "
            f"got {revise_cap!r}"
        )
    return NegotiatePolicy(revise_cap=revise_cap)


def _append(
    audit: list[AuditEvent],
    *,
    kind: str,
    party: str,
    version: int | None,
    detail: str,
) -> list[AuditEvent]:
    """Return a NEW audit list with one event appended (seq = current length, monotonic)."""
    event = AuditEvent(
        seq=len(audit), kind=kind, party=party, version=version, detail=detail
    )
    return [*audit, event]


def _iter_open_legs(
    legs: Mapping[str, NormalizedQuote] | Sequence[tuple[Leg, NormalizedQuote]],
) -> list[tuple[str, str, NormalizedQuote]]:
    """Normalize the ``open_negotiation`` legs argument to ``(party, context_id, quote)``.

    Accepts either a ``{party: quote}`` mapping (context_id defaults to the party) or a
    sequence of ``(Leg, quote)`` pairs (context_id from the leg — the fan-out path).
    """
    if isinstance(legs, Mapping):
        return [(party, party, quote) for party, quote in legs.items()]
    return [(leg.party, leg.context_id, quote) for leg, quote in legs]


def open_negotiation(
    program_id: str,
    legs: Mapping[str, NormalizedQuote] | Sequence[tuple[Leg, NormalizedQuote]],
    standard: ProgramStandard,
) -> NegotiationProgram:
    """Open a negotiation: seed v0 per leg, flag each against the standard, record the trail.

    Each leg gets a single v0 ``QuoteVersion`` carrying its initial quote + sense-A verdict;
    the audit trail gets a ``leg_opened`` + ``reflagged`` event per leg (monotonic seq, in
    leg order). Pure — returns a new ``NegotiationProgram``.
    """
    audit: list[AuditEvent] = []
    out_legs: list[NegotiationLeg] = []
    for party, context_id, quote in _iter_open_legs(legs):
        verdict = flag_quote(quote, standard)
        out_legs.append(
            NegotiationLeg(
                party=party,
                context_id=context_id,
                versions=[QuoteVersion(version=0, quote=quote, verdict=verdict)],
            )
        )
        audit = _append(
            audit, kind=KIND_LEG_OPENED, party=party, version=0, detail="v0 opened"
        )
        audit = _append(
            audit,
            kind=KIND_REFLAGGED,
            party=party,
            version=0,
            detail=f"route={verdict.route}",
        )
    return NegotiationProgram(program_id=program_id, legs=out_legs, audit=audit)


def _leg_index(program: NegotiationProgram, party: str) -> int:
    for idx, leg in enumerate(program.legs):
        if leg.party == party:
            return idx
    raise KeyError(f"party {party!r} has no leg in program {program.program_id!r}")


def revise(
    program: NegotiationProgram,
    party: str,
    revised_quote: NormalizedQuote,
    standard: ProgramStandard,
    *,
    policy: NegotiatePolicy,
) -> NegotiationProgram:
    """Append a revised quote as a new version on ``party``'s leg, re-flagged + audited.

    The new version is re-flagged against the standard (the comparison recomputes as it
    lands). Enforces ``policy.revise_cap``: if this revise would push the leg's revise_count
    past the cap it **escalates** — raises ``ReviseCapExceeded`` (route to human), never
    rejects and never silently drops. Pure — returns a new ``NegotiationProgram``; prior
    versions are left untouched.
    """
    idx = _leg_index(program, party)
    leg = program.legs[idx]

    # After appending, revise_count would be len(versions) (v0 is index 0). Cap it.
    new_revise_count = len(leg.versions)
    if new_revise_count > policy.revise_cap:
        raise ReviseCapExceeded(party, new_revise_count, policy.revise_cap)

    version_no = len(leg.versions)
    verdict = flag_quote(revised_quote, standard)
    new_version = QuoteVersion(version=version_no, quote=revised_quote, verdict=verdict)
    new_leg = NegotiationLeg(
        party=leg.party,
        context_id=leg.context_id,
        versions=[*leg.versions, new_version],
        bound_version=leg.bound_version,
    )
    new_legs = [*program.legs[:idx], new_leg, *program.legs[idx + 1 :]]

    audit = _append(
        program.audit,
        kind=KIND_QUOTE_REVISED,
        party=party,
        version=version_no,
        detail=f"revision {version_no} received",
    )
    audit = _append(
        audit,
        kind=KIND_REFLAGGED,
        party=party,
        version=version_no,
        detail=f"route={verdict.route}",
    )
    return NegotiationProgram(program_id=program.program_id, legs=new_legs, audit=audit)


def comparison_from_negotiation(
    program: NegotiationProgram, standard: ProgramStandard
) -> ProgramComparison:
    """Recompute the apples-to-apples comparison from each leg's CURRENT version.

    Pulls the current (latest) version's quote off every leg and reuses
    ``bridge.comparison.build_comparison`` (does not reimplement flagging), so the comparison
    reflects every revised version that has landed.
    """
    quotes = [leg.current.quote for leg in program.legs]
    return build_comparison(program.program_id, quotes, standard)


def bind_leg(
    program: NegotiationProgram, party: str, *, version: int | None = None
) -> NegotiationProgram:
    """Record the agent's bind of a chosen version on ``party``'s leg (sense B).

    ``version`` defaults to the current (latest) version. The Bridge RECORDS the bind, it
    never decides — there is no accept/reject gate and no conformance enforcement, so a
    non-conformant bind is allowed. Audits a ``leg_bound`` event. Pure — returns a new
    ``NegotiationProgram``.
    """
    idx = _leg_index(program, party)
    leg = program.legs[idx]

    bound_version = leg.current.version if version is None else version
    if not any(v.version == bound_version for v in leg.versions):
        known = [v.version for v in leg.versions]
        raise ValueError(
            f"party {party!r} has no version {bound_version} to bind; versions: {known}"
        )

    new_leg = NegotiationLeg(
        party=leg.party,
        context_id=leg.context_id,
        versions=leg.versions,
        bound_version=bound_version,
    )
    new_legs = [*program.legs[:idx], new_leg, *program.legs[idx + 1 :]]
    audit = _append(
        program.audit,
        kind=KIND_LEG_BOUND,
        party=party,
        version=bound_version,
        detail=f"bound version {bound_version}",
    )
    return NegotiationProgram(program_id=program.program_id, legs=new_legs, audit=audit)


def roll_up(program: NegotiationProgram) -> ProgramRollup:
    """Roll the program up: bound legs (party/version/premium/conformance) + unbound parties.

    Carries the full ordered audit trail. The ``conformant`` flag on a bound leg is advisory
    (copied from the bound version's verdict) — it never blocked the bind (sense B).
    """
    bound: list[BoundLeg] = []
    unbound: list[str] = []
    for leg in program.legs:
        if leg.bound_version is None:
            unbound.append(leg.party)
            continue
        version = next(v for v in leg.versions if v.version == leg.bound_version)
        bound.append(
            BoundLeg(
                party=leg.party,
                version=version.version,
                premium=version.quote.premium,
                conformant=version.verdict.conformant,
            )
        )
    return ProgramRollup(
        program_id=program.program_id,
        bound=bound,
        unbound=unbound,
        audit=program.audit,
    )
