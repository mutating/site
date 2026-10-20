import { expect, login, test } from "./fixtures";
import type { Page } from "@playwright/test";

async function gazeOffset(page: Page) {
  return page.locator(".tmc-eye").evaluate((eye) => {
    const bounds = eye.getBoundingClientRect();
    const pupil = eye.querySelector(".tmc-eye__pupil")!.getBoundingClientRect();
    return {
      x: pupil.x + pupil.width / 2 - (bounds.x + bounds.width / 2),
      y: pupil.y + pupil.height / 2 - (bounds.y + bounds.height / 2),
      width: bounds.width,
      height: bounds.height,
    };
  });
}

test("the eye follows the pointer outside the header before and after login", async ({
  page,
  account,
}) => {
  await page.goto("login");
  await expect(page.locator(".auth-panel")).toBeVisible();
  await expect(page.locator(".tmc-eye")).toBeVisible();
  const viewport = page.viewportSize()!;
  const eye = (await page.locator(".tmc-eye").boundingBox())!;
  await page.mouse.move(viewport.width - 10, viewport.height - 30);
  await expect.poll(async () => (await gazeOffset(page)).x).toBeGreaterThan(2);
  await expect.poll(async () => (await gazeOffset(page)).y).toBeGreaterThan(1);
  await page.mouse.move(1, eye.y + eye.height / 2);
  await expect.poll(async () => (await gazeOffset(page)).x).toBeLessThan(-1);
  const offset = await gazeOffset(page);
  expect(Math.abs(offset.x)).toBeLessThan(offset.width * 0.26);
  expect(Math.abs(offset.y)).toBeLessThan(offset.height * 0.15 + 0.3);

  // Pointer tracking must continue working after login and navigation.
  await login(page, account);
  await page.mouse.move(viewport.width - 10, viewport.height - 30);
  await expect.poll(async () => (await gazeOffset(page)).x).toBeGreaterThan(2);
  await page.getByRole("link", { name: "Профиль", exact: true }).click();
  await page.mouse.move(1, eye.y + eye.height / 2);
  await expect.poll(async () => (await gazeOffset(page)).x).toBeLessThan(-1);
});

test("TMC unfolds without moving the header controls and remains open across its stage", async ({
  page,
}, info) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("login");
  await expect(page.locator(".auth-panel")).toBeVisible();
  await page.evaluate(() => document.fonts.ready);
  const wordmark = page.locator(".tmc-brand__wordmark");
  const stage = page.locator(".tmc-brand__wordmark-stage");
  await expect(stage).toHaveAttribute("data-can-expand", "true");
  const compactWidth = (await wordmark.boundingBox())!.width;
  const actions = (await page.locator(".tmc-header-actions").boundingBox())!;
  const text = (await wordmark.boundingBox())!;
  // Start on the final compact C; changes in its animated hitbox must not close it.
  await page.mouse.move(text.x + text.width - 3, text.y + text.height / 2);
  await expect
    .poll(async () => (await wordmark.boundingBox())!.width)
    .toBeGreaterThan(compactWidth * 3);
  await expect(page.locator(".tmc-brand__word-reveal").last()).toHaveCSS(
    "opacity",
    "1",
  );
  const bounds = (await stage.boundingBox())!;
  await page.mouse.move(
    bounds.x + bounds.width - 2,
    bounds.y + bounds.height / 2,
  );
  await expect
    .poll(async () => (await wordmark.boundingBox())!.width)
    .toBeGreaterThan(compactWidth * 3);
  const expanded = (await wordmark.boundingBox())!;
  const caption = (await page.locator(".header-caption").boundingBox())!;
  expect(expanded.x + expanded.width).toBeLessThan(caption.x);
  expect((await page.locator(".tmc-header-actions").boundingBox())!.x).toBeCloseTo(
    actions.x,
    1,
  );
  await page.screenshot({ path: info.outputPath("expanded-brand.png") });
  await page.mouse.move(1000, 500);
  await expect
    .poll(async () => (await wordmark.boundingBox())!.width)
    .toBeLessThan(compactWidth + 1);

  await page.setViewportSize({ width: 320, height: 800 });
  await expect(stage).toHaveAttribute("data-can-expand", "false");
  await wordmark.hover();
  await expect(page.locator(".tmc-brand__word-reveal").last()).toHaveCSS(
    "opacity",
    "0",
  );
  const mobileBrand = (await page.locator(".tmc-brand").boundingBox())!;
  const mobileActions = (await page.locator(".tmc-header-actions").boundingBox())!;
  // Controls can use a second row at the shared header's smallest breakpoint.
  expect(
    mobileBrand.x + mobileBrand.width <= mobileActions.x ||
      mobileBrand.y + mobileBrand.height <= mobileActions.y,
  ).toBe(true);
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(320);

  await page.mouse.move(10, 500);
  await page.setViewportSize({ width: 900, height: 800 });
  await expect(stage).toHaveAttribute("data-can-expand", "true");
  await wordmark.hover();
  await expect(page.locator(".tmc-brand__word-reveal").last()).toHaveCSS(
    "opacity",
    "1",
  );
});

test("keyboard expansion and pointer tracking work with reduced motion", async ({
  page,
}) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("login");
  await expect(page.locator(".auth-panel")).toBeVisible();
  await expect(page.locator(".tmc-brand__wordmark-stage")).toHaveAttribute(
    "data-can-expand",
    "true",
  );
  await page.keyboard.press("Tab");
  await expect(
    page.getByRole("link", { name: "The Mutating Company", exact: true }),
  ).toBeFocused();
  await expect(page.locator(".tmc-brand__word-reveal").last()).toHaveCSS(
    "opacity",
    "1",
  );
  await page.keyboard.press("Tab");
  await expect(page.locator(".tmc-brand__word-reveal").last()).toHaveCSS(
    "opacity",
    "0",
  );
  await page.mouse.move(1300, 700);
  await expect.poll(async () => (await gazeOffset(page)).x).toBeGreaterThan(2);
  await page.getByRole("button", { name: "Выбрать язык", exact: true }).click();
  await expect(
    page.getByRole("link", { name: "The Mutating Company", exact: true }),
  ).toHaveAttribute("href", "https://mutating.tech/");
  const eye = (await page.locator(".tmc-eye").boundingBox())!;
  await page.mouse.move(1, eye.y + eye.height / 2);
  await expect.poll(async () => (await gazeOffset(page)).x).toBeLessThan(-1);
});
