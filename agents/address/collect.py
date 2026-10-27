"""The collect node — a ``RemoteA2aAgent`` pointed at the Bridge card (AGENT-1/2).

The collect node is a plain ADK ``RemoteA2aAgent`` (a ``BaseAgent``, hence a
``BaseNode``) that reaches the Bridge over the A2A wire by *card URL only*. The
mock→real swap is only the URL — this module never imports ``bridge/`` (the
isolation invariant, enforced from the reverse in convention here).

``@a2a_experimental`` surface discipline (TEST-4): ``RemoteA2aAgent`` is a
registered experimental surface pinned to ``use_legacy=True`` in
``bridge/adk/experimental.py``; keep that pin — do not flip it.

**Scope.** The outbound message content is whatever the session's latest user
content is (``RemoteA2aAgent._construct_message_parts_from_session`` forwards
session content). P3-1 seeds that with the free-text ``scenario:<name>`` marker
the mock understands; the structured ``CollectRequest`` data-part encoding is P3-3.

The send-path ``RequestInterceptor`` (resume re-detection / ``task_id`` +
``context_id`` threading — AGENT-6/5) **is wired here** (P3-2): a
``resume_stamping_interceptor`` installed via ``A2aRemoteAgentConfig`` re-stamps a
parked task the native detection misses under the ``Workflow`` event ordering, so
the peer resumes the SAME task under the SAME exchange context. ``use_legacy=True``
is preserved (TEST-4 pin); adding ``config=`` is independent of the legacy flag.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from google.adk.a2a.agent.config import A2aRemoteAgentConfig
from google.adk.agents.remote_a2a_agent import RemoteA2aAgent

from agents.address.interceptor import resume_stamping_interceptor

if TYPE_CHECKING:  # pragma: no cover - typing only
    import httpx


def build_bridge_remote_agent(
    card_url: str,
    *,
    name: str = "collect",
    rerun_on_resume: bool = True,
    httpx_client: httpx.AsyncClient | None = None,
    timeout: float = 600.0,
) -> RemoteA2aAgent:
    """Build the collect node as a ``RemoteA2aAgent`` resolving ``card_url``.

    Args:
        card_url: The Bridge Agent Card URL (or ``/.well-known`` base). Resolved
            by the ``RemoteA2aAgent`` against the (optionally injected) httpx
            client.
        name: Node name in the workflow graph (default ``"collect"``).
        rerun_on_resume: Design requirement — a parked collect leg resumes
            mid-flight rather than being fast-forwarded (architecture atom,
            "collect node"). Defaults ``True``.
        httpx_client: A test port so an in-process ``ASGITransport`` client can
            point the agent at the in-process mock Bridge. ``None`` in prod
            (``RemoteA2aAgent`` makes its own client).
        timeout: A2A request timeout in seconds.

    Returns:
        A ``RemoteA2aAgent`` node ready to drop into the ``Workflow`` graph.
    """
    return RemoteA2aAgent(
        name=name,
        agent_card=card_url,
        httpx_client=httpx_client,
        rerun_on_resume=rerun_on_resume,
        timeout=timeout,
        use_legacy=True,  # TEST-4 pin — do not flip (bridge/adk/experimental.py).
        config=A2aRemoteAgentConfig(
            # AGENT-6/5: re-stamp a parked task the native resume detection misses
            # under the Workflow event ordering (see interceptor.py).
            request_interceptors=[resume_stamping_interceptor(name)],
        ),
    )
