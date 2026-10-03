// Escalation Queue component (P6-4) — exchanges with a REASON_ESCALATED entry.
//
// A list of escalated exchanges (pending, route-to-human). Renders each entry's
// reasonCode/message verbatim (content-not-pixels).

import {
  List,
  ListItem,
  ListItemText,
  Paper,
  Typography,
} from "@mui/material";
import type { OpsExchange } from "../domain/ops";

interface EscalationQueueProps {
  queue: OpsExchange[];
}

export function EscalationQueue({ queue }: EscalationQueueProps) {
  if (queue.length === 0) {
    return (
      <Paper sx={{ p: 2 }}>
        <Typography variant="h6" gutterBottom>
          Escalation Queue
        </Typography>
        <Typography variant="body2" color="text.secondary">
          No escalated exchanges.
        </Typography>
      </Paper>
    );
  }

  return (
    <Paper sx={{ p: 2 }}>
      <Typography variant="h6" gutterBottom>
        Escalation Queue
      </Typography>
      <List>
        {queue.map((ex) => {
          const escalatedEntry = ex.entries.find((e) => e.reasonCode === "escalated_for_review");
          return (
            <ListItem key={ex.sessionId}>
              <ListItemText
                primary={`Exchange ${ex.contextId.slice(0, 8)}`}
                secondary={escalatedEntry ? `${escalatedEntry.reasonCode}: ${escalatedEntry.message}` : "Escalated"}
              />
            </ListItem>
          );
        })}
      </List>
    </Paper>
  );
}
