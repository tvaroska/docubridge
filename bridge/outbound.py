"""Outbound A2A client port (PORT-7): nudge (OUT-1/2), dial-out + Deliver (OUT-3/4).

The local adapter is an OutboundClient over an *injected fake transport* (an in-process
outbox). Every send is idempotent and crash-safe: replaying the same idempotency key
returns the prior receipt and never delivers twice (OUT-1). Three outbound activities all
ride the ONE fake transport — nothing new at the wire, only a different target:

* **Nudge** (OUT-1/2) — the party-channel chase. build_nudge_notifier() adapts the client
  to bridge.followup's Notifier hook so a FollowupLadder fires a real chase send on each
  nudge step (overdue, reminder) — the first concrete outbound Address needs.
* **DialOut** (OUT-3) — the Bridge *originating* an A2A exchange against a counterparty:
  Agent-Card discovery (resolve the peer's card, verify it advertises the requested
  process) → message/send. The discovery step GATES the send (a Bridge never message/sends
  to a peer that can't serve the request); an unadvertised process raises ValueError.
* **Delivery** (OUT-4) — push a finished document/result (here the normalized program
  comparison) outward, carried **by reference** as a contract.wire.ArtifactRef (bytes never
  travel on the wire).

**Designed-but-unbuilt this cycle (no silent cap):**

* The **real** ADK dial via ``RemoteA2aAgent`` + consuming a counterparty's
  ``input-required`` park as a ``RemoteA2aAgent`` consumer lands when the real carrier
  agents are stood up in **S1-benefits-6** (ADK Carrier A / LangGraph Carrier B). This
  module dials a *simulated* counterparty over the fake transport, exactly as the nudge is
  a fake send.
* The **authenticated egress transport** (workload Agent Identity, Agent-Card host vs.
  Gateway-authorized host reconciliation) is the **P7 GCP adapter**; the registry raises
  ``NotImplementedError`` until then.
* The **multi-turn Negotiate revise loop** + versioned artifacts + per-leg bind / program
  rollup is **S1-benefits-5**. This module originates the exchange and delivers the
  *initial* comparison; it does not run the revise loop.

Imports contract + bridge.followup only. NO google.adk, NO a2a, NEVER agents/.
Synchronous (no asyncio). NO outbound message carries a long-lived party bearer token —
workload auth (Agent Identity) is the P7 GCP transport's job, never a field on the message.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urljoin, urlparse

from bridge.followup import FollowupEvent, Notifier
from contract.enums import LifecycleStatus
from contract.wire import ArtifactRef

A2A_PROTOCOL_VERSION = "1.0"
A2A_CARD_WELL_KNOWN_PATH = "/.well-known/agent-card.json"

# Party-facing nudge steps. `escalated` is an INTERNAL escalation (dashboard/servicer),
# not a party send — and max_nudges == 2 (overdue + reminder). See §2(b).
NUDGE_STEPS: tuple[LifecycleStatus, ...] = (
    LifecycleStatus.overdue,
    LifecycleStatus.reminder,
)


class _HttpResponseLike(Protocol):
    def raise_for_status(self) -> object: ...

    def json(self) -> object: ...


class _HttpClientLike(Protocol):
    def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> _HttpResponseLike: ...

    def post(
        self,
        url: str,
        *,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> _HttpResponseLike: ...


@runtime_checkable
class OutboundMessage(Protocol):
    """Any egress message the client can idempotently deliver over the transport.

    The idempotency_key namespaces the receipt store (``nudge:`` / ``dial:`` / ``deliver:``
    prefixes never collide across activities), so one transport + one client handle all
    three activities with identical crash-safe re-drive semantics.
    """

    @property
    def idempotency_key(self) -> str:
        """Return the deterministic idempotency key for this outbound message."""
        ...


@dataclass(frozen=True)
class Nudge:
    """An outbound party-channel chase send. NO bearer token field (OUT-1).

    ``seq`` is set only by a **manual** chase (S0-frontend-1, the operator's "chase now"):
    the leg's chase count *before* this chase (``RfpLeg.chases``). A ladder nudge leaves it
    ``None`` and keeps the one-send-per-step key; a manual chase is keyed by ``seq`` alone
    (step-independent), so a second chase at the same step is a new send, while a re-drive
    of the same chase after a crash still dedupes even if the ladder stepped meanwhile.
    """

    context_id: str
    party: str
    step: LifecycleStatus  # overdue | reminder (a manual chase: the carrier's step)
    channel: str  # delivery channel; callers pass a fixed default ("email")
    message: str
    seq: int | None = None  # manual chase sequence (None = ladder nudge)

    @property
    def idempotency_key(self) -> str:
        """Return the deterministic idempotency key for this follow-up nudge."""
        if self.seq is None:
            return f"nudge:{self.context_id}:{self.step.value}"
        return f"nudge:{self.context_id}:chase-{self.seq}"


@dataclass(frozen=True)
class DialOut:
    """The Bridge originating an A2A exchange against a counterparty (OUT-3).

    ``card_url`` is the peer's Agent Card URL — the discovery target ``dial()`` resolves and
    checks against ``process`` before it message/sends. ``process`` is the capability the
    Bridge needs (e.g. the ``benefits-procurement`` process skill). NO bearer token field —
    workload auth (Agent Identity) is the P7 transport's job, never a field here.
    """

    context_id: str
    party: str
    process: str  # the capability requested (must be advertised)
    card_url: str  # the peer Agent Card URL (discovery target)
    message: str  # the request payload (e.g. "request benefits-quote …")

    @property
    def idempotency_key(self) -> str:
        """Return the deterministic idempotency key for this outbound A2A dial-out."""
        return f"dial:{self.context_id}"


@dataclass(frozen=True)
class Delivery:
    """Push a finished document/result outward, by reference (OUT-4).

    ``artifact`` is a contract.wire.ArtifactRef — a by-reference doc handle; bytes NEVER
    travel on the wire (ART discipline). NO bearer token field.
    """

    context_id: str
    recipient: str
    artifact: ArtifactRef
    channel: str = "a2a"
    summary: str = ""

    @property
    def idempotency_key(self) -> str:
        """Return the deterministic idempotency key for this delivery record."""
        return f"deliver:{self.context_id}:{self.recipient}"


@dataclass(frozen=True)
class PeerCard:
    """A counterparty's Agent Card (the discovery record).

    ``processes`` is the tuple of process/doctype capabilities the peer advertises — what
    ``dial()`` checks the requested ``process`` against.
    """

    party: str
    card_url: str
    processes: tuple[str, ...]


@runtime_checkable
class PeerDirectory(Protocol):
    """Agent-Card discovery. Local = injected FakePeerDirectory; GCP = P7 resolver."""

    def resolve(self, card_url: str) -> PeerCard:
        """Resolve a logical peer identifier into its A2A endpoint URL."""
        ...


class FakePeerDirectory:
    """In-memory card resolver — the local adapter's injected directory (mirrors
    FakeTransport)."""

    def __init__(self, cards: dict[str, PeerCard] | None = None) -> None:
        self.cards: dict[str, PeerCard] = dict(cards or {})

    def resolve(self, card_url: str) -> PeerCard:
        """Resolve a logical peer identifier from the in-memory test peer map."""
        try:
            return self.cards[card_url]
        except KeyError:
            raise ValueError(
                f"no Agent Card discoverable at {card_url!r}; "
                f"known: {sorted(self.cards)}"
            ) from None


@dataclass(frozen=True)
class NudgeReceipt:
    """Proof a nudge was sent — or was already sent (idempotent re-drive)."""

    idempotency_key: str
    nudge: Nudge
    resent: bool = False  # True when a re-driven send returned the prior receipt


@dataclass(frozen=True)
class DialReceipt:
    """Proof a dial-out was sent — or was already sent (idempotent re-drive)."""

    idempotency_key: str
    dialout: DialOut
    resent: bool = False


@dataclass(frozen=True)
class DeliveryReceipt:
    """Proof a Delivery was pushed — or was already pushed (idempotent re-drive)."""

    idempotency_key: str
    delivery: Delivery
    resent: bool = False


OutboundReceipt = NudgeReceipt | DialReceipt | DeliveryReceipt


@runtime_checkable
class OutboundTransport(Protocol):
    """The injectable wire. Local = FakeTransport; GCP = Agent-Identity transport (P7)."""

    def deliver(self, message: OutboundMessage) -> None:
        """Deliver a serialized outbound payload to a resolved peer endpoint."""
        ...


class FakeTransport:
    """In-process outbox — the local adapter's injected transport.

    Holds any outbound message (Nudge / DialOut / Delivery); existing ``outbox == [nudge]``
    assertions still hold because a nudge-only run appends only nudges.
    """

    def __init__(self) -> None:
        self.outbox: list[OutboundMessage] = []

    def deliver(self, message: OutboundMessage) -> None:
        """Record an outbound payload delivery in memory for deterministic testing."""
        self.outbox.append(message)


@runtime_checkable
class A2AClient(Protocol):
    """The a2a_client port's outbound abstraction. Local = OutboundClient; GCP = P7."""

    def send(self, nudge: Nudge) -> NudgeReceipt:
        """Enqueue and deliver an outbound message or nudge with idempotency protection."""
        ...

    def dial(self, dialout: DialOut, *, directory: PeerDirectory) -> DialReceipt:
        """Initiate an outbound A2A dial-out exchange with a remote peer agent."""
        ...

    def deliver(self, delivery: Delivery) -> DeliveryReceipt:
        """Deliver a final verified package or status notification to the caller peer."""
        ...

    def sent(self) -> list[OutboundReceipt]:
        """Return the chronological tuple of all outbound messages delivered by this client."""
        ...


