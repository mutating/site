import { openUserMenu, selectLanguage } from "./header-controls";
import {
  test,
  expect,
  login,
  API,
  headers,
  addComputer,
  makeAccount,
  password,
} from "./fixtures";
import { randomUUID } from "node:crypto";

test("root routes keep navigation and sessions while account URLs are absent", async ({
  page,
  request,
  account,
}) => {
  await login(page, account);
  for (const path of [
    "/account",
    "/account/",
    "/account/profile",
    "/account/api/session",
  ]) {
    const response = await request.get(path, { maxRedirects: 0 });
    expect(response.status()).toBe(404);
    expect(response.headers().location).toBeUndefined();
  }
  await page.goto("/profile");
  await expect(page).toHaveURL("http://127.0.0.1:18101/profile");
  await expect(page.getByLabel("Имя", { exact: true })).toHaveValue(
    account.first_name,
  );
  await page.reload();
  await expect(page.getByLabel("Имя", { exact: true })).toHaveValue(
    account.first_name,
  );
  await page.goto(`/power-cells/computers?user_id=${account.id}&page=1`);
  await expect(page).toHaveURL(
    new RegExp(`/power-cells/computers\\?user_id=${account.id}&page=1$`),
  );
  await page
    .getByRole("navigation")
    .getByRole("link", { name: "Обзор" })
    .click();
  await expect(page).toHaveURL("http://127.0.0.1:18101/");
});

test("profile and sign out exist only in the header menu at every breakpoint", async ({
  page,
  account,
}) => {
  await login(page, account);
  await page.goto("organizations");
  const sidebar = page.getByRole("complementary");
  const trigger = page.getByRole("banner").getByRole("button", { name: "Меню пользователя", exact: true });
  for (const width of [1920, 1440, 801, 800, 581, 580, 481, 480, 390, 320, 280, 801]) {
    await page.setViewportSize({ width, height: 1000 });
    await expect(page.locator(".sidebar-account, .account-card, .mobile-account-actions")).toHaveCount(0);
    await expect(sidebar.getByRole("link", { name: "Профиль", exact: true })).toHaveCount(0);
    await expect(sidebar.getByRole("button", { name: "Выйти", exact: true })).toHaveCount(0);
    await expect(sidebar.locator(".avatar")).toHaveCount(0);
    await expect(sidebar).not.toContainText(account.email);
    await expect(trigger).toHaveCount(1);
    await expect(trigger).toBeVisible();
    const menu = await openUserMenu(page);
    await expect(page.getByRole("menuitem")).toHaveText(["Профиль", "Выйти"]);
    await expect(menu).toBeInViewport({ ratio: 1 });
    await trigger.click();
    await expect(menu).toHaveCount(0);
    // The wordmark updates its available width through ResizeObserver.
    await expect
      .poll(() => page.evaluate(() => document.documentElement.scrollWidth))
      .toBeLessThanOrEqual(width);
  }
});

test("header account actions open the profile and sign out through the browser session API", async ({
  page,
  account,
}) => {
  await login(page, account);
  await expect(
    page
      .getByRole("navigation")
      .getByRole("link", { name: "Профиль", exact: true }),
  ).toHaveCount(0);
  await expect(page.getByRole("banner").getByRole("button", { name: "Меню пользователя", exact: true })).toHaveAttribute("title", account.email);
  await (await openUserMenu(page)).getByRole("menuitem", { name: "Профиль", exact: true }).click();
  await expect(page).toHaveURL("http://127.0.0.1:18101/profile");
  await expect(page.getByLabel("Имя", { exact: true })).toHaveValue(
    account.first_name,
  );
  await expect(
    page.getByRole("main").getByRole("button", { name: "Выйти", exact: true }),
  ).toHaveCount(0);
  const menu = await openUserMenu(page);
  await expect(page.getByRole("menuitem", { name: "Выйти", exact: true })).toHaveCount(1);
  const signedOut = page.waitForResponse(
    (response) =>
      response.url() === `${API}/auth/session` &&
      response.request().method() === "DELETE",
  );
  await menu.getByRole("menuitem", { name: "Выйти", exact: true }).click();
  expect((await signedOut).status()).toBe(204);
  await expect(
    page.getByRole("heading", { name: "С возвращением." }),
  ).toBeVisible();
});

