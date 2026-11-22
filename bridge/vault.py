"""Privacy at the Edge + Cloud Storage Audit Vault (Zone 2 -> Zone 3).

Implements two enterprise data foundation guarantees from the architecture:

1. **Privacy at the Edge (:func:`redact_personal_data`)**:
   Removes personal client and claimant data (SSNs, tax IDs, emails, phone numbers,
   dates of birth, bank account numbers) from extracted ``key_fields``, ``tables``,
   and ``source_anchors`` snippets during Path B intake before any record reaches
   operational storage. Returns both the sanitized :class:`ExtractionResult` and a
   :class:`RedactionAudit` summary (which carries field paths and counts only, never
   raw personal values).

2. **Cloud Storage Audit Vault (:class:`AuditVaultStore`, :func:`promote_accepted_to_vault`)**:
   Separates Tier 1 **Draft & Review Quarantine** (ADK ``GcsArtifactService``, mutable
   attempt versions, 365-day lifecycle delete) from Tier 2 **Cloud Storage Audit Vault**
   (write-once WORM lock, tamper-proof SHA-256 content digest, permanent archive for
   PDFs, Excel spreadsheets, and raw JSON feeds):
   - Only ``Disposition.accepted`` ledger entries can be promoted into the Audit Vault
     (attempting to promote a ``pending`` or ``rejected`` entry raises
     :class:`VaultPromotionRefused`).
   - Every vault write is **write-once** (``if_generation_match=0`` on GCS;
     :class:`VaultImmutabilityError` on any overwrite or delete attempt).
   - Promotion stamps ``entry.vault_uri`` and updates every ``SourceAnchor.artifact_uri``
     so downstream AlloyDB facts, Role-Based Views, and A2UI visual cards link directly
     to the immutable source page in the Audit Vault.

Isolation: imports ``contract/`` + stdlib + ``pydantic`` only (``google.cloud.storage``
is imported lazily inside :class:`GcpAuditVaultStore`); NEVER ``agents/``.
"""

from __future__ import annotations

import abc
import hashlib
import os
import re
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Protocol, cast

from pydantic import BaseModel, ConfigDict

from bridge.telemetry import trace_step
from contract.enums import Disposition
from contract.extraction import ExtractionResult, SourceAnchor
from contract.wire import LedgerEntry


class _GcsBlobLike(Protocol):
    name: str

    def upload_from_string(
        self,
        data: bytes,
        *,
        content_type: str | None = None,
        if_generation_match: int | None = None,
    ) -> object: ...

    def download_as_bytes(self) -> bytes: ...


class _GcsBucketLike(Protocol):
    def list_blobs(self, *, prefix: str | None = None) -> Iterable[_GcsBlobLike]: ...

    def blob(self, blob_name: str) -> _GcsBlobLike: ...


class _GcsClientLike(Protocol):
    def bucket(self, bucket_name: str) -> _GcsBucketLike: ...


class _RetentionHolder(Protocol):
    retention: object


class _MetadataHolder(Protocol):
    metadata: dict[str, str]


VAULT_BUCKET_ENV = "BRIDGE_VAULT_BUCKET"
VAULT_PREFIX_ENV = "BRIDGE_VAULT_PREFIX"
VAULT_RETENTION_DAYS_ENV = "BRIDGE_VAULT_RETENTION_DAYS"
VAULT_RETENTION_MODE_ENV = "BRIDGE_VAULT_RETENTION_MODE"
VALID_RETENTION_MODES: frozenset[str] = frozenset({"Unlocked", "Locked"})

# Patterns for inline personal data scrubbing in free-text fields and snippets.
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PHONE_RE = re.compile(
    r"(?<!\d)(?:\+1[-.\s]?)?(?:\(\d{3}\)|\d{3})[-.\s]\d{3}[-.\s]\d{4}(?!\d)"
)

# Field/column names that always hold personal identifiers and are masked in full.
DEFAULT_SENSITIVE_KEYS: frozenset[str] = frozenset(
    {
        "ssn",
        "social_security_number",
        "member_ssn",
        "claimant_ssn",
        "tax_id",
        "tin",
        "dob",
        "date_of_birth",
        "bank_account",
        "routing_number",
        "driver_license_number",
        "patient_name",
        "claimant_phone",
        "claimant_email",
    }
)


