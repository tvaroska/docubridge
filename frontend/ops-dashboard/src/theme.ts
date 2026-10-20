// MUI theme for the Ops Dashboard — the shared factory (P6-5).
//
// The dashboard host sends content + protocol only (OpsExchange projections + queues — no
// pixels); THIS app owns 100% of the pixels. The theme (plus Panel/Badges/domain) now lives
// in @docbride/shared; the per-zone accent (#1a73e8, Google Cloud Blue, distinct from
// console/portal) is passed to the shared makeTheme factory (§2d).

import { makeTheme } from "@docbride/shared";

export const theme = makeTheme("#1a73e8");
