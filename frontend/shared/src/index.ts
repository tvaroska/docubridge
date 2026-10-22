// @docubridge/shared — the shared UI/domain workspace package (P6-5).
//
// SOURCE-ONLY (§2e): no build step. Vite/Vitest resolve the TS through the bundler; the
// consuming apps type-check this source during their own `tsc --noEmit`. Consumers import
// from the barrel: `import { makeTheme, Panel, Badges, mapEntry } from "@docubridge/shared";`.
//
// Factors out of the three apps: `theme` (a makeTheme factory — accents differ per zone),
// `Panel`, `Badges`, and the snake→camel `domain/contract` mirror (types + mappers). The
// content-not-pixels relay (A2UI-1) holds throughout: Panel/Badges arrange + label only,
// and the mappers relay reasonCode/message verbatim.

export { makeTheme } from "./theme";
export { Panel } from "./Panel";
export type { PanelProps } from "./Panel";
export {
  DispositionBadge,
  RequirementBadge,
  LifecycleBadge,
  RfpStatusBadge,
} from "./Badges";
export {
  mapEntry,
  mapFieldReview,
  mapRequirement,
} from "./domain/contract";
export type {
  Disposition,
  RequirementStatus,
  LifecycleStatus,
  RfpItemStatus,
  FieldReview,
  WireFieldReview,
  LedgerEntry,
  Requirement,
  WireLedgerEntry,
  WireRequirement,
} from "./domain/contract";
export {
  isChaseable,
  mapRfpCarrier,
  mapRfpCell,
  mapRfpStatus,
} from "./domain/rfp";
export type {
  RfpCarrier,
  RfpCell,
  RfpStatus,
  WireRfpCarrier,
  WireRfpCell,
  WireRfpStatus,
} from "./domain/rfp";
