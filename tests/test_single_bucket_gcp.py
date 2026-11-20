"""Single-bucket GCS prefix isolation, per-object WORM retention, and Terraform HCL hygiene tests."""

from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from google.api_core.exceptions import PreconditionFailed

from bridge.edge.artifacts import (
    APP_NAME,
    ARTIFACT_BUCKET_ENV,
    _adk_gcs_blob_path,
    build_artifact_store,
    build_fetch_signer,
    gcs_signer,
)
from bridge.ports.gcp.common import (
    EMAIL_BUCKET_ENV,
    EMAIL_PREFIX_ENV,
    SKILLS_BUCKET_ENV,
    SKILLS_PREFIX_ENV,
    join_gcs_prefix,
    normalize_gcs_prefix,
    split_bucket_and_prefix,
)
from bridge.ports.gcp.enterprise import GcpAuditVault
from bridge.ports.gcp.services import GcpEmailIngress, GcpSkillRegistry
from bridge.vault import (
    VAULT_BUCKET_ENV,
    VAULT_PREFIX_ENV,
    VAULT_RETENTION_DAYS_ENV,
    VAULT_RETENTION_MODE_ENV,
    GcpAuditVaultStore,
    VaultImmutabilityError,
    vault_from_env,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

_SKILL_MD = (
    "---\n"
    "name: single-bucket-skill\n"
    "description: Synthetic skill for prefix isolation verification.\n"
    "metadata:\n"
    "  bridge-kind: doctype\n"
    '  bridge-tags: "doctype synthetic"\n'
    "---\n\n"
    "Extract synthetic test fields.\n"
)


class _RetentionState:
    def __init__(self) -> None:
        self.mode: str | None = None
        self.retain_until_time: datetime | None = None


class _SharedFakeBlob:
    def __init__(self, bucket: _SharedFakeBucket, name: str) -> None:
        self._bucket = bucket
        self.name = name
        self.metadata: dict[str, str] | None = bucket.metadata.get(name)
        self.content_type: str = bucket.content_types.get(
            name, "application/octet-stream"
        )
        self.retention = bucket.retentions.setdefault(name, _RetentionState())

    @property
    def time_created(self) -> datetime:
        return self._bucket.created[self.name]

    def upload_from_string(
        self,
        data: bytes | str,
        content_type: str = "application/octet-stream",
        if_generation_match: int | None = None,
    ) -> None:
        if if_generation_match == 0 and self.name in self._bucket.objects:
            raise PreconditionFailed(f"{self.name} already exists")
        raw = data.encode("utf-8") if isinstance(data, str) else bytes(data)
        self._bucket.objects[self.name] = raw
        self._bucket.content_types[self.name] = content_type
        if self.metadata is not None:
            self._bucket.metadata[self.name] = dict(self.metadata)
        self._bucket.created[self.name] = self._bucket.tick()
        self._bucket.uploads.append(
            {
                "name": self.name,
                "content_type": content_type,
                "if_generation_match": if_generation_match,
                "retention_mode": self.retention.mode,
                "retain_until_time": self.retention.retain_until_time,
            }
        )

    def exists(self) -> bool:
        return self.name in self._bucket.objects

    def download_as_bytes(self) -> bytes:
        return self._bucket.objects[self.name]

    def download_as_text(self) -> str:
        return self._bucket.objects[self.name].decode("utf-8")

    def download_to_filename(self, path: str) -> None:
        Path(path).write_bytes(self._bucket.objects[self.name])


class _SharedFakeBucket:
    def __init__(self, name: str = "demo-single-bucket") -> None:
        self.name = name
        self.objects: dict[str, bytes] = {}
        self.metadata: dict[str, dict[str, str]] = {}
        self.content_types: dict[str, str] = {}
        self.created: dict[str, datetime] = {}
        self.retentions: dict[str, _RetentionState] = {}
        self.uploads: list[dict[str, Any]] = []
        self.list_prefixes: list[str | None] = []
        self._t = datetime(2026, 10, 1, tzinfo=UTC)

    def tick(self) -> datetime:
        self._t += timedelta(seconds=1)
        return self._t

    def blob(self, name: str) -> _SharedFakeBlob:
        return _SharedFakeBlob(self, name)

    def list_blobs(self, prefix: str | None = None) -> list[_SharedFakeBlob]:
        self.list_prefixes.append(prefix)
        return [
            _SharedFakeBlob(self, name)
            for name in sorted(self.objects)
            if prefix is None or name.startswith(prefix)
        ]


class _SharedFakeClient:
    def __init__(self, bucket: _SharedFakeBucket) -> None:
        self._bucket = bucket
        self.requested_buckets: list[str] = []

    def bucket(self, name: str) -> _SharedFakeBucket:
        self.requested_buckets.append(name)
        return self._bucket


def test_bucket_prefix_helpers_and_traversal_defense() -> None:
    """Bucket/prefix parsing normalizes gs:// URIs and rejects traversal segments."""
    assert split_bucket_and_prefix("proj-bucket") == ("proj-bucket", "")
    assert split_bucket_and_prefix("proj-bucket/skills/") == ("proj-bucket", "skills")
    assert split_bucket_and_prefix("gs://proj-bucket/audit-vault/") == (
        "proj-bucket",
        "audit-vault",
    )
    assert split_bucket_and_prefix("gs://proj-bucket", "email/") == (
        "proj-bucket",
        "email",
    )
    assert join_gcs_prefix("skills", "a/SKILL.md") == "skills/a/SKILL.md"
    assert join_gcs_prefix("", "a/SKILL.md") == "a/SKILL.md"
    assert join_gcs_prefix("skills", "") == "skills/"

    for bad in ("../escape", "skills/../../etc", "./dot"):
        with pytest.raises(ValueError, match="traversal"):
            normalize_gcs_prefix(bad)


def test_single_bucket_full_prefix_isolation_and_worm_retention(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Skills, email, vault, and ADK artifacts coexist cleanly in one shared bucket."""
    shared = _SharedFakeBucket("demo-single-bucket")
    client = _SharedFakeClient(shared)

    # Seed foreign prefixes inside the same bucket to prove strict isolation.
    shared.objects["document_bridge/_unscoped/ctx-1/doc.pdf/0"] = b"%PDF-adk"
    shared.objects["scenarios/demo/scenario.yaml"] = b"id: demo\n"
    shared.objects["genai-payloads/trace-1.jsonl"] = b'{"payload": {}}\n'

    # Skill registry scoped to skills/
    skills = GcpSkillRegistry("demo-single-bucket", prefix="skills")
    monkeypatch.setattr(skills, "_bucket", lambda: shared)
    meta = skills.install_skill(
        "single-bucket-skill", _SKILL_MD, assets={"assets/schema.json": '{"ok": true}'}
    )
    assert meta.name == "single-bucket-skill"
    assert "skills/single-bucket-skill/SKILL.md" in shared.objects
    assert "skills/single-bucket-skill/assets/schema.json" in shared.objects
    assert [s.name for s in skills.list_skills()] == ["single-bucket-skill"]
    assert (
        skills.read_asset("single-bucket-skill", "assets/schema.json") == '{"ok": true}'
    )
    assert any(p == "skills/" for p in shared.list_prefixes)

    # Email ingress scoped to email/
    email = GcpEmailIngress("demo-single-bucket/email")
    monkeypatch.setattr(email, "_bucket", lambda: shared)
    key = "a" * 32
    assert email.land(key, b"raw-mime") is True
    assert email.land(key, b"duplicate") is False
    assert f"email/inbox/{key}.eml" in shared.objects
    assert email.pending() == [key]
    assert email.read(key) == b"raw-mime"
    att_uri = email.put_attachment(key, 0, "quote.pdf", b"%PDF-q", "application/pdf")
    assert att_uri == f"gs://demo-single-bucket/email/attachments/{key}/0/quote.pdf"
    assert email.mark_processed(key, {"status": "filed"}) is True
    assert f"email/processed/{key}.json" in shared.objects
    assert email.pending() == []
    assert email.processed(key) == {"status": "filed"}

    # Audit vault scoped to audit-vault/ with per-object WORM retention lock
    locked_at = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
    vault = GcpAuditVaultStore(
        "demo-single-bucket",
        prefix="audit-vault",
        retention_days=2555,
        retention_mode="Locked",
        client=client,
    )
    rec = vault.lock_document(
        context_id="ctx-single-1",
        doc_id="doc-single-1",
        version=1,
        filename="closing.pdf",
        data=b"%PDF-worm-bytes",
        locked_at=locked_at,
    )
    expected_obj = "audit-vault/vault/ctx-single-1/doc-single-1/v1/closing.pdf"
    assert rec.vault_uri == f"gs://demo-single-bucket/{expected_obj}"
    assert expected_obj in shared.objects
    assert client.requested_buckets == ["demo-single-bucket"]

    vault_upload = next(u for u in shared.uploads if u["name"] == expected_obj)
    assert vault_upload["if_generation_match"] == 0
    assert vault_upload["retention_mode"] == "Locked"
    assert vault_upload["retain_until_time"] == locked_at + timedelta(days=2555)
    assert shared.metadata[expected_obj]["retention_mode"] == "Locked"
    assert shared.metadata[expected_obj]["retain_until_time"].endswith("Z")

    # Overwrite and deletion are refused on locked prefix objects
    with pytest.raises(VaultImmutabilityError):
        vault.lock_document(
            context_id="ctx-single-1",
            doc_id="doc-single-1",
            version=1,
            filename="closing.pdf",
            data=b"%PDF-tampered",
        )
    with pytest.raises(VaultImmutabilityError):
        vault.delete_document("ctx-single-1", "doc-single-1", version=1)

    # Cold replica reconstructs only audit-vault/ records and ignores foreign prefixes
    shared.objects["other-prefix/vault/ctx-single-1/doc-other/v1/x.pdf"] = b"foreign"
    cold_vault = GcpAuditVaultStore(
        "demo-single-bucket/audit-vault",
        client=client,
    )
    assert cold_vault.bucket_name == "demo-single-bucket"
    assert cold_vault.prefix == "audit-vault"
    cold_rec = cold_vault.get_record("ctx-single-1", "doc-single-1", version=1)
    assert cold_rec.vault_uri == rec.vault_uri
    assert cold_vault.read_bytes("ctx-single-1", "doc-single-1", version=1) == (
        b"%PDF-worm-bytes"
    )
    listed = cold_vault.list_records()
    assert [r.doc_id for r in listed] == ["doc-single-1"]
    assert all(
        p is not None and p.startswith(("skills/", "email/", "audit-vault/"))
        for p in shared.list_prefixes
    )


def test_env_var_prefix_and_retention_wiring(monkeypatch: pytest.MonkeyPatch) -> None:
    """Env vars configure single-bucket prefixes and per-object retention cleanly."""
    monkeypatch.setenv("BRIDGE_PORT_MODE", "gcp")
    monkeypatch.setenv(SKILLS_BUCKET_ENV, "env-proj")
    monkeypatch.setenv(SKILLS_PREFIX_ENV, "skills")
    monkeypatch.setenv(EMAIL_BUCKET_ENV, "env-proj")
    monkeypatch.setenv(EMAIL_PREFIX_ENV, "email")
    monkeypatch.setenv(VAULT_BUCKET_ENV, "env-proj")
    monkeypatch.setenv(VAULT_PREFIX_ENV, "audit-vault")
    monkeypatch.setenv(VAULT_RETENTION_DAYS_ENV, "365")
    monkeypatch.setenv(VAULT_RETENTION_MODE_ENV, "Unlocked")

    assert GcpSkillRegistry()._effective_prefix() == "skills"
    assert GcpSkillRegistry("arg-proj/custom-skills")._effective_prefix() == (
        "custom-skills"
    )
    assert GcpEmailIngress()._effective_prefix() == "email"
    assert GcpEmailIngress("arg-proj", prefix="")._effective_prefix() == ""

    vault = vault_from_env()
    assert isinstance(vault, GcpAuditVaultStore)
    assert vault.bucket_name == "env-proj"
    assert vault.prefix == "audit-vault"
    assert vault.retention_days == 365
    assert vault.retention_mode == "Unlocked"

    # Invalid retention env values fail loudly
    monkeypatch.setenv(VAULT_RETENTION_DAYS_ENV, "not-a-number")
    with pytest.raises(ValueError, match=VAULT_RETENTION_DAYS_ENV):
        GcpAuditVault().build_vault()

    monkeypatch.setenv(VAULT_RETENTION_DAYS_ENV, "30")
    monkeypatch.setenv(VAULT_RETENTION_MODE_ENV, "InvalidMode")
    with pytest.raises(ValueError, match="retention_mode"):
        GcpAuditVault().build_vault()


def test_real_gcs_blob_object_retention_in_isolated_subprocess() -> None:
    """Verify `_apply_blob_retention` populates real google-cloud-storage Blob writable metadata."""
    code = (
        "from datetime import UTC, datetime, timedelta; "
        "from google.cloud import storage; "
        "from bridge.vault import _apply_blob_retention; "
        "blob = storage.Client.create_anonymous_client().bucket('demo-proj').blob('audit-vault/vault/c/d/v1/a.pdf'); "
        "until = datetime(2033, 1, 1, 0, 0, tzinfo=UTC); "
        "assert _apply_blob_retention(blob, mode='Locked', retain_until_time=until) is True; "
        "meta = blob._get_writable_metadata(); "
        "assert meta['retention']['mode'] == 'Locked', meta; "
        "assert meta['retention']['retainUntilTime'].startswith('2033-01-01T00:00:00'), meta"
    )
    proc = subprocess.run(  # noqa: S603 - test spawns trusted sys.executable probe
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"


def test_artifact_bucket_and_signer_align_with_document_bridge_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADK GcsArtifactService and gcs_signer align on document_bridge/ in single-bucket mode."""
    import google.adk.artifacts as adk_artifacts

    seen: dict[str, Any] = {}

    class _StubGcs:
        def __init__(self, bucket_name: str) -> None:
            seen["bucket"] = bucket_name

    monkeypatch.setattr(adk_artifacts, "GcsArtifactService", _StubGcs)

    for raw in (
        "demo-single-bucket",
        "gs://demo-single-bucket",
        "demo-single-bucket/document_bridge",
        "gs://demo-single-bucket/document_bridge/",
    ):
        monkeypatch.setenv(ARTIFACT_BUCKET_ENV, raw)
        store = build_artifact_store()
        assert isinstance(store, _StubGcs)
        assert seen["bucket"] == "demo-single-bucket"
        assert build_fetch_signer() is not None

    assert _adk_gcs_blob_path("alice", "ctx-1", "doc.pdf", 2) == (
        f"{APP_NAME}/alice/ctx-1/doc.pdf/2"
    )

    # Incompatible prefix fails fast so store, signer, and IAM/lifecycle never drift
    monkeypatch.setenv(ARTIFACT_BUCKET_ENV, "demo-single-bucket/wrong-prefix")
    with pytest.raises(ValueError, match="document_bridge"):
        build_artifact_store()
    with pytest.raises(ValueError, match="document_bridge"):
        gcs_signer("demo-single-bucket/wrong-prefix")


def test_terraform_has_zero_bucket_objects_and_deploy_script_rsyncs_skills() -> None:
    """Zero google_storage_bucket_object resources or invalid bucket-IAM CEL attributes remain in Terraform."""
    tf_root = REPO_ROOT / "terraform"
    tf_files = [p for p in tf_root.rglob("*.tf") if ".terraform" not in p.parts]
    assert tf_files
    for tf_file in tf_files:
        text = tf_file.read_text(encoding="utf-8")
        assert 'resource "google_storage_bucket_object"' not in text, (
            f"Found forbidden HCL bucket object in {tf_file.relative_to(REPO_ROOT)}"
        )
        assert "api.getAttribute" not in text, (
            f"Found invalid bucket-IAM CAB attribute in {tf_file.relative_to(REPO_ROOT)}"
        )

    deploy_sh = (REPO_ROOT / "scripts" / "deploy.sh").read_text(encoding="utf-8")
    assert "gcloud storage rsync" in deploy_sh
    assert "single_bucket_mode=" in deploy_sh
    assert "create_shared_bucket=" in deploy_sh
    assert "create_eval_dataset=" in deploy_sh
    assert "create_vertex_connection=" in deploy_sh


def test_skill_get_and_vault_ids_reject_traversal_before_storage_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GcpSkillRegistry.get_skill and GcpAuditVaultStore reject path traversal before touching GCS."""
    from bridge.vault import _vault_object_key, _vault_version_prefix

    reg = GcpSkillRegistry("demo-single-bucket", prefix="skills")

    def _boom() -> Any:
        raise AssertionError("storage bucket must not be accessed for traversing names")

    monkeypatch.setattr(reg, "_bucket", _boom)
    for bad_name in ("../evil", "../../etc/x", "a/b", "..", ".", ""):
        with pytest.raises(ValueError):
            reg.get_skill(bad_name)

    vault = GcpAuditVaultStore("demo-single-bucket", prefix="audit-vault")
    monkeypatch.setattr(vault, "_get_bucket", _boom)

    bad_segments = ("", ".", "..", "../escape", "ctx/sub", r"ctx\sub", " padded ")
    for bad in bad_segments:
        with pytest.raises(ValueError, match="context_id"):
            _vault_object_key(bad, "doc-1", 1, "quote.pdf", prefix="audit-vault")
        with pytest.raises(ValueError, match="context_id"):
            _vault_version_prefix(bad, "doc-1", 1, prefix="audit-vault")
        with pytest.raises(ValueError, match="context_id"):
            vault.lock_document(
                context_id=bad, doc_id="doc-1", filename="quote.pdf", data=b"x"
            )
        with pytest.raises(ValueError, match="context_id"):
            vault.get_record(bad, "doc-1", version=1)
        with pytest.raises(ValueError, match="context_id"):
            vault.read_bytes(bad, "doc-1", version=1)
        with pytest.raises(ValueError, match="context_id"):
            vault.list_records(context_id=bad)

        with pytest.raises(ValueError, match="doc_id"):
            _vault_object_key("ctx-1", bad, 1, "quote.pdf", prefix="audit-vault")
        with pytest.raises(ValueError, match="doc_id"):
            _vault_version_prefix("ctx-1", bad, 1, prefix="audit-vault")
        with pytest.raises(ValueError, match="doc_id"):
            vault.lock_document(
                context_id="ctx-1", doc_id=bad, filename="quote.pdf", data=b"x"
            )
        with pytest.raises(ValueError, match="doc_id"):
            vault.get_record("ctx-1", bad, version=1)
        with pytest.raises(ValueError, match="doc_id"):
            vault.read_bytes("ctx-1", bad, version=1)
