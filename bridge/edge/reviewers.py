"""Reviewer authorization for the HITL resume route (S3-edge-1 R6, D10).

A ``HumanReviewResolution`` resumes a parked ``request_human_review`` call only when
it comes from an **authorized reviewer principal**. The principal is the
authenticated A2A call-context user (``RequestContext.call_context.user``) — never
the payload: ``HumanReviewResolution.reviewer`` is an audit label and is never read
here.

**Fail closed.** The allow-list is ``BRIDGE_EDGE_REVIEWERS`` (comma/space-separated
principal names) or an explicit value passed to ``build_a2a_app(reviewers=...)``.
Unset or empty → the empty set → every resolution is refused. An unauthenticated
caller is refused whatever its name. The principal source on deploy is the server's
authentication middleware (``build_a2a_app(auth_backend=...)``); the edge ships no
header-trusting backend.
"""
from __future__ import annotations

import os
import re
from collections.abc import Iterable
from typing import Any

REVIEWERS_ENV = "BRIDGE_EDGE_REVIEWERS"

_SEPARATORS = re.compile(r"[,\s]+")


def resolve_reviewers(value: Iterable[str] | str | None = None) -> frozenset[str]:
    """The reviewer allow-list: an explicit ``value`` wins, else ``BRIDGE_EDGE_REVIEWERS``.

    A string is split on commas and whitespace; blanks are dropped. ``None`` falls
    through to the env; unset/empty → ``frozenset()`` (every resolution refused).
    """
    if value is None:
        value = os.environ.get(REVIEWERS_ENV, "")
    if isinstance(value, str):
        names: Iterable[str] = _SEPARATORS.split(value)
    else:
        names = value
    return frozenset(n.strip() for n in names if n and n.strip())


def is_authorized_reviewer(user: Any, reviewers: frozenset[str]) -> bool:
    """Whether the call-context ``user`` is an authenticated, allow-listed reviewer.

    ``user`` is an ``a2a.auth.user.User`` (``UnauthenticatedUser`` by default, a
    ``StarletteUser`` behind authentication middleware). Fails closed on a missing
    user, an unauthenticated user, an empty name and an empty allow-list.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    name = getattr(user, "user_name", "")
    return bool(name) and name in reviewers
