// HITL Queue component (P6-4) — exchanges with an active HITL park.
//
// A list of HITL-parked exchanges with an approve/reject resolve form. Posts
// resolveExchange on submit. Renders the parked entry's reasonCode/message verbatim
// (content-not-pixels).

import {
  Box,
  Button,
  List,
  ListItem,
  ListItemText,
  Paper,
  TextField,
  Typography,
} from "@mui/material";
import { useState } from "react";
import * as api from "../api";
import type { OpsExchange } from "../domain/ops";

interface HitlQueueProps {
  queue: OpsExchange[];
  onResolved: () => void;
}

export function HitlQueue({ queue, onResolved }: HitlQueueProps) {
  const [message, setMessage] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const handleResolve = async (sessionId: string, resolution: "accepted" | "rejected") => {
    if (!message.trim()) {
      alert("Please provide a message.");
      return;
    }
    setSubmitting(true);
    try {
      await api.resolveExchange(sessionId, resolution, message.trim());
      setMessage("");
      onResolved();
    } catch (e) {
      alert(`Resolve failed: ${e}`);
    } finally {
      setSubmitting(false);
    }
  };

  if (queue.length === 0) {
    return (
      <Paper sx={{ p: 2 }}>
        <Typography variant="h6" gutterBottom>
          HITL Queue
        </Typography>
        <Typography variant="body2" color="text.secondary">
          No exchanges awaiting human review.
        </Typography>
      </Paper>
    );
  }

  return (
    <Paper sx={{ p: 2 }}>
      <Typography variant="h6" gutterBottom>
        HITL Queue
      </Typography>
      <List>
        {queue.map((ex) => {
          const parkedEntry = ex.entries.find((e) => e.disposition === "pending");
          return (
            <ListItem key={ex.sessionId} sx={{ flexDirection: "column", alignItems: "flex-start" }}>
              <ListItemText
                primary={`Exchange ${ex.contextId.slice(0, 8)}`}
                secondary={parkedEntry ? `${parkedEntry.reasonCode}: ${parkedEntry.message}` : "Pending"}
              />
              <Box sx={{ mt: 1, display: "flex", gap: 1, width: "100%" }}>
                <TextField
                  size="small"
                  label="Reviewer message"
                  value={message}
                  onChange={(e) => setMessage(e.target.value)}
                  disabled={submitting}
                  fullWidth
                />
                <Button
                  variant="contained"
                  color="success"
                  onClick={() => handleResolve(ex.sessionId, "accepted")}
                  disabled={submitting}
                >
                  Approve
                </Button>
                <Button
                  variant="outlined"
                  color="error"
                  onClick={() => handleResolve(ex.sessionId, "rejected")}
                  disabled={submitting}
                >
                  Reject
                </Button>
              </Box>
            </ListItem>
          );
        })}
      </List>
    </Paper>
  );
}
