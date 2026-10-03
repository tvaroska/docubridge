// Carrier RFP checklist (S0-frontend-1): one carrier's product lines on one RFP.
//
// Header: RFP id, due date, the carrier's lifecycle badge, and — when the host knows it —
// the reply address + subject tag to email a quote to. One row per product line: its
// RfpStatusBadge, outstanding emphasis, the gate's reasonCode/message + field reviews
// (`path · reason · detail`) VERBATIM (A2UI-1 — the frontend never authors an explanation),
// and for an outstanding row an artifact picker + Upload button.
//
// Local demo: the picker offers benefits conformance corpus ids the fixture extractor
// resolves (P7 swaps this for a real byte upload). Props-driven (no fetch).

import {
  Alert,
  Box,
  Button,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { LifecycleBadge, Panel, RfpStatusBadge } from "@docbride/shared";
import { useState } from "react";
import type { RfpChecklist as Checklist, RfpChecklistItem } from "../domain/rfp";

// Benefits conformance corpus ids the local fixture extractor understands
// (wiki/evals/benefits/conformance.json).
export const QUOTE_ARTIFACTS = [
  "benefits-conformance-ci-usd",
  "benefits-conformance-hi-usd-flagged",
];

export type UploadHandler = (productLine: string, artifactName: string) => void;

function ItemRow({
  item,
  canUpload,
  busy,
  onUpload,
}: {
  item: RfpChecklistItem;
  canUpload: boolean;
  busy: boolean;
  onUpload: UploadHandler;
}) {
  const [artifact, setArtifact] = useState(QUOTE_ARTIFACTS[0]);
  return (
    <Box
      data-testid={`rfp-item-${item.productLine}`}
      data-outstanding={item.outstanding ? "true" : "false"}
      sx={{
        p: 1.5,
        borderRadius: 1,
        border: 1,
        borderColor: item.outstanding ? "warning.main" : "divider",
      }}
    >
      <Stack spacing={1}>
        <Stack direction="row" spacing={1} alignItems="center">
          <Typography variant="subtitle1" sx={{ fontWeight: item.outstanding ? 700 : 400 }}>
            {item.productLine}
          </Typography>
          <RfpStatusBadge value={item.status} />
          <Typography variant="caption" color="text.secondary">
            {item.versionCount} version{item.versionCount === 1 ? "" : "s"}
          </Typography>
        </Stack>
        {item.reasonCode || item.message ? (
          <Alert severity={item.outstanding ? "warning" : "info"}>
            {/* reasonCode + message relayed verbatim from the gate. */}
            {item.reasonCode ? <strong>{item.reasonCode}</strong> : null}
            {item.message ? <Box>{item.message}</Box> : null}
            {item.fieldReviews.map((fr) => (
              <Box key={fr.path} component="div" sx={{ fontFamily: "monospace" }}>
                {[fr.path, fr.reason, fr.detail].filter(Boolean).join(" · ")}
              </Box>
            ))}
          </Alert>
        ) : null}
        {item.outstanding && canUpload ? (
          <Stack direction="row" spacing={1} alignItems="center">
            <TextField
              select
              size="small"
              label={`Quote for ${item.productLine}`}
              value={artifact}
              onChange={(e) => setArtifact(e.target.value)}
              sx={{ minWidth: 280 }}
            >
              {QUOTE_ARTIFACTS.map((id) => (
                <MenuItem key={id} value={id}>
                  {id}
                </MenuItem>
              ))}
            </TextField>
            <Button
              variant="contained"
              disabled={busy}
              aria-label={`Upload ${item.productLine}`}
              onClick={() => onUpload(item.productLine, artifact)}
            >
              Upload
            </Button>
          </Stack>
        ) : null}
      </Stack>
    </Box>
  );
}

export function RfpChecklist({
  checklist,
  onUpload,
  busy = false,
}: {
  checklist: Checklist;
  onUpload: UploadHandler;
  busy?: boolean;
}) {
  const canUpload = checklist.actions.some((a) => a.kind === "upload");
  return (
    <Panel title={`RFP ${checklist.rfpId}`} data-testid="rfp-checklist">
      <Stack spacing={2}>
        <Stack direction="row" spacing={1} alignItems="center">
          <Typography variant="body2">
            {checklist.party} · due {checklist.dueAt}
          </Typography>
          <LifecycleBadge value={checklist.lifecycle} />
        </Stack>
        {checklist.replyAddress ? (
          <Typography variant="body2" color="text.secondary" data-testid="rfp-reply">
            Or email your quote to <strong>{checklist.replyAddress}</strong>
            {checklist.replySubjectTag
              ? ` with subject tag ${checklist.replySubjectTag}`
              : null}
            .
          </Typography>
        ) : null}
        {checklist.items.map((item) => (
          <ItemRow
            key={item.productLine}
            item={item}
            canUpload={canUpload}
            busy={busy}
            onUpload={onUpload}
          />
        ))}
      </Stack>
    </Panel>
  );
}
