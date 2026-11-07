"""S0-sec-2 / S0-sec-3 — push webhook SSRF guard + loopback serve defaults.

S0-sec-2: ``webhook_url_allowed`` and ``SsrfGuardPushConfigStore`` reject webhook
URLs that resolve to non-global IPs (cloud metadata / internal hosts) at
registration, with an env allowlist escape hatch.
S0-sec-3: the manual-run ``serve`` helpers default to ``127.0.0.1``, not
``0.0.0.0``.
"""

from __future__ import annotations

import inspect

import pytest
from a2a.utils.errors import InvalidParamsError

from bridge.edge.pushguard import (
    SsrfGuardPushConfigStore,
    allowlist_from_env,
    guarded_push_config_store,
    webhook_url_allowed,
)


# --------------------------------------------------------------------------- #
# S0-sec-2 — URL classification                                               #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata (link-local)
        "http://127.0.0.1/hook",  # loopback
        "http://10.0.0.5/hook",  # private
        "http://192.168.1.10:8080/hook",  # private
        "http://[::1]/hook",  # IPv6 loopback
        "ftp://example.com/hook",  # unsupported scheme
        "http:///hook",  # missing host
    ],
)
def test_webhook_url_rejected(url):
    allowed, reason = webhook_url_allowed(url)
    assert not allowed, f"{url} should be rejected, got: {reason}"


@pytest.mark.parametrize(
    "url",
    [
        "http://8.8.8.8/hook",  # public IP literal
        "http://receiver/hook",  # unresolvable name — no SSRF reach
        "https://receiver/hook",
    ],
)
def test_webhook_url_allowed(url):
    allowed, _reason = webhook_url_allowed(url)
    assert allowed, f"{url} should be allowed"


def test_allowlist_permits_internal_host():
    url = "http://10.0.0.5:9000/hook"
    assert not webhook_url_allowed(url)[0]
    allowed, reason = webhook_url_allowed(url, allowlist=frozenset({"10.0.0.5:9000"}))
    assert allowed and reason == "allowlisted"
    # host-only allowlist entry also matches
    assert webhook_url_allowed(url, allowlist=frozenset({"10.0.0.5"}))[0]


def test_allowlist_from_env(monkeypatch):
    monkeypatch.setenv("BRIDGE_PUSH_ALLOWLIST", "host-a, HOST-B:8080 ,")
    assert allowlist_from_env() == frozenset({"host-a", "host-b:8080"})
    monkeypatch.delenv("BRIDGE_PUSH_ALLOWLIST", raising=False)
    assert allowlist_from_env() == frozenset()


# --------------------------------------------------------------------------- #
# S0-sec-2 — the wrapping config store                                        #
# --------------------------------------------------------------------------- #
class _FakeInnerStore:
    """Minimal PushNotificationConfigStore double recording delegated calls."""

    def __init__(self):
        self.set_calls = []
        self.dispatch_calls = 0

    async def set_info(self, task_id, notification_config, context):
        self.set_calls.append((task_id, notification_config.url))

    async def get_info(self, task_id, context):
        return []

    async def delete_info(self, task_id, context, config_id=None):
        return None

    async def get_info_for_dispatch(self, task_id):
        self.dispatch_calls += 1
        return []


class _Cfg:
    def __init__(self, url):
        self.url = url


async def test_guard_store_rejects_metadata_url():
    inner = _FakeInnerStore()
    store = SsrfGuardPushConfigStore(inner)
    with pytest.raises(InvalidParamsError):
        await store.set_info("t1", _Cfg("http://169.254.169.254/x"), None)
    assert inner.set_calls == []  # never reached the inner store


async def test_guard_store_delegates_allowed_url_and_dispatch():
    inner = _FakeInnerStore()
    store = SsrfGuardPushConfigStore(inner)
    await store.set_info("t1", _Cfg("http://receiver/hook"), None)
    assert inner.set_calls == [("t1", "http://receiver/hook")]
    # get_info_for_dispatch is delegated (preserves inner multi-owner override)
    await store.get_info_for_dispatch("t1")
    assert inner.dispatch_calls == 1


def test_guarded_push_config_store_is_idempotent():
    inner = _FakeInnerStore()
    once = guarded_push_config_store(inner)
    twice = guarded_push_config_store(once)
    assert once is twice


# --------------------------------------------------------------------------- #
# S0-sec-3 — serve() helpers bind to loopback by default                      #
# --------------------------------------------------------------------------- #
def test_serve_helpers_default_to_loopback():
    from bridge.dashboard.server import serve as dashboard_serve
    from bridge.edge.mock import serve_mock
    from bridge.edge.server import serve as edge_serve
    from bridge.portal.server import serve as portal_serve
    from bridge.timewarp.server import serve as timewarp_serve

    for fn in (
        edge_serve,
        serve_mock,
        portal_serve,
        dashboard_serve,
        timewarp_serve,
    ):
        default = inspect.signature(fn).parameters["host"].default
        assert default == "127.0.0.1", f"{fn.__module__}.{fn.__name__} host={default!r}"
