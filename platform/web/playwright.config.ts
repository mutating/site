import { defineConfig, devices } from "@playwright/test";
import { resolve } from "node:path";
const runtime = resolve(".runtime", `playwright-${process.pid}`);
export default defineConfig({
  testDir: "../../tests/platform/web/e2e",
  testIgnore: ["**/visual.spec.ts"],
  outputDir: "test-results/e2e",
  fullyParallel: true,
  workers: 2,
  timeout: 45_000,
  expect: { timeout: 10_000 },
  reporter: [
    ["list"],
    ["html", { outputFolder: "playwright-report/e2e", open: "never" }],
  ],
  use: {
    baseURL: "http://127.0.0.1:18101/",
    locale: "ru-RU",
    colorScheme: "dark",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "desktop",
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: 1440, height: 1000 },
        launchOptions: {
          executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH,
        },
      },
    },
    {
      name: "mobile",
      use: {
        ...devices["Pixel 7"],
        defaultBrowserType: "chromium",
        launchOptions: {
          executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH,
        },
      },
    },
    {
      name: "firefox",
      use: {
        ...devices["Desktop Firefox"],
        viewport: { width: 1440, height: 1000 },
      },
    },
    {
      name: "webkit",
      use: {
        ...devices["Desktop Safari"],
        viewport: { width: 1440, height: 1000 },
      },
    },
    { name: "iphone", use: { ...devices["iPhone 13"] } },
  ],
  webServer: [
    {
      command:
        "../api/.venv/bin/python -m throng_api.cli migrate && ../api/.venv/bin/python -m uvicorn api_app:app --app-dir ../../tests/platform/web/e2e --host 127.0.0.1 --port 18100",
      url: "http://127.0.0.1:18100/health/ready",
      reuseExistingServer: false,
      timeout: 30_000,
      env: {
        THRONG_DATABASE_URL: `sqlite+aiosqlite:///${runtime}/api.db`,
        THRONG_STORAGE_PATH: `${runtime}/artifacts`,
        THRONG_AGENT_LEASE_SECONDS: "3600",
        THRONG_ALLOW_REGISTRATION: "true",
      },
    },
    {
      command:
        ".venv/bin/python -m uvicorn server.app:app --host 127.0.0.1 --port 18101",
      url: "http://127.0.0.1:18101/login",
      reuseExistingServer: false,
      timeout: 30_000,
      env: {
        DASHBOARD_API_URL: "http://127.0.0.1:18100",
        DASHBOARD_PUBLIC_ORIGIN: "http://127.0.0.1:18101",
        DASHBOARD_SECURE_COOKIE: "false",
        DASHBOARD_SESSION_KEY: Buffer.alloc(32, 7).toString("base64"),
      },
    },
  ],
});