class OutboundClient:
    """Idempotent, crash-safe outbound client (PORT-7 local, OUT-1/3/4).

    Wraps an injected transport (default FakeTransport). Every activity — send (nudge),
    dial (dial-out), deliver (Deliver) — is keyed by its message's idempotency_key;
    re-driving the same key returns the prior receipt WITHOUT delivering again (the
    crash-safe re-drive). The transport outbox thus holds one entry per key. The
    ``nudge:`` / ``dial:`` / ``deliver:`` prefixes namespace the receipt store so keys
    never collide across activities.
    """

    def __init__(self, transport: OutboundTransport | None = None) -> None:
        self._transport: OutboundTransport = transport or FakeTransport()
        self._receipts: dict[str, OutboundReceipt] = {}

    def _dispatch(self, message: OutboundMessage) -> bool:
        """Idempotently deliver ANY outbound message over the transport.

        Returns True if this was a crash-safe re-drive (key already delivered — no double
        send), False if the message was delivered now. The per-activity method wraps this
        to mint / return the typed receipt.
        """
        if (
            message.idempotency_key in self._receipts
        ):  # crash-safe re-drive: no double send
            return True
        self._transport.deliver(message)
        return False

    def send(self, nudge: Nudge) -> NudgeReceipt:
        """Send an outbound notification through the configured transport."""
        key = nudge.idempotency_key
        if self._dispatch(nudge):
            prior = self._receipts[key]
            return NudgeReceipt(key, prior.nudge, resent=True)  # type: ignore[union-attr]
        receipt = NudgeReceipt(key, nudge)
        self._receipts[key] = receipt
        return receipt

    def dial(self, dialout: DialOut, *, directory: PeerDirectory) -> DialReceipt:
        """Originate an A2A exchange: Agent-Card discovery GATE → message/send (OUT-3).

        Resolves the peer's card and verifies it advertises ``dialout.process`` BEFORE any
        send — a Bridge never message/sends to a peer that can't serve the request. Raises
        ValueError naming the mismatch if the process is unadvertised (nothing delivered).
        """
        card = directory.resolve(dialout.card_url)
        if dialout.process not in card.processes:
            raise ValueError(
                f"peer {card.party!r} at {dialout.card_url!r} does not advertise process "
                f"{dialout.process!r}; advertised: {list(card.processes)}"
            )
        key = dialout.idempotency_key
        if self._dispatch(dialout):
            prior = self._receipts[key]
            return DialReceipt(key, prior.dialout, resent=True)  # type: ignore[union-attr]
        receipt = DialReceipt(key, dialout)
        self._receipts[key] = receipt
        return receipt

    def deliver(self, delivery: Delivery) -> DeliveryReceipt:
        """Push a finished result outward by reference, idempotently (OUT-4)."""
        key = delivery.idempotency_key
        if self._dispatch(delivery):
            prior = self._receipts[key]
            return DeliveryReceipt(key, prior.delivery, resent=True)  # type: ignore[union-attr]
        receipt = DeliveryReceipt(key, delivery)
        self._receipts[key] = receipt
        return receipt

    def sent(self) -> list[OutboundReceipt]:
        """Return the chronological tuple of all sent outbound notifications."""
        return list(self._receipts.values())

    def send_message(  # noqa: PLR0913 - public A2A client contract with keyword options
        self,
        *,
        endpoint_url: str,
        payload: dict[str, Any] | None = None,
        text: str | None = None,
        expected_skill: str | None = None,
        context_id: str | None = None,
        headers: dict[str, str] | None = None,
        http_client: _HttpClientLike | None = None,
        timeout: float = 20.0,
        url_reconciler: Callable[[str], str] | None = None,
        auth_header_provider: Callable[[str], dict[str, str]] | None = None,
    ) -> A2ASendResult:
        """Discover a peer's Agent Card and invoke canonical A2A v1.0 ``SendMessage``."""
        return send_a2a_message(
            endpoint_url=endpoint_url,
            payload=payload,
            text=text,
            expected_skill=expected_skill,
            context_id=context_id,
            headers=headers,
            http_client=http_client,
            timeout=timeout,
            url_reconciler=url_reconciler,
            auth_header_provider=auth_header_provider,
        )


