import { expect, login, test } from "./fixtures";
import { actionsPeer } from "./actions-peer";

test("header controls accept clicks and taps beside the expanded brand at every breakpoint", async ({ page, isMobile }) => {
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
  const language = header.getByRole("button", { name: /^(Выбрать язык|Choose language)$/ });
  const theme = header.getByRole("button", { name: /^(Переключить тему|Toggle color theme)$/ });

  for (const width of [320, 360, 380, 381, 390, 480, 540, 768, 800, 900, 1120, 1121, 1240, 1241, 1440, 1920]) {
    await test.step(`${width}px, both languages and themes`, async () => {
      await page.setViewportSize({ width, height: 600 });
      await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
      for (const next of ["en", "ru"]) {
        await brand.hover();
        await expect(language).toBeInViewport();
        await expect(theme).toBeInViewport();
        if (isMobile) await language.tap();
        else await language.click();
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

test("keyboard order reaches all header controls and the cabinet skip link stays first", async ({ page, account, browserName }) => {
  // Safari's native sequential navigation includes buttons with Option-Tab.
  const next = browserName === "webkit" ? "Alt+Tab" : "Tab";
  const previous = browserName === "webkit" ? "Alt+Shift+Tab" : "Shift+Tab";
  await page.goto("login");
  await expect(page.locator(".auth-panel")).toBeVisible();
  const brand = page.getByRole("link", { name: "The Mutating Company", exact: true });
  const language = page.getByRole("button", { name: "Выбрать язык", exact: true });
  const theme = page.getByRole("button", { name: "Переключить тему", exact: true });
  await page.keyboard.press(next);
  await expect(brand).toBeFocused();
  await page.keyboard.press(next);
  await expect(language).toBeFocused();
  await page.keyboard.press(next);
  await expect(theme).toBeFocused();
  await page.keyboard.press(next);
  await expect(page.getByLabel("Email или ник")).toBeFocused();
  await page.keyboard.press(previous);
  await expect(theme).toBeFocused();

  await login(page, account);
  await page.reload();
  await expect(page.getByRole("navigation", { name: "Навигация кабинета" })).toBeVisible();
  await page.keyboard.press(next);
  const skip = page.getByRole("link", { name: "Перейти к содержимому" });
  await expect(skip).toBeFocused();
  await expect(skip).toBeInViewport();
  await page.keyboard.press(next);
  await expect(brand).toBeFocused();
  await page.keyboard.press(next);
  await expect(language).toBeFocused();
  await page.keyboard.press(next);
  await expect(theme).toBeFocused();
});

test("header controls work through every cabinet section and logout", async ({ page, account, isMobile }) => {
  await actionsPeer(page);
  await login(page, account);
  const navigation = page.getByRole("navigation", { name: /^(Навигация кабинета|Account navigation)$/ });
  const header = page.getByRole("banner");
  const language = header.getByRole("button", { name: /^(Выбрать язык|Choose language)$/ });
  const theme = header.getByRole("button", { name: /^(Переключить тему|Toggle color theme)$/ });
  for (const [path, label] of [["/power-cells", "Power Cells"], ["/actions", "Actions"], ["/cloud", "Облако"], ["/organizations", "Организации"], ["/", "Обзор"]]) {
    await navigation.getByRole("link", { name: label, exact: true }).click();
    await expect(page).toHaveURL(`http://127.0.0.1:18101${path}`);
    await expect(page.locator("main h1")).toBeVisible();
    if (isMobile) await language.tap();
    else await language.click();
    await expect(page.locator("html")).toHaveAttribute("lang", "en");
    if (isMobile) await theme.tap();
    else await theme.click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
    await expect(header.getByRole("link", { name: "The Mutating Company", exact: true })).toHaveAttribute("href", "https://mutating.tech/");
    await language.click();
    await theme.click();
    await expect(page.locator("html")).toHaveAttribute("lang", "ru");
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  }
  await page.getByRole("link", { name: "Профиль", exact: true }).filter({ visible: true }).click();
  await expect(page).toHaveURL("http://127.0.0.1:18101/profile");
  await language.click();
  await theme.click();
  await page.getByRole("button", { name: "Sign out", exact: true }).filter({ visible: true }).click();
  await expect(page.getByRole("heading", { name: "Welcome back." })).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await expect(header).toHaveCount(1);
  await language.click();
  await theme.click();
  await expect(page.locator("html")).toHaveAttribute("lang", "ru");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
});
