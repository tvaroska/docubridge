"""Inbound email intake edge (S0-arch-13): a carrier's emailed quote → the RFP exchange.

Two halves, deployable apart (``design/email-intake.md``):

* **The Graph webhook** (``POST /email/graph/notifications``) — answers the Graph
  subscription handshake, authenticates each change notification by its
  ``clientState`` secret, fetches the raw RFC 5322 bytes from the CONFIGURED mailbox
  by message id (``bridge.email_ingress.graph``) and lands them on the
  ``email_ingress`` port. Landing is **create-only**, so a Graph re-delivery (or a
  503-then-retry) is a ``duplicate``, never a second filing; nothing is acknowledged
  to Graph before it is landed (any Graph / storage failure → 503 for the batch).
* **The processor** (:class:`EmailProcessor`, ``POST /email/drain``) — on the host
  that owns the ``RfpBoard``: ``pending()`` → parse → sender-auth / limits → token
  correlation to exactly one open (RFP, carrier) leg → each attachment through the
  extraction port + the disposition gate → ``bridge.rfp.receive`` → ``board.put`` →
  ``mark_processed``. The landed bucket/directory IS the queue.

**Where the processor binds (S0-bug-3).** On the host that owns the RFP board —
``bridge.dashboard.server.build_dashboard_app(rfps=…)`` (and the local demo host that
mounts it) — wired from env by :func:`email_processor_from_env` (``BRIDGE_EMAIL_MAILBOX``
+ ``BRIDGE_EMAIL_TOKEN_SECRET`` + a landing location), serving the drain routes
(:func:`mount_email_drain_routes`) and draining every ``BRIDGE_EMAIL_DRAIN_INTERVAL``
seconds (:func:`email_drain_loop`; default 30, ``0`` = on demand only). Deployed, that
host is the internal RFP board host (``bridge.dashboard.board_host``, S0-arch-17): its
board is a read-through view of the durable shared ``exchange_store`` (it refuses to bind
a processor over an in-memory store), and Cloud Scheduler drives ``POST /email/drain``
(``BRIDGE_EMAIL_DRAIN_INTERVAL=0`` there). NEVER on a host whose board is not the source
of truth (an empty per-process board no RFP opens onto): every landed mail would
correlate to nothing and be permanently marked processed ``no_exchange_token`` — that
destroys the queue.

**Retry order (load-bearing).** Every attachment is extracted FIRST, with the board
untouched. A deterministic refusal (``ValueError`` / ``KeyError``: resolver refusal,
schema-retry exhaustion, fixture miss) marks that attachment unreadable; any OTHER
exception (a transient 429/500) propagates — ``drain`` raises, no marker is written,
the board is untouched, and the next drain retries that key (drain stops at the
first failure, preserving landing order). Only then are the bytes persisted, the
board re-read, the quotes filed and ``board.put`` called — with no ``await`` between
that re-read and the put, so a concurrent writer on the same event loop is never
overwritten — and finally the processed marker written. ``board.put`` is
compare-and-set (S0-arch-14), so a writer on ANOTHER replica makes it stale: the
processor redoes "fresh read → apply → put" (never the extraction or the persisted
bytes) up to three attempts, then lets ``StaleExchangeWrite`` propagate (no marker, the
key stays pending, the next drain retries). **Re-file guard:** if the fresh read already
holds one of this mail's ``email-<key>-<index>`` doc ids — another replica filed it, or a
crash hit between ``board.put`` and the marker — nothing is re-applied and only the
marker is written (from the stored entries). Delivery is at-least-once without a
duplicate version for any mail with an extracted attachment; an unreadable-only /
attachment-less mail re-applied may append one duplicate audit-only event (no cell
moves), and a retry after a crash in phases 2-3 may add one extra artifact version.

**No fabricated verdict.** An attachment no engine reads (``rates.xlsx``) or one the
engine deterministically refuses is recorded with ``bridge.rfp.note_unreadable`` —
an audit-only event; the disposition gate stays the sole verdict authority.

**Per-RFP policy (S4-usecase-4).** The board serves its home process plus any
emergent-Collect process installed live in the skill registry; a correlated mail is
dispositioned and filed with the RFP's OWN process policy (``RfpBoard.policy_for``: its
label space and ``item_field``), resolved once before phase 1.

**Quarantine never touches the board.** An unparseable / oversize / over-limit mail,
an explicit ``dmarc=fail``, no token, an ambiguous token, or a correlated RFP whose
process no longer resolves (``process_unavailable``) is quarantined: the reason
is written to the processed marker (``GET /email/outcomes/{key}``) and logged at
WARNING — never silently dropped. The message body is never persisted anywhere.

Edge-only because it touches ``google.adk`` artifacts (the ``bridge/edge/artifacts.py``
Decision 3 posture). Imports ``contract/``, ``bridge.*``, ``fastapi``/``starlette``,
``pydantic`` and stdlib — NEVER ``agents/``.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import json
import logging
import math
import os
import re
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any, Literal

from fastapi import BackgroundTasks, FastAPI, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict
from starlette.concurrency import run_in_threadpool

from bridge.collect import DocumentSubmission, project_ledger_entry
from bridge.edge.artifacts import build_artifact_store, persist_submission, scoped_uri
from bridge.edge.card import resolved_card_url
from bridge.email_ingress.correlation import (
    AmbiguousToken,
    IntakeAddressing,
    correlate,
    find_tokens,
    intake_address,
    mint_token,
    subject_tag,
    token_secret_from_env,
)
from bridge.email_ingress.graph import (
    MESSAGE_ID_RE,
    GraphError,
    GraphMailClient,
    MessageTooLarge,
    client_state_from_env,
)
from bridge.email_ingress.mime import landing_key, max_mail_bytes, parse_mime
from bridge.exchange_store import StaleExchangeWrite
from bridge.extraction.resolve import SUFFIX_MIME
from bridge.ports import EmailIngressPort, Port, PortMode, get_adapter, resolve_mode
from bridge.ports.gcp import EMAIL_BUCKET_ENV
from bridge.ports.local import EMAIL_DIR_ENV
from bridge.ports.protocols import EMAIL_KEY_PATTERN
from bridge.rfp import RfpPolicy, acknowledge, note_unreadable, receive
from bridge.signals import dispose_signals, signals_from_result
from contract.enums import RfpItemStatus
from contract.wire import ArtifactRef, LedgerEntry

if TYPE_CHECKING:
    # Annotation-only: importing ``bridge.dashboard`` at module top would run
    # ``bridge.dashboard.server``, which lazily binds this module — a fresh-process
    # ``import bridge.edge.email_ingress`` must not hit a partially-initialized cycle.
    from bridge.dashboard.board import RfpBoard

_LOG = logging.getLogger("bridge.edge.email_ingress")

MAILBOX_ENV = "BRIDGE_EMAIL_MAILBOX"
DRAIN_INTERVAL_ENV = "BRIDGE_EMAIL_DRAIN_INTERVAL"
DEFAULT_DRAIN_INTERVAL = 30.0
NOTIFICATIONS_PATH = "/email/graph/notifications"
MAX_NOTIFICATION_BYTES = 256 * 1024
MAX_VALIDATION_TOKEN = 1024
_KEY_RE = re.compile(EMAIL_KEY_PATTERN)
_DMARC_FAIL_RE = re.compile(r"\bdmarc\s*=\s*fail\b", re.IGNORECASE)
_GUID_RE = re.compile(r"^[0-9a-fA-F]{8}-([0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$")
# Phase-3 compare-and-set attempts before a concurrent-write conflict propagates.
_STALE_RETRIES = 3

# The media types an extraction engine reads — the shared resolver's one table.
READABLE_MIME_TYPES = frozenset(SUFFIX_MIME.values())

# Quarantine / outcome reason tokens.
REASON_SENDER_AUTH = "sender_authentication_failed"
REASON_NO_TOKEN = "no_exchange_token"
REASON_AMBIGUOUS = "ambiguous_token"
REASON_NO_ATTACHMENTS = "no_attachments"
REASON_UNSUPPORTED = "unsupported_media_type"
# The correlated RFP's process no longer resolves on this board (its skill was removed or
# broken after the RFP opened) — S4-usecase-4.
REASON_PROCESS_UNAVAILABLE = "process_unavailable"


class FiledAttachment(BaseModel):
    """One attachment that went through the gate and was filed on the RFP."""

    model_config = ConfigDict(extra="forbid")

    index: int
    filename: str
    doc_id: str
    disposition: str
    gate: str
    reason_code: str | None = None
    product_line: str | None = None
    artifact_uri: str


class UnreadableAttachment(BaseModel):
    """One attachment nothing could read — recorded, never verdicted."""

    model_config = ConfigDict(extra="forbid")

    index: int
    filename: str
    reason: str


class MailOutcome(BaseModel):
    """What happened to one landed mail — exactly the processed-marker JSON. No bytes, no body."""

    model_config = ConfigDict(extra="forbid")

    key: str
    status: Literal["filed", "quarantined"]
    reason: str | None = None
    rfp_id: str | None = None
    party: str | None = None
    sender: str | None = None
    subject: str | None = None
    filed: list[FiledAttachment] = []
    unreadable: list[UnreadableAttachment] = []
    skipped_inline: int = 0


def _mailbox_from_env() -> str:
    value = os.environ.get(MAILBOX_ENV, "").strip()
    if not value:
        raise RuntimeError(f"email intake requires {MAILBOX_ENV} (the intake mailbox)")
    return value


def _default_port() -> EmailIngressPort:
    return get_adapter(Port.email_ingress, resolve_mode(Port.email_ingress))


class EmailProcessor:
    """Drain landed mail onto the ``RfpBoard`` (see the module docstring for the order)."""

    def __init__(
        self,
        *,
        board: RfpBoard,
        port: EmailIngressPort | None = None,
        mailbox: str | None = None,
        secret: bytes | str | None = None,
        extraction: Any = None,
        artifact_store: Any = None,
        base_url: str | None = None,
    ) -> None:
        max_mail_bytes()  # a malformed BRIDGE_EMAIL_MAX_BYTES fails loud here, not per mail
        self.board = board
        self.port = port or _default_port()
        self.mailbox = mailbox or _mailbox_from_env()
        intake_address(self.mailbox, "a" * 16)  # validate the mailbox shape now
        if secret is None:
            secret = token_secret_from_env()
        self._secret = secret.encode() if isinstance(secret, str) else secret
        if not self._secret:
            raise ValueError("email intake token secret must not be empty")
        self.extraction = extraction or get_adapter(
            Port.extraction, resolve_mode(Port.extraction)
        )
        self.artifact_store = artifact_store or build_artifact_store()
        self.base_url = resolved_card_url(base_url)
        self._lock = asyncio.Lock()

    # -- outbound helpers (the invite / "chase now" copy, S0-frontend-1) ------------ #

    def token_for(self, rfp_id: str, party: str) -> str:
        """Extract the intake correlation token from an inbound email recipient address."""
        return mint_token(self._secret, rfp_id, party)

    def reply_address(self, rfp_id: str, party: str) -> str:
        """The plus-addressed intake address a carrier replies to for this leg."""
        return intake_address(self.mailbox, self.token_for(rfp_id, party))

    def reply_subject_tag(self, rfp_id: str, party: str) -> str:
        """The ``[ref:<token>]`` subject fallback for this leg."""
        return subject_tag(self.token_for(rfp_id, party))

    @property
    def intake(self) -> IntakeAddressing:
        """The reply addressing this processor correlates on (mailbox + token key).

        A host showing reply addresses next to a bound processor uses this, so the
        address a carrier is told to reply to is exactly the one that correlates.
        """
        return IntakeAddressing(mailbox=self.mailbox, secret=self._secret)

    # -- the drain ------------------------------------------------------------------ #

    async def drain(self) -> list[MailOutcome]:
        """Process every pending key in landing order; a transient error propagates."""
        async with self._lock:
            outcomes: list[MailOutcome] = []
            for key in await asyncio.to_thread(self.port.pending):
                outcomes.append(await self.process(key))
            return outcomes

    async def _quarantine(
        self,
        key: str,
        reason: str,
        *,
        sender: str | None = None,
        subject: str | None = None,
        skipped_inline: int = 0,
    ) -> MailOutcome:
        outcome = MailOutcome(
            key=key,
            status="quarantined",
            reason=reason,
            sender=sender,
            subject=subject,
            skipped_inline=skipped_inline,
        )
        _LOG.warning("email %s quarantined: %s (from %s)", key, reason, sender)
        await asyncio.to_thread(
            self.port.mark_processed, key, outcome.model_dump(mode="json")
        )
        return outcome

    def _filed(
        self, att: Any, entry: LedgerEntry, policy: RfpPolicy
    ) -> FiledAttachment:
        """The outcome line for one attachment filed as ``entry`` (``policy``: the RFP's
        own process policy — its ``item_field`` names the item)."""
        line = entry.key_fields.get(policy.item_field)
        return FiledAttachment(
            index=att.index,
            filename=att.filename,
            doc_id=entry.doc_id,
            disposition=str(entry.disposition.value),
            gate=str(entry.gate.value),
            reason_code=entry.reason_code,
            product_line=str(line) if line is not None else None,
            artifact_uri=entry.artifact.uri if entry.artifact is not None else "",
        )

    async def process(self, key: str) -> MailOutcome:
        """Process one landed key (idempotent: an already-processed key returns its marker)."""
        existing = await asyncio.to_thread(self.port.processed, key)
        if existing is not None:
            return MailOutcome.model_validate(existing)
        raw = await asyncio.to_thread(self.port.read, key)
        try:
            mail = parse_mime(raw)
        except ValueError as exc:
            return await self._quarantine(key, str(exc))
        meta = {
            "sender": mail.sender,
            "subject": mail.subject,
            "skipped_inline": mail.skipped_inline,
        }
        if any(_DMARC_FAIL_RE.search(v) for v in mail.auth_results):
            return await self._quarantine(key, REASON_SENDER_AUTH, **meta)
        tokens = find_tokens(mail, self.mailbox)
        try:
            match = correlate(tokens, list(self.board.rfps.values()), self._secret)
        except AmbiguousToken:
            return await self._quarantine(key, REASON_AMBIGUOUS, **meta)
        if match is None:
            return await self._quarantine(key, REASON_NO_TOKEN, **meta)
        rfp, leg = match
        # The RFP's OWN process policy (S4-usecase-4: the board's home process, or a
        # process installed live in the registry), resolved once, BEFORE any attachment
        # is stored or extracted. Unresolvable → quarantine; the board is not touched.
        try:
            policy = self.board.policy_for(rfp.process)
        except ValueError:
            return await self._quarantine(key, REASON_PROCESS_UNAVAILABLE, **meta)

        # Phase 1 — extract every attachment; the board is NOT touched.
        extracted = []
        unreadable: list[UnreadableAttachment] = []
        for att in mail.attachments:
            uri = await asyncio.to_thread(
                self.port.put_attachment,
                key,
                att.index,
                att.filename,
                att.data,
                att.mime_type,
            )
            if att.mime_type not in READABLE_MIME_TYPES:
                unreadable.append(
                    UnreadableAttachment(
                        index=att.index,
                        filename=att.filename,
                        reason=REASON_UNSUPPORTED,
                    )
                )
                continue
            try:
                result = await self.extraction.extract(uri)
            except (ValueError, KeyError) as exc:
                unreadable.append(
                    UnreadableAttachment(
                        index=att.index,
                        filename=att.filename,
                        reason=f"extraction_failed: {type(exc).__name__}",
                    )
                )
                continue
            verdict = dispose_signals(
                signals_from_result(
                    result,
                    candidate_doctypes=list(policy.candidate_doctypes),
                )
            )
            extracted.append((att, uri, result, verdict))

        # Phase 2 — persist the bytes leg-scoped (an ArtifactRef by reference).
        refs: list[ArtifactRef] = []
        for att, uri, _result, _verdict in extracted:
            submission = DocumentSubmission(
                doc_id=f"email-{key}-{att.index}",
                artifact_name=uri,
                artifact=ArtifactRef(
                    uri=uri, mime_type=att.mime_type, name=att.filename
                ),
            )
            version = await persist_submission(
                self.artifact_store,
                party=leg.party,
                context_id=leg.context_id,
                submission=submission,
                resolver=lambda _uri, att=att: (att.data, att.mime_type),
            )
            refs.append(
                ArtifactRef(
                    uri=scoped_uri(
                        self.base_url,
                        context_id=leg.context_id,
                        filename=att.filename,
                        version=version,
                    ),
                    mime_type=att.mime_type,
                    name=att.filename,
                )
                if version is not None
                else submission.artifact
            )

        # Phase 3 — apply to a FRESH read of the RFP, then put (no await in between).
        # The ledger entries depend only on the extraction + the persisted refs, so a
        # retry reuses them: phases 1-2 are never redone.
        entries = [
            (
                att,
                project_ledger_entry(
                    doc_id=f"email-{key}-{att.index}",
                    result=result,
                    verdict=verdict,
                    artifact=ref,
                ),
            )
            for (att, _uri, result, verdict), ref in zip(extracted, refs, strict=True)
        ]
        doc_ids = {entry.doc_id for _att, entry in entries}
        for attempt in range(1, _STALE_RETRIES + 1):
            now = self.board.clock()
            current = self.board.rfps.get(rfp.rfp_id)
            if current is None:
                return await self._quarantine(key, REASON_NO_TOKEN, **meta)
            stored = {e.doc_id: e for e in current.entries if e.doc_id in doc_ids}
            if stored:
                # Re-file guard: another replica — or a crash between ``board.put``
                # and the marker — already filed this mail. Never file it twice; the
                # marker reports what the RFP actually holds.
                _LOG.info(
                    "email %s: already filed on RFP %s; writing the marker only",
                    key,
                    current.rfp_id,
                )
                rfp = current
                filed = [
                    self._filed(att, stored.get(entry.doc_id, entry), policy)
                    for att, entry in entries
                ]
                break
            rfp = current
            if any(
                c.party == leg.party and c.status is RfpItemStatus.invited
                for c in rfp.cells
            ):
                rfp = acknowledge(rfp, leg.party, now=now)
            for _att, entry in entries:
                rfp = receive(rfp, leg.party, entry, now=now, policy=policy)
            for item in unreadable:
                rfp = note_unreadable(
                    rfp, leg.party, filename=item.filename, reason=item.reason, now=now
                )
            try:
                self.board.put(rfp)
            except StaleExchangeWrite:
                # Compare-and-set lost to a concurrent writer: redo read → apply → put.
                # Exhausted → propagate (no marker; the key stays pending for the
                # next drain tick).
                if attempt == _STALE_RETRIES:
                    raise
                _LOG.info(
                    "email %s: RFP %s changed concurrently; retrying", key, rfp.rfp_id
                )
                continue
            filed = [self._filed(att, entry, policy) for att, entry in entries]
            break

        outcome = MailOutcome(
            key=key,
            status="filed",
            reason=None if mail.attachments else REASON_NO_ATTACHMENTS,
            rfp_id=rfp.rfp_id,
            party=leg.party,
            filed=filed,
            unreadable=unreadable,
            **meta,
        )
        await asyncio.to_thread(
            self.port.mark_processed, key, outcome.model_dump(mode="json")
        )
        return outcome


async def _read_capped(request: Request, limit: int) -> bytes | None:
    """The request body, or ``None`` once it exceeds ``limit`` (streamed, never buffered past it)."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        return None
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


