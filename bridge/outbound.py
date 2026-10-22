"""Outbound A2A client seam (SEAM-7): nudge (OUT-1/2), dial-out + Deliver (OUT-3/4).

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

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from bridge.followup import FollowupEvent, Notifier
from contract.enums import LifecycleStatus
from contract.wire import ArtifactRef

# Party-facing nudge steps. `escalated` is an INTERNAL escalation (dashboard/servicer),
# not a party send — and max_nudges == 2 (overdue + reminder). See §2(b).
NUDGE_STEPS: tuple[LifecycleStatus, ...] = (
    LifecycleStatus.overdue,
    LifecycleStatus.reminder,
)


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
    """The a2a_client seam's outbound abstraction. Local = OutboundClient; GCP = P7."""

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
    """Idempotent, crash-safe outbound client (SEAM-7 local, OUT-1/3/4).

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
