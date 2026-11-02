import { API, expect, headers, startSession, test, type Account } from "./fixtures";
import type { APIRequestContext } from "@playwright/test";

async function seedTokens(request: APIRequestContext, account: Account, count: number) {
  for (let index = 0; index < count; index++) {
    expect((await request.post(`${API}/api-tokens`, {
      headers: headers(account),
      data: { user_id: account.id, name: `Проверка ${index + 1}`, scopes: ["read"] },
    })).status()).toBe(201);
  }
}

test("editing a saved profile clears the previous success message", async ({ page, account }) => {
  await startSession(page, account);
  await page.goto("/profile");
  const name = page.getByLabel("Имя", { exact: true });
  const status = page.locator(".profile-settings-form").getByRole("status");
  await name.fill("Сохранённое имя");
  await page.getByRole("button", { name: "Сохранить изменения", exact: true }).click();
  await expect(status).toHaveText("Изменения сохранены.");
  await name.fill("Несохранённое имя");
  await expect(status).toHaveCount(0);
  await page.getByRole("tab", { name: "Аккаунт", exact: true }).click();
  await page.getByRole("tab", { name: "Личные данные", exact: true }).click();
  await expect(name).toHaveValue("Несохранённое имя");
  await expect(status).toHaveCount(0);
});

test("edits made during a save are retained without a false saved message", async ({ page, account }) => {
  let release!: () => void;
  let requested = false;
  const gate = new Promise<void>(resolve => release = resolve);
  await page.route(`${API}/users/${account.id}`, async route => {
    if (route.request().method() !== "PATCH") return route.continue();
    const response = await route.fetch();
    requested = true;
    await gate;
    await route.fulfill({ response });
  });
  try {
    await startSession(page, account);
    await page.goto("/profile");
    const name = page.getByLabel("Имя", { exact: true });
    const save = page.getByRole("button", { name: "Сохранить изменения", exact: true });
    const status = page.locator(".profile-settings-form").getByRole("status");
    await name.fill("Отправлено на сервер");
    await save.click();
    await expect.poll(() => requested).toBe(true);
    await name.fill("Новый черновик");
    release();
    await expect(save).toBeEnabled();
    await expect(name).toHaveValue("Новый черновик");
    const stored = await page.request.get(`${API}/users/me`);
    expect((await stored.json()).first_name).toBe("Отправлено на сервер");
    await expect(status).toHaveCount(0);
    await save.click();
    await expect(status).toHaveText("Изменения сохранены.");
    await page.reload();
    await expect(name).toHaveValue("Новый черновик");
  } finally {
    release();
  }
});

test("profile sections omit the duplicate identity summary beside the heading", async ({ page, account }) => {
  await startSession(page, account);
  await page.goto("/profile");
  await expect(page.locator(".avatar-editor .avatar.large")).toBeVisible();
  for (const section of ["Личные данные", "Аккаунт", "API-токены"]) {
    await page.getByRole("tab", { name: section, exact: true }).click();
    const heading = page.locator(".profile-heading");
    await expect(heading.getByRole("heading", { name: "Профиль.", exact: true })).toBeVisible();
    await expect(heading.locator(".avatar")).toHaveCount(0);
    await expect(heading).not.toContainText(account.first_name);
    await expect(heading).not.toContainText(account.nickname);
  }
});

test("history restores tab focus while keeping focus outside the switcher intact", async ({ page, account }) => {
  await startSession(page, account);
  await page.goto("/profile");
  const accountTab = page.getByRole("tab", { name: "Аккаунт", exact: true });
  const tokens = page.getByRole("tab", { name: "API-токены", exact: true });
  await accountTab.click();
  await tokens.click();
  await tokens.focus();
  await page.goBack();
  await expect(accountTab).toHaveAttribute("aria-selected", "true");
  await expect(accountTab).toBeFocused();
  await page.goForward();
  await expect(tokens).toBeFocused();
  const theme = page.getByRole("button", { name: "Переключить тему", exact: true });
  await theme.focus();
  await page.goBack();
  await expect(accountTab).toHaveAttribute("aria-selected", "true");
  await expect(theme).toBeFocused();
});

test("modified arrow keys are not consumed by profile tabs", async ({ page, account }) => {
  await startSession(page, account);
  await page.goto("/profile");
  const personal = page.getByRole("tab", { name: "Личные данные", exact: true });
  for (const modifier of ["Control", "Alt", "Meta"]) {
    await personal.press(`${modifier}+ArrowRight`);
    await expect(personal).toHaveAttribute("aria-selected", "true");
  }
});

test("pagination puts keyboard focus on the newly loaded token page", async ({ page, account, request }) => {
  await seedTokens(request, account, 11);
  await startSession(page, account);
  await page.goto("/profile?section=api-tokens&owner=personal");
  await expect(page.locator(".token-registry-row")).toHaveCount(10);
  await page.getByRole("button", { name: "Далее →", exact: true }).press("Enter");
  await expect(page.locator(".token-registry-row")).toHaveCount(1);
  const heading = page.getByRole("heading", { name: "Доступные токены.", exact: true });
  await expect(heading).toBeFocused();
  await page.getByRole("button", { name: "← Назад", exact: true }).press("Enter");
  await expect(page.locator(".token-registry-row")).toHaveCount(10);
  await expect(heading).toBeFocused();
});

test("a successful revoke returns focus to the token instead of a disabled button", async ({ page, account, request }) => {
  await seedTokens(request, account, 1);
  await startSession(page, account);
  await page.goto("/profile?section=api-tokens");
  const row = page.getByRole("article", { name: "Проверка 1", exact: true });
  await row.getByRole("button", { name: "Отозвать токен", exact: true }).press("Enter");
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("textbox").fill("Проверка 1");
  await dialog.getByRole("button", { name: "Подтвердить", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  await expect(row.locator(".token-status")).toHaveText("Отозван");
  await expect(row.getByRole("button", { name: "Отозвать токен", exact: true })).toBeDisabled();
  await expect(row).toBeFocused();
});

test("a delayed token page does not steal focus from a control the user chose", async ({ page, account, request }) => {
  await seedTokens(request, account, 11);
  let release!: () => void;
  let requested = false;
  const gate = new Promise<void>(resolve => release = resolve);
  await page.route(`${API}/api-tokens?*`, async route => {
    if (new URL(route.request().url()).searchParams.get("offset") !== "10") return route.continue();
    const response = await route.fetch();
    requested = true;
    await gate;
    await route.fulfill({ response });
  });
  try {
    await startSession(page, account);
    await page.goto("/profile?section=api-tokens&owner=personal");
    await page.getByRole("button", { name: "Далее →", exact: true }).click();
    await expect.poll(() => requested).toBe(true);
    const tokens = page.getByRole("tab", { name: "API-токены", exact: true });
    await tokens.focus();
    release();
    await expect(page.locator(".token-registry-row")).toHaveCount(1);
    await expect(tokens).toBeFocused();
  } finally {
    release();
  }
});
