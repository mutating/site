import { expect, startSession, test } from "./fixtures";
import { openLanguages, openUserMenu } from "./header-controls";
import type { Page } from "@playwright/test";

async function enlargeHeader(page: Page) {
  await page.evaluate(() => document.fonts.ready);
  await page.getByRole("banner").evaluate(header => {
    const sizes = Array.from(header.querySelectorAll<HTMLElement>("*")).map(node => [node, parseFloat(getComputedStyle(node).fontSize) * 2] as const);
    for (const [node, size] of sizes) node.style.fontSize = `${size}px`;
  });
}

test("the merged header keeps equal gaps between language, theme, avatar and its edge", async ({ page, account }) => {
  await startSession(page, account);
  await page.goto("");
  const header = page.getByRole("banner");
  const avatar = header.locator(".user-menu .avatar");
  await expect(avatar).toBeVisible();
  for (const scale of [1, 2]) {
    if (scale === 2) await enlargeHeader(page);
    for (const width of scale === 1 ? [1120, 900, 581, 580, 481, 480, 360, 280] : [1120, 900, 580, 480, 360]) {
      await page.setViewportSize({ width, height: 800 });
      await expect.poll(() => header.evaluate(header => {
        const circle = header.querySelector(".user-menu .avatar")!.getBoundingClientRect();
        const theme = header.querySelector(".tmc-theme-toggle")!.getBoundingClientRect();
        const language = header.querySelector(".tmc-language-switcher--standalone > summary")!.getBoundingClientRect();
        const bounds = header.getBoundingClientRect();
        const edge = bounds.right - parseFloat(getComputedStyle(header).borderRightWidth);
        const gap = circle.left - theme.right;
        return Math.max(Math.abs(gap - (edge - circle.right)), Math.abs(gap - (theme.left - language.right)));
      })).toBeLessThanOrEqual(1);
    }
  }
  const button = header.locator(".user-menu > button");
  await page.keyboard.press("Tab");
  await button.focus();
  await expect(button).toBeFocused();
  expect(await button.evaluate(button => {
    const bounds = button.getBoundingClientRect(), style = getComputedStyle(button);
    const theme = button.closest("header")!.querySelector(".tmc-theme-toggle")!.getBoundingClientRect();
    const outside = Math.max(0, parseFloat(style.outlineWidth) + parseFloat(style.outlineOffset));
    return bounds.left - outside - theme.right;
  })).toBeGreaterThanOrEqual(-1);
  await page.keyboard.press("Enter");
  await expect(page.getByRole("menuitem")).toHaveCount(2);
});

test("avatar text and images stay centered inside the button with enlarged line spacing", async ({ page, account }) => {
  await page.setViewportSize({ width: 1120, height: 800 });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await startSession(page, account);
  await page.goto("");
  const avatar = page.getByRole("banner").locator(".user-menu .avatar");
  await expect(avatar).toBeVisible();
  await enlargeHeader(page);
  const offsets = () => avatar.evaluate(avatar => {
    const circle = avatar.getBoundingClientRect();
    const button = avatar.closest("button")!.getBoundingClientRect();
    const range = document.createRange();
    range.selectNodeContents(avatar);
    const content = range.getBoundingClientRect();
    return {
      circle: Math.abs(circle.y + circle.height / 2 - button.y - button.height / 2),
      text: Math.abs(content.y + content.height / 2 - circle.y - circle.height / 2),
    };
  });
  for (const lineHeight of ["normal", "1.5", "2"]) {
    await avatar.evaluate((node: HTMLElement, lineHeight) => { node.style.lineHeight = lineHeight; }, lineHeight);
    for (const width of [1120, 360, 1121, 580]) {
      await page.setViewportSize({ width, height: 800 });
      await expect.poll(async () => (await offsets()).circle).toBeLessThanOrEqual(1);
      await expect.poll(async () => (await offsets()).text).toBeLessThanOrEqual(1);
    }
  }
  // A non-square image must fill the same cell without moving the avatar.
  await avatar.evaluate(async avatar => {
    const image = new Image();
    image.src = 'data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" width="160" height="80"><rect width="160" height="80" fill="red"/></svg>';
    await image.decode();
    avatar.replaceChildren(image);
  });
  const image = avatar.locator("img");
  await expect.poll(() => image.evaluate(image => {
    const bounds = image.getBoundingClientRect(), circle = image.parentElement!.getBoundingClientRect();
    return Math.abs(bounds.y + bounds.height / 2 - circle.y - circle.height / 2);
  })).toBeLessThanOrEqual(1);
  await expect(image).toBeVisible();
});

