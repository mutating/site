import { expect, test } from "./fixtures";

for (const route of ["login", "register"]) {
  test(`${route} keeps the full-width artwork inside a vertically scrolling page`, async ({ page }) => {
    await page.goto(route);
    await page.setViewportSize({ width: 1440, height: 700 });
    const art = await page.locator(".network-art").boundingBox();
    expect(art).not.toBeNull();
    expect(art!.x).toBeCloseTo(0, 0);
    expect(art!.width).toBeCloseTo(1440, 0);
    expect(await page.evaluate(() => document.documentElement.scrollHeight)).toBeGreaterThan(700);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(1440);
  });

  test(`${route} keeps the hero and compact form on narrow screens`, async ({ page }) => {
    await page.goto(route);
    for (const width of [600, 540, 320]) {
      await page.setViewportSize({ width, height: 900 });
      await expect(page.locator(".auth-story")).toBeVisible();
      await expect(page.locator(".auth-story h1")).toBeVisible();
      await expect(page.locator(".network-art")).toBeHidden();
      const panel = page.locator(".auth-panel");
      await expect(panel).toBeVisible();
      const box = await panel.boundingBox();
      expect(box).not.toBeNull();
      expect(box!.x).toBeGreaterThanOrEqual(0);
      expect(box!.x + box!.width).toBeLessThanOrEqual(width);
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    }
  });

  test(`${route} follows the shared header container`, async ({ page }) => {
    await page.goto(route);
    await expect(page.locator(".auth-shell")).toBeVisible();
    // Tablets and narrow laptops keep the full composition; only phones use the fallback.
    for (const width of [601, 712, 768, 892, 1000, 1120, 1440, 1920, 2560]) {
      await page.setViewportSize({ width, height: 1000 });
      const header = await page.getByRole("banner").boundingBox();
      const main = await page.locator(".auth-shell").boundingBox();
      expect(header).not.toBeNull();
      expect(main).not.toBeNull();
      // The auth layout must use the same centered shell as the site header
      // instead of adding its own wider side insets.
      expect(main!.x).toBeCloseTo(header!.x, 0);
      expect(main!.x + main!.width).toBeCloseTo(header!.x + header!.width, 0);
      const centre = main!.x + main!.width / 2;
      const stage = await page.locator(".auth-stage").boundingBox();
      const panel = await page.locator(".auth-panel").boundingBox();
      const heading = await page.locator(".auth-story h1").boundingBox();
      expect(stage).not.toBeNull();
      expect(panel).not.toBeNull();
      expect(heading).not.toBeNull();
      await expect(page.locator(".network-art")).toBeVisible();
      const art = await page.locator(".network-art").boundingBox();
      expect(art).not.toBeNull();
      expect(art!.x).toBeCloseTo(0, 0);
      expect(art!.width).toBeCloseTo(width, 0);
      expect(await page.locator(".network-label").first().evaluate((element) => getComputedStyle(element).fontSize)).toBe("9px");
      const node = await page.locator(".network-node").first().boundingBox();
      expect(node).not.toBeNull();
      expect(node!.width).toBeCloseTo(16, 0);
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
      // Keep the artwork longer than its small labels: the labels start
      // colliding with the form before the decorated composition stops fitting.
      if (width < 768) await expect(page.locator(".network-labels")).toBeHidden();
      else await expect(page.locator(".network-labels")).toBeVisible();
      // The approved composition centres the hero, artwork and form on one axis.
      expect(heading!.x + heading!.width / 2).toBeCloseTo(centre, 0);
      expect(panel!.x + panel!.width / 2).toBeCloseTo(stage!.x + stage!.width / 2, 0);
    }
  });
}
