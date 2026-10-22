// Carrier-facing RFP checklist model (S0-frontend-1) — the portal's RFP surface.
//
// `GET /rfp/legs/{context_id}` returns one carrier's checklist: content + protocol only
// (`bridge.portal.rfp_checklist.RfpChecklist`), filtered to that carrier. Each item's
// reasonCode / message / field reviews are relayed VERBATIM (A2UI-1); this app owns the
// pixels. The upload affordance reuses the A2UI action shape.

import {
  mapFieldReview,
  type FieldReview,
  type LifecycleStatus,
  type RequirementStatus,
  type RfpItemStatus,
  type WireFieldReview,
} from "@docubridge/shared";
import { mapAction, type A2uiAction, type WireAction } from "./a2ui";

export interface WireRfpChecklistItem {
  product_line: string;
  requirement: RequirementStatus;
  status: RfpItemStatus;
  outstanding: boolean;
  version_count: number;
  last_activity: string | null;
  reason_code: string | null;
  message: string | null;
  field_reviews?: WireFieldReview[];
}

export interface WireRfpChecklist {
  kind: "rfp_checklist";
  rfp_id: string;
  party: string;
  context_id: string;
  due_at: string;
  lifecycle: LifecycleStatus;
  items: WireRfpChecklistItem[];
  reply_address?: string | null;
  reply_subject_tag?: string | null;
  actions?: WireAction[];
}

export interface RfpChecklistItem {
  productLine: string;
  requirement: RequirementStatus;
  status: RfpItemStatus;
  outstanding: boolean;
  versionCount: number;
  lastActivity: string | null;
  // Relayed verbatim.
  reasonCode: string | null;
  message: string | null;
  fieldReviews: FieldReview[];
}

export interface RfpChecklist {
  kind: "rfp_checklist";
  rfpId: string;
  party: string;
  contextId: string;
  dueAt: string;
  lifecycle: LifecycleStatus;
  items: RfpChecklistItem[];
  replyAddress: string | null;
  replySubjectTag: string | null;
  actions: A2uiAction[];
}

export function mapChecklistItem(w: WireRfpChecklistItem): RfpChecklistItem {
  return {
    productLine: w.product_line,
    requirement: w.requirement,
    status: w.status,
    outstanding: w.outstanding,
    versionCount: w.version_count,
    lastActivity: w.last_activity,
    reasonCode: w.reason_code ?? null, // verbatim
    message: w.message ?? null, // verbatim
    fieldReviews: (w.field_reviews ?? []).map(mapFieldReview),
  };
}

/** Map the host's snake_case checklist JSON into the app's camelCase model. */
export function mapRfpChecklist(w: WireRfpChecklist): RfpChecklist {
  return {
    kind: w.kind,
    rfpId: w.rfp_id,
    party: w.party,
    contextId: w.context_id,
    dueAt: w.due_at,
    lifecycle: w.lifecycle,
    items: w.items.map(mapChecklistItem),
    replyAddress: w.reply_address ?? null,
    replySubjectTag: w.reply_subject_tag ?? null,
    actions: (w.actions ?? []).map(mapAction),
  };
}
