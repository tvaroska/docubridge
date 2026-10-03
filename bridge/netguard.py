"""Pure egress guard — default-deny URL classification for every outbound leg.

Two Bridge surfaces make requests to a URL a **counterparty** chose: the A2A push
notifier POSTs task events to a client-supplied webhook (``bridge/edge/pushguard.py``,
S0-sec-2) and the artifact resolver GETs a party's by-reference document
(``bridge/extraction/resolve.py``, S0-arch-7). Both need the same answer to the same
question — *may this process issue a request to this URL?* — so the classification
lives here, once, with **no dependencies beyond the standard library** (in particular
no ``a2a`` import, which is what lets ``bridge/extraction/`` use it without pulling
the edge's ADK/A2A stack or closing an import cycle).

The policy: http/https only, a host is required, and any host that resolves to a
non-global IP range (loopback, private, link-local — the ``169.254.169.254`` cloud
metadata endpoint — reserved, multicast) is refused. A per-surface env allowlist
(``host`` or ``host:port``) is the escape hatch a deployment uses to trust a specific
internal receiver or a loopback test server. An **unresolvable** host is allowed: it
cannot reach an internal target, so the request simply fails.

Scope: this is the proportionate pre-deploy default-deny. Full mitigation — Gateway
egress policy and closing the DNS-rebinding window (a host that resolves public at
check time and private at connect time) — is P7 (``design/zones.md``).
"""
from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urlsplit


def allowlist_from_env(var: str, env: dict[str, str] | None = None) -> frozenset[str]:
    """Parse a comma-separated hosts / ``host:port`` allowlist out of ``env[var]``.

    Read at CALL time, never at import time — a deployment (and a test) changes the
    env after this module is imported.
    """
    raw = (env or os.environ).get(var, "")
    return frozenset(h.strip().lower() for h in raw.split(",") if h.strip())


def ip_is_global(ip: str) -> bool:
    """True only for public/global IPs — rejects loopback/link-local/private/etc."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    # ``is_global`` is False for private, loopback, link-local (169.254/16 — the
    # cloud metadata range), reserved, multicast and unspecified addresses.
    return addr.is_global


def url_allowed(
    url: str, *, allowlist: frozenset[str] = frozenset()
) -> tuple[bool, str]:
    """Return ``(allowed, reason)`` for an outbound URL.

    Rejects non-http(s) schemes and any URL whose host resolves to a non-global IP
    range. An explicit ``allowlist`` match (host or ``host:port``) always wins, so a
    deployment can trust a specific internal receiver.
    """
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return False, f"unsupported scheme {parts.scheme!r} (want http/https)"
    host = parts.hostname
    if not host:
        return False, "missing host"

    host_l = host.lower()
    if host_l in allowlist or (
        parts.port is not None and f"{host_l}:{parts.port}" in allowlist
    ):
        return True, "allowlisted"

    # An IP literal is classified directly; a name is resolved and every
    # returned address must be global (so a name pointing at 10.x or the
    # metadata IP is caught).
    try:
        ipaddress.ip_address(host)
        is_literal = True
    except ValueError:
        is_literal = False

    if is_literal:
        if not ip_is_global(host):
            return False, f"host {host} is a non-global address"
        return True, "ok"

    try:
        infos = socket.getaddrinfo(host, parts.port or None, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        # Unresolvable: cannot reach an internal target — allow (the request fails).
        return True, "unresolvable host (no SSRF reach)"

    resolved = {info[4][0] for info in infos}
    bad = [ip for ip in resolved if not ip_is_global(ip)]
    if bad:
        return False, f"host {host} resolves to non-global address(es): {sorted(bad)}"
    return True, "ok"


__all__ = ["allowlist_from_env", "ip_is_global", "url_allowed"]
