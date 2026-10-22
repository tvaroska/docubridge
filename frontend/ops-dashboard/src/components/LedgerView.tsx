// Ledger viewer component (P6-4) — the per-exchange classified ledger.
//
// A table of ledger entries: docId, doctype, disposition, issuer, reasonCode, message.
// Renders reasonCode/message verbatim (content-not-pixels).

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import { DispositionBadge, Panel } from "@docubridge/shared";
import type { Disposition } from "@docubridge/shared";
import type { OpsExchange } from "../domain/ops";

interface LedgerViewProps {
  exchange: OpsExchange | null;
}

export function LedgerView({ exchange }: LedgerViewProps) {
  if (!exchange) {
    return (
      <Panel>
        <Typography variant="body2" color="text.secondary">
          Select an exchange to view its ledger.
        </Typography>
      </Panel>
    );
  }

  if (exchange.entries.length === 0) {
    return (
      <Panel title={`Exchange ${exchange.contextId.slice(0, 8)} — ${exchange.status}`}>
        <Typography variant="body2" color="text.secondary">
          No ledger entries yet.
        </Typography>
      </Panel>
    );
  }

  return (
    <Panel title={`Exchange ${exchange.contextId.slice(0, 8)} — ${exchange.status}`}>
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>Doc ID</TableCell>
            <TableCell>Doctype</TableCell>
            <TableCell>Issuer</TableCell>
            <TableCell>Disposition</TableCell>
            <TableCell>Reason Code</TableCell>
            <TableCell>Message</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {exchange.entries.map((entry, idx) => (
            <TableRow key={idx}>
              <TableCell>{entry.docId}</TableCell>
              <TableCell>{entry.doctype}</TableCell>
              <TableCell>{entry.issuer}</TableCell>
              <TableCell>
                <DispositionBadge value={entry.disposition as Disposition} />
              </TableCell>
              <TableCell>{entry.reasonCode}</TableCell>
              <TableCell>{entry.message}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Panel>
  );
}
