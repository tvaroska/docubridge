// View switch (content-not-pixels).
//
// The A2UI host sends CONTENT + PROTOCOL only — the declarative view kind, the ledger
// items, the outstanding requirements, and each agent `reasonCode`/`message` (relayed
// verbatim). THIS app owns 100% of the pixels (MUI). The components never author or
// rewrite an agent explanation; they only arrange + label (the A2UI-1 relay invariant).
//
// P6-5 hoists the shared theme/domain/Panel/Badges into a shared workspace package;
// P6-2's portal is self-contained (§2f).

import { Typography } from "@mui/material";
import type { A2uiView } from "../domain/a2ui";
import { HitlReview } from "./HitlReview";
import { PartyStatus } from "./PartyStatus";
import { UploadPrompt } from "./UploadPrompt";

export function View({
  view,
  onUpload,
  onResolve,
}: {
  view: A2uiView;
  onUpload: (artifactName: string) => void;
  onResolve: (resolution: "accepted" | "rejected", message: string) => void;
}) {
  switch (view.kind) {
    case "upload_prompt":
      return <UploadPrompt view={view} onUpload={onUpload} />;
    case "hitl_review":
      return <HitlReview view={view} onResolve={onResolve} />;
    case "party_status":
      return <PartyStatus view={view} />;
    default:
      return <Typography>Unknown view.</Typography>;
  }
}
