// MUI theme for the Provider Portal — the shared factory (P6-5).
//
// The A2UI host sends content + protocol only (no pixels); THIS app owns 100% of the pixels.
// The theme (plus Panel/Badges/domain) now lives in @docubridge/shared; the per-zone accent
// (#1f6feb, external-zone blue) is passed to the shared makeTheme factory (§2d).

import { makeTheme } from "@docubridge/shared";

// Themeable per deployment (a consumer-branded portal): VITE_PORTAL_ACCENT overrides it.
export const theme = makeTheme(
  (import.meta.env.VITE_PORTAL_ACCENT as string | undefined) ?? "#1f6feb",
);