test("the enlarged avatar stays level with languages and theme while the header reflows", async ({ page, account }) => {
  await page.setViewportSize({ width: 1120, height: 800 });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await startSession(page, account);
  await page.goto("");
  const header = page.getByRole("banner");
  const avatar = header.locator(".user-menu > button");
  await expect(avatar).toBeVisible();
  await enlargeHeader(page);
  const alignment = () => header.evaluate(header => {
    const controls = header.querySelector(".tmc-header-actions")!.getBoundingClientRect();
    const avatar = header.querySelector(".user-menu > button")!.getBoundingClientRect();
    return Math.abs(avatar.top + avatar.height / 2 - controls.top - controls.height / 2);
  });
  for (const width of [1120, 580, 1121, 1440, 1120]) {
    await page.setViewportSize({ width, height: 800 });
    await expect.poll(alignment).toBeLessThanOrEqual(1);
    await expect(avatar).toBeInViewport({ ratio: 1 });
  }
  await page.setViewportSize({ width: 280, height: 800 });
  await expect.poll(() => header.evaluate(header => {
    const main = header.querySelector(".tmc-header")!.getBoundingClientRect();
    return header.querySelector(".user-menu")!.getBoundingClientRect().top - main.bottom;
  })).toBeGreaterThanOrEqual(0);
  await page.setViewportSize({ width: 1120, height: 800 });
  await expect.poll(alignment).toBeLessThanOrEqual(1);
  await avatar.click();
  await page.getByRole("menuitem", { name: /^(Профиль|Profile)$/ }).click();
  await expect(page).toHaveURL(/\/profile$/);
});

test("the avatar menu reopens after focus returns from an outside iframe", async ({ page, account }) => {
  await page.setViewportSize({ width: 390, height: 780 });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await startSession(page, account);
  await page.goto("");
  await page.evaluate(() => {
    const iframe = document.createElement("iframe");
    iframe.id = "outside-header-frame";
    iframe.srcdoc = "<!doctype html><button>Frame action</button>";
    iframe.style.cssText = "position:fixed;left:20px;bottom:20px;width:220px;height:50px;z-index:100";
    document.body.append(iframe);
  });
  const trigger = page.getByRole("banner").getByRole("button", { name: /^(Меню пользователя|User menu)$/ });
  const menu = page.getByRole("menu", { name: /^(Меню пользователя|User menu)$/ });
  const iframe = page.locator("#outside-header-frame");
  for (let attempt = 0; attempt < 3; attempt++) {
    await trigger.click();
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    await expect(menu).toBeVisible();
    await iframe.contentFrame().getByRole("button", { name: "Frame action", exact: true }).click();
    await expect(iframe).toBeFocused();
    await expect(menu).toHaveCount(0);
  }
  await trigger.click();
  await menu.getByRole("menuitem", { name: /^(Профиль|Profile)$/ }).click();
  await expect(page).toHaveURL(/\/profile$/);
});

test.describe("enlarged popup stacking", () => {
  test.use({ deviceScaleFactor: 2 });
  test("the upward user menu remains clickable over other header controls", async ({ page, account }) => {
    await page.setViewportSize({ width: 280, height: 301 });
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.addInitScript(() => {
      localStorage.setItem("tmc-language", "ru");
      localStorage.setItem("tmc-theme", "dark");
    });
    await startSession(page, account);
    await page.goto("");
    const trigger = page.getByRole("button", { name: "Меню пользователя", exact: true });
    await expect(trigger).toBeVisible();
    await enlargeHeader(page);
    await trigger.focus();
    await page.keyboard.press("ArrowUp");
    const menu = page.getByRole("menu", { name: "Меню пользователя" });
    const logout = menu.getByRole("menuitem", { name: "Выйти", exact: true });
    await expect(menu).toHaveAttribute("data-tmc-above", "");
    await expect(logout).toBeFocused();
    await expect(logout).toBeInViewport({ ratio: 1 });
    const box = (await logout.boundingBox())!;
    await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
    await expect(page).toHaveURL(/\/login$/);
  });
});

