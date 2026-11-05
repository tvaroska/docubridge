# Local dev — running the stack

How to bring the Bridge up on your machine. Requirements live in [`spec/`](../spec/cujs.md),
design in [`design/`](../design/bridge.md); this page is ops only.

## One command

```bash
scripts/run-all.sh          # five Python hosts + four Vite apps; Ctrl-C tears it down
```

Or a single surface at a time — each `scripts/run-<surface>.sh` starts just that host,
and `pnpm --filter <app> dev` starts just that app.

A local run is credential-free: every port resolves to its local adapter and extraction
is the deterministic fixture engine over `wiki/evals/`. No GCP, no Gemini, no billing.

## Port map

| Port | Host | Entry point | App | Vite port |
|------|------|-------------|-----|-----------|
| 8000 | Bridge core A2A edge | `bridge.edge.server:build_a2a_app` | — | — |
| 8001 | Provider Portal (A2UI Path-B, external zone) | `bridge.portal.server:build_portal_app` | `provider-portal` | 5173 |
| 8001 | *(alt)* mock Bridge edge — scripted executor | `bridge.edge.mock:serve_mock` | — | — |
| 8002 | Agent Console (SSE, internal zone) | `agents.console.server:build_console_app` | `agent-console` | 5174 |
| 8003 | Ops Dashboard (Bridge-side read model, SSE) | `bridge.dashboard.server:build_dashboard_app` | `ops-dashboard` | 5175 |
| 8004 | Time-warp host (plain REST, deliberately no SSE) | `bridge.timewarp.server:build_timewarp_app` | `timewarp` | 5176 |
| 8010 | RFP demo host (seeded RFP board: Ops dashboard at `/ops`, carrier checklist at `/portal`) | `bridge.demos.rfp_demo:build_rfp_demo_app` | `ops-dashboard` / `provider-portal` via `VITE_DASHBOARD_URL` / `VITE_PORTAL_URL` | 5173 / 5174 |
| 8011 | Showcase runner (one core: A2A edge at `/`, Ops dashboard at `/ops`, portal at `/portal`) | `bridge.demos.showcase:build_showcase_app` | `ops-dashboard` / `provider-portal` via `VITE_DASHBOARD_URL` / `VITE_PORTAL_URL` | 5175 / 5173 |
| 8100+ | Benefits carrier agents (real A2A servers, per-carrier) | `agents.benefits.server:serve_carrier` | — | — |

Every port is override-able: `CORE_PORT`, `PORTAL_PORT`, `MOCK_PORT`, `CONSOLE_PORT`,
`OPS_PORT`, `TIMEWARP_PORT`. The RFP demo host takes uvicorn's `--port`;
the showcase runner takes `SHOWCASE_HOST` / `SHOWCASE_PORT`.

## RFP demo host

One host serves a seeded RFP board behind both views, off one shared board: the Ops
dashboard read model at `/ops` (`/exchanges` + SSE, the consumer read API `/rfps` /
`/rfps/{id}` and the manual chase `POST /rfps/{id}/chase`) and the carrier checklist at
`/portal` (`/rfp/legs/{context_id}`). Recipe (from the `bridge/demos/rfp_demo.py`
docstring):

```bash
uv run uvicorn --factory bridge.demos.rfp_demo:build_rfp_demo_app --port 8010
VITE_DASHBOARD_URL=http://localhost:8010/ops pnpm --filter ops-dashboard dev
VITE_PORTAL_URL=http://localhost:8010/portal pnpm --filter provider-portal dev -- --port 5174
```

- **It is an alternative to `run-all.sh`, not a companion.** Its two Vite apps take 5173
  and 5174, which `run-all.sh` already gives the Provider Portal and the Agent Console.
- **What it seeds:** RFP `acme-vb-2027` (process `benefits-rfp`), carriers `carrier-a` /
  `carrier-b` / `carrier-c`, with carrier-a's quotes uploaded through the real checklist
  path from the Benefits conformance corpus (`wiki/evals/benefits/conformance.json`,
  read-only). Synthetic data only.
- **Persistence:** unset, the RFP lives in memory and is re-seeded on every start.
  `export BRIDGE_EXCHANGE_DB_URL=sqlite+aiosqlite:////tmp/rfp-demo.db` keeps the RFP
  (uploads, chases, the audit trail) across restarts; the seed runs only when the store
  does not already hold `acme-vb-2027`.
- **Email:** set `BRIDGE_EMAIL_MAILBOX` + `BRIDGE_EMAIL_TOKEN_SECRET` to show each leg's
  reply address. Add a landing directory (`BRIDGE_EMAIL_DIR`) and the same board also
  binds the email processor: `/ops/email/drain` + `/ops/email/outcomes/{key}`, plus a
  periodic drain every `BRIDGE_EMAIL_DRAIN_INTERVAL` seconds (default 30; `0` = only on
  demand).

## Showcase runner

One process boots the core with every demo loaded: one Agent Card, one dashboard, shared
stores. `scripts/run-showcase.sh` serves `bridge.demos.showcase:build_showcase_app` on
:8011 and mounts:

- `/` — the A2A edge. The card (`/.well-known/agent-card.json`) lists `address-proof`,
  `benefits-procurement` and `benefits-rfp`; JSON-RPC is at `/`.
- `/ops` — the Ops dashboard: Address exchanges on `/exchanges`, the Benefits program
  on `/programs`, the RFP on `/rfps`.
- `/portal` — the Provider portal (Address sessions + the carrier RFP checklist).

