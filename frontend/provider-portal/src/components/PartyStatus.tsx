// Party-status view: accepted vs sent-but-not-accepted (rejected/escalated) documents,
// each showing its `reasonCode`/`message` VERBATIM, plus any outstanding requirements.
// The frontend arranges + labels; it never rewrites an agent explanation (A2UI-1).

import { Chip, Divider, List, ListItem, ListItemText, Stack, Typography } from "@mui/material";
import type { A2uiItem, A2uiView } from "../domain/a2ui";

function ItemLine({ item }: { item: A2uiItem }) {
  return (
    <ListItem alignItems="flex-start">
      <ListItemText
        primary={
          <Stack direction="row" spacing={1} alignItems="center">
            <span>
              {item.docId} · {item.doctype}
            </span>
            <Chip size="small" label={item.disposition} />
            {item.reasonCode ? (
              <Chip size="small" variant="outlined" label={item.reasonCode} />
            ) : null}
          </Stack>
        }
        // message relayed verbatim.
        secondary={item.message ?? undefined}
      />
    </ListItem>
  );
}

export function PartyStatus({ view }: { view: A2uiView }) {
  return (
    <Stack spacing={2}>
      <Typography variant="h6">Document status</Typography>

      {view.accepted.length ? (
        <>
          <Typography variant="subtitle2">Accepted</Typography>
          <List dense>
            {view.accepted.map((item) => (
              <ItemLine key={item.docId} item={item} />
            ))}
          </List>
        </>
      ) : null}

      {view.rejected.length ? (
        <>
          <Divider />
          <Typography variant="subtitle2">Not yet accepted</Typography>
          <List dense>
            {view.rejected.map((item) => (
              <ItemLine key={item.docId} item={item} />
            ))}
          </List>
        </>
      ) : null}

      {view.outstanding.length ? (
        <>
          <Divider />
          <Typography variant="subtitle2">Outstanding</Typography>
          <List dense>
            {view.outstanding.map((req, i) => (
              <ListItem key={i}>
                <ListItemText primary={req.item} secondary={req.message ?? undefined} />
              </ListItem>
            ))}
          </List>
        </>
      ) : null}
    </Stack>
  );
}
