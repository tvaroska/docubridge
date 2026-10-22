"""Program + RFP boards (S1-benefits-7, S0-arch-11/14) — the injectable dashboard sources.

The Ops Dashboard host reads its **program comparison** view off a small, injectable
``ProgramBoard`` — the exact same host-glue posture as ``build_dashboard_app`` taking an
injectable ``PortalStore``. The board holds the ``NegotiationProgram`` aggregates under
negotiation (keyed by ``program_id``, insertion-ordered) plus the ``ProgramStandard`` to
flag them against, and **recomputes the comparison on every read** via
``bridge.negotiate.comparison_from_negotiation`` — which pulls each leg's *current*
version. So a landed revision (``board.put(revise(...))``) is reflected on the next read
with no snapshotting: this is exactly the "recomputes live as revised versions land"
requirement (beat 4 UI · CUJ-11).

**Recompute-on-read, not snapshot (load-bearing).** The board stores the
``NegotiationProgram``, NEVER a materialized ``ProgramComparison``. Recomputing per read is
what makes a ``board.put(revised_program)`` surface immediately (over REST and on the next
SSE tick). Do not cache a comparison here.

**Durable program store DEFERRED, not dropped.** ``ProgramBoard`` is a pure in-memory
aggregate this cycle — the same posture ``bridge.negotiate`` chose for negotiation
persistence (a leg holds its versions in memory, driven off the corpus). A **durable**
program store (surviving a process restart) is a later-cycle concern; it is logged here as
designed-but-unbuilt (no silent cap). This paragraph is about programs only — RFPs persist
through the ``exchange_store`` seam (below).

**RFP board (S0-arch-11, S0-arch-14).** ``RfpBoard`` is the same host glue for RFP
collections: it projects ``bridge.rfp.rfp_status`` on read, so the dashboard ``/exchanges``
answers "which quotes are ready, which are waiting on a carrier". ``find_leg`` resolves a
carrier leg by its ``context_id`` — the per-leg addressing handle the carrier checklist
(S0-frontend-1) is reached by. The ``RfpCollection`` is the **materialized Exchange
record** of an emergent Collect (``design/aggregate-model.md``), so the board persists it
through the ``exchange_store`` seam (``bridge.exchange_store``): one record per RFP,
``kind="rfp"``, keyed by ``rfp_id``, carrying the broker-side A2A ``context_id``. Every read
goes to the store (no cache — a cache would re-create a per-replica split), and every
``put`` is compare-and-set against the version THIS THREAD last read or wrote for that RFP,
so a write derived from a superseded read raises ``StaleExchangeWrite`` instead of silently
losing the other writer's change. A board defaults to an in-memory store; a host wires the
seam-resolved one (``exchange_store_from_env()``, e.g. ``BRIDGE_EXCHANGE_DB_URL``) so an RFP
survives a restart.

Isolation: imports ``bridge.*`` + stdlib only; NEVER ``agents/`` (the ``bridge/``
isolation guard walks ``bridge/**``, ``tests/test_isolation_guard.py``).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterator, Mapping
from datetime import UTC, datetime

from bridge.comparison import ProgramComparison
from bridge.exchange_store import ExchangeRecord, ExchangeStore, InMemoryExchangeStore
from bridge.negotiate import NegotiationProgram, comparison_from_negotiation
from bridge.rfp import RfpCollection, RfpLeg, RfpPolicy, RfpStatusView, rfp_status
from bridge.standard import ProgramStandard

logger = logging.getLogger(__name__)


class ProgramBoard:
    """An injectable in-memory board of ``NegotiationProgram``s + the flagging standard.

    ``put`` upserts a program by ``program_id`` (a landed revise is a re-put of the same
    program). ``comparisons`` / ``get`` recompute the comparison on read so revisions
    surface live. Insertion order is preserved (a ``dict`` keeps it).
    """

    def __init__(self, *, standard: ProgramStandard) -> None:
        self.standard = standard
        self.programs: dict[str, NegotiationProgram] = {}

    def put(self, program: NegotiationProgram) -> None:
        """Upsert a program by ``program.program_id`` (a revise lands as a re-put)."""
        self.programs[program.program_id] = program

    def comparisons(self) -> list[ProgramComparison]:
        """Recompute every program's comparison from each leg's CURRENT version.

        Recomputes per read (does NOT snapshot) so a landed revision is reflected.
        Preserves insertion order.
        """
        return [
            comparison_from_negotiation(program, self.standard)
            for program in self.programs.values()
        ]

    def get(self, program_id: str) -> ProgramComparison:
        """Recompute one program's comparison; raise ``KeyError`` if unknown."""
        program = self.programs[program_id]
        return comparison_from_negotiation(program, self.standard)


# The exchange-record kind an RFP is stored under (``bridge.exchange_store``).
KIND_RFP = "rfp"