def stringify_decimals(value: object) -> object:
    """Recursively convert ``Decimal`` values to strings before A2A JSON/Struct encoding."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {k: stringify_decimals(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [stringify_decimals(v) for v in value]
    return value


def _host_token(value: str) -> str:
    parsed = urlparse(value if "//" in value else f"//{value}")
    host = (parsed.hostname or "").lower()
    port = parsed.port
    if (parsed.scheme == "https" and port == 443) or (
        parsed.scheme == "http" and port == 80
    ):
        port = None
    return f"{host}:{port}" if port is not None else host


def reconcile_origin(target_url: str, expected_origin: str) -> str:
    """Verify a peer card's advertised RPC URL shares the expected origin scheme and host."""
    advertised = _host_token(target_url)
    authorized = _host_token(expected_origin)
    if advertised and authorized and advertised != authorized:
        raise ValueError(
            f"peer Agent Card host {advertised!r} (from {target_url!r}) does not "
            f"match expected origin host {authorized!r}; refusing to send."
        )
    target_scheme = urlparse(target_url).scheme.lower()
    expected_scheme = urlparse(expected_origin).scheme.lower()
    if (
        expected_scheme == "https"
        and target_scheme
        and target_scheme != expected_scheme
    ):
        raise ValueError(
            f"peer Agent Card scheme {target_scheme!r} (from {target_url!r}) "
            f"downgrades expected {expected_scheme!r} origin; refusing to send."
        )
    return target_url