test("created account recovers from failed automatic login without repeating registration", async ({
  page,
}) => {
  await page.goto("cloud");
  await page.getByRole("link", { name: "Создать аккаунт" }).click();
  const email = `register_retry_${randomUUID()}@example.com`;
  await page.getByLabel("Электронная почта").fill(email);
  await page.getByLabel("Пароль", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Создать аккаунт" }).click();
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByRole("status")).toContainText("Аккаунт создан.");
  await expect(page.getByLabel("Email или ник")).toHaveValue(email);
  await expect(page.getByLabel("Пароль", { exact: true })).toHaveValue("");
  await selectLanguage(page);
  await expect(page.getByRole("status")).toContainText(
    "Your account has been created.",
  );
  await expect(page.getByLabel("Email or nickname")).toHaveValue(email);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Cloud is on the horizon" }),
  ).toBeVisible();
});

test("computer pagination recovers when the last page disappears", async ({
  page,
  request,
  account,
}) => {
  const computers = [];
  for (let i = 0; i < 11; i++)
    computers.push(await addComputer(request, account, `pagination-${i}`));
  await login(page, account);
  await page.goto(`power-cells/computers?user_id=${account.id}&page=2`);
  await expect(page.locator("tbody tr")).toHaveCount(1);
  // Reducing the total to ten removes page 2 regardless of which computer goes offline.
  expect(
    (
      await request.patch(`${API}/computers/${computers[10].id}`, {
        headers: headers(account),
        data: { enabled: false },
      })
    ).status(),
  ).toBe(200);
  await page.reload();
  await expect(page.getByText("Страница 1 из 1")).toBeVisible();
  await expect(page).toHaveURL(new RegExp(`user_id=${account.id}&page=1$`));
  await expect(page.locator("tbody tr")).toHaveCount(10);
  await expect(page.getByText("Пока нет подключённых компьютеров")).toHaveCount(
    0,
  );
  for (const computer of computers.slice(0, 10)) {
    expect(
      (
        await request.patch(`${API}/computers/${computer.id}`, {
          headers: headers(account),
          data: { enabled: false },
        })
      ).status(),
    ).toBe(200);
  }
  await page.reload();
  await expect(
    page.getByText("Пока нет подключённых компьютеров"),
  ).toBeVisible();
  await expect(page.getByText("Страница 1 из 1")).toBeVisible();
});

