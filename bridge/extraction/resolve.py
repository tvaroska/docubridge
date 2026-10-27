"""Artifact resolver — turn an inbound document *reference* into something readable.

ART-2 says documents cross the wire **by reference** (``FileWithUri``, never inline
bytes), so what actually arrives on the extraction port is whatever URI the
counterparty put on the part: their signed URL, a ``gs://`` object, our own scoped
fetch endpoint, or — for the eval corpus — a bare corpus-relative name. This module
is the one place that turns any of those into a :class:`ResolvedDocument` the engine
can hand the model, and the one place that decides which of them the Bridge is
willing to touch at all.

**Four inbound shapes** (dispatch is scheme-first, corpus-relative last):

=========================  ====================================================
``gs://bucket/obj.pdf``    **By reference.** No download and no GCS client — the
                           resolved document carries the ``uri`` and Vertex reads
                           the object natively (``Part.from_uri``).
``https://…`` / ``http://…``  **Leg-scoped fetch**: default-deny egress guard on
                           every redirect hop, bounded timeout, bounded body,
                           mime from ``Content-Type`` (else the URL suffix).
``file:///abs/path``       **Refused by default**; opt-in with
                           ``BRIDGE_ALLOW_FILE_URI=1`` (a local-dev affordance).
``images/gov-id.svg``      Corpus-relative read, contained to the corpus root —
                           exactly the pre-existing behaviour, unchanged.
anything else              ``ValueError`` — refuse loud.
=========================  ====================================================

**Leg-scoping, concretely — a reference is not a capability**
(``design/artifacts.md``). Two halves:

1. *Outbound (already enforced).* Our own ``/artifacts/{context_id}/{filename}``
   endpoint 404s a foreign ``(context_id, filename)`` **before** minting a signed
   URL, so a resolver pointed at a leaked or guessed URI of our own endpoint gets a
   404 and the resolve fails with ``HTTP 404``.
2. *Inbound (here).* The Bridge never becomes an open fetch proxy. An ``http(s)``
   target whose host resolves to a non-global IP is refused **with no request
   issued** unless its host / ``host:port`` is in ``BRIDGE_FETCH_ALLOWLIST``.
   Redirects are followed manually — at most :data:`MAX_REDIRECTS` hops, each hop
   re-validated — because our own fetch endpoint 307s to a GCS signed URL on
   deploy, and because httpx's own redirect following would skip the per-hop guard
   (a 307 to ``169.254.169.254`` must die).

Module posture: this module is imported by ``bridge/extraction/gemini.py`` and by
``bridge/edge/artifacts.py``, so it must import **no** ``bridge.edge``,
``bridge.ports`` or ``google.*`` at module level (import cycle +
``tests/test_gemini_extraction.py``'s AST guard). ``httpx`` is imported lazily
inside the fetch, the repo's idiom for an already-present transitive dependency.
"""

from __future__ import annotations

import inspect
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

from bridge import netguard

# Suffix -> mime. The SINGLE source of truth for both the extraction engine and the
# edge's artifact persistence (both used to carry their own copy of this table).
SUFFIX_MIME = {
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".pdf": "application/pdf",
}

# Corpus-relative byte source anchor: parents[0]=extraction, [1]=bridge, [2]=repo
# root (mirrors ``bridge/extraction/fixture.py`` + ``bridge/edge/artifacts.py``).
DEFAULT_CORPUS_ROOT = Path(__file__).resolve().parents[2] / "wiki/evals/address"

# Comma-separated hosts / host:port the fetch guard trusts even when they resolve to
# a non-global IP (an internal receiver, or a loopback test server). Read at CALL
# time, never at import time.
FETCH_ALLOWLIST_ENV = "BRIDGE_FETCH_ALLOWLIST"

# ``file://`` is off unless this is exactly "1". An external consumer submitting
# ``file:///etc/passwd`` would otherwise get host file contents read by the model and
# laundered back through the extracted ``key_fields`` onto the ledger.
ALLOW_FILE_URI_ENV = "BRIDGE_ALLOW_FILE_URI"

FETCH_TIMEOUT_S = 20.0
MAX_FETCH_BYTES = 32 * 1024 * 1024
MAX_REDIRECTS = 3


