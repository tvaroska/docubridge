"""GCP adapters for skill registry, Cloud Tasks scheduler, Agent-Identity A2A client, extraction, and email ingress."""

from __future__ import annotations

import os
from typing import Any

from ..protocols import (
    EMAIL_KEY_PATTERN,
    A2AClientPort,
    EmailIngressPort,
    ExtractionPort,
    SchedulerPort,
    SkillRegistryPort,
    validate_attachment_slot,
    validate_landing_key,
)
from .common import (
    _LOG,
    BRIDGE_EXTRACTION_ENGINE_ENV,
    BRIDGE_FOLLOWUP_CALLBACK_URL_ENV,
    BRIDGE_GATEWAY_HOST_ENV,
    BRIDGE_RUNTIME_SA_EMAIL_ENV,
    BRIDGE_TASKS_QUEUE_ENV,
    EMAIL_BUCKET_ENV,
    SKILLS_BUCKET_ENV,
)


def _resolve_bucket(
    bucket_name: str | None = None, env: str = SKILLS_BUCKET_ENV
) -> str:
    """Resolve the Skill Registry GCS bucket: `bucket_name` arg wins, else env.

    Raises `RuntimeError` when neither is set — the gcp adapter REFUSES a silent
    fallback (mirrors `_resolve_dsn`; a misconfigured registry must fail loud, not
    quietly serve an empty/wrong bucket).
    """
    name = (bucket_name or os.environ.get(env, "")).strip()
    if not name:
        raise RuntimeError(
            f"GCP skill_registry requires a GCS bucket ({env} env or bucket_name=); "
            "refusing a silent fallback."
        )
    return name


def _validate_skill_paths(name: str, rels: Any) -> None:
    """Reject a traversing skill name / asset path BEFORE any storage I/O."""
    import posixpath
    import re

    if not re.fullmatch(r"[a-z0-9-]+", name or ""):
        raise ValueError(f"invalid skill name {name!r}: must match ^[a-z0-9-]+$")
    for rel in rels or ():
        dest = posixpath.normpath(f"{name}/{rel}")
        if dest != name and not dest.startswith(f"{name}/"):
            raise ValueError(f"asset path {rel!r} escapes skill dir")


def _download_prefix(bucket: Any, prefix: str, dest: Any) -> None:
    """Download every blob under `prefix` into `dest` preserving relative paths."""
    for blob in bucket.list_blobs(prefix=prefix):
        rel = blob.name[len(prefix) :]
        if not rel:
            continue
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        blob.download_to_filename(str(target))


class GcpSkillRegistry(SkillRegistryPort):
    """GCP `skill_registry` adapter (P7-3) — a GCS bucket of Agent Skills folders.

    The SAME folder format as `LocalSkillRegistry` (`<name>/SKILL.md`,
    `<name>/<asset>`) — "the format is the port contract" (`design/ports.md`), so
    local->gcp is a no-op for the Agent Card.
    """

    def __init__(self, bucket_name: str | None = None) -> None:
        self._bucket_name = bucket_name

    def _bucket(self) -> Any:
        name = _resolve_bucket(self._bucket_name)
        from google.cloud.storage import Client

        return Client().bucket(name)

    def list_skills(self) -> list[Any]:
        """List all skill packages discovered in the Cloud Storage skills bucket."""
        import tempfile
        from pathlib import Path

        from bridge.edge.skills import listed_skill_meta

        bucket = self._bucket()
        have_skill_md: set[str] = set()
        for blob in bucket.list_blobs():
            parts = blob.name.split("/", 1)
            if len(parts) < 2:
                continue
            if blob.name == f"{parts[0]}/SKILL.md":
                have_skill_md.add(parts[0])
        metas = []
        for name in sorted(have_skill_md):
            with tempfile.TemporaryDirectory() as tmp:
                dest = Path(tmp) / name
                _download_prefix(bucket, f"{name}/", dest)
                meta = listed_skill_meta(dest)
            if meta is not None:
                metas.append(meta)
        return metas

    def get_skill(self, name: str) -> Any:
        """Retrieve a single skill package definition by skill name."""
        import tempfile
        from pathlib import Path

        from bridge.edge.skills import skill_meta_from_dir

        bucket = self._bucket()
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / name
            _download_prefix(bucket, f"{name}/", dest)
            if not (dest / "SKILL.md").exists():
                raise KeyError(name)
            return skill_meta_from_dir(dest)

    def install_skill(
        self, name: str, skill_md: str, assets: dict[str, str] | None = None
    ) -> Any:
        """Upload and register a skill directory into the Cloud Storage skills bucket."""
        import tempfile
        from pathlib import Path

        from bridge.edge.skills import skill_meta_from_dir, validate_skill_upload

        _validate_skill_paths(name, assets or {})
        validate_skill_upload(name, skill_md, assets)
        bucket = self._bucket()
        bucket.blob(f"{name}/SKILL.md").upload_from_string(skill_md)
        for rel, content in (assets or {}).items():
            bucket.blob(f"{name}/{rel}").upload_from_string(content)
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / name
            _download_prefix(bucket, f"{name}/", dest)
            return skill_meta_from_dir(dest)

    def read_asset(self, name: str, rel: str) -> str:
        """Read a raw asset file belonging to an installed skill package."""
        _validate_skill_paths(name, [rel])
        blob = self._bucket().blob(f"{name}/{rel}")
        if not blob.exists():
            raise KeyError(f"{name}/{rel}")
        return blob.download_as_text()


