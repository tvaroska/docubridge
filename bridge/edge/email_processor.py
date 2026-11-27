"""Inbound email processor: drains landed RFC 5322 mail onto the durable ``RfpBoard``."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import TYPE_CHECKING, Literal, Protocol

from pydantic import BaseModel, ConfigDict

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
from bridge.email_ingress.mime import (
    InboundMail,
    MailAttachment,
    max_mail_bytes,
    parse_mime,
)
from bridge.exchange_store import StaleExchangeWrite
from bridge.extraction.resolve import SUFFIX_MIME
from bridge.ports import EmailIngressPort, Port, get_adapter, resolve_mode
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

    from bridge.dashboard.board import RfpBoard
    from bridge.disposition import DispositionVerdict
    from contract.extraction import ExtractionResult

_LOG = logging.getLogger("bridge.edge.email_ingress")

MAILBOX_ENV = "BRIDGE_EMAIL_MAILBOX"
_DMARC_FAIL_RE = re.compile(r"\bdmarc\s*=\s*fail\b", re.IGNORECASE)
_STALE_RETRIES = 3

READABLE_MIME_TYPES = frozenset(SUFFIX_MIME.values())

REASON_SENDER_AUTH = "sender_authentication_failed"
REASON_NO_TOKEN = "no_exchange_token"  # noqa: S105 - outcome reason token, not a secret
REASON_AMBIGUOUS = "ambiguous_token"
REASON_NO_ATTACHMENTS = "no_attachments"
REASON_UNSUPPORTED = "unsupported_media_type"
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
    """What happened to one landed mail — the processed-marker JSON without body or bytes."""

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
    """Drain landed mail onto the ``RfpBoard`` with compare-and-set retry protection."""

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
        max_mail_bytes()
        self.board = board
        self.port = port or _default_port()
        self.mailbox = mailbox or _mailbox_from_env()
        intake_address(self.mailbox, "a" * 16)
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

    def token_for(self, rfp_id: str, party: str) -> str:
        """Extract the intake correlation token from an inbound email recipient address."""
        return mint_token(self._secret, rfp_id, party)

    def reply_address(self, rfp_id: str, party: str) -> str:
        """Return the plus-addressed intake address a carrier replies to for this leg."""
        return intake_address(self.mailbox, self.token_for(rfp_id, party))

    def reply_subject_tag(self, rfp_id: str, party: str) -> str:
        """Return the ``[ref:<token>]`` subject fallback for this leg."""
        return subject_tag(self.token_for(rfp_id, party))

    @property
    def intake(self) -> IntakeAddressing:
        """Return the reply addressing this processor correlates on (mailbox + token key)."""
        return IntakeAddressing(mailbox=self.mailbox, secret=self._secret)

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
        return None  # pragma: no cover

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
