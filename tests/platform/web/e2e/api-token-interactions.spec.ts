import { expect, login, test } from "./fixtures";
import type { Locator } from "@playwright/test";

/**
 * Navigation and token actions must work through native keyboard activation and
 * touch as well as mouse clicks. Exercise the complete UI flow, including both
 * cancellation paths, copy feedback, exact revoke confirmation and reloading.
 */
for (const input of ["mouse", "keyboard", "touch"] as const) {
  test(`token navigation, creation, copy and revoke with ${input}`, async ({
    page,
    account,
    isMobile,
    browserName,
  }) => {
    test.skip(
      input === "touch" && !isMobile,
      "Touch is exercised on the phone projects.",
    );
    await page.addInitScript(() =>
      Object.defineProperty(navigator, "clipboard", {
        configurable: true,
        value: { writeText: async () => {} },
      }),
    );
    const activate = async (control: Locator) => {
      if (input === "keyboard") {
        await expect(control).toBeVisible();
        await expect(control).toBeEnabled();
        await control.press("Enter");
      } else if (input === "touch") await control.tap();
      else await control.click();
    };
    await login(page, account);
    await activate(
      page
        .getByRole("banner")
        .getByRole("button", { name: "Меню пользователя", exact: true }),
    );
    await activate(
      page.getByRole("menuitem", { name: "Профиль", exact: true }),
    );
    await expect(page).toHaveURL(/\/profile$/);
    await activate(
      page.getByRole("link", { name: "API-токены ↗", exact: true }),
    );
    await expect(page).toHaveURL(/\/api-tokens$/);
    const owner = page.getByLabel("Владелец токенов", { exact: true });
    await expect(owner).toBeEnabled();
    // macOS headless browsers delegate the native popup outside the page;
    // selectOption commits its choice without depending on that OS popup.
    if (input === "keyboard" && process.platform !== "darwin") {
      if (browserName !== "firefox") await owner.press("Space");
      await owner.press("ArrowDown");
      await owner.press("Enter");
      await owner.press("Tab");
    } else await owner.selectOption("personal");
    await expect(owner).toHaveValue("personal");
    const trigger = page.getByRole("button", {
      name: "Создать токен",
      exact: true,
    });
    const dialog = page.getByRole("dialog");
    await activate(trigger);
    await activate(
      dialog.getByRole("button", { name: "Закрыть диалог", exact: true }),
    );
    await expect(trigger).toBeFocused();
    await activate(trigger);
    await page.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);
    await expect(trigger).toBeFocused();
    await activate(trigger);
    const name = `Interactive ${input}`;
    const nameField = dialog.getByLabel("Название токена", { exact: true });
    if (input === "keyboard") await nameField.pressSequentially(name);
    else await nameField.fill(name);
    if (input === "keyboard") await nameField.press("Enter");
    else
      await activate(
        dialog.getByRole("button", {
          name: "Создать личный токен",
          exact: true,
        }),
      );
    await expect(
      dialog.getByRole("heading", { name: "Токен создан", exact: true }),
    ).toBeFocused();
    await activate(
      dialog.getByRole("button", { name: "Скопировать токен", exact: true }),
    );
    await expect(dialog.getByRole("status")).toHaveText("Токен скопирован.");
    await activate(dialog.getByRole("button", { name: "Готово", exact: true }));
    await expect(trigger).toBeFocused();
    const row = page.getByRole("article", { name, exact: true });
    await expect(row).toBeVisible();
    const revoke = row.getByRole("button", {
      name: "Отозвать токен",
      exact: true,
    });
    await activate(revoke);
    await activate(dialog.getByRole("button", { name: "Отмена", exact: true }));
    await expect(row.locator(".token-status")).toHaveText("Активен");
    await activate(revoke);
    const confirmation = dialog.getByRole("textbox");
    await confirmation.fill("Wrong token");
    await expect(
      dialog.getByRole("button", { name: "Подтвердить", exact: true }),
    ).toBeDisabled();
    if (input === "keyboard") {
      await confirmation.press("ControlOrMeta+A");
      await confirmation.pressSequentially(name);
      await confirmation.press("Enter");
    } else {
      await confirmation.fill(name);
      await activate(
        dialog.getByRole("button", { name: "Подтвердить", exact: true }),
      );
    }
    await expect(dialog).toHaveCount(0);
    await expect(row.locator(".token-status")).toHaveText("Отозван");
    await expect(revoke).toBeDisabled();
    await activate(page.getByRole("button", { name: "Обновить", exact: true }));
    await expect(row.locator(".token-status")).toHaveText("Отозван");
    await page.reload();
    await expect(owner).toHaveValue("personal");
    await expect(row.locator(".token-status")).toHaveText("Отозван");
    await activate(
      page
        .getByRole("banner")
        .getByRole("button", { name: "Меню пользователя", exact: true }),
    );
    await activate(
      page.getByRole("menuitem", { name: "API-токены", exact: true }),
    );
    await expect(page).toHaveURL(/\/api-tokens$/);
  });
}
