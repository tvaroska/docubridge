// Exchange list component (P6-4) — in-flight + done collections.
//
// A list/table of exchanges: {contextId, process, status}. Click selects an exchange for
// the ledger viewer. Newest-first (the host reverses for the UI).

import {
  List,
  ListItemButton,
  ListItemText,
  Paper,
  Typography,
} from "@mui/material";
import type { OpsExchange } from "../domain/ops";

interface ExchangeListProps {
  exchanges: OpsExchange[];
  selectedId: string | null;
  onSelect: (sessionId: string) => void;
}

export function ExchangeList({ exchanges, selectedId, onSelect }: ExchangeListProps) {
  if (exchanges.length === 0) {
    return (
      <Paper sx={{ p: 2 }}>
        <Typography variant="body2" color="text.secondary">
          No exchanges yet.
        </Typography>
      </Paper>
    );
  }

  return (
    <Paper>
      <List>
        {exchanges.map((ex) => (
          <ListItemButton
            key={ex.sessionId}
            selected={ex.sessionId === selectedId}
            onClick={() => onSelect(ex.sessionId)}
          >
            <ListItemText
              primary={`Exchange ${ex.contextId.slice(0, 8)}`}
              secondary={`${ex.process} — ${ex.status}`}
            />
          </ListItemButton>
        ))}
      </List>
    </Paper>
  );
}