def oversize_key(message_id: str) -> str:
    """The marker key for a Graph message refused as oversize (never landed)."""
    return hashlib.sha256(f"graph:{message_id}".encode()).hexdigest()[:32]


# -- board-owning host wiring (S0-bug-3) -------------------------------------------- #


def drain_interval_from_env() -> float:
    """``BRIDGE_EMAIL_DRAIN_INTERVAL`` in seconds (unset/blank → 30; ``0`` → no loop).

    Negative, non-numeric or non-finite → ``ValueError`` — a malformed value fails at
    app build, never silently disables (or hot-loops) the drain.
    """
    raw = os.environ.get(DRAIN_INTERVAL_ENV, "").strip()
    if not raw:
        return DEFAULT_DRAIN_INTERVAL
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(
            f"{DRAIN_INTERVAL_ENV} must be a number of seconds; got {raw!r}"
        ) from None
    if not math.isfinite(value) or value < 0:
        raise ValueError(
            f"{DRAIN_INTERVAL_ENV} must be a finite, non-negative number of seconds; "
            f"got {raw!r}"
        )
    return value


def _landing_configured() -> bool:
    """Whether the resolved ``email_ingress`` adapter has somewhere to read mail from."""
    local = resolve_mode(Port.email_ingress) is PortMode.local
    env = EMAIL_DIR_ENV if local else EMAIL_BUCKET_ENV
    return bool(os.environ.get(env, "").strip())


