// Upload-prompt view: pick a document to submit + show any outstanding requirement
// messages (relayed verbatim — e.g. "blurry, resend a clear copy").
//
// Local demo: the picker offers the eval-corpus ids so the fixture extractor resolves
// them (P7 swaps this for a real byte upload). The requirement `message`/`reasonCode`
// are displayed verbatim; the frontend never authors an explanation.

import {
  Alert,
  Box,
  Button,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { useState } from "react";
import type { A2uiView } from "../domain/a2ui";

// Corpus ids the local fixture extractor understands (see wiki/evals/address).
const CORPUS_IDS = [
  "gov-id-clean",
  "bill-powerco-clean",
  "bill-aquautil-clear",
  "bill-aquautil-blurry",
  "passport-unsupported",
];

export function UploadPrompt({
  view,
  onUpload,
}: {
  view: A2uiView;
  onUpload: (artifactName: string) => void;
}) {
  const [artifact, setArtifact] = useState(CORPUS_IDS[0]);
  const action = view.actions.find((a) => a.kind === "upload");

  return (
    <Stack spacing={2}>
      <Typography variant="h6">
        {action?.prompt ?? "Please upload the requested document."}
      </Typography>

      {view.outstanding.map((req, i) => (
        <Alert key={i} severity="info">
          {/* reasonCode + message relayed verbatim from the graph. */}
          <strong>{req.item}</strong>
          {req.reasonCode ? ` — ${req.reasonCode}` : null}
          {req.message ? <Box>{req.message}</Box> : null}
        </Alert>
      ))}

      {action?.accept?.length ? (
        <Typography variant="body2" color="text.secondary">
          Accepted document types: {action.accept.join(", ")}
        </Typography>
      ) : null}

      <TextField
        select
        label="Document"
        value={artifact}
        onChange={(e) => setArtifact(e.target.value)}
      >
        {CORPUS_IDS.map((id) => (
          <MenuItem key={id} value={id}>
            {id}
          </MenuItem>
        ))}
      </TextField>

      <Button variant="contained" onClick={() => onUpload(artifact)}>
        Upload
      </Button>
    </Stack>
  );
}
