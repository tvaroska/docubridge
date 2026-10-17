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
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict

from contract.enums import Disposition
from contract.extraction import ExtractionResult, SourceAnchor
from contract.wire import LedgerEntry

VAULT_BUCKET_ENV = "BRIDGE_VAULT_BUCKET"

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
    new_key_fields: dict[str, str] = {}

    for key, val in result.fields.key_fields.items():
        path = f"fields.key_fields.{key}"
        if key.lower() in sensitive_keys:
            new_key_fields[key] = "[REDACTED-PII]"
            redacted_paths.append(path)
        else:
            scrubbed, changed = _scrub_string(val)
            new_key_fields[key] = scrubbed
            if changed:
                redacted_paths.append(path)

    new_tables: dict[str, list[dict[str, str]]] = {}
    for table_name, rows in result.fields.tables.items():
        new_rows: list[dict[str, str]] = []
        for idx, row in enumerate(rows):
            new_row: dict[str, str] = {}
            for col, val in row.items():
                path = f"fields.tables.{table_name}[{idx}].{col}"
                if col.lower() in sensitive_keys:
                    new_row[col] = "[REDACTED-PII]"
                    redacted_paths.append(path)
                else:
                    scrubbed, changed = _scrub_string(val)
                    new_row[col] = scrubbed
                    if changed:
                        redacted_paths.append(path)
            new_rows.append(new_row)
        new_tables[table_name] = new_rows

    new_anchors: dict[str, SourceAnchor] = {}
    for anchor_key, anchor in result.fields.source_anchors.items():
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

    sanitized_fields = result.fields.model_copy(
        update={
            "key_fields": new_key_fields,
            "tables": new_tables,
            "source_anchors": new_anchors,
        }
    )
    sanitized_result = result.model_copy(update={"fields": sanitized_fields})
    return sanitized_result, RedactionAudit(
        redacted_count=len(redacted_paths),
        redacted_paths=redacted_paths,
    )


# --------------------------------------------------------------------------- #
# Zone 3: Cloud Storage Audit Vault (Tamper-Proof Write-Once Lock)             #
# --------------------------------------------------------------------------- #


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
    retention_locked: bool = True


def _vault_object_key(context_id: str, doc_id: str, version: int, filename: str) -> str:
    safe_name = os.path.basename(filename) or doc_id
    return f"vault/{context_id}/{doc_id}/v{version}/{safe_name}"


def _vault_version_prefix(context_id: str, doc_id: str, version: int) -> str:
    return f"vault/{context_id}/{doc_id}/v{version}/"


_VAULT_KEY_RE = re.compile(
    r"^vault/(?P<context_id>[^/]+)/(?P<doc_id>[^/]+)/v(?P<version>\d+)/(?P<filename>.+)$"
)


