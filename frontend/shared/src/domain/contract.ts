// Shared contract mirror (@docbride/shared, P6-5) — the snake_case→camelCase domain core.
//
// The three frontend apps (provider-portal, agent-console, ops-dashboard) each hand-mapped
// the same contract core (LedgerEntry / Requirement + their wire shapes + snake→camel
// mappers). P6-5 hoists that duplicated core HERE, once. Each app re-exports these types and
// composes them into its OWN app-specific view types (A2uiView / Snapshot / OpsExchange) +
// view mappers that call the shared mapEntry / mapRequirement.
//
// SUPERSET WITH OPTIONAL FIELDS (§4.2, gotcha #7): the three domains disagree on which
// fields exist and on nullability (portal's item has no gate/issuer; dashboard's entry has
// non-null reason/message; console's are nullable). The shared mirror is the UNION with
// optional fields and the mappers keep the `?? null` behavior; each app narrows as needed.
//
// CONTENT-NOT-PIXELS (A2UI-1): the mappers RELAY `reasonCode`/`message` verbatim — they
// never author, rewrite, or summarize the agent's explanation. Same invariant the three
// apps held before the hoist.

// --------------------------------------------------------------------------- //
// Enums (as string unions — the wire values)                                  //
// --------------------------------------------------------------------------- //
export type Disposition = "accepted" | "rejected" | "pending";
export type RequirementStatus = "required" | "optional" | "satisfied" | "waived";
export type LifecycleStatus = "on_track" | "overdue" | "reminder" | "escalated";
// One RFP item's effective status (S0-frontend-1; `overdue` is derived on read).
export type RfpItemStatus =
  | "invited"
  | "acknowledged"
  | "received"
  | "incomplete"
  | "declined"
  | "overdue";

// --------------------------------------------------------------------------- //
// Camel domain types (the superset — apps narrow via their own view types)     //
// --------------------------------------------------------------------------- //
export interface LedgerEntry {
  docId: string;
  doctype: string;
  disposition: Disposition;
  gate?: string | null;
  issuer?: string | null;
  doctypeHint?: string | null;
  // Relayed verbatim from the graph — never rewritten by the frontend.
  reasonCode: string | null;
  message: string | null;
}

export interface Requirement {
  item: string;
  status: RequirementStatus;
  doctypeHint: string | null;
  // Relayed verbatim.
  reasonCode: string | null;
  message: string | null;
}

// One field that requires review (the gate's resubmit reason, relayed verbatim).
export interface FieldReview {
  path: string;
  reason: string;
  confidence: number | null;
  detail: string | null;
}

// --------------------------------------------------------------------------- //
// Wire shapes (snake_case — the superset of the three apps' wire entries)      //
// --------------------------------------------------------------------------- //
export interface WireLedgerEntry {
  doc_id: string;
  doctype: string;
  disposition: Disposition;
  gate?: string | null;
  issuer?: string | null;
  doctype_hint?: string | null;
  reason_code?: string | null;
  message?: string | null;
}

export interface WireRequirement {
  item: string;
  status: RequirementStatus;
  doctype_hint?: string | null;
  reason_code?: string | null;
  message?: string | null;
}

export interface WireFieldReview {
  path: string;
  reason: string;
  confidence?: number | null;
  detail?: string | null;
}

// --------------------------------------------------------------------------- //
// Mappers (snake→camel; relay reasonCode/message verbatim)                     //
// --------------------------------------------------------------------------- //
export function mapEntry(w: WireLedgerEntry): LedgerEntry {
  return {
    docId: w.doc_id,
    doctype: w.doctype,
    disposition: w.disposition,
    gate: w.gate ?? null,
    issuer: w.issuer ?? null,
    doctypeHint: w.doctype_hint ?? null,
    reasonCode: w.reason_code ?? null, // verbatim (content-not-pixels)
    message: w.message ?? null, // verbatim (content-not-pixels)
  };
}

export function mapRequirement(w: WireRequirement): Requirement {
  return {
    item: w.item,
    status: w.status,
    doctypeHint: w.doctype_hint ?? null,
    reasonCode: w.reason_code ?? null, // verbatim (content-not-pixels)
    message: w.message ?? null, // verbatim (content-not-pixels)
  };
}

export function mapFieldReview(w: WireFieldReview): FieldReview {
  return {
    path: w.path, // verbatim (content-not-pixels)
    reason: w.reason,
    confidence: w.confidence ?? null,
    detail: w.detail ?? null, // verbatim
  };
}
