// MUI theme for the Agent Console — the shared factory (P6-5).
//
// The console_server sends content + protocol only (the contract ExchangeTurn + a
// status/rounds/satisfied projection — no pixels); THIS app owns 100% of the pixels. The
// theme (plus Panel/Badges/domain) now lives in @docubridge/shared; the per-zone accent
// (#6f42c1, internal-zone purple, distinct from the portal's blue) is passed to the shared
// makeTheme factory (§2d).

import { makeTheme } from "@docubridge/shared";

export const theme = makeTheme("#6f42c1");