def _strip_outcome_baggage(headers: dict[str, str]) -> dict[str, str]:
    cleaned: dict[str, str] = {}
    for key, val in headers.items():
        if key.lower() == "baggage":
            kept = [
                part.strip()
                for part in val.split(",")
                if part.strip()
                and not part.strip()
                .lower()
                .startswith(("outcome.id=", "outcome.owner="))
            ]
            if kept:
                cleaned[key] = ",".join(kept)
        else:
            cleaned[key] = val
    return cleaned


@dataclass(frozen=True)
class A2ASendResult:
    """Parsed canonical A2A v1.0 JSON-RPC ``SendMessage`` response."""

    card_url: str
    rpc_url: str
    task_id: str
    task_state: str
    card: dict[str, Any]
    artifacts: tuple[dict[str, Any], ...]
    task_metadata: dict[str, Any]
    response_text: str

    def find_data_part(
        self,
        *,
        kind: str | None = None,
        artifact_name: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]] | None:
        """Select a JSON data part by ``metadata.kind`` / ``artifact.name`` (never by index)."""
        if kind or artifact_name:
            for artifact in self.artifacts:
                art_name = str(artifact.get("name") or "")
                art_meta = dict(artifact.get("metadata") or {})
                for part in artifact.get("parts") or ():
                    data = part.get("data")
                    if not isinstance(data, dict):
                        continue
                    part_meta = dict(part.get("metadata") or {})
                    if kind and part_meta.get("kind") == kind:
                        return data, part_meta, art_meta
                    if artifact_name and art_name == artifact_name:
                        return data, part_meta, art_meta
        for artifact in self.artifacts:
            art_meta = dict(artifact.get("metadata") or {})
            for part in artifact.get("parts") or ():
                data = part.get("data")
                if isinstance(data, dict):
                    return data, dict(part.get("metadata") or {}), art_meta
        return None

    def find_file_uri(
        self,
        *,
        mime_type: str | None = None,
        artifact_name: str | None = None,
    ) -> str:
        """Extract a by-reference document URI from A2A URL parts or artifact metadata."""
        for artifact in self.artifacts:
            if artifact_name and str(artifact.get("name") or "") != artifact_name:
                continue
            for part in artifact.get("parts") or ():
                url = str(part.get("url") or "").strip()
                if not url:
                    continue
                part_mime = str(
                    part.get("mediaType") or part.get("media_type") or ""
                ).strip()
                if mime_type is None or part_mime == mime_type:
                    return url
        for key in ("pdf_uri", "file_uri"):
            found = self.find_metadata_value(key)
            if isinstance(found, str) and found.strip():
                return found.strip()
        return ""

    def find_metadata_value(self, key: str) -> object:
        """Look up a metadata attribute across artifact parts, artifacts, data, task, and card."""
        for artifact in self.artifacts:
            for part in artifact.get("parts") or ():
                part_meta = part.get("metadata")
                if isinstance(part_meta, dict) and part_meta.get(key) not in (None, ""):
                    return part_meta[key]
            art_meta = artifact.get("metadata")
            if isinstance(art_meta, dict) and art_meta.get(key) not in (None, ""):
                return art_meta[key]
            for part in artifact.get("parts") or ():
                data = part.get("data")
                if isinstance(data, dict) and data.get(key) not in (None, ""):
                    return data[key]
        if self.task_metadata.get(key) not in (None, ""):
            return self.task_metadata[key]
        if self.card.get(key) not in (None, ""):
            return self.card[key]
        return None


