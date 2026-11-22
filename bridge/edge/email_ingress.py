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
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol

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
from bridge.email_ingress.mime import (
    InboundMail,
    MailAttachment,
    landing_key,
    max_mail_bytes,
    parse_mime,
)
from bridge.exchange_store import StaleExchangeWrite
from bridge.extraction.resolve import SUFFIX_MIME
from bridge.ports import (
    EmailIngressPort,
    Port,
    PortMode,
    get_adapter,
    resolve_mode,
)
from bridge.ports.gcp import EMAIL_BUCKET_ENV
from bridge.ports.local import EMAIL_DIR_ENV
from bridge.ports.protocols import EMAIL_KEY_PATTERN
from bridge.rfp import (
    RfpCollection,
    RfpLeg,
    RfpPolicy,
    acknowledge,
    note_unreadable,
    receive,
)
from bridge.signals import dispose_signals, signals_from_result
from contract.enums import RfpItemStatus
from contract.wire import ArtifactRef, LedgerEntry

if TYPE_CHECKING:
    from google.adk.artifacts import BaseArtifactService

    # Annotation-only: importing ``bridge.dashboard`` at module top would run
    # ``bridge.dashboard.server``, which lazily binds this module — a fresh-process
    # ``import bridge.edge.email_ingress`` must not hit a partially-initialized cycle.
    from bridge.dashboard.board import RfpBoard
    from bridge.disposition import DispositionVerdict
    from bridge.knowledge_catalog import CatalogRole
    from contract.extraction import ExtractionResult

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
REASON_NO_TOKEN = "no_exchange_token"  # noqa: S105 - outcome reason token, not a secret
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


class _ExtractorLike(Protocol):
    async def extract(self, artifact_name: str) -> ExtractionResult: ...


