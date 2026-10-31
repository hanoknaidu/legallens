import { defineConfig, devices } from "@playwright/test";

/**
 * A separate run from the test suite: this one produces the defect screenshots that go
 * with the report, rather than asserting anything.
 *
 * Kept out of `tests/` so a normal `npm test` never pays for it — it generates live
 * answers, which cost money and minutes.
 */
export default defineConfig({
  testDir: "./evidence",
  globalSetup: "./global-setup.ts",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 420_000,
  expect: { timeout: 20_000 },
  reporter: [["list"]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:5173",
    viewport: { width: 1600, height: 1000 },
    actionTimeout: 20_000,
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1600, height: 1000 } },
    },
  ],
});