@dataclass(frozen=True)
class ResolvedDocument:
    """A resolved artifact: inline ``data`` **or** a by-reference ``uri``, never both.

    ``uri`` is the ``gs://`` case — the model fetches the object itself, so the
    Bridge downloads nothing and needs no storage client on the extraction path.
    """

    mime_type: str
    data: bytes | None = None
    uri: str | None = None

    def __post_init__(self) -> None:
        if (self.data is None) == (self.uri is None):
            raise ValueError(
                "ResolvedDocument needs exactly one of data= (inline bytes) or "
                "uri= (by reference)"
            )

    @property
    def by_reference(self) -> bool:
        """True when the model reads the object itself (no bytes were fetched)."""
        return self.uri is not None


# A fetcher maps an http(s) URL to a ResolvedDocument. Injectable so a test can bind
# the fetch to an ASGI app (or assert no fetch happens at all).
Fetcher = Callable[..., "ResolvedDocument | Awaitable[ResolvedDocument]"]


def mime_for(name: str) -> str:
    """Best-effort content type from a filename/URL-path suffix."""
    return SUFFIX_MIME.get(Path(name).suffix.lower(), "application/octet-stream")


async def maybe_await(value: Any) -> Any:
    """Await ``value`` when the resolver is async; pass a sync result straight through.

    The resolver became a coroutine when it grew a network leg, but every injected
    resolver in the suite (and the corpus-only legacy helper) is an ordinary
    function. Both call sites — ``GeminiExtractor.extract`` and
    ``persist_submission`` — go through here so a sync resolver keeps working.
    """
    return await value if inspect.isawaitable(value) else value


async def resolve_document(
    uri: str,
    *,
    corpus_root: Path | str = DEFAULT_CORPUS_ROOT,
    allowlist: frozenset[str] | None = None,
    fetch: Fetcher | None = None,
) -> ResolvedDocument:
    """Resolve an inbound artifact reference to bytes, or to a by-reference pointer.

    Raises ``ValueError`` for an empty URI, an unsupported scheme, a refused or
    failing fetch (**including a transport failure** — :func:`_fetch_http` normalizes
    httpx's error tree so no transport exception escapes this function), and for a
    path escaping ``corpus_root``. The two local-disk legs (``file://`` and the
    corpus read) raise ``OSError`` for a missing/unreadable path, as an ordinary
    read does. Refusing loud is the point: the extraction path must never substitute
    a fabricated or fallback document for one it could not read (the S0-bug-2 rule).
    The edge's persistence path wants the opposite (skip, don't raise) and wraps this
    — see ``bridge/edge/artifacts.py::resolve_bytes``.

    ``allowlist`` defaults to ``BRIDGE_FETCH_ALLOWLIST`` read **now**, not at import.
    """
    name = (uri or "").strip()
    if not name:
        raise ValueError("empty artifact uri")

    scheme = urlsplit(name).scheme.lower()

    if scheme == "gs":
        # By reference: Vertex reads the object natively. No network here, no GCS
        # client, no credentials. An unclassifiable suffix is refused rather than
        # sent as application/octet-stream (the backend would reject the part).
        mime = mime_for(urlsplit(name).path)
        if mime == "application/octet-stream":
            raise ValueError(
                f"cannot infer a mime type for by-reference artifact {name!r} "
                f"(known suffixes: {', '.join(sorted(SUFFIX_MIME))})"
            )
        return ResolvedDocument(mime_type=mime, uri=name)

    if scheme in ("http", "https"):
        if allowlist is None:
            allowlist = netguard.allowlist_from_env(FETCH_ALLOWLIST_ENV)
        fetcher = fetch or _fetch_http
        return await maybe_await(fetcher(name, allowlist=allowlist))

    if scheme == "file":
        if os.environ.get(ALLOW_FILE_URI_ENV) != "1":
            raise ValueError(
                f"artifact fetch refused: file:// URIs are disabled ({name}); set "
                f"{ALLOW_FILE_URI_ENV}=1 to allow local-disk reads"
            )
        path = Path(urlsplit(name).path)
        return ResolvedDocument(data=path.read_bytes(), mime_type=mime_for(path.name))

    if scheme:
        raise ValueError(f"unsupported artifact uri scheme {scheme!r} ({name})")

    # No scheme: the eval corpus's bare relative name. Unchanged behaviour, including
    # the traversal containment (resolve + relative_to the resolved root).
    root = Path(corpus_root).resolve()
    path = (root / name).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"artifact {name!r} escapes the corpus root") from exc
    return ResolvedDocument(data=path.read_bytes(), mime_type=mime_for(name))