class _RfpMapping(Mapping[str, RfpCollection]):
    """``board.rfps`` — a READ-ONLY ``rfp_id → RfpCollection`` view over the store.

    Every access reads through the store (and records the version read for this thread);
    ``values()`` / ``items()`` cost one ``store.list``. There is no ``__setitem__``:
    ``RfpBoard.put`` is the only writer.
    """

    def __init__(self, board: RfpBoard) -> None:
        self._board = board

    def __getitem__(self, rfp_id: str) -> RfpCollection:
        rfp = self._board._get(rfp_id)
        if rfp is None:
            raise KeyError(rfp_id)
        return rfp

    def get(
        self, rfp_id: str, default: RfpCollection | None = None
    ) -> RfpCollection | None:  # type: ignore[override]
        """Return the RFP state for a given key, or the default value if absent."""
        rfp = self._board._get(rfp_id)
        return default if rfp is None else rfp

    def __contains__(self, rfp_id: object) -> bool:
        return (
            isinstance(rfp_id, str)
            and bool(rfp_id)
            and self._board.store.get(KIND_RFP, rfp_id) is not None
        )

    def __iter__(self) -> Iterator[str]:
        return iter([rec.ref for rec in self._board.store.list(KIND_RFP)])

    def __len__(self) -> int:
        return len(self._board.store.list(KIND_RFP))

    def values(self) -> list[RfpCollection]:  # type: ignore[override]
        """Return an iterable view of all stored RFP state records."""
        return self._board._all()

    def items(self) -> list[tuple[str, RfpCollection]]:  # type: ignore[override]
        """Return an iterable view of all (rfp_id, state) pairs."""
        return [(rfp.rfp_id, rfp) for rfp in self._board._all()]


