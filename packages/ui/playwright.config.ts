import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "../../tests/ui",
  testMatch: "**/*.spec.ts",
  outputDir: "test-results",
  snapshotPathTemplate: "{testDir}/../../packages/ui/.runtime/pixel-expected/{projectName}/{testFilePath}/{testName}/{arg}{ext}",
  fullyParallel: true,
  workers: 2,
  use: {
    baseURL: "http://127.0.0.1:18120",
    colorScheme: "dark",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "desktop", testIgnore: "**/header-parity.spec.ts", use: { viewport: { width: 1440, height: 1000 } } },
    { name: "mobile", testIgnore: "**/header-parity.spec.ts", use: { viewport: { width: 360, height: 800 }, isMobile: true, hasTouch: true } },
    { name: "header-parity", testMatch: "**/header-parity.spec.ts", use: { browserName: "firefox", viewport: { width: 1440, height: 1000 } } },
  ],
  webServer: {
    command: "npm run dev",
    url: "http://127.0.0.1:18120",
    reuseExistingServer: false,
  },
});