test("all private pages require login; registration has optional fields and no password confirmation", async ({
  page,
}) => {
  for (const path of [
    "",
    "power-cells",
    "power-cells/computers?page=2",
    "cloud",
    "organizations",
    "profile",
    "missing-page",
  ]) {
    await page.goto(path);
    await expect(
      page.getByRole("heading", { name: "С возвращением." }),
    ).toBeVisible();
    await expect(page).toHaveURL(/\/login$/);
  }
  await page.getByRole("link", { name: "Создать аккаунт" }).click();
  await expect(page.locator("input[type=password]")).toHaveCount(1);
  const email = randomUUID() + "@example.com";
  await page.getByLabel("Электронная почта").fill(email);
  await page.getByLabel("Пароль", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Создать аккаунт" }).click();
  await expect(
    page.getByRole("heading", { name: "Страница не найдена" }),
  ).toBeVisible();
  await (await openUserMenu(page)).getByRole("menuitem", { name: "Профиль", exact: true }).click();
  await expect(page.getByLabel("Имя", { exact: true })).toHaveValue("");
  await expect(page.getByLabel("Фамилия", { exact: true })).toHaveValue("");
  await expect(page.getByLabel("Ник", { exact: true })).toHaveValue("");
  const cookie = (await page.context().cookies()).find(
    (c) => c.name === "tmc_session",
  );
  expect(cookie?.httpOnly).toBe(true);
  expect(cookie?.sameSite).toBe("Lax");
  expect(cookie?.path).toBe("/v1/");
  expect(await page.evaluate(() => document.cookie)).not.toContain(
    "tmc_session",
  );
  expect(await page.evaluate(() => JSON.stringify(localStorage))).not.toContain(
    "thr_",
  );
});

test("nickname login, names, avatar, session persistence and confirmed account deletion", async ({
  page,
  account,
}) => {
  await login(page, account, account.nickname.toUpperCase());
  await page.goto("profile");
  await page.getByLabel("Имя", { exact: true }).fill("Евгений");
  await page.getByLabel("Фамилия", { exact: true }).fill("Блинов");
  await page.getByRole("button", { name: "Сохранить изменения" }).click();
  await expect(page.getByRole("status")).toHaveText("Изменения сохранены.");
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.locator("input[type=file]").setInputFiles({
    name: "oversized.png",
    mimeType: "image/png",
    buffer: Buffer.alloc(2 * 1024 * 1024 + 1),
  });
  // Upload feedback must remain visible beside the control, including on mobile.
  await expect(
    page.getByRole("alert").filter({ hasText: "не больше 2 МиБ" }),
  ).toBeInViewport({ ratio: 1 });
  // Real PNG upload through the dashboard server and image validation in the API.
  await page.locator("input[type=file]").setInputFiles({
    name: "avatar.png",
    mimeType: "image/png",
    buffer: Buffer.from(
      "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jWZkAAAAASUVORK5CYII=",
      "base64",
    ),
  });
  await expect(page.locator(".avatar.large img")).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
  await expect(
    page.locator(".avatar-editor").getByRole("status"),
  ).toBeInViewport();
  await page.reload();
  await expect(page.getByLabel("Имя", { exact: true })).toHaveValue("Евгений");
  await expect(page.locator(".avatar.large img")).toBeVisible();
  await page.getByRole("button", { name: "Удалить аватар" }).click();
  await expect(page.locator(".avatar.large img")).toHaveCount(0);
  await page.getByText("Управление аккаунтом", { exact: true }).click();
  const deleteAccount = page.getByRole("button", {
    name: "Удалить аккаунт",
    exact: true,
  });
  await deleteAccount.click();
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("button", { name: "Отмена", exact: true }).click();
  await expect(deleteAccount).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(
    dialog.getByRole("button", { name: "Подтвердить", exact: true }),
  ).toBeDisabled();
  await dialog.getByRole("textbox").fill(account.email);
  await dialog
    .getByRole("button", { name: "Подтвердить", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "С возвращением." }),
  ).toBeVisible();
});

test("mobile computer rows keep identity and both resources visible", async ({
  page,
  request,
  account,
}) => {
  await addComputer(request, account, "Production workstation");
  await login(page, account);
  await page.goto(`power-cells/computers?user_id=${account.id}`);
  await expect(page.locator("tbody tr")).toHaveCount(1);
  for (const width of [320, 390]) {
    await page.setViewportSize({ width, height: 844 });
    await expect(page.getByText("Production workstation")).toBeInViewport({
      ratio: 1,
    });
    for (const metric of await page.locator("tbody .numeric").all())
      await expect(metric).toBeInViewport({ ratio: 1 });
    expect(
      await page
        .locator(".table-scroll")
        .evaluate((element) => element.scrollWidth <= element.clientWidth),
    ).toBe(true);
  }
});