def email_processor_from_env(
    board: RfpBoard,
    *,
    extraction: Any = None,
    artifact_store: Any = None,
    base_url: str | None = None,
) -> EmailProcessor | None:
    """The env-wired processor for the host that owns ``board`` — or ``None``.

    ``None`` when email intake is not enabled (``BRIDGE_EMAIL_MAILBOX`` blank) or there
    is no landing location for the resolved ``email_ingress`` mode (local:
    ``BRIDGE_EMAIL_DIR``; gcp: ``BRIDGE_EMAIL_BUCKET``) — mailbox + secret alone only
    shows reply addresses. With both set, a missing ``BRIDGE_EMAIL_TOKEN_SECRET``
    raises ``RuntimeError`` (fail loud at build, never a default secret). Only call this
    on the board that is the source of truth (a durable shared exchange store, or a host
    that opens its RFPs onto this board — see the module docstring).
    """
    if not os.environ.get(MAILBOX_ENV, "").strip() or not _landing_configured():
        return None
    return EmailProcessor(
        board=board,
        extraction=extraction,
        artifact_store=artifact_store,
        base_url=base_url,
    )


@contextlib.asynccontextmanager
async def email_drain_loop(
    processor: EmailProcessor, interval: float
) -> AsyncIterator[None]:
    """Drain ``processor`` every ``interval`` seconds while the context is open.

    Meant for a host ``lifespan``. ``interval <= 0`` starts nothing (drain on demand
    only). A failed drain is logged and retried on the next tick — the key stays
    pending, never crashes the host; ``drain`` holds its own lock, so this loop and
    ``POST /email/drain`` serialize. On exit the task is cancelled and awaited.
    """
    if interval <= 0:
        yield
        return

    async def _loop() -> None:
        while True:
            await asyncio.sleep(interval)
            try:
                await processor.drain()
            except Exception:  # retried next tick; CancelledError still propagates
                _LOG.exception("periodic email drain failed; will retry next tick")

    task = asyncio.create_task(_loop(), name="bridge-email-drain")
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def mount_email_drain_routes(
    app: FastAPI, processor: EmailProcessor, *, port: EmailIngressPort | None = None
) -> None:
    """Bind ``POST /email/drain`` + ``GET /email/outcomes/{key}`` for ``processor``.

    Only on the board-owning host (the outcome markers carry sender, subject, RFP and
    carrier). ``port`` (default ``processor.port``) is where the markers are read.
    """
    marker_port = port if port is not None else processor.port

    @app.post("/email/drain")
    async def drain() -> list[dict[str, Any]]:
        outcomes = await processor.drain()
        return [o.model_dump(mode="json") for o in outcomes]

    @app.get("/email/outcomes/{key}")
    async def outcome(key: str) -> Any:
        if not _KEY_RE.fullmatch(key):
            return Response(status_code=404)
        marker = await run_in_threadpool(marker_port.processed, key)
        if marker is None:
            return Response(status_code=404)
        return marker


