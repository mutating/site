import { expect, test } from "./fixtures";
import { languageControl, openLanguages, selectLanguage } from "./header-controls";

for (const language of ["en", "ru"]) for (const theme of ["light", "dark"] as const) {
  test(`language collapses into the menu and works with pointer input: ${language}, ${theme}`, async ({ page, isMobile }) => {
    test.setTimeout(90_000);
    await page.addInitScript(({ language, theme }) => {
      localStorage.setItem("tmc-language", language);
      localStorage.setItem("tmc-theme", theme);
    }, { language, theme });
    await page.goto("register?source=mobile-menu#draft");
    const draft = page.locator('input[name="email"]');
    await draft.fill("mobile-menu@example.com");
    const input = isMobile ? "touch" : "mouse";
    for (const width of [320, 480, 481, 900, 1120, 1121, 1440]) {
      await page.setViewportSize({ width, height: 800 });
      await page.mouse.move(0, 799);
      const menu = page.locator(".tmc-mobile-menu");
      const menuSummary = menu.locator(":scope > summary");
      await expect(page.locator(".tmc-language-toggle")).toHaveCount(0);
      await expect(page.locator(".tmc-theme-toggle")).toBeVisible();
      await expect(page.locator(".tmc-language-switcher > summary:visible")).toHaveCount(width <= 1120 ? 0 : 1);
      if (width <= 1120) {
        await expect(menuSummary).toBeVisible();
        await expect(page.locator(".tmc-language-switcher--desktop")).toBeHidden();
        await expect(menu.locator("nav")).toBeHidden();
        await expect(menu.locator(".tmc-mobile-menu__icon")).toBeVisible({ visible: width <= 480 });
        await expect(menu.locator(".tmc-mobile-menu__label")).toBeVisible({ visible: width > 480 });
        const dropdown = await openLanguages(page, input);
        await expect(page.locator(".tmc-language-switcher > summary:visible")).toHaveCount(1);
        await expect(dropdown.locator("summary")).toHaveText(language.toUpperCase());
        await expect(dropdown.locator("a")).toBeVisible();
        if (isMobile) await draft.tap(); else await draft.click();
        await expect(menu).not.toHaveAttribute("open");
        await expect(dropdown).not.toHaveAttribute("open");
        await expect(page.locator(".tmc-language-switcher > summary:visible")).toHaveCount(0);
      } else await expect(menu).toBeHidden();
      await selectLanguage(page, language === "en" ? "ru" : "en", input);
      await selectLanguage(page, language, input);
      await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
      await expect(draft).toHaveValue("mobile-menu@example.com");
      await expect(page).toHaveURL(/register\?source=mobile-menu#draft$/);
    }
  });
}

for (const language of ["en", "ru"]) for (const theme of ["light", "dark"] as const) for (const input of ["keyboard", "pointer"] as const) {
  test(`short viewport keeps language choices reachable: ${input}, ${language}, ${theme}`, async ({ page, isMobile }) => {
    test.setTimeout(90_000);
    await page.setViewportSize({ width: 320, height: 180 });
    await page.addInitScript(({ language, theme }) => {
      localStorage.setItem("tmc-language", language);
      localStorage.setItem("tmc-theme", theme);
    }, { language, theme });
    await page.goto("register?source=short-menu#draft");
    const draft = page.locator('input[name="email"]');
    await draft.fill("short-menu@example.com");
    const activate = input === "keyboard" ? "keyboard" : isMobile ? "touch" : "mouse";
    for (const viewport of [{ width: 320, height: 180 }, { width: 280, height: 180 }, { width: 568, height: 220 }, { width: 900, height: 180 }, { width: 320, height: 300 }, { width: 320, height: 301 }]) {
      await page.setViewportSize(viewport);
      const dropdown = await openLanguages(page, activate);
      const option = dropdown.locator("a");
      const selected = await option.getAttribute("lang");
      if (input === "keyboard") await option.focus();
      else await option.scrollIntoViewIfNeeded();
      await expect(option).toBeInViewport({ ratio: 1 });
      if (input === "keyboard") await page.keyboard.press("Enter");
      else if (isMobile) await option.tap();
      else await option.click();
      await expect(page.locator("html")).toHaveAttribute("lang", selected!);
      await expect(page.locator(".tmc-mobile-menu")).not.toHaveAttribute("open");
      await expect(dropdown).not.toHaveAttribute("open");
      await expect(draft).toHaveValue("short-menu@example.com");
      await expect(page).toHaveURL(/register\?source=short-menu#draft$/);
      await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
    }
  });
}

test("language dropdown supports Escape, outside clicks, layout changes and keyboard selection", async ({ page }) => {
  await page.goto("register?source=dropdown#draft");
  await page.locator('input[name="email"]').fill("dropdown@example.com");
  for (const width of [320, 390, 900, 1040, 1041, 1120, 1121, 1440]) {
    await page.setViewportSize({ width, height: 800 });
    const dropdown = await openLanguages(page, "keyboard");
    await expect(dropdown.locator("a")).toHaveCount(1);
    await expect(dropdown.locator("a")).toHaveAttribute("lang", "en");
    await dropdown.locator("a").focus();
    await page.keyboard.press("Escape");
    await expect(dropdown).not.toHaveAttribute("open");
    await expect(dropdown.locator("summary")).toBeFocused();
    if (width <= 1120) {
      await expect(page.locator(".tmc-mobile-menu")).toHaveAttribute("open", "");
      await page.keyboard.press("Escape");
      await expect(page.locator(".tmc-mobile-menu")).not.toHaveAttribute("open");
      await expect(languageControl(page)).toBeFocused();
    }
    await openLanguages(page);
    await page.locator('input[name="email"]').click();
    await expect(dropdown).not.toHaveAttribute("open");
    await expect(page.locator(".tmc-mobile-menu")).not.toHaveAttribute("open");
    await selectLanguage(page, "en", "keyboard");
    await expect(languageControl(page)).toBeFocused();
    await selectLanguage(page, "ru", "keyboard");
    await expect(page.locator('input[name="email"]')).toHaveValue("dropdown@example.com");
    await expect(page).toHaveURL(/register\?source=dropdown#draft$/);
  }
  await openLanguages(page);
  await page.setViewportSize({ width: 390, height: 800 });
  await expect(page.locator(".tmc-language-switcher--desktop")).not.toHaveAttribute("open");
  await openLanguages(page);
  await page.setViewportSize({ width: 1440, height: 800 });
  await expect(page.locator(".tmc-mobile-menu")).not.toHaveAttribute("open");
  await expect(page.locator(".tmc-language-switcher--mobile")).not.toHaveAttribute("open");
});

test("desktop pointer hover opens the same dropdown as the static site", async ({ page, isMobile }) => {
  test.skip(isMobile, "Requires a fine pointer with hover");
  await page.goto("login");
  const dropdown = page.locator(".tmc-language-switcher--desktop");
  await dropdown.locator("summary").hover();
  await expect(dropdown).toHaveAttribute("open", "");
  await dropdown.locator("a").hover();
  await expect(dropdown).toHaveAttribute("open", "");
  await page.mouse.move(1, 500);
  await expect(dropdown).not.toHaveAttribute("open");
});

test("a native new-tab language choice preserves the current form and survives a later in-place switch", async ({ page, context, browserName }) => {
  test.skip(browserName === "webkit", "Headless WebKit does not expose native middle-click tab creation");
  await page.goto("register?source=new-tab#draft");
  await page.locator('input[name="email"]').fill("original@example.com");
  const dropdown = await openLanguages(page);
  const opened = context.waitForEvent("page");
  await dropdown.locator('[data-language-option="en"]').click({ button: "middle" });
  const popup = await opened;
  try {
    await expect(popup.getByRole("heading", { name: "Let’s get started." })).toBeVisible();
    await expect(popup).toHaveURL(/register\?source=new-tab#draft$/);
    await expect(page.locator("html")).toHaveAttribute("lang", "ru");
    await expect(page.locator('input[name="email"]')).toHaveValue("original@example.com");
    await selectLanguage(popup, "ru");
    await popup.reload();
    await expect(popup.locator("html")).toHaveAttribute("lang", "ru");
    await expect(popup.getByRole("heading", { name: "Начнём знакомство." })).toBeVisible();
  } finally {
    await popup.close();
  }
});
