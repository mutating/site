import { test, expect, login, makeAccount, startSession, API, headers } from "./fixtures";
import type { Locator } from "@playwright/test";

/**
 * Refresh used to retain cached organization tokens and enabled actions after
 * access was revoked. Transient outages may retain authorized data, but a
 * confirmed 403/404 must discard it, including while a retry is pending.
 * Returning to the personal filter must still work without signing in again.
 */
for (const denial of [403, 404]) {
  test(`token refresh discards unauthorized cached rows after ${denial}`, async ({ page, request, account }) => {
    const creator = await makeAccount(request, "Владелец");
    const created = await request.post(`${API}/companies`, {
      headers: headers(creator), data: { name: "Закрытая команда" },
    });
    expect(created.status()).toBe(201);
    const company = await created.json();
    const joined = await request.post(`${API}/memberships`, {
      headers: headers(creator), data: {
        company_id: company.id, user_id: account.id, role: "user", permissions: ["tokens.read", "tokens.manage"],
      },
    });
    expect(joined.status()).toBe(201);
    const membership = await joined.json();
    for (const [actor, owner, name] of [
      [creator, { company_id: company.id }, "Скрыть после отзыва прав"],
      [account, { user_id: account.id }, "Личный доступ"],
    ] as const) {
      expect((await request.post(`${API}/api-tokens`, {
        headers: headers(actor), data: { ...owner, name, scopes: ["read", "execute"] },
      })).status()).toBe(201);
    }
    await startSession(page, account);
    await page.goto(`api-tokens?owner=${company.id}`);
    const row = page.getByRole("article", { name: "Скрыть после отзыва прав", exact: true });
    const refresh = page.getByRole("button", { name: "Обновить", exact: true });
    const create = page.getByRole("button", { name: "Создать токен", exact: true });
    const alert = page.getByRole("alert");
    await expect(row).toBeVisible();
    await page.route(`${API}/api-tokens?*`, route => route.request().method() === "GET"
      ? route.fulfill({ status: 503, headers: {
        "Access-Control-Allow-Origin": "http://127.0.0.1:18101", "Access-Control-Allow-Credentials": "true",
      }, json: {} }) : route.continue());
    await refresh.click();
    await expect(alert).toBeVisible();
    await expect(row).toBeVisible();
    await page.unroute(`${API}/api-tokens?*`);
    await alert.getByRole("button", { name: /Попробовать снова/ }).click();
    await expect(alert).toHaveCount(0);
    await expect(refresh).toBeEnabled();
    await expect(row).toBeVisible();
    if (denial === 404) {
      expect((await request.delete(`${API}/memberships/${membership.id}`, { headers: headers(creator) })).status()).toBe(204);
    } else {
      await page.route(`${API}/companies?*`, async route => {
        const response = await route.fetch();
        expect((await request.patch(`${API}/memberships/${membership.id}`, {
          headers: headers(creator), data: { permissions: [] },
        })).status()).toBe(200);
        // Rights change after the company snapshot, before the token-list GET.
        await route.fulfill({ response });
      });
    }
    await refresh.click();
    await expect(alert).toBeVisible();
    expect((await request.get(`${API}/api-tokens?company_id=${company.id}`, { headers: headers(account) })).status()).toBe(denial);
    await expect(row).toHaveCount(0);
    await expect(create).toBeDisabled();
    await page.unroute(`${API}/companies?*`);
    let release!: () => void;
    let pending = false;
    const gate = new Promise<void>(resolve => release = resolve);
    await page.route(`${API}/companies?*`, async route => {
      pending = true;
      await gate;
      await route.continue();
    });
    try {
      await alert.getByRole("button", { name: /Попробовать снова/ }).click();
      await expect.poll(() => pending).toBe(true);
      await expect(row).toHaveCount(0);
      await expect(create).toBeDisabled();
    } finally { release(); }
    await expect(refresh).toBeEnabled();
    await page.unroute(`${API}/companies?*`);
    await page.getByLabel("Владелец токенов", { exact: true }).selectOption("personal");
    await expect(page.getByRole("article", { name: "Личный доступ", exact: true })).toBeVisible();
    await expect(alert).toHaveCount(0);
    await expect(create).toBeEnabled();
  });
}

