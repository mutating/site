import { defineConfig } from "@playwright/test";
import functional from "./playwright.config";

export default defineConfig({
  ...functional,
  testMatch: "**/header-shared.spec.ts",
  testIgnore: [],
  outputDir: "test-results/header-shared",
  reporter: [["list"], ["html", { outputFolder: "playwright-report/header-shared", open: "never" }]],
  webServer: [
    ...(Array.isArray(functional.webServer) ? functional.webServer : []),
    {
      command: "node tests/site/header/server.mjs",
      cwd: "../..",
      url: "http://127.0.0.1:18121",
      timeout: 30_000,
      reuseExistingServer: false,
    },
  ],
});