class EmailProcessor:
    """Drain landed mail onto the ``RfpBoard`` (see the module docstring for the order)."""

    def __init__(  # noqa: PLR0913 - keyword-only processor collaborators
        self,
        *,
        board: RfpBoard,
        port: EmailIngressPort | None = None,
        mailbox: str | None = None,
        secret: bytes | str | None = None,
        extraction: _ExtractorLike | None = None,
        artifact_store: BaseArtifactService | None = None,
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
        mail: InboundMail | None = None,
    ) -> MailOutcome:
        outcome = MailOutcome(
            key=key,
            status="quarantined",
            reason=reason,
            sender=mail.sender if mail is not None else None,
            subject=mail.subject if mail is not None else None,
            skipped_inline=mail.skipped_inline if mail is not None else 0,
        )
        _LOG.warning("email %s quarantined: %s (from %s)", key, reason, outcome.sender)
        await asyncio.to_thread(
            self.port.mark_processed, key, outcome.model_dump(mode="json")
        )
        return outcome

    def _filed(
        self, att: MailAttachment, entry: LedgerEntry, policy: RfpPolicy
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

    def _resolve_mail_target(
        self, mail: InboundMail
    ) -> tuple[RfpCollection, RfpLeg, RfpPolicy] | str:
        if any(_DMARC_FAIL_RE.search(v) for v in mail.auth_results):
            return REASON_SENDER_AUTH
        tokens = find_tokens(mail, self.mailbox)
        try:
            match = correlate(tokens, list(self.board.rfps.values()), self._secret)
        except AmbiguousToken:
            return REASON_AMBIGUOUS
        if match is None:
            return REASON_NO_TOKEN
        rfp, leg = match
        try:
            policy = self.board.policy_for(rfp.process)
        except ValueError:
            return REASON_PROCESS_UNAVAILABLE
        return rfp, leg, policy

    async def _extract_attachments(
        self, key: str, mail: InboundMail, policy: RfpPolicy
    ) -> tuple[
        list[tuple[MailAttachment, str, ExtractionResult, DispositionVerdict]],
        list[UnreadableAttachment],
    ]:
        extracted: list[
            tuple[MailAttachment, str, ExtractionResult, DispositionVerdict]
        ] = []
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
                if getattr(self.extraction, "accepts_inline_document", False):
                    from bridge.extraction.resolve import ResolvedDocument

                    doc = ResolvedDocument(data=att.data, mime_type=att.mime_type)
                    result = await self.extraction.extract(uri, inline_document=doc)  # type: ignore[call-arg]
                else:
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
        return extracted, unreadable

    async def _persist_attachments(
        self,
        key: str,
        leg: RfpLeg,
        extracted: list[
            tuple[MailAttachment, str, ExtractionResult, DispositionVerdict]
        ],
    ) -> list[ArtifactRef]:
        refs: list[ArtifactRef] = []
        for att, uri, _result, _verdict in extracted:
            raw_ref = ArtifactRef(uri=uri, mime_type=att.mime_type, name=att.filename)
            submission = DocumentSubmission(
                doc_id=f"email-{key}-{att.index}",
                artifact_name=uri,
                artifact=raw_ref,
            )

            def _resolve_att(
                _uri: str,
                data: bytes = att.data,
                mime: str = att.mime_type,
            ) -> tuple[bytes, str]:
                return data, mime

            version = await persist_submission(
                self.artifact_store,
                party=leg.party,
                context_id=leg.context_id,
                submission=submission,
                resolver=_resolve_att,
                caller_principal="system:email-ingress",
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
                else raw_ref
            )
        return refs

    def _apply_filing_cas(
        self,
        key: str,
        target: tuple[RfpCollection, RfpLeg, RfpPolicy],
        entries: list[tuple[MailAttachment, LedgerEntry]],
        unreadable: list[UnreadableAttachment],
    ) -> list[FiledAttachment] | None:
        rfp, leg, policy = target
        doc_ids = {entry.doc_id for _att, entry in entries}
        for attempt in range(1, _STALE_RETRIES + 1):
            now = self.board.clock()
            current = self.board.rfps.get(rfp.rfp_id)
            if current is None:
                return None
            stored = {e.doc_id: e for e in current.entries if e.doc_id in doc_ids}
            if stored:
                _LOG.info(
                    "email %s: already filed on RFP %s; writing the marker only",
                    key,
                    current.rfp_id,
                )
                return [
                    self._filed(att, stored.get(entry.doc_id, entry), policy)
                    for att, entry in entries
                ]
            updated = current
            if any(
                c.party == leg.party and c.status is RfpItemStatus.invited
                for c in updated.cells
            ):
                updated = acknowledge(updated, leg.party, now=now)
            for _att, entry in entries:
                updated = receive(updated, leg.party, entry, now=now, policy=policy)
            for item in unreadable:
                updated = note_unreadable(
                    updated,
                    leg.party,
                    filename=item.filename,
                    reason=item.reason,
                    now=now,
                )
            try:
                self.board.put(updated)
            except StaleExchangeWrite:
                if attempt == _STALE_RETRIES:
                    raise
                _LOG.info(
                    "email %s: RFP %s changed concurrently; retrying",
                    key,
                    updated.rfp_id,
                )
                continue
            return [self._filed(att, entry, policy) for att, entry in entries]
        return None  # pragma: no cover - loop always returns or raises

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
        resolved = self._resolve_mail_target(mail)
        if isinstance(resolved, str):
            return await self._quarantine(key, resolved, mail=mail)
        rfp, leg, policy = resolved
        extracted, unreadable = await self._extract_attachments(key, mail, policy)
        refs = await self._persist_attachments(key, leg, extracted)
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
        filed = self._apply_filing_cas(key, resolved, entries, unreadable)
        if filed is None:
            return await self._quarantine(key, REASON_NO_TOKEN, mail=mail)
        outcome = MailOutcome(
            key=key,
            status="filed",
            reason=None if mail.attachments else REASON_NO_ATTACHMENTS,
            rfp_id=rfp.rfp_id,
            party=leg.party,
            sender=mail.sender,
            subject=mail.subject,
            skipped_inline=mail.skipped_inline,
            filed=filed,
            unreadable=unreadable,
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
    extraction: _ExtractorLike | None = None,
    artifact_store: BaseArtifactService | None = None,
    base_url: str | None = None,
) -> EmailProcessor | None:
    """Return the env-wired processor for the board-owning host, or ``None``."""
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
    """Drain ``processor`` periodically while open (``interval <= 0`` disables polling)."""
    if interval <= 0:
        yield
        return

    async def _loop() -> None:
        while True:
            await asyncio.sleep(interval)
            try:
                await processor.drain()
            except Exception:  # noqa: BLE001 - periodic loop must log and retry on next tick
                _LOG.exception("periodic email drain failed; will retry next tick")

    task = asyncio.create_task(_loop(), name="bridge-email-drain")
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def mount_email_drain_routes(
    app: FastAPI,
    processor: EmailProcessor,
    *,
    port: EmailIngressPort | None = None,
    principal_roles: dict[str, CatalogRole] | None = None,
    require_auth: bool = False,
) -> None:
    """Bind gated ``POST /email/drain`` and ``GET /email/outcomes/{key}`` onto ``app``."""
    from bridge.user_interaction.demo_session import email_drain_auth_dependency

    marker_port = port if port is not None else processor.port
    drain_dep = email_drain_auth_dependency(
        principal_roles=principal_roles, require_auth=require_auth
    )
    outcome_dep = email_drain_auth_dependency(
        principal_roles=principal_roles,
        require_auth=require_auth,
        allow_operator_read=True,
    )

    @app.post("/email/drain", dependencies=[drain_dep])
    async def drain() -> list[dict[str, Any]]:
        return [o.model_dump(mode="json") for o in await processor.drain()]

    @app.get("/email/outcomes/{key}", response_model=None, dependencies=[outcome_dep])
    async def outcome(key: str) -> Response | dict[str, Any]:
        if not _KEY_RE.fullmatch(key):
            return Response(status_code=404)
        marker = await run_in_threadpool(marker_port.processed, key)
        return Response(status_code=404) if marker is None else marker


def _validate_graph_item(
    item: object, *, client_state: str, tenant_id: str | None
) -> str | None:
    if not isinstance(item, dict):
        return None
    state = item.get("clientState")
    resource = item.get("resourceData")
    message_id = resource.get("id") if isinstance(resource, dict) else None
    if not isinstance(state, str) or not hmac.compare_digest(
        state.encode(), client_state.encode()
    ):
        _LOG.warning("email notification ignored: clientState mismatch")
        return None
    if tenant_id and item.get("tenantId") != tenant_id:
        _LOG.warning("email notification ignored: tenantId mismatch")
        return None
    if not isinstance(message_id, str) or not MESSAGE_ID_RE.fullmatch(message_id):
        _LOG.warning("email notification ignored: invalid message id")
        return None
    return message_id


async def _ingest_graph_message(
    message_id: str, *, graph: GraphMailClient, port: EmailIngressPort
) -> Literal["landed", "duplicate", "ignored", "retry"]:
    try:
        raw = await run_in_threadpool(graph.fetch_mime, message_id)
    except MessageTooLarge:
        key = oversize_key(message_id)
        outcome = MailOutcome(key=key, status="quarantined", reason="oversize")
        _LOG.warning("email %s quarantined: oversize (graph id)", key)
        try:
            await run_in_threadpool(
                port.mark_processed, key, outcome.model_dump(mode="json")
            )
        except Exception:  # noqa: BLE001 - marker write error asks Graph to retry via 503
            _LOG.exception("email oversize marker failed; asking Graph to retry")
            return "retry"
        return "ignored"
    except GraphError as exc:
        _LOG.warning("email fetch failed (%s); asking Graph to retry", exc)
        return "retry"
    key = landing_key(raw)
    try:
        fresh = await run_in_threadpool(port.land, key, raw)
    except Exception:  # noqa: BLE001 - landing write error asks Graph to retry via 503
        _LOG.exception("email landing failed; asking Graph to retry")
        return "retry"
    return "landed" if fresh else "duplicate"


@dataclass(frozen=True)
class _WebhookDeps:
    port: EmailIngressPort
    graph: GraphMailClient | None
    client_state: str | None
    processor: EmailProcessor | None
    tenant_id: str | None


async def _run_background_drain(processor: EmailProcessor) -> None:
    try:
        await processor.drain()
    except Exception:  # noqa: BLE001 - background drain retries on next webhook/tick
        _LOG.exception("background email drain failed; will retry on next drain")


async def _handle_graph_notifications(
    request: Request,
    background: BackgroundTasks,
    validation_token: str | None,
    deps: _WebhookDeps,
) -> Response:
    if deps.graph is None or not deps.client_state:
        return Response(status_code=404)
    if validation_token is not None:
        return (
            Response(status_code=400)
            if len(validation_token) > MAX_VALIDATION_TOKEN
            else PlainTextResponse(validation_token, status_code=200)
        )
    body = await _read_capped(request, MAX_NOTIFICATION_BYTES)
    if body is None:
        return Response(status_code=413)
    try:
        items = json.loads(body)["value"]
        if not isinstance(items, list):
            raise TypeError
    except (ValueError, KeyError, TypeError):
        return Response(status_code=400)

    counts = {"landed": 0, "duplicates": 0, "ignored": 0}
    for item in items:
        message_id = _validate_graph_item(
            item, client_state=deps.client_state, tenant_id=deps.tenant_id
        )
        if message_id is None:
            counts["ignored"] += 1
            continue
        verdict = await _ingest_graph_message(
            message_id, graph=deps.graph, port=deps.port
        )
        if verdict == "retry":
            return Response(status_code=503)
        counts["duplicates" if verdict == "duplicate" else verdict] += 1
    if deps.processor is not None and counts["landed"]:
        background.add_task(_run_background_drain, deps.processor)
    return JSONResponse(counts, status_code=202)


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
    resolved_port = (
        port
        if port is not None
        else (processor.port if processor is not None else _default_port())
    )
    deps = _WebhookDeps(
        port=resolved_port,
        graph=graph,
        client_state=client_state,
        processor=processor,
        tenant_id=tenant_id,
    )
    app = FastAPI(title="Document Bridge — email intake")

    @app.post(NOTIFICATIONS_PATH)
    async def graph_notifications(
        request: Request,
        background: BackgroundTasks,
        validationToken: str | None = None,
    ) -> Response:
        return await _handle_graph_notifications(
            request, background, validationToken, deps
        )

    if processor is not None:
        mount_email_drain_routes(app, processor, port=resolved_port)
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
    uvicorn.run(app, host="0.0.0.0", port=port)  # noqa: S104 - Cloud Run requires 0.0.0.0 bind


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