/** An organization filter must keep its name while its token list is loading or has failed, instead of displaying all owners. */
test("organization token owner stays selected through a failed load and retry", async ({ page, request, account }) => {
  const created = await request.post(`${API}/companies`, { headers: headers(account), data: { name: "CI Lab" } });
  expect(created.status()).toBe(201);
  const company = await created.json();
  await login(page, account); await page.goto("/api-tokens");
  const owner = page.getByLabel("Владелец токенов", { exact: true });
  await expect(owner.locator(`option[value="${company.id}"]`)).toBeAttached();
  let release!: () => void;
  const gate = new Promise<void>(resolve => release = resolve);
  let fail = true, intercepted = false;
  await page.route(`${API}/api-tokens?*`, async route => {
    if (route.request().method() !== "GET" || !fail || new URL(route.request().url()).searchParams.get("company_id") !== company.id) return route.continue();
    intercepted = true; await gate;
    await route.fulfill({ status: 503, headers: { "Access-Control-Allow-Origin": "http://127.0.0.1:18101", "Access-Control-Allow-Credentials": "true" }, json: {} });
  });
  try {
    await owner.selectOption(company.id);
    await expect.poll(() => intercepted).toBe(true);
    await expect(owner).toHaveValue(company.id);
    await expect(owner.locator("option:checked")).toContainText("CI Lab");
    release(); await expect(page.getByRole("alert")).toBeVisible();
    await expect(owner).toHaveValue(company.id);
    await expect(owner.locator("option:checked")).toContainText("CI Lab");
    fail = false;
    await page.getByRole("button", { name: /Попробовать снова/ }).click();
    await expect(page.getByRole("alert")).toHaveCount(0);
    await page.getByRole("button", { name: "Создать токен", exact: true }).click();
    await expect(page.getByRole("dialog").getByRole("radio", { name: "Организация CI Lab", exact: true })).toBeChecked();
    await page.keyboard.press("Escape");
  } finally { release(); }
});

