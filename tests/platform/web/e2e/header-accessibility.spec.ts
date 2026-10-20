import { firefox } from "@playwright/test";
import { expect, login, test } from "./fixtures";

test("header controls fit before the delayed initial expansion measurement", async ({ page }) => {
  // Hold the effect's measurements pending to expose the first rendered layout
  // deterministically, including on a fast test runner.
  await page.addInitScript(() => { window.requestAnimationFrame = () => 0; });
  for (const width of [540, 320]) {
    await page.setViewportSize({ width, height: 800 });
    await page.goto("login");
    await expect.poll(() => page.evaluate(() => !!document.querySelector(".auth-panel"))).toBe(true);
    await expect(page.locator(".tmc-brand__wordmark-stage")).not.toHaveAttribute("data-expansion-measured", "true");
    for (const selector of [".tmc-language-toggle", ".tmc-theme-toggle"]) {
      const bounds = (await page.locator(selector).boundingBox())!;
      expect(bounds.x).toBeGreaterThanOrEqual(0);
      expect(bounds.x + bounds.width).toBeLessThanOrEqual(width);
    }
    expect(await page.getByRole("banner").evaluate((header) => header.scrollWidth - header.clientWidth)).toBeLessThanOrEqual(0);
  }
});

test("a constrained wordmark releases room for controls before a collapse transition can finish", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await page.setViewportSize({ width: 1440, height: 800 });
  await page.goto("login");
  await expect(page.locator(".auth-panel")).toBeVisible();
  await page.evaluate(() => document.fonts.ready);
  await page.locator(".tmc-brand").focus();
  await expect(page.locator(".tmc-brand__word-reveal").last()).toHaveCSS("opacity", "1");
  await page.setViewportSize({ width: 540, height: 800 });
  await expect(page.locator(".tmc-brand__wordmark-stage")).toHaveAttribute("data-can-expand", "false");
  await page.locator(".tmc-brand__wordmark").evaluate((element) => {
    // Freeze any real collapse transition at its first frame, where controls
    // must already fit. This must also work when animation frames are delayed.
    for (const animation of document.getAnimations()) {
      const target = (animation.effect as KeyframeEffect | null)?.target;
      if (!(target instanceof Element) || !element.contains(target)) continue;
      animation.pause();
      animation.currentTime = 0;
    }
  });
  for (const selector of [".tmc-language-toggle", ".tmc-theme-toggle"]) {
    const bounds = (await page.locator(selector).boundingBox())!;
    expect(bounds.x).toBeGreaterThanOrEqual(0);
    expect(bounds.x + bounds.width).toBeLessThanOrEqual(540);
  }
  expect(await page.getByRole("banner").evaluate((header) => header.scrollWidth - header.clientWidth)).toBeLessThanOrEqual(0);
  await page.locator(".tmc-language-toggle").click();
  await expect(page.locator("html")).toHaveAttribute("lang", "en");
});

test("the animated wordmark settles inside a resized header with hover and keyboard focus", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await page.setViewportSize({ width: 1440, height: 800 });
  await page.goto("login");
  await expect(page.locator(".auth-panel")).toBeVisible();
  await page.evaluate(() => document.fonts.ready);
  const wordmark = page.locator(".tmc-brand__wordmark");
  const stage = page.locator(".tmc-brand__wordmark-stage");
  for (const activation of ["hover", "focus"]) {
    await page.setViewportSize({ width: 1440, height: 800 });
    await page.mouse.move(1, 500);
    await page.locator(".tmc-brand").evaluate((brand: HTMLElement) => brand.blur());
    if (activation === "hover") {
      await wordmark.hover();
      await page.mouse.move(200, 50);
    } else await page.locator(".tmc-brand").focus();
    await expect(page.locator(".tmc-brand__word-reveal").last()).toHaveCSS("opacity", "1");
    for (const width of [768, 640, 540, 320, 921, 1440]) {
      await page.setViewportSize({ width, height: 800 });
      if (width <= 640) await expect(stage).toHaveAttribute("data-can-expand", "false");
      else if (width >= 921) await expect(stage).toHaveAttribute("data-can-expand", "true");
      await expect.poll(() => wordmark.evaluate((element) => document.getAnimations().some((animation) => {
        const target = (animation.effect as KeyframeEffect | null)?.target;
        return target instanceof Element && element.contains(target) && animation.playState === "running";
      }))).toBe(false);
      await expect.poll(() => page.getByRole("banner").evaluate((header) => header.scrollWidth - header.clientWidth)).toBeLessThanOrEqual(0);
      for (const selector of [".tmc-language-toggle", ".tmc-theme-toggle"]) {
        const bounds = (await page.locator(selector).boundingBox())!;
        expect(bounds.x).toBeGreaterThanOrEqual(0);
        expect(bounds.x + bounds.width).toBeLessThanOrEqual(width);
      }
    }
  }
});

