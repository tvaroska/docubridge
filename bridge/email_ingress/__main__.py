"""Operator CLI for the email intake (S0-arch-13).

* ``land <file.eml>`` — land one raw message on the ``email_ingress`` port (the local
  analogue of the Graph webhook). Prints ``landed <key>`` or ``duplicate <key>``.
  Locally set ``BRIDGE_EMAIL_DIR``; ``BRIDGE_PORT_EMAIL_INGRESS=gcp`` +
  ``BRIDGE_EMAIL_BUCKET`` lands in the deployed bucket.
* ``subscribe --notification-url URL`` — create the Graph change-notification
  subscription for the intake mailbox's Inbox (needs ``BRIDGE_EMAIL_GRAPH_*``,
  ``BRIDGE_EMAIL_MAILBOX`` and ``BRIDGE_EMAIL_CLIENT_STATE``).
* ``renew <subscription-id>`` — push the subscription's expiry out again.
* ``drain [--url URL] [--timeout SECONDS]`` — trigger the drain on the SERVED host that
  owns the RFP board (``POST <url>/email/drain``; ``--url`` defaults to
  ``BRIDGE_EMAIL_DRAIN_URL``, e.g. ``http://127.0.0.1:8010/ops`` for the local RFP demo
  host). Prints ``filed <key> <rfp_id> <party> filed=<n> unreadable=<m>`` or
  ``quarantined <key> <reason>`` per outcome, then ``drained <count>``; an HTTP or
  connection failure goes to stderr with exit 1.

  **Why it calls the host instead of draining in-process (S0-bug-3).** The RFP board
  lives in the serving host's process. A processor built here would correlate against
  an EMPTY board: every landed mail would match no open leg and be marked processed
  ``no_exchange_token`` — permanently, since a processed key never drains again. So the
  CLI only ever asks the board-owning host to drain.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from bridge.email_ingress.graph import (
    DEFAULT_SUBSCRIPTION_MINUTES,
    GraphMailClient,
    client_state_from_env,
)
from bridge.email_ingress.mime import landing_key

if TYPE_CHECKING:
    import httpx

DRAIN_URL_ENV = "BRIDGE_EMAIL_DRAIN_URL"
DEFAULT_DRAIN_TIMEOUT = 120.0


def _land(path: str) -> int:
    from bridge.ports import Port, get_adapter, resolve_mode

    raw = Path(path).read_bytes()
    key = landing_key(raw)
    port = get_adapter(Port.email_ingress, resolve_mode(Port.email_ingress))
    print(f"{'landed' if port.land(key, raw) else 'duplicate'} {key}")
    return 0


def _subscribe(notification_url: str, minutes: int) -> int:
    sub = GraphMailClient.from_env().create_subscription(
        notification_url, client_state_from_env(), minutes=minutes
    )
    print(f"subscription {sub.get('id')} expires {sub.get('expirationDateTime')}")
    return 0


def _renew(subscription_id: str, minutes: int) -> int:
    sub = GraphMailClient.from_env().renew_subscription(
        subscription_id, minutes=minutes
    )
    print(f"subscription {sub.get('id')} expires {sub.get('expirationDateTime')}")
    return 0


def _outcome_line(outcome: dict) -> str:
    key = outcome.get("key")
    if outcome.get("status") == "filed":
        return (
            f"filed {key} {outcome.get('rfp_id')} {outcome.get('party')} "
            f"filed={len(outcome.get('filed') or [])} "
            f"unreadable={len(outcome.get('unreadable') or [])}"
        )
    return f"{outcome.get('status')} {key} {outcome.get('reason')}"


def _drain(url: str, timeout: float, *, client: httpx.Client | None = None) -> int:
    """POST the served host's ``/email/drain`` and print each outcome (never a traceback)."""
    import httpx  # lazy: the repo idiom for an already-present dependency

    target = f"{url.rstrip('/')}/email/drain"
    try:
        if client is not None:  # an injected client carries its own timeout
            resp = client.post(target)
        else:
            with httpx.Client(timeout=timeout) as own:
                resp = own.post(target)
    except httpx.HTTPError as exc:
        print(f"drain failed: {type(exc).__name__}: {exc} ({target})", file=sys.stderr)
        return 1
    if not resp.is_success:
        print(
            f"drain failed: HTTP {resp.status_code} {resp.text[:200]}", file=sys.stderr
        )
        return 1
    try:
        outcomes = resp.json()
        if not isinstance(outcomes, list):
            raise TypeError
        lines = [_outcome_line(o) for o in outcomes]
    except (ValueError, TypeError, AttributeError):
        print(f"drain failed: unexpected response {resp.text[:200]}", file=sys.stderr)
        return 1
    for line in lines:
        print(line)
    print(f"drained {len(lines)}")
    return 0


def main(argv: list[str] | None = None, *, client: httpx.Client | None = None) -> int:
    """Run the standalone email ingress polling worker loop from CLI arguments."""
    parser = argparse.ArgumentParser(
        prog="python -m bridge.email_ingress", description=__doc__.splitlines()[0]
    )
    sub = parser.add_subparsers(dest="command", required=True)
    land = sub.add_parser("land", help="land one raw .eml on the email_ingress port")
    land.add_argument("path")
    subscribe = sub.add_parser("subscribe", help="create the Graph subscription")
    subscribe.add_argument("--notification-url", required=True)
    subscribe.add_argument("--minutes", type=int, default=DEFAULT_SUBSCRIPTION_MINUTES)
    renew = sub.add_parser("renew", help="renew the Graph subscription")
    renew.add_argument("subscription_id")
    renew.add_argument("--minutes", type=int, default=DEFAULT_SUBSCRIPTION_MINUTES)
    drain = sub.add_parser(
        "drain", help="drain landed mail on the served board-owning host"
    )
    drain.add_argument("--url", help=f"the host's base URL (default ${DRAIN_URL_ENV})")
    drain.add_argument("--timeout", type=float, default=DEFAULT_DRAIN_TIMEOUT)
    args = parser.parse_args(argv)
    if args.command == "land":
        return _land(args.path)
    if args.command == "drain":
        url = (args.url or os.environ.get(DRAIN_URL_ENV, "")).strip()
        if not url:
            parser.error(f"drain needs --url or {DRAIN_URL_ENV}")
        return _drain(url, args.timeout, client=client)
    if args.command == "subscribe":
        return _subscribe(args.notification_url, args.minutes)
    return _renew(args.subscription_id, args.minutes)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