class AuditVaultStore(abc.ABC):
    """Abstract interface for the tamper-proof Cloud Storage Audit Vault."""

    @abc.abstractmethod
    def lock_document(
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
    def get_record(
        self, context_id: str, doc_id: str, version: int = 1
    ) -> VaultRecord:
        """Return the :class:`VaultRecord` for ``(context_id, doc_id, version)``."""

    @abc.abstractmethod
    def read_bytes(self, context_id: str, doc_id: str, version: int = 1) -> bytes:
        """Read the raw immutable bytes for ``(context_id, doc_id, version)``."""

    @abc.abstractmethod
    def list_records(self, context_id: str | None = None) -> list[VaultRecord]:
        """List locked vault records (optionally filtered by ``context_id``)."""

    def delete_document(
        self, context_id: str, doc_id: str, version: int = 1
    ) -> None:
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

    def lock_document(
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
            retention_locked=True,
        )
        self._records[key] = rec
        self._blobs[key] = bytes(data)
        return rec

    def get_record(
        self, context_id: str, doc_id: str, version: int = 1
    ) -> VaultRecord:
        key = (context_id, doc_id, version)
        if key not in self._records:
            raise KeyError(f"no vault record for {context_id}/{doc_id}/v{version}")
        return self._records[key]

    def read_bytes(self, context_id: str, doc_id: str, version: int = 1) -> bytes:
        key = (context_id, doc_id, version)
        if key not in self._blobs:
            raise KeyError(f"no vault bytes for {context_id}/{doc_id}/v{version}")
        return self._blobs[key]

    def list_records(self, context_id: str | None = None) -> list[VaultRecord]:
        records = list(self._records.values())
        if context_id is not None:
            records = [r for r in records if r.context_id == context_id]
        return sorted(records, key=lambda r: (r.context_id, r.doc_id, r.version))


class GcpAuditVaultStore(AuditVaultStore):
    """Cloud Storage Audit Vault adapter using ``if_generation_match=0`` (write-once)."""

    def __init__(self, bucket_name: str, *, client=None) -> None:
        bucket = (bucket_name or "").strip()
        if not bucket:
            raise ValueError("GcpAuditVaultStore requires a non-empty bucket_name")
        self.bucket_name = bucket
        self._client = client
        self._index: dict[tuple[str, str, int], VaultRecord] = {}

    def _get_bucket(self):
        if self._client is None:  # pragma: no cover - live GCP client lazy import
            from google.cloud import storage

            self._client = storage.Client()
        return self._client.bucket(self.bucket_name)

    def lock_document(
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
        key = (context_id, doc_id, version)
        if key in self._index:
            raise VaultImmutabilityError(
                f"vault object {context_id}/{doc_id}/v{version} is already locked"
            )
        obj_path = _vault_object_key(context_id, doc_id, version, filename)
        bucket = self._get_bucket()
        existing_blobs = list(
            bucket.list_blobs(
                prefix=_vault_version_prefix(context_id, doc_id, version)
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
        if hasattr(blob, "metadata"):
            blob.metadata = {
                "context_id": context_id,
                "doc_id": doc_id,
                "version": str(version),
                "filename": safe_filename,
                "mime_type": mime_type,
                "sha256": digest,
                "size_bytes": str(len(data)),
                "locked_at": locked_ts.isoformat(),
            }
        try:
            blob.upload_from_string(
                data, content_type=mime_type, if_generation_match=0
            )
        except Exception as exc:
            raise VaultImmutabilityError(
                f"GCS write-once precondition failed for {obj_path}: {exc}"
            ) from exc
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
            retention_locked=True,
        )
        self._index[key] = rec
        return rec

    def _record_from_blob(self, blob) -> VaultRecord | None:
        """Reconstruct a ``VaultRecord`` from a GCS blob across stateless replicas."""
        match = _VAULT_KEY_RE.match(getattr(blob, "name", ""))
        if not match:
            return None
        context_id = match["context_id"]
        doc_id = match["doc_id"]
        version = int(match["version"])
        filename = match["filename"]
        meta = getattr(blob, "metadata", None) or {}
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
            retention_locked=True,
        )
        self._index[(context_id, doc_id, version)] = rec
        return rec

    def get_record(
        self, context_id: str, doc_id: str, version: int = 1
    ) -> VaultRecord:
        key = (context_id, doc_id, version)
        if key in self._index:
            return self._index[key]
        prefix = _vault_version_prefix(context_id, doc_id, version)
        for blob in self._get_bucket().list_blobs(prefix=prefix):
            rec = self._record_from_blob(blob)
            if rec is not None:
                return rec
        raise KeyError(f"no vault record for {context_id}/{doc_id}/v{version}")

    def read_bytes(self, context_id: str, doc_id: str, version: int = 1) -> bytes:
        rec = self.get_record(context_id, doc_id, version)
        obj_path = _vault_object_key(context_id, doc_id, version, rec.filename)
        blob = self._get_bucket().blob(obj_path)
        return blob.download_as_bytes()

    def list_records(self, context_id: str | None = None) -> list[VaultRecord]:
        prefix = f"vault/{context_id}/" if context_id is not None else "vault/"
        for blob in self._get_bucket().list_blobs(prefix=prefix):
            key_match = _VAULT_KEY_RE.match(getattr(blob, "name", ""))
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
    """Build the Audit Vault store from env: GCS when ``BRIDGE_VAULT_BUCKET`` is set,
    else :class:`InMemoryAuditVaultStore` (follows the host-controlled wiring guard)."""
    bucket = os.environ.get(VAULT_BUCKET_ENV, "").strip()
    if bucket:
        return GcpAuditVaultStore(bucket)
    return InMemoryAuditVaultStore()


def promote_accepted_to_vault(
    vault: AuditVaultStore,
    *,
    context_id: str,
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
    eff = effective_from or entry.effective_from or now
    record = vault.lock_document(
        context_id=context_id,
        doc_id=entry.doc_id,
        version=version,
        filename=resolved_name,
        data=data,
        mime_type=resolved_mime,
        locked_at=now,
    )
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
