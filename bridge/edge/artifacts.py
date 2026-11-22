"""Scoped artifact persistence + fetch endpoint for the Bridge edge (ART-2).

The whole artifact concern in one edge module: on inbound the edge persists the
referenced document bytes into an ADK ``ArtifactService`` (versioned per
resubmission = ART-1 history); on outbound it rewrites each ledger entry's
``ArtifactRef.uri`` to a **scoped fetch endpoint** hosted on the same ASGI app. A
consumer reads the ``ExchangeTurn`` and GETs the bytes on demand from that
endpoint. Every fetch is **leg-scoped**: a foreign ``context_id``/filename → 404
(a reference is not a capability — ``design/artifacts.md``).

**Not a port (Decision 1).** The artifact store is NOT one of the managed
``Port``s. ``design/artifacts.md`` is explicit that documents use the
platform's native ``ArtifactService`` "as-is rather than inventing a store":
``InMemoryArtifactService`` locally, ``GcsArtifactService`` on deploy — a native
backend swap (like Sessions' ``InMemory…↔Vertex``), not a new managed port. No
``@pytest.mark.port`` here; this follows the port-in-waiting idiom
(``bridge/edge/intake.py`` Decision 1): a local default + a documented swap point.

**P7-3 swap points (Decision 2 + 7) — now built.**
- *Store backend:* :func:`build_artifact_store` returns ``GcsArtifactService`` when
  ``BRIDGE_ARTIFACT_BUCKET`` is set, else ``InMemoryArtifactService`` — a native
  backend swap (like Sessions' ``InMemory…↔Vertex``), injectable so an explicit
  store still wins (the P7-8 wiring-guard idiom).
- *Fetch serving:* :func:`build_fetch_app` streams bytes directly locally
  (``signer=None``). On deploy the same endpoint, given a :data:`Signer`
  (:func:`gcs_signer`), **authorizes on the leg boundary first** (404 on a
  foreign/unknown ``(context_id, filename)``) then returns a 307 redirect to a
  time-limited V4 GCS signed URL — **bytes never ride the response** (the route +
  contract stay put). Signing on Cloud Run uses IAM SignBlob via the SA's existing
  self-scoped ``serviceAccountTokenCreator`` grant (P7-1) — no private key, no new
  IAM. See ``design/runtime.md`` §runtime.
- *Inbound byte source:* :func:`resolve_bytes` is the edge's never-raising wrapper
  around the shared artifact resolver (``bridge/extraction/resolve.py``): a
  corpus-relative name, a party's http(s) URL fetched under the default-deny
  egress guard, or an opt-in ``file://`` path. Injectable so tests can supply
  bytes directly.

**ART-3 — Artifact retention / no GC (P4-5, P7-3).** Artifact versions are retained
for the life of the exchange (ART-1 versioning is the history). The post-terminal
retention window is enforced by the backend's object-lifecycle policy — the GCS
object-lifecycle **Delete** rule declared in ``terraform/modules/gcs`` (the
``retention_days`` lifecycle_rule on the artifacts bucket), **not bespoke Bridge
GC**. The Bridge has, and must keep, zero artifact-GC/TTL code. The only deletion
path is the off-script explicit erase (``handle_deletion_request`` in
``bridge/offscript.py``), which is a user action, not retention GC.

**Edge-only (Decision 3).** All ``google.adk`` artifact persistence + serving
lives here in ``bridge/edge/`` (which already imports ``google.adk``), never in
``bridge/collect.py`` (pure Bridge core, ``google.adk``-free). The ``Collector``
signature is unchanged; persistence + uri-rewrite are an additive layer the
executor orchestrates around ``collect``.

**Isolation guard:** ``bridge/edge/`` must NEVER import ``agents/``. This module
imports only ``contract/``, ``bridge.*``, ``google.adk.artifacts``,
``google.genai.types``, ``fastapi``/``starlette``, and stdlib.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Awaitable, Callable
from urllib.parse import quote
from weakref import WeakKeyDictionary

import google.genai.types as genai_types
from fastapi import FastAPI, Request, Response
from fastapi.responses import RedirectResponse
from google.adk.artifacts import BaseArtifactService

from bridge.collect import DocumentSubmission
from bridge.edge.auth import authenticated_principal
from bridge.extraction.resolve import maybe_await, mime_for, resolve_document
from bridge.identity import is_auth_enforced

_LOG = logging.getLogger("bridge.edge.artifacts")

# ADK artifact key components (Decision 5). ``app_name`` reuses the name from
# ``bridge/agent.py::build_app``; ``user_id`` falls back to a constant when the
# request carries no party (the one-turn path).
APP_NAME = "document_bridge"
UNSCOPED_USER = "_unscoped"
ARTIFACT_INDEX_USER = "_bridge_index"
ARTIFACTS_PREFIX = "/artifacts"

_RECORDED_PARTIES: WeakKeyDictionary[BaseArtifactService, dict[str, set[str]]] = (
    WeakKeyDictionary()
)

# P7-3 deploy swap. When set (by the runtime module's plain env, from the gcs
# Terraform module) the artifact store is ``GcsArtifactService`` and the fetch
# endpoint mints time-limited V4 signed URLs instead of streaming bytes. Unset =
# the local ``InMemoryArtifactService`` + direct byte stream. A bucket name is not
# a secret (rides a plain env, never a secret_key_ref).
ARTIFACT_BUCKET_ENV = "BRIDGE_ARTIFACT_BUCKET"

# Signed-URL lifetime (minutes). Short-lived — the URL is a time-boxed capability
# to ONE already-authorized blob, minted only after the leg-scoping 404 check.
SIGNED_URL_TTL_MIN = 15

# A signer maps an authorized (context_id, filename, user_id, version) to a
# time-limited URL the client fetches the bytes from directly (bytes never ride
# the response). ``None`` = the local direct-stream path.
Signer = Callable[[str, str, str, "int | None"], str]

# A resolver maps an inbound URI to (bytes, mime_type) or None when unresolvable.
# May be sync (the injected test resolvers) or async (the default): the call site
# goes through ``maybe_await``.
Resolver = Callable[
    [str], "tuple[bytes, str] | Awaitable[tuple[bytes, str] | None] | None"
]

# The suffix→mime table lives with the shared resolver (one table, two callers).
_mime_for = mime_for


async def resolve_bytes(uri: str) -> tuple[bytes, str] | None:
    """Resolve an inbound document URI to ``(bytes, mime_type)``, or ``None``.

    Bytes never ride the wire (``design/artifacts.md``), so the edge must obtain
    them from the referenced URI: a corpus-relative name
    (``images/gov-id-clean.svg``), a party's ``http(s)`` URL fetched under the
    default-deny egress guard, or an opt-in ``file://`` path — the shared resolver
    (``bridge/extraction/resolve.py``) owns that dispatch and its security posture.

    This wrapper **never raises** (Decision 7): an unresolvable, refused or failing
    URI logs a WARNING and returns ``None``, and the caller then skips persistence
    and leaves the ref unchanged so the disposition turn still runs. That is
    deliberately the opposite of the extraction path, which refuses loud — a
    document the *engine* cannot read must not be silently substituted, while a
    document the *store* cannot persist only costs the ART-1 byte history.

    "Never" is taken literally, in two layers: the shared resolver normalizes every
    fetch failure (transport included) into ``ValueError``, and this catch is the base
    ``Exception`` anyway. An enumerated tuple is what broke here before — a party's
    artifact host being briefly unreachable raised ``httpx.ConnectError`` straight past
    ``(ValueError, OSError)`` and failed the whole disposition turn, the exact outcome
    Decision 7 exists to prevent. ``asyncio.CancelledError`` is a ``BaseException`` and
    is deliberately NOT caught: a cancelled turn must stay cancelled.

    A **by-reference** resolve (``gs://``) also returns ``None``: there are no bytes
    to persist, the consumer's ``gs://`` ref stays on the ledger unrewritten, and the
    extraction engine reads the object natively. A deliberate asymmetry, not a gap.
    """
    if not uri:
        return None
    try:
        resolved = await resolve_document(uri)
    except Exception as exc:  # noqa: BLE001 - Decision 7: never fail a turn when artifact persistence fails
        _LOG.warning("artifact %s not persisted: %s: %s", uri, type(exc).__name__, exc)
        return None
    if resolved.by_reference or resolved.data is None:
        return None
    return resolved.data, resolved.mime_type


def _validate_artifact_segment(
    value: str, label: str, *, forbid_index_user: bool = False
) -> None:
    cleaned = value.strip()
    if (
        not cleaned
        or cleaned != value
        or "/" in value
        or "\\" in value
        or value in {".", ".."}
        or ".." in value
        or any(ord(ch) < 32 or ord(ch) == 127 for ch in value)
    ):
        raise ValueError(f"Unsafe artifact {label}: {value!r}")
    if forbid_index_user and value == ARTIFACT_INDEX_USER:
        raise ValueError(
            f"Reserved artifact {label} {ARTIFACT_INDEX_USER!r} is forbidden."
        )


def filename_for(submission: DocumentSubmission) -> str:
    """Derive the ADK artifact ``filename`` for a submission (Decision 5).

    Prefers ``ArtifactRef.name`` when set, else the basename of the artifact URI
    (``images/gov-id-clean.svg`` → ``gov-id-clean.svg``), else the doc id.
    """
    ref = submission.artifact
    if ref is not None and ref.name:
        return ref.name
    if ref is not None and ref.uri:
        return os.path.basename(ref.uri)
    return submission.doc_id


async def _lookup_artifact_owner(
    store: BaseArtifactService,
    *,
    context_id: str,
    filename: str,
) -> tuple[str, str | None] | None:
    """Resolve ``(party, caller_principal)`` via the store's public ``load_artifact`` API."""
    part = await store.load_artifact(
        app_name=APP_NAME,
        user_id=ARTIFACT_INDEX_USER,
        session_id=context_id,
        filename=filename,
    )
    if part is None or part.inline_data is None or not part.inline_data.data:
        return None
    try:
        payload = json.loads(part.inline_data.data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    party = str(payload.get("party") or "").strip()
    if not party:
        return None
    caller_raw = payload.get("caller_principal")
    caller = (
        str(caller_raw).strip()
        if isinstance(caller_raw, str) and caller_raw.strip()
        else None
    )
    return party, caller


async def _record_artifact_owner(
    store: BaseArtifactService,
    *,
    context_id: str,
    filename: str,
    party: str,
    caller_principal: str | None,
) -> None:
    """Persist ``(context_id, filename) -> (party, caller_principal)`` through public store APIs."""
    try:
        by_ctx = _RECORDED_PARTIES.setdefault(store, {})
        by_ctx.setdefault(context_id, set()).add(party)
    except TypeError:
        pass
    existing = await _lookup_artifact_owner(
        store, context_id=context_id, filename=filename
    )
    if existing == (party, caller_principal):
        return
    manifest = json.dumps(
        {"party": party, "caller_principal": caller_principal},
        separators=(",", ":"),
    ).encode("utf-8")
    await store.save_artifact(
        app_name=APP_NAME,
        user_id=ARTIFACT_INDEX_USER,
        session_id=context_id,
        filename=filename,
        artifact=genai_types.Part.from_bytes(
            data=manifest, mime_type="application/json"
        ),
    )


async def persist_submission(  # noqa: PLR0913 - public artifact submission persistence contract
    store: BaseArtifactService,
    *,
    party: str,
    context_id: str,
    submission: DocumentSubmission,
    resolver: Resolver = resolve_bytes,
    caller_principal: str | None = None,
) -> int | None:
    """Persist one submission's bytes into the artifact store; return the version.

    Resolves the referenced bytes via ``resolver`` (sync or async — the default is
    the async :func:`resolve_bytes`) → ``save_artifact`` keyed by
    ``(APP_NAME, party, context_id, filename)`` (the leg-scoping key, Decision 5)
    → returns the new version (0-based, one per resubmission = ART-1). Returns
    ``None`` when the URI is unresolvable — persistence is skipped and the ref is
    left unchanged so the disposition turn still runs (Decision 7).
    """
    ref = submission.artifact
    if ref is None or not ref.uri:
        return None
    filename = filename_for(submission)
    _validate_artifact_segment(party, "party", forbid_index_user=True)
    _validate_artifact_segment(context_id, "context_id")
    _validate_artifact_segment(filename, "filename")
    existing = await _lookup_artifact_owner(
        store, context_id=context_id, filename=filename
    )
    if existing is not None and caller_principal != existing[1]:
        raise PermissionError(
            f"Artifact '{context_id}/{filename}' is owned by another principal."
        )
    resolved = await maybe_await(resolver(ref.uri))
    if resolved is None:
        return None
    data, mime_type = resolved
    part = genai_types.Part.from_bytes(data=data, mime_type=mime_type)
    version = await store.save_artifact(
        app_name=APP_NAME,
        user_id=party,
        session_id=context_id,
        filename=filename,
        artifact=part,
    )
    await _record_artifact_owner(
        store,
        context_id=context_id,
        filename=filename,
        party=party,
        caller_principal=caller_principal,
    )
    return version


def scoped_uri(base_url: str, *, context_id: str, filename: str, version: int) -> str:
    """Build the absolute scoped fetch URI (Decision 6).

    ``{base}/artifacts/{context_id}/{filename}?v={version}`` with ``context_id``
    and ``filename`` URL-encoded (a hex context id / a dotted filename). Absolute
    so a separate-process consumer (P3) can resolve it.
    """
    base = base_url.rstrip("/")
    ctx = quote(context_id, safe="")
    name = quote(filename, safe="")
    return f"{base}{ARTIFACTS_PREFIX}/{ctx}/{name}?v={version}"


def _parse_artifact_bucket(raw: str) -> tuple[str, str]:
    """Parse `<bucket>`, `gs://<bucket>`, or `<bucket>/document_bridge` for ADK GCS storage.

    ADK ``GcsArtifactService`` always stores session artifacts under the top-level
    ``document_bridge/{user_id}/{session_id}/{filename}/{version}`` prefix, so both
    ``<bucket>`` and ``<bucket>/document_bridge`` resolve to the same bucket without
    double-prefixing. Any other prefix is rejected loudly so the store, signed URLs,
    and single-bucket GCS lifecycle/IAM rules cannot drift.
    """
    cleaned = (raw or "").strip()
    if cleaned.startswith("gs://"):
        cleaned = cleaned[len("gs://") :]
    cleaned = cleaned.strip("/")
    bucket, _, embedded = cleaned.partition("/")
    prefix = "/".join(seg for seg in embedded.strip("/").split("/") if seg)
    if any(seg in {".", ".."} for seg in prefix.split("/") if seg):
        raise ValueError(
            f"invalid {ARTIFACT_BUCKET_ENV} prefix {prefix!r}: path traversal forbidden"
        )
    if prefix and prefix != APP_NAME:
        raise ValueError(
            f"{ARTIFACT_BUCKET_ENV} prefix {prefix!r} is incompatible with ADK "
            f"GcsArtifactService (objects always live under '{APP_NAME}/'); "
            f"use '{bucket}' or '{bucket}/{APP_NAME}'."
        )
    return bucket, prefix


def build_artifact_store() -> BaseArtifactService:
    """Build the artifact store from env: GCS on deploy, in-memory locally (P7-3).

    Returns ``GcsArtifactService(bucket_name=...)`` when ``BRIDGE_ARTIFACT_BUCKET``
    is set (accepting ``<bucket>``, ``gs://<bucket>``, or ``<bucket>/document_bridge``;
    ADK always writes under ``document_bridge/{user_id}/{session_id}/{filename}/{version}``),
    else ``InMemoryArtifactService``. Callers keep the injectable
    ``param or build_artifact_store()`` idiom so an explicit store still wins.
    """
    raw = os.environ.get(ARTIFACT_BUCKET_ENV, "").strip()
    if raw:
        bucket, _ = _parse_artifact_bucket(raw)
        from google.adk.artifacts import GcsArtifactService

        return GcsArtifactService(bucket_name=bucket)
    from google.adk.artifacts import InMemoryArtifactService

    return InMemoryArtifactService()


def _adk_gcs_blob_path(
    user_id: str, context_id: str, filename: str, version: int
) -> str:
    """The ADK ``GcsArtifactService`` object path for a session-scoped artifact.

    Mirrors ADK's ``_get_blob_name`` for a non-``user:``-namespaced filename:
    ``document_bridge/{user_id}/{session_id}/{filename}/{version}`` (session_id ==
    the exchange ``context_id``). Kept in lockstep with the store and single-bucket
    ``document_bridge/`` lifecycle/IAM prefix rules.
    """
    return f"{APP_NAME}/{user_id}/{context_id}/{filename}/{version}"


def gcs_signer(bucket_name: str) -> Signer:
    """Return a :data:`Signer` that mints a V4 GCS signed URL for one blob (P7-3).

    Accepts ``<bucket>``, ``gs://<bucket>``, or ``<bucket>/document_bridge`` and
    always signs ``document_bridge/{user_id}/{context_id}/{filename}/{version}``.
    On Cloud Run the signing uses IAM SignBlob via the ``bridge-runtime`` SA's
    existing self-scoped ``serviceAccountTokenCreator`` grant (P7-1) — no private
    key, no new IAM. The client is imported lazily (module import stays dep-free).
    """
    from datetime import timedelta

    resolved_bucket, _ = _parse_artifact_bucket(bucket_name)

    def _sign(context_id: str, filename: str, user_id: str, version: int | None) -> str:
        from google.cloud.storage import Client

        blob = (
            Client()
            .bucket(resolved_bucket)
            .blob(_adk_gcs_blob_path(user_id, context_id, filename, version or 0))
        )
        return str(
            blob.generate_signed_url(
                expiration=timedelta(minutes=SIGNED_URL_TTL_MIN), version="v4"
            )
        )

    return _sign


def build_fetch_signer() -> Signer | None:
    """Return a GCS signer when ``BRIDGE_ARTIFACT_BUCKET`` is set, else ``None``.

    ``None`` selects the local direct-stream fetch path (unchanged); a signer
    selects the deploy redirect-to-signed-URL path (bytes never on the wire).
    """
    bucket = os.environ.get(ARTIFACT_BUCKET_ENV, "").strip()
    return gcs_signer(bucket) if bucket else None


async def _authorize_fetch_candidates(
    store: BaseArtifactService,
    request: Request,
    *,
    context_id: str,
    filename: str,
    require_auth: bool,
) -> list[str] | Response:
    caller = authenticated_principal(request)
    enforced = is_auth_enforced(require_auth)
    if enforced and caller is None:
        return Response(status_code=401)
    try:
        _validate_artifact_segment(context_id, "context_id")
        _validate_artifact_segment(filename, "filename")
    except ValueError:
        return Response(status_code=404)
    indexed = await _lookup_artifact_owner(
        store, context_id=context_id, filename=filename
    )
    if indexed is None:
        return (
            Response(status_code=404)
            if enforced
            else list(_candidate_users(store, context_id))
        )
    indexed_party, recorded_principal = indexed
    if recorded_principal is None:
        return Response(status_code=404) if enforced else [indexed_party]
    if caller is None:
        return Response(status_code=401)
    return (
        [indexed_party] if caller == recorded_principal else Response(status_code=404)
    )


def build_fetch_app(
    store: BaseArtifactService,
    *,
    resolver: Resolver | None = None,
    signer: Signer | None = None,
    require_auth: bool = False,
) -> FastAPI:
    """Build the scoped artifact-fetch FastAPI sub-app (Decision 2, 9).

    One GET route ``/{context_id}/{filename}`` (mounted at ``/artifacts``) with an
    optional ``?v={version}`` (default = latest). Enforces both leg scoping
    (``(context_id, filename)`` → 404) and caller-principal ownership when recorded
    or when authentication is enforced (401 unauthenticated / 404 foreign caller).

    Owner resolution uses the public ``BaseArtifactService`` API
    (``_bridge_index`` metadata record written by :func:`persist_submission`) so
    party-scoped artifacts resolve identically on ``GcsArtifactService`` and
    ``InMemoryArtifactService`` without touching store internals.
    """
    app = FastAPI()

    @app.get("/{context_id}/{filename}")
    async def fetch_artifact(
        request: Request, context_id: str, filename: str, v: int | None = None
    ) -> Response:
        authorized = await _authorize_fetch_candidates(
            store,
            request,
            context_id=context_id,
            filename=filename,
            require_auth=require_auth,
        )
        if isinstance(authorized, Response):
            return authorized
        if signer is not None:
            owner = await _resolve_owner(
                store, context_id, filename, v, candidates=authorized
            )
            if owner is None:
                return Response(status_code=404)  # authorize before you sign
            user_id, version = owner
            return RedirectResponse(
                signer(context_id, filename, user_id, version), status_code=307
            )
        part = await _load_any_party(
            store, context_id, filename, v, candidates=authorized
        )
        if part is None or part.inline_data is None:
            return Response(status_code=404)
        blob = part.inline_data
        return Response(
            content=blob.data,
            media_type=blob.mime_type or "application/octet-stream",
        )

    return app


def _candidate_users(store: BaseArtifactService, context_id: str) -> set[str]:
    """Candidate ``user_id`` owners of a ``context_id`` when no index record exists."""
    users: set[str] = {UNSCOPED_USER}
    try:
        recorded = _RECORDED_PARTIES.get(store, {}).get(context_id)
        if recorded:
            users.update(recorded)
    except TypeError:
        pass
    artifacts = getattr(store, "artifacts", None)
    if isinstance(artifacts, dict):
        for key in artifacts:
            # Key format: "app_name/user_id/session_id/filename" for session-scoped.
            parts = key.split("/")
            if (
                len(parts) >= 3
                and parts[0] == APP_NAME
                and parts[2] == context_id
                and parts[1] != ARTIFACT_INDEX_USER
            ):
                users.add(parts[1])
    return users


def owning_parties(store: BaseArtifactService, context_id: str) -> set[str]:
    """Parties (``user_id``s) other than the unscoped fallback owning ``context_id``."""
    return _candidate_users(store, context_id) - {UNSCOPED_USER, ARTIFACT_INDEX_USER}


async def _load_any_party(
    store: BaseArtifactService,
    context_id: str,
    filename: str,
    version: int | None,
    *,
    candidates: list[str] | None = None,
) -> genai_types.Part | None:
    """Load the artifact across the resolved owner or fallback candidate parties."""
    for user_id in (
        candidates if candidates is not None else _candidate_users(store, context_id)
    ):
        part = await store.load_artifact(
            app_name=APP_NAME,
            user_id=user_id,
            session_id=context_id,
            filename=filename,
            version=version,
        )
        if part is not None:
            return part
    return None


async def _resolve_owner(
    store: BaseArtifactService,
    context_id: str,
    filename: str,
    version: int | None,
    *,
    candidates: list[str] | None = None,
) -> tuple[str, int] | None:
    """Resolve ``(user_id, concrete_version)`` for an artifact, or ``None`` if absent."""
    for user_id in (
        candidates if candidates is not None else _candidate_users(store, context_id)
    ):
        part = await store.load_artifact(
            app_name=APP_NAME,
            user_id=user_id,
            session_id=context_id,
            filename=filename,
            version=version,
        )
        if part is None:
            continue
        resolved = version
        if resolved is None:
            versions = await store.list_versions(
                app_name=APP_NAME,
                user_id=user_id,
                session_id=context_id,
                filename=filename,
            )
            resolved = versions[-1] if versions else 0
        return user_id, resolved
    return None
