// Badges — labelled MUI Chips for the shared domain vocabularies (@docubridge/shared, P6-5).
//
// CONTENT-NOT-PIXELS (A2UI-1): these components ARRANGE + LABEL only. They render the value
// the graph produced (a Disposition / RequirementStatus / LifecycleStatus) as a coloured
// Chip — they NEVER author, rewrite, or summarize an agent's `reasonCode`/`message`. The
// colour is a cosmetic affordance chosen by THIS app (which owns 100% of the pixels); the
// TEXT is always the verbatim value.

import { Chip } from "@mui/material";
import type {
  Disposition,
  LifecycleStatus,
  RequirementStatus,
  RfpItemStatus,
} from "./domain/contract";

type ChipColor = "success" | "error" | "warning" | "info" | "default";

function dispositionColor(d: Disposition): ChipColor {
  if (d === "accepted") return "success";
  if (d === "rejected") return "error";
  return "warning"; // pending
}

function requirementColor(s: RequirementStatus): ChipColor {
  if (s === "satisfied" || s === "waived") return "success";
  if (s === "required") return "warning";
  return "default"; // optional
}

function lifecycleColor(l: LifecycleStatus): ChipColor {
  if (l === "on_track") return "success";
  if (l === "overdue") return "warning";
  if (l === "reminder") return "info";
  return "error"; // escalated
}

function rfpStatusColor(s: RfpItemStatus): ChipColor {
  if (s === "received") return "success";
  if (s === "incomplete" || s === "overdue") return "warning";
  if (s === "declined") return "default";
  return "info"; // invited / acknowledged
}

/** A coloured Chip labelling a per-document Disposition (accepted/rejected/pending). */
export function DispositionBadge({ value }: { value: Disposition }) {
  return <Chip size="small" color={dispositionColor(value)} label={value} />;
}

/** A coloured Chip labelling a Requirement status (required/optional/satisfied/waived). */
export function RequirementBadge({ value }: { value: RequirementStatus }) {
  return <Chip size="small" color={requirementColor(value)} label={value} />;
}

/** A coloured Chip labelling the SLA lifecycle (on_track/overdue/reminder/escalated). */
export function LifecycleBadge({ value }: { value: LifecycleStatus }) {
  return (
    <Chip
      size="small"
      color={lifecycleColor(value)}
      label={value}
      data-testid="lifecycle-badge"
      data-lifecycle={value}
    />
  );
}

/** A coloured Chip labelling one RFP item's effective status (S0-frontend-1). */
export function RfpStatusBadge({ value }: { value: RfpItemStatus }) {
  return (
    <Chip
      size="small"
      color={rfpStatusColor(value)}
      label={value}
      data-testid="rfp-status-badge"
      data-status={value}
    />
  );
}
