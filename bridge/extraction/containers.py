"""Recursive submission container and email envelope unpacker for solicited Collect legs.

Unpacks nested ``.zip`` archives, RFC-5322 ``.eml`` messages, and OLE2/CFBF ``.msg``
envelopes using pure Python stdlib with bounded recursion/decompression guards,
SHA-256 leaf deduplication, envelope provenance chains, and structured HTML reply-table
advisory extraction for Sense-B requirement updates.
"""

from __future__ import annotations

import email
import hashlib
import io
import mimetypes
import posixpath
import re
import zipfile
from dataclasses import dataclass
from email import policy as email_policy
from email.message import EmailMessage, Message, MIMEPart

from pydantic import BaseModel, ConfigDict, Field

from bridge.extraction.container_advisories import (
    AdvisoryKind as AdvisoryKind,
)
from bridge.extraction.container_advisories import (
    ReplyItemAdvisory as ReplyItemAdvisory,
)
from bridge.extraction.container_advisories import (
    apply_advisories_to_requirements as apply_advisories_to_requirements,
)
from bridge.extraction.container_advisories import (
    parse_html_reply_advisories as parse_html_reply_advisories,
)
from bridge.extraction.ole2_msg import (
    OLE2_MAGIC as OLE2_MAGIC,
)
from bridge.extraction.ole2_msg import (
    Ole2CompoundReader,
    Ole2DirEntry,
    decode_mapi_binary,
    decode_mapi_text,
)


class ExtractedEnvelopeBody(BaseModel):
    """Extracted metadata, body text, and reply-table advisories from an email envelope."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    envelope_id: str
    filename: str
    parent_envelope_id: str | None = None
    envelope_chain: tuple[str, ...] = ()
    subject: str = ""
    sender: str | None = None
    recipients: tuple[str, ...] = ()
    plain_text: str = ""
    html_text: str = ""
    advisories: tuple[ReplyItemAdvisory, ...] = ()


class UnpackedArtifact(BaseModel):
    """Leaf document artifact extracted from a submission container or envelope."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_id: str
    filename: str
    mime_type: str
    data: bytes
    sha256: str
    size_bytes: int
    context_id: str
    parent_envelope_id: str | None = None
    envelope_chain: tuple[str, ...] = ()
    depth: int = Field(default=0, ge=0)


