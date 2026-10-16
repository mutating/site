import { expect, test } from "@playwright/test";

for (const [name, path] of [["HTML", "/"], ["React", "/react.html"], ["Hugo", "/.runtime/hugo/index.html"]]) {
  test.describe(name, () => {
    test("theme choice persists, controls stay accessible, and buttons work", async ({ page }) => {
      await page.goto(path);
      const toggle = page.getByRole("button", { name: "Переключить тему", exact: true });
      await expect(toggle).toHaveAttribute("aria-pressed", "true");
      await toggle.focus();
      await page.keyboard.press("Enter");
      await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
      await expect(toggle).toHaveAttribute("aria-pressed", "false");
      await expect(page.getByTestId("theme")).toHaveText("light");
      await page.reload();
      await expect(page.getByTestId("theme")).toHaveText("light");
      await expect(page.getByRole("button", { name: "Недоступна" })).toBeDisabled();
      await page.getByRole("button", { name: "Действие →", exact: true }).click();
      await expect(page.getByTestId("count")).toHaveText("1");
      await expect(page.getByRole("link", { name: "The Mutating Company", exact: true })).toBeVisible();
    });

    test("local assets load and the header fits the viewport", async ({ page }) => {
      const errors: string[] = [];
      page.on("pageerror", (error) => errors.push(error.message));
      page.on("response", (response) => { if (response.status() >= 400) errors.push(`${response.status()} ${response.url()}`); });
      await page.goto(path);
      await page.evaluate(() => document.fonts.ready);
      const metrics = await page.evaluate(async () => {
        await document.fonts.load('780 35px "Science Gothic"', "TMC");
        const eye = document.querySelector<HTMLElement>(".tmc-eye__surface")!;
        const image = getComputedStyle(eye).maskImage;
        return {
          overflow: document.documentElement.scrollWidth > innerWidth,
          wordmarkFont: document.fonts.check('780 35px "Science Gothic"', "TMC"),
          image,
          bg: getComputedStyle(document.body).backgroundColor,
        };
      });
      expect(metrics.overflow).toBe(false);
      expect(metrics.wordmarkFont).toBe(true);
      expect(metrics.image).toContain("muta-eye-surface-alpha-v1.webp");
      expect(metrics.bg).toBe("rgb(9, 6, 8)");
      expect(errors).toEqual([]);
    });

    test("responsive navigation supports keyboard dismissal and language selection", async ({ page }, testInfo) => {
      await page.goto(path);
      if (testInfo.project.name === "mobile") {
        const menu = page.locator(".tmc-mobile-menu");
        await menu.locator(":scope > summary").click();
        await expect(menu).toHaveAttribute("open", "");
        const language = menu.locator(".tmc-language-switcher");
        await language.locator("summary").click();
        await page.keyboard.press("Escape");
        await expect(language).not.toHaveAttribute("open");
        await expect(menu).toHaveAttribute("open", "");
        await language.locator("summary").click();
        await language.getByRole("link", { name: "EN", exact: true }).click();
        await expect(menu).not.toHaveAttribute("open");
        expect(await page.evaluate(() => localStorage.getItem("tmc-language"))).toBe("en");
        await menu.locator(":scope > summary").click();
        await page.keyboard.press("Escape");
        await expect(menu).not.toHaveAttribute("open");
        await expect(menu.locator(":scope > summary")).toBeFocused();
      } else {
        const language = page.locator(".tmc-language-switcher--desktop");
        await language.locator("summary").focus();
        await page.keyboard.press("Enter");
        await expect(language).toHaveAttribute("open", "");
        await page.keyboard.press("Escape");
        await expect(language).not.toHaveAttribute("open");
        await expect(language.locator("summary")).toBeFocused();
      }
    });
  });
}