for (const textSpacing of [false, true]) {
  test(`shrinking an existing header keeps its controls visible with custom spacing ${textSpacing}`, async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.setViewportSize({ width: 1440, height: 800 });
    await page.goto("login");
    await expect(page.locator(".auth-panel")).toBeVisible();
    await page.evaluate(() => document.fonts.ready);
    if (textSpacing) await page.addStyleTag({ content: "* { letter-spacing: .12em !important; word-spacing: .16em !important; line-height: 1.5 !important; }" });
    for (const activation of ["none", "hover", "focus"]) {
      await page.setViewportSize({ width: 1440, height: 800 });
      await page.mouse.move(1, 500);
      await page.locator(".tmc-brand").evaluate((brand: HTMLElement) => brand.blur());
      if (activation === "hover") {
        await page.locator(".tmc-brand__wordmark").hover();
        await page.mouse.move(200, 50);
      } else if (activation === "focus") await page.locator(".tmc-brand").focus();
      // Keep the pointer still throughout both shrinking and growing paths.
      for (const width of [900, 880, 860, 800, 768, 720, 640, 540, 380, 320, 540, 768, 921, 1440]) {
        await page.setViewportSize({ width, height: 800 });
        await expect.poll(() => page.getByRole("banner").evaluate((header) => {
          const controls = [...header.querySelectorAll("button")].map((button) => button.getBoundingClientRect());
          return Math.max(header.scrollWidth - header.clientWidth, ...controls.map((rect) => rect.right - innerWidth));
        }), { message: `${activation}, ${width}px: controls must fit after viewport change` }).toBeLessThanOrEqual(0);
      }
    }
    await page.locator(".tmc-language-toggle").click();
    await expect(page.locator("html")).toHaveAttribute("lang", "en");
    const theme = await page.locator("html").getAttribute("data-theme");
    await page.locator(".tmc-theme-toggle").click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", theme === "dark" ? "light" : "dark");
  });
}

test("custom text spacing keeps the unfolding logo separate from the account caption and controls", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("login");
  await expect(page.locator(".auth-panel")).toBeVisible();
  await page.evaluate(() => document.fonts.ready);
  await page.addStyleTag({ content: "* { letter-spacing: .12em !important; word-spacing: .16em !important; line-height: 1.5 !important; }" });
  for (const width of [880, 900, 920, 921, 1121, 1440]) {
    await page.setViewportSize({ width, height: 800 });
    for (const language of ["ru", "en"]) {
      if (await page.locator("html").getAttribute("lang") !== language) {
        await page.locator(".tmc-language-toggle").click();
      }
      for (const activation of ["focus", "hover"]) {
        await page.mouse.move(1, 500);
        await page.locator(".tmc-brand").evaluate((brand: HTMLElement) => brand.blur());
        if (activation === "focus") await page.locator(".tmc-brand").focus();
        else await page.locator(".tmc-brand__wordmark").hover();
        await expect.poll(() => page.evaluate(() => {
          const wordmark = document.querySelector(".tmc-brand__wordmark")!.getBoundingClientRect();
          const caption = document.querySelector(".header-caption")!.getBoundingClientRect();
          return wordmark.right - caption.left;
        }), { message: `${language}, ${width}px, ${activation}: visible logo must not overlap caption` }).toBeLessThanOrEqual(0);
        for (const selector of [".tmc-language-toggle", ".tmc-theme-toggle"]) {
          const box = (await page.locator(selector).boundingBox())!;
          expect(box.x).toBeGreaterThanOrEqual(0);
          expect(box.x + box.width).toBeLessThanOrEqual(width);
        }
      }
    }
  }
});

