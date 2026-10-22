// MUI theme for the presenter stage — the shared factory (P6-5).
//
// The time-warp stage carries its OWN accent (#0b7285, stage teal — distinct from portal
// blue / console purple / dashboard red) via the shared makeTheme factory (§2d).

import { makeTheme } from "@docubridge/shared";

export const theme = makeTheme("#0b7285");