async def _fetch_http(
    url: str,
    *,
    allowlist: frozenset[str] = frozenset(),
    timeout: float = FETCH_TIMEOUT_S,
    max_bytes: int = MAX_FETCH_BYTES,
    client: Any = None,
) -> ResolvedDocument:
    """GET an http(s) artifact under the egress guard; return its bytes + mime.

    **Guard first, request second** — a refused URL never has a request issued
    against it, which is what makes ``http://169.254.169.254/…`` safe rather than
    merely unreturned. Redirects are followed manually (``follow_redirects=False``
    is load-bearing) so every hop is re-validated: our own fetch endpoint 307s to a
    GCS signed URL on deploy, and an attacker-controlled 307 to a metadata IP must
    still die. A non-2xx terminal response raises — in particular the ``HTTP 404``
    our own endpoint returns for a foreign ``(context_id, filename)``, which is the
    outbound half of leg-scoping refusing a reference that is not a capability.

    **Every failure leaves here as a ``ValueError``.** A dead host, a DNS failure or a
    read timeout is a *failing fetch*, exactly like a 404, and both callers are written
    against that: the extraction path refuses loud on ``ValueError``, and
    ``bridge/edge/artifacts.py::resolve_bytes`` owes its never-raise contract
    (Decision 7) to it — a party's artifact host being briefly unreachable must skip
    persistence, never fail the disposition turn. The normalization catches the base
    ``Exception`` rather than an httpx tuple on purpose: httpx's error tree is not
    single-rooted (``InvalidURL`` and ``StreamError`` sit outside ``HTTPError``), and
    the contract is "a failing fetch is a ValueError", not "these five classes are".

    ``client`` is injectable (a test binds an ``httpx.ASGITransport`` to the real
    fetch app); when this function builds the client, this function closes it.
    """
    import httpx

    owned = client is None
    if owned:
        client = httpx.AsyncClient(timeout=timeout, follow_redirects=False)
    try:
        return await _fetch_hops(client, url, allowlist=allowlist, max_bytes=max_bytes)
    except ValueError:
        raise  # already our own refusal/failure vocabulary
    except Exception as exc:
        raise ValueError(
            f"artifact fetch failed: {type(exc).__name__}: {exc} ({url})"
        ) from exc
    finally:
        if owned:
            await client.aclose()


async def _fetch_hops(
    client: Any,
    url: str,
    *,
    allowlist: frozenset[str],
    max_bytes: int,
) -> ResolvedDocument:
    """The guarded, redirect-following GET itself (see :func:`_fetch_http`).

    Split out so its caller owns exactly one extra thing: turning whatever the client
    layer throws into the ``ValueError`` both call sites are written against.
    """
    current = url
    for _hop in range(MAX_REDIRECTS + 1):
        allowed, reason = netguard.url_allowed(current, allowlist=allowlist)
        if not allowed:
            raise ValueError(f"artifact fetch refused: {reason} ({current})")
        async with client.stream("GET", current) as response:
            location = response.headers.get("location")
            if response.is_redirect and location:
                current = urljoin(current, location)
                continue
            if not response.is_success:
                raise ValueError(
                    f"artifact fetch failed: HTTP {response.status_code} ({current})"
                )
            declared = response.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > max_bytes:
                raise ValueError(
                    f"artifact too large: {declared} bytes exceeds the "
                    f"{max_bytes}-byte cap ({current})"
                )
            chunks: list[bytes] = []
            total = 0
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > max_bytes:
                    # Stop reading: an oversized body is never fully buffered.
                    raise ValueError(
                        f"artifact too large: body exceeds the {max_bytes}-byte "
                        f"cap ({current})"
                    )
                chunks.append(chunk)
            return ResolvedDocument(
                data=b"".join(chunks),
                mime_type=_mime_from_response(response.headers, current),
            )
    raise ValueError(
        f"artifact fetch refused: more than {MAX_REDIRECTS} redirects ({url})"
    )


def _mime_from_response(headers: Any, url: str) -> str:
    """``Content-Type`` (parameters stripped), falling back to the URL suffix."""
    raw = (headers.get("content-type") or "").split(";")[0].strip().lower()
    if not raw or raw == "application/octet-stream":
        return mime_for(urlsplit(url).path)
    return raw


__all__ = [
    "ALLOW_FILE_URI_ENV",
    "DEFAULT_CORPUS_ROOT",
    "FETCH_ALLOWLIST_ENV",
    "FETCH_TIMEOUT_S",
    "MAX_FETCH_BYTES",
    "MAX_REDIRECTS",
    "SUFFIX_MIME",
    "ResolvedDocument",
    "maybe_await",
    "mime_for",
    "resolve_document",
]
