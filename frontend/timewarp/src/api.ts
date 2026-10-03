// Time-warp REST client (presenter stage, P6-5, §2h).
//
// Plain REST against the local-only time-warp host (`bridge/timewarp/server.py`, :8004).
// Every clock move is an EXPLICIT call — deterministic stage time (no auto-run, no wall
// clock). Each response is mapped snake→camel via the domain mapper. Base URL from the Vite
// env, defaulting to the local host port. Mirrors `provider-portal/src/api.ts`.

import { mapState, type TimewarpState, type WireState } from "./domain/timewarp";

const BASE_URL: string =
  (import.meta.env.VITE_TIMEWARP_URL as string | undefined) ?? "http://localhost:8004";

async function postJson(path: string, body?: unknown): Promise<TimewarpState> {
  const resp = await fetch(`${BASE_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!resp.ok) throw new Error(`timewarp ${path} failed: ${resp.status}`);
  return mapState((await resp.json()) as WireState);
}

export async function getState(): Promise<TimewarpState> {
  const resp = await fetch(`${BASE_URL}/timewarp/state`);
  if (!resp.ok) throw new Error(`timewarp /timewarp/state failed: ${resp.status}`);
  return mapState((await resp.json()) as WireState);
}

export function arm(sessionId: string): Promise<TimewarpState> {
  return postJson("/timewarp/arm", { session_id: sessionId });
}

/** Fast-forward by `ticks` (default 1), or scrub to an absolute tick with `{ to }`. */
export function advance(ticks: number): Promise<TimewarpState> {
  return postJson("/timewarp/advance", { ticks });
}

export function advanceTo(to: number): Promise<TimewarpState> {
  return postJson("/timewarp/advance", { to });
}

export function step(): Promise<TimewarpState> {
  return postJson("/timewarp/step");
}

export function pause(): Promise<TimewarpState> {
  return postJson("/timewarp/pause");
}

export function resume(): Promise<TimewarpState> {
  return postJson("/timewarp/resume");
}

export function reset(): Promise<TimewarpState> {
  return postJson("/timewarp/reset");
}
