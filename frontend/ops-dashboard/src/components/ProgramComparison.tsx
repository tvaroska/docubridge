// Program comparison view (S1-benefits-7, beat 4 UI · CUJ-11).
//
// Renders N legs SIDE BY SIDE in one currency/schema (one column per carrier), rows = the
// comparable normalized fields, with the canonical basis (currency / language) stamped once
// in the header. Gaps are FLAGGED per leg: a leg whose verdict routes to negotiate shows a
// warning Chip and lists each gap's message + ruleId VERBATIM (content-not-pixels — the view
// arranges + labels, it authors no prose). A conformant leg shows a "conformant" marker.
//
// Pure props-driven (no fetch) — mirrors LedgerView/HitlQueue. The App re-renders on each SSE
// snapshot, so this view recomputes live as revised versions land (no extra wiring here).

import {
  Chip,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import { Panel } from "@docbride/shared";
import type { Comparison, ComparisonLeg } from "../domain/ops";

interface ProgramComparisonProps {
  programs: Comparison[];
}

// The comparable rows, in display order. Each maps a leg's normalized quote field.
const FIELD_ROWS: { label: string; get: (leg: ComparisonLeg) => string }[] = [
  { label: "Premium", get: (leg) => leg.quote.premium },
  { label: "Deductible", get: (leg) => leg.quote.deductible },
  { label: "Coverage limits", get: (leg) => leg.quote.coverageLimits },
  { label: "Exclusions", get: (leg) => leg.quote.exclusions },
  { label: "Effective", get: (leg) => leg.quote.effectiveDate },
  { label: "Expiry", get: (leg) => leg.quote.expiryDate },
];

function GapFlag({ leg }: { leg: ComparisonLeg }) {
  const flagged = leg.verdict.route === "negotiate" || leg.verdict.gaps.length > 0;
  if (!flagged) {
    return <Chip size="small" color="success" label="conformant" />;
  }
  return (
    <Stack spacing={0.5}>
      <Chip size="small" color="warning" label="gap — negotiate" />
      {leg.verdict.gaps.map((gap) => (
        <Typography key={gap.ruleId} variant="caption" color="text.secondary">
          {gap.ruleId}: {gap.message}
        </Typography>
      ))}
    </Stack>
  );
}

function ComparisonTable({ program }: { program: Comparison }) {
  const basis = `${program.currency} / ${program.language}`;
  return (
    <Panel
      title={`Program ${program.programId} — normalized to ${basis}`}
      data-testid={`program-${program.programId}`}
    >
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>Field</TableCell>
            {program.legs.map((leg) => (
              <TableCell key={leg.party}>{leg.party}</TableCell>
            ))}
          </TableRow>
        </TableHead>
        <TableBody>
          {FIELD_ROWS.map((row) => (
            <TableRow key={row.label}>
              <TableCell>{row.label}</TableCell>
              {program.legs.map((leg) => (
                <TableCell key={leg.party}>{row.get(leg)}</TableCell>
              ))}
            </TableRow>
          ))}
          <TableRow>
            <TableCell>Standard</TableCell>
            {program.legs.map((leg) => (
              <TableCell key={leg.party}>
                <GapFlag leg={leg} />
              </TableCell>
            ))}
          </TableRow>
        </TableBody>
      </Table>
    </Panel>
  );
}

export function ProgramComparison({ programs }: ProgramComparisonProps) {
  if (programs.length === 0) {
    return (
      <Panel title="Program Comparison">
        <Typography variant="body2" color="text.secondary">
          No programs under negotiation.
        </Typography>
      </Panel>
    );
  }

  return (
    <Stack spacing={2}>
      {programs.map((program) => (
        <ComparisonTable key={program.programId} program={program} />
      ))}
    </Stack>
  );
}
