import { expect, test, type Page } from "@playwright/test";
import { openLanguages, selectLanguage } from "./header-controls";

const site = "https://mutating.tech";
const platform = "https://platform.mutating.tech";

// Exercise real secure sibling origins and cookies while serving every byte locally.
// No request in this suite is allowed to reach the production services.
test.beforeEach(async ({ context }) => {
  await context.route("**/*", async route => {
    const url = new URL(route.request().url());
    const web = url.origin === platform;
    if (!web && url.origin !== site) return route.abort();
    if (web && url.pathname === "/config.js") return route.fulfill({ contentType: "text/javascript", body: 'window.__TMC_API_URL__ = "/v1";' });
    if (web && url.pathname.startsWith("/v1/")) return route.fulfill({ status: 401, json: { detail: "Not signed in" } });
    if (/\/(?:muta-tentacles|tokamak-reactor)\.html/.test(url.pathname)) return route.fulfill({ contentType: "text/html", body: "<!doctype html><title>Header test</title>" });
    const response = await route.fetch({ url: `http://127.0.0.1:${web ? 18101 : 18121}${url.pathname}${url.search}` });
    await route.fulfill({ response });
  });
});

async function ready(page: Page, web: boolean) {
  if (web) await expect(page.locator(".auth-panel")).toBeVisible();
  else await expect(page.locator("html")).toHaveAttribute("data-theme-ready", "true");
  await page.evaluate(() => document.fonts.ready);
}

for (const language of ["en", "ru"]) for (const theme of ["light", "dark"] as const) {
  test(`theme round trip, legacy migration and stale platform storage: ${language}, ${theme}`, async ({ page, context }) => {
    await page.emulateMedia({ colorScheme: theme === "light" ? "dark" : "light" });
    await context.addInitScript(({ language, theme }) => {
      if (sessionStorage.getItem("header-migrated")) return;
      localStorage.setItem("tmc-language", language);
      localStorage.setItem("tmc-theme", location.hostname === "mutating.tech" ? theme : theme === "light" ? "dark" : "light");
      sessionStorage.setItem("header-migrated", "1");
    }, { language, theme });
    await page.goto(`${site}/${language === "ru" ? "ru/" : ""}`);
    await ready(page, false);
    await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
    const cookie = (await context.cookies()).find(cookie => cookie.name === "tmc-theme");
    expect(cookie).toMatchObject({ domain: ".mutating.tech", path: "/", value: theme, secure: true, sameSite: "Lax" });
    await page.locator(".tmc-platform-link").click();
    await ready(page, true);
    await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
    await expect(page.locator(".header-caption")).toHaveCount(0);
    await expect(page.getByRole("banner")).not.toContainText(/account|личный кабинет/i);
    await page.locator(".tmc-theme-toggle").click();
    const next = theme === "light" ? "dark" : "light";
    await expect(page.locator("html")).toHaveAttribute("data-theme", next);
    await page.reload();
    await ready(page, true);
    await expect(page.locator("html")).toHaveAttribute("data-theme", next);
    await page.locator(".tmc-brand").click();
    await ready(page, false);
    await expect(page.locator("html")).toHaveAttribute("data-theme", next);
    await page.locator(".tmc-theme-toggle").click();
    await page.locator(".tmc-platform-link").click();
    await ready(page, true);
    await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
  });
}

for (const scheme of ["light", "dark", "no-preference"] as const) {
  test(`platform follows the static site's system-theme policy: ${scheme}`, async ({ page, context }) => {
    await page.emulateMedia({ colorScheme: scheme });
    if (scheme === "no-preference") await page.addInitScript(() => {
      const match = window.matchMedia.bind(window);
      window.matchMedia = query => query.includes("prefers-color-scheme") ? Object.defineProperty(match(query), "matches", { value: false }) : match(query);
    });
    for (const web of [false, true]) {
      await page.goto(web ? `${platform}/login` : site);
      await ready(page, web);
      await expect(page.locator("html")).toHaveAttribute("data-theme", scheme === "light" ? "light" : "dark");
      expect((await context.cookies()).find(cookie => cookie.name === "tmc-theme")).toBeUndefined();
    }
    if (scheme !== "no-preference") {
      await page.emulateMedia({ colorScheme: scheme === "light" ? "dark" : "light" });
      await expect(page.locator("html")).toHaveAttribute("data-theme", scheme === "light" ? "dark" : "light");
      await page.locator(".tmc-theme-toggle").click();
      await page.emulateMedia({ colorScheme: scheme });
      await expect(page.locator("html")).toHaveAttribute("data-theme", scheme);
    }
  });
}

for (const theme of ["light", "dark"] as const) {
  test(`the shared ${theme} theme is applied before the platform application bundle arrives`, async ({ page, context }) => {
    await page.emulateMedia({ colorScheme: theme === "light" ? "dark" : "light" });
    await context.addCookies([{ name: "tmc-theme", value: theme, domain: ".mutating.tech", path: "/", secure: true, sameSite: "Lax" }]);
    const held: (() => Promise<void>)[] = [];
    await page.route("**/assets/index-*.js", route => { held.push(() => route.fallback()); });
    try {
      await page.goto(`${platform}/login`, { waitUntil: "commit" });
      await expect.poll(() => held.length).toBeGreaterThan(0);
      await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
      await expect(page.locator("#root")).toBeEmpty();
    } finally {
      for (const release of held) await release();
    }
    await ready(page, true);
    await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
  });
}

