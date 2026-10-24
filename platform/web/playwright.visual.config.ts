import { defineConfig, devices } from "@playwright/test";
import { resolve } from "node:path";
import functional from "./playwright.config";

// Snapshots are produced and compared in the same pinned Linux container, including fonts.
export default defineConfig({
  ...functional,
  testMatch: "**/visual.spec.ts",
  testIgnore: [],
  workers: 2,
  timeout: 90_000,
  updateSnapshots: "none",
  outputDir: "test-results/visual",
  snapshotPathTemplate: "{testDir}/visual-snapshots/{projectName}/{arg}{ext}",
  reporter: [
    ["list"],
    ["html", { outputFolder: "playwright-report/visual", open: "never" }],
  ],
  expect: {
    timeout: 10_000,
    toHaveScreenshot: {
      stylePath: resolve("../../tests/platform/web/e2e/visual-stable.css"),
      animations: "disabled",
      caret: "hide",
      scale: "css",
      threshold: 0.15,
      maxDiffPixels: 100,
    },
  },
  use: { ...functional.use, timezoneId: "UTC", reducedMotion: "reduce" },
  projects: ["ru", "en"].flatMap((language) =>
    ["dark", "light"].flatMap((theme) =>
      ["desktop", "mobile", "tablet-portrait", "tablet-landscape"].map(
        (size) => ({
          name: `${language}-${theme}-${size}`,
          metadata: { language, theme },
          ...(size.startsWith("tablet")
            ? { grep: /owner-aware API token|Actions repository/ }
            : {}),
          use: {
            ...(size === "desktop"
              ? {
                  ...devices["Desktop Chrome"],
                  viewport: { width: 1440, height: 1000 },
                }
              : size === "mobile"
                ? devices["Pixel 7"]
                : {
                    ...devices["iPad (gen 7)"],
                    defaultBrowserType: "chromium",
                    viewport:
                      size === "tablet-portrait"
                        ? { width: 768, height: 1024 }
                        : { width: 1180, height: 820 },
                  }),
            locale: language === "ru" ? "ru-RU" : "en-US",
          },
        }),
      ),
    ),
  ),
});
