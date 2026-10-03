// Provider Portal app shell: session lifecycle + the A2UI view switch.
//
// Drives the CUJ-2 flow against the portal host: start a session, upload documents
// (resubmit across parks), resolve HITL — the host threads PathBState between calls.
//
// RFP checklist mode (S0-frontend-1): opened with `?leg=<leg context id>` the app shows that
// carrier's RFP checklist instead (fetched on mount, replaced after each upload). The title
// is themeable via VITE_PORTAL_TITLE (the accent via VITE_PORTAL_ACCENT, see theme.ts).

import { Box, Button, Container, Paper, Stack, Typography } from "@mui/material";
import { useEffect, useState } from "react";
import {
  getRfpChecklist,
  resolveHitl,
  startSession,
  upload,
  uploadRfpQuote,
  type Session,
} from "./api";
import type { A2uiView } from "./domain/a2ui";
import type { RfpChecklist as Checklist } from "./domain/rfp";
import { RfpChecklist } from "./components/RfpChecklist";
import { View } from "./components/View";

const PROCESS = "address-proof";
let docCounter = 0;
const TITLE: string =
  (import.meta.env.VITE_PORTAL_TITLE as string | undefined) ?? "Provider Portal";

function legFromLocation(): string | null {
  return new URLSearchParams(window.location.search).get("leg");
}

function RfpLegApp({ leg }: { leg: string }) {
  const [checklist, setChecklist] = useState<Checklist | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    getRfpChecklist(leg)
      .then(setChecklist)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }, [leg]);

  const onUpload = (productLine: string, artifactName: string) => {
    setError(null);
    setBusy(true);
    docCounter += 1;
    const docId = `${leg.slice(0, 8)}-${productLine.toLowerCase()}-${Date.now()}-${docCounter}`;
    uploadRfpQuote(leg, docId, artifactName, productLine)
      .then(setChecklist)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setBusy(false));
  };

  return (
    <Container maxWidth="md" sx={{ py: 4 }}>
      <Stack spacing={2}>
        <Typography variant="h5">{TITLE}</Typography>
        {error ? <Typography color="error">{error}</Typography> : null}
        {checklist ? (
          <RfpChecklist checklist={checklist} onUpload={onUpload} busy={busy} />
        ) : error ? null : (
          <Typography color="text.secondary">Loading checklist…</Typography>
        )}
      </Stack>
    </Container>
  );
}

export function App() {
  const leg = legFromLocation();
  return leg ? <RfpLegApp leg={leg} /> : <AddressApp />;
}

function AddressApp() {
  const [session, setSession] = useState<Session | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function guard(fn: () => Promise<void>) {
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  const start = () =>
    guard(async () => {
      setSession(await startSession(PROCESS));
    });

  const onUpload = (artifactName: string) =>
    guard(async () => {
      if (!session) return;
      docCounter += 1;
      const view: A2uiView = await upload(session.sessionId, `doc-${docCounter}`, artifactName);
      setSession({ ...session, view });
    });

  const onResolve = (resolution: "accepted" | "rejected", message: string) =>
    guard(async () => {
      if (!session) return;
      const view = await resolveHitl(session.sessionId, resolution, message);
      setSession({ ...session, view });
    });

  return (
    <Container maxWidth="sm" sx={{ py: 4 }}>
      <Stack spacing={2}>
        <Typography variant="h5">{TITLE}</Typography>
        {error ? <Typography color="error">{error}</Typography> : null}
        {session ? (
          <Paper sx={{ p: 3 }}>
            <View view={session.view} onUpload={onUpload} onResolve={onResolve} />
          </Paper>
        ) : (
          <Box>
            <Button variant="contained" onClick={start}>
              Start address-proof
            </Button>
          </Box>
        )}
      </Stack>
    </Container>
  );
}
