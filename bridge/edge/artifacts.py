"""Scoped artifact persistence + fetch endpoint for the Bridge edge (ART-2).

The whole artifact concern in one edge module: on inbound the edge persists the
referenced document bytes into an ADK ``ArtifactService`` (versioned per
resubmission = ART-1 history); on outbound it rewrites each ledger entry's
``ArtifactRef.uri`` to a **scoped fetch endpoint** hosted on the same ASGI app. A
consumer reads the ``ExchangeTurn`` and GETs the bytes on demand from that
endpoint. Every fetch is **leg-scoped**: a foreign ``context_id``/filename → 404
(a reference is not a capability — ``design/artifacts.md``).

**Not a seam (Decision 1).** The artifact store is NOT one of the managed
``Seam``s. ``design/artifacts.md`` is explicit that documents use the
platform's native ``ArtifactService`` "as-is rather than inventing a store":
``InMemoryArtifactService`` locally, ``GcsArtifactService`` on deploy — a native
backend swap (like Sessions' ``InMemory…↔Vertex``), not a new managed seam. No
``@pytest.mark.seam`` here; this follows the seam-in-waiting idiom
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
  IAM. See ``design/gcp-substrate.md`` §runtime.
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

import logging
import os
from collections.abc import Awaitable, Callable
from urllib.parse import quote

import google.genai.types as genai_types
from fastapi import FastAPI, Response
from fastapi.responses import RedirectResponse
from google.adk.artifacts import BaseArtifactService

from bridge.collect import DocumentSubmission
from bridge.extraction.resolve import maybe_await, mime_for, resolve_document

_LOG = logging.getLogger("bridge.edge.artifacts")

# ADK artifact key components (Decision 5). ``app_name`` reuses the name from
# ``bridge/agent.py::build_app``; ``user_id`` falls back to a constant when the
# request carries no party (the one-turn path).
APP_NAME = "document_bridge"
UNSCOPED_USER = "_unscoped"
ARTIFACTS_PREFIX = "/artifacts"

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
    except Exception as exc:
        _LOG.warning("artifact %s not persisted: %s: %s", uri, type(exc).__name__, exc)
        return None
    if resolved.by_reference:
        return None
    return resolved.data, resolved.mime_type


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


async def persist_submission(
    store: BaseArtifactService,
    *,
    party: str,
    context_id: str,
    submission: DocumentSubmission,
    resolver: Resolver = resolve_bytes,
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
    resolved = await maybe_await(resolver(ref.uri))
    if resolved is None:
        return None
    data, mime_type = resolved
    part = genai_types.Part.from_bytes(data=data, mime_type=mime_type)
    return await store.save_artifact(
        app_name=APP_NAME,
        user_id=party,
        session_id=context_id,
        filename=filename_for(submission),
        artifact=part,
    )


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


def build_artifact_store() -> BaseArtifactService:
    """Build the artifact store from env: GCS on deploy, in-memory locally (P7-3).

    Returns ``GcsArtifactService(bucket_name=$BRIDGE_ARTIFACT_BUCKET)`` when the env
    is set (lazy import — importing this module pulls no ``google.cloud`` client),
    else ``InMemoryArtifactService``. This is the native backend swap point (not a
    seam, Decision 1). Callers keep the injectable ``param or build_artifact_store()``
    idiom so an explicit store still wins (the P7-8 wiring-guard contract).
    """
    bucket = os.environ.get(ARTIFACT_BUCKET_ENV, "").strip()
    if bucket:
        from google.adk.artifacts import GcsArtifactService

        return GcsArtifactService(bucket_name=bucket)
    from google.adk.artifacts import InMemoryArtifactService

    return InMemoryArtifactService()


def _adk_gcs_blob_path(
    user_id: str, context_id: str, filename: str, version: int
) -> str:
    """The ADK ``GcsArtifactService`` object path for a session-scoped artifact.

    Mirrors ADK's ``_get_blob_name`` for a non-``user:``-namespaced filename:
    ``{app}/{user_id}/{session_id}/{filename}/{version}`` (session_id == the
    exchange ``context_id``). Kept in lockstep with the store so the signed URL
    points at the exact object ``save_artifact`` wrote.
    """
    return f"{APP_NAME}/{user_id}/{context_id}/{filename}/{version}"


def gcs_signer(bucket_name: str) -> Signer:
    """Return a :data:`Signer` that mints a V4 GCS signed URL for one blob (P7-3).

    On Cloud Run the signing uses IAM SignBlob via the ``bridge-runtime`` SA's
    existing self-scoped ``serviceAccountTokenCreator`` grant (P7-1) — no private
    key, no new IAM. The client is imported lazily (module import stays dep-free).
    """
    from datetime import timedelta

    def _sign(context_id: str, filename: str, user_id: str, version: int | None) -> str:
        from google.cloud.storage import Client

        blob = (
            Client()
            .bucket(bucket_name)
            .blob(_adk_gcs_blob_path(user_id, context_id, filename, version or 0))
        )
        return blob.generate_signed_url(
            expiration=timedelta(minutes=SIGNED_URL_TTL_MIN), version="v4"
        )

    return _sign


def build_fetch_signer() -> Signer | None:
    """Return a GCS signer when ``BRIDGE_ARTIFACT_BUCKET`` is set, else ``None``.

    ``None`` selects the local direct-stream fetch path (unchanged); a signer
    selects the deploy redirect-to-signed-URL path (bytes never on the wire).
    """
    bucket = os.environ.get(ARTIFACT_BUCKET_ENV, "").strip()
    return gcs_signer(bucket) if bucket else None


def build_fetch_app(
    store: BaseArtifactService,
    *,
    resolver: Resolver | None = None,
    signer: Signer | None = None,
) -> FastAPI:
    """Build the scoped artifact-fetch FastAPI sub-app (Decision 2, 9).

    One GET route ``/{context_id}/{filename}`` (mounted at ``/artifacts``) with an
    optional ``?v={version}`` (default = latest). The leg boundary is
    ``(context_id, filename)``: a foreign ``context_id`` / unknown filename owns
    nothing → ``404``. That 404 IS the leg-scoping enforcement on BOTH paths (a
    reference is not a capability, design/artifacts.md).

    - ``signer is None`` (local): streams the stored bytes with the stored content
      type — unchanged from P2-4.
    - ``signer`` provided (deploy, P7-3): **authorizes first** — resolves the
      owning ``user_id`` + concrete version and 404s if the blob is absent —
      **before** minting anything, then returns a 307 redirect to a time-limited
      signed URL. Bytes never ride the response; the client fetches GCS directly.
      A signed URL for a guessed path is never mintable (the 404 gates it).

    ``resolver`` is accepted for symmetry with the executor wiring but is unused on
    the read path (bytes already live in the store).
    """
    app = FastAPI()

    @app.get("/{context_id}/{filename}")
    async def fetch_artifact(context_id: str, filename: str, v: int | None = None):
        # Real per-turn Gateway re-auth / bearer scoping is DUR-7 / P7-5; the leg
        # boundary here is structural (the store key = context_id + filename),
        # not authenticated. The URI carries no party, so probe known parties.
        if signer is not None:
            owner = await _resolve_owner(store, context_id, filename, v)
            if owner is None:
                return Response(status_code=404)  # authorize before you sign
            user_id, version = owner
            return RedirectResponse(
                signer(context_id, filename, user_id, version), status_code=307
            )
        part = await _load_any_party(store, context_id, filename, v)
        if part is None or part.inline_data is None:
            return Response(status_code=404)
        blob = part.inline_data
        return Response(
            content=blob.data,
            media_type=blob.mime_type or "application/octet-stream",
        )

    return app


def _candidate_users(store: BaseArtifactService, context_id: str) -> set[str]:
    """Candidate ``user_id`` owners of a ``context_id`` (the fetch URI omits party).

    Always includes the unscoped fallback; for ``InMemoryArtifactService`` (which
    keeps a composite-key ``artifacts`` dict) also enumerates the tracked users so
    a party-scoped artifact is found. A store without that dict (the deployed
    ``GcsArtifactService``) probes the unscoped user; per-party discovery on the
    deployed path is a P7-9 concern.
    """
    users: set[str] = {UNSCOPED_USER}
    artifacts = getattr(store, "artifacts", None)
    if isinstance(artifacts, dict):
        for key in artifacts:
            # Key format: "app_name/user_id/session_id/filename" for session-scoped.
            parts = key.split("/")
            if len(parts) >= 3 and parts[0] == APP_NAME and parts[2] == context_id:
                users.add(parts[1])
    return users


def owning_parties(store: BaseArtifactService, context_id: str) -> set[str]:
    """Parties (``user_id``s) other than the unscoped fallback owning ``context_id``.

    The S3-edge-1 D9 party resolution for the LLM-leg session key (session key ==
    artifact key): the same enumeration the fetch endpoint uses
    (:func:`_candidate_users`), minus :data:`UNSCOPED_USER`. Same limits — an
    ``InMemoryArtifactService`` is enumerated; a store without that dict (the
    deployed ``GcsArtifactService``) yields an empty set (per-party discovery on
    the deployed path is a P7-9 concern).
    """
    return _candidate_users(store, context_id) - {UNSCOPED_USER}


async def _load_any_party(
    store: BaseArtifactService,
    context_id: str,
    filename: str,
    version: int | None,
) -> genai_types.Part | None:
    """Load the artifact regardless of which ``user_id`` (party) owns it.

    The leg boundary is ``(context_id, filename)``; the fetch URI does not carry
    the party, so probe the unscoped fallback and the store's tracked users.
    """
    for user_id in _candidate_users(store, context_id):
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
) -> tuple[str, int] | None:
    """Resolve ``(user_id, concrete_version)`` for an artifact, or ``None`` if absent.

    The signer needs both the owning party and a concrete version to build the exact
    blob path. Probes each candidate user; on a hit, resolves ``version=None``
    (latest) to the highest stored version via ``list_versions``. Returns ``None``
    when no candidate owns ``(context_id, filename)`` — the fetch route's 404 (the
    leg-scoping gate that must fire before any URL is minted).
    """
    for user_id in _candidate_users(store, context_id):
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
