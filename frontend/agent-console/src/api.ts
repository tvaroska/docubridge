// HTTP + SSE client for the Agent-Console host (P6-3).
//
// The host is the internal-zone App/Runner host; this app talks plain REST to it for the
// prepared-request catalog + start + session list + snapshot, and subscribes to the live
// SSE channel (`EventSource`). Every response is mapped snake->camel. Base URL from the
// Vite env, defaulting to the local host port (`agents.console.server.serve` -> :8002).

import {
  mapSnapshot,
  type Prepared,
  type Snapshot,
  type WireSnapshot,
} from "./domain/console";

const BASE_URL: string =
  (import.meta.env.VITE_CONSOLE_URL as string | undefined) ?? "http://localhost:8002";

export async function getPrepared(): Promise<Prepared[]> {
  const resp = await fetch(`${BASE_URL}/prepared`);
  if (!resp.ok) throw new Error(`console /prepared failed: ${resp.status}`);
  return (await resp.json()) as Prepared[];
}

export async function startSession(preparedKey: string): Promise<Snapshot> {
  const resp = await fetch(`${BASE_URL}/sessions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prepared_key: preparedKey }),
  });
  if (!resp.ok) throw new Error(`console POST /sessions failed: ${resp.status}`);
  return mapSnapshot((await resp.json()) as WireSnapshot);
}

export async function listSessions(): Promise<Snapshot[]> {
  const resp = await fetch(`${BASE_URL}/sessions`);
  if (!resp.ok) throw new Error(`console /sessions failed: ${resp.status}`);
  return ((await resp.json()) as WireSnapshot[]).map(mapSnapshot);
}

export async function getSnapshot(sessionId: string): Promise<Snapshot> {
  const resp = await fetch(`${BASE_URL}/sessions/${sessionId}`);
  if (!resp.ok) throw new Error(`console /sessions/${sessionId} failed: ${resp.status}`);
  return mapSnapshot((await resp.json()) as WireSnapshot);
}

/**
 * Subscribe to the live SSE channel for a session. Each `data:` frame is a snapshot;
 * `onSnapshot` receives the mapped (camelCase) snapshot. Returns an unsubscribe that
 * closes the underlying `EventSource`.
 */
export function subscribeEvents(
  sessionId: string,
  onSnapshot: (snap: Snapshot) => void,
): () => void {
  const source = new EventSource(`${BASE_URL}/sessions/${sessionId}/events`);
  source.onmessage = (e: MessageEvent) => {
    onSnapshot(mapSnapshot(JSON.parse(e.data) as WireSnapshot));
  };
  return () => source.close();
}
