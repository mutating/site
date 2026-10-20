import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: ".",
  testMatch: "screenshots.spec.ts",
  outputDir: "test-results",
  fullyParallel: true,
  workers: process.env.CI ? 2 : 4,
  timeout: 180_000,
  use: { browserName: "firefox", baseURL: "http://127.0.0.1:18121", viewport: { width: 1440, height: 1000 }, trace: "off" },
  projects: [
    { name: "firefox", grepInvert: /Chromium high contrast/ },
    { name: "chromium-high-contrast", grep: /Chromium high contrast/, use: { browserName: "chromium" } },
  ],
  webServer: { command: "node tests/site/header/server.mjs", cwd: "../../..", url: "http://127.0.0.1:18121", reuseExistingServer: false },
});
