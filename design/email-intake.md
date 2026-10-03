---
type: atom
up: "seams.md"
related:
  - "seams.md"
  - "edges.md"
  - "collect.md"
  - "artifacts.md"
  - "gcp-substrate.md"
tags: [bridge, edge, collect, email]
---

# Email intake — a dedicated mailbox, token-correlated, create-only landing

> A carrier answering a **solicited** request by email is just another collection channel. It mails the quote to a dedicated intake mailbox, at a reply address that carries an **exchange token**. The Bridge lands the raw message **create-only**, correlates it to exactly one open leg by token, extracts every attachment through the [extraction seam](seams.md), and files the readable ones through the same disposition gate as an A2A or portal submission. The attachment reaches the exchange as a leg-scoped `ArtifactRef` by reference. Mail the Bridge cannot place is **quarantined, visibly**; an attachment nothing can read is **recorded, never verdicted**.

## Why a dedicated mailbox

The alternative, watching each broker's own mailbox, needs a tenant-wide Graph `Mail.Read` grant. That puts every employee's personal, HR, and PHI mail in the app's reach. Instead the Bridge reads exactly **one** mailbox (for example `rfp-quotes@<tenant domain>`):

- The app registration holds the Graph **application** permission `Mail.Read`, with admin consent.
- An Exchange Online **Application Access Policy** restricts that app to a mail-enabled security group containing only the intake mailbox. Exchange Online's **RBAC for Applications** is the successor mechanism: a management scope plus `New-ManagementRoleAssignment -App <id> -Role "Application Mail.Read" -CustomResourceScope <scope>`. Either one achieves "this mailbox only".
- The webhook fetches messages **by id under the configured mailbox only**. A notification cannot steer it to another mailbox: the id is validated against `^[A-Za-z0-9+/=_-]{1,512}$`, and the URL path is built from configuration, never from the notification's `resource`.

## Solicited, not cold

The reply address is minted when the request goes out, so email intake is the **pull** motion over a new transport, not unsolicited push ([bridge scope](bridge.md)). Mail with no valid token is not a new exchange. It is quarantined `no_exchange_token`.

## The exchange token

- `token = base32(HMAC-SHA256(secret, "<rfp_id>\x00<party>"))[:16]`, lowercase: 80 bits, one token per **(RFP, carrier) leg**. The key is `BRIDGE_EMAIL_TOKEN_SECRET`, held only by the processor's host.
- **Carried two ways.** The primary carrier is the plus-address `rfp-quotes+<token>@<domain>`, found in `To` / `Cc` / `Delivered-To` / `X-Original-To`. The fallback is a subject tag `[ref:<token>]` for clients or relays that strip plus-addressing. `EmailProcessor.reply_address(rfp_id, party)` and `reply_subject_tag(...)` produce both for the outbound request.
- **Correlation** recomputes the token for every open leg and compares in constant time (`hmac.compare_digest`). A token is never decoded, and the carrier is never inferred from the sender address.
- **Leg isolation.** A token names exactly one carrier leg, and holding it grants nothing beyond that leg. Tokens naming two **different** legs are refused `ambiguous_token`, never split. The same token repeated is fine.

## Flow

```
Carrier ──SMTP──▶ M365 mailbox rfp-quotes@ (app sees ONLY this mailbox)
                     │ Graph change notification (created)
                     ▼
  email webhook  (public, webhook-only; build_email_app with graph bound)
     validationToken handshake · clientState check · GET /users/{mbx}/messages/{id}/$value
     → EmailIngressSeam.land(key, raw)      [create-only; a Graph re-delivery is a duplicate]
                     │
                     ▼
  EmailProcessor.drain()  (the host that owns the RFP board — build_dashboard_app(rfps=…);
                          deployed: the RFP board host, POST /email/drain by Cloud Scheduler)
     pending() → read → parse_mime → dmarc / limits → find_tokens → correlate → (rfp, leg)
     per attachment: put_attachment → extract (extraction seam) → signals → dispose_signals
     → persist bytes to the ADK artifact store (leg-scoped) → ArtifactRef (scoped fetch uri)
     → acknowledge + rfp.receive / rfp.note_unreadable → board.put → mark_processed(key, outcome)
```

