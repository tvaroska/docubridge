// App-specific view model for the Ops Dashboard (P6-4; hoisted to @docbride/shared at P6-5).
//
// The shared contract core (LedgerEntry camel + its wire shape + the snake→camel mapEntry)
// now lives in @docbride/shared (§2f). This module keeps only the DASHBOARD-SPECIFIC view
// types (OpsExchange / Snapshot) + the view mappers that compose the shared mapEntry.
//
// Content-not-pixels: the dashboard host sends protocol-only JSON (OpsExchange projections
// + queues); THIS app owns the pixels. reasonCode/message are relayed verbatim by the
// shared mapEntry.

import {
  mapEntry,
  mapRfpStatus,
  type LedgerEntry,
  type RfpStatus,
  type WireLedgerEntry,
  type WireRfpStatus,
} from "@docbride/shared";

// Re-export the shared contract core so existing app imports keep working.
export { mapEntry };
export type { LedgerEntry, RfpStatus, WireLedgerEntry, WireRfpStatus };

// --------------------------------------------------------------------------- //
// Wire types (snake_case from the dashboard host) — app-specific views        //
// --------------------------------------------------------------------------- //
export interface WireExchange {
  session_id: string;
  context_id: string;
  process: string;
  status: string;
  lifecycle: string;
  entries: WireLedgerEntry[];
  hitl: boolean;
  escalated: boolean;
  // Present on an RFP row only (S0-frontend-1): the per-carrier x product-line matrix.
  rfp?: WireRfpStatus;
}

// Program comparison wire types (snake_case) — mirror ProgramComparison.model_dump(mode=json).
export interface WireStandardGap {
  rule_id: string;
  field: string;
  check: string;
  expected: string;
  actual: string;
  message: string;
}

export interface WireGapVerdict {
  party: string;
  conformant: boolean;
  gaps: WireStandardGap[];
  route: string;
}

export interface WireNormalizedQuote {
  party: string;
  currency: string;
  language: string;
  premium: string;
  deductible: string;
  coverage_limits: string;
  exclusions: string;
  effective_date: string;
  expiry_date: string;
  original_currency: string;
  original_language: string;
}

export interface WireComparisonLeg {
  party: string;
  quote: WireNormalizedQuote;
  verdict: WireGapVerdict;
}

export interface WireComparison {
  program_id: string;
  currency: string;
  language: string;
  legs: WireComparisonLeg[];
}

export interface WireSnapshot {
  exchanges: WireExchange[];
  hitl: WireExchange[];
  escalation: WireExchange[];
  // Present only when the dashboard host has a ProgramBoard injected (S1-benefits-7).
  programs?: WireComparison[];
}

// --------------------------------------------------------------------------- //
// App-side types (camelCase)                                                  //
// --------------------------------------------------------------------------- //
export interface OpsExchange {
  sessionId: string;
  contextId: string;
  process: string;
  status: string;
  lifecycle: string;
  entries: LedgerEntry[];
  hitl: boolean;
  escalated: boolean;
  // The RFP matrix on an RFP row; null/absent on a session row.
  rfp?: RfpStatus | null;
}

// Program comparison view types (camelCase).
export interface StandardGap {
  ruleId: string;
  field: string;
  check: string;
  expected: string;
  actual: string;
  message: string;
}

export interface GapVerdict {
  party: string;
  conformant: boolean;
  gaps: StandardGap[];
  route: string;
}

export interface NormalizedQuote {
  party: string;
  currency: string;
  language: string;
  premium: string;
  deductible: string;
  coverageLimits: string;
  exclusions: string;
  effectiveDate: string;
  expiryDate: string;
  originalCurrency: string;
  originalLanguage: string;
}

export interface ComparisonLeg {
  party: string;
  quote: NormalizedQuote;
  verdict: GapVerdict;
}

export interface Comparison {
  programId: string;
  currency: string;
  language: string;
  legs: ComparisonLeg[];
}

export interface Snapshot {
  exchanges: OpsExchange[];
  hitl: OpsExchange[];
  escalation: OpsExchange[];
  programs: Comparison[];
}

// --------------------------------------------------------------------------- //
// View mappers (compose the shared mapEntry)                                  //
// --------------------------------------------------------------------------- //
export function mapExchange(wire: WireExchange): OpsExchange {
  return {
    sessionId: wire.session_id,
    contextId: wire.context_id,
    process: wire.process,
    status: wire.status,
    lifecycle: wire.lifecycle,
    entries: wire.entries.map(mapEntry),
    hitl: wire.hitl,
    escalated: wire.escalated,
    rfp: wire.rfp ? mapRfpStatus(wire.rfp) : null,
  };
}

export function mapGap(wire: WireStandardGap): StandardGap {
  return {
    ruleId: wire.rule_id,
    field: wire.field,
    check: wire.check,
    expected: wire.expected,
    actual: wire.actual,
    message: wire.message,
  };
}

export function mapVerdict(wire: WireGapVerdict): GapVerdict {
  return {
    party: wire.party,
    conformant: wire.conformant,
    gaps: wire.gaps.map(mapGap),
    route: wire.route,
  };
}

export function mapQuote(wire: WireNormalizedQuote): NormalizedQuote {
  return {
    party: wire.party,
    currency: wire.currency,
    language: wire.language,
    premium: wire.premium,
    deductible: wire.deductible,
    coverageLimits: wire.coverage_limits,
    exclusions: wire.exclusions,
    effectiveDate: wire.effective_date,
    expiryDate: wire.expiry_date,
    originalCurrency: wire.original_currency,
    originalLanguage: wire.original_language,
  };
}

export function mapComparison(wire: WireComparison): Comparison {
  return {
    programId: wire.program_id,
    currency: wire.currency,
    language: wire.language,
    legs: wire.legs.map((leg) => ({
      party: leg.party,
      quote: mapQuote(leg.quote),
      verdict: mapVerdict(leg.verdict),
    })),
  };
}

export function mapSnapshot(wire: WireSnapshot): Snapshot {
  return {
    exchanges: wire.exchanges.map(mapExchange),
    hitl: wire.hitl.map(mapExchange),
    escalation: wire.escalation.map(mapExchange),
    programs: (wire.programs ?? []).map(mapComparison),
  };
}