test("computer pagination starts at the first row of the new page", async ({
  page,
  request,
  account,
}) => {
  for (let index = 0; index < 20; index++)
    await addComputer(request, account, `Workstation ${index + 1}`);
  await login(page, account);
  await page.goto(`power-cells/computers?user_id=${account.id}`);
  await expect(page.locator("tbody tr")).toHaveCount(10);
  await page.getByRole("button", { name: "Далее" }).click();
  await expect(page).toHaveURL(/page=2/);
  await expect(page.getByText("Страница 2 из 2")).toBeVisible();
  await expect(page.locator("tbody tr")).toHaveCount(10);
  await expect(page.locator("tbody tr").first()).toBeInViewport({ ratio: 1 });
  await page.getByRole("button", { name: "Назад", exact: false }).click();
  await expect(page).toHaveURL(/page=1/);
  await expect(page.getByText("Страница 1 из 2")).toBeVisible();
  await expect(page.locator("tbody tr")).toHaveCount(10);
  await expect(page.locator("tbody tr").first()).toBeInViewport({ ratio: 1 });
});

test("personal and organization capacity stay separate, offline excluded, computer pages paginate", async ({
  page,
  request,
  account,
}) => {
  const created = await request.post(`${API}/companies`, {
    headers: headers(account),
    data: { name: "Mutating Research" },
  });
  const company = await created.json();
  for (let i = 0; i < 12; i++)
    await addComputer(
      request,
      account,
      `personal-${String(i).padStart(2, "0")}`,
    );
  await addComputer(request, account, "offline-personal", undefined, false);
  await addComputer(request, account, "company-only", company.id);
  await login(page, account);
  const organization = page.getByRole("region", { name: "Mutating Research" });
  await expect(organization.getByTestId("computer-count")).toHaveText("1");
  await page
    .getByRole("navigation", { name: "Навигация кабинета" })
    .getByRole("link", { name: "Power Cells", exact: true })
    .click();
  const personal = page.getByRole("region", { name: "Личные компьютеры" });
  await expect(personal.getByTestId("computer-count")).toHaveText("12");
  await expect(personal.locator("path[data-series]")).toHaveCount(4);
  await personal
    .getByRole("button", { name: "CPU · занято", exact: true })
    .click();
  await expect(personal.locator("path[data-series]")).toHaveCount(3);
  await personal
    .getByRole("button", { name: "CPU · занято", exact: true })
    .click();
  await personal.getByRole("link", { name: "Список компьютеров" }).click();
  await expect(page.locator("tbody tr")).toHaveCount(10);
  await expect(page.getByText("Страница 1 из 2")).toBeVisible();
  await expect(page.getByText("offline-personal")).toHaveCount(0);
  await page.getByRole("button", { name: "Далее" }).click();
  await expect(page.locator("tbody tr")).toHaveCount(2);
  await expect(page).toHaveURL(/page=2/);
  await page.reload();
  await expect(page.locator("tbody tr")).toHaveCount(2);
  await page.goBack();
  await expect(page.locator("tbody tr")).toHaveCount(10);
  await page.goto(`power-cells/computers?company_id=${company.id}`);
  await expect(page.getByText("company-only")).toBeVisible();
  await expect(page.locator("tbody tr")).toHaveCount(1);
});

