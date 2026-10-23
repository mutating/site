import { test, expect, type Page } from "@playwright/test";
import { prepare, selector, capture, compare } from "./fixture";

// Every header breakpoint and its neighbor, plus common phones and wide screens.
const widths = [280, 320, 360, 375, 380, 381, 390, 480, 481, 580, 581, 768,
  860, 861, 920, 921, 1024, 1040, 1041, 1120, 1121, 1240, 1241, 1440, 1920, 2560];
const destination = "https://platform.mutating.tech/";

async function buttonStates(page: Page) {
  const button = page.locator(await selector(page, "platform-link"));
  const pictures = [await capture(page, "button-rest")];
  await button.hover();
  pictures.push(await capture(page, "button-hover"));
  await page.mouse.down();
  pictures.push(await capture(page, "button-active"));
  // Release away from the link to avoid navigation.
  await page.mouse.move(1, 999);
  await page.mouse.up();
  await page.keyboard.press("Tab");
  await button.focus();
  pictures.push(await capture(page, "button-keyboard-focus"));
  await button.hover();
  pictures.push(await capture(page, "button-hover-and-focus"));
  await button.blur();
  await page.mouse.move(1, 999);
  await page.evaluate(() => window.scrollTo({ top: 500, behavior: "instant" }));
  await page.clock.runFor(1024);
  pictures.push(await capture(page, "button-sticky-over-content"));
  return pictures;
}

for (const width of widths) for (const language of ["en", "ru"]) for (const theme of ["light", "dark"]) {
  test(`platform button pixels ${language} ${theme} ${width}px`, async ({ context }, info) => {
    const before = await context.newPage();
    const after = await context.newPage();
    await prepare(before, true, language, theme, width);
    const expected = await buttonStates(before);
    await prepare(after, false, language, theme, width);
    await compare(expected, await buttonStates(after), info);
  });
}

test.describe("platform button retina", () => {
  test.use({ deviceScaleFactor: 2 });
  for (const width of [320, 480, 481, 580, 581, 1120, 1121, 1920]) for (const theme of ["light", "dark"]) {
    test(`pixels ${theme} ${width}px at DPR 2`, async ({ context }, info) => {
      const before = await context.newPage();
      const after = await context.newPage();
      await prepare(before, true, "ru", theme, width);
      const expected = await buttonStates(before);
      await prepare(after, false, "ru", theme, width);
      await compare(expected, await buttonStates(after), info);
    });
  }
});

test.describe("Chromium platform button behavior", () => {
  for (const language of ["en", "ru"]) for (const theme of ["light", "dark"]) {
    test(`geometry and pointer navigation ${language} ${theme}`, async ({ page }) => {
      await prepare(page, false, language, theme, 1440);
      await page.route(destination, route => route.fulfill({ contentType: "text/html", body: "<!doctype html><title>Platform</title>" }));
      for (const width of widths) {
        await page.setViewportSize({ width, height: 1000 });
        const button = page.getByRole("link", { name: language === "ru" ? "Открыть платформу" : "Open platform", exact: true });
        await expect(button).toBeVisible();
        await expect(button).toHaveAttribute("href", destination);
        await expect(button.locator("svg")).toHaveAttribute("aria-hidden", "true");
        const box = (await button.boundingBox())!;
        expect(box.width).toBe(width <= 480 ? 56 : width <= 580 ? 68 : 74);
        expect(box.height).toBe(width <= 580 ? 68 : 74);
        expect(box.x).toBeGreaterThanOrEqual(0);
        expect(box.x + box.width).toBeLessThanOrEqual(width);
        const header = (await page.locator(".tmc-header").boundingBox())!;
        expect(box.y).toBe(header.y);
        expect(box.x - header.x - header.width).toBe(width <= 580 ? 8 : 10);
        const icon = (await button.locator("svg").boundingBox())!;
        expect(icon.width).toBe(width <= 580 ? 32 : 36);
        expect(icon.height).toBe(icon.width);
        expect(icon.x + icon.width / 2).toBe(box.x + box.width / 2);
        expect(icon.y + icon.height / 2).toBe(box.y + box.height / 2);
      }
      await page.locator(".tmc-platform-link").click();
      await expect(page).toHaveURL(destination);
    });

    test(`keyboard navigation ${language} ${theme}`, async ({ page }) => {
      await prepare(page, false, language, theme, 375);
      await page.route(destination, route => route.fulfill({ contentType: "text/html", body: "<!doctype html><title>Platform</title>" }));
      const button = page.locator(".tmc-platform-link");
      for (let index = 0; index < 12 && !await button.evaluate(node => node === document.activeElement); index++) {
        await page.keyboard.press("Tab");
      }
      await expect(button).toBeFocused();
      await page.keyboard.press("Enter");
      await expect(page).toHaveURL(destination);
    });
  }
});
