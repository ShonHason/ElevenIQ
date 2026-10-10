import { existsSync } from "node:fs";
import { defineConfig, devices } from "@playwright/test";

// The tests run against tests/fixtures/web (ADR 005 §7), never data/. One smoke test runs on the
// full export too, when it exists (export/v1; `npm run local` builds it).
const FULL = existsSync(new URL("../export/v1/manifest.json", import.meta.url));
if (!FULL) console.log("full-export smoke test skipped: no export/v1 (npm run local builds it)");

export default defineConfig({
  testDir: "e2e",
  fullyParallel: true,
  retries: 0,
  reporter: [["list"]],
  use: { baseURL: "http://localhost:5175/", trace: "retain-on-failure", screenshot: "only-on-failure" },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] }, testIgnore: /full-export/ },
    { name: "phone", use: { ...devices["Pixel 7"] }, testIgnore: /full-export/ },
    ...(FULL ? [{ name: "full-export", testMatch: /full-export/,
                  use: { ...devices["Desktop Chrome"], baseURL: "http://localhost:5176/" } }] : []),
  ],
  webServer: [
    {
      command: "npx vite --port 5175 --strictPort",
      env: { ELEVENIQ_DATA: "../tests/fixtures/web" },
      url: "http://localhost:5175/",
      reuseExistingServer: false,
      timeout: 60_000,
    },
    ...(FULL ? [{
      command: "npx vite --port 5176 --strictPort",
      env: { ELEVENIQ_DATA: "../export/v1" },
      url: "http://localhost:5176/",
      reuseExistingServer: false,
      timeout: 60_000,
    }] : []),
  ],
});
