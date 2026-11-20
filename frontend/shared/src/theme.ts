// Shared MUI theme factory (@docubridge/shared, P6-5).
//
// The four apps (portal, console, dashboard, timewarp) share the SAME theme shape
// (borderRadius, success/warning) but each carries a DIFFERENT primary accent by design —
// zone identity (portal #1f6feb, console #6f42c1, dashboard #1a73e8, timewarp #0b7285). A
// single shared `theme` constant would erase that, so this is a FACTORY, not a constant
// (§2d): each app calls `makeTheme(<its accent>)`.

import "@fontsource-variable/inter";
import "@fontsource-variable/jetbrains-mono";
import { createTheme } from "@mui/material/styles";
import type { Theme } from "@mui/material/styles";

const FONT_SANS =
  '"Google Sans", "Inter Variable", "Inter", system-ui, -apple-system, sans-serif';
const FONT_MONO =
  '"Roboto Mono", "JetBrains Mono Variable", "JetBrains Mono", ui-monospace, monospace';

/** Build the shared Tier-2 GM3 Light app theme with the given primary accent (zone identity). */
export function makeTheme(primaryMain: string): Theme {
  return createTheme({
    palette: {
      mode: "light",
      primary: { main: primaryMain },
      success: { main: "#1a7f37" },
      warning: { main: "#bf8700" },
      background: {
        default: "#f8f9fa",
        paper: "#ffffff",
      },
      text: {
        primary: "#202124",
        secondary: "#5f6368",
      },
      divider: "#dadce0",
    },
    typography: {
      fontFamily: FONT_SANS,
    },
    shape: { borderRadius: 10 },
    components: {
      MuiCssBaseline: {
        styleOverrides: `
          :root {
            --surface: #f8f9fa;
            --surface-container: #ffffff;
            --surface-container-low: #f1f3f4;
            --on-surface: #202124;
            --on-surface-variant: #5f6368;
            --outline-variant: #dadce0;
            --primary: ${primaryMain};
            --on-primary: #ffffff;
            --status-ok-fg: #137333;
            --status-ok-bg: #e6f4ea;
            --status-warn-fg: #b06000;
            --status-warn-bg: #fef7e0;
            --status-err-fg: #c5221f;
            --status-err-bg: #fce8e6;
            --status-info-fg: #1967d2;
            --status-info-bg: #e8f0fe;
            --font-sans: ${FONT_SANS};
            --font-mono: ${FONT_MONO};
            --radius: 0.5rem;
          }
          table, th, td, .MuiChip-root, .MuiTableCell-root {
            font-variant-numeric: tabular-nums;
          }
          @media (prefers-reduced-motion: reduce) {
            *, *::before, *::after {
              animation-duration: 0.01ms !important;
              animation-iteration-count: 1 !important;
              transition-duration: 0.01ms !important;
              scroll-behavior: auto !important;
            }
          }
        `,
      },
    },
  });
}
