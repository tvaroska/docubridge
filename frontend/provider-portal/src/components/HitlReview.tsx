// HITL-review view: the parked document (shown verbatim with its reasonCode/message)
// + an accept/reject form that posts the reviewer's resolution back to the host.

import { Alert, Box, Button, Stack, TextField, Typography } from "@mui/material";
import { useState } from "react";
import type { A2uiView } from "../domain/a2ui";

export function HitlReview({
  view,
  onResolve,
}: {
  view: A2uiView;
  onResolve: (resolution: "accepted" | "rejected", message: string) => void;
}) {
  const [message, setMessage] = useState("");
  const parked = view.rejected[0];

  return (
    <Stack spacing={2}>
      <Typography variant="h6">Human review</Typography>

      {parked ? (
        <Alert severity="warning">
          <strong>
            {parked.docId} · {parked.doctype}
          </strong>
          {parked.reasonCode ? ` — ${parked.reasonCode}` : null}
          {/* message relayed verbatim from the graph. */}
          {parked.message ? <Box>{parked.message}</Box> : null}
        </Alert>
      ) : null}

      <TextField
        label="Reviewer note"
        value={message}
        onChange={(e) => setMessage(e.target.value)}
        multiline
        minRows={2}
      />

      <Stack direction="row" spacing={2}>
        <Button
          variant="contained"
          color="success"
          onClick={() => onResolve("accepted", message)}
        >
          Accept
        </Button>
        <Button
          variant="outlined"
          color="error"
          onClick={() => onResolve("rejected", message)}
        >
          Reject
        </Button>
      </Stack>
    </Stack>
  );
}
