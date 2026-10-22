// App-specific view model for the Agent Console (P6-3; hoisted to @docubridge/shared at P6-5).
//
// The shared contract core (LedgerEntry / Requirement camel + their wire shapes + the
// snake→camel mapEntry / mapRequirement) now lives in @docubridge/shared (§2f). This module
// keeps only the CONSOLE-SPECIFIC view types (Turn / Snapshot / Prepared) + the view mappers
// that compose the shared mappers.
//
// This is SSE observability, **not A2UI** — the service agent does not speak A2UI. The
// relayed reasonCode/message are relayed VERBATIM by the shared mappers (content-not-pixels
// still holds); this app owns 100% of the pixels.

import {
  mapEntry,
  mapRequirement,
  type LedgerEntry,
  type LifecycleStatus,
  type Requirement,
  type WireLedgerEntry,
  type WireRequirement,
} from "@docubridge/shared";

// Re-export the shared contract core so existing app imports keep working.
export { mapEntry, mapRequirement };
export type { LedgerEntry, LifecycleStatus, Requirement };

export type Disposition = "accepted" | "rejected" | "pending";
export type RequirementStatus = "required" | "optional" | "satisfied" | "waived";
export type SessionStatus = "running" | "parked" | "done" | "error";

// --------------------------------------------------------------------------- //
// App-specific view types (camelCase)                                         //
// --------------------------------------------------------------------------- //
export interface Turn {
  contextId: string;
  entries: LedgerEntry[];
  outstanding: Requirement[];
  terminal: boolean;
  lifecycle: LifecycleStatus;
}

export interface Snapshot {
  sessionId: string;
  preparedKey: string;
  status: SessionStatus;
  rounds: number;
  satisfied: boolean;
  error: string | null;
  turn: Turn | null;
}

export interface Prepared {
  key: string;
  label: string;
}

// --------------------------------------------------------------------------- //
// Wire shapes (snake_case — the console_server's app-specific envelope)        //
// --------------------------------------------------------------------------- //
interface WireRequirementsList {
  requirements?: WireRequirement[];
  done?: boolean;
}

interface WireCollectionStatus {
  entries?: WireLedgerEntry[];
  outstanding?: WireRequirementsList | null;
}

export interface WireTurn {
  context_id: string;
  status: WireCollectionStatus;
  requirements?: WireRequirementsList | null;
  terminal?: boolean;
  lifecycle?: LifecycleStatus;
}

export interface WireSnapshot {
  session_id: string;
  prepared_key: string;
  status: SessionStatus;
  rounds?: number;
  satisfied?: boolean;
  error?: string | null;
  turn?: WireTurn | null;
}

// --------------------------------------------------------------------------- //
// View mappers (compose the shared mapEntry / mapRequirement)                  //
// --------------------------------------------------------------------------- //
/** Map the host's snake_case ExchangeTurn JSON into the app's camelCase model. */
export function mapTurn(json: WireTurn): Turn {
  return {
    contextId: json.context_id,
    entries: (json.status?.entries ?? []).map(mapEntry),
    outstanding: (json.status?.outstanding?.requirements ?? []).map(mapRequirement),
    terminal: json.terminal ?? false,
    lifecycle: json.lifecycle ?? "on_track",
  };
}

/** Map the host's snake_case snapshot JSON into the app's camelCase model. */
export function mapSnapshot(json: WireSnapshot): Snapshot {
  return {
    sessionId: json.session_id,
    preparedKey: json.prepared_key,
    status: json.status,
    rounds: json.rounds ?? 0,
    satisfied: json.satisfied ?? false,
    error: json.error ?? null,
    turn: json.turn ? mapTurn(json.turn) : null,
  };
}