test("the cookie shares choices when localStorage is denied on both origins", async ({ page }) => {
  await page.addInitScript(() => {
    for (const method of ["getItem", "setItem", "removeItem"] as const) Storage.prototype[method] = () => { throw new DOMException("Denied", "SecurityError"); };
  });
  await page.emulateMedia({ colorScheme: "light" });
  await page.goto(`${site}/ru/`);
  await ready(page, false);
  await page.locator(".tmc-theme-toggle").click();
  await page.locator(".tmc-platform-link").click();
  await ready(page, true);
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.locator(".tmc-theme-toggle").click();
  await page.locator(".tmc-brand").click();
  await ready(page, false);
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
});

test("separate open documents keep the static site's theme policy until reload", async ({ page, context }) => {
  await page.emulateMedia({ colorScheme: "light" });
  await page.goto(site);
  await ready(page, false);
  const other = await context.newPage();
  await other.goto(`${platform}/login`);
  await ready(other, true);
  if (await other.locator("html").getAttribute("data-theme") !== "dark") await other.locator(".tmc-theme-toggle").click();
  else {
    await other.locator(".tmc-theme-toggle").click();
    await other.locator(".tmc-theme-toggle").click();
  }
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await page.emulateMedia({ colorScheme: "dark" });
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await page.reload();
  await ready(page, false);
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await other.locator(".tmc-theme-toggle").click();
  await page.reload();
  await ready(page, false);
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await other.close();
});

for (const language of ["en", "ru"]) for (const theme of ["light", "dark"] as const) {
  test(`language dropdown matches the static site in every layout: ${language}, ${theme}`, async ({ page }) => {
    test.setTimeout(90_000);
    await page.addInitScript(({ language, theme }) => {
      localStorage.setItem("tmc-language", language);
      localStorage.setItem("tmc-theme", theme);
    }, { language, theme });
    await page.emulateMedia({ reducedMotion: "reduce" });
    async function appearance(web: boolean, width: number) {
      await page.setViewportSize({ width, height: 900 });
      await page.goto(web ? `${platform}/register?draft=1#form` : `${site}/${language === "ru" ? "ru/" : ""}`);
      await ready(page, web);
      await page.mouse.move(0, 899);
      const menuSummary = page.locator(".tmc-mobile-menu > summary");
      if (width <= 1120) {
        await expect(menuSummary).toBeVisible();
        await expect(page.locator(".tmc-language-switcher > summary:visible")).toHaveCount(0);
        await expect(page.locator(".tmc-language-switcher--desktop")).toBeHidden();
        await expect(page.locator(".tmc-mobile-menu__icon")).toBeVisible({ visible: width <= 480 });
        await expect(page.locator(".tmc-mobile-menu__label")).toBeVisible({ visible: width > 480 });
      } else {
        await expect(menuSummary).toBeHidden();
        await expect(page.locator(".tmc-language-switcher--desktop > summary")).toBeVisible();
      }
      const dropdown = await openLanguages(page, "keyboard");
      const png = await dropdown.screenshot({ animations: "disabled" });
      const properties = ["display", "position", "font-family", "font-size", "font-weight", "line-height", "color", "background-color", "border", "border-radius", "padding", "gap", "width", "min-height", "backdrop-filter", "box-shadow", "outline", "outline-offset"];
      const styles = await dropdown.evaluate((element, properties) => [element, ...element.querySelectorAll("summary, summary span, .tmc-language-switcher__list, a")].map(node => {
        const style = getComputedStyle(node);
        return Object.fromEntries(properties.map(property => [property, style.getPropertyValue(property)]));
      }), properties);
      if (width <= 1120) await menuSummary.focus();
      const menuStyles = width <= 1120 ? await menuSummary.evaluate((element, properties) => {
        // Reduced-motion focus transitions still have a nonzero duration.
        element.getAnimations().forEach(animation => animation.finish());
        const style = getComputedStyle(element);
        return Object.fromEntries(properties.map(property => [property, style.getPropertyValue(property)]));
      }, properties) : null;
      return { styles, menuStyles, png };
    }
    for (const width of [320, 390, 900, 1040, 1041, 1120, 1121, 1440]) {
      const original = await appearance(false, width);
      const actual = await appearance(true, width);
      expect(actual.styles, `${width}px computed styles`).toEqual(original.styles);
      expect(actual.menuStyles, `${width}px mobile menu button`).toEqual(original.menuStyles);
      // Backgrounds differ below the translucent desktop popup; compare opaque mobile
      // menus pixel for pixel in Firefox, which paints deterministic antialiasing.
      if (width <= 1120 && test.info().project.name === "firefox") {
        if (!actual.png.equals(original.png)) {
          await test.info().attach(`${width}-site.png`, { body: original.png, contentType: "image/png" });
          await test.info().attach(`${width}-platform.png`, { body: actual.png, contentType: "image/png" });
        }
        expect(actual.png.equals(original.png), `${width}px dropdown pixels`).toBe(true);
      }
      await selectLanguage(page);
      expect(page.url()).toBe(`${platform}/register?draft=1#form`);
    }
  });
}
