// Panel — a titled MUI Paper wrapper (@docbride/shared, P6-5).
//
// A thin layout primitive the apps share for "a titled card of content". It ARRANGES +
// LABELS only (a title + a padded Paper); it never authors or rewrites any agent content
// (A2UI-1 content-not-pixels) — callers pass the relayed reasonCode/message as children.

import { Paper, Typography } from "@mui/material";
import type { ReactNode } from "react";

export interface PanelProps {
  title?: ReactNode;
  children: ReactNode;
  /** Optional test id for e2e / component assertions. */
  "data-testid"?: string;
}

export function Panel({ title, children, ...rest }: PanelProps) {
  return (
    <Paper sx={{ p: 2 }} data-testid={rest["data-testid"]}>
      {title !== undefined ? (
        <Typography variant="h6" gutterBottom>
          {title}
        </Typography>
      ) : null}
      {children}
    </Paper>
  );
}