class ContainerUnpackResult(BaseModel):
    """Complete result of recursively unpacking a solicited submission container."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    context_id: str
    root_envelope_id: str
    artifacts: tuple[UnpackedArtifact, ...] = ()
    envelopes: tuple[ExtractedEnvelopeBody, ...] = ()
    advisories: tuple[ReplyItemAdvisory, ...] = ()
    skipped_duplicates: int = Field(default=0, ge=0)
    duplicate_sha256s: tuple[str, ...] = ()


def _sanitize_member_filename(raw_name: str, fallback: str = "attachment.bin") -> str:
    """Strip directory traversal components and normalize container member filenames."""
    cleaned = (raw_name or "").replace("\\", "/").strip()
    if not cleaned:
        return fallback
    norm = posixpath.normpath(cleaned).lstrip("/")
    base = posixpath.basename(norm)
    if not base or base in (".", ".."):
        return fallback
    return re.sub(r"[\x00-\x1f\x7f]+", "_", base)


_EXT_MIME: dict[str, str] = {
    ".msg": "application/vnd.ms-outlook",
    ".eml": "message/rfc822",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xlsm": "application/vnd.ms-excel.sheet.macroenabled.12",
    ".rtf": "application/rtf",
}


def _guess_mime(filename: str, explicit_mime: str | None = None) -> str:
    """Resolve a canonical MIME type from an explicit header or filename suffix."""
    if explicit_mime and explicit_mime.strip():
        lowered = explicit_mime.strip().split(";", 1)[0].strip().lower()
        if lowered and lowered != "application/octet-stream":
            return lowered
    lower_name = filename.lower()
    for ext, mime in _EXT_MIME.items():
        if lower_name.endswith(ext):
            return mime
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or "application/octet-stream"


@dataclass(frozen=True)
class _NodeContext:
    filename: str
    parent_envelope_id: str | None
    envelope_chain: tuple[str, ...]
    depth: int
    mime_type: str = "application/octet-stream"


class _UnpackBudget:
    """Mutable recursion and decompression budget shared across nested unpack steps."""

    def __init__(  # noqa: PLR0913 - keyword-only unpack limits
        self,
        *,
        context_id: str,
        max_depth: int,
        max_files: int,
        max_entry_bytes: int,
        max_total_bytes: int,
        max_compression_ratio: float,
        deduplicate: bool,
    ) -> None:
        self.context_id = context_id
        self.max_depth = max_depth
        self.max_files = max_files
        self.max_entry_bytes = max_entry_bytes
        self.max_total_bytes = max_total_bytes
        self.max_compression_ratio = max_compression_ratio
        self.deduplicate = deduplicate

        self.total_bytes = 0
        self.file_counter = 0
        self.envelope_counter = 0
        self.seen_sha256: set[str] = set()
        self.skipped_duplicates = 0
        self.duplicate_hashes: list[str] = []
        self.artifacts: list[UnpackedArtifact] = []
        self.envelopes: list[ExtractedEnvelopeBody] = []
        self.advisories: list[ReplyItemAdvisory] = []

    def next_envelope_id(self) -> str:
        self.envelope_counter += 1
        return f"{self.context_id}:env-{self.envelope_counter}"

    def charge_bytes(self, amount: int) -> None:
        if amount > self.max_entry_bytes:
            raise ValueError(
                f"Container member size {amount} exceeds max_entry_bytes={self.max_entry_bytes}"
            )
        self.total_bytes += amount
        if self.total_bytes > self.max_total_bytes:
            raise ValueError(
                f"Unpacked byte total {self.total_bytes} exceeds max_total_bytes={self.max_total_bytes}"
            )

    def record_leaf(self, data: bytes, ctx: _NodeContext) -> None:
        self.charge_bytes(len(data))
        digest = hashlib.sha256(data).hexdigest()
        if self.deduplicate and digest in self.seen_sha256:
            self.skipped_duplicates += 1
            self.duplicate_hashes.append(digest)
            return
        if len(self.artifacts) >= self.max_files:
            raise ValueError(
                f"Container exceeds max_files={self.max_files} extracted artifacts"
            )
        self.seen_sha256.add(digest)
        self.file_counter += 1
        self.artifacts.append(
            UnpackedArtifact(
                artifact_id=f"{self.context_id}:art-{self.file_counter}",
                filename=_sanitize_member_filename(ctx.filename),
                mime_type=ctx.mime_type,
                data=data,
                sha256=digest,
                size_bytes=len(data),
                context_id=self.context_id,
                parent_envelope_id=ctx.parent_envelope_id,
                envelope_chain=ctx.envelope_chain,
                depth=ctx.depth,
            )
        )


def _is_zip_payload(data: bytes, filename: str) -> bool:
    """Return True strictly for `.zip` archives (never `.docx`/`.xlsx` OOXML packages)."""
    lower = filename.lower()
    if lower.endswith((".docx", ".xlsx", ".xlsm", ".pptx")):
        return False
    return lower.endswith(".zip") or (
        data[:4] == b"PK\x03\x04" and not lower.endswith((".pdf", ".eml", ".msg"))
    )


def _is_msg_payload(data: bytes, filename: str) -> bool:
    """Return True when payload has OLE2 magic and `.msg` suffix (or MAPI root streams)."""
    if len(data) >= 8 and data[:8] == OLE2_MAGIC:
        lower = filename.lower()
        return not lower.endswith((".doc", ".xls", ".ppt"))
    return False


def _is_eml_payload(data: bytes, filename: str, mime_type: str) -> bool:
    """Return True when filename or MIME indicates an RFC-5322 `.eml` message."""
    if filename.lower().endswith(".eml") or mime_type == "message/rfc822":
        return True
    head = data[:512].lstrip()
    return head.startswith((b"From:", b"MIME-Version:", b"Subject:", b"Received:"))


def _unpack_ole2_attachment(
    reader: Ole2CompoundReader,
    att_dir: Ole2DirEntry,
    idx: int,
    parent_ctx: _NodeContext,
    budget: _UnpackBudget,
) -> None:
    att_streams: dict[str, bytes] = {}
    embedded_msg_storage: Ole2DirEntry | None = None
    for node in reader.list_children(att_dir):
        if node.obj_type == 2:
            att_streams[node.name] = reader.read_stream(node)
        elif node.obj_type == 1 and node.name.upper() == "__SUBSTG1.0_3701000D":
            embedded_msg_storage = node

    att_name = (
        decode_mapi_text(att_streams, "3707")
        or decode_mapi_text(att_streams, "3704")
        or f"attachment-{idx}.bin"
    )
    att_name = _sanitize_member_filename(att_name, fallback=f"attachment-{idx}.bin")
    att_mime = _guess_mime(att_name, decode_mapi_text(att_streams, "370E"))

    if embedded_msg_storage is not None:
        child_name = (
            att_name if att_name.lower().endswith(".msg") else f"{att_name}.msg"
        )
        _unpack_ole2_storage(
            reader,
            embedded_msg_storage,
            _NodeContext(
                filename=child_name,
                parent_envelope_id=parent_ctx.parent_envelope_id,
                envelope_chain=parent_ctx.envelope_chain,
                depth=parent_ctx.depth,
                mime_type="application/vnd.ms-outlook",
            ),
            budget,
        )
        return

    att_bytes = decode_mapi_binary(att_streams, "3701")
    if att_bytes is not None:
        _unpack_node(
            att_bytes,
            _NodeContext(
                filename=att_name,
                mime_type=att_mime,
                parent_envelope_id=parent_ctx.parent_envelope_id,
                envelope_chain=parent_ctx.envelope_chain,
                depth=parent_ctx.depth,
            ),
            budget,
        )


def _unpack_ole2_storage(
    reader: Ole2CompoundReader,
    storage_node: Ole2DirEntry,
    ctx: _NodeContext,
    budget: _UnpackBudget,
) -> None:
    """Extract MAPI headers, body, advisories, and child attachments from a `.msg` storage."""
    if ctx.depth > budget.max_depth:
        raise ValueError(
            f"Container nesting depth {ctx.depth} exceeds max_depth={budget.max_depth}"
        )

    env_id = budget.next_envelope_id()
    next_chain = (*ctx.envelope_chain, env_id)
    streams: dict[str, bytes] = {}
    attach_storages: list[Ole2DirEntry] = []
    for child in reader.list_children(storage_node):
        if child.obj_type == 2:
            streams[child.name] = reader.read_stream(child)
        elif child.obj_type == 1 and child.name.lower().startswith(
            "__attach_version1.0_"
        ):
            attach_storages.append(child)

    subject = decode_mapi_text(streams, "0037")
    sender = (
        decode_mapi_text(streams, "0C1F") or decode_mapi_text(streams, "0065") or None
    )
    to_raw = decode_mapi_text(streams, "0E04")
    recipients = tuple(r.strip() for r in re.split(r"[;,]", to_raw) if r.strip())
    plain_text = decode_mapi_text(streams, "1000")
    html_text = decode_mapi_text(streams, "1013")
    if not html_text:
        html_bin = decode_mapi_binary(streams, "1013")
        if html_bin:
            html_text = html_bin.decode("utf-8", errors="replace")

    advs = parse_html_reply_advisories(html_text, source_envelope_id=env_id)
    budget.envelopes.append(
        ExtractedEnvelopeBody(
            envelope_id=env_id,
            filename=_sanitize_member_filename(ctx.filename, fallback="message.msg"),
            parent_envelope_id=ctx.parent_envelope_id,
            envelope_chain=next_chain,
            subject=subject,
            sender=sender,
            recipients=recipients,
            plain_text=plain_text,
            html_text=html_text,
            advisories=advs,
        )
    )
    budget.advisories.extend(advs)

    child_parent_ctx = _NodeContext(
        filename="",
        parent_envelope_id=env_id,
        envelope_chain=next_chain,
        depth=ctx.depth + 1,
    )
    for idx, att_dir in enumerate(attach_storages, start=1):
        _unpack_ole2_attachment(reader, att_dir, idx, child_parent_ctx, budget)


def _unpack_eml_bytes(data: bytes, ctx: _NodeContext, budget: _UnpackBudget) -> None:
    """Parse an RFC-5322 `.eml` message, extract bodies/advisories, and recurse attachments."""
    if ctx.depth > budget.max_depth:
        raise ValueError(
            f"Container nesting depth {ctx.depth} exceeds max_depth={budget.max_depth}"
        )
    budget.charge_bytes(len(data))
    msg: EmailMessage = email.message_from_bytes(data, policy=email_policy.default)

    env_id = budget.next_envelope_id()
    next_chain = (*ctx.envelope_chain, env_id)
    subject = str(msg.get("Subject") or "").strip()
    sender = str(msg.get("From") or "").strip() or None
    raw_to = ",".join(
        str(v) for v in (*(msg.get_all("To") or ()), *(msg.get_all("Cc") or ()))
    )
    recipients = tuple(r.strip() for r in raw_to.split(",") if r.strip())

    plain_parts: list[str] = []
    html_parts: list[str] = []
    child_items: list[tuple[str, str, bytes]] = []

    if msg.is_multipart():
        for part in msg.iter_parts():
            _collect_eml_part(part, plain_parts, html_parts, child_items)
    else:
        ctype = msg.get_content_type()
        if ctype == "text/plain":
            plain_parts.append(str(msg.get_content()))
        elif ctype == "text/html":
            html_parts.append(str(msg.get_content()))

    plain_text = "\n".join(p.strip() for p in plain_parts if p.strip())
    html_text = "\n".join(h.strip() for h in html_parts if h.strip())
    advs = parse_html_reply_advisories(html_text, source_envelope_id=env_id)

    budget.envelopes.append(
        ExtractedEnvelopeBody(
            envelope_id=env_id,
            filename=_sanitize_member_filename(ctx.filename, fallback="message.eml"),
            parent_envelope_id=ctx.parent_envelope_id,
            envelope_chain=next_chain,
            subject=subject,
            sender=sender,
            recipients=recipients,
            plain_text=plain_text,
            html_text=html_text,
            advisories=advs,
        )
    )
    budget.advisories.extend(advs)

    for att_name, att_mime, att_bytes in child_items:
        _unpack_node(
            att_bytes,
            _NodeContext(
                filename=att_name,
                mime_type=att_mime,
                parent_envelope_id=env_id,
                envelope_chain=next_chain,
                depth=ctx.depth + 1,
            ),
            budget,
        )


def _collect_eml_part(
    part: MIMEPart[str, str],
    plain_parts: list[str],
    html_parts: list[str],
    child_items: list[tuple[str, str, bytes]],
) -> None:
    """Recursively classify MIME parts into inline bodies vs. child attachments."""
    ctype = part.get_content_type()
    disp = part.get_content_disposition()
    fname = part.get_filename()

    if ctype == "message/rfc822" and disp != "inline":
        payload = part.get_payload()
        first = payload[0] if isinstance(payload, list) and payload else None
        nested_bytes = (
            first.as_bytes() if isinstance(first, Message) else part.as_bytes()
        )
        child_items.append(
            (
                _sanitize_member_filename(fname or "nested.eml"),
                "message/rfc822",
                nested_bytes,
            )
        )
        return

    if part.is_multipart():
        for sub in part.iter_parts():
            _collect_eml_part(sub, plain_parts, html_parts, child_items)
        return

    if disp == "attachment" or fname:
        raw_bytes = part.get_payload(decode=True)
        if isinstance(raw_bytes, bytes):
            safe_name = _sanitize_member_filename(fname or "attachment.bin")
            child_items.append((safe_name, _guess_mime(safe_name, ctype), raw_bytes))
        return

    if ctype == "text/plain":
        plain_parts.append(str(part.get_content()))
    elif ctype == "text/html":
        html_parts.append(str(part.get_content()))


def _read_zip_member(
    zf: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    safe_name: str,
    budget: _UnpackBudget,
) -> bytes:
    if info.file_size > budget.max_entry_bytes:
        raise ValueError(
            f"ZIP entry {safe_name!r} declared size {info.file_size} exceeds "
            f"max_entry_bytes={budget.max_entry_bytes}"
        )
    comp_size = max(info.compress_size, 1)
    if (
        info.file_size > 65536
        and (info.file_size / comp_size) > budget.max_compression_ratio
    ):
        raise ValueError(
            f"ZIP entry {safe_name!r} compression ratio "
            f"{info.file_size / comp_size:.1f} exceeds "
            f"max_compression_ratio={budget.max_compression_ratio}"
        )
    buf = bytearray()
    with zf.open(info, mode="r") as stream:
        while chunk := stream.read(65536):
            buf.extend(chunk)
            if len(buf) > budget.max_entry_bytes:
                raise ValueError(
                    f"ZIP entry {safe_name!r} actual size exceeds "
                    f"max_entry_bytes={budget.max_entry_bytes}"
                )
            if (
                len(buf) > 65536
                and (len(buf) / comp_size) > budget.max_compression_ratio
            ):
                raise ValueError(
                    f"ZIP entry {safe_name!r} streaming compression ratio exceeds "
                    f"max_compression_ratio={budget.max_compression_ratio}"
                )
    return bytes(buf)


def _unpack_zip_bytes(data: bytes, ctx: _NodeContext, budget: _UnpackBudget) -> None:
    """Safely unpack a `.zip` archive with zip-bomb ratio, count, and byte-ceiling checks."""
    if ctx.depth > budget.max_depth:
        raise ValueError(
            f"Container nesting depth {ctx.depth} exceeds max_depth={budget.max_depth}"
        )
    env_id = budget.next_envelope_id()
    next_chain = (*ctx.envelope_chain, env_id)

    with zipfile.ZipFile(io.BytesIO(data), mode="r") as zf:
        members = [info for info in zf.infolist() if not info.is_dir()]
        if len(members) > budget.max_files:
            raise ValueError(
                f"ZIP archive member count {len(members)} exceeds max_files={budget.max_files}"
            )
        for info in members:
            safe_name = _sanitize_member_filename(info.filename)
            if safe_name.startswith("__MACOSX") or safe_name == ".DS_Store":
                continue
            member_bytes = _read_zip_member(zf, info, safe_name, budget)
            _unpack_node(
                member_bytes,
                _NodeContext(
                    filename=safe_name,
                    mime_type=_guess_mime(safe_name),
                    parent_envelope_id=env_id,
                    envelope_chain=next_chain,
                    depth=ctx.depth + 1,
                ),
                budget,
            )


def _unpack_node(data: bytes, ctx: _NodeContext, budget: _UnpackBudget) -> None:
    """Dispatch a single container or leaf payload according to format magic and suffix."""
    if _is_zip_payload(data, ctx.filename):
        _unpack_zip_bytes(data, ctx, budget)
        return
    if _is_msg_payload(data, ctx.filename):
        reader = Ole2CompoundReader(data, max_stream_bytes=budget.max_entry_bytes)
        if not reader.entries or reader.entries[0].obj_type != 5:
            raise ValueError(f"Malformed OLE2 root entry in {ctx.filename!r}")
        _unpack_ole2_storage(reader, reader.entries[0], ctx, budget)
        return
    if _is_eml_payload(data, ctx.filename, ctx.mime_type):
        _unpack_eml_bytes(data, ctx, budget)
        return
    budget.record_leaf(data, ctx)


def unpack_submission_container(  # noqa: PLR0913 - public container unpack API with keyword limits
    data: bytes,
    filename: str,
    *,
    context_id: str,
    parent_envelope_id: str | None = None,
    max_depth: int = 5,
    max_files: int = 100,
    max_entry_bytes: int = 25 * 1024 * 1024,
    max_total_bytes: int = 64 * 1024 * 1024,
    max_compression_ratio: float = 100.0,
    deduplicate: bool = True,
) -> ContainerUnpackResult:
    """Recursively unpack a solicited ``.zip`` / ``.msg`` / ``.eml`` submission container.

    Enforces strict recursion depth, file count, per-entry size, total byte ceiling,
    and compression-ratio guards (zip-bomb safe), links every extracted child artifact
    to its ``parent_envelope_id`` and ``envelope_chain`` on ``context_id``, deduplicates
    identical leaf artifacts by SHA-256, and parses inline HTML reply status tables
    into ``ReplyItemAdvisory`` records.
    """
    cleaned_ctx = (context_id or "").strip()
    if not cleaned_ctx:
        raise ValueError("unpack_submission_container requires a non-empty context_id")
    if max_depth < 1:
        raise ValueError(f"max_depth must be >= 1; got {max_depth}")

    budget = _UnpackBudget(
        context_id=cleaned_ctx,
        max_depth=max_depth,
        max_files=max_files,
        max_entry_bytes=max_entry_bytes,
        max_total_bytes=max_total_bytes,
        max_compression_ratio=max_compression_ratio,
        deduplicate=deduplicate,
    )
    root_env = parent_envelope_id or f"{cleaned_ctx}:root"
    initial_chain: tuple[str, ...] = (root_env,)
    safe_root_name = _sanitize_member_filename(filename, fallback="submission.bin")
    _unpack_node(
        data,
        _NodeContext(
            filename=safe_root_name,
            mime_type=_guess_mime(safe_root_name),
            parent_envelope_id=root_env,
            envelope_chain=initial_chain,
            depth=0,
        ),
        budget,
    )
    return ContainerUnpackResult(
        context_id=cleaned_ctx,
        root_envelope_id=root_env,
        artifacts=tuple(budget.artifacts),
        envelopes=tuple(budget.envelopes),
        advisories=tuple(budget.advisories),
        skipped_duplicates=budget.skipped_duplicates,
        duplicate_sha256s=tuple(budget.duplicate_hashes),
    )


__all__ = [
    "OLE2_MAGIC",
    "AdvisoryKind",
    "ContainerUnpackResult",
    "ExtractedEnvelopeBody",
    "ReplyItemAdvisory",
    "UnpackedArtifact",
    "apply_advisories_to_requirements",
    "parse_html_reply_advisories",
    "unpack_submission_container",
]
