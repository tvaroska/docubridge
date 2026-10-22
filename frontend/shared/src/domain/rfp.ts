// RFP status projection mirror (@docubridge/shared, S0-frontend-1).
//
// The wire shape of `bridge.rfp.RfpStatusView` as the Bridge serves it in two places:
//   * the dashboard SSE/REST row's `rfp` key (`project_rfp_exchange` — no `as_of`), and
//   * the consumer read API `GET /rfps/{id}` (`project_rfp_status` — adds `as_of`,
//     `api_version` and each carrier's `reply_address` / `reply_subject_tag`).
// The mapper tolerates both: those fields are optional and map to `null`.
//
// A consumer's own status board can mirror this file. CONTENT-NOT-PIXELS (A2UI-1): every
// `reasonCode` / `message` / field review is relayed verbatim.

import {
  mapFieldReview,
  type FieldReview,
  type LifecycleStatus,
  type RequirementStatus,
  type RfpItemStatus,
  type WireFieldReview,
} from "./contract";

// --------------------------------------------------------------------------- //
// Wire (snake_case)                                                           //
// --------------------------------------------------------------------------- //
export interface WireRfpCarrier {
  party: string;
  context_id: string;
  lifecycle: LifecycleStatus;
  ready: number;
  waiting: number;
  chases?: number;
  last_chased_at?: string | null;
  reply_address?: string | null;
  reply_subject_tag?: string | null;
}

export interface WireRfpCell {
  party: string;
  product_line: string;
  requirement: RequirementStatus;
  status: RfpItemStatus;
  versions: string[];
  attempts: number;
  escalated: boolean;
  last_activity: string | null;
  reason_code: string | null;
  message: string | null;
  field_reviews?: WireFieldReview[];
}

export interface WireRfpStatus {
  rfp_id: string;
  process: string;
  context_id: string;
  due_at: string;
  as_of?: string;
  api_version?: string;
  lifecycle: LifecycleStatus;
  ready: number;
  waiting: number;
  counts: Partial<Record<RfpItemStatus, number>>;
  carriers: WireRfpCarrier[];
  cells: WireRfpCell[];
  unmatched: string[];
}

// --------------------------------------------------------------------------- //
// App-side (camelCase)                                                        //
// --------------------------------------------------------------------------- //
export interface RfpCarrier {
  party: string;
  contextId: string;
  lifecycle: LifecycleStatus;
  ready: number;
  waiting: number;
  chases: number;
  lastChasedAt: string | null;
  replyAddress: string | null;
  replySubjectTag: string | null;
}

export interface RfpCell {
  party: string;
  productLine: string;
  requirement: RequirementStatus;
  status: RfpItemStatus;
  versions: string[];
  attempts: number;
  escalated: boolean;
  lastActivity: string | null;
  // Relayed verbatim.
  reasonCode: string | null;
  message: string | null;
  fieldReviews: FieldReview[];
}

export interface RfpStatus {
  rfpId: string;
  process: string;
  contextId: string;
  dueAt: string;
  asOf: string | null;
  apiVersion: string | null;
  lifecycle: LifecycleStatus;
  ready: number;
  waiting: number;
  counts: Partial<Record<RfpItemStatus, number>>;
  carriers: RfpCarrier[];
  cells: RfpCell[];
  unmatched: string[];
}

// --------------------------------------------------------------------------- //
// Mappers                                                                     //
// --------------------------------------------------------------------------- //
export function mapRfpCarrier(w: WireRfpCarrier): RfpCarrier {
  return {
    party: w.party,
    contextId: w.context_id,
    lifecycle: w.lifecycle,
    ready: w.ready,
    waiting: w.waiting,
    chases: w.chases ?? 0,
    lastChasedAt: w.last_chased_at ?? null,
    replyAddress: w.reply_address ?? null,
    replySubjectTag: w.reply_subject_tag ?? null,
  };
}

export function mapRfpCell(w: WireRfpCell): RfpCell {
  return {
    party: w.party,
    productLine: w.product_line,
    requirement: w.requirement,
    status: w.status,
    versions: w.versions,
    attempts: w.attempts,
    escalated: w.escalated,
    lastActivity: w.last_activity,
    reasonCode: w.reason_code ?? null, // verbatim (content-not-pixels)
    message: w.message ?? null, // verbatim (content-not-pixels)
    fieldReviews: (w.field_reviews ?? []).map(mapFieldReview),
  };
}

export function mapRfpStatus(w: WireRfpStatus): RfpStatus {
  return {
    rfpId: w.rfp_id,
    process: w.process,
    contextId: w.context_id,
    dueAt: w.due_at,
    asOf: w.as_of ?? null,
    apiVersion: w.api_version ?? null,
    lifecycle: w.lifecycle,
    ready: w.ready,
    waiting: w.waiting,
    counts: w.counts,
    carriers: w.carriers.map(mapRfpCarrier),
    cells: w.cells.map(mapRfpCell),
    unmatched: w.unmatched,
  };
}

/** True when the carrier still owes something a chase can ask for: a tracked
 *  (required/optional), outstanding (not received), non-declined item. Mirrors
 *  `bridge.rfp.chaseable_items`. */
export function isChaseable(rfp: RfpStatus, party: string): boolean {
  return rfp.cells.some(
    (c) =>
      c.party === party &&
      (c.requirement === "required" || c.requirement === "optional") &&
      c.status !== "received" &&
      c.status !== "declined",
  );
}
