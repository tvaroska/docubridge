// HTTP + SSE client for the Ops Dashboard host (P6-4).
//
// The host is the internal-zone Bridge-side read-model host; this app talks plain REST to
// it for the exchange list + per-exchange ledger + queues + resolve, and subscribes to the
// live SSE channel (`EventSource`). Every response is mapped snake->camel. Base URL from
// the Vite env, defaulting to the local host port (`bridge.dashboard.server.serve` ->
// :8003).

import {
  mapExchange,
  mapSnapshot,
  type OpsExchange,
  type Snapshot,
  type WireExchange,
  type WireSnapshot,
} from "./domain/ops";

const BASE_URL: string =
  (import.meta.env.VITE_DASHBOARD_URL as string | undefined) ?? "http://localhost:8003";

export async function getExchanges(): Promise<OpsExchange[]> {
  const resp = await fetch(`${BASE_URL}/exchanges`);
  if (!resp.ok) throw new Error(`dashboard /exchanges failed: ${resp.status}`);
  return ((await resp.json()) as WireExchange[]).map(mapExchange);
}

export async function getExchange(sessionId: string): Promise<OpsExchange> {
  const resp = await fetch(`${BASE_URL}/exchanges/${sessionId}`);
  if (!resp.ok) throw new Error(`dashboard /exchanges/${sessionId} failed: ${resp.status}`);
  return mapExchange((await resp.json()) as WireExchange);
}

export async function getHitlQueue(): Promise<OpsExchange[]> {
  const resp = await fetch(`${BASE_URL}/queues/hitl`);
  if (!resp.ok) throw new Error(`dashboard /queues/hitl failed: ${resp.status}`);
  return ((await resp.json()) as WireExchange[]).map(mapExchange);
}

export async function getEscalationQueue(): Promise<OpsExchange[]> {
  const resp = await fetch(`${BASE_URL}/queues/escalation`);
  if (!resp.ok) throw new Error(`dashboard /queues/escalation failed: ${resp.status}`);
  return ((await resp.json()) as WireExchange[]).map(mapExchange);
}

export async function resolveExchange(
  sessionId: string,
  resolution: "accepted" | "rejected",
  message: string,
): Promise<OpsExchange> {
  const resp = await fetch(`${BASE_URL}/exchanges/${sessionId}/resolve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ resolution, message }),
  });
  if (!resp.ok) throw new Error(`dashboard POST /exchanges/${sessionId}/resolve failed: ${resp.status}`);
  return mapExchange((await resp.json()) as WireExchange);
}

export interface ChaseResult {
  chased: { party: string; step: string; idempotencyKey: string; resent: boolean }[];
}

/**
 * The operator's manual "chase now" (S0-frontend-1): `POST /rfps/{id}/chase` — one carrier
 * (`party`) or every carrier with something outstanding (`null`). The SSE stream refreshes
 * the matrix; the caller only surfaces the outcome / error.
 */
export async function chaseRfp(rfpId: string, party: string | null): Promise<ChaseResult> {
  const resp = await fetch(`${BASE_URL}/rfps/${encodeURIComponent(rfpId)}/chase`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ party }),
  });
  if (!resp.ok) {
    const detail = await resp.text();
    throw new Error(`dashboard POST /rfps/${rfpId}/chase failed: ${resp.status} ${detail}`);
  }
  const body = (await resp.json()) as {
    chased: { party: string; step: string; idempotency_key: string; resent: boolean }[];
  };
  return {
    chased: body.chased.map((c) => ({
      party: c.party,
      step: c.step,
      idempotencyKey: c.idempotency_key,
      resent: c.resent,
    })),
  };
}

/**
 * Subscribe to the live SSE channel for the whole cross-exchange read-model. Each `data:`
 * frame is a snapshot; `onSnapshot` receives the mapped (camelCase) snapshot. Returns an
 * unsubscribe that closes the underlying `EventSource`.
 */
export function subscribeEvents(onSnapshot: (snap: Snapshot) => void): () => void {
  const source = new EventSource(`${BASE_URL}/events`);
  source.onmessage = (e: MessageEvent) => {
    onSnapshot(mapSnapshot(JSON.parse(e.data) as WireSnapshot));
  };
  return () => source.close();
}