class RedactionAudit(BaseModel):
    """Audit record of personal data removed at intake (carries paths + counts only)."""

    model_config = ConfigDict(extra="forbid")

    redacted_count: int = 0
    redacted_paths: list[str] = []


def _scrub_string(value: str) -> tuple[str, bool]:
    """Scrub inline SSN, email, and phone patterns from ``value``."""
    out = _SSN_RE.sub("[REDACTED-SSN]", value)
    out = _EMAIL_RE.sub("[REDACTED-EMAIL]", out)
    out = _PHONE_RE.sub("[REDACTED-PHONE]", out)
    return out, (out != value)


def _redact_string_mapping(
    values: Mapping[str, str],
    path_prefix: str,
    sensitive_keys: frozenset[str],
    redacted_paths: list[str],
) -> dict[str, str]:
    sanitized: dict[str, str] = {}
    for key, val in values.items():
        path = f"{path_prefix}.{key}"
        if key.lower() in sensitive_keys:
            sanitized[key] = "[REDACTED-PII]"
            redacted_paths.append(path)
        else:
            scrubbed, changed = _scrub_string(val)
            sanitized[key] = scrubbed
            if changed:
                redacted_paths.append(path)
    return sanitized


def _redact_tables(
    tables: Mapping[str, list[dict[str, str]]],
    sensitive_keys: frozenset[str],
    redacted_paths: list[str],
) -> dict[str, list[dict[str, str]]]:
    return {
        table_name: [
            _redact_string_mapping(
                row,
                f"fields.tables.{table_name}[{idx}]",
                sensitive_keys,
                redacted_paths,
            )
            for idx, row in enumerate(rows)
        ]
        for table_name, rows in tables.items()
    }


def _redact_anchors(
    anchors: Mapping[str, SourceAnchor],
    sensitive_keys: frozenset[str],
    redacted_paths: list[str],
) -> dict[str, SourceAnchor]:
    new_anchors: dict[str, SourceAnchor] = {}
    for anchor_key, anchor in anchors.items():
        if anchor.snippet is not None:
            scrubbed, changed = _scrub_string(anchor.snippet)
            if anchor_key.split(".")[-1].lower() in sensitive_keys:
                scrubbed = "[REDACTED-PII]"
                changed = True
            if changed:
                redacted_paths.append(f"fields.source_anchors.{anchor_key}.snippet")
                new_anchors[anchor_key] = anchor.model_copy(
                    update={"snippet": scrubbed}
                )
                continue
        new_anchors[anchor_key] = anchor
    return new_anchors


def redact_personal_data(
    result: ExtractionResult,
    *,
    sensitive_keys: frozenset[str] = DEFAULT_SENSITIVE_KEYS,
) -> tuple[ExtractionResult, RedactionAudit]:
    """Remove personal client/claimant data from an ``ExtractionResult`` at the edge.

    - Any ``key_fields`` key or ``tables`` column whose lowercase name is in
      ``sensitive_keys`` is replaced with ``"[REDACTED-PII]"``.
    - All other string values in ``key_fields``, ``tables``, and
      ``source_anchors[*].snippet`` are scrubbed for inline SSN, email, and phone
      patterns.
    - Returns ``(sanitized_result, RedactionAudit)`` without mutating ``result``.
    """
    redacted_paths: list[str] = []
    new_key_fields = _redact_string_mapping(
        result.fields.key_fields, "fields.key_fields", sensitive_keys, redacted_paths
    )
    new_tables = _redact_tables(result.fields.tables, sensitive_keys, redacted_paths)
    new_anchors = _redact_anchors(
        result.fields.source_anchors, sensitive_keys, redacted_paths
    )

    sanitized_fields = result.fields.model_copy(
        update={
            "key_fields": new_key_fields,
            "tables": new_tables,
            "source_anchors": new_anchors,
        }
    )
    sanitized_result = result.model_copy(update={"fields": sanitized_fields})
    audit = RedactionAudit(
        redacted_count=len(redacted_paths),
        redacted_paths=redacted_paths,
    )
    with trace_step(
        "docubridge.intake.redact_pii",
        {
            "docubridge.zone": "2_intake_validation",
            "docubridge.doctype": result.fields.doctype,
            "docubridge.pii_redacted_count": audit.redacted_count,
        },
    ):
        return sanitized_result, audit


# Zone 3: Cloud Storage Audit Vault (Tamper-Proof Write-Once Lock)


class VaultImmutabilityError(RuntimeError):
    """Raised when attempting to overwrite or delete a write-once Audit Vault object."""


