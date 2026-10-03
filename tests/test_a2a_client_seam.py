import pytest

from bridge.outbound import (
    A2AClient,
    Delivery,
    DialOut,
    FakePeerDirectory,
    Nudge,
    PeerCard,
)
from bridge.seams import Seam, SeamMode, get_adapter
from contract.enums import LifecycleStatus
from contract.wire import ArtifactRef

# The Gateway-authorized host the gcp branch reconciles peer card URLs against
# (adk >=2.7 same-origin — a matching host passes, a mismatch refuses loud; the
# mismatch case is exercised in tests/test_gcp_a2a_client.py).
_GATEWAY_HOST = "host.example"


class _RecordingSender:
    """A credential-free fake HTTP sender for the Agent-Identity transport (mirrors
    the scheduler's fake Cloud Tasks client): records the POST, never fires."""

    def __init__(self) -> None:
        self.posts: list[dict] = []

    def __call__(self, *, url, headers, body) -> None:
        self.posts.append({"url": url, "headers": headers, "body": body})


@pytest.mark.seam("a2a_client")
def test_a2a_client_builds_outbound_client(adapter):
    """One parametrized parity test (local + gcp): both adapters build an A2AClient and
    the SAME send/dial/deliver flow yields non-resent receipts — terminal-outcome
    parity, credential-free. The gcp branch swaps only the transport (Agent-Identity
    OIDC + Gateway-host reconciliation); card URLs keep host == the Gateway host so
    reconciliation passes (the mismatch case lives in test_gcp_a2a_client.py)."""
    if adapter is SeamMode.gcp:
        card_url = f"https://{_GATEWAY_HOST}/rpc"
        client = get_adapter(Seam.a2a_client, adapter).build_client(
            gateway_host=_GATEWAY_HOST,
            sa_email="bridge-runtime@test-project.iam.gserviceaccount.com",
            id_token_fetcher=lambda audience: f"id-token-for::{audience}",
            sender=_RecordingSender(),
        )
    else:
        card_url = "card://carrier-a"
        client = get_adapter(Seam.a2a_client, adapter).build_client()
    assert isinstance(client, A2AClient)

    # Nudge — the party-channel chase (OUT-1/2).
    nudge = Nudge(context_id="x", party="jordan-lee",
                  step=LifecycleStatus.overdue, channel="email", message="hi")
    receipt = client.send(nudge)
    assert receipt.resent is False
    assert receipt.nudge is nudge

    # Dial-out — Agent-Card discovery → message/send (OUT-3), through the built client.
    directory = FakePeerDirectory(
        {card_url: PeerCard("carrier-a", card_url, ("benefits-procurement",))}
    )
    dialout = DialOut(context_id="ctx-a", party="carrier-a",
                      process="benefits-procurement", card_url=card_url,
                      message="request benefits-quote")
    dial_receipt = client.dial(dialout, directory=directory)
    assert dial_receipt.resent is False
    assert dial_receipt.dialout is dialout

    # Deliver — push a by-reference artifact outward (OUT-4).
    delivery = Delivery(context_id="ctx-a", recipient="broker",
                        artifact=ArtifactRef(uri="bridge://comparison/x"))
    deliver_receipt = client.deliver(delivery)
    assert deliver_receipt.resent is False
    assert deliver_receipt.delivery is delivery

    # All three receipts recorded on the one client.
    assert nudge in [getattr(r, "nudge", None) for r in client.sent()]
