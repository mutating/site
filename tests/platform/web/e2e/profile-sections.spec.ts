import { API, expect, headers, password, startSession, test } from "./fixtures";
import { openUserMenu } from "./header-controls";

test("profile sections preserve unsaved personal data and browser history", async ({ page, account }) => {
  let tokenReads = 0;
  page.on("request", request => {
    if (request.method() === "GET" && new URL(request.url()).pathname === "/v1/api-tokens") tokenReads++;
  });
  await startSession(page, account);
  await page.goto("/profile");
  const personal = page.getByRole("tab", { name: "Личные данные", exact: true });
  const accountTab = page.getByRole("tab", { name: "Аккаунт", exact: true });
  const tokens = page.getByRole("tab", { name: "API-токены", exact: true });
  await expect(personal).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("tabpanel")).toHaveCount(1);
  await expect(page.getByRole("tabpanel")).toHaveAccessibleName("Личные данные");
  await page.getByLabel("Имя", { exact: true }).fill("Несохранённое имя");
  await page.getByLabel("Ник", { exact: true }).fill("draft_nickname");
  await accountTab.click();
  await expect(page).toHaveURL("/profile?section=account");
  await expect(page.getByRole("tabpanel")).toHaveAccessibleName("Аккаунт");
  await expect(page.getByRole("button", { name: "Удалить аккаунт", exact: true })).toBeVisible();
  await expect(page.getByLabel("Имя", { exact: true })).toBeHidden();
  expect(tokenReads).toBe(0);
  await page.goBack();
  await expect(personal).toHaveAttribute("aria-selected", "true");
  await expect(page.getByLabel("Имя", { exact: true })).toHaveValue("Несохранённое имя");
  await tokens.click();
  await expect(page.getByRole("button", { name: "Создать токен", exact: true })).toBeEnabled();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Профиль.");
  await expect(page.getByRole("heading", { level: 2, name: "Доступные токены." })).toBeVisible();
  expect(tokenReads).toBeGreaterThan(0);
  await page.getByLabel("Владелец токенов", { exact: true }).selectOption("personal");
  await accountTab.click();
  await personal.click();
  await expect(page.getByLabel("Ник", { exact: true })).toHaveValue("draft_nickname");
  await page.goBack();
  await expect(accountTab).toHaveAttribute("aria-selected", "true");
  await page.goBack();
  await expect(tokens).toHaveAttribute("aria-selected", "true");
  await expect(page.getByLabel("Владелец токенов", { exact: true })).toHaveValue("personal");
  await page.reload();
  await expect(tokens).toHaveAttribute("aria-selected", "true");
  await expect(page.getByLabel("Владелец токенов", { exact: true })).toHaveValue("personal");
  await personal.click();
  await page.getByLabel("Имя", { exact: true }).fill("Анна");
  await page.getByLabel("Ник", { exact: true }).fill(`${account.nickname}_edit`);
  await page.getByRole("button", { name: "Сохранить изменения", exact: true }).click();
  await expect(page.locator(".profile-settings-form").getByRole("status")).toHaveText("Изменения сохранены.");
  await page.reload();
  await expect(page.getByLabel("Имя", { exact: true })).toHaveValue("Анна");
  await expect(page.getByLabel("Ник", { exact: true })).toHaveValue(`${account.nickname}_edit`);
});

test("profile tabs support arrow keys, wrapping, Home, End and native activation", async ({ page, account }) => {
  await startSession(page, account);
  await page.goto("/profile");
  const personal = page.getByRole("tab", { name: "Личные данные", exact: true });
  const accountTab = page.getByRole("tab", { name: "Аккаунт", exact: true });
  const tokens = page.getByRole("tab", { name: "API-токены", exact: true });
  await personal.focus();
  await page.keyboard.press("ArrowRight");
  await expect(accountTab).toBeFocused();
  await expect(accountTab).toHaveAttribute("aria-selected", "true");
  await expect(personal).toHaveAttribute("tabindex", "-1");
  await page.keyboard.press("End");
  await expect(tokens).toBeFocused();
  await expect(tokens).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("ArrowRight");
  await expect(personal).toBeFocused();
  await page.keyboard.press("ArrowLeft");
  await expect(tokens).toBeFocused();
  await page.keyboard.press("Home");
  await expect(personal).toBeFocused();
  await expect(personal).toHaveAttribute("aria-selected", "true");
  await accountTab.press("Enter");
  await expect(accountTab).toHaveAttribute("aria-selected", "true");
  await tokens.press("Space");
  await expect(tokens).toHaveAttribute("aria-selected", "true");
  await expect(page.locator('.profile-tabs [tabindex="0"]')).toHaveCount(1);
});

