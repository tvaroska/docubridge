// Ops Dashboard App (P6-4) — the internal-zone operator read-model.
//
// Wires ExchangeList + LedgerView + HitlQueue + EscalationQueue and subscribes to the SSE
// stream (`subscribeEvents`), re-rendering on each snapshot. The dashboard is a
// read-model over the shared PortalStore (the P6-2 Path-B session store); it renders the
// contract LedgerEntry/OpsExchange directly (SSE, not A2UI). RFP rows (S0-frontend-1) also
// render as a carriers x product-lines matrix with a manual "Chase now" (RfpExchangeView).

import { Alert, Container, Grid2, Stack, Typography } from "@mui/material";
import { useEffect, useState } from "react";
import * as api from "./api";
import { EscalationQueue } from "./components/EscalationQueue";
import { ExchangeList } from "./components/ExchangeList";
import { HitlQueue } from "./components/HitlQueue";
import { LedgerView } from "./components/LedgerView";
import { ProgramComparison } from "./components/ProgramComparison";
import { RfpExchangeView } from "./components/RfpExchangeView";
import type { OpsExchange, Snapshot } from "./domain/ops";

const PORTAL_APP_URL: string =
  (import.meta.env.VITE_PORTAL_APP_URL as string | undefined) ?? "http://localhost:5174";

export function App() {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const [chasing, setChasing] = useState(false);
  const [chaseNote, setChaseNote] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    const unsubscribe = api.subscribeEvents((snap) => {
      setSnapshot(snap);
    });
    return unsubscribe;
  }, []);

  const handleResolved = () => {
    // Re-fetch the snapshot (the SSE stream will update it anyway, but this is immediate).
    // In a real app, we'd rely on the SSE stream alone; this is a belt-and-suspenders.
  };

  // Manual "chase now": the SSE stream refreshes the matrix; surface the outcome / error.
  const handleChase = (rfpId: string, party: string | null) => {
    setChasing(true);
    api
      .chaseRfp(rfpId, party)
      .then((res) => {
        const who = res.chased.map((c) => `${c.party}${c.resent ? " (already sent)" : ""}`);
        setChaseNote({ ok: true, text: `Chased ${who.join(", ")} on ${rfpId}.` });
      })
      .catch((err: unknown) => {
        setChaseNote({ ok: false, text: err instanceof Error ? err.message : String(err) });
      })
      .finally(() => setChasing(false));
  };

  const rfpRows = snapshot ? snapshot.exchanges.filter((ex) => ex.rfp) : [];

  const selectedExchange: OpsExchange | null = snapshot
    ? snapshot.exchanges.find((ex) => ex.sessionId === selectedSessionId) || null
    : null;

  return (
    <Container maxWidth="xl" sx={{ py: 4 }}>
      <Typography variant="h4" gutterBottom>
        Ops Dashboard
      </Typography>
      <Grid2 container spacing={2}>
        <Grid2 size={{ xs: 12, md: 4 }}>
          <Typography variant="h6" gutterBottom>
            Exchanges
          </Typography>
          {snapshot ? (
            <ExchangeList
              exchanges={snapshot.exchanges}
              selectedId={selectedSessionId}
              onSelect={setSelectedSessionId}
            />
          ) : (
            <Typography variant="body2" color="text.secondary">
              Connecting to SSE stream...
            </Typography>
          )}
        </Grid2>
        <Grid2 size={{ xs: 12, md: 8 }}>
          <Typography variant="h6" gutterBottom>
            Ledger
          </Typography>
          <LedgerView exchange={selectedExchange} />
        </Grid2>
        <Grid2 size={{ xs: 12, md: 6 }}>
          {snapshot ? (
            <HitlQueue queue={snapshot.hitl} onResolved={handleResolved} />
          ) : null}
        </Grid2>
        <Grid2 size={{ xs: 12, md: 6 }}>
          {snapshot ? <EscalationQueue queue={snapshot.escalation} /> : null}
        </Grid2>
        {rfpRows.length > 0 ? (
          <Grid2 size={{ xs: 12 }}>
            <Typography variant="h6" gutterBottom>
              RFP exchanges
            </Typography>
            <Stack spacing={2}>
              {chaseNote ? (
                <Alert severity={chaseNote.ok ? "success" : "error"} onClose={() => setChaseNote(null)}>
                  {chaseNote.text}
                </Alert>
              ) : null}
              {rfpRows.map((ex) =>
                ex.rfp ? (
                  <RfpExchangeView
                    key={ex.sessionId}
                    rfp={ex.rfp}
                    onChase={handleChase}
                    busy={chasing}
                    portalAppUrl={PORTAL_APP_URL}
                  />
                ) : null,
              )}
            </Stack>
          </Grid2>
        ) : null}
        <Grid2 size={{ xs: 12 }}>
          {snapshot ? <ProgramComparison programs={snapshot.programs} /> : null}
        </Grid2>
      </Grid2>
    </Container>
  );
}