The processor binds on the host that owns the RFP board: `build_dashboard_app(rfps=…)` (the deployed RFP board host `bridge.dashboard.board_host`, and the local RFP demo host that mounts it at `/ops`) builds it from env with `email_processor_from_env` — `BRIDGE_EMAIL_MAILBOX` + `BRIDGE_EMAIL_TOKEN_SECRET` + a landing location (`BRIDGE_EMAIL_DIR` locally, `BRIDGE_EMAIL_BUCKET` on gcp). Without a mailbox or a landing location no processor binds (mailbox + secret alone only shows reply addresses); with both, a missing token secret fails the host at build. That host serves `POST /email/drain` and `GET /email/outcomes/{key}` (`mount_email_drain_routes`) and its lifespan drains every `BRIDGE_EMAIL_DRAIN_INTERVAL` seconds (`email_drain_loop`; default 30, `0` = on demand only, malformed = fail at build). Because Starlette runs no mounted sub-app's lifespan, a host that mounts the dashboard runs the loop in its own lifespan — exactly one loop per process. `drain` holds a lock, so the loop and a `POST /email/drain` serialize. The reply addresses that host shows come from the processor itself, so what a carrier is told is what correlates.

A processor binds only over the board that is the source of truth — a durable shared exchange store every RFP writer uses, or a host that opens its RFPs onto that board. A board no RFP opens onto would correlate every landed mail to nothing and permanently mark it processed `no_exchange_token`, destroying the queue; so the RFP board host refuses to bind a processor over an in-memory store. For the same reason `python -m bridge.email_ingress drain --url <host>` POSTs to the served host's `/email/drain` and never drains in-process. `build_email_app(processor=…)` can also host the webhook and a processor in one app, adding a background drain after each landing. `python -m bridge.email_ingress land reply.eml` is the local analogue of the Graph transport.

## Create-only landing is the idempotency and the queue

The landing key is `sha256("message-id:" + normalized Message-ID)[:32]`, or `sha256(raw)[:32]` when the header is absent. Every write is **create-only**:

- Local: write a temp file, then `os.link` it into place.
- GCS: `if_generation_match=0`. A `412` means the object already exists, so it is a **duplicate**. A `403` is a duplicate only if the object exists; otherwise it is re-raised, because an objectCreator-only identity gets 403, not 412, when it tries to overwrite.

| Object | Meaning |
|---|---|
| `inbox/<key>.eml` | the raw message, as landed |
| `attachments/<key>/<i>/<filename>` | attachment `i` (directory per index: two parts may share a filename) |
| `processed/<key>.json` | the outcome marker; its presence removes the key from `pending()` |

`pending()` is `inbox` minus `processed`, in **landing order**. A Graph re-delivery, a resend, or a second `land` of the same `.eml` is a no-op. The message **body is never persisted** beyond the raw `.eml` that the lifecycle rule expires.

## Retry order — at-least-once, never a torn board

1. **Extract every attachment first. The board is not touched.** A deterministic refusal (`ValueError` / `KeyError`: an unreadable document, schema-retry exhaustion, a fixture miss) marks that one attachment unreadable. Any **other** exception, such as a transient 429 or 500, propagates. The drain stops there with no marker written, and the next drain retries the key, which keeps landing order.
2. **Persist the bytes** leg-scoped in the artifact store.
3. **Apply to a fresh read** of the RFP (`acknowledge`, `receive`, `note_unreadable`), then `board.put`. There is **no `await`** between the read and the put.
4. **Write the processed marker.**