test("API tokens have one entry in the profile and no entries in global navigation", async ({ page, account }) => {
  await startSession(page, account);
  await page.goto("/");
  await expect(page.locator('.sidebar a[href*="api-tokens"]')).toHaveCount(0);
  const menu = await openUserMenu(page);
  await expect(menu.getByRole("menuitem")).toHaveText(["Профиль", "Выйти"]);
  await menu.getByRole("menuitem", { name: "Профиль", exact: true }).click();
  await expect(page.locator(".token-profile-link")).toHaveCount(0);
  await expect(page.getByRole("link", { name: /API/ })).toHaveCount(0);
  await expect(page.getByRole("tab", { name: "API-токены", exact: true })).toHaveCount(1);
});

test("legacy token URLs preserve owner, pagination and hash without a history loop", async ({ page, account, request }) => {
  for (let index = 0; index < 11; index++) {
    expect((await request.post(`${API}/api-tokens`, {
      headers: headers(account),
      data: { user_id: account.id, name: `Legacy ${index}`, scopes: ["read"] },
    })).status()).toBe(201);
  }
  await startSession(page, account);
  await page.goto("/");
  await expect(page.getByRole("heading", { name: `Привет, ${account.first_name}` })).toBeVisible();
  await page.goto("/api-tokens?owner=personal&page=2#registry");
  await expect.poll(() => new URL(page.url()).pathname).toBe("/profile");
  const url = new URL(page.url());
  expect(url.searchParams.get("section")).toBe("api-tokens");
  expect(url.searchParams.get("owner")).toBe("personal");
  expect(url.searchParams.get("page")).toBe("2");
  expect(url.hash).toBe("#registry");
  await expect(page.getByRole("tab", { name: "API-токены", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByLabel("Владелец токенов", { exact: true })).toHaveValue("personal");
  await expect(page.locator(".token-registry-row")).toHaveCount(1);
  await page.goBack();
  await expect(page).toHaveURL("/");
});

test("a direct token link survives authentication", async ({ page, account }) => {
  await page.goto("/profile?section=api-tokens&owner=personal");
  await expect(page).toHaveURL(/\/login$/);
  await page.getByLabel("Email или ник").fill(account.email);
  await page.getByLabel("Пароль", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Войти", exact: true }).click();
  await expect(page).toHaveURL("/profile?section=api-tokens&owner=personal");
  await expect(page.getByRole("tab", { name: "API-токены", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByLabel("Владелец токенов", { exact: true })).toHaveValue("personal");
});

test("leaving tokens closes its dialog and does not restore secret or draft UI", async ({ page, account }) => {
  await startSession(page, account);
  await page.goto("/profile");
  await page.getByRole("tab", { name: "API-токены", exact: true }).click();
  await page.getByRole("button", { name: "Создать токен", exact: true }).click();
  await page.getByRole("dialog").getByLabel("Название токена", { exact: true }).fill("Незавершённый токен");
  await page.goBack();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.getByRole("tab", { name: "Личные данные", exact: true })).toHaveAttribute("aria-selected", "true");
  await page.goForward();
  await expect(page.getByRole("tab", { name: "API-токены", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await page.getByRole("button", { name: "Создать токен", exact: true }).click();
  await expect(page.getByRole("dialog").getByLabel("Название токена", { exact: true })).toHaveValue("");
});

for (const theme of ["light", "dark"] as const) {
  test(`profile section links reload with English labels in the ${theme} theme`, async ({ page, account }) => {
    await page.addInitScript(theme => {
      localStorage.setItem("tmc-language", "en");
      localStorage.setItem("tmc-theme", theme);
    }, theme);
    await startSession(page, account);
    await page.goto("/profile?section=account");
    await expect(page.getByRole("tab")).toHaveText(["Personal details", "Account", "API tokens"]);
    await expect(page.getByRole("tab", { name: "Account", exact: true })).toHaveAttribute("aria-selected", "true");
    await expect(page.getByRole("button", { name: "Delete account", exact: true })).toBeVisible();
    await page.reload();
    await expect(page.getByRole("tab", { name: "Account", exact: true })).toHaveAttribute("aria-selected", "true");
    await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
    await page.goto("/profile?section=unknown");
    await expect(page.getByRole("tab", { name: "Personal details", exact: true })).toHaveAttribute("aria-selected", "true");
    await expect(page.getByLabel("First name", { exact: true })).toBeVisible();
  });
}
