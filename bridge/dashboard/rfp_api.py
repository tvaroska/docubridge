"""Consumer RFP read API + manual "chase now" (S0-frontend-1) — mounted on the dashboard host.

The internal-zone Ops Dashboard host gains three routes over its injected ``RfpBoard``:

* ``GET /rfps`` — every open RFP as ``readmodel.project_rfp_status`` (``[]`` with no board).
* ``GET /rfps/{rfp_id}`` — one RFP: carrier x product line → effective status, due date,
  last activity, versions, outstanding reasons (verbatim), ``as_of``, ``api_version`` and
  each carrier's reply address. This is what a consumer's own status board renders (not an
  iframe); the dashboard frontend renders the same matrix off the SSE row.
* ``POST /rfps/{rfp_id}/chase`` body ``{"party": <carrier> | null}`` — the operator's manual
  chase. One ``bridge.outbound.Nudge`` per targeted carrier (``party`` named, or every
  carrier with something chaseable) over the ``a2a_client`` seam, keyed ``seq = leg.chases``
  so a second chase is a new send and a re-drive dedupes. The board records the chase
  (``bridge.rfp.record_chase``: audit-only, no cell moves). 404 for an unknown RFP/carrier,
  409 when there is nothing outstanding to chase, 503 when the outbound adapter is
  misconfigured.

**Concurrency (the S0-arch-13 rule).** Every board writer runs on the event loop and does
its fresh read → mutate → ``put`` with no ``await`` in between. The chase route is
``async``: the (possibly blocking) sends run in the threadpool first, then the fresh read →
``record_chase`` → ``put`` runs on the loop without yielding.

**Client wiring.** An injected ``outbound`` (any ``A2AClient``) wins; otherwise the seam's
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
from bridge.rfp import chase_message, chaseable_items, record_chase
from bridge.seams import Seam, get_adapter, resolve_mode

if TYPE_CHECKING:
    from bridge.dashboard.board import RfpBoard
    from bridge.email_ingress.correlation import IntakeAddressing
    from bridge.outbound import A2AClient, NudgeReceipt

CHASE_CHANNEL = "email"


class ChaseBody(BaseModel):
    """POST /rfps/{rfp_id}/chase body: one carrier, or ``None`` = every chaseable carrier."""

    model_config = ConfigDict(extra="forbid")

    party: str | None = None


def mount_rfp_routes(
    app: FastAPI,
    *,
    rfps: RfpBoard | None,
    intake: IntakeAddressing | None,
    outbound: A2AClient | None = None,
) -> None:
    """Add the consumer RFP read API + the manual chase route to ``app``."""
    built: dict[str, Any] = {}

    def _client() -> A2AClient:
        if outbound is not None:
            return outbound
        if "client" not in built:
            try:
                built["client"] = get_adapter(
                    Seam.a2a_client, resolve_mode(Seam.a2a_client)
                ).build_client()
            except RuntimeError as e:
                raise HTTPException(status_code=503, detail=str(e)) from e
        return built["client"]

    def _board() -> RfpBoard:
        if rfps is None:
            raise HTTPException(status_code=404, detail="no RFP board configured")
        return rfps

    def _project(rfp_id: str) -> dict:
        board = _board()
        try:
            view = board.get(rfp_id)
        except KeyError as e:
            raise HTTPException(
                status_code=404, detail=f"unknown RFP {rfp_id!r}"
            ) from e
        return readmodel.project_rfp_status(view, intake=intake)

    @app.get("/rfps")
    def get_rfps() -> list[dict]:
        if rfps is None:
            return []
        return [
            readmodel.project_rfp_status(view, intake=intake)
            for _, view in rfps.items()
        ]

    @app.get("/rfps/{rfp_id}")
    def get_rfp(rfp_id: str) -> dict:
        return _project(rfp_id)

    @app.post("/rfps/{rfp_id}/chase")
    async def post_chase(rfp_id: str, body: ChaseBody | None = None) -> dict:
        board = _board()
        party = body.party if body is not None else None
        rfp = board.rfps.get(rfp_id)
        if rfp is None:
            raise HTTPException(status_code=404, detail=f"unknown RFP {rfp_id!r}")
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

        now = board.clock()
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

        # Sends first (may block — off the loop); a failure records what already went.
        client = _client()
        receipts: list[NudgeReceipt] = []
        failure: Exception | None = None
        for nudge in nudges:
            try:
                receipts.append(await run_in_threadpool(client.send, nudge))
            except Exception as e:  # recorded below, then surfaced as 502
                failure = e
                break

        # Fresh read → record → put, with NO await in between.
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