def build_email_app(
    *,
    port: EmailIngressPort | None = None,
    graph: GraphMailClient | None = None,
    client_state: str | None = None,
    processor: EmailProcessor | None = None,
    tenant_id: str | None = None,
) -> FastAPI:
    """Build the standalone email intake host (webhook + optional drain).

    ``graph`` binds the Graph webhook (requires ``client_state``); ``processor`` binds
    ``POST /email/drain``, ``GET /email/outcomes/{key}`` and a background drain after
    each landing (a webhook-only host serves neither read route). The A2A server
    holds no RFP state, so this is NOT mounted on it — it is its own host (deployed:
    webhook-only, ``serve_email_webhook_container``).
    """
    if graph is not None and not client_state:
        raise ValueError("build_email_app(graph=...) requires client_state")
    if graph is not None:
        max_mail_bytes()  # a malformed BRIDGE_EMAIL_MAX_BYTES fails at startup
    if port is None:
        port = processor.port if processor is not None else _default_port()
    app = FastAPI(title="Document Bridge — email intake")

    async def _background_drain() -> None:
        try:
            await processor.drain()
        except Exception:  # retried by the next drain; never crash the host
            _LOG.exception("background email drain failed; will retry on next drain")

    @app.post(NOTIFICATIONS_PATH)
    async def graph_notifications(
        request: Request,
        background: BackgroundTasks,
        validationToken: str | None = None,
    ) -> Response:
        if graph is None:
            return Response(status_code=404)
        if validationToken is not None:
            if len(validationToken) > MAX_VALIDATION_TOKEN:
                return Response(status_code=400)
            return PlainTextResponse(validationToken, status_code=200)
        body = await _read_capped(request, MAX_NOTIFICATION_BYTES)
        if body is None:
            return Response(status_code=413)
        try:
            payload = json.loads(body)
            items = payload["value"]
            if not isinstance(items, list):
                raise TypeError
        except (ValueError, KeyError, TypeError):
            return Response(status_code=400)

        landed = duplicates = ignored = 0
        for item in items:
            if not isinstance(item, dict):
                ignored += 1
                continue
            state = item.get("clientState")
            resource = item.get("resourceData")
            message_id = resource.get("id") if isinstance(resource, dict) else None
            if not isinstance(state, str) or not hmac.compare_digest(
                state.encode(), client_state.encode()
            ):
                _LOG.warning("email notification ignored: clientState mismatch")
                ignored += 1
                continue
            if tenant_id and item.get("tenantId") != tenant_id:
                _LOG.warning("email notification ignored: tenantId mismatch")
                ignored += 1
                continue
            if not isinstance(message_id, str) or not MESSAGE_ID_RE.fullmatch(
                message_id
            ):
                _LOG.warning("email notification ignored: invalid message id")
                ignored += 1
                continue
            try:
                raw = await run_in_threadpool(graph.fetch_mime, message_id)
            except MessageTooLarge:
                # Oversize: never landed, but recorded — a 5xx would make Graph retry forever.
                # ONLY this refusal is acked; every other failure is a 503 (no mail lost).
                key = oversize_key(message_id)
                outcome = MailOutcome(key=key, status="quarantined", reason="oversize")
                _LOG.warning("email %s quarantined: oversize (graph id)", key)
                try:
                    await run_in_threadpool(
                        port.mark_processed, key, outcome.model_dump(mode="json")
                    )
                except Exception:
                    _LOG.exception(
                        "email oversize marker failed; asking Graph to retry"
                    )
                    return Response(status_code=503)
                ignored += 1
                continue
            except GraphError as exc:
                _LOG.warning("email fetch failed (%s); asking Graph to retry", exc)
                return Response(status_code=503)
            key = landing_key(raw)
            try:
                fresh = await run_in_threadpool(port.land, key, raw)
            except Exception:
                _LOG.exception("email landing failed; asking Graph to retry")
                return Response(status_code=503)
            if fresh:
                landed += 1
            else:
                duplicates += 1
        if processor is not None and landed:
            background.add_task(_background_drain)
        return JSONResponse(
            {"landed": landed, "duplicates": duplicates, "ignored": ignored},
            status_code=202,
        )

    # The drain and the outcome read live only on the board-owning host. The deployed
    # webhook is PUBLIC (allUsers): it must not serve markers (sender, subject, RFP,
    # carrier) to anyone holding a key — and its create-only SA could not read them.
    if processor is not None:
        mount_email_drain_routes(app, processor, port=port)

    return app


