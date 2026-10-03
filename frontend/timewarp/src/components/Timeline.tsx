// Timeline mode (presenter stage, P6-5, §2g).
//
// Renders each armed exchange's fired-event trail (armed → overdue → reminder → escalated) as
// an ordered list, plus a range SCRUBBER (MUI Slider) whose value drives
// `POST /timewarp/advance` with `advance_to` semantics — scrubbing the virtual clock forward.
// Every relayed lifecycle value is shown verbatim via the shared LifecycleBadge.

import { Box, List, ListItem, ListItemText, Slider, Stack, Typography } from "@mui/material";
import { LifecycleBadge, Panel } from "@docbride/shared";
import { useState } from "react";
import * as api from "../api";
import type { TimewarpState } from "../domain/timewarp";

const SCRUB_MAX = 6; // ticks; address-proof escalates at tick 3, so 6 covers the ladder + slack.

interface TimelineProps {
  state: TimewarpState | null;
  onState: (s: TimewarpState) => void;
}

export function Timeline({ state, onState }: TimelineProps) {
  const [scrub, setScrub] = useState(state?.now ?? 0);

  const commitScrub = async (to: number) => {
    if (to <= (state?.now ?? 0)) return; // the clock only moves forward (deterministic time)
    try {
      onState(await api.advanceTo(to));
    } catch {
      /* backward/paused rejected by the host; leave the slider where it was */
    }
  };

  return (
    <Stack spacing={2}>
      <Panel title="Scrubber">
        <Typography variant="body2" color="text.secondary" gutterBottom>
          Drag to fast-forward the virtual clock (advance_to). tick {state?.now ?? 0}
        </Typography>
        <Box sx={{ px: 2 }}>
          <Slider
            data-testid="scrubber"
            value={scrub}
            min={0}
            max={SCRUB_MAX}
            step={1}
            marks
            valueLabelDisplay="auto"
            onChange={(_, v) => setScrub(v as number)}
            onChangeCommitted={(_, v) => commitScrub(v as number)}
          />
        </Box>
      </Panel>

      {(state?.exchanges ?? []).map((ex) => (
        <Panel key={ex.sessionId} title={`Exchange ${ex.contextId.slice(0, 8)}`}>
          <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1 }}>
            <Typography variant="body2">lifecycle:</Typography>
            <LifecycleBadge value={ex.lifecycle} />
          </Stack>
          <List dense disablePadding data-testid="event-list">
            <ListItem disableGutters>
              <ListItemText primary="armed" secondary="tick 0" />
            </ListItem>
            {ex.events.map((e, i) => (
              <ListItem key={`${e.step}-${i}`} disableGutters>
                <ListItemText primary={e.step} secondary={`tick ${e.tick}`} />
              </ListItem>
            ))}
          </List>
        </Panel>
      ))}
      {(state?.exchanges ?? []).length === 0 ? (
        <Typography variant="body2" color="text.secondary">
          No armed exchanges yet — arm a session in the control strip.
        </Typography>
      ) : null}
    </Stack>
  );
}
