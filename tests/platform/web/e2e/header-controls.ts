import { expect, type Page } from "@playwright/test";

export const languageControl = (page: Page) => page.locator(".tmc-language-switcher > summary:visible, .tmc-mobile-menu > summary:visible").first();
export const languageSummary = (page: Page) => page.locator(".tmc-language-switcher > summary:visible");

export async function openLanguages(page: Page, input: "mouse" | "keyboard" | "touch" = "mouse") {
  const activate = async (selector: string) => {
    const control = page.locator(selector).filter({ visible: true });
    if (input === "keyboard") { await control.focus(); await page.keyboard.press("Space"); }
    else if (input === "touch") await control.tap();
    else await control.click();
  };
  if (!await page.locator(".tmc-language-switcher--desktop").isVisible() && !await page.locator(".tmc-mobile-menu").evaluate((menu: HTMLDetailsElement) => menu.open)) {
    await activate(".tmc-mobile-menu > summary");
  }
  const variant = await page.locator(".tmc-language-switcher--desktop").isVisible() ? "desktop" : "mobile";
  const switcher = page.locator(`.tmc-language-switcher--${variant}`);
  if (!await switcher.evaluate((details: HTMLDetailsElement) => details.open)) await activate(".tmc-language-switcher > summary");
  await expect(switcher).toHaveAttribute("open", "");
  return switcher;
}

export async function selectLanguage(page: Page, code?: string, input: "mouse" | "keyboard" | "touch" = "mouse") {
  code ??= await page.locator("html").getAttribute("lang") === "ru" ? "en" : "ru";
  const switcher = await openLanguages(page, input);
  const option = switcher.locator(`[data-language-option="${code}"]`);
  if (input === "keyboard") { await option.focus(); await page.keyboard.press("Enter"); }
  else if (input === "touch") await option.tap();
  else await option.click();
  await expect(page.locator("html")).toHaveAttribute("lang", code);
  await expect(switcher).not.toHaveAttribute("open");
  await expect(page.locator(".tmc-mobile-menu")).not.toHaveAttribute("open");
}