# ---------------------------------------------------------------------------
# scheduler (P7-4) — Cloud Tasks bridge-followups queue
# ---------------------------------------------------------------------------

_ADVANCE_UNSUPPORTED = (
    "Cloud Tasks is wall-clock/push-driven: the queue fires the OIDC callback at "
    "schedule_time. Virtual advance()/advance_to() is a local-only VirtualClock "
    "test affordance and has no meaning against a real queue (terminal-outcome "
    "parity, not ledger-identical)."
)


def _resolve_scheduler_config(value: str | None, env: str, what: str) -> str:
    """Resolve one required scheduler config value: arg wins, else env, else refuse."""
    v = (value or os.environ.get(env, "")).strip()
    if not v:
        raise RuntimeError(
            f"GCP scheduler requires {what} ({env} env or the build_scheduler arg); "
            "refusing a silent in-process fallback (the scheduler analogue of the "
            "session-affinity trap)."
        )
    return v


def _task_id(key: str) -> str:
    """Derive a deterministic Cloud Tasks task id from an alarm `key`."""
    import re

    return re.sub(r"[^A-Za-z0-9_-]", "_", key)


def _schedule_seconds(schedule_time: Any) -> int:
    """Best-effort epoch-seconds from a task's `schedule_time`."""
    if schedule_time is None:
        return 0
    if isinstance(schedule_time, dict):
        return int(schedule_time.get("seconds", 0))
    if hasattr(schedule_time, "timestamp"):
        return int(schedule_time.timestamp())
    if hasattr(schedule_time, "seconds"):
        return int(schedule_time.seconds)
    return 0


class _GcpCloudTasksScheduler:
    """Cloud Tasks-backed `Scheduler` (P7-4) — satisfies `bridge.scheduler.Scheduler`."""

    def __init__(
        self,
        *,
        queue_path: str,
        callback_url: str,
        oidc_sa_email: str,
        client: Any = None,
    ) -> None:
        self._queue_path = queue_path
        self._callback_url = callback_url
        self._oidc_sa_email = oidc_sa_email
        self._client_obj = client

    def _client(self) -> Any:
        if self._client_obj is None:
            from google.cloud import tasks_v2

            self._client_obj = tasks_v2.CloudTasksClient()
        return self._client_obj

    def _task_name(self, key: str) -> str:
        return f"{self._queue_path}/tasks/{_task_id(key)}"

    def _delete_quietly(self, client: Any, name: str) -> None:
        import contextlib

        from google.api_core.exceptions import NotFound

        with contextlib.suppress(NotFound):
            client.delete_task(name=name)

    def now(self) -> int:
        """Return the current UTC timestamp from the scheduler clock."""
        import time

        return int(time.time())

    def schedule(self, key: str, fire_at: int, payload: Any = None) -> Any:
        """Mint a Cloud Task firing at `fire_at` (absolute epoch seconds)."""
        import json

        from bridge.scheduler import Alarm

        client = self._client()
        name = self._task_name(key)
        self._delete_quietly(client, name)
        body = json.dumps(
            {"key": key, "payload": payload}, separators=(",", ":")
        ).encode()
        task = {
            "name": name,
            "schedule_time": {"seconds": int(fire_at)},
            "http_request": {
                "http_method": "POST",
                "url": self._callback_url,
                "headers": {"Content-Type": "application/json"},
                "body": body,
                "oidc_token": {
                    "service_account_email": self._oidc_sa_email,
                    "audience": self._callback_url,
                },
            },
        }
        client.create_task(parent=self._queue_path, task=task)
        return Alarm(key=key, fire_at=int(fire_at), payload=payload)

    def cancel(self, key: str) -> bool:
        """Delete the task by its derived name; `True` if it existed, `False` on `NotFound`."""
        from google.api_core.exceptions import NotFound

        client = self._client()
        try:
            client.delete_task(name=self._task_name(key))
            return True
        except NotFound:
            return False

    def pending(self) -> list[Any]:
        """List armed tasks as `Alarm`s."""
        import json

        from bridge.scheduler import Alarm

        alarms = []
        for task in self._client().list_tasks(parent=self._queue_path):
            if isinstance(task, dict):
                http = task.get("http_request", {}) or {}
                body = http.get("body", b"")
                st = task.get("schedule_time")
            else:
                body = task.http_request.body
                st = task.schedule_time
            key, payload = "", None
            if body:
                data = json.loads(body)
                key, payload = data.get("key", ""), data.get("payload")
            alarms.append(
                Alarm(key=key, fire_at=_schedule_seconds(st), payload=payload)
            )
        return alarms

    def advance(self, ticks: int) -> list[Any]:
        """Advance the scheduler clock by a relative duration and fire due callbacks."""
        raise NotImplementedError(_ADVANCE_UNSUPPORTED)

    def advance_to(self, tick: int) -> list[Any]:
        """Advance the scheduler clock to a target UTC timestamp and fire due callbacks."""
        raise NotImplementedError(_ADVANCE_UNSUPPORTED)


