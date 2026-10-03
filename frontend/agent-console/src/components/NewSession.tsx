// NewSession — the "new session from a prepared request" control (P6-3).
//
// A picker over the console_server's prepared-request catalog + a "New session" button.
// The client sends a KEY (not raw agent args) — the console is the curated initiation
// surface (§2d). THIS app owns the pixels; the host owns the catalog.

import {
  Box,
  Button,
  FormControl,
  InputLabel,
  MenuItem,
  Select,
  Stack,
} from "@mui/material";
import { useState } from "react";
import type { Prepared } from "../domain/console";

export function NewSession({
  prepared,
  onStart,
}: {
  prepared: Prepared[];
  onStart: (preparedKey: string) => void;
}) {
  const [key, setKey] = useState<string>(prepared[0]?.key ?? "");

  return (
    <Stack direction="row" spacing={2} alignItems="center">
      <FormControl size="small" sx={{ minWidth: 320 }}>
        <InputLabel id="prepared-label">Prepared request</InputLabel>
        <Select
          labelId="prepared-label"
          label="Prepared request"
          value={key}
          onChange={(e) => setKey(e.target.value)}
        >
          {prepared.map((p) => (
            <MenuItem key={p.key} value={p.key}>
              {p.label}
            </MenuItem>
          ))}
        </Select>
      </FormControl>
      <Box>
        <Button
          variant="contained"
          disabled={!key}
          onClick={() => onStart(key)}
        >
          New session
        </Button>
      </Box>
    </Stack>
  );
}
