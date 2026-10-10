import { defineConfig, devices } from "@playwright/test";

/**
 * End-to-end tests run against an already running stack (`make up`), exactly as
 * a user would reach it. Override the target with NIMBRIX_E2E_BASE_URL.
 */
export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: process.env.NIMBRIX_E2E_BASE_URL ?? "http://127.0.0.1:3200",
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
