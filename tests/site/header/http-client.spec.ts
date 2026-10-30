import { expect, test } from "@playwright/test";

/** The built bundle must retry the SVG URL and mount its actual content; checking only a visible SVG missed an empty-placeholder mutation. */
test("pantograph recovers a failed SVG request through reboop", async ({ page }) => {
  let attempts = 0;
  await page.route("**/pantograph-variant-20-clean-v2.svg", async route => {
    if (++attempts === 1) await route.abort("failed");
    else await route.continue();
  });
  await page.goto("/");
  const stage = page.locator(".pantograph-visual__object");
  await expect(stage.locator("svg#pantographArtwork")).toBeVisible();
  await expect(stage.locator("svg")).toHaveAttribute("viewBox", "0 0 1536 885");
  expect(attempts).toBe(2);
  await expect(stage).not.toHaveAttribute("data-pantograph-error");
});

/** A received 404 must report the asset failure once rather than retrying forever or inserting an invalid SVG. */
test("pantograph reports an HTTP asset failure without retrying", async ({ page }) => {
  let attempts = 0;
  await page.route("**/pantograph-variant-20-clean-v2.svg", async route => {
    ++attempts;
    await route.fulfill({ status: 404, contentType: "text/plain", body: "Missing" });
  });
  await page.goto("/");
  const stage = page.locator(".pantograph-visual__object");
  await expect(stage).toHaveAttribute("data-pantograph-error", /404/);
  await expect(stage.locator("svg")).toHaveCount(0);
  expect(attempts).toBe(1);
});