def _card_advertises_skill(card: dict[str, Any], expected_skill: str) -> bool:
    for skill in card.get("skills") or ():
        if not isinstance(skill, dict):
            continue
        if skill.get("id") == expected_skill:
            return True
        tags = skill.get("tags") or ()
        if f"process:{expected_skill}" in tags:
            return True
    return False


def _resolve_rpc_url(card: dict[str, Any], endpoint_url: str) -> str:
    interfaces = (
        card.get("supportedInterfaces") or card.get("supported_interfaces") or ()
    )
    for iface in interfaces:
        if not isinstance(iface, dict):
            continue
        binding = str(
            iface.get("protocolBinding") or iface.get("protocol_binding") or ""
        ).upper()
        url = str(iface.get("url") or "").strip()
        if url and (not binding or binding == "JSONRPC"):
            return url
    card_url = str(card.get("url") or "").strip()
    if card_url:
        return card_url
    return f"{endpoint_url.rstrip('/')}/" if endpoint_url else "/"


def _build_card_url(endpoint_url: str) -> str:
    cleaned = endpoint_url.strip()
    if not cleaned:
        return A2A_CARD_WELL_KNOWN_PATH
    if cleaned.endswith((A2A_CARD_WELL_KNOWN_PATH, "/.well-known/agent.json")):
        return cleaned
    return f"{cleaned.rstrip('/')}{A2A_CARD_WELL_KNOWN_PATH}"


