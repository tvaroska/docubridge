// Presenter-stage phase-gate e2e (P6-5, §4.4).
//
// The backend is STUBBED at the network boundary (`page.route`) — no Python time-warp host,
// no sibling apps. Asserts: (1) Split-Screen renders the external + internal zones and the
// drawn Gateway boundary; (2) a Fast-forward click issues the stubbed `advance` and drives the
// visible lifecycle badge to `escalated`.

import { expect, test } from "@playwright/test";

// The escalated state the stubbed host returns after a fast-forward.
const ESCALATED_STATE = {
  now: 3,
  paused: false,
  pending: [],
  exchanges: [
    {
      session_id: "sid-1",
      context_id: "ctx-abcdef01",
      lifecycle: "escalated",
      done: true,
      events: [
        { step: "overdue", tick: 1 },
        { step: "reminder", tick: 2 },
        { step: "escalated", tick: 3 },
      ],
    },
  ],
};

test.beforeEach(async ({ page }) => {
  // Stub the time-warp REST host at the network boundary (default VITE_TIMEWARP_URL :8004).
  await page.route("**/timewarp/**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(ESCALATED_STATE),
    });
  });
});

test("Split-Screen renders both zones + the Gateway boundary", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("external-zone")).toBeVisible();
  await expect(page.getByTestId("internal-zone")).toBeVisible();
  const gateway = page.getByTestId("gateway-boundary");
  await expect(gateway).toBeVisible();
  await expect(gateway).toContainText("Gateway");
});

test("Fast-forward drives the visible lifecycle to escalated", async ({ page }) => {
  await page.goto("/");

  // Before any advance, the control strip shows the base lifecycle.
  const badge = page.getByTestId("lifecycle-badge");
  await expect(badge).toHaveAttribute("data-lifecycle", "on_track");

  // Click Fast-forward → the stubbed advance returns the escalated state.
  await page.getByTestId("fast-forward-btn").click();

  await expect(badge).toHaveAttribute("data-lifecycle", "escalated");
  await expect(badge).toContainText("escalated");
});