/** Switching accounts must discard a previous user's revoke dialog instead of mixing their token with the new owner's name or submitting a stale revocation. */
test("token revoke confirmation is discarded after an actual account switch", async ({ page, context, request, account }) => {
  const other = await makeAccount(request, "Борис");
  const issued = await request.post(`${API}/api-tokens`, {
    headers: headers(account),
    data: { user_id: account.id, name: "A-only token", scopes: ["read", "execute"] },
  });
  expect(issued.status()).toBe(201);
  const token = await issued.json();
  let releaseProfile!: () => void;
  let profileRequested = false;
  const profileGate = new Promise<void>(resolve => releaseProfile = resolve);
  await page.route(`${API}/users/${account.id}`, async route => {
    if (route.request().method() !== "PATCH") return route.continue();
    const response = await route.fetch();
    expect(response.status()).toBe(200);
    profileRequested = true;
    await profileGate;
    await route.fulfill({ response });
  });
  try {
    await login(page, account);
    const menu = page.getByRole("banner").getByRole("button", { name: "Меню пользователя", exact: true });
    await menu.click();
    await page.getByRole("menuitem", { name: "Профиль", exact: true }).click();
    await page.getByLabel("Имя", { exact: true }).fill("Анна");
    await page.locator(".profile-settings-form").getByRole("button", { name: "Сохранить изменения", exact: true }).click();
    await expect.poll(() => profileRequested).toBe(true);
    await page.getByRole("navigation", { name: "Навигация кабинета" }).getByRole("link", { name: "API-токены", exact: true }).click();
    await page.getByRole("article", { name: token.name, exact: true }).getByRole("button", { name: "Отозвать токен", exact: true }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    await expect(dialog).toContainText(token.name);
    const second = await context.newPage();
    await second.goto("/profile");
    const secondMenu = second.getByRole("banner").getByRole("button", { name: "Меню пользователя", exact: true });
    await expect(secondMenu).toHaveAttribute("title", account.email);
    await secondMenu.click();
    await second.getByRole("menuitem", { name: "Выйти", exact: true }).click();
    await expect(second.getByLabel("Email или ник")).toBeVisible();
    await login(second, other);
    releaseProfile();
    await expect(menu).toHaveAttribute("title", other.email);
    await expect(dialog).toHaveCount(0);
    await expect(page.getByRole("article", { name: token.name, exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Создать токен", exact: true })).toBeEnabled();
    expect((await request.get(`${API}/auth/check`, { headers: { Authorization: `Bearer ${token.token}` } })).status()).toBe(200);
  } finally { releaseProfile(); }
});

/**
 * On a short phone screen the sticky submit panel used to cover issuance errors,
 * leaving a failed request unexplained. Visibility and viewport assertions alone
 * missed this overlap: hit testing must reach the alert itself. Retry must keep
 * the selected organization, unlimited lifetime and permissions intact.
 * Native Tab/arrow navigation must also keep scope controls unobscured, including
 * after an error increases the sticky panel's height.
 */
for (const [width, height] of [[390, 568], [568, 320]]) {
  test(`failed issuance stays readable above the submit action at ${width}x${height}`, async ({ page, request, account, browserName }) => {
    const uncovered = (control: Locator) => control.evaluate(element => {
      const box = element.getBoundingClientRect();
      const visible = document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2);
      return visible !== null && element.contains(visible);
    });
    await page.setViewportSize({ width, height });
    const response = await request.post(`${API}/companies`, {
      headers: headers(account), data: { name: "Лаборатория" },
    });
    expect(response.status()).toBe(201);
    const company = await response.json();
    await startSession(page, account);
    await page.goto(`api-tokens?owner=${company.id}`);
    await page.getByRole("button", { name: "Создать токен", exact: true }).click();
    const dialog = page.getByRole("dialog");
    const name = dialog.getByLabel("Название токена", { exact: true });
    const lifetime = dialog.getByRole("combobox", { name: "Срок действия", exact: true });
    await name.fill("Ночные сборки");
    // Native macOS WebKit Tab skips radio controls; use the other engines for
    // this sequence while retaining issuance/error/retry coverage in WebKit.
    if (browserName !== "webkit") {
      await name.press("Tab");
      await expect(lifetime).toBeFocused();
      await lifetime.press("Tab");
      const execute = dialog.getByRole("radio", { name: "Чтение и выполнение команд", exact: true });
      await expect(execute).toBeFocused();
      await execute.press("ArrowDown");
      const read = dialog.getByRole("radio", { name: "Только чтение", exact: true });
      await expect(read).toBeFocused();
      await expect.poll(() => uncovered(read)).toBe(true);
      await read.press("ArrowUp");
    }
    await lifetime.selectOption("unlimited");
    await dialog.locator("summary").click();
    const manage = dialog.getByRole("checkbox", { name: "Управление ресурсами и доступом", exact: true });
    await manage.check();
    let fail = true;
    await page.route(`${API}/api-tokens`, async route => {
      if (route.request().method() !== "POST" || !fail) return route.continue();
      await route.fulfill({ status: 503, headers: {
        "Access-Control-Allow-Origin": "http://127.0.0.1:18101", "Access-Control-Allow-Credentials": "true",
      }, json: {} });
    });
    const create = dialog.getByRole("button", { name: "Создать токен организации", exact: true });
    await create.click();
    const alert = dialog.getByRole("alert");
    await expect(alert).toBeVisible();
    await expect(alert).toBeInViewport({ ratio: 1 });
    await expect.poll(() => uncovered(alert)).toBe(true);
    if (browserName !== "webkit") {
      await create.press("Shift+Tab");
      await expect(manage).toBeFocused();
      await expect.poll(() => uncovered(manage)).toBe(true);
    }
    await expect(dialog.getByLabel("Название токена", { exact: true })).toHaveValue("Ночные сборки");
    await expect(dialog.getByRole("combobox", { name: "Срок действия", exact: true })).toHaveValue("unlimited");
    await expect(manage).toBeChecked();
    fail = false;
    const issued = page.waitForResponse(value => value.request().method() === "POST" && value.url().endsWith("/api-tokens"));
    await create.click();
    const created = await issued;
    expect(created.status()).toBe(201);
    expect(created.request().postDataJSON()).toMatchObject({
      company_id: company.id, name: "Ночные сборки", expires_in_seconds: null, scopes: ["read", "execute", "manage"],
    });
    await expect(dialog.getByRole("heading", { name: "Токен создан", exact: true })).toBeFocused();
    await expect(dialog.locator(".token-owner-summary")).toContainText("Лаборатория");
  });
}