class VaultPromotionRefused(ValueError):
    """Raised when attempting to promote a non-accepted entry into the Audit Vault."""


class VaultRecord(BaseModel):
    """Immutable metadata receipt for a raw file locked in the Cloud Storage Audit Vault."""

    model_config = ConfigDict(extra="forbid")

    context_id: str
    doc_id: str
    version: int
    filename: str
    mime_type: str
    sha256: str
    size_bytes: int
    vault_uri: str
    locked_at: datetime
    retention_locked: bool = False


def _normalize_vault_prefix(prefix: str | None) -> str:
    cleaned = (prefix or "").strip().strip("/")
    if not cleaned:
        return ""
    segments = [seg for seg in cleaned.split("/") if seg]
    if any(seg in {".", ".."} for seg in segments):
        raise ValueError(
            f"invalid Audit Vault GCS prefix {prefix!r}: path traversal forbidden"
        )
    return "/".join(segments)


def _parse_vault_bucket_and_prefix(
    raw_bucket: str, raw_prefix: str | None = None
) -> tuple[str, str]:
    cleaned = (raw_bucket or "").strip()
    if cleaned.startswith("gs://"):
        cleaned = cleaned[len("gs://") :]
    cleaned = cleaned.strip("/")
    bucket, _, embedded = cleaned.partition("/")
    embedded_norm = _normalize_vault_prefix(embedded)
    if raw_prefix is not None:
        return bucket, _normalize_vault_prefix(raw_prefix)
    return bucket, embedded_norm


def _validate_vault_segment(value: str, label: str) -> str:
    """Reject empty, slash-bearing, or dot-traversal vault path segments before storage I/O."""
    if (
        not isinstance(value, str)
        or not value
        or value.strip() != value
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
    ):
        raise ValueError(
            f"invalid vault {label} {value!r}: must be a single path segment without slashes or traversal"
        )
    return value


def _vault_object_key(
    context_id: str,
    doc_id: str,
    version: int,
    filename: str,
    *,
    prefix: str = "",
) -> str:
    ctx = _validate_vault_segment(context_id, "context_id")
    doc = _validate_vault_segment(doc_id, "doc_id")
    raw_base = os.path.basename((filename or "").replace("\\", "/")) or doc
    _validate_vault_segment(raw_base, "filename")
    rel = f"vault/{ctx}/{doc}/v{version}/content"
    clean_prefix = _normalize_vault_prefix(prefix)
    return f"{clean_prefix}/{rel}" if clean_prefix else rel


def _is_precondition_failed(exc: BaseException) -> bool:
    try:
        from google.api_core.exceptions import PreconditionFailed

        if isinstance(exc, PreconditionFailed):
            return True
    except ImportError:
        pass
    return getattr(exc, "code", None) == 412


def _vault_version_prefix(
    context_id: str,
    doc_id: str,
    version: int,
    *,
    prefix: str = "",
) -> str:
    ctx = _validate_vault_segment(context_id, "context_id")
    doc = _validate_vault_segment(doc_id, "doc_id")
    rel = f"vault/{ctx}/{doc}/v{version}/"
    clean_prefix = _normalize_vault_prefix(prefix)
    return f"{clean_prefix}/{rel}" if clean_prefix else rel


_VAULT_KEY_RE = re.compile(
    r"^vault/(?P<context_id>[^/]+)/(?P<doc_id>[^/]+)/v(?P<version>\d+)/(?P<filename>.+)$"
)


def _match_vault_key(blob_name: str, *, prefix: str = "") -> re.Match[str] | None:
    clean_prefix = _normalize_vault_prefix(prefix)
    if clean_prefix:
        expected = f"{clean_prefix}/"
        if not blob_name.startswith(expected):
            return None
        rel = blob_name[len(expected) :]
    else:
        rel = blob_name
    return _VAULT_KEY_RE.match(rel)


def _apply_blob_retention(
    blob: object, *, mode: str, retain_until_time: datetime
) -> bool:
    """Apply per-object GCS Object Retention onto ``blob`` prior to upload when supported."""
    if not hasattr(blob, "retention"):
        return False
    ret = getattr(blob, "retention", None)
    if ret is not None and hasattr(ret, "mode") and hasattr(ret, "retain_until_time"):
        ret.mode = mode
        ret.retain_until_time = retain_until_time
        return True
    rfc3339 = retain_until_time.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(ret, dict):
        ret["mode"] = mode
        ret["retainUntilTime"] = rfc3339
        return True
    try:
        cast(_RetentionHolder, blob).retention = {
            "mode": mode,
            "retainUntilTime": rfc3339,
        }
        return True
    except AttributeError:
        return False