test("the skip link reveals the main heading below the sticky header", async ({ page, account, browserName }) => {
  await login(page, account);
  for (const width of [320, 390, 1440]) {
    await page.setViewportSize({ width, height: 600 });
    await page.goto("profile?source=header-skip");
    await expect(page.locator(".profile-settings-form")).toBeVisible();
    await page.getByRole("link", { name: "Перейти к содержимому" }).focus();
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/profile\?source=header-skip#main$/);
    const geometry = await page.evaluate(() => ({
      headingTop: document.querySelector("main h1")!.getBoundingClientRect().top,
      headerBottom: document.querySelector("header")!.getBoundingClientRect().bottom,
    }));
    expect(geometry.headingTop).toBeGreaterThanOrEqual(geometry.headerBottom);
    await page.keyboard.press(browserName === "webkit" ? "Alt+Tab" : "Tab");
    await expect.poll(() => page.evaluate(() => document.querySelector("main")!.contains(document.activeElement))).toBe(true);
  }
});

test("forced colors keep the theme's selected state visually distinct", async ({ page, browserName }) => {
  test.skip(browserName !== "chromium", "Windows high contrast rendering is exercised in Chromium");
  await page.emulateMedia({ forcedColors: "active", colorScheme: "light", reducedMotion: "reduce" });
  await page.goto("login");
  await expect(page.locator(".auth-panel")).toBeVisible();
  const toggle = page.getByRole("button", { name: "Переключить тему", exact: true });
  if (await page.locator("html").getAttribute("data-theme") !== "light") await toggle.click();
  await toggle.hover();
  const light = await toggle.screenshot({ animations: "disabled" });
  await toggle.click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await expect(toggle).toHaveAttribute("aria-pressed", "true");
  const dark = await toggle.screenshot({ animations: "disabled" });
  expect(dark.equals(light), "The thumb and selected icon must remain visible in high contrast").toBe(false);
});

test("the brand eye remains visible in forced colors", async ({ page, browserName }) => {
  test.skip(browserName !== "chromium", "Windows high contrast rendering is exercised in Chromium");
  await page.emulateMedia({ forcedColors: "active", colorScheme: "light", reducedMotion: "reduce" });
  await page.goto("login");
  await expect(page.locator(".auth-panel")).toBeVisible();
  await expect(page.locator(".tmc-eye__clip")).toHaveCSS("clip-path", "none");
  await page.mouse.move(1000, 500);
  const eye = page.locator(".tmc-eye");
  const visible = await eye.screenshot({ animations: "disabled" });
  const hidden = await page.addStyleTag({ content: ".site-header .tmc-eye__surface, .site-header .tmc-eye__pupil { visibility: hidden !important; }" });
  const blank = await eye.screenshot({ animations: "disabled" });
  await hidden.evaluate((style) => style.parentNode!.removeChild(style));
  expect(visible.equals(blank), "The high contrast eye must paint visible pixels").toBe(false);
});

test("Firefox's minimum font size keeps all header controls inside the viewport", async ({ browserName, baseURL }) => {
  test.skip(browserName !== "firefox", "Uses Firefox's native minimum font preference");
  const browser = await firefox.launch({ firefoxUserPrefs: {
    "font.minimum-size.x-western": 32,
    "font.minimum-size.x-cyrillic": 32,
  } });
  try {
    const context = await browser.newContext({ baseURL, locale: "ru-RU", reducedMotion: "reduce" });
    const page = await context.newPage();
    await page.goto("login");
    await expect(page.locator(".auth-panel")).toBeVisible();
    await page.evaluate(() => document.fonts.ready);
    for (const width of [320, 390, 540, 768, 800, 900, 920, 921, 922, 1000, 1121, 1440]) {
      await page.setViewportSize({ width, height: 800 });
      for (const language of ["en", "ru"]) {
        await page.locator(".tmc-brand__wordmark").hover();
        for (const control of [".tmc-language-toggle", ".tmc-theme-toggle"]) {
          const box = (await page.locator(control).boundingBox())!;
          expect(box.x).toBeGreaterThanOrEqual(0);
          expect(box.x + box.width).toBeLessThanOrEqual(width);
        }
        // Measure header overflow separately from the enlarged auth form below it.
        await expect.poll(() => page.getByRole("banner").evaluate((header) => header.scrollWidth - header.clientWidth)).toBeLessThanOrEqual(0);
        await page.locator(".tmc-language-toggle").click();
        await expect(page.locator("html")).toHaveAttribute("lang", language);
        await page.mouse.move(1, 500);
      }
    }
  } finally {
    await browser.close();
  }
});