Step 3 is a compare-and-set on the exchange store. A concurrent writer (another replica's drain, a chase, a portal upload) makes the put stale; the processor re-reads and re-applies, up to three attempts. An exhausted retry propagates with no marker written, so the mail stays pending and the next drain retries it (served: a 409 the next Scheduler tick absorbs).

A crash between steps 3 and 4 leaves the entries on the RFP without a marker. The next drain finds its own `doc_id`s (`email-<key>-<i>`) already on the RFP and writes the marker only, so a re-drain never files a second version. A mail with no extracted attachment (unreadable-only) re-applied may append one duplicate audit-only event; no cell moves. A retry after a crash in step 2 or 3 may add one extra artifact version; two replicas draining the same key at the same instant may each extract it before one wins the put.

## Quarantine

Quarantine never touches the board. The reason is written to the processed marker (`GET /email/outcomes/{key}` on the board-owning host) and logged at WARNING, so nothing is dropped silently:

| Reason | When |
|---|---|
| parser reason (`oversize`, `too_many_attachments`, `unparseable…`) | the message exceeds `BRIDGE_EMAIL_MAX_BYTES` (default 25 MiB) or 20 attachments, or is not MIME |
| `oversize` (Graph id marker) | Graph's message is over the cap before landing; recorded under `sha256("graph:"+id)[:32]` so Graph is not asked to retry. Oversize is the only fetch failure acknowledged; any other (a Graph error, a garbled token response, a storage error) answers 503, so no mail is acknowledged without landing |
| `sender_authentication_failed` | an `Authentication-Results` header says `dmarc=fail` |
| `no_exchange_token` | no token matches an open leg |
| `ambiguous_token` | tokens name two different legs |

A mail with a valid token but **no attachments** is filed (the carrier is acknowledged) with reason `no_attachments`.

## Unreadable attachments — no fabricated verdict

An attachment whose media type no engine reads (for example `rates.xlsx`), or one the engine deterministically refuses, gets `rfp.note_unreadable`. That is an **audit-only** event, `attachment_unreadable`, with detail `<filename>: <reason>`. The cell does not move, and no disposition or gate is invented. The [disposition gate](collect.md) remains the sole verdict authority. Inline images (`Content-Disposition: inline`, or a `Content-ID` part with no disposition) are signature furniture: they are counted (`skipped_inline`), not filed. A forwarded `message/rfc822` is opened one level deep. A deeper forward is kept as an opaque `.eml` attachment.

## The seam

| Seam | Local adapter | GCP adapter |
|---|---|---|
| Email ingress | a landing directory (`BRIDGE_EMAIL_DIR`) of `.eml` + attachments + processed markers | a GCS landing bucket (`BRIDGE_EMAIL_BUCKET`), same object layout, create-only writes |

Graph is the deployed **transport** that feeds `land()`, not part of the ABC (`EmailIngressSeam`). Parity is the terminal outcome: land/duplicate, pending order, round-tripped bytes. See [seams](seams.md).

## Deployed topology

Three Cloud Run services on one image:

- **The webhook** (`<service>-email-webhook`) is public and webhook-only. It runs `serve_email_webhook_container()` as the `bridge-email-webhook` SA, whose only grants are `secretAccessor` on the Graph client secret and the clientState, plus `roles/storage.objectCreator` on the email bucket. It holds no project roles and no board, DB, or Vertex access.
  - **`allUsers` may invoke it.** This is a deliberate trust-boundary exception: Graph cannot present a Google identity. Each notification is authenticated by its `clientState` (constant-time, before any Graph call), checked against `tenantId` when the tenant is a GUID, and capped at 256 KiB. The webhook serves no marker reads: `GET /email/outcomes/{key}` and `POST /email/drain` exist only on the host that binds a processor.
  - An org policy with **Domain-Restricted Sharing** rejects `allUsers`. The operator then needs a project-level exception or an external LB / Gateway in front of the service.
- **The A2A runtime** (`<service>`) is the deployed **RFP opener**. With `enable_rfp_board` it carries the board's `BRIDGE_RFP_PROCESS`, and `build_a2a_app` binds an opener-only `RfpBoard` (`bridge/edge/rfp_open.py::rfp_board_from_env`) over the same durable exchange store the board host reads. A broker agent's `CollectRequest` for that process, or for an emergent-Collect process installed live, opens the RFP, keyed `rfp_id` = the A2A exchange context, or updates its requirements on a follow-up; the board host then drains replies onto it. The opener requires a durable store: over an in-memory store the app build raises `RuntimeError`, because the board host would never see the RFP. It binds no processor, serves no read API and carries no `BRIDGE_EMAIL_*` env, since reply addresses are minted on the board host from `rfp_id` + party. See [A2A edge](a2a-edge.md) R2.
- **The RFP board host** (`<service>-rfp-board`, gated on `enable_rfp_board`) owns the board and binds the `EmailProcessor`. It runs `serve_rfp_board_container()` (`bridge.dashboard.board_host.build_rfp_board_app`): one `RfpBoard` whose home process skill is named by `BRIDGE_RFP_PROCESS` and which also serves any emergent-Collect process skill installed live in the registry (each RFP filed with its own process's label space and item key; a mail for an RFP whose process no longer resolves is quarantined `process_unavailable` before extraction), over the durable exchange store (Cloud SQL via the `DATABASE_URL` secret env), with the Ops Dashboard app mounted at the root — the consumer RFP read API, `POST /email/drain` and `GET /email/outcomes/{key}`. It seeds nothing; it serves whatever RFPs the shared store holds. Its lifespan closes the store on shutdown.
  - It carries the a2a_client gcp env (`BRIDGE_RUNTIME_SA_EMAIL`, `BRIDGE_GATEWAY_HOST` when `gateway_host` is set), so its manual-chase route can dial carriers.
  - It runs as `bridge-runtime`, which reads the bucket (`objectViewer` + `objectCreator`, never `objectAdmin`) and holds the token key (`BRIDGE_EMAIL_TOKEN_SECRET` as a secret env). It never holds the Graph credentials.
  - Ingress is internal-only (`rfp_board_ingress`), and `roles/run.invoker` is granted to `bridge-runtime` alone. The outcome markers and the read API are operator data (internal zone), so the board is not mounted on the Gateway-public A2A edge.
  - **Cloud Scheduler drives the drain.** A job (`<service>-email-drain`, gated on `enable_rfp_board` and `enable_email_ingress`) POSTs `/email/drain` every minute (`email_drain_schedule`) with an OIDC token for `bridge-runtime`, retry count 0 (the next tick is the retry). The target is the board's deterministic URL `https://<service>-rfp-board-<project-number>.<region>.run.app`. The host sets `BRIDGE_EMAIL_DRAIN_INTERVAL=0`: request-based Cloud Run CPU starves a background loop between requests. Drains on one instance serialize on the processor lock, and `rfp_board_max_instances` defaults to 1; across replicas the compare-and-set retry and the re-file guard keep a drain safe.
  - If Scheduler's calls are refused by internal-only ingress in a given project, set `rfp_board_ingress = "INGRESS_TRAFFIC_ALL"`; the invoker grant still gates every request.
- The email bucket is UBLA, public access prevention is enforced, and a lifecycle rule deletes objects after `email_retention_days` (default 30). Retention is the lifecycle policy, never Bridge GC.

Terraform gates the webhook, bucket and secrets on `enable_email_ingress` and the board host on `enable_rfp_board` (both default `false`); a root `check` warns when email intake is enabled without a board host to drain it. `scripts/deploy.sh` enables `cloudscheduler.googleapis.com`. The three secret values are never in HCL ([gcp substrate](gcp-substrate.md)).

## Provisioning

1. Register the app in Entra ID with the Graph **application** permission `Mail.Read`, and grant admin consent. Record the tenant id and client id (`email_graph_tenant_id`, `email_graph_client_id`; identifiers, not secrets).
2. Scope the app to the intake mailbox:
   ```powershell
   New-ApplicationAccessPolicy -AppId <client-id> -PolicyScopeGroupId <mail-enabled security group containing rfp-quotes@> -AccessRight RestrictAccess -Description "Bridge email intake"
   Test-ApplicationAccessPolicy -AppId <client-id> -Identity rfp-quotes@<domain>   # Granted
   Test-ApplicationAccessPolicy -AppId <client-id> -Identity <a broker mailbox>     # Denied
   ```
3. `terraform apply -var enable_email_ingress=true -var email_mailbox=rfp-quotes@<domain> …`, then add the secret values out-of-band:
   ```sh
   gcloud secrets versions add bridge-email-graph-client-secret --data-file=-
   gcloud secrets versions add bridge-email-client-state --data-file=-   # random, ≤128 chars
   gcloud secrets versions add bridge-email-token-secret --data-file=-   # random HMAC key
   ```
4. Subscribe Graph to the mailbox's Inbox (`changeType=created`):
   ```sh
   uv run python -m bridge.email_ingress subscribe --notification-url https://<email_webhook_uri>/email/graph/notifications
   ```
   Graph's handshake (`?validationToken=…`) is echoed as `text/plain`.

**Renewal.** A mail-resource subscription lives at most 4230 minutes (under three days). Renew before expiry with `python -m bridge.email_ingress renew <subscription-id>`. `subscribe` and `renew` default to the maximum.

## Local development

```sh
export BRIDGE_EMAIL_DIR=$(mktemp -d) BRIDGE_EMAIL_MAILBOX=rfp-quotes@broker.example BRIDGE_EMAIL_TOKEN_SECRET=dev-only BRIDGE_EMAIL_DRAIN_INTERVAL=0
uv run uvicorn --factory bridge.demos.rfp_demo:build_rfp_demo_app --port 8010 &
curl -s localhost:8010/ops/rfps/acme-vb-2027                  # each carrier's reply_address
uv run python -m bridge.email_ingress land reply.eml          # → landed <key>   (again → duplicate <key>)
uv run python -m bridge.email_ingress drain --url http://127.0.0.1:8010/ops
#   → filed <key> acme-vb-2027 <party> filed=<n> unreadable=<m>  (or quarantined <key> <reason>), then drained <count>
curl -s localhost:8010/ops/email/outcomes/<key>               # the processed marker
```

The demo host reads mailed attachments with its fixture engine (the benefits conformance PDFs). With `BRIDGE_EMAIL_DRAIN_INTERVAL` unset (or any positive value) the host drains on its own; `drain` takes `--url` or `BRIDGE_EMAIL_DRAIN_URL` and exits 1 on an HTTP or connection failure.

The landed attachments are `file://` URIs. With `BRIDGE_EXTRACTION_ENGINE=gemini`, also set `BRIDGE_ALLOW_FILE_URI=1` so the engine may read them.
