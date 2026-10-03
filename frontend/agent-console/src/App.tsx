// Agent Console app shell (P6-3): initiate + index + per-turn viewer, over SSE.
//
// The GUI form of the FE-1 CLI driver: start a run from a prepared request, index it in
// the session list, and watch its per-turn view live over SSE (the Sense-B satisfaction
// check the gate computes). This is SSE observability/control, NOT A2UI.

import {
  Container,
  Divider,
  Grid,
  Paper,
  Stack,
  Typography,
} from "@mui/material";
import { useEffect, useState } from "react";
import {
  getPrepared,
  listSessions,
  startSession,
  subscribeEvents,
} from "./api";
import { NewSession } from "./components/NewSession";
import { SessionList } from "./components/SessionList";
import { TurnViewer } from "./components/TurnViewer";
import type { Prepared, Snapshot } from "./domain/console";

export function App() {
  const [prepared, setPrepared] = useState<Prepared[]>([]);
  const [sessions, setSessions] = useState<Snapshot[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function guard(fn: () => Promise<void>) {
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  useEffect(() => {
    void guard(async () => setPrepared(await getPrepared()));
  }, []);

  // Live-update the selected session over SSE, refreshing the list on each frame.
  useEffect(() => {
    if (!selectedId) return;
    const unsubscribe = subscribeEvents(selectedId, (snap) => {
      setSessions((prev) =>
        prev.map((s) => (s.sessionId === snap.sessionId ? snap : s)),
      );
    });
    return unsubscribe;
  }, [selectedId]);

  const onStart = (preparedKey: string) =>
    guard(async () => {
      const snap = await startSession(preparedKey);
      setSessions(await listSessions());
      setSelectedId(snap.sessionId);
    });

  const selected = sessions.find((s) => s.sessionId === selectedId) ?? null;

  return (
    <Container maxWidth="lg" sx={{ py: 4 }}>
      <Stack spacing={2}>
        <Typography variant="h5">Agent Console</Typography>
        {error ? <Typography color="error">{error}</Typography> : null}
        <NewSession prepared={prepared} onStart={onStart} />
        <Divider />
        <Grid container spacing={2}>
          <Grid item xs={12} md={4}>
            <Paper sx={{ p: 2 }}>
              <Typography variant="subtitle1" gutterBottom>
                Sessions
              </Typography>
              <SessionList
                sessions={sessions}
                selectedId={selectedId}
                onSelect={setSelectedId}
              />
            </Paper>
          </Grid>
          <Grid item xs={12} md={8}>
            <Paper sx={{ p: 2 }}>
              {selected ? (
                <TurnViewer snapshot={selected} />
              ) : (
                <Typography color="text.secondary">
                  Select a session to view its turn.
                </Typography>
              )}
            </Paper>
          </Grid>
        </Grid>
      </Stack>
    </Container>
  );
}