class GcpScheduler(SchedulerPort):
    """GCP scheduler adapter (P7-4) — the `bridge-followups` Cloud Tasks queue."""

    def __init__(
        self,
        queue_path: str | None = None,
        callback_url: str | None = None,
        oidc_sa_email: str | None = None,
    ) -> None:
        self._queue_path = queue_path
        self._callback_url = callback_url
        self._oidc_sa_email = oidc_sa_email

    def build_scheduler(
        self,
        client: Any = None,
        *,
        queue_path: str | None = None,
        callback_url: str | None = None,
        oidc_sa_email: str | None = None,
    ) -> Any:
        """Build the Cloud Tasks scheduler (injectable-port idiom, P7-8 wiring-guard)."""
        return _GcpCloudTasksScheduler(
            queue_path=_resolve_scheduler_config(
                queue_path or self._queue_path,
                BRIDGE_TASKS_QUEUE_ENV,
                "a Cloud Tasks queue path",
            ),
            callback_url=_resolve_scheduler_config(
                callback_url or self._callback_url,
                BRIDGE_FOLLOWUP_CALLBACK_URL_ENV,
                "a follow-up callback URL",
            ),
            oidc_sa_email=_resolve_scheduler_config(
                oidc_sa_email or self._oidc_sa_email,
                BRIDGE_RUNTIME_SA_EMAIL_ENV,
                "the runtime SA email for the OIDC token",
            ),
            client=client,
        )


# ---------------------------------------------------------------------------
# a2a_client (P7-5) — the Agent-Identity outbound transport
# ---------------------------------------------------------------------------


def _resolve_a2a_config(value: str | None, env: str, what: str) -> str:
    """Resolve one required a2a_client config value: arg wins, else env, else refuse."""
    v = (value or os.environ.get(env, "")).strip()
    if not v:
        raise RuntimeError(
            f"GCP a2a_client requires {what} ({env} env or the build_client arg); "
            "refusing a silent in-process/fake-transport fallback (the a2a_client "
            "analogue of the session-affinity trap)."
        )
    return v


def _host_token(value: str) -> str:
    """Normalize a host / URL / host:port to a comparable `host[:port]` token."""
    from urllib.parse import urlparse

    parsed = urlparse(value if "//" in value else f"//{value}")
    host = parsed.hostname or ""
    return f"{host}:{parsed.port}" if parsed.port is not None else host


