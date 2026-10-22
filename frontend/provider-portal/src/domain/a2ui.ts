// App-specific A2UI view model for the Provider Portal (P6-2; hoisted to @docubridge/shared
// at P6-5).
//
// The shared contract core (the camel LedgerEntry / Requirement + their wire shapes + the
// snake→camel mapEntry / mapRequirement) now lives in @docubridge/shared (§2f). This module
// keeps only the PORTAL-SPECIFIC A2UI view types (A2uiView / A2uiAction) + the view mapper
// that composes the shared mappers.
//
// The portal host sends the P6-1 A2uiView as JSON: **content + protocol, never pixels** — no
// color/size/style field, and each reason_code/message is copied VERBATIM from the
// LedgerEntry/Requirement the graph produced (the A2UI-1 relay invariant). The shared
// mappers preserve that verbatim relay; this app owns 100% of the pixels.

import {
  mapEntry,
  mapRequirement,
  type LedgerEntry,
  type LifecycleStatus,
  type Requirement,
  type WireLedgerEntry,
  type WireRequirement,
} from "@docubridge/shared";

// Re-export the shared contract core so existing app imports keep working. The portal's
// A2UI "item" IS a shared LedgerEntry; its "requirement" IS a shared Requirement (the portal
// simply ignores the gate/issuer fields it does not surface).
export { mapEntry, mapRequirement };
export type { LifecycleStatus };
export type A2uiItem = LedgerEntry;
export type A2uiRequirement = Requirement;

export type ViewKind = "party_status" | "upload_prompt" | "hitl_review";
export type Disposition = "accepted" | "rejected" | "pending";
export type RequirementStatus = "required" | "optional" | "satisfied" | "waived";
export type ActionKind = "upload" | "text" | "form";

export interface A2uiAction {
  kind: ActionKind;
  accept: string[];
  prompt: string | null;
}

export interface A2uiView {
  kind: ViewKind;
  contextId: string;
  accepted: A2uiItem[];
  rejected: A2uiItem[];
  outstanding: A2uiRequirement[];
  actions: A2uiAction[];
  lifecycle: LifecycleStatus;
}

// --- snake_case wire shapes (exactly what encode_a2ui_view emits) ------------

export interface WireAction {
  kind: ActionKind;
  accept?: string[];
  prompt?: string | null;
}

export interface WireView {
  kind: ViewKind;
  context_id: string;
  accepted?: WireLedgerEntry[];
  rejected?: WireLedgerEntry[];
  outstanding?: WireRequirement[];
  actions?: WireAction[];
  lifecycle?: LifecycleStatus;
}

export function mapAction(w: WireAction): A2uiAction {
  return {
    kind: w.kind,
    accept: w.accept ?? [],
    prompt: w.prompt ?? null,
  };
}

/** Map the host's snake_case A2uiView JSON into the app's camelCase model. */
export function mapView(json: WireView): A2uiView {
  return {
    kind: json.kind,
    contextId: json.context_id,
    accepted: (json.accepted ?? []).map(mapEntry),
    rejected: (json.rejected ?? []).map(mapEntry),
    outstanding: (json.outstanding ?? []).map(mapRequirement),
    actions: (json.actions ?? []).map(mapAction),
    lifecycle: json.lifecycle ?? "on_track",
  };
}