test("the first outside click dismisses the user menu after a canceled link drag", async ({ page, account, isMobile }) => {
  test.skip(isMobile, "This scenario uses native mouse link dragging.");
  await page.setViewportSize({ width: 390, height: 780 });
  await startSession(page, account);
  await page.goto("");
  const menu = await openUserMenu(page);
  const profile = menu.getByRole("menuitem", { name: /^(Профиль|Profile)$/ });
  await profile.evaluate(node => {
    (window as any).headerDragEnded = false;
    node.addEventListener("dragend", () => { (window as any).headerDragEnded = true; }, { once: true });
  });
  const box = (await profile.boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(15, 250, { steps: 10 });
  await page.mouse.up();
  await expect.poll(() => page.evaluate(() => (window as any).headerDragEnded)).toBe(true);
  await expect(menu).toBeVisible();
  const previousTheme = await page.locator("html").getAttribute("data-theme");
  await page.getByRole("banner").locator(".tmc-theme-toggle").click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", previousTheme === "dark" ? "light" : "dark");
  await expect(menu).toHaveCount(0);
});

test("pointer selection still works after opening the user menu with the keyboard", async ({ page, account, isMobile }) => {
  await startSession(page, account);
  await page.goto("");
  const trigger = page.getByRole("banner").getByRole("button", { name: /^(Меню пользователя|User menu)$/ });
  await trigger.focus();
  await page.keyboard.press("Enter");
  const profile = page.getByRole("menuitem", { name: /^(Профиль|Profile)$/ });
  await expect(profile).toBeFocused();
  if (isMobile) await profile.tap(); else await profile.click();
  await expect(page).toHaveURL(/\/profile$/);
  await trigger.focus();
  await page.keyboard.press("Enter");
  const logout = page.getByRole("menuitem", { name: /^(Выйти|Sign out)$/ });
  if (isMobile) await logout.tap(); else await logout.click();
  await expect(page).toHaveURL(/\/login$/);
});

test("keyboard navigation closes languages before opening the user menu", async ({ page, account, browserName }) => {
  await page.setViewportSize({ width: 390, height: 800 });
  await startSession(page, account);
  await page.goto("");
  const avatar = page.getByRole("button", { name: "Меню пользователя", exact: true });
  await expect(avatar).toBeVisible();
  const language = await openLanguages(page, "keyboard");
  const next = browserName === "webkit" ? "Alt+Tab" : "Tab";
  await page.keyboard.press(next);
  await expect(language.locator("a")).toBeFocused();
  await page.keyboard.press(next);
  await expect(language).not.toHaveAttribute("open");
  await page.keyboard.press(next);
  await expect(avatar).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("menu", { name: "Меню пользователя" })).toBeVisible();
  await expect(page.locator(".tmc-language-switcher[open]")).toHaveCount(0);
});

test("short desktop windows keep the enlarged user menu fully reachable", async ({ page, account }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.setViewportSize({ width: 1121, height: 180 });
  await startSession(page, account);
  await page.goto("");
  const banner = page.getByRole("banner");
  const avatar = banner.getByRole("button", { name: /^(Меню пользователя|User menu)$/ });
  await expect(avatar).toBeVisible();
  await enlargeHeader(page);
  await avatar.focus();
  await page.keyboard.press("ArrowUp");
  const logout = page.getByRole("menuitem", { name: /^(Выйти|Sign out)$/ });
  await expect(logout).toBeFocused();
  for (let frame = 0; frame < 10; frame++) {
    await page.evaluate(() => new Promise(requestAnimationFrame));
    await expect(logout).toBeInViewport({ ratio: 1 });
  }
});

test("content anchors stay visible when an enlarged header scrolls with the page", async ({ page, account }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.setViewportSize({ width: 280, height: 301 });
  await startSession(page, account);
  await page.goto("");
  const banner = page.getByRole("banner");
  await expect(banner.getByRole("button", { name: /^(Меню пользователя|User menu)$/ })).toBeVisible();
  await enlargeHeader(page);
  await page.addStyleTag({ content: ".site-header * {line-height:1.5!important;letter-spacing:.12em!important;word-spacing:.16em!important}" });
  await expect(banner).toHaveCSS("position", "relative");
  for (let frame = 0; frame < 4; frame++) await page.evaluate(() => new Promise(requestAnimationFrame));
  const main = page.locator("main#main");
  await main.evaluate(node => {
    node.style.minHeight = "1800px";
    node.scrollIntoView({ block: "start", behavior: "instant" });
  });
  await expect.poll(() => main.evaluate(node => node.getBoundingClientRect().top)).toBeLessThan(301);
  await page.setViewportSize({ width: 280, height: 1000 });
  await expect(banner).toHaveCSS("position", "sticky");
  for (let frame = 0; frame < 4; frame++) await page.evaluate(() => new Promise(requestAnimationFrame));
  await main.evaluate(node => node.scrollIntoView({ block: "start", behavior: "instant" }));
  await expect.poll(() => page.evaluate(() => document.querySelector("main#main")!.getBoundingClientRect().top - document.querySelector(".site-header")!.getBoundingClientRect().bottom)).toBeGreaterThanOrEqual(0);
});

