// Presenter stage App (P6-5, FE-5) — the mode switcher + the always-on time-warp control.
//
// The stage hosts the three presentation modes (Split-Screen Theater / Timeline / Architecture
// X-Ray) behind a ToggleButtonGroup, defaulting to `split`. The time-warp control strip is
// always visible above the mode and drives the shared time-warp host over REST; the resulting
// state feeds Timeline + X-Ray. Uses @docbride/shared (theme, Panel, Badges).

import {
  Alert,
  Container,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import { useState } from "react";
import { ArchitectureXRay } from "./components/ArchitectureXRay";
import { Controls } from "./components/Controls";
import { SplitScreen } from "./components/SplitScreen";
import { Timeline } from "./components/Timeline";
import type { TimewarpState } from "./domain/timewarp";

type Mode = "split" | "timeline" | "xray";

export function App() {
  const [mode, setMode] = useState<Mode>("split");
  const [state, setState] = useState<TimewarpState | null>(null);
  const [error, setError] = useState<string | null>(null);

  return (
    <Container maxWidth="xl" sx={{ py: 3 }}>
      <Typography variant="h4" gutterBottom>
        Time-warp — Presenter Stage
      </Typography>

      <Controls
        state={state}
        onState={(s) => {
          setState(s);
          setError(null);
        }}
        onError={setError}
      />

      {error ? (
        <Alert severity="error" sx={{ mb: 2 }} data-testid="error">
          {error}
        </Alert>
      ) : null}

      <Stack direction="row" spacing={1} sx={{ mb: 2 }}>
        <ToggleButtonGroup
          exclusive
          value={mode}
          onChange={(_, m: Mode | null) => m && setMode(m)}
          size="small"
          data-testid="mode-switch"
        >
          <ToggleButton value="split" data-testid="mode-split">
            Split-Screen
          </ToggleButton>
          <ToggleButton value="timeline" data-testid="mode-timeline">
            Timeline
          </ToggleButton>
          <ToggleButton value="xray" data-testid="mode-xray">
            Architecture X-Ray
          </ToggleButton>
        </ToggleButtonGroup>
      </Stack>

      {mode === "split" ? <SplitScreen /> : null}
      {mode === "timeline" ? <Timeline state={state} onState={setState} /> : null}
      {mode === "xray" ? <ArchitectureXRay state={state} /> : null}
    </Container>
  );
}
