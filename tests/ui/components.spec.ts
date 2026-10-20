import { expect, test } from "@playwright/test";

test("a language-only React header puts its switcher inside the mobile menu", async ({ page, isMobile }) => {
  await page.goto("/react.html?language-only");
  const menu = page.locator(".tmc-mobile-menu");
  const summary = menu.locator(":scope > summary");
  const language = menu.locator(".tmc-language-switcher--mobile");
  const activate = async (control: typeof summary) => isMobile ? control.tap() : control.click();
  await expect(page.locator(".tmc-nav")).toHaveCount(0);
  for (const width of [320, 480, 481, 900, 1120, 1121, 1440]) {
    await page.setViewportSize({ width, height: 800 });
    await page.mouse.move(0, 799);
    await expect(page.locator("header .tmc-theme-toggle")).toBeVisible();
    if (width > 1120) {
      await expect(menu).toBeHidden();
      await expect(page.locator(".tmc-language-switcher--desktop > summary")).toBeVisible();
      continue;
    }
    await expect(summary).toBeVisible();
    await expect(page.locator(".tmc-language-switcher > summary:visible")).toHaveCount(0);
    await expect(page.locator(".tmc-language-switcher--desktop")).toBeHidden();
    await activate(summary);
    await expect(language.locator("summary")).toBeVisible();
    await expect(page.locator(".tmc-language-switcher > summary:visible")).toHaveCount(1);
    await activate(language.locator("summary"));
    await expect(language.getByRole("link", { name: "EN", exact: true })).toBeVisible();
    await activate(language.getByRole("link", { name: "EN", exact: true }));
    await expect(page).toHaveURL(/#en$/);
    await expect(menu).not.toHaveAttribute("open");
    await expect(language).not.toHaveAttribute("open");
    await expect(page.locator(".tmc-language-switcher > summary:visible")).toHaveCount(0);
  }
});

for (const destination of ["empty", "other-origin"]) {
  test(`reload-home respects the complete explicit URL: ${destination}`, async ({ page, baseURL }) => {
    const home = destination === "empty" ? "" : new URL("/react.html", baseURL!.replace("127.0.0.1", "localhost")).href;
    if (home) await page.route(home, route => route.fulfill({ contentType: "text/html", body: "<!doctype html><title>Other home</title>" }));
    const query = new URLSearchParams({ "reload-brand": "1", "brand-home": home, probe: "1" });
    await page.goto(`/react.html?${query}#old`);
    const current = page.url().replace(/#old$/, "");
    const brand = page.locator(".tmc-brand");
    await expect(brand).toHaveAttribute("data-home-url", home);
    await page.evaluate(() => {
      sessionStorage.removeItem("brand-url-activations");
      addEventListener("tmc-brand-home-activate", () => sessionStorage.setItem("brand-url-activations", "1"));
    });
    const navigated = page.waitForEvent("framenavigated", frame => frame === page.mainFrame());
    await brand.click();
    await navigated;
    await expect(page).toHaveURL(home || current);
    if (!home) expect(await page.evaluate(() => sessionStorage.getItem("brand-url-activations"))).toBe("1");
  });
}

test("explicit empty configuration preserves labels, current-document navigation and storage keys", async ({ page }, testInfo) => {
  await page.goto("/.runtime/hugo/config-contract/?probe=1");
  await expect(page.locator("html")).toHaveAttribute("data-fixture-ready", "true");
  const initialUrl = page.url();
  const brand = page.locator(".tmc-brand");
  await expect(brand).toHaveAttribute("href", "");
  await expect(brand).toHaveAttribute("aria-label", "");
  for (const selector of [".tmc-nav", ".tmc-mobile-menu nav", ".tmc-theme-toggle", ".tmc-mobile-menu > summary", ".tmc-language-switcher summary", ".tmc-language-switcher__list"]) {
    for (const element of await page.locator(selector).all()) await expect(element).toHaveAttribute("aria-label", "");
  }
  await expect(page.locator(".tmc-mobile-menu__label")).toHaveText("");
  const reloaded = page.waitForEvent("framenavigated", frame => frame === page.mainFrame());
  await brand.click();
  await reloaded;
  await expect(page).toHaveURL(initialUrl);
  await expect(page.locator("html")).toHaveAttribute("data-fixture-ready", "true");
  const mobile = testInfo.project.name === "mobile";
  if (mobile) await page.locator(".tmc-mobile-menu > summary").click();
  const language = page.locator(`.tmc-language-switcher--${mobile ? "mobile" : "desktop"}`);
  await expect(language).toHaveAttribute("data-tmc-language-storage-key", "");
  await language.locator(":scope > summary").focus();
  await page.keyboard.press("Enter");
  const option = language.locator('[data-language-option="ru"]');
  await expect(option).toHaveText("");
  await option.click();
  await expect(page).toHaveURL(/#ru$/);
  expect(await page.evaluate(() => localStorage.getItem(""))).toBe("ru");
  expect(await page.evaluate(() => localStorage.getItem("tmc-language"))).toBeNull();
  await expect(language).not.toHaveAttribute("open");
});

test("explicit empty link relation preserves cross-origin referrers", async ({ page, context }) => {
  const destination = "https://header-destination.example.test/landing";
  let referer: string | undefined;
  await context.route(destination, async route => {
    referer = route.request().headers().referer;
    await route.fulfill({ contentType: "text/html", body: "<!doctype html><title>Destination</title>" });
  });
  await page.goto("/.runtime/hugo/rel-contract/");
  const origin = new URL(page.url()).origin + "/";
  for (const [name, rel, expectedReferer] of [
    ["absent", "noopener noreferrer", ""],
    ["empty", "", origin],
    ["noopener", "noopener", origin],
    ["noreferrer", "noreferrer", ""],
  ]) {
    const link = page.locator(`[data-case="${name}"] a`);
    await expect(link).toHaveAttribute("rel", rel);
    referer = undefined;
    const opened = context.waitForEvent("page");
    await link.click();
    const popup = await opened;
    await popup.waitForLoadState("domcontentloaded");
    expect(referer ?? "").toBe(expectedReferer);
    expect(await popup.evaluate(() => document.referrer)).toBe(expectedReferer);
    await popup.close();
  }
});

for (const failure of ["missing", "constructor", "event-signal"]) {
  test(`native cleanup and remount work without abortable listeners: ${failure}`, async ({ page }) => {
    await page.addInitScript(failure => {
      if (failure === "missing") (window as any).AbortController = undefined;
      else if (failure === "constructor") (window as any).AbortController = class { constructor() { throw new Error("AbortController unavailable"); } };
      else {
        const add = EventTarget.prototype.addEventListener;
        EventTarget.prototype.addEventListener = function(type, listener, options) {
          if (options && typeof options === "object" && "signal" in options) throw new TypeError("Event listener AbortSignal unavailable");
          return add.call(this, type, listener, options);
        };
      }
    }, failure);
    await page.goto("/");
    await expect(page.getByTestId("theme")).toHaveText("dark");
    for (let index = 0; index < 4; index++) await page.getByRole("button", { name: "Пересоздать хэдер" }).click();
    await page.getByRole("button", { name: "Переключить тему", exact: true }).click();
    await expect(page.getByTestId("theme")).toHaveText("light");
    await page.getByRole("button", { name: "Переключить тему", exact: true }).click();
    await expect(page.getByTestId("theme")).toHaveText("dark");
  });
}

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

test("updated language options still close the enclosing mobile menu", async ({ page }, testInfo) => {
  if (testInfo.project.name === "desktop") await page.setViewportSize({ width: 1040, height: 1000 });
  await page.goto("/react.html");
  const menu = page.locator(".tmc-mobile-menu");
  const language = menu.locator(".tmc-language-switcher");
  for (const code of ["de", "en", "de"]) {
    await page.getByRole("button", { name: "Другие языки", exact: true }).click();
    await menu.locator(":scope > summary").click();
    await language.locator(":scope > summary").click();
    await language.getByRole("link", { name: code.toUpperCase(), exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`#${code}$`));
    expect(await page.evaluate(() => localStorage.getItem("tmc-language"))).toBe(code);
    await expect(language).not.toHaveAttribute("open");
    await expect(menu).not.toHaveAttribute("open");
  }
});

test("changing the current language keeps new options interactive", async ({ page }, testInfo) => {
  await page.goto("/react.html");
  const mobile = testInfo.project.name === "mobile";
  const menu = page.locator(".tmc-mobile-menu");
  const language = page.locator(`.tmc-language-switcher--${mobile ? "mobile" : "desktop"}`);
  for (const [current, selected] of [["en", "ru"], ["ru", "en"]]) {
    await page.getByRole("button", { name: "Сменить текущий язык", exact: true }).click();
    await expect(language.locator(":scope > summary span")).toHaveText(current.toUpperCase());
    if (mobile) await menu.locator(":scope > summary").click();
    await language.locator(":scope > summary").focus();
    await page.keyboard.press("Enter");
    await language.getByRole("link", { name: selected.toUpperCase(), exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`#${selected}$`));
    expect(await page.evaluate(() => localStorage.getItem("tmc-language"))).toBe(selected);
    await expect(language).not.toHaveAttribute("open");
    if (mobile) await expect(menu).not.toHaveAttribute("open");
  }
});

test("replacing router links preserves menu dismissal without closing it on unrelated renders", async ({ page }, testInfo) => {
  if (testInfo.project.name === "desktop") await page.setViewportSize({ width: 1040, height: 1000 });
  await page.goto("/react.html");
  const menu = page.locator(".tmc-mobile-menu");
  for (const [index, wrapped] of [false, true, false, true].entries()) {
    if (index) await page.getByRole("button", { name: "Другие ссылки", exact: true }).click();
    await menu.locator(":scope > summary").click();
    const link = menu.locator(".tmc-mobile-menu__links a").first();
    if (wrapped) {
      await expect(link).toHaveAttribute("data-demo-router-link", "");
      // A fresh inline renderLink callback must not reset an open menu.
      const action = page.getByRole("button", { name: "Действие →", exact: true });
      await action.focus();
      await page.keyboard.press("Enter");
      await expect(page.getByTestId("count")).toHaveText(String(index === 1 ? 1 : 2));
      await expect(menu).toHaveAttribute("open", "");
    } else await expect(link).not.toHaveAttribute("data-demo-router-link");
    await link.click();
    await expect(page).toHaveURL(/#components$/);
    await expect(menu).not.toHaveAttribute("open");
  }
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