def reconcile_card_host(advertised_url: str, gateway_host: str) -> str:
    """Reconcile a peer card's advertised URL against the Gateway-authorized host.

    Under `google-adk >= 2.11.0` same-origin card validation
    (`RemoteA2aAgent._validate_card_rpc_targets`: a card fetched over the network
    may only aim RPC traffic at its own origin), the legacy `.mtls` card-repoint
    hack is deliberately NOT ported. The advertised host MUST equal the
    Gateway-authorized host; a mismatch raises `ValueError` naming both hosts.
    """
    advertised = _host_token(advertised_url)
    authorized = _host_token(gateway_host)
    if advertised != authorized:
        raise ValueError(
            f"peer Agent Card host {advertised!r} (from {advertised_url!r}) does not "
            f"match the Gateway-authorized host {authorized!r}; refusing to send "
            "(adk >=2.7 same-origin — no .mtls repoint). Fix the peer card or "
            "BRIDGE_GATEWAY_HOST, do not repoint."
        )
    return advertised_url


class _AgentIdentityTransport:
    """Agent-Identity outbound transport (P7-5) — satisfies `OutboundTransport`."""

    def __init__(
        self,
        *,
        gateway_host: str,
        sa_email: str,
        id_token_fetcher: Any = None,
        sender: Any = None,
    ) -> None:
        self._gateway_host = gateway_host
        self._sa_email = sa_email
        self._id_token_fetcher = id_token_fetcher
        self._sender = sender

    def _fetch_id_token(self, audience: str) -> str:
        if self._id_token_fetcher is not None:
            return self._id_token_fetcher(audience)
        import google.auth.transport.requests
        from google.oauth2 import id_token

        return id_token.fetch_id_token(
            google.auth.transport.requests.Request(), audience
        )

    def _post(self, url: str, headers: dict[str, str], body: bytes) -> None:
        if self._sender is not None:
            self._sender(url=url, headers=headers, body=body)
            return
        import httpx

        httpx.Client().post(url, headers=headers, content=body)

    def deliver(self, message: Any) -> None:
        """Deliver a signed A2A envelope to a remote peer endpoint with Cloud Run OIDC credentials."""
        target_url = getattr(message, "card_url", None)
        if not target_url:
            return
        url = reconcile_card_host(target_url, self._gateway_host)
        id_token = self._fetch_id_token(url)
        headers = {
            "Authorization": f"Bearer {id_token}",
            "Content-Type": "application/json",
        }
        body = str(getattr(message, "message", "")).encode()
        self._post(url, headers, body)


class GcpA2AClient(A2AClientPort):
    """GCP `a2a_client` adapter (P7-5) — Agent-Identity outbound transport."""

    def __init__(
        self, gateway_host: str | None = None, sa_email: str | None = None
    ) -> None:
        self._gateway_host = gateway_host
        self._sa_email = sa_email

    def build_client(
        self,
        *,
        gateway_host: str | None = None,
        sa_email: str | None = None,
        id_token_fetcher: Any = None,
        sender: Any = None,
    ) -> Any:
        """Build the Agent-Identity outbound client (injectable-port idiom)."""
        from bridge.outbound import OutboundClient

        transport = _AgentIdentityTransport(
            gateway_host=_resolve_a2a_config(
                gateway_host or self._gateway_host,
                BRIDGE_GATEWAY_HOST_ENV,
                "the Gateway-authorized host",
            ),
            sa_email=_resolve_a2a_config(
                sa_email or self._sa_email,
                BRIDGE_RUNTIME_SA_EMAIL_ENV,
                "the runtime SA email for the OIDC token",
            ),
            id_token_fetcher=id_token_fetcher,
            sender=sender,
        )
        return OutboundClient(transport)


# ---------------------------------------------------------------------------
# extraction (P7-6) — the Gemini extraction engine behind the port
# ---------------------------------------------------------------------------


class GcpExtraction(ExtractionPort):
    """GCP extraction adapter (P7-6) — the deployed Gemini engine behind the port."""

    def __init__(self, engine: str | None = None) -> None:
        self._engine_name = engine

    def _resolve_engine(self) -> Any:
        """Select the extraction engine: arg wins, else env, else gemini default."""
        name = (
            self._engine_name or os.environ.get(BRIDGE_EXTRACTION_ENGINE_ENV, "gemini")
        ).strip() or "gemini"
        if name == "gemini":
            from bridge.extraction.gemini import GeminiExtractor

            return GeminiExtractor()
        if name == "docai":
            from bridge.extraction.docai import DocAIPrepass
            from bridge.extraction.gemini import GeminiExtractor

            return GeminiExtractor(prepass=DocAIPrepass())
        if name == "fixture":
            from bridge.extraction.fixture import FixtureExtractor

            return FixtureExtractor()
        raise ValueError(
            f"unknown extraction engine {name!r}: expected fixture|gemini|docai"
        )

    async def extract(
        self,
        artifact_name: str,
        *,
        target_doctype: str | None = None,
        candidate_doctypes: Any = None,
        requested_fields: Any = None,
        context_set: str | None = None,
        ontology_revision: str | None = None,
    ) -> Any:
        """Run structured document extraction via Document AI and Gemini schema mapping."""
        return await self._resolve_engine().extract(
            artifact_name,
            target_doctype=target_doctype,
            candidate_doctypes=candidate_doctypes,
            requested_fields=requested_fields,
            context_set=context_set,
            ontology_revision=ontology_revision,
        )


