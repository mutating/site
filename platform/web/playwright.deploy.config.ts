import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "../../tests/platform/web/deploy",
  workers: 1,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  outputDir: "test-results/deploy",
  reporter: [
    ["list"],
    ["html", { outputFolder: "playwright-report/deploy", open: "never" }],
  ],
  use: {
    ...devices["Desktop Chrome"],
    baseURL: "https://127.0.0.1:18443/",
    ignoreHTTPSErrors: true,
    locale: "ru-RU",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    launchOptions: {
      executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH,
    },
  },
});