class RfpBoard:
    """An injectable board of ``RfpCollection``s under collection (S0-arch-11), persisted
    through the ``exchange_store`` seam (S0-arch-14).

    Same posture as ``ProgramBoard``: ``put`` writes by ``rfp_id`` (a filed quote is a
    re-put), and ``statuses`` / ``get`` project **on read** at ``clock()`` — so both a filed
    quote AND the due date passing (an item turning ``overdue``, the ladder stepping) surface
    on the next read with no snapshotting. ``clock`` is injectable for tests and time-warp.

    ``store`` defaults to a fresh ``InMemoryExchangeStore`` (NOT env resolution — a bare
    ``RfpBoard(policy=…)`` stays pure); a host passes ``exchange_store_from_env()``. Reads
    are read-through (no cache). ``put`` is compare-and-set: it expects the version this
    thread last read or wrote for that ``rfp_id`` (``0`` — create — if never seen), so the
    write sites' "fresh read → apply → put" rule holds across replicas; a put of a
    superseded object raises ``StaleExchangeWrite``. Versions are thread-scoped because
    FastAPI runs sync routes on a threadpool: a concurrent read on another thread must not
    bump the version an in-flight write expects.

    Processes served: the **home** process (``policy.process``, loaded from the process
    skill when the host is built and used as-is), plus — when ``live_policy`` is bound
    (the deployed hosts bind ``bridge.edge.skills.rfp_policy_for``) — every
    emergent-Collect process live in the skill registry, resolved per RFP on every read
    (S4-usecase-4). An RFP is always projected with its OWN process's policy
    (``policy_for(rfp.process)``), so no RFP is ever projected with another skill's ladder
    or label space (S0-arch-15 holds per RFP). ``put`` refuses an RFP of a process the
    board does not serve. With ``live_policy=None`` (the default) this is exactly the
    single-process board: every other process is refused.
    """

    def __init__(
        self,
        *,
        policy: RfpPolicy,
        clock: Callable[[], datetime] | None = None,
        store: ExchangeStore | None = None,
        live_policy: Callable[[str], RfpPolicy] | None = None,
    ) -> None:
        self.policy = policy
        # The live resolver for non-home processes; raises ``ValueError`` for a process
        # that is not a live emergent-Collect skill. ``None`` = single-process board.
        self.live_policy = live_policy
        self.clock = clock or (lambda: datetime.now(UTC))
        # The injectable ``param or InMemory…()`` idiom (tests/test_service_wiring_guard.py);
        # an ExchangeStore defines no ``__len__`` / ``__bool__``, so it is always truthy.
        self.store: ExchangeStore = store or InMemoryExchangeStore()
        self._seen = threading.local()

    # -- store plumbing ----------------------------------------------------------

    def _versions(self) -> dict[str, int]:
        """The ``rfp_id → version`` this thread last read or wrote."""
        versions = getattr(self._seen, "versions", None)
        if versions is None:
            versions = self._seen.versions = {}
        return versions

    def _decode(self, rec: ExchangeRecord) -> RfpCollection:
        self._versions()[rec.ref] = rec.version
        return RfpCollection.model_validate(rec.doc)

    def _get(self, rfp_id: str) -> RfpCollection | None:
        if not isinstance(rfp_id, str) or not rfp_id:
            return None
        rec = self.store.get(KIND_RFP, rfp_id)
        return None if rec is None else self._decode(rec)

    def _all(self) -> list[RfpCollection]:
        return [self._decode(rec) for rec in self.store.list(KIND_RFP)]

    # -- public surface ----------------------------------------------------------

    def policy_for(self, process: str) -> RfpPolicy:
        """The policy an RFP of ``process`` is projected / mutated with.

        The home process → ``self.policy`` (fixed at build, no re-read). Any other process
        → ``live_policy(process)`` when bound (a live registry read, every call), else
        ``ValueError``. ``live_policy`` raises ``ValueError`` for a process that is not a
        live emergent-Collect process skill.
        """
        if process == self.policy.process:
            return self.policy
        if self.live_policy is None:
            raise ValueError(
                f"this board runs {self.policy.process!r}, not {process!r}"
            )
        return self.live_policy(process)

    def serves(self, process: str) -> bool:
        """Whether an RFP of ``process`` can be opened / projected on this board now."""
        try:
            self.policy_for(process)
        except ValueError:
            return False
        return True

    def _resolved(
        self, rfps: list[RfpCollection]
    ) -> list[tuple[RfpCollection, RfpPolicy]]:
        """Pair each RFP with its process's policy, resolving each process ONCE.

        An RFP whose process no longer resolves (its skill removed or broken after the
        RFP opened) is skipped with a WARNING, so one such RFP cannot fail the whole
        listing.
        """
        policies: dict[str, RfpPolicy | None] = {}
        out: list[tuple[RfpCollection, RfpPolicy]] = []
        for rfp in rfps:
            if rfp.process not in policies:
                try:
                    policies[rfp.process] = self.policy_for(rfp.process)
                except ValueError as exc:
                    policies[rfp.process] = None
                    logger.warning(
                        "RFP process %r unavailable; its RFPs are skipped: %s",
                        rfp.process,
                        exc,
                    )
            policy = policies[rfp.process]
            if policy is None:
                continue
            out.append((rfp, policy))
        return out

    @property
    def rfps(self) -> Mapping[str, RfpCollection]:
        """A read-only ``rfp_id → RfpCollection`` view (creation order) over the store."""
        return _RfpMapping(self)

    def put(self, rfp: RfpCollection) -> None:
        """Write an RFP by ``rfp.rfp_id`` (a filed quote lands as a re-put).

        Compare-and-set against this thread's last-seen version; raises
        ``StaleExchangeWrite`` when another writer has moved the record since. Raises
        ``ValueError`` (before touching the store) for an RFP whose ``process`` this board
        does not serve (``policy_for``).
        """
        if self.live_policy is None:
            if rfp.process != self.policy.process:
                raise ValueError(
                    f"RFP {rfp.rfp_id!r} runs process {rfp.process!r}; "
                    f"this board runs {self.policy.process!r}"
                )
        else:
            self.policy_for(rfp.process)
        versions = self._versions()
        version = self.store.put(
            KIND_RFP,
            rfp.rfp_id,
            context_id=rfp.context_id,
            doc=rfp.model_dump(mode="json"),
            expected_version=versions.get(rfp.rfp_id, 0),
        )
        versions[rfp.rfp_id] = version

    def items(self) -> list[tuple[RfpCollection, RfpStatusView]]:
        """Every RFP with its projection at one ``clock()`` reading (creation order)."""
        now = self.clock()
        return [
            (r, rfp_status(r, now=now, policy=policy))
            for r, policy in self._resolved(self._all())
        ]

    def statuses(self) -> list[RfpStatusView]:
        """Project every RFP at ``clock()`` (creation order)."""
        return [view for _, view in self.items()]

    def get(self, rfp_id: str) -> RfpStatusView:
        """Project one RFP at ``clock()``; raise ``KeyError`` if unknown.

        Also ``KeyError`` (chained) when the RFP's process no longer resolves, so the API
        maps it to 404 rather than projecting it with another process's policy.
        """
        rfp = self.rfps[rfp_id]
        try:
            policy = self.policy_for(rfp.process)
        except ValueError as exc:
            raise KeyError(rfp_id) from exc
        return rfp_status(rfp, now=self.clock(), policy=policy)

    def find_leg(self, context_id: str) -> tuple[RfpCollection, RfpLeg]:
        """The open (RFP, carrier leg) whose leg ``context_id`` this is; ``KeyError`` if none.

        A leg context is unguessable (``uuid4().hex``) and names exactly one carrier, so it
        is the carrier checklist's addressing handle (``design/zones.md``: carrier-vs-carrier
        confidentiality is addressing).
        """
        for rfp in self._all():
            for leg in rfp.legs:
                if leg.context_id == context_id:
                    return rfp, leg
        raise KeyError(f"no open RFP leg has context {context_id!r}")
