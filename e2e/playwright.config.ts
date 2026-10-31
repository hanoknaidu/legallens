import { defineConfig, devices } from "@playwright/test";

/**
 * End-to-end tests that drive the real UI against the real backend.
 *
 * There is no `webServer` block on purpose. The backend's provider is chosen by
 * environment variable, and a config that started it would pin that choice — these
 * tests are meant to run against whichever provider is under test. Both servers are
 * started by hand (see README.md in this folder) and `global-setup.ts` fails fast
 * with instructions if either is missing.
 */
export default defineConfig({
  testDir: "./tests",
  globalSetup: "./global-setup.ts",

  // Serial, one worker. The backend is deliberately single-process (one SQLite
  // writer, an in-process pending-turn registry, a per-process rate limit), and the
  // conversation list is shared mutable state that several specs assert on.
  fullyParallel: false,
  workers: 1,
  retries: 0,

  // A real generation can take a couple of minutes; a draft of 16K output tokens is
  // the worst case. Individual waits set their own shorter budgets.
  timeout: 420_000,
  expect: { timeout: 20_000 },

  reporter: [["list"], ["html", { open: "never", outputFolder: "playwright-report" }]],

  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:5173",
    // Wide enough that the sidebar is pinned (>=1024px) and the document panel docks
    // beside the thread (>=1440px) rather than taking over as an overlay.
    viewport: { width: 1600, height: 1000 },
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "off",
    actionTimeout: 20_000,
  },

  projects: [
    {
      name: "chromium",
      // The viewport comes last on purpose: `devices["Desktop Chrome"]` is 1280 wide,
      // which is below the 1440px breakpoint where the document panel docks beside the
      // thread. On a 1280 viewport the panel is a modal overlay instead, and the tests
      // would be exercising the compact layout without saying so.
      use: { ...devices["Desktop Chrome"], viewport: { width: 1600, height: 1000 } },
    },
  ],
});