test("organization creation, creator restrictions and safe deletion errors", async ({
  page,
  request,
  account,
  browserName,
}) => {
  await login(page, account);
  await page.goto("organizations");
  await page
    .getByRole("button", { name: "Создать организацию", exact: true })
    .click();
  await page.getByLabel("Название организации").fill("Новая команда");
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Создать организацию" })
    .click();
  await expect(
    page.getByRole("heading", { name: "Новая команда" }),
  ).toBeVisible();
  const deleteButton = page.getByRole("button", {
    name: "Удалить организацию",
  });
  await deleteButton.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("dialog").getByRole("textbox")).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(deleteButton).toBeFocused();
  // Safari on macOS uses Option-Tab to include buttons in sequential navigation.
  await page.keyboard.press(
    browserName === "webkit" && process.platform === "darwin"
      ? "Alt+Tab"
      : "Tab",
  );
  await expect(
    page.getByRole("button", { name: "Выйти из организации" }),
  ).toBeFocused();
  await deleteButton.click();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Отмена" })
    .click();
  await expect(deleteButton).toBeFocused();
  await page.keyboard.press("Enter");
  await page.getByRole("dialog").getByRole("textbox").fill("Новая команда");
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Подтвердить", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Новая команда" }),
  ).toHaveCount(0);
  const owner = await makeAccount(request, "Боб");
  const company = await (
    await request.post(`${API}/companies`, {
      headers: headers(owner),
      data: { name: "Команда Боба" },
    })
  ).json();
  await request.post(`${API}/memberships`, {
    headers: headers(owner),
    data: { company_id: company.id, user_id: account.id, role: "user" },
  });
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "Команда Боба" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Удалить организацию" }),
  ).toHaveCount(0);
  await page.goto("profile");
  await page.getByText("Управление аккаунтом", { exact: true }).click();
  await page
    .getByRole("button", { name: "Удалить аккаунт", exact: true })
    .click();
  await page.getByRole("dialog").getByRole("textbox").fill(account.email);
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Подтвердить", exact: true })
    .click();
  await expect(page.getByRole("alert")).toContainText("Удаление заблокировано");
  await page.getByRole("button", { name: "Отмена", exact: true }).click();
  await page.goto("organizations");
  await page.getByRole("button", { name: "Выйти из организации" }).click();
  await page.getByRole("dialog").getByRole("textbox").fill("Команда Боба");
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Подтвердить", exact: true })
    .click();
  await expect(page.getByRole("heading", { name: "Команда Боба" })).toHaveCount(
    0,
  );
});

