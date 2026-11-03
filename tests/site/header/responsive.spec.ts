import { expect, test, type Page } from "@playwright/test";

test("the menu remains visible when its enlarged header scrolls out of view", async ({ page }) => {
  await page.setViewportSize({ width: 280, height: 301 });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.route(/\/(?:muta-tentacles|tokamak-reactor)\.html(?:\?|$)/, route => route.fulfill({ contentType: "text/html", body: "" }));
  await page.goto("/ru/?tmc-language-choice=ru");
  await page.evaluate(() => document.fonts.ready);
  const header = page.getByRole("banner");
  await header.evaluate(header => {
    const sizes = Array.from(header.querySelectorAll<HTMLElement>("*")).map(node => [node, parseFloat(getComputedStyle(node).fontSize) * 2] as const);
    for (const [node, size] of sizes) node.style.fontSize = `${size}px`;
  });
  await expect(header).toHaveCSS("position", "relative");
  const menu = header.locator(".tmc-mobile-menu");
  await menu.locator(":scope > summary").click();
  await page.evaluate(() => window.scrollTo({ top: 220, behavior: "instant" }));
  const panel = menu.locator(":scope > nav");
  await expect(panel).toBeInViewport({ ratio: 1 });
  const contact = panel.locator('.tmc-mobile-menu__links a[href$="#contact"]');
  await contact.focus();
  await expect(contact).toBeInViewport({ ratio: 1 });
  await expect(panel).toBeInViewport({ ratio: 1 });
  await contact.click();
  await expect(page).toHaveURL(/#contact$/);
  await expect(menu).not.toHaveAttribute("open");
});

for (const language of ["ru", "en"]) {
  test(`the platform link follows the control row with enlarged text: ${language}`, async ({ page }) => {
    await page.setViewportSize({ width: 1120, height: 800 });
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.route(/\/(?:muta-tentacles|tokamak-reactor)\.html(?:\?|$)/, route => route.fulfill({ contentType: "text/html", body: "" }));
    await page.goto(`${language === "ru" ? "/ru/" : "/"}?tmc-language-choice=${language}`);
    await page.evaluate(() => document.fonts.ready);
    const header = page.getByRole("banner");
    const link = header.locator(".tmc-platform-link");
    await header.evaluate(header => {
      const sizes = Array.from(header.querySelectorAll<HTMLElement>("*")).map(node => [node, parseFloat(getComputedStyle(node).fontSize) * 2] as const);
      for (const [node, size] of sizes) node.style.fontSize = `${size}px`;
    });
    const alignment = () => header.evaluate(header => {
      const row = header.querySelector(".tmc-header-actions")!.getBoundingClientRect();
      const link = header.querySelector(".tmc-platform-link")!.getBoundingClientRect();
      return Math.abs(link.top + link.height / 2 - row.top - row.height / 2);
    });
    for (const width of [1120, 581, 580, 481, 480, 900, 1120]) {
      await page.setViewportSize({ width, height: 800 });
      await expect.poll(alignment).toBeLessThanOrEqual(1);
      await expect.poll(() => header.evaluate(header => {
        const icon = header.querySelector(".tmc-platform-link > svg")!.getBoundingClientRect();
        const row = header.querySelector(".tmc-header-actions")!.getBoundingClientRect();
        const bounds = header.getBoundingClientRect();
        return Math.abs(icon.left - row.right - (bounds.right - parseFloat(getComputedStyle(header).borderRightWidth) - icon.right));
      })).toBeLessThanOrEqual(1);
    }
    await page.setViewportSize({ width: 280, height: 800 });
    await expect.poll(() => header.evaluate(header => {
      return header.querySelector(".tmc-platform-link")!.getBoundingClientRect().top - header.querySelector(".tmc-header")!.getBoundingClientRect().bottom;
    })).toBeGreaterThanOrEqual(0);
    await page.setViewportSize({ width: 1120, height: 800 });
    await expect.poll(alignment).toBeLessThanOrEqual(1);
    await page.route("https://platform.mutating.tech/", route => route.fulfill({ contentType: "text/html", body: "<!doctype html><title>Platform</title>" }));
    await link.click();
    await expect(page).toHaveURL("https://platform.mutating.tech/");
  });
}

async function visitContactFrame(page: Page, width: number, height: number) {
  await page.setViewportSize({ width, height });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.route(/\/muta-tentacles\.html(?:\?|$)/, route => {
    const url = new URL(route.request().url());
    url.searchParams.set("max-tentacles", "64");
    url.searchParams.set("render-supersample", "0.5");
    url.searchParams.set("manual-animation-frame", "1");
    return route.continue({ url: url.href });
  });
  await page.goto("/ru/?tmc-language-choice=ru#contact");
  const iframe = page.locator("iframe.contact__tentacles");
  await iframe.scrollIntoViewIfNeeded();
  await expect(iframe.contentFrame().locator("canvas")).toBeVisible();
  const point = await iframe.evaluate(node => {
    const box = node.getBoundingClientRect();
    const x = Math.min(innerWidth - 24, box.right - 24), y = Math.min(innerHeight - 24, box.bottom - 24);
    if (document.elementFromPoint(x, y) !== node) throw new Error("Contact iframe must be exposed");
    return { x, y };
  });
  return { iframe, point };
}

test("desktop language hover works while the contact iframe keeps focus", async ({ page }) => {
  const { iframe, point } = await visitContactFrame(page, 1440, 1000);
  await page.mouse.click(point.x, point.y);
  await expect(iframe).toBeFocused();
  const languages = page.locator(".tmc-language-switcher--desktop");
  await languages.locator("summary").hover();
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  await expect(languages).toHaveAttribute("open", "");
  await expect(iframe).toBeFocused();
  await languages.locator('[data-language-option="en"]').click();
  await expect(page.locator("html")).toHaveAttribute("lang", "en");
});

test("clicking the contact iframe dismisses compact navigation and nested languages", async ({ page }) => {
  const { iframe, point } = await visitContactFrame(page, 1120, 780);
  const menu = page.locator(".tmc-mobile-menu");
  const languages = menu.locator(".tmc-language-switcher--mobile");
  for (let attempt = 0; attempt < 2; attempt++) {
    await menu.locator(":scope > summary").click();
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    await expect(menu).toHaveAttribute("open", "");
    await expect(languages.locator("summary")).toBeVisible();
    await languages.locator("summary").click();
    await expect(languages).toHaveAttribute("open", "");
    await page.mouse.click(point.x, point.y);
    await expect(iframe).toBeFocused();
    await expect(menu).not.toHaveAttribute("open", "");
    await expect(languages).not.toHaveAttribute("open", "");
  }
});

for (const language of ["en", "ru"]) {
  test(`mouse events without pointer events dismiss only menus outside their target: ${language}`, async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 780 });
    await page.route(/\/(?:muta-tentacles|tokamak-reactor)\.html(?:\?|$)/, route => route.fulfill({ contentType: "text/html", body: "" }));
    await page.goto(language === "ru" ? "/ru/" : "/");
    await expect(page.locator("html")).toHaveAttribute("data-theme-ready", "true");
    const menu = page.locator(".tmc-mobile-menu");
    const languages = menu.locator(".tmc-language-switcher--mobile");
    await menu.locator(":scope > summary").click();
    await languages.locator("summary").click();
    await expect(languages).toHaveAttribute("open", "");
    // WebKit can omit pointerdown after a canceled native link drag.
    await menu.locator(":scope > nav").dispatchEvent("mousedown", { button: 0 });
    await expect(languages).not.toHaveAttribute("open", "");
    await expect(menu).toHaveAttribute("open", "");
    await languages.locator("summary").click();
    const previousTheme = await page.locator("html").getAttribute("data-theme");
    await page.locator("header .tmc-theme-toggle").dispatchEvent("mousedown", { button: 0 });
    await page.locator("header .tmc-theme-toggle").evaluate((node: HTMLButtonElement) => node.click());
    await expect(page.locator("html")).toHaveAttribute("data-theme", previousTheme === "dark" ? "light" : "dark");
    await expect(menu).not.toHaveAttribute("open", "");
    await expect(languages).not.toHaveAttribute("open", "");
  });
}

