// RFP exchange view (S0-frontend-1) — the per-carrier x product-line matrix for one RFP.
//
// One Panel per RFP: a header (due date, overall LifecycleBadge, ready/waiting), then a
// carriers (rows) x product lines (columns) table. Each cell shows its RfpStatusBadge,
// version count, last activity, and the gate's reasonCode/message + field reviews VERBATIM
// (content-not-pixels — this view arranges + labels, it authors no explanation).
//
// Highlighting: an overdue cell is warning-tinted; an escalated cell (resubmission cap) or
// an escalated carrier row (SLA ladder) is error-tinted — `data-highlight="overdue" |
// "escalated"` carries the same signal for tests. Each carrier row has its LifecycleBadge,
// chase count / last chased, a "Chase now" button (disabled when that carrier has nothing
// chaseable) and, when a portal app URL is given, a link to that carrier's checklist.
//
// Props-driven (no fetch): the App wires `onChase` to the dashboard host and re-renders on
// each SSE snapshot, so a chase / upload shows up live.

import {
  Box,
  Button,
  Link,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import {
  isChaseable,
  LifecycleBadge,
  Panel,
  RfpStatusBadge,
  type RfpCarrier,
  type RfpCell,
  type RfpStatus,
} from "@docubridge/shared";

export type ChaseHandler = (rfpId: string, party: string | null) => void;

interface RfpExchangeViewProps {
  rfp: RfpStatus;
  onChase: ChaseHandler;
  /** Disables the chase buttons while a chase is in flight. */
  busy?: boolean;
  /** The provider-portal app URL; when set each carrier links to its checklist. */
  portalAppUrl?: string;
}

type Highlight = "overdue" | "escalated" | undefined;

const TINT: Record<"overdue" | "escalated", string> = {
  overdue: "warning.light",
  escalated: "error.light",
};

/** ISO timestamp → "YYYY-MM-DD HH:MMZ" (UTC; the wire value, shortened). */
export function shortTime(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : `${d.toISOString().slice(0, 16).replace("T", " ")}Z`;
}

function cellHighlight(cell: RfpCell): Highlight {
  if (cell.escalated) return "escalated";
  if (cell.status === "overdue") return "overdue";
  return undefined;
}

function MatrixCell({ cell }: { cell: RfpCell | undefined }) {
  if (!cell) {
    return <TableCell data-testid="rfp-cell-empty" />;
  }
  const highlight = cellHighlight(cell);
  return (
    <TableCell
      data-testid={`rfp-cell-${cell.party}-${cell.productLine}`}
      data-highlight={highlight}
      sx={highlight ? { bgcolor: TINT[highlight] } : undefined}
    >
      <Stack spacing={0.5} alignItems="flex-start">
        <RfpStatusBadge value={cell.status} />
        <Typography variant="caption" color="text.secondary">
          {cell.versions.length} version{cell.versions.length === 1 ? "" : "s"} · last{" "}
          {shortTime(cell.lastActivity)}
        </Typography>
        {cell.reasonCode || cell.message ? (
          <Typography variant="caption">
            {[cell.reasonCode, cell.message].filter(Boolean).join(": ")}
          </Typography>
        ) : null}
        {cell.fieldReviews.map((fr) => (
          <Typography key={fr.path} variant="caption" color="text.secondary">
            {[fr.path, fr.reason, fr.detail].filter(Boolean).join(" · ")}
          </Typography>
        ))}
      </Stack>
    </TableCell>
  );
}

function CarrierCell({
  carrier,
  portalAppUrl,
}: {
  carrier: RfpCarrier;
  portalAppUrl?: string;
}) {
  return (
    <TableCell>
      <Stack spacing={0.5} alignItems="flex-start">
        <Typography variant="subtitle2">{carrier.party}</Typography>
        <LifecycleBadge value={carrier.lifecycle} />
        <Typography variant="caption" color="text.secondary" data-testid="rfp-chases">
          chased {carrier.chases}×
          {carrier.lastChasedAt ? ` · last ${shortTime(carrier.lastChasedAt)}` : ""}
        </Typography>
        {portalAppUrl ? (
          <Link
            variant="caption"
            href={`${portalAppUrl}/?leg=${encodeURIComponent(carrier.contextId)}`}
            target="_blank"
            rel="noreferrer"
          >
            carrier checklist
          </Link>
        ) : null}
      </Stack>
    </TableCell>
  );
}

export function RfpExchangeView({ rfp, onChase, busy = false, portalAppUrl }: RfpExchangeViewProps) {
  const lines = [...new Set(rfp.cells.map((c) => c.productLine))];
  const cellAt = (party: string, line: string) =>
    rfp.cells.find((c) => c.party === party && c.productLine === line);
  const anyChaseable = rfp.carriers.some((c) => isChaseable(rfp, c.party));

  return (
    <Panel
      title={`RFP ${rfp.rfpId}`}
      data-testid={`rfp-exchange-${rfp.rfpId}`}
    >
      <Stack direction="row" spacing={2} alignItems="center" sx={{ mb: 1 }}>
        <Typography variant="body2">due {shortTime(rfp.dueAt)}</Typography>
        <LifecycleBadge value={rfp.lifecycle} />
        <Typography variant="body2" color="text.secondary">
          {rfp.ready} ready · {rfp.waiting} waiting
        </Typography>
        <Box sx={{ flexGrow: 1 }} />
        <Button
          size="small"
          variant="outlined"
          disabled={busy || !anyChaseable}
          onClick={() => onChase(rfp.rfpId, null)}
        >
          Chase all waiting
        </Button>
      </Stack>
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>Carrier</TableCell>
            {lines.map((line) => (
              <TableCell key={line}>{line}</TableCell>
            ))}
            <TableCell />
          </TableRow>
        </TableHead>
        <TableBody>
          {rfp.carriers.map((carrier) => {
            const rowHighlight: Highlight =
              carrier.lifecycle === "escalated" ? "escalated" : undefined;
            return (
              <TableRow
                key={carrier.party}
                data-testid={`rfp-row-${carrier.party}`}
                data-highlight={rowHighlight}
                sx={rowHighlight ? { bgcolor: TINT[rowHighlight] } : undefined}
              >
                <CarrierCell carrier={carrier} portalAppUrl={portalAppUrl} />
                {lines.map((line) => (
                  <MatrixCell key={line} cell={cellAt(carrier.party, line)} />
                ))}
                <TableCell>
                  <Button
                    size="small"
                    variant="contained"
                    aria-label={`Chase ${carrier.party} now`}
                    disabled={busy || !isChaseable(rfp, carrier.party)}
                    onClick={() => onChase(rfp.rfpId, carrier.party)}
                  >
                    Chase now
                  </Button>
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
      {rfp.unmatched.length > 0 ? (
        <Typography variant="caption" color="text.secondary">
          unmatched documents: {rfp.unmatched.join(", ")}
        </Typography>
      ) : null}
    </Panel>
  );
}
