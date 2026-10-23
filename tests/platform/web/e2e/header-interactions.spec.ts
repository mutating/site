import { languageControl, openUserMenu, selectLanguage } from "./header-controls";
import { expect, login, test } from "./fixtures";
import { actionsPeer } from "./actions-peer";

// Overlapping groups retain every adjacent resize transition while giving each
// group its own test budget on slower mobile WebKit runners.
for (const widths of [
  [320, 360, 380, 381, 390],
  [390, 480, 540, 768, 800],
  [800, 900, 1120, 1121, 1240],
  [1240, 1241, 1440, 1920],
]) {
  test(`header controls accept clicks and taps beside the expanded brand at every breakpoint: ${widths[0]}-${widths[widths.length - 1]}px`, async ({ page, isMobile }) => {
    test.setTimeout(90_000);
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto("register?source=header-breakpoints#draft");
    const email = page.locator('input[name="email"]');
    await email.fill("header-draft@example.com");
    await page.locator('input[name="password"]').fill("unsaved header password");
    // End text editing before resizing: macOS WebKit's IME crashes on a focused
    // password field when the emulated phone viewport changes.
    await page.getByRole("heading", { name: "Начнём знакомство." }).click();
    await page.evaluate(() => document.fonts.ready);
    const originalURL = page.url();
    const header = page.getByRole("banner");
    const brand = header.getByRole("link", { name: "The Mutating Company", exact: true });
    const theme = header.getByRole("button", { name: /^(Переключить тему|Toggle color theme)$/ });

    for (const width of widths) {
      await test.step(`${width}px, both languages and themes`, async () => {
        await page.setViewportSize({ width, height: 600 });
        await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
        for (const next of ["en", "ru"]) {
          await brand.hover();
          await expect(languageControl(page)).toBeInViewport();
          await expect(theme).toBeInViewport();
          await selectLanguage(page, next, isMobile ? "touch" : "mouse");
          await expect(page.locator("html")).toHaveAttribute("lang", next);
          await expect(brand).toHaveAttribute("href", `https://mutating.tech/${next === "ru" ? "ru/" : ""}`);
          for (const nextTheme of ["light", "dark"]) {
            await brand.hover();
            if (isMobile) await theme.tap();
            else await theme.click();
            await expect(page.locator("html")).toHaveAttribute("data-theme", nextTheme);
            await expect(theme).toHaveAttribute("aria-pressed", String(nextTheme === "dark"));
          }
          await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
        }
        await expect(email).toHaveValue("header-draft@example.com");
        await expect(page.locator('input[name="password"]')).toHaveValue("unsaved header password");
        expect(page.url()).toBe(originalURL);
      });
    }
    expect(errors).toEqual([]);
  });
}

test("keyboard order reaches all header controls and the cabinet skip link stays first", async ({ page, account, browserName }) => {
  // Safari's native sequential navigation includes buttons with Option-Tab.
  const next = browserName === "webkit" ? "Alt+Tab" : "Tab";
  const previous = browserName === "webkit" ? "Alt+Shift+Tab" : "Shift+Tab";
  await page.goto("login");
  await expect(page.locator(".auth-panel")).toBeVisible();
  const brand = page.getByRole("link", { name: "The Mutating Company", exact: true });
  const theme = page.getByRole("button", { name: "Переключить тему", exact: true });
  const mobile = !await page.locator(".tmc-language-switcher--desktop").isVisible();
  async function headerOrder() {
    await expect(brand).toBeFocused();
    await page.keyboard.press(next);
    if (mobile) {
      await expect(theme).toBeFocused();
      await page.keyboard.press(next);
      await expect(languageControl(page)).toBeFocused();
    } else {
      await expect(languageControl(page)).toBeFocused();
      await page.keyboard.press(next);
      await expect(theme).toBeFocused();
    }
  }
  await page.keyboard.press(next);
  await headerOrder();
  await page.keyboard.press(next);
  await expect(page.getByLabel("Email или ник")).toBeFocused();
  await page.keyboard.press(previous);
  await expect(mobile ? languageControl(page) : theme).toBeFocused();

  await login(page, account);
  await page.reload();
  await expect(page.getByRole("navigation", { name: "Навигация кабинета" })).toBeVisible();
  await page.keyboard.press(next);
  const skip = page.getByRole("link", { name: "Перейти к содержимому" });
  await expect(skip).toBeFocused();
  await expect(skip).toBeInViewport();
  await page.keyboard.press(next);
  await headerOrder();
});

test("header controls work through every cabinet section and logout", async ({ page, account, isMobile }) => {
  await actionsPeer(page);
  await login(page, account);
  const navigation = page.getByRole("navigation", { name: /^(Навигация кабинета|Account navigation)$/ });
  const header = page.getByRole("banner");
  const theme = header.getByRole("button", { name: /^(Переключить тему|Toggle color theme)$/ });
  for (const [path, label] of [["/power-cells", "Power Cells"], ["/actions", "Actions"], ["/cloud", "Облако"], ["/organizations", "Организации"], ["/", "Обзор"]]) {
    await navigation.getByRole("link", { name: label, exact: true }).click();
    await expect(page).toHaveURL(`http://127.0.0.1:18101${path}`);
    await expect(page.locator("main h1")).toBeVisible();
    await selectLanguage(page, "en", isMobile ? "touch" : "mouse");
    await expect(page.locator("html")).toHaveAttribute("lang", "en");
    if (isMobile) await theme.tap();
    else await theme.click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
    await expect(header.getByRole("link", { name: "The Mutating Company", exact: true })).toHaveAttribute("href", "https://mutating.tech/");
    await selectLanguage(page);
    await theme.click();
    await expect(page.locator("html")).toHaveAttribute("lang", "ru");
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  }
  await (await openUserMenu(page)).getByRole("menuitem", { name: "Профиль", exact: true }).click();
  await expect(page).toHaveURL("http://127.0.0.1:18101/profile");
  await selectLanguage(page);
  await theme.click();
  await (await openUserMenu(page)).getByRole("menuitem", { name: "Sign out", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Welcome back." })).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await expect(header).toHaveCount(1);
  await selectLanguage(page);
  await theme.click();
  await expect(page.locator("html")).toHaveAttribute("lang", "ru");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
});
