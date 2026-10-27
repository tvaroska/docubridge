"""The A2A RFP opener (S0-arch-19) — host glue that opens RFPs onto the durable board.

The deployed RFP board host (``bridge.dashboard.board_host``, S0-arch-17) serves and drains
onto whatever RFPs the shared ``exchange_store`` holds, but nothing deployed opened one: the
broker agent's emergent-Collect ``CollectRequest`` reached the A2A runtime's ``Collector``
and every drained reply correlated to nothing (``no_exchange_token``). This module is what
the edge executor calls instead, for every process the bound board serves: the HOME process
named by ``BRIDGE_RFP_PROCESS`` (``bridge.rfp.RFP_PROCESS_ENV`` — the same home process the
board host runs), plus any emergent-Collect process installed live in the skill registry
(``bridge.edge.skills.rfp_policy_for``, read through the ``skill_registry`` port on every
check — S4-usecase-4). Every RFP is projected with its OWN process's policy
(``RfpBoard.policy_for``):

* **An opener-only board, durable or nothing.** :func:`rfp_board_from_env` builds one
  live ``RfpBoard`` (home policy + ``live_policy=rfp_policy_for``) over the port-resolved
  ``exchange_store`` (local
  ``BRIDGE_EXCHANGE_DB_URL``, gcp ``DATABASE_URL`` — the same Cloud SQL DB the board host
  reads). It refuses an ``InMemoryExchangeStore``: an RFP opened into a per-process store
  is invisible to the board host, so every reply to it would be quarantined — the mirror
  of the board host's own refusal (S0-arch-17 #2). Unset / blank env → no board, and the
  edge is byte-identical to before. No processor, no read API, no ``BRIDGE_EMAIL_*``
  (reply addresses are minted on the board host from ``rfp_id`` + party).
* **Identity: ``rfp_id`` = the exchange context** (:func:`rfp_context_id`):
  ``request.context_id``, else the A2A task context. The intake token is
  ``HMAC(rfp_id, party)``, so the id must be stable; ``CollectRequest`` carries no rfp id
  (adding one is a contract change); the ``ExchangeTurn.context_id`` the broker gets back
  IS the rfp id (``GET /rfps/{context_id}`` on the board host), a follow-up on the same
  context addresses the same RFP, and a retried open is idempotent. An id that would
  break ``/rfps/{rfp_id}`` (``/``, whitespace, …) is refused.
* **One sync call does read → apply → put → project** (:func:`open_or_update_rfp`). The
  board's CAS versions are thread-local (S0-arch-14 #5), so the executor runs it whole on
  ONE worker thread (``asyncio.to_thread``) with no ``await`` between the read and the put;
  the event loop never blocks on the DB round-trip. Absent → ``open_rfp`` (create-only
  put); present + requirements → ``update_requirements`` (put only when it changes
  anything — an identical list is a no-op: no audit event, no version bump); present, no
  requirements → a status read. A present RFP of a different process than the request's is
  refused (``ValueError``). A ``StaleExchangeWrite`` re-reads and re-applies, up to
  ``_STALE_RETRIES`` attempts (two replicas racing one create: the loser's retry becomes
  an update or a read); exhausted → re-raised. A changed ``due_at`` on a follow-up is
  ignored (fixed at open).

Isolation: imports ``bridge.rfp``, ``bridge.exchange_store`` (stdlib-only top level),
``bridge.edge.skills`` (same package), ``contract.*`` and stdlib; ``RfpBoard`` only under ``TYPE_CHECKING`` plus a lazy import
inside :func:`rfp_board_from_env` (``bridge.edge.*`` never imports ``bridge.dashboard`` at
module top — the S0-bug-3 #7 circular-import trap). NEVER ``agents/``, never
``bridge.demos``, no skill literal (S0-arch-15).
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING

from bridge.edge.skills import rfp_policy_for
from bridge.exchange_store import (
    ExchangeStore,
    InMemoryExchangeStore,
    StaleExchangeWrite,
    exchange_store_from_env,
)
from bridge.rfp import (
    RFP_PROCESS_ENV,
    load_rfp_policy,
    open_rfp,
    to_exchange_turn,
    update_requirements,
)
from contract.wire import CollectRequest, ExchangeTurn

if TYPE_CHECKING:
    from bridge.dashboard.board import RfpBoard

logger = logging.getLogger(__name__)

# An rfp id is a URL path segment (``/rfps/{rfp_id}``) and the token's HMAC input.
RFP_CONTEXT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

# Fresh read → apply → put attempts on a CAS conflict (the EmailProcessor's posture).
_STALE_RETRIES = 3

# Refusal texts (raised by the executor before any task event; the reason code leads).
RFP_SUBMISSIONS_TEXT = (
    "rfp_submissions_unsupported: quotes for this process arrive by email or the "
    "carrier checklist, not on the A2A edge"
)
INVALID_RFP_CONTEXT_TEXT = (
    "invalid_rfp_context: the exchange context id must match "
    f"{RFP_CONTEXT_RE.pattern} (it is the RFP id)"
)
INVALID_RFP_REQUEST_PREFIX = "invalid_rfp_request: "
RFP_CONFLICT_TEXT = (
    "rfp_changed_concurrently: RFP {rfp_id!r} changed concurrently; retry"
)


class InvalidRfpContext(ValueError):
    """The exchange context id cannot be an RFP id (see :data:`RFP_CONTEXT_RE`)."""


def rfp_board_from_env(
    process: str | None = None,
    *,
    exchange_store: ExchangeStore | None = None,
    clock: Callable[[], datetime] | None = None,
) -> RfpBoard | None:
    """The A2A runtime's opener-only RFP board, or ``None`` when no process is configured.

    ``process`` (``None`` → ``BRIDGE_RFP_PROCESS``; blank → ``None``, no opener) names the
    board's HOME emergent-Collect process skill, loaded once here; a skill that is not one
    raises ``load_rfp_policy``'s ``ValueError`` (fail closed at app build). The board is
    live (``live_policy=rfp_policy_for``): it also serves any emergent-Collect process
    installed in the skill registry later, resolved per call (S4-usecase-4). ``exchange_store`` (``None`` →
    ``exchange_store_from_env()``) must be durable: an ``InMemoryExchangeStore`` raises
    ``RuntimeError`` — the board host would never see an RFP opened into it.
    """
    process = (process or os.environ.get(RFP_PROCESS_ENV, "")).strip()
    if not process:
        return None
    policy = load_rfp_policy(process)
    store = exchange_store or exchange_store_from_env()
    if isinstance(store, InMemoryExchangeStore):
        raise RuntimeError(
            f"{RFP_PROCESS_ENV}={process} binds the A2A RFP opener only over the durable "
            "shared exchange store (set BRIDGE_EXCHANGE_DB_URL locally / DATABASE_URL on "
            "gcp): an RFP opened into a per-process in-memory store is invisible to the "
            "RFP board host, so every carrier reply to it would be quarantined "
            "no_exchange_token"
        )
    from bridge.dashboard.board import RfpBoard  # lazy: the circular-import trap

    return RfpBoard(policy=policy, clock=clock, store=store, live_policy=rfp_policy_for)


def rfp_context_id(request: CollectRequest, a2a_context_id: str) -> str:
    """The RFP id of this exchange: ``request.context_id``, else the A2A task context.

    Raises :class:`InvalidRfpContext` (a ``ValueError``) when it does not match
    :data:`RFP_CONTEXT_RE`.
    """
    cid = request.context_id or a2a_context_id
    if not isinstance(cid, str) or not RFP_CONTEXT_RE.fullmatch(cid):
        raise InvalidRfpContext(f"not a valid RFP context id: {cid!r}")
    return cid


def open_or_update_rfp(
    board: RfpBoard, request: CollectRequest, *, context_id: str
) -> ExchangeTurn:
    """Open, update or read the RFP at ``context_id`` and return its ``ExchangeTurn``.

    Sync and whole: the fresh read (``board.rfps.get`` records this thread's version) and
    the put run in one loop iteration on the calling thread, with no other I/O between.

    - absent → ``open_rfp`` (``rfp_id`` = ``context_id`` = the exchange context), put
      create-only;
    - present, ``request.requirements`` non-empty → ``update_requirements``; put only if
      the result differs (an identical list is a no-op);
    - present, no requirements → a status read (no put).

    The policy is the RFP's own process's (``board.policy_for``), resolved inside this
    call (on the worker thread). A present RFP whose process differs from
    ``request.process`` raises ``ValueError``. ``ValueError`` from ``policy_for`` /
    ``open_rfp`` / ``update_requirements`` / the board's process guard propagates (no requirements or ``due_at`` on open, a naive ``due_at``, a party-less or
    duplicate item). ``StaleExchangeWrite`` re-reads and re-applies up to
    ``_STALE_RETRIES`` attempts, then propagates.
    """
    for attempt in range(_STALE_RETRIES):
        now = board.clock()
        existing = board.rfps.get(context_id)
        if existing is not None and existing.process != request.process:
            raise ValueError(
                f"RFP {context_id!r} runs process {existing.process!r}, "
                f"not {request.process!r}"
            )
        policy = board.policy_for(request.process)
        if existing is None:
            rfp = open_rfp(
                request.model_copy(update={"context_id": context_id}),
                now=now,
                rfp_id=context_id,
            )
        elif request.requirements is not None and request.requirements.requirements:
            rfp = update_requirements(existing, request.requirements, now=now)
            if rfp == existing:
                return to_exchange_turn(existing, now=now, policy=policy)
        else:
            return to_exchange_turn(existing, now=now, policy=policy)
        try:
            board.put(rfp)
        except StaleExchangeWrite:
            if attempt == _STALE_RETRIES - 1:
                raise
            logger.info(
                "RFP %r changed concurrently; retrying (%d/%d)",
                context_id,
                attempt + 1,
                _STALE_RETRIES,
            )
            continue
        return to_exchange_turn(rfp, now=now, policy=policy)
    raise AssertionError("unreachable")  # pragma: no cover - the loop returns or raises


__all__ = [
    "INVALID_RFP_CONTEXT_TEXT",
    "INVALID_RFP_REQUEST_PREFIX",
    "RFP_CONFLICT_TEXT",
    "RFP_CONTEXT_RE",
    "RFP_SUBMISSIONS_TEXT",
    "InvalidRfpContext",
    "open_or_update_rfp",
    "rfp_board_from_env",
    "rfp_context_id",
]