def discover_a2a_card(  # noqa: PLR0913 - public A2A discovery contract with keyword options
    endpoint_url: str,
    *,
    expected_skill: str | None = None,
    headers: dict[str, str] | None = None,
    http_client: _HttpClientLike | None = None,
    timeout: float = 15.0,
    url_reconciler: Callable[[str], str] | None = None,
    auth_header_provider: Callable[[str], dict[str, str]] | None = None,
) -> tuple[dict[str, Any], str, str]:
    """Fetch ``/.well-known/agent-card.json``, gate on ``expected_skill``, and resolve RPC URL."""
    card_url = _build_card_url(endpoint_url)
    get_headers = _strip_outcome_baggage(dict(headers or {}))
    get_headers["A2A-Version"] = A2A_PROTOCOL_VERSION
    if auth_header_provider is not None:
        get_headers.update(auth_header_provider(card_url))

    if http_client is not None:
        resp: _HttpResponseLike = http_client.get(card_url, headers=get_headers)
    else:
        import httpx

        with httpx.Client(timeout=timeout) as client:
            resp = client.get(card_url, headers=get_headers)
    resp.raise_for_status()
    card = resp.json()
    if not isinstance(card, dict):
        raise ValueError(f"Agent Card at {card_url!r} did not return a JSON object.")

    if expected_skill and not _card_advertises_skill(card, expected_skill):
        advertised = [
            s.get("id") for s in (card.get("skills") or ()) if isinstance(s, dict)
        ]
        raise ValueError(
            f"peer Agent Card at {card_url!r} does not advertise skill "
            f"{expected_skill!r}; advertised: {advertised}"
        )

    rpc_url = _resolve_rpc_url(card, endpoint_url)
    if rpc_url.startswith("/") and endpoint_url.strip():
        rpc_url = urljoin(f"{endpoint_url.rstrip('/')}/", rpc_url.lstrip("/"))

    client_base = str(getattr(http_client, "base_url", "") or "").strip()
    expected_origin = endpoint_url.strip() or client_base
    if url_reconciler is not None:
        rpc_url = url_reconciler(rpc_url)
    elif expected_origin and "://" in rpc_url:
        rpc_url = reconcile_origin(rpc_url, expected_origin)
    return card, card_url, rpc_url


def _parse_a2a_task_response(
    task: dict[str, Any],
    *,
    card_url: str,
    rpc_url: str,
    card: dict[str, Any],
) -> A2ASendResult:
    raw_status = task.get("status")
    status: dict[str, Any] = raw_status if isinstance(raw_status, dict) else {}
    raw_msg = status.get("message")
    status_msg: dict[str, Any] = raw_msg if isinstance(raw_msg, dict) else {}
    artifacts = tuple(a for a in (task.get("artifacts") or ()) if isinstance(a, dict))
    response_text = " ".join(
        str(p.get("text"))
        for p in (status_msg.get("parts") or ())
        if isinstance(p, dict) and isinstance(p.get("text"), str)
    ).strip()
    task_id = str(task.get("id") or "")
    task_state = str(status.get("state") or "")
    if task_state in (
        "TASK_STATE_FAILED",
        "TASK_STATE_REJECTED",
        "TASK_STATE_CANCELED",
    ):
        raise RuntimeError(
            f"A2A SendMessage task {task_id!r} at {rpc_url!r} ended in "
            f"{task_state}: {response_text or 'no details'}"
        )
    return A2ASendResult(
        card_url=card_url,
        rpc_url=rpc_url,
        task_id=task_id,
        task_state=task_state,
        card=card,
        artifacts=artifacts,
        task_metadata=dict(task.get("metadata") or {}),
        response_text=response_text,
    )


def _parse_a2a_direct_message(
    direct_msg: dict[str, Any],
    *,
    card_url: str,
    rpc_url: str,
    card: dict[str, Any],
) -> A2ASendResult:
    msg_parts = [p for p in (direct_msg.get("parts") or ()) if isinstance(p, dict)]
    response_text = " ".join(
        str(p.get("text")) for p in msg_parts if isinstance(p.get("text"), str)
    ).strip()
    return A2ASendResult(
        card_url=card_url,
        rpc_url=rpc_url,
        task_id=str(direct_msg.get("taskId") or ""),
        task_state="TASK_STATE_COMPLETED",
        card=card,
        artifacts=(
            {
                "name": "",
                "parts": msg_parts,
                "metadata": dict(direct_msg.get("metadata") or {}),
            },
        ),
        task_metadata=dict(direct_msg.get("metadata") or {}),
        response_text=response_text,
    )


def _build_a2a_rpc_body(
    payload: dict[str, Any] | None,
    text: str | None,
    context_id: str | None,
) -> dict[str, Any]:
    sanitized = stringify_decimals(payload) if payload is not None else None
    parts: list[dict[str, Any]] = []
    if text is not None:
        parts.append({"text": text})
    elif sanitized is not None:
        parts.append({"text": json.dumps(sanitized, sort_keys=True)})
    if isinstance(sanitized, dict) and sanitized:
        parts.append({"data": sanitized, "mediaType": "application/json"})
    message: dict[str, Any] = {
        "messageId": uuid.uuid4().hex,
        "role": "ROLE_USER",
        "parts": parts,
    }
    if context_id:
        message["contextId"] = context_id
    return {
        "jsonrpc": "2.0",
        "id": uuid.uuid4().hex,
        "method": "SendMessage",
        "params": {"message": message},
    }


