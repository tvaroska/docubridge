// TurnViewer — the per-turn viewer (P6-3).
//
// Renders the contract ExchangeTurn the agent was handed DIRECTLY (this is SSE, NOT
// A2UI): a ledger table (doc/doctype/disposition/issuer/reasonCode/message), the gate
// round count ("reasoning"/negotiation depth), the SATISFACTION check (the gate's
// Sense-B verdict — read from the host's `satisfied`, never re-decided here), and the
// returned requirements list. The Sense-B verdict lives in the gate, not here
// (design/frontend.md §1) — this component only displays it (the FE-2 "Sense B
// watchable" surface).
//
// Content-not-pixels still holds: every relayed `reasonCode`/`message` renders verbatim.
// P6-5 hoists the shared theme/domain/Panel/Badges into a shared workspace package.

import {
  Chip,
  Divider,
  List,
  ListItem,
  ListItemText,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import { DispositionBadge } from "@docbride/shared";
import type { Disposition } from "@docbride/shared";
import type { Snapshot } from "../domain/console";

export function TurnViewer({ snapshot }: { snapshot: Snapshot }) {
  const { turn, satisfied, rounds, status } = snapshot;

  return (
    <Stack spacing={2}>
      <Stack direction="row" spacing={2} alignItems="center" flexWrap="wrap">
        <Chip label={`status: ${status}`} />
        <Chip label={`rounds: ${rounds}`} />
        <Chip
          color={satisfied ? "success" : "warning"}
          label={satisfied ? "satisfied" : "not satisfied"}
        />
      </Stack>

      {snapshot.error ? (
        <Typography color="error">{snapshot.error}</Typography>
      ) : null}

      {turn ? (
        <>
          <Typography variant="subtitle2">Ledger</Typography>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>doc</TableCell>
                <TableCell>doctype</TableCell>
                <TableCell>disposition</TableCell>
                <TableCell>issuer</TableCell>
                <TableCell>reason</TableCell>
                <TableCell>message</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {turn.entries.map((e) => (
                <TableRow key={e.docId}>
                  <TableCell>{e.docId}</TableCell>
                  <TableCell>{e.doctype}</TableCell>
                  <TableCell>
                    <DispositionBadge value={e.disposition as Disposition} />
                  </TableCell>
                  <TableCell>{e.issuer ?? "—"}</TableCell>
                  {/* relayed verbatim */}
                  <TableCell>{e.reasonCode ?? "—"}</TableCell>
                  <TableCell>{e.message ?? "—"}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>

          {turn.outstanding.length > 0 ? (
            <>
              <Divider />
              <Typography variant="subtitle2">Outstanding requirements</Typography>
              <List dense disablePadding>
                {turn.outstanding.map((r, i) => (
                  <ListItem key={`${r.item}-${i}`} disableGutters>
                    <ListItemText
                      primary={`${r.item} (${r.status})`}
                      // relayed verbatim
                      secondary={r.message ?? r.reasonCode ?? undefined}
                    />
                  </ListItem>
                ))}
              </List>
            </>
          ) : null}
        </>
      ) : (
        <Typography color="text.secondary">No turn observed yet.</Typography>
      )}
    </Stack>
  );
}