for (const language of ["en", "ru"]) for (const theme of ["light", "dark"]) {
  test(`unified mobile header preserves site navigation and the platform link: ${language}, ${theme}`, async ({ page, baseURL }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.addInitScript(({ language, theme }) => {
      localStorage.setItem("tmc-language", language);
      localStorage.setItem("tmc-theme", theme);
    }, { language, theme });
    await page.route("**/*", route => {
      const url = new URL(route.request().url());
      if (url.origin !== new URL(baseURL!).origin) return route.abort();
      if (/\/(?:muta-tentacles|tokamak-reactor)\.html/.test(url.pathname)) {
        return route.fulfill({ contentType: "text/html", body: "<!doctype html><title>Local header test</title>" });
      }
      return route.continue();
    });
    await page.goto(language === "ru" ? "/ru/" : "/");
    await expect(page.locator("html")).toHaveAttribute("data-theme-ready", "true");
    await page.evaluate(() => document.fonts.ready);
    const shell = page.getByRole("banner");
    const link = shell.locator(".tmc-platform-link");
    for (const width of [1121, 1120, 900, 581, 580, 481, 480, 381, 380, 320, 280, 1121]) {
      await page.setViewportSize({ width, height: 1000 });
      const mobile = width <= 1120;
      await expect(shell).toHaveCSS("gap", mobile ? "0px" : "10px");
      await expect(shell).toHaveCSS("border-top-width", mobile ? "1px" : "0px");
      for (const part of [shell.locator(".tmc-header"), link]) {
        await expect(part).toHaveCSS("border-top-width", mobile ? "0px" : "1px");
        if (mobile) {
          await expect(part).toHaveCSS("background-color", "rgba(0, 0, 0, 0)");
          await expect(part).toHaveCSS("box-shadow", "none");
          await expect(part).toHaveCSS("backdrop-filter", "none");
        }
      }
      await expect(link).toBeInViewport({ ratio: 1 });
      await expect(link).toHaveAttribute("href", "https://platform.mutating.tech/");
      await expect.poll(() => shell.evaluate(node => node.scrollWidth - node.clientWidth)).toBeLessThanOrEqual(0);
      await page.evaluate(() => window.scrollTo({ top: 500, behavior: "instant" }));
      await expect(link).toBeInViewport({ ratio: 1 });
      await page.evaluate(() => window.scrollTo({ top: 0, behavior: "instant" }));
      if (mobile) {
        await expect.poll(() => shell.evaluate(header => {
          const icon = header.querySelector(".tmc-platform-link > svg")!.getBoundingClientRect();
          const controls = header.querySelector(".tmc-header-actions")!.getBoundingClientRect();
          const theme = header.querySelector(".tmc-theme-toggle")!.getBoundingClientRect();
          const menu = header.querySelector(".tmc-mobile-menu > summary")!.getBoundingClientRect();
          const bounds = header.getBoundingClientRect();
          const edge = bounds.right - parseFloat(getComputedStyle(header).borderRightWidth);
          const gap = icon.left - controls.right;
          return Math.max(Math.abs(gap - (edge - icon.right)), Math.abs(gap - (menu.left - theme.right)));
        })).toBeLessThanOrEqual(1);
        const menu = shell.locator(".tmc-mobile-menu");
        await menu.locator(":scope > summary").click();
        await expect(menu.locator(".tmc-mobile-menu__links a")).toHaveCount(4);
        await expect(menu.locator(".tmc-language-switcher > summary")).toBeVisible();
        const languages = menu.locator(".tmc-language-switcher");
        await languages.locator("summary").click();
        await expect(languages).toHaveAttribute("open", "");
        await page.keyboard.press("Escape");
        await expect(languages).not.toHaveAttribute("open");
        await expect(menu).toHaveAttribute("open", "");
        await page.keyboard.press("Escape");
        await expect(menu).not.toHaveAttribute("open");
        await expect(menu.locator(":scope > summary")).toBeFocused();
      }
    }
    await page.setViewportSize({ width: 900, height: 180 });
    const menu = shell.locator(".tmc-mobile-menu");
    await menu.locator(":scope > summary").focus();
    await page.keyboard.press("Enter");
    const contact = menu.locator(".tmc-mobile-menu__links a").last();
    await contact.focus();
    await page.setViewportSize({ width: 320, height: 180 });
    await expect(contact).toBeFocused();
    // Brand measurement can reflow the header after the viewport event itself.
    for (let frame = 0; frame < 10; frame++) {
      await page.evaluate(() => new Promise(requestAnimationFrame));
      await expect(contact).toBeInViewport({ ratio: 1 });
    }
    await page.keyboard.press("Escape");
    await page.setViewportSize({ width: 390, height: 800 });
    await menu.locator(":scope > summary").click();
    await page.setViewportSize({ width: 1121, height: 800 });
    await expect(menu).not.toHaveAttribute("open");
    await page.setViewportSize({ width: 390, height: 800 });
    await expect(menu).not.toHaveAttribute("open");
    await page.setViewportSize({ width: 1121, height: 160 });
    await shell.evaluate(header => {
      const sizes = Array.from(header.querySelectorAll<HTMLElement>("*")).map(node => [node, parseFloat(getComputedStyle(node).fontSize) * 2] as const);
      for (const [node, size] of sizes) node.style.fontSize = `${size}px`;
    });
    for (const control of [shell.locator(".tmc-language-switcher--desktop > summary"), shell.locator(".tmc-theme-toggle"), link]) {
      await control.focus();
      await control.scrollIntoViewIfNeeded();
      await expect(control).toBeInViewport({ ratio: 1 });
    }
    await page.setViewportSize({ width: 280, height: 301 });
    await page.addStyleTag({ content: ".tmc-header-shell * {line-height:1.5!important;letter-spacing:.12em!important;word-spacing:.16em!important}" });
    await menu.locator(":scope > summary").click();
    await menu.locator(".tmc-mobile-menu__links a").last().click();
    await expect(page).toHaveURL(/#contact$/);
    for (let frame = 0; frame < 10; frame++) {
      await page.evaluate(() => new Promise(requestAnimationFrame));
      await expect.poll(() => page.locator("#contact").evaluate(node => node.getBoundingClientRect().top)).toBeLessThan(301);
    }
    await page.setViewportSize({ width: 280, height: 1000 });
    await expect(shell).toHaveCSS("position", "sticky");
    await expect.poll(() => page.evaluate(() => document.querySelector("#contact h2")!.getBoundingClientRect().top - document.querySelector(".tmc-header-shell")!.getBoundingClientRect().bottom)).toBeGreaterThanOrEqual(0);
    await expect(page.locator("#contact h2")).toBeInViewport({ ratio: 1 });
    await page.route("https://platform.mutating.tech/", route => route.fulfill({ contentType: "text/html", body: "<!doctype html><title>Platform</title>" }));
    await link.focus();
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL("https://platform.mutating.tech/");
  });
}
