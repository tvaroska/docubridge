// SessionList — the live session index (P6-3).
//
// Lists this agent's own runs (the console lists its OWN sessions; the cross-exchange
// operator window is the Ops Dashboard, P6-4). Each row shows the prepared-request key +
// a live status; clicking a row selects it for the per-turn viewer.

import {
  Chip,
  List,
  ListItemButton,
  ListItemText,
  Typography,
} from "@mui/material";
import type { SessionStatus, Snapshot } from "../domain/console";

const STATUS_COLOR: Record<
  SessionStatus,
  "default" | "info" | "success" | "warning" | "error"
> = {
  running: "info",
  parked: "warning",
  done: "success",
  error: "error",
};

export function SessionList({
  sessions,
  selectedId,
  onSelect,
}: {
  sessions: Snapshot[];
  selectedId: string | null;
  onSelect: (sessionId: string) => void;
}) {
  if (sessions.length === 0) {
    return <Typography color="text.secondary">No sessions yet.</Typography>;
  }
  return (
    <List dense disablePadding>
      {sessions.map((s) => (
        <ListItemButton
          key={s.sessionId}
          selected={s.sessionId === selectedId}
          onClick={() => onSelect(s.sessionId)}
        >
          <ListItemText
            primary={s.preparedKey}
            secondary={s.sessionId.slice(0, 8)}
          />
          <Chip
            size="small"
            label={s.status}
            color={STATUS_COLOR[s.status] ?? "default"}
          />
        </ListItemButton>
      ))}
    </List>
  );
}
