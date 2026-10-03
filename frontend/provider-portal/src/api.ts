// HTTP client for the Provider-Portal host (P6-2).
//
// The host is the external-zone A2UI Path-B edge; this app talks plain REST to it and
// maps every response through `mapView` (snake->camel). Base URL from the Vite env,
// defaulting to the local host port (`bridge.portal.server.serve` -> :8001).

import { mapView, type A2uiView, type WireView } from "./domain/a2ui";
import { mapRfpChecklist, type RfpChecklist, type WireRfpChecklist } from "./domain/rfp";

const BASE_URL: string =
  (import.meta.env.VITE_PORTAL_URL as string | undefined) ?? "http://localhost:8001";

interface StartResponse {
  session_id: string;
  view: WireView;
}

export interface Session {
  sessionId: string;
  view: A2uiView;
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const resp = await fetch(`${BASE_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!resp.ok) {
    const detail = await resp.text().catch(() => "");
    throw new Error(`portal ${path} failed: ${resp.status}${detail ? ` ${detail}` : ""}`);
  }
  return (await resp.json()) as T;
}

export async function startSession(process: string): Promise<Session> {
  const data = await postJson<StartResponse>("/sessions", { process });
  return { sessionId: data.session_id, view: mapView(data.view) };
}

export async function getView(sessionId: string): Promise<A2uiView> {
  const resp = await fetch(`${BASE_URL}/sessions/${sessionId}/view`);
  if (!resp.ok) throw new Error(`portal view failed: ${resp.status}`);
  return mapView((await resp.json()) as WireView);
}

export async function upload(
  sessionId: string,
  docId: string,
  artifactName: string,
): Promise<A2uiView> {
  const wire = await postJson<WireView>(`/sessions/${sessionId}/uploads`, {
    doc_id: docId,
    artifact_name: artifactName,
  });
  return mapView(wire);
}

export async function resolveHitl(
  sessionId: string,
  resolution: "accepted" | "rejected",
  message: string,
): Promise<A2uiView> {
  const wire = await postJson<WireView>(`/sessions/${sessionId}/hitl`, {
    resolution,
    message,
  });
  return mapView(wire);
}

// --- RFP carrier checklist (S0-frontend-1) — addressed by the leg context id ---

export async function getRfpChecklist(contextId: string): Promise<RfpChecklist> {
  const resp = await fetch(`${BASE_URL}/rfp/legs/${encodeURIComponent(contextId)}`);
  if (!resp.ok) throw new Error(`portal RFP checklist failed: ${resp.status}`);
  return mapRfpChecklist((await resp.json()) as WireRfpChecklist);
}

export async function uploadRfpQuote(
  contextId: string,
  docId: string,
  artifactName: string,
  productLine: string,
): Promise<RfpChecklist> {
  const wire = await postJson<WireRfpChecklist>(
    `/rfp/legs/${encodeURIComponent(contextId)}/uploads`,
    { doc_id: docId, artifact_name: artifactName, product_line: productLine },
  );
  return mapRfpChecklist(wire);
}
