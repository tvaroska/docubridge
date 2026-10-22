// Split-Screen Theater (presenter stage, P6-5, §2g).
//
// Two columns split by a labelled "Gateway" boundary: the LEFT is the external zone (the
// provider-portal), the RIGHT is the internal zone (agent-console + ops-dashboard stacked).
// The sibling apps are embedded by IFRAME at their dev URLs (from Vite env); when a URL is
// unset the column renders a labelled PLACEHOLDER panel, so the stage builds/tests standalone
// without the siblings running (gotcha #8). No cross-app bundling — real byte streaming
// between the four apps is out of scope.

import { Box, Typography } from "@mui/material";
import { Panel } from "@docubridge/shared";

const PORTAL_URL = import.meta.env.VITE_PORTAL_APP_URL;
const CONSOLE_URL = import.meta.env.VITE_CONSOLE_APP_URL;
const DASHBOARD_URL = import.meta.env.VITE_DASHBOARD_APP_URL;

function Embed({ url, title }: { url: string | undefined; title: string }) {
  if (!url) {
    return (
      <Panel title={title}>
        <Typography variant="body2" color="text.secondary" data-testid="placeholder">
          {title} not connected — set its VITE_*_APP_URL to embed the live app.
        </Typography>
      </Panel>
    );
  }
  return (
    <Box sx={{ height: "100%", minHeight: 240 }}>
      <Typography variant="overline">{title}</Typography>
      <iframe
        title={title}
        src={url}
        style={{ width: "100%", height: "100%", minHeight: 240, border: "1px solid #ddd", borderRadius: 8 }}
      />
    </Box>
  );
}

export function SplitScreen() {
  return (
    <Box
      sx={{
        display: "grid",
        gridTemplateColumns: "1fr auto 1fr",
        gap: 2,
        alignItems: "stretch",
      }}
    >
      {/* External zone (left) */}
      <Box data-testid="external-zone">
        <Typography variant="h6" gutterBottom>
          External zone
        </Typography>
        <Embed url={PORTAL_URL} title="Provider Portal" />
      </Box>

      {/* The Gateway boundary between the zones (a labelled vertical divider). */}
      <Box
        data-testid="gateway-boundary"
        sx={{
          width: 40,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          borderLeft: "2px dashed",
          borderRight: "2px dashed",
          borderColor: "primary.main",
        }}
      >
        <Typography
          variant="caption"
          sx={{ writingMode: "vertical-rl", transform: "rotate(180deg)", fontWeight: 700 }}
        >
          Gateway
        </Typography>
      </Box>

      {/* Internal zone (right): console + dashboard stacked */}
      <Box data-testid="internal-zone">
        <Typography variant="h6" gutterBottom>
          Internal zone
        </Typography>
        <Box sx={{ display: "flex", flexDirection: "column", gap: 2 }}>
          <Embed url={CONSOLE_URL} title="Agent Console" />
          <Embed url={DASHBOARD_URL} title="Ops Dashboard" />
        </Box>
      </Box>
    </Box>
  );
}
