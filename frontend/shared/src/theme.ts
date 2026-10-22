// Shared MUI theme factory (@docubridge/shared, P6-5).
//
// The four apps (portal, console, dashboard, timewarp) share the SAME theme shape
// (borderRadius, success/warning) but each carries a DIFFERENT primary accent by design —
// zone identity (portal #1f6feb, console #6f42c1, dashboard #1a73e8, timewarp #0b7285). A
// single shared `theme` constant would erase that, so this is a FACTORY, not a constant
// (§2d): each app calls `makeTheme(<its accent>)`.

import { createTheme } from "@mui/material/styles";
import type { Theme } from "@mui/material/styles";

/** Build the shared app theme with the given primary accent (zone identity). */
export function makeTheme(primaryMain: string): Theme {
  return createTheme({
    palette: {
      mode: "light",
      primary: { main: primaryMain },
      success: { main: "#1a7f37" },
      warning: { main: "#bf8700" },
    },
    shape: { borderRadius: 10 },
  });
}
