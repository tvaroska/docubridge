"""SSRF guard for A2A push-notification webhooks (S0-sec-2).

The A2A push surface POSTs task events to a **client-supplied** webhook URL. With
no guard a client can register ``http://169.254.169.254/…`` (cloud metadata) or an
internal host and turn the Bridge into an SSRF pivot. This module wraps any
``PushNotificationConfigStore`` and rejects webhook URLs that resolve to
non-global IP ranges **at registration time** (``set_info``), with an env
allowlist escape hatch (``BRIDGE_PUSH_ALLOWLIST``).

The URL classification itself is NOT here: it is ``bridge/netguard.py``, shared
with the artifact resolver's inbound fetch (S0-arch-7) so both egress legs answer
"may this process request that URL?" the same way. ``netguard`` is stdlib-only —
this module is the A2A-flavoured half (the store wrapper, the env var name, the
``InvalidParamsError``), and ``webhook_url_allowed`` stays the name/signature this
surface's callers and tests use.

Scope: this is the proportionate default-deny egress guard for the pre-deploy
path. Full mitigation — per-party auth, Gateway egress policy, and a
check-at-send re-validation to close the DNS-rebinding window (a host that
resolves public at ``set_info`` time and private at send time) — is P7
(``design/zones.md``). Unresolvable hosts are allowed here: they cannot reach an
internal target, so the POST simply fails; only names/literals that resolve to a
private range are the SSRF risk this guard blocks.
"""

from __future__ import annotations

from a2a.server.tasks import PushNotificationConfigStore
from a2a.utils.errors import A2AError, InvalidParamsError

from bridge import netguard
from bridge.netguard import url_allowed as webhook_url_allowed

_ALLOWLIST_ENV = "BRIDGE_PUSH_ALLOWLIST"


def allowlist_from_env(env: dict[str, str] | None = None) -> frozenset[str]:
    """Parse ``BRIDGE_PUSH_ALLOWLIST`` (comma-separated hosts / host:port)."""
    return netguard.allowlist_from_env(_ALLOWLIST_ENV, env)


class SsrfGuardPushConfigStore(PushNotificationConfigStore):
    """Wrap a ``PushNotificationConfigStore``, rejecting SSRF-prone webhook URLs.

    Delegates every operation to ``inner`` and enforces :func:`webhook_url_allowed`
    on registration. ``get_info_for_dispatch`` is delegated too, preserving the
    inner store's multi-owner dispatch override.
    """

    def __init__(
        self,
        inner: PushNotificationConfigStore,
        *,
        allowlist: frozenset[str] | None = None,
    ) -> None:
        self._inner = inner
        self._allowlist = allowlist if allowlist is not None else allowlist_from_env()

    async def set_info(self, task_id, notification_config, context):
        """Validate the webhook destination against SSRF rules and store the push notification config."""
        url = getattr(notification_config, "url", "") or ""
        allowed, reason = webhook_url_allowed(url, allowlist=self._allowlist)
        if not allowed:
            raise InvalidParamsError(
                message=f"push notification webhook rejected: {reason}"
            )
        return await self._inner.set_info(task_id, notification_config, context)

    async def get_info(self, task_id, context):
        """Retrieve the stored push notification configuration for a task ID."""
        return await self._inner.get_info(task_id, context)

    async def delete_info(self, task_id, context, config_id=None):
        """Delete the stored push notification configuration for a task ID."""
        return await self._inner.delete_info(task_id, context, config_id)

    async def get_info_for_dispatch(self, task_id):
        """Re-validate the target webhook URL immediately before outbound push dispatch."""
        return await self._inner.get_info_for_dispatch(task_id)


def guarded_push_config_store(
    inner: PushNotificationConfigStore,
    *,
    allowlist: frozenset[str] | None = None,
) -> PushNotificationConfigStore:
    """Wrap ``inner`` in the SSRF guard (idempotent — won't double-wrap)."""
    if isinstance(inner, SsrfGuardPushConfigStore):
        return inner
    return SsrfGuardPushConfigStore(inner, allowlist=allowlist)


__all__ = [
    "A2AError",
    "SsrfGuardPushConfigStore",
    "allowlist_from_env",
    "guarded_push_config_store",
    "webhook_url_allowed",
]
