// Time-warp control strip (presenter stage, P6-5, §2h).
//
// Always visible above the presentation mode. Every button is an EXPLICIT REST call to the
// time-warp host — deterministic stage time:
//   Fast-forward → advance far enough to drain the ladder to `escalated`;
//   Step         → advance by one tick;
//   Pause/Resume → toggle the host's pause flag (advance is rejected while paused);
//   Replay       → reset the clock to 0 and restore every armed record to `on_track`.
// The Arm control arms the demo session's SLA ladder from tick 0.

import { Box, Button, Stack, TextField, Typography } from "@mui/material";
import { LifecycleBadge } from "@docubridge/shared";
import { useState } from "react";
import * as api from "../api";
import { latestLifecycle, type TimewarpState } from "../domain/timewarp";

// Fast-forward advances far enough that the ladder drains through overdue→reminder→escalated
// in one jump (the host re-arms + fires each step deterministically; see host `_drain`).
const FAST_FORWARD_TICKS = 10;

interface ControlsProps {
  state: TimewarpState | null;
  onState: (s: TimewarpState) => void;
  onError?: (message: string) => void;
}

export function Controls({ state, onState, onError }: ControlsProps) {
  const [sessionId, setSessionId] = useState("");
  const paused = state?.paused ?? false;

  const run = async (fn: () => Promise<TimewarpState>) => {
    try {
      onState(await fn());
    } catch (e) {
      onError?.(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <Box
      data-testid="control-strip"
      sx={{ p: 2, mb: 2, borderRadius: 2, bgcolor: "action.hover" }}
    >
      <Stack direction="row" spacing={2} alignItems="center" flexWrap="wrap" useFlexGap>
        <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>
          Time-warp
        </Typography>
        <TextField
          size="small"
          label="session id"
          value={sessionId}
          onChange={(e) => setSessionId(e.target.value)}
          inputProps={{ "data-testid": "session-input" }}
        />
        <Button
          variant="outlined"
          data-testid="arm-btn"
          disabled={!sessionId}
          onClick={() => run(() => api.arm(sessionId))}
        >
          Arm
        </Button>
        <Button
          variant="contained"
          data-testid="fast-forward-btn"
          onClick={() => run(() => api.advance(FAST_FORWARD_TICKS))}
        >
          Fast-forward
        </Button>
        <Button variant="outlined" data-testid="step-btn" onClick={() => run(() => api.step())}>
          Step
        </Button>
        {paused ? (
          <Button variant="outlined" data-testid="resume-btn" onClick={() => run(() => api.resume())}>
            Resume
          </Button>
        ) : (
          <Button variant="outlined" data-testid="pause-btn" onClick={() => run(() => api.pause())}>
            Pause
          </Button>
        )}
        <Button variant="text" data-testid="replay-btn" onClick={() => run(() => api.reset())}>
          Replay
        </Button>
        <Box sx={{ flexGrow: 1 }} />
        <Typography variant="body2" data-testid="clock-now">
          tick {state?.now ?? 0}
        </Typography>
        <LifecycleBadge value={latestLifecycle(state)} />
      </Stack>
    </Box>
  );
}
