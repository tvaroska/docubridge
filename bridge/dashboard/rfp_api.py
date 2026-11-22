"""Consumer RFP read API + manual "chase now" (S0-frontend-1) — mounted on the dashboard host.

The internal-zone Ops Dashboard host gains three routes over its injected ``RfpBoard``:

* ``GET /rfps`` — every open RFP as ``readmodel.project_rfp_status`` (``[]`` with no board).
* ``GET /rfps/{rfp_id}`` — one RFP: carrier x product line → effective status, due date,
  last activity, versions, outstanding reasons (verbatim), ``as_of``, ``api_version`` and
  each carrier's reply address. This is what a consumer's own status board renders (not an
  iframe); the dashboard frontend renders the same matrix off the SSE row.
* ``POST /rfps/{rfp_id}/chase`` body ``{"party": <carrier> | null}`` — the operator's manual
  chase. One ``bridge.outbound.Nudge`` per targeted carrier (``party`` named, or every
  carrier with something chaseable) over the ``a2a_client`` port, keyed ``seq = leg.chases``
  so a second chase is a new send and a re-drive dedupes. The board records the chase
  (``bridge.rfp.record_chase``: audit-only, no cell moves). 404 for an unknown RFP/carrier,
  409 when there is nothing outstanding to chase, 503 when the outbound adapter is
  misconfigured.

**Concurrency (the S0-arch-13 rule).** Every board writer runs on the event loop and does
its fresh read → mutate → ``put`` with no ``await`` in between. The chase route is
``async``: the (possibly blocking) sends run in the threadpool first, then the fresh read →
``record_chase`` → ``put`` runs on the loop without yielding.

**Client wiring.** An injected ``outbound`` (any ``A2AClient``) wins; otherwise the port's
adapter is built lazily on the first chase (never at app construction — the gcp adapter
refuses without ``BRIDGE_GATEWAY_HOST``) and reused, so its receipt store dedupes re-drives.

Isolation: imports ``bridge.*`` + ``contract.*`` + ``fastapi`` / ``pydantic`` / ``starlette``
+ stdlib only; NEVER ``agents/``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict
from starlette.concurrency import run_in_threadpool

from bridge.dashboard import readmodel
from bridge.outbound import Nudge
from bridge.ports import Port, get_adapter, resolve_mode
from bridge.rfp import RfpCollection, chase_message, chaseable_items, record_chase
from bridge.user_interaction.demo_session import (
    mutating_auth_dependency,
    read_auth_dependency,
)

if TYPE_CHECKING:
    from bridge.dashboard.board import RfpBoard
    from bridge.email_ingress.correlation import IntakeAddressing
    from bridge.outbound import A2AClient, NudgeReceipt

CHASE_CHANNEL = "email"


class ChaseBody(BaseModel):
    """POST /rfps/{rfp_id}/chase body: one carrier, or ``None`` = every chaseable carrier."""

    model_config = ConfigDict(extra="forbid")

    party: str | None = None


def _resolve_chase_targets(
    rfp: RfpCollection, rfp_id: str, party: str | None
) -> list[str]:
    if party is not None:
        try:
            rfp.leg_for(party)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e.args[0])) from e
        targets = [party] if chaseable_items(rfp, party) else []
    else:
        targets = [leg.party for leg in rfp.legs if chaseable_items(rfp, leg.party)]
    if not targets:
        raise HTTPException(
            status_code=409,
            detail=f"nothing outstanding to chase on RFP {rfp_id!r}"
            + (f" for {party!r}" if party is not None else ""),
        )
    return targets


def _build_chase_nudges(
    board: RfpBoard,
    rfp: RfpCollection,
    rfp_id: str,
    targets: list[str],
    intake: IntakeAddressing | None,
) -> list[Nudge]:
    view = board.get(rfp_id)
    step_of = {c.party: c.lifecycle for c in view.carriers}
    nudges: list[Nudge] = []
    for target in targets:
        leg = rfp.leg_for(target)
        reply = intake.reply_address(rfp_id, target) if intake else None
        tag = intake.reply_subject_tag(rfp_id, target) if intake else None
        lines = [c.product_line for c in chaseable_items(rfp, target)]
        nudges.append(
            Nudge(
                context_id=leg.context_id,
                party=target,
                step=step_of[target],
                channel=CHASE_CHANNEL,
                message=chase_message(
                    rfp,
                    target,
                    lines=lines,
                    reply_address=reply,
                    reply_subject_tag=tag,
                ),
                seq=leg.chases,
            )
        )
    return nudges


async def _send_chase_nudges(
    client: A2AClient, nudges: list[Nudge]
) -> tuple[list[NudgeReceipt], Exception | None]:
    receipts: list[NudgeReceipt] = []
    for nudge in nudges:
        try:
            receipts.append(await run_in_threadpool(client.send, nudge))
        except Exception as e:  # noqa: BLE001 - recorded below, then surfaced as 502
            return receipts, e
    return receipts, None


def _record_chase_receipts(
    board: RfpBoard, rfp_id: str, receipts: list[NudgeReceipt]
) -> None:
    now = board.clock()
    current = board.rfps.get(rfp_id)
    if current is None:
        raise HTTPException(status_code=404, detail=f"unknown RFP {rfp_id!r}")
    for receipt in receipts:
        sent = receipt.nudge
        # Record once per seq: a concurrent chase that already recorded this seq
        # (its send deduped here) must not count twice; a crash re-drive (seq not
        # yet recorded) still records.
        if current.leg_for(sent.party).chases == sent.seq:
            current = record_chase(
                current, sent.party, now=now, step=sent.step, channel=sent.channel
            )
    board.put(current)


def _require_rfp_board(rfps: RfpBoard | None) -> RfpBoard:
    if rfps is None:
        raise HTTPException(status_code=404, detail="no RFP board configured")
    return rfps


def _resolve_outbound_client(
    outbound: A2AClient | None, built: dict[str, A2AClient]
) -> A2AClient:
    if outbound is not None:
        return outbound
    if "client" not in built:
        try:
            adapter = get_adapter(Port.a2a_client, resolve_mode(Port.a2a_client))
            built["client"] = adapter.build_client()
        except RuntimeError as e:
            raise HTTPException(status_code=503, detail=str(e)) from e
    return built["client"]


def _project_rfp_or_404(
    rfps: RfpBoard | None, rfp_id: str, intake: IntakeAddressing | None
) -> dict[str, Any]:
    board = _require_rfp_board(rfps)
    try:
        view = board.get(rfp_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=f"unknown RFP {rfp_id!r}") from e
    return readmodel.project_rfp_status(view, intake=intake)


def mount_rfp_routes(  # noqa: PLR0913 - public dashboard RFP route mount
    app: FastAPI,
    *,
    rfps: RfpBoard | None,
    intake: IntakeAddressing | None,
    outbound: A2AClient | None = None,
    principal_roles: dict[str, Any] | None = None,
    require_auth: bool = False,
) -> None:
    """Add the consumer RFP read API + the manual chase route to ``app``."""
    built: dict[str, A2AClient] = {}
    read_deps = [
        read_auth_dependency(
            default_role="broker",
            principal_roles=principal_roles,
            require_auth=require_auth,
            allow_external_roles=False,
        )
    ]
    mutating_deps = [
        mutating_auth_dependency(
            default_role="broker",
            principal_roles=principal_roles,
            require_auth=require_auth,
            allow_external_roles=False,
        )
    ]

    @app.get("/rfps", dependencies=read_deps)
    def get_rfps() -> list[dict[str, Any]]:
        if rfps is None:
            return []
        return [
            readmodel.project_rfp_status(view, intake=intake)
            for _, view in rfps.items()
        ]

    @app.get("/rfps/{rfp_id}", dependencies=read_deps)
    def get_rfp(rfp_id: str) -> dict[str, Any]:
        return _project_rfp_or_404(rfps, rfp_id, intake)

    @app.post("/rfps/{rfp_id}/chase", dependencies=mutating_deps)
    async def post_chase(rfp_id: str, body: ChaseBody | None = None) -> dict[str, Any]:
        board = _require_rfp_board(rfps)
        party = body.party if body is not None else None
        rfp = board.rfps.get(rfp_id)
        if rfp is None:
            raise HTTPException(status_code=404, detail=f"unknown RFP {rfp_id!r}")
        targets = _resolve_chase_targets(rfp, rfp_id, party)
        nudges = _build_chase_nudges(board, rfp, rfp_id, targets, intake)
        # Sends first (may block — off the loop); a failure records what already went.
        receipts, failure = await _send_chase_nudges(
            _resolve_outbound_client(outbound, built), nudges
        )
        # Fresh read → record → put, with NO await in between.
        _record_chase_receipts(board, rfp_id, receipts)
        if failure is not None:
            raise HTTPException(
                status_code=502,
                detail=f"chase send failed after {len(receipts)} of {len(nudges)}: "
                f"{type(failure).__name__}: {failure}",
            )
        return {
            "chased": [
                {
                    "party": r.nudge.party,
                    "step": r.nudge.step.value,
                    "idempotency_key": r.idempotency_key,
                    "resent": r.resent,
                    "reply_address": intake.reply_address(rfp_id, r.nudge.party)
                    if intake
                    else None,
                }
                for r in receipts
            ],
            "rfp": readmodel.project_rfp_status(board.get(rfp_id), intake=intake),
        }