test("organization deletion preserves members and tokens until dependencies are explicitly removed", async ({
  page,
  request,
  account,
}) => {
  const member = await makeAccount(request, "Боб");
  const company = await (
    await request.post(`${API}/companies`, {
      headers: headers(account),
      data: { name: "Общая команда" },
    })
  ).json();
  const membership = await (
    await request.post(`${API}/memberships`, {
      headers: headers(account),
      data: { company_id: company.id, user_id: member.id, role: "user" },
    })
  ).json();
  await login(page, account);
  await page.goto("organizations");
  await page.getByRole("button", { name: "Удалить организацию" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("textbox").fill(company.name);
  await dialog
    .getByRole("button", { name: "Подтвердить", exact: true })
    .click();
  await expect(dialog.getByRole("alert")).toContainText(
    "в организации есть другие участники",
  );
  expect(
    (
      await request.get(`${API}/memberships/${membership.id}`, {
        headers: headers(member),
      })
    ).status(),
  ).toBe(200);
  expect(
    (
      await request.delete(`${API}/memberships/${membership.id}`, {
        headers: headers(member),
      })
    ).status(),
  ).toBe(204);
  const ownership = await (
    await request.post(`${API}/token-ownerships`, {
      headers: headers(account),
      data: { company_id: company.id },
    })
  ).json();
  const token = await (
    await request.post(`${API}/access-tokens`, {
      headers: headers(account),
      data: { ownership_id: ownership.id, name: "CI", scopes: ["read"] },
    })
  ).json();
  await dialog
    .getByRole("button", { name: "Подтвердить", exact: true })
    .click();
  await expect(dialog.getByRole("alert")).toContainText(
    "у организации есть API-токены",
  );
  expect(
    (
      await request.get(`${API}/companies/${company.id}`, {
        headers: { Authorization: `Bearer ${token.token}` },
      })
    ).status(),
  ).toBe(200);
  expect(
    (
      await request.delete(`${API}/access-tokens/${token.id}`, {
        headers: headers(account),
      })
    ).status(),
  ).toBe(204);
  expect(
    (
      await request.delete(`${API}/token-ownerships/${ownership.id}`, {
        headers: headers(account),
      })
    ).status(),
  ).toBe(204);
  await dialog
    .getByRole("button", { name: "Подтвердить", exact: true })
    .click();
  await expect(dialog).not.toBeVisible();
  await expect(page.getByRole("heading", { name: company.name })).toHaveCount(
    0,
  );
});

test("theme and language persist; cloud stays an empty product; layout has no horizontal overflow", async ({
  page,
  account,
}, info) => {
  await login(page, account);
  await page.goto("cloud");
  await expect(
    page.getByRole("heading", { name: "Облако — впереди" }),
  ).toBeVisible();
  await selectLanguage(page);
  await expect(
    page.getByRole("heading", { name: "Cloud is on the horizon" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Toggle color theme" }).click();
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("lang", "en");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  for (const route of [
    "",
    "power-cells",
    "profile",
    "organizations",
    "cloud",
  ]) {
    await page.goto(route);
    await expect(page.locator("h1")).toBeVisible();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    ).toBe(true);
  }
  await page.goto("power-cells");
  await expect(
    page
      .getByRole("region", { name: "Personal computers" })
      .getByTestId("computer-count"),
  ).toHaveText("0");
  await page.screenshot({
    path: info.outputPath("power-cells-light.png"),
    fullPage: true,
  });
});

test("wrong password, expired session and cross-origin mutation are rejected", async ({
  page,
  request,
  account,
}) => {
  await page.goto("login");
  await page.getByLabel("Email или ник").fill(account.email);
  await page.getByLabel("Пароль", { exact: true }).fill("wrong password");
  await page.getByRole("button", { name: "Войти", exact: true }).click();
  await expect(page.getByRole("alert")).toHaveText(
    "Неверный логин или пароль.",
  );
  await login(page, account);
  const cookies = await page.context().cookies();
  const response = await request.post("http://127.0.0.1:18100/v1/companies", {
    headers: {
      Origin: "https://example.com",
      Cookie: cookies.map((c) => `${c.name}=${c.value}`).join("; "),
      "X-Requested-With": "ThrongDashboard",
    },
    data: { name: "forbidden" },
  });
  expect(response.status()).toBe(403);
  await page.context().clearCookies();
  await page.goto("profile");
  await expect(
    page.getByRole("heading", { name: "С возвращением." }),
  ).toBeVisible();
});

test("logout revokes its API token and a revoked active session returns to login", async ({
  page,
  request,
  account,
}) => {
  const before = await (
    await request.get(`${API}/access-tokens`, { headers: headers(account) })
  ).json();
  await login(page, account);
  const sessionCookie = (await page.context().cookies()).find(
    (cookie) => cookie.name === "tmc_session",
  )!;
  await page.goto("profile");
  const signOut = (await openUserMenu(page)).getByRole("menuitem", { name: "Выйти", exact: true });
  // The menu must finish loading before taking the browser offline.
  await expect(signOut).toBeVisible();
  await expect(signOut).toBeEnabled();
  await page.context().setOffline(true);
  await signOut.click();
  // Firefox can leave a cross-origin preflight pending while offline.
  // The client must report its bounded request timeout and allow retry.
  await expect(page.getByRole("alert")).toContainText(
    "Не удалось связаться с сервером",
    { timeout: 30_000 },
  );
  await expect(page.getByRole("alert")).toBeInViewport({ ratio: 1 });
  await expect(signOut).toBeEnabled();
  await page.context().setOffline(false);
  await signOut.click();
  await expect(
    page.getByRole("heading", { name: "С возвращением." }),
  ).toBeVisible();
  const after = await (
    await request.get(`${API}/access-tokens`, { headers: headers(account) })
  ).json();
  expect(after).toEqual(before);
  const replay = await request.get("http://127.0.0.1:18100/v1/auth/session", {
    headers: { Cookie: `${sessionCookie.name}=${sessionCookie.value}` },
  });
  expect(replay.status()).toBe(401);
  await login(page, account);
  const tokens: { id: string }[] = await (
    await request.get(`${API}/access-tokens`, { headers: headers(account) })
  ).json();
  const dashboardToken = tokens.find(
    (token) => !before.some((old: { id: string }) => old.id === token.id),
  )!;
  expect(
    (
      await request.delete(`${API}/access-tokens/${dashboardToken.id}`, {
        headers: headers(account),
      })
    ).status(),
  ).toBe(204);
  await page
    .getByRole("navigation", { name: "Навигация кабинета" })
    .getByRole("link", { name: "Power Cells", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "С возвращением." }),
  ).toBeVisible();
});