class AuditVaultStore(abc.ABC):
    """Abstract interface for the tamper-proof Cloud Storage Audit Vault."""

    @abc.abstractmethod
    def lock_document(  # noqa: PLR0913 - public AuditVaultStore contract
        self,
        *,
        context_id: str,
        doc_id: str,
        version: int = 1,
        filename: str,
        data: bytes,
        mime_type: str = "application/pdf",
        locked_at: datetime | None = None,
    ) -> VaultRecord:
        """Write-once lock ``data`` into the Audit Vault; refuse overwrites."""

    @abc.abstractmethod
    def get_record(self, context_id: str, doc_id: str, version: int = 1) -> VaultRecord:
        """Return the :class:`VaultRecord` for ``(context_id, doc_id, version)``."""

    @abc.abstractmethod
    def read_bytes(self, context_id: str, doc_id: str, version: int = 1) -> bytes:
        """Read the raw immutable bytes for ``(context_id, doc_id, version)``."""

    @abc.abstractmethod
    def list_records(self, context_id: str | None = None) -> list[VaultRecord]:
        """List locked vault records (optionally filtered by ``context_id``)."""

    def delete_document(self, context_id: str, doc_id: str, version: int = 1) -> None:
        """Deletion is unconditionally forbidden on the write-once Audit Vault."""
        raise VaultImmutabilityError(
            f"Audit Vault is write-once locked (WORM); deletion of "
            f"{context_id}/{doc_id}/v{version} is forbidden"
        )


class InMemoryAuditVaultStore(AuditVaultStore):
    """Local write-once Audit Vault store enforcing WORM immutability."""

    def __init__(self) -> None:
        self._records: dict[tuple[str, str, int], VaultRecord] = {}
        self._blobs: dict[tuple[str, str, int], bytes] = {}

    def lock_document(  # noqa: PLR0913 - public AuditVaultStore contract
        self,
        *,
        context_id: str,
        doc_id: str,
        version: int = 1,
        filename: str,
        data: bytes,
        mime_type: str = "application/pdf",
        locked_at: datetime | None = None,
    ) -> VaultRecord:
        """Compute SHA-256 content hashes and lock an immutable document record in memory."""
        key = (context_id, doc_id, version)
        if key in self._records:
            raise VaultImmutabilityError(
                f"vault object {context_id}/{doc_id}/v{version} is already locked "
                "and cannot be overwritten"
            )
        digest = hashlib.sha256(data).hexdigest()
        obj_path = _vault_object_key(context_id, doc_id, version, filename)
        rec = VaultRecord(
            context_id=context_id,
            doc_id=doc_id,
            version=version,
            filename=os.path.basename(filename) or doc_id,
            mime_type=mime_type,
            sha256=digest,
            size_bytes=len(data),
            vault_uri=f"vault://local/{obj_path}",
            locked_at=locked_at or datetime.now(UTC),
            retention_locked=False,
        )
        self._records[key] = rec
        self._blobs[key] = bytes(data)
        return rec

    def get_record(self, context_id: str, doc_id: str, version: int = 1) -> VaultRecord:
        """Retrieve an immutable vault record by vault ID from memory."""
        key = (context_id, doc_id, version)
        if key not in self._records:
            raise KeyError(f"no vault record for {context_id}/{doc_id}/v{version}")
        return self._records[key]

    def read_bytes(self, context_id: str, doc_id: str, version: int = 1) -> bytes:
        """Read the locked raw document bytes for a vault ID from memory."""
        key = (context_id, doc_id, version)
        if key not in self._blobs:
            raise KeyError(f"no vault bytes for {context_id}/{doc_id}/v{version}")
        return self._blobs[key]

    def list_records(self, context_id: str | None = None) -> list[VaultRecord]:
        """List locked vault records in memory, optionally filtered by exchange ID."""
        records = list(self._records.values())
        if context_id is not None:
            records = [r for r in records if r.context_id == context_id]
        return sorted(records, key=lambda r: (r.context_id, r.doc_id, r.version))


