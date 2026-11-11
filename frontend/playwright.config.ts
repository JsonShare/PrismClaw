import { defineConfig, devices } from "@playwright/test";

// PrismClaw E2E configuration.
//
// Runs against the locally built SPA served by `vite preview` (port 4173).
// The backend API must be reachable at the configured origin for full
// login→dashboard→logout flows; the smoke spec below only asserts the SPA
// shell renders and the auth form is present, so it stays green without live
// credentials. Run with: `npm run test:e2e` (after `npm run build`).
export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL: process.env.E2E_BASE_URL || "http://localhost:4173",
    trace: "on-first-retry",
  },
  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        // Run the full Chromium build (shipped by `npx playwright install chromium`)
        // instead of chromium-headless-shell, so E2E does not depend on a second
        // browser download / version match and stays closer to what users run.
        channel: "chromium",
      },
    },
  ],
  webServer: {
    command: "npm run build && npm run preview -- --port 4173",
    url: "http://localhost:4173",
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
