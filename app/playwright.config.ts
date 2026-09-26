import { defineConfig, devices } from "@playwright/test";

// End-to-end checks against the mock API (dev/mock-nora.mjs) and the dev
// sign-in. `npm run e2e` starts both unless they are already running
// (reuseExistingServer), so it works beside a dev session too.
const PORT = Number(process.env.E2E_PORT ?? 3400);

export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: `http://localhost:${PORT}`,
    trace: "retain-on-failure",
  },
  projects: [
    { name: "phone", use: { ...devices["iPhone 13"], browserName: "chromium" } },
    { name: "desktop", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } },
  ],
  webServer: [
    {
      command: "node dev/mock-nora.mjs",
      url: "http://127.0.0.1:9199/app/v1/status",
      reuseExistingServer: true,
      env: { MOCK_LATENCY_MS: "40" },
    },
    {
      command: `npx next dev --port ${PORT}`,
      url: `http://localhost:${PORT}/en/login`,
      reuseExistingServer: true,
      timeout: 120_000,
    },
  ],
});
