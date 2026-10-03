import { defineConfig } from "@playwright/test";

// P6-5 phase-gate e2e. Runs the BUILT presenter stage via `vite preview`; the spec stubs the
// time-warp REST at the network boundary (`page.route`), so NO Python host and NO sibling
// apps need to be running (gotcha #9, design/frontend.md watch-item). Deterministic and
// small — the phase-gate e2e, not an exhaustive suite.

const PORT = 4317;

export default defineConfig({
  testDir: ".",
  timeout: 30_000,
  use: {
    baseURL: `http://localhost:${PORT}`,
    headless: true,
  },
  webServer: {
    command: `pnpm --filter timewarp build && pnpm --filter timewarp preview --port ${PORT} --strictPort`,
    port: PORT,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
    cwd: "../..",
  },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
});