```bash
scripts/run-showcase.sh                    # core on :8011 + ops-dashboard :5175 + provider-portal :5173
SHOWCASE_APPS=0 scripts/run-showcase.sh    # the core only
```

- **It is an alternative to `run-all.sh` and the RFP demo host, not a companion.** Its
  Vite apps take 5173 and 5175, which `run-all.sh` already uses.
- **Shared stores:** one PortalStore (dashboard + portal), one RFP board (edge +
  dashboard + portal) and one program board (dashboard). An RFP opened over A2A
  (`CollectRequest(process="benefits-rfp")`) appears on `/ops/rfps`; a portal upload
  appears on `/ops/exchanges`.
- **What it seeds:** two Address portal sessions (`bill-powerco-clean` accepted,
  `bill-aquautil-clear` parked for HITL review), the Benefits program `acme-2026`
  (four carriers; carrier-b routes to `negotiate`) and the RFP `acme-vb-2027` (as the
  RFP demo host). Synthetic data only.
- **Scope:** `/exchanges` joins portal sessions and RFPs; programs are on `/programs`.
  Address exchanges driven over A2A live in the edge task store, not on `/exchanges`.
  `BRIDGE_EXCHANGE_DB_URL` and the `BRIDGE_EMAIL_*` variables behave as on the RFP demo
  host.

## Email intake CLI

`uv run python -m bridge.email_ingress {land,subscribe,renew,drain}` is the operator CLI
for the email intake. The local loop against the RFP demo host:

```bash
export BRIDGE_EMAIL_DIR=$(mktemp -d) BRIDGE_EMAIL_MAILBOX=rfp-quotes@broker.example \
       BRIDGE_EMAIL_TOKEN_SECRET=dev-only BRIDGE_EMAIL_DRAIN_INTERVAL=0
uv run uvicorn --factory bridge.demos.rfp_demo:build_rfp_demo_app --port 8010
# mail a reply to a leg's reply_address (GET /ops/rfps/acme-vb-2027), save it as .eml
uv run python -m bridge.email_ingress land reply.eml
uv run python -m bridge.email_ingress drain --url http://127.0.0.1:8010/ops
```

- `land <file.eml>` lands one raw message on the `email_ingress` port (`BRIDGE_EMAIL_DIR`
  locally; `BRIDGE_PORT_EMAIL_INGRESS=gcp` + `BRIDGE_EMAIL_BUCKET` lands in the deployed
  bucket).
- `drain [--url URL] [--timeout S]` asks the board-owning host to drain
  (`POST <url>/email/drain`; `--url` defaults to `BRIDGE_EMAIL_DRAIN_URL`). It never
  drains in-process: a processor built in the CLI would correlate against an empty board
  and permanently quarantine every mail as `no_exchange_token`.
- `subscribe --notification-url URL` / `renew <subscription-id>` manage the Microsoft
  Graph change-notification subscription. They need `BRIDGE_EMAIL_GRAPH_TENANT`,
  `BRIDGE_EMAIL_GRAPH_CLIENT_ID`, `BRIDGE_EMAIL_GRAPH_CLIENT_SECRET`,
  `BRIDGE_EMAIL_MAILBOX` and `BRIDGE_EMAIL_CLIENT_STATE`.
- `BRIDGE_EMAIL_TOKEN_SECRET`, `BRIDGE_EMAIL_GRAPH_CLIENT_SECRET` and
  `BRIDGE_EMAIL_CLIENT_STATE` are secrets: export them in your shell or a gitignored
  `.env`, never commit a value.

## Things that will bite you

- **8001 is double-booked.** The portal and the mock edge both default to it, and they
  are alternatives, not companions: `run-all.sh` starts the *real* edge (8000) + the
  portal (8001). To drive the mock instead, run `run-mock.sh` without the portal and
  point the console at it with `BRIDGE_CARD_URL`.
- **Each host builds its own in-memory store.** A session started in the portal is not
  visible to a separately launched dashboard or time-warp host. One shared store across
  the three is a wiring choice (`store=` injection), which only the tests and a wired
  deployment do today.
- **CORS is a fixed allowlist:** `localhost:5173`–`localhost:5177`. That is why the run
  scripts pin each Vite port with `--strictPort` — an app on a different port is
  blocked by the browser, not by the host.
- **No `--reload`.** The hosts hold live state in memory; a reload drops every session.
- **The console is a consumer, not a surface of the Bridge.** It dials a card URL over
  A2A, so the edge (or mock) has to be up first.

## Environment

[`.env.example`](../.env.example) is the canonical index of every variable the code
reads, with defaults and which ones the deployed runtime injects. Nothing loads it
automatically — copy what you need into a gitignored `.env` or export it.
Shared local defaults for the run scripts live in `scripts/_dev-env.sh`.
The content roots a downstream showcase swaps (`BRIDGE_SKILLS_DIR`,
`BRIDGE_OKF_DOCTYPES_DIR`, `BRIDGE_SCENARIOS_DIR`, `BRIDGE_DEMO_STATIC_DIR`,
`BRIDGE_DEFAULT_DEMO_DOMAIN`) are described in [`consume-over-a2a.md`](consume-over-a2a.md).

## Deployed path

`scripts/deploy.sh` (build + two-phase Terraform apply) and `scripts/parity.sh` (the
deployed-path gate) are the GCP side; both need live auth and a provisioned project.
The deployed RFP board host is `bridge.dashboard.board_host` (internal-zone
`<service>-rfp-board`).
See [`docs/features/gcp-deployment.md`](features/gcp-deployment.md).