def send_a2a_message(  # noqa: PLR0913 - public A2A SendMessage contract with keyword options
    *,
    endpoint_url: str,
    payload: dict[str, Any] | None = None,
    text: str | None = None,
    expected_skill: str | None = None,
    context_id: str | None = None,
    headers: dict[str, str] | None = None,
    http_client: _HttpClientLike | None = None,
    timeout: float = 20.0,
    url_reconciler: Callable[[str], str] | None = None,
    auth_header_provider: Callable[[str], dict[str, str]] | None = None,
) -> A2ASendResult:
    """Discover a peer's Agent Card and send a canonical A2A v1.0 JSON-RPC ``SendMessage``."""
    card, card_url, rpc_url = discover_a2a_card(
        endpoint_url,
        expected_skill=expected_skill,
        headers=headers,
        http_client=http_client,
        timeout=timeout,
        url_reconciler=url_reconciler,
        auth_header_provider=auth_header_provider,
    )
    rpc_body = _build_a2a_rpc_body(payload, text, context_id)
    post_headers = _strip_outcome_baggage(dict(headers or {}))
    post_headers.setdefault("content-type", "application/json")
    post_headers["A2A-Version"] = A2A_PROTOCOL_VERSION
    if auth_header_provider is not None:
        post_headers.update(auth_header_provider(rpc_url))

    if http_client is not None:
        resp: _HttpResponseLike = http_client.post(
            rpc_url, json=rpc_body, headers=post_headers
        )
    else:
        import httpx

        with httpx.Client(timeout=timeout) as client:
            resp = client.post(rpc_url, json=rpc_body, headers=post_headers)
    resp.raise_for_status()
    body = resp.json()
    if not isinstance(body, dict):
        raise RuntimeError(
            f"A2A SendMessage at {rpc_url!r} returned non-object JSON payload."
        )
    if body.get("error"):
        raise RuntimeError(
            f"A2A SendMessage JSON-RPC error from {rpc_url!r}: {body['error']}"
        )

    result = body.get("result") or {}
    task = result.get("task") if isinstance(result, dict) else None
    if (
        not isinstance(task, dict)
        and isinstance(result, dict)
        and "artifacts" in result
    ):
        task = result

    if isinstance(task, dict):
        return _parse_a2a_task_response(
            task, card_url=card_url, rpc_url=rpc_url, card=card
        )

    direct_msg = result.get("message") if isinstance(result, dict) else None
    if isinstance(direct_msg, dict):
        return _parse_a2a_direct_message(
            direct_msg, card_url=card_url, rpc_url=rpc_url, card=card
        )

    raise RuntimeError(
        f"A2A SendMessage response from {rpc_url!r} contained neither a task nor a message."
    )


def default_nudge_message(event: FollowupEvent, party: str) -> str:
    """Default chase copy for a nudge step."""
    return f"Reminder ({event.step.value}) for {party}: your address-proof exchange needs a response."


def build_nudge_notifier(
    client: A2AClient,
    *,
    party: str,
    channel: str = "email",
    steps: tuple[LifecycleStatus, ...] = NUDGE_STEPS,
    message_for: Callable[[FollowupEvent], str] | None = None,
) -> Notifier:
    """Adapt the outbound client to bridge.followup's Notifier hook (OUT-2).

    Returns a Notifier that, on each *nudge* ladder step (overdue/reminder — NOT escalated),
    builds a Nudge and sends it through `client`. Plugged into FollowupLadder(..., notifier=…),
    it makes the P5-2 chase send real. escalated fires the ladder event but sends nothing.
    """

    def notify(event: FollowupEvent) -> None:
        if event.step not in steps:
            return  # escalated etc.: internal only, no send
        message = (
            message_for(event) if message_for else default_nudge_message(event, party)
        )
        client.send(
            Nudge(
                context_id=event.context_id,
                party=party,
                step=event.step,
                channel=channel,
                message=message,
            )
        )

    return notify
