import { defineConfig } from "@playwright/test";

// Phase 2e post-acceptance audit, Task 2: the smallest practical browser
// test for the real workflow. Runs entirely against the real frontend
// build (npm run dev) with EVERY /api/** request intercepted and answered
// from hand-built fixtures (see e2e/fixtures.js) -- no real backend, no
// real database, read or write, is ever touched. That's not a shortcut:
// it's what "reproducible test environment with safe test data" and "do
// not mutate production business records" actually require here, since
// the real backend's reporting DB is the customer's live Odoo data and
// its app DB is real case/action state a manager may be relying on.
export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  fullyParallel: false,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: "http://localhost:5183",
    trace: "retain-on-failure",
  },
  webServer: {
    command: "npm run dev -- --port 5183 --strictPort",
    url: "http://localhost:5183",
    reuseExistingServer: false,
    timeout: 60_000,
  },
});