for (const path of ["/", "/react.html"]) {
  test(`destroying and remounting behaviors does not duplicate theme handlers: ${path}`, async ({ page }) => {
    await page.goto(path);
    for (let i = 0; i < 4; i++) await page.getByRole("button", { name: "Пересоздать хэдер" }).click();
    await page.getByRole("button", { name: "Переключить тему", exact: true }).click();
    await expect(page.getByTestId("theme")).toHaveText("light");
    const second = page.getByRole("button", { name: "Второй переключатель темы" });
    if (path.includes("react")) await expect(second).toHaveAttribute("aria-pressed", "false");
    await page.getByRole("button", { name: "Системная тема", exact: true }).click();
    await expect(page.getByTestId("theme")).toHaveText("dark");
    await page.emulateMedia({ colorScheme: "light" });
    await expect(page.getByTestId("theme")).toHaveText("light");
  });
}

test("storage denial preserves the in-memory choice and reduced motion keeps the eye visible", async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(Storage.prototype, "getItem", { value() { throw new DOMException("Blocked"); } });
    Object.defineProperty(Storage.prototype, "setItem", { value() { throw new DOMException("Blocked"); } });
  });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/");
  await page.getByRole("button", { name: "Переключить тему", exact: true }).click();
  await expect(page.getByTestId("theme")).toHaveText("light");
  await page.emulateMedia({ colorScheme: "light" });
  await page.emulateMedia({ colorScheme: "dark" });
  await expect(page.getByTestId("theme")).toHaveText("light");
  await expect(page.locator(".tmc-eye__clip")).toHaveCSS("clip-path", "none");
});

test("theme changes synchronize between tabs", async ({ page, context }) => {
  await page.goto("/");
  const other = await context.newPage();
  await other.goto("/react.html");
  await expect(other.getByTestId("theme")).toHaveText("dark");
  await page.getByRole("button", { name: "Переключить тему", exact: true }).click();
  await expect(other.getByTestId("theme")).toHaveText("light");
  await other.close();
});

test("wordmark expands within its reserved space and collapses when the pointer leaves", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name === "mobile", "Hover expansion requires a mouse.");
  await page.goto("/");
  const stage = page.locator(".tmc-brand__wordmark-stage");
  await expect(stage).toHaveAttribute("data-can-expand", "true");
  const wordmark = page.locator(".tmc-brand__wordmark");
  const bounds = await stage.boundingBox();
  await wordmark.hover();
  await expect(wordmark).toHaveAttribute("data-hover-expanded", "true");
  await expect(page.locator(".tmc-brand__word-reveal").first()).toHaveCSS("opacity", "1");
  expect(await stage.boundingBox()).toEqual(bounds);
  await page.mouse.move(1000, 800);
  await expect(wordmark).not.toHaveAttribute("data-hover-expanded");
});

test("one items array updates both menus, including active states", async ({ page }) => {
  await page.goto("/react.html");
  await page.getByRole("button", { name: "Другое меню", exact: true }).click();
  for (const selector of [".tmc-nav", ".tmc-mobile-menu__links"]) {
    const links = page.locator(`${selector} > a`);
    await expect(links).toHaveText(["Обзор", "Организации"]);
    await expect(links.first()).toHaveAttribute("href", "#overview");
    await expect(links.first()).toHaveAttribute("aria-current", "page");
  }
  await page.getByRole("button", { name: "Другое меню", exact: true }).click();
  await expect(page.locator(".tmc-nav > a")).toHaveText(["Компоненты", "HTML-версия"]);
  await expect(page.locator(".tmc-mobile-menu__links > a")).toHaveText(["Компоненты", "HTML-версия"]);
});

test("an obsolete cleanup cannot disable a newly mounted brand", async ({ page }) => {
  await page.goto("/");
  await page.evaluate(async () => {
    const path = "/src/index.ts";
    const { mountBrand } = await import(path);
    const brand = document.querySelector<HTMLElement>(".tmc-brand")!.cloneNode(true) as HTMLElement;
    brand.dataset.testid = "remounted-brand";
    brand.style.cssText = "position:fixed;left:20px;bottom:20px;z-index:100";
    document.body.append(brand);
    const obsolete = mountBrand(brand, { blinkIntervalMs: 0 });
    obsolete();
    mountBrand(brand, { blinkIntervalMs: 0 });
    obsolete();
  });
  const brand = page.getByTestId("remounted-brand");
  await brand.locator(".tmc-brand__wordmark").hover();
  await expect(brand.locator(".tmc-brand__word-reveal").first()).toHaveCSS("opacity", "1");
});