# ---------------------------------------------------------------------------
# email_ingress (S0-arch-13) — the GCS mailbox landing bucket
# ---------------------------------------------------------------------------


class GcpEmailIngress(EmailIngressPort):
    """GCP `email_ingress` adapter (S0-arch-13) — a GCS landing bucket."""

    def __init__(self, bucket_name: str | None = None) -> None:
        self._bucket_name = bucket_name

    def _name(self) -> str:
        name = (self._bucket_name or os.environ.get(EMAIL_BUCKET_ENV, "")).strip()
        if not name:
            raise RuntimeError(
                f"GCP email_ingress requires a GCS bucket ({EMAIL_BUCKET_ENV} env or "
                "bucket_name=); refusing a silent fallback."
            )
        return name

    def _bucket(self) -> Any:
        name = self._name()
        from google.cloud.storage import Client

        return Client().bucket(name)

    @staticmethod
    def _create_only(blob: Any, data: bytes, content_type: str) -> bool:
        from google.api_core.exceptions import Forbidden, PreconditionFailed

        try:
            blob.upload_from_string(
                data, content_type=content_type, if_generation_match=0
            )
        except PreconditionFailed:
            return False
        except Forbidden:
            if blob.exists():
                return False
            raise
        return True

    def land(self, key: str, raw: bytes) -> bool:
        """Persist an inbound raw MIME email message into the Cloud Storage ingress prefix."""
        validate_landing_key(key)
        blob = self._bucket().blob(f"inbox/{key}.eml")
        return self._create_only(blob, raw, "message/rfc822")

    def pending(self) -> list[str]:
        """List message IDs currently waiting in the pending ingress prefix."""
        import re

        bucket = self._bucket()
        done = {
            blob.name[len("processed/") : -len(".json")]
            for blob in bucket.list_blobs(prefix="processed/")
            if blob.name.endswith(".json")
        }
        rows = []
        for blob in bucket.list_blobs(prefix="inbox/"):
            rel = blob.name[len("inbox/") :]
            key = rel.removesuffix(".eml")
            if not rel.endswith(".eml") or not re.fullmatch(EMAIL_KEY_PATTERN, key):
                _LOG.warning(
                    "email_ingress: skipping unexpected inbox object %r", blob.name
                )
                continue
            if key in done:
                continue
            rows.append((blob.time_created, key))
        return [key for _, key in sorted(rows)]

    def read(self, key: str) -> bytes:
        """Read the raw MIME bytes for a pending or processed ingress message ID."""
        validate_landing_key(key)
        blob = self._bucket().blob(f"inbox/{key}.eml")
        if not blob.exists():
            raise KeyError(key)
        return blob.download_as_bytes()

    def put_attachment(
        self, key: str, index: int, filename: str, data: bytes, mime_type: str
    ) -> str:
        """Upload an extracted MIME attachment to the Cloud Storage attachment prefix."""
        validate_landing_key(key)
        validate_attachment_slot(index, filename)
        name = f"attachments/{key}/{index}/{filename}"
        bucket = self._bucket()
        self._create_only(bucket.blob(name), data, mime_type)
        return f"gs://{bucket.name}/{name}"

    def mark_processed(self, key: str, outcome: dict) -> bool:
        """Move a processed message from the pending prefix to the processed archive prefix."""
        import json

        validate_landing_key(key)
        blob = self._bucket().blob(f"processed/{key}.json")
        body = json.dumps(outcome, sort_keys=True).encode()
        return self._create_only(blob, body, "application/json")

    def processed(self, key: str) -> dict | None:
        """Return the processed outcome marker for `key`, or `None` if unprocessed."""
        import json

        validate_landing_key(key)
        blob = self._bucket().blob(f"processed/{key}.json")
        if not blob.exists():
            return None
        return json.loads(blob.download_as_bytes())