class GcpAuditVaultStore(AuditVaultStore):
    """Cloud Storage Audit Vault adapter using ``if_generation_match=0`` and optional per-object retention."""

    def __init__(
        self,
        bucket_name: str,
        *,
        prefix: str | None = None,
        retention_days: int | None = None,
        retention_mode: str | None = None,
        client: _GcsClientLike | None = None,
    ) -> None:
        bucket, resolved_prefix = _parse_vault_bucket_and_prefix(bucket_name, prefix)
        if not bucket:
            raise ValueError("GcpAuditVaultStore requires a non-empty bucket_name")
        if retention_days is not None and (
            isinstance(retention_days, bool) or retention_days < 1
        ):
            raise ValueError("retention_days must be a positive integer")
        resolved_mode = (retention_mode or "").strip() or "Unlocked"
        if resolved_mode not in VALID_RETENTION_MODES:
            raise ValueError(
                f"invalid retention_mode {retention_mode!r}: expected 'Unlocked' or 'Locked'"
            )
        self.bucket_name = bucket
        self.prefix = resolved_prefix
        self.retention_days = retention_days
        self.retention_mode = resolved_mode
        self._client = client
        self._index: dict[tuple[str, str, int], VaultRecord] = {}

    def _get_bucket(self) -> _GcsBucketLike:
        if self._client is None:  # pragma: no cover - live GCP client lazy import
            from google.cloud import storage

            self._client = cast(_GcsClientLike, storage.Client())
        return self._client.bucket(self.bucket_name)

    def lock_document(  # noqa: PLR0913 - public AuditVaultStore contract
        self,
        *,
        context_id: str,
        doc_id: str,
        version: int = 1,
        filename: str,
        data: bytes,
        mime_type: str = "application/pdf",
        locked_at: datetime | None = None,
    ) -> VaultRecord:
        """Write an immutable object to Cloud Storage with write-once precondition (`if_generation_match=0`) and optional per-object retention."""
        key = (context_id, doc_id, version)
        if key in self._index:
            raise VaultImmutabilityError(
                f"vault object {context_id}/{doc_id}/v{version} is already locked"
            )
        obj_path = _vault_object_key(
            context_id, doc_id, version, filename, prefix=self.prefix
        )
        bucket = self._get_bucket()
        existing_blobs = list(
            bucket.list_blobs(
                prefix=_vault_version_prefix(
                    context_id, doc_id, version, prefix=self.prefix
                )
            )
        )
        if existing_blobs:
            raise VaultImmutabilityError(
                f"vault object {context_id}/{doc_id}/v{version} is already locked in GCS"
            )
        blob = bucket.blob(obj_path)
        locked_ts = locked_at or datetime.now(UTC)
        digest = hashlib.sha256(data).hexdigest()
        safe_filename = os.path.basename(filename) or doc_id
        retain_until: datetime | None = None
        retention_applied = False
        if self.retention_days is not None:
            retain_until = locked_ts + timedelta(days=self.retention_days)
            retention_applied = _apply_blob_retention(
                blob, mode=self.retention_mode, retain_until_time=retain_until
            )
        if hasattr(blob, "metadata"):
            metadata = {
                "context_id": context_id,
                "doc_id": doc_id,
                "version": str(version),
                "filename": safe_filename,
                "mime_type": mime_type,
                "sha256": digest,
                "size_bytes": str(len(data)),
                "locked_at": locked_ts.isoformat(),
            }
            if retain_until is not None and retention_applied:
                metadata["retention_mode"] = self.retention_mode
                metadata["retain_until_time"] = (
                    retain_until.astimezone(UTC).isoformat().replace("+00:00", "Z")
                )
            cast(_MetadataHolder, blob).metadata = metadata
        try:
            blob.upload_from_string(data, content_type=mime_type, if_generation_match=0)
        except Exception as exc:
            if _is_precondition_failed(exc):
                raise VaultImmutabilityError(
                    f"GCS write-once precondition failed for {obj_path}: {exc}"
                ) from exc
            raise
        rec = VaultRecord(
            context_id=context_id,
            doc_id=doc_id,
            version=version,
            filename=safe_filename,
            mime_type=mime_type,
            sha256=digest,
            size_bytes=len(data),
            vault_uri=f"gs://{self.bucket_name}/{obj_path}",
            locked_at=locked_ts,
            retention_locked=bool(
                retention_applied and self.retention_mode == "Locked"
            ),
        )
        self._index[key] = rec
        return rec

    def _record_from_blob(self, blob: _GcsBlobLike) -> VaultRecord | None:
        """Reconstruct a ``VaultRecord`` from a GCS blob across stateless replicas."""
        match = _match_vault_key(getattr(blob, "name", ""), prefix=self.prefix)
        if not match:
            return None
        context_id = match["context_id"]
        doc_id = match["doc_id"]
        version = int(match["version"])
        meta = getattr(blob, "metadata", None) or {}
        filename = meta.get("filename") or match["filename"]
        raw_bytes: bytes | None = None
        if "sha256" in meta and "size_bytes" in meta:
            digest = meta["sha256"]
            size_bytes = int(meta["size_bytes"])
        else:
            raw_bytes = blob.download_as_bytes()
            digest = hashlib.sha256(raw_bytes).hexdigest()
            size_bytes = len(raw_bytes)
        mime_type = (
            meta.get("mime_type")
            or getattr(blob, "content_type", None)
            or "application/pdf"
        )
        locked_at_str = meta.get("locked_at")
        if locked_at_str:
            locked_at = datetime.fromisoformat(locked_at_str)
        else:
            locked_at = getattr(blob, "time_created", None) or datetime.now(UTC)
        blob_ret = getattr(blob, "retention", None)
        blob_ret_mode = (
            blob_ret.get("mode")
            if isinstance(blob_ret, dict)
            else getattr(blob_ret, "mode", None)
        )
        blob_ret_until = (
            blob_ret.get("retainUntilTime")
            if isinstance(blob_ret, dict)
            else getattr(blob_ret, "retain_until_time", None)
        )
        is_locked = (
            meta.get("retention_mode") == "Locked"
            and bool(meta.get("retain_until_time"))
        ) or (blob_ret_mode == "Locked" and blob_ret_until is not None)
        rec = VaultRecord(
            context_id=context_id,
            doc_id=doc_id,
            version=version,
            filename=filename,
            mime_type=mime_type,
            sha256=digest,
            size_bytes=size_bytes,
            vault_uri=f"gs://{self.bucket_name}/{blob.name}",
            locked_at=locked_at,
            retention_locked=is_locked,
        )
        self._index[(context_id, doc_id, version)] = rec
        return rec

    def get_record(self, context_id: str, doc_id: str, version: int = 1) -> VaultRecord:
        """Fetch an immutable vault audit record by vault ID from Cloud Storage blob metadata."""
        key = (context_id, doc_id, version)
        if key in self._index:
            return self._index[key]
        prefix = _vault_version_prefix(context_id, doc_id, version, prefix=self.prefix)
        for blob in self._get_bucket().list_blobs(prefix=prefix):
            rec = self._record_from_blob(blob)
            if rec is not None:
                return rec
        raise KeyError(f"no vault record for {context_id}/{doc_id}/v{version}")

    def read_bytes(self, context_id: str, doc_id: str, version: int = 1) -> bytes:
        """Download the locked raw document bytes for a vault ID from Cloud Storage."""
        rec = self.get_record(context_id, doc_id, version)
        uri_prefix = f"gs://{self.bucket_name}/"
        if rec.vault_uri.startswith(uri_prefix):
            obj_path = rec.vault_uri[len(uri_prefix) :]
        else:
            obj_path = _vault_object_key(
                context_id, doc_id, version, rec.filename, prefix=self.prefix
            )
        blob = self._get_bucket().blob(obj_path)
        return blob.download_as_bytes()

    def list_records(self, context_id: str | None = None) -> list[VaultRecord]:
        """List locked vault audit records from Cloud Storage blob metadata, optionally filtered by exchange ID."""
        if context_id is not None:
            _validate_vault_segment(context_id, "context_id")
        rel_prefix = f"vault/{context_id}/" if context_id is not None else "vault/"
        list_prefix = f"{self.prefix}/{rel_prefix}" if self.prefix else rel_prefix
        for blob in self._get_bucket().list_blobs(prefix=list_prefix):
            key_match = _match_vault_key(getattr(blob, "name", ""), prefix=self.prefix)
            if key_match:
                k = (
                    key_match["context_id"],
                    key_match["doc_id"],
                    int(key_match["version"]),
                )
                if k not in self._index:
                    self._record_from_blob(blob)
        records = list(self._index.values())
        if context_id is not None:
            records = [r for r in records if r.context_id == context_id]
        return sorted(records, key=lambda r: (r.context_id, r.doc_id, r.version))


