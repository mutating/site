import { expect, type Locator, type Page } from "@playwright/test";

export async function openOrganizationSettings(page: Page) {
  const dialog = page.getByRole("dialog", { name: "Настройки организации", exact: true });
  if (!(await dialog.isVisible())) {
    await page.getByRole("button", { name: "Настройки организации", exact: true }).click();
  }
  await expect(dialog).toBeVisible();
  return dialog;
}

export async function openOrganizationTokens(page: Page) {
  const dialog = await openOrganizationSettings(page);
  await dialog.getByRole("tab", { name: "API-токены", exact: true }).click();
  return dialog;
}

export async function switchOrganizationPeople(page: Page, section: "Участники" | "Приглашения") {
  await page.getByRole("tablist", { name: "Команда", exact: true }).getByRole("tab", { name: section, exact: true }).click();
}

export async function openOrganizationMember(member: Locator) {
  const summary = member.locator(".organization-member-summary");
  await expect(summary).toBeVisible();
  if ((await summary.getAttribute("aria-expanded")) !== "true") await summary.click();
  return summary;
}