test("an enlarged skip link stays hidden until focused and still reaches the content", async ({ page, account }) => {
  await page.setViewportSize({ width: 381, height: 568 });
  await page.addInitScript(() => localStorage.setItem("tmc-language", "ru"));
  await startSession(page, account);
  await page.goto("profile");
  const skip = page.locator(".skip-link");
  await page.evaluate(() => document.fonts.ready);
  await skip.evaluate(node => { node.style.fontSize = `${parseFloat(getComputedStyle(node).fontSize) * 2}px`; });
  await expect(skip).not.toBeFocused();
  await expect.poll(() => skip.evaluate(node => node.getBoundingClientRect().bottom)).toBeLessThanOrEqual(0);
  await skip.focus();
  await expect(skip).toBeInViewport({ ratio: 1 });
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/profile#main$/);
  await page.getByRole("banner").locator(".tmc-theme-toggle").focus();
  await expect(skip).not.toBeFocused();
  await expect.poll(() => skip.evaluate(node => node.getBoundingClientRect().bottom)).toBeLessThanOrEqual(0);
});

for (const language of ["en", "ru"]) for (const theme of ["light", "dark"]) {
  test(`one mobile header keeps languages and the complete user menu accessible: ${language}, ${theme}`, async ({ page, account, isMobile }) => {
    test.setTimeout(90_000);
    await page.addInitScript(({ language, theme }) => {
      localStorage.setItem("tmc-language", language);
      localStorage.setItem("tmc-theme", theme);
    }, { language, theme });
    await startSession(page, account);
    await page.goto("");
    const banner = page.getByRole("banner");
    const trigger = banner.getByRole("button", { name: language === "ru" ? "Меню пользователя" : "User menu", exact: true });
    for (const width of [1440, 1121, 1120, 900, 581, 580, 481, 480, 381, 380, 320, 280, 1121, 390]) {
      await page.setViewportSize({ width, height: 800 });
      await expect(banner.locator(".tmc-mobile-menu")).toHaveCount(0);
      await expect(banner.locator(".tmc-language-switcher > summary:visible")).toHaveCount(1);
      await expect(banner).toHaveCSS("gap", width <= 1120 ? "0px" : "10px");
      await expect(trigger).toHaveCSS("border-top-width", width <= 1120 ? "0px" : "1px");
      await expect(trigger).toBeInViewport({ ratio: 1 });
      await expect.poll(() => banner.evaluate(node => node.scrollWidth - node.clientWidth)).toBeLessThanOrEqual(0);
      if (isMobile) await trigger.tap(); else await trigger.click();
      const menu = await openUserMenu(page);
      await expect(menu.getByRole("menuitem")).toHaveText(language === "ru" ? ["Профиль", "Выйти"] : ["Profile", "Sign out"]);
      await expect(menu).toBeInViewport({ ratio: 1 });
      const band = (await banner.boundingBox())!;
      expect((await menu.boundingBox())!.y, "the user menu must open below the entire header").toBeGreaterThanOrEqual(band.y + band.height);
      const dropdown = await openLanguages(page, isMobile ? "touch" : "mouse");
      await expect(menu).toHaveCount(0);
      await expect(dropdown.locator("a")).toBeInViewport({ ratio: 1 });
      await page.keyboard.press("Escape");
      await expect(dropdown).not.toHaveAttribute("open");
    }
    await trigger.focus();
    await page.keyboard.press("ArrowDown");
    const profile = page.getByRole("menuitem", { name: language === "ru" ? "Профиль" : "Profile", exact: true });
    await expect(profile).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/\/profile$/);
    const menu = await openUserMenu(page);
    await menu.getByRole("menuitem", { name: language === "ru" ? "Выйти" : "Sign out", exact: true }).click();
    await expect(page).toHaveURL(/\/login$/);
    await expect(banner.locator(".tmc-mobile-menu")).toHaveCount(0);
    await expect(banner.locator(".tmc-language-switcher > summary")).toBeVisible();
  });
}