def serve_email_webhook_container() -> None:  # pragma: no cover
    """Cloud Run entrypoint for the PUBLIC, webhook-only email service (S0-arch-13).

    Graph → this service only: ``graph = GraphMailClient.from_env()``, ``client_state``
    from ``BRIDGE_EMAIL_CLIENT_STATE`` (missing → ``RuntimeError``), the port from its
    adapter (``BRIDGE_PORT_EMAIL_INGRESS=gcp`` → the create-only GCS landing bucket),
    and NO processor — this service holds no board, no runtime grants. ``tenantId`` is
    checked when ``BRIDGE_EMAIL_GRAPH_TENANT`` is a tenant GUID (a domain-name tenant
    cannot be compared with the notification's GUID). Binds ``0.0.0.0:$PORT`` like
    ``bridge.edge.server.serve_container``.
    """
    import uvicorn

    graph = GraphMailClient.from_env()
    tenant = graph.tenant_id if _GUID_RE.fullmatch(graph.tenant_id) else None
    app = build_email_app(
        graph=graph, client_state=client_state_from_env(), tenant_id=tenant
    )
    port = int(os.environ.get("PORT", "8080"))
    # 0.0.0.0 is required by Cloud Run; this service is public by design (Graph cannot
    # present a Google identity) and authenticates each notification by clientState.
    uvicorn.run(app, host="0.0.0.0", port=port)


__all__ = [
    "DEFAULT_DRAIN_INTERVAL",
    "DRAIN_INTERVAL_ENV",
    "EmailProcessor",
    "FiledAttachment",
    "MailOutcome",
    "UnreadableAttachment",
    "build_email_app",
    "drain_interval_from_env",
    "email_drain_loop",
    "email_processor_from_env",
    "mount_email_drain_routes",
    "oversize_key",
    "serve_email_webhook_container",
]
