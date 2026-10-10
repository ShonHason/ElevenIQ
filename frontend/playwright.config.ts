import { defineConfig, devices } from "@playwright/test";

// Always against tests/fixtures/web (ADR 005 §7), never data/.
export default defineConfig({
  testDir: "e2e",
  fullyParallel: true,
  retries: 0,
  reporter: [["list"]],
  use: { baseURL: "http://localhost:5175/", trace: "retain-on-failure", screenshot: "only-on-failure" },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] } },
    { name: "phone", use: { ...devices["Pixel 7"] } },
  ],
  webServer: {
    command: "npm run data:fixture && npx vite --port 5175 --strictPort",
    url: "http://localhost:5175/",
    reuseExistingServer: false,
    timeout: 60_000,
  },
});