def vault_from_env() -> AuditVaultStore:
    """Build the Audit Vault store via the ``Port.audit_vault`` port adapter."""
    from bridge.ports import Port, get_adapter, resolve_mode

    mode = resolve_mode(Port.audit_vault)
    adapter = get_adapter(Port.audit_vault, mode)
    return adapter.build_vault()


def promote_accepted_to_vault(  # noqa: PLR0913 - public vault promotion contract
    vault: AuditVaultStore,
    *,
    context_id: str,
    entity_id: str | None = None,
    entry: LedgerEntry,
    data: bytes,
    filename: str | None = None,
    mime_type: str = "application/pdf",
    version: int = 1,
    recorded_at: datetime | None = None,
    effective_from: datetime | None = None,
) -> tuple[LedgerEntry, VaultRecord]:
    """Promote an accepted ``LedgerEntry`` from Draft Quarantine into the Audit Vault.

    - Refuses any entry whose ``disposition`` is not ``Disposition.accepted``
      (:class:`VaultPromotionRefused` — draft/parked/rejected files stay in
      Quarantine and never pollute the Audit Vault).
    - Locks the raw bytes in ``vault`` (write-once WORM) and returns a copy of
      ``entry`` stamped with ``vault_uri``, ``recorded_at``, ``effective_from``, and
      updated ``source_anchors[*].artifact_uri``.
    """
    if entry.disposition is not Disposition.accepted:
        raise VaultPromotionRefused(
            f"only accepted entries can be promoted to the Audit Vault; "
            f"entry {entry.doc_id!r} has disposition={entry.disposition.value!r}"
        )
    resolved_entity_id = (
        entity_id.strip() if entity_id and entity_id.strip() else context_id
    )
    resolved_name = (
        filename
        or (entry.artifact.name if entry.artifact and entry.artifact.name else None)
        or (
            os.path.basename(entry.artifact.uri)
            if entry.artifact and entry.artifact.uri
            else None
        )
        or f"{entry.doc_id}.pdf"
    )
    resolved_mime = (
        entry.artifact.mime_type
        if entry.artifact and entry.artifact.mime_type
        else mime_type
    )
    now = recorded_at or entry.recorded_at or datetime.now(UTC)
    if effective_from is not None:
        eff: datetime | None = effective_from
    elif entry.effective_from is not None:
        eff = entry.effective_from
    else:
        raw_doc_date = entry.key_fields.get("effective_date")
        if raw_doc_date is not None and raw_doc_date.strip():
            try:
                parsed_d = datetime.fromisoformat(raw_doc_date.strip())
                eff = (
                    parsed_d.replace(tzinfo=UTC)
                    if parsed_d.tzinfo is None
                    else parsed_d.astimezone(UTC)
                )
            except ValueError:
                eff = None
        else:
            eff = now
    with trace_step(
        "docubridge.intake.redact_pii",
        {
            "docubridge.zone": "2_intake_validation",
            "docubridge.entity_id": resolved_entity_id,
            "entity_id": resolved_entity_id,
            "docubridge.context_id": context_id,
            "docubridge.doc_id": entry.doc_id,
            "docubridge.doctype": entry.doctype,
        },
    ):
        pass
    with trace_step(
        "docubridge.vault.lock_worm_artifact",
        {
            "docubridge.entity_id": resolved_entity_id,
            "entity_id": resolved_entity_id,
            "docubridge.context_id": context_id,
            "docubridge.doc_id": entry.doc_id,
            "docubridge.doctype": entry.doctype,
            "docubridge.version": version,
        },
    ) as span:
        record = vault.lock_document(
            context_id=context_id,
            doc_id=entry.doc_id,
            version=version,
            filename=resolved_name,
            data=data,
            mime_type=resolved_mime,
            locked_at=now,
        )
        span.set_attribute("docubridge.vault_uri", record.vault_uri)
        span.set_attribute("docubridge.sha256", record.sha256)
        span.set_attribute("docubridge.size_bytes", record.size_bytes)
    updated_anchors = {
        k: anchor.model_copy(update={"artifact_uri": record.vault_uri})
        for k, anchor in entry.source_anchors.items()
    }
    promoted_entry = entry.model_copy(
        update={
            "vault_uri": record.vault_uri,
            "recorded_at": now,
            "effective_from": eff,
            "source_anchors": updated_anchors,
        }
    )
    return promoted_entry, record
