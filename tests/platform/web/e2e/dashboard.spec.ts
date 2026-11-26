import { openOrganizationMember, openOrganizationSettings, switchOrganizationPeople } from "./organization-controls";
import { openUserMenu, selectLanguage } from "./header-controls";
import {
  test,
  expect,
  login,
  startSession,
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
  await page.goto(`/compute/computers?user_id=${account.id}&page=1`);
  await expect(page).toHaveURL(
    new RegExp(`/compute/computers\\?user_id=${account.id}&page=1$`),
  );
  await page
    .getByRole("navigation")
    .getByRole("link", { name: "Обзор" })
    .click();
  await expect(page).toHaveURL("http://127.0.0.1:18101/");
});

/** The token shortcut is a deliberate third menu entry; profile/logout must still stay out of the sidebar. */
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
    await expect(page.getByRole("menuitem")).toHaveText(["Алиса", "Выйти"]);
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
  await (await openUserMenu(page)).getByRole("menuitem", { name: /(Профиль|Profile)$/ }).click();
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
  await page.goto(`compute/computers?user_id=${account.id}&page=2`);
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
    "compute",
    "compute/computers?page=2",
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
  await (await openUserMenu(page)).getByRole("menuitem", { name: /(Профиль|Profile)$/ }).click();
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
  await page.getByRole("tab", { name: "Аккаунт", exact: true }).click();
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
  await page.goto(`compute/computers?user_id=${account.id}`);
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
  await page.goto(`compute/computers?user_id=${account.id}`);
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

test("personal and organization capacity stay separate and the personal inventory paginates all computers", async ({
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
    .getByRole("link", { name: "Compute", exact: true })
    .click();
  await page.getByRole("link", { name: "Вычислительные пулы", exact: true }).click();
  await page.getByRole("link", { name: "Открыть пул Личный пул", exact: true }).click();
  const personal = page.getByRole("region", { name: "Личный пул" });
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
  const offlineFirstPage = await page.getByText("offline-personal", { exact: true }).count();
  await page.getByRole("button", { name: "Далее" }).click();
  await expect(page.locator("tbody tr")).toHaveCount(3);
  await expect(page.getByText("offline-personal", { exact: true })).toHaveCount(1 - offlineFirstPage);
  await expect(page).toHaveURL(/page=2/);
  await page.reload();
  await expect(page.locator("tbody tr")).toHaveCount(3);
  await page.goBack();
  await expect(page.locator("tbody tr")).toHaveCount(10);
  await page.goto(`compute/computers?company_id=${company.id}`);
  await expect(page.getByText("company-only")).toBeVisible();
  await expect(page.locator("tbody tr")).toHaveCount(1);
});

test("organization computers include offline and disabled machines while Compute stays filtered", async ({
  page,
  request,
  account,
}) => {
  const name = "Компьютеры команды";
  const response = await request.post(`${API}/companies`, {
    headers: headers(account),
    data: { name },
  });
  expect(response.status()).toBe(201);
  const { id } = await response.json();
  const online = await addComputer(request, account, "online-node", id);
  await addComputer(request, account, "offline-node", id, false);
  const expired = await addComputer(request, account, "expired-lease-node", id, false);
  const disabledComputer = await addComputer(request, account, "disabled-node", id, false);
  const update = await request.patch(`${API}/computers/${disabledComputer.id}`, {
    headers: headers(account),
    data: { enabled: false },
  });
  expect(update.status()).toBe(200);
  await page.route("**/v1/computers?*", async (route) => {
    const url = new URL(route.request().url());
    if (url.searchParams.get("only_available") !== "false") return route.continue();
    const response = await route.fetch();
    const rows = await response.json();
    await route.fulfill({
      response,
      json: rows.map((computer: { id: string }) => {
        if (computer.id === expired.id) {
          return {
            ...computer,
            online: true,
            accepting_work: true,
            session_id: expired.id,
            lease_expires_at: "2020-01-01T00:00:00Z",
            session_active: false,
          };
        }
        if (computer.id === online.id) {
          return { ...computer, free_cpu: 2000, free_memory: 2 * 1024 ** 3 };
        }
        return computer;
      }),
    });
  });
  await startSession(page, account);
  await page.goto(`organizations?q=${encodeURIComponent(name)}`);
  const organization = page.getByRole("article", { name, exact: true });
  await expect(organization.getByTestId("organization-computer-count")).toHaveText("4");
  await organization.getByRole("link").click();
  await page.getByRole("link", { name: /Список компьютеров/ }).click();
  const offline = page.locator(".computer-row").filter({ hasText: "offline-node" });
  await expect(offline).toBeVisible();
  await expect(offline.locator(".status-badge:visible")).toHaveText("Офлайн");
  const expiredRow = page.locator(".computer-row").filter({ hasText: "expired-lease-node" });
  await expect.soft(expiredRow.locator(".status-badge:visible")).toHaveText("Офлайн");
  await expect.soft(expiredRow.locator(".numeric")).toHaveText(["— / 8", "— / 8 GiB"]);
  const onlineRow = page.locator(".computer-row").filter({ hasText: "online-node" });
  await expect.soft(onlineRow.locator(".numeric")).toHaveText(["2 / 8", "2 / 8 GiB"]);
  const disabled = page.locator(".computer-row").filter({ hasText: "disabled-node" });
  await expect(disabled.locator(".status-badge:visible")).toHaveText("Отключён");
  await offline.getByRole("link", { name: "offline-node", exact: true }).click();
  const details = page.locator(".computer-details dd");
  await expect.soft(details.nth(2)).toHaveText("— / 8");
  await expect.soft(details.nth(3)).toHaveText("— / 8 GiB");
  await page.getByRole("link", { name: "← К списку компьютеров", exact: true }).click();
  await expect(offline).toBeVisible();
  await page.getByRole("link", { name: "← К организации", exact: true }).click();
  await page.getByRole("link", { name: "← К организациям", exact: true }).click();
  await expect(page.getByRole("searchbox")).toHaveValue(name);

  await page.getByRole("link", { name: "Compute", exact: true }).click();
  await page.getByRole("link", { name: "Вычислительные пулы", exact: true }).click();
  await page.getByRole("link", { name: `Открыть пул ${name}`, exact: true }).click();
  await page.getByRole("region", { name, exact: true })
    .getByRole("link", { name: /Список компьютеров/ }).click();
  await expect(page.locator(".computer-row")).toHaveCount(1);
  await expect(page.getByRole("link", { name: "online-node", exact: true })).toBeVisible();
});

test("organization settings actions work while the team panels are loading", async ({
  page,
  request,
  account,
}) => {
  const response = await request.post(`${API}/companies`, {
    headers: headers(account),
    data: { name: "Команда с медленной загрузкой" },
  });
  expect(response.status()).toBe(201);
  const { id } = await response.json();
  let releaseMembers!: () => void;
  let releaseInvitations!: () => void;
  const members = new Promise<void>((resolve) => {
    releaseMembers = resolve;
  });
  const invitations = new Promise<void>((resolve) => {
    releaseInvitations = resolve;
  });
  await page.route(`${API}/memberships?company_id=${id}&*`, async (route) => {
    await members;
    await route.continue();
  });
  await page.route(`${API}/invitations?company_id=${id}&*`, async (route) => {
    await invitations;
    await route.continue();
  });
  await startSession(page, account);
  try {
    await page.goto(`organizations/${id}`);
    await openOrganizationSettings(page);
    const leave = page.getByRole("button", {
      name: "Выйти из организации",
      exact: true,
    });
    const remove = page.getByRole("button", {
      name: "Удалить организацию",
      exact: true,
    });
    await expect(leave).toBeEnabled();
    await expect(remove).toBeEnabled();
    await leave.click();
    const leaveDialog = page.getByRole("dialog", { name: "Подтвердите выход" });
    await expect(leaveDialog).toBeVisible();
    await leaveDialog.getByRole("button", { name: "Отмена", exact: true }).click();
    await remove.click();
    const dialog = page.getByRole("dialog", { name: "Удалить организацию" });
    await expect(dialog).toBeVisible();
    await dialog.getByRole("button", { name: "Отмена", exact: true }).click();
    await expect(remove).toBeFocused();
  } finally {
    releaseMembers();
    releaseInvitations();
  }
});

for (const endpoint of ["memberships", "invitations"]) {
  test(`organization deletion stays reachable when ${endpoint} cannot load`, async ({
    page,
    request,
    account,
  }) => {
    const name = "Команда при сбое загрузки";
    const created = await request.post(`${API}/companies`, {
      headers: headers(account),
      data: { name },
    });
    expect(created.status()).toBe(201);
    const { id } = await created.json();
    let attempts = 0;
    await page.route(`**/v1/${endpoint}?company_id=${id}&*`, (route) => {
      attempts++;
      return route.abort("failed");
    });
    await page.clock.install();
    await startSession(page, account);
    await page.goto(`organizations/${id}`);
    if (endpoint === "invitations") await switchOrganizationPeople(page, "Приглашения");
    await openOrganizationSettings(page);
    const remove = page.getByRole("button", { name: "Удалить организацию", exact: true });
    await expect(remove).toBeEnabled();
    await expect.poll(() => attempts).toBeGreaterThan(1);
    await page.clock.fastForward(21_000);
    await expect(remove).toBeEnabled();
    if (endpoint === "memberships") {
      await expect(page.getByRole("button", { name: "Выйти из организации" })).toBeEnabled();
    }
    await page.keyboard.press("Escape");
    const panel = page.getByRole("region", { name: endpoint === "invitations" ? "Приглашения" : "Участники", exact: true });
    await expect(panel.getByRole("alert")).toContainText("Не удалось связаться");
    await openOrganizationSettings(page);
    await remove.click();
    const dialog = page.getByRole("dialog", { name: "Удалить организацию" });
    await dialog.getByRole("textbox").fill(name);
    await dialog.getByRole("button", { name: "Подтвердить", exact: true }).click();
    await expect(page).toHaveURL(/\/organizations$/);
  });
}

test("organization actions recover after the organization lookup stops responding", async ({
  page,
  request,
  account,
}) => {
  const name = "Команда при сбое страницы";
  const created = await request.post(`${API}/companies`, {
    headers: headers(account),
    data: { name },
  });
  expect(created.status()).toBe(201);
  const { id } = await created.json();
  let attempts = 0;
  let disconnected = true;
  await page.route(`${API}/companies/${id}`, (route) => {
    if (disconnected && route.request().method() === "GET") {
      attempts++;
      return route.abort("failed");
    }
    return route.continue();
  });
  await page.clock.install();
  await startSession(page, account);
  await page.goto("organizations");
  await page.getByRole("link", { name: `Открыть организацию: ${name}` }).click();
  await expect.poll(() => attempts).toBeGreaterThan(1);
  await page.clock.fastForward(21_000);
  await expect(page.getByRole("alert")).toContainText("Не удалось связаться");
  await expect(page.getByRole("button", { name: "Удалить организацию" })).toHaveCount(0);
  disconnected = false;
  await page.getByRole("button", { name: "Попробовать снова" }).click();
  await expect(page.getByRole("heading", { name: `${name}.`, exact: true })).toBeVisible();
  await openOrganizationSettings(page);
  await expect(page.getByRole("button", { name: "Выйти из организации" })).toBeEnabled();
  await page.getByRole("button", { name: "Удалить организацию" }).click();
  const dialog = page.getByRole("dialog", { name: "Удалить организацию" });
  await dialog.getByRole("textbox").fill(name);
  await dialog.getByRole("button", { name: "Подтвердить", exact: true }).click();
  await expect(page).toHaveURL(/\/organizations$/);
});

test("organization deletion requires the saved name while the details reload", async ({
  page,
  request,
  account,
}) => {
  const oldName = "Прежнее имя команды";
  const name = "Новое имя команды";
  const created = await request.post(`${API}/companies`, {
    headers: headers(account),
    data: { name: oldName },
  });
  expect(created.status()).toBe(201);
  const { id } = await created.json();
  await startSession(page, account);
  await page.goto(`organizations/${id}`);
  await openOrganizationSettings(page);
  const remove = page.getByRole("button", { name: "Удалить организацию" });
  await expect(remove).toBeEnabled();

  let release!: () => void;
  let startRefresh!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  const refreshing = new Promise<void>((resolve) => { startRefresh = resolve; });
  await page.route(`${API}/companies/${id}`, async (route) => {
    if (route.request().method() === "GET") {
      startRefresh();
      await gate;
    }
    await route.continue();
  });
  try {
    await page.getByLabel("Название организации").fill(name);
    await page.getByRole("button", { name: "Сохранить", exact: true }).click();
    await expect(page.getByText("Изменения сохранены.")).toBeVisible();
    await refreshing;
    await remove.click();
    const dialog = page.getByRole("dialog", { name: "Удалить организацию" });
    const input = dialog.getByRole("textbox");
    const confirm = dialog.getByRole("button", { name: "Подтвердить", exact: true });
    await input.fill(oldName);
    await expect(confirm).toBeDisabled();
    await input.fill(name);
    await expect(confirm).toBeEnabled();
    await confirm.click();
    await expect(page).toHaveURL(/\/organizations$/);
  } finally {
    release();
  }
});

test("organization deletion is unavailable as soon as a creator changes their role", async ({
  page,
  request,
  account,
}) => {
  const coCreator = await makeAccount(request, "Боб");
  const created = await request.post(`${API}/companies`, {
    headers: headers(account),
    data: { name: "Команда с двумя создателями" },
  });
  expect(created.status()).toBe(201);
  const { id } = await created.json();
  expect((await request.post(`${API}/memberships`, {
    headers: headers(account),
    data: { company_id: id, user_id: coCreator.id, role: "creator" },
  })).status()).toBe(201);
  await startSession(page, account);
  await page.goto(`organizations/${id}`);
  await openOrganizationSettings(page);
  const remove = page.getByRole("button", { name: "Удалить организацию" });
  await expect(remove).toBeEnabled();

  let release!: () => void;
  let startRefresh!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  const refreshing = new Promise<void>((resolve) => { startRefresh = resolve; });
  await page.route(`${API}/companies/${id}`, async (route) => {
    startRefresh();
    await gate;
    await route.continue();
  });
  try {
    await page.keyboard.press("Escape");
    const self = page.getByRole("article", { name: account.first_name, exact: true });
    await openOrganizationMember(self);
    await self.getByRole("button", { name: "Изменить права" }).click();
    const editor = page.getByRole("dialog", { name: "Изменить права" });
    await editor.getByLabel("Роль", { exact: true }).selectOption("admin");
    await editor.getByRole("button", { name: "Сохранить изменения", exact: true }).click();
    await expect(editor).toHaveCount(0);
    await refreshing;
    await expect(self).toContainText("Администратор");
    await openOrganizationSettings(page);
    await expect(remove).toHaveCount(0);
    await expect(page.getByLabel("Название организации")).toBeVisible();
  } finally {
    release();
  }
});

for (const action of ["leave", "delete"] as const) {
  test(`organization actions support exact multiline confirmation to ${action}`, async ({
    page,
    request,
    account,
  }) => {
    const confirmation = "Первая строка\nВторая строка";
    const leaving = action === "leave";
    const owner = leaving ? await makeAccount(request) : account;
    const created = await request.post(`${API}/companies`, {
      headers: headers(owner),
      data: { name: leaving ? "Общая команда" : confirmation },
    });
    expect(created.status()).toBe(201);
    const { id } = await created.json();
    if (leaving) {
      const renamed = await request.patch(`${API}/users/${account.id}`, {
        headers: headers(account),
        data: { first_name: confirmation },
      });
      expect(renamed.status()).toBe(200);
      expect((await renamed.json()).first_name).toBe(confirmation);
      const joined = await request.post(`${API}/memberships`, {
        headers: headers(owner),
        data: { company_id: id, user_id: account.id },
      });
      expect(joined.status()).toBe(201);
    }
    await startSession(page, account);
    await page.goto(`organizations/${id}`);
    await openOrganizationSettings(page);
    const button = leaving ? "Выйти из организации" : "Удалить организацию";
    await page.getByRole("button", { name: button, exact: true }).click();
    const dialog = page.getByRole("dialog", { name: leaving ? "Подтвердите выход" : button });
    const input = dialog.getByRole("textbox");
    const confirm = dialog.getByRole("button", { name: "Подтвердить", exact: true });
    await input.fill(confirmation.replace("\n", " "));
    await expect(confirm).toBeDisabled();
    await input.fill(confirmation);
    await expect(confirm).toBeEnabled();
    await confirm.click();
    await expect(page).toHaveURL(/\/organizations$/);
    expect((await request.get(`${API}/companies/${id}`, {
      headers: headers(account),
    })).status()).toBe(404);
  });
}

test("organization creation, creator restrictions and safe deletion errors", async ({
  page,
  request,
  account,
}) => {
  await startSession(page, account);
  await page.goto("organizations");
  await page
    .getByRole("button", { name: "Создать организацию", exact: true })
    .click();
  await page.getByLabel("Название организации").fill("Новая команда");
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Создать организацию" })
    .click();
  const row = page.getByRole("article", { name: "Новая команда", exact: true });
  await expect(row).toBeVisible();
  await expect(row.getByRole("button")).toHaveCount(0);
  await row
    .getByRole("link", { name: "Открыть организацию: Новая команда" })
    .press("Enter");
  await expect(page).toHaveURL(/\/organizations\/[^/]+$/);
  await openOrganizationSettings(page);
  const deleteButton = page.getByRole("button", {
    name: "Удалить организацию",
  });
  const dialog = page.getByRole("dialog", { name: "Удалить организацию" });
  await expect(deleteButton).toBeEnabled();
  await deleteButton.press("Enter");
  await expect(dialog.getByRole("textbox")).toBeFocused();
  await expect(dialog).toContainText("Это действие нельзя отменить.");
  await page.keyboard.press("Escape");
  await expect(deleteButton).toBeFocused();
  await deleteButton.click();
  await dialog.getByRole("button", { name: "Отмена" }).click();
  await expect(deleteButton).toBeFocused();
  await page.keyboard.press("Enter");
  const confirm = dialog.getByRole("button", {
    name: "Подтвердить",
    exact: true,
  });
  await dialog.getByRole("textbox").fill("Новая");
  await expect(confirm).toBeDisabled();
  await dialog.getByRole("textbox").fill("Новая команда");
  await confirm.click();
  await expect(page).toHaveURL(/\/organizations$/);
  await expect(page.locator(".organization-directory")).toHaveAttribute(
    "aria-busy",
    "false",
  );
  await expect(row).toHaveCount(0);
  const owner = await makeAccount(request, "Боб");
  const company = await (
    await request.post(`${API}/companies`, {
      headers: headers(owner),
      data: { name: "Команда Боба" },
    })
  ).json();
  const membership = await request.post(`${API}/memberships`, {
    headers: headers(owner),
    data: {
      company_id: company.id,
      user_id: account.id,
      role: "admin",
      permissions: ["company.manage"],
    },
  });
  expect(membership.status()).toBe(201);
  await page.goto(`organizations/${company.id}`);
  await openOrganizationSettings(page);
  await expect(page.getByLabel("Название организации")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Удалить организацию" }),
  ).toHaveCount(0);
  await page.goto("profile");
  await page.getByRole("tab", { name: "Аккаунт", exact: true }).click();
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
  await page.goto(`organizations/${company.id}`);
  await expect(page.locator(".member-row")).toHaveCount(0);
  await openOrganizationSettings(page);
  await page.getByRole("button", { name: "Выйти из организации" }).click();
  await page.getByRole("dialog", { name: "Подтвердите выход" }).getByRole("textbox").fill(account.first_name);
  await page
    .getByRole("dialog", { name: "Подтвердите выход" })
    .getByRole("button", { name: "Подтвердить", exact: true })
    .click();
  await expect(page).toHaveURL(/\/organizations$/);
  await expect(page.locator(".organization-directory")).toHaveAttribute(
    "aria-busy",
    "false",
  );
  await expect(page.getByRole("heading", { name: "Команда Боба" })).toHaveCount(
    0,
  );
});

test("organization deletion preserves members, tokens and computers until dependencies are explicitly removed", async ({
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
  const computer = await addComputer(
    request,
    account,
    "lab-node",
    company.id,
    false,
  );
  await startSession(page, account);
  await page.goto("organizations");
  await page
    .getByRole("link", { name: `Открыть организацию: ${company.name}` })
    .click();
  await expect(
    page.getByRole("link", { name: /Список компьютеров/ }),
  ).toHaveAttribute("href", `/compute/computers?company_id=${company.id}&only_available=false`);
  await openOrganizationSettings(page);
  await page.getByRole("button", { name: "Удалить организацию" }).click();
  const dialog = page.getByRole("dialog", { name: "Удалить организацию" });
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
  await expect(dialog.getByRole("alert")).toContainText(
    "Удаление заблокировано связанными ресурсами",
  );
  expect(
    (
      await request.delete(`${API}/computers/${computer.id}`, {
        headers: headers(account),
      })
    ).status(),
  ).toBe(204);
  await dialog
    .getByRole("button", { name: "Подтвердить", exact: true })
    .click();
  await expect(page).toHaveURL(/\/organizations$/);
  await expect(dialog).not.toBeVisible();
  await expect(page.locator(".organization-directory")).toHaveAttribute(
    "aria-busy",
    "false",
  );
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
    "compute",
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
  await page.goto("compute");
  await expect(page.locator(".compute-status-summary dd")).toHaveText(["0", "0", "0"]);
  await page.screenshot({
    path: info.outputPath("compute-light.png"),
    fullPage: true,
  });
});

test("sidebar omits the cloud entry while the overview card still opens the cloud page", async ({
  page,
  account,
}) => {
  await login(page, account);
  const navigation = page.getByRole("navigation", { name: "Навигация кабинета" });
  for (const label of ["Обзор", "Compute", "Организации"]) {
    await expect(
      navigation.getByRole("link", { name: label, exact: true }),
    ).toBeVisible();
  }
  await expect(
    navigation.getByRole("link", { name: "Облако", exact: true }),
  ).toHaveCount(0);
  // The last entry keeps the desktop group gap, but a wrapped mobile row must
  // not inherit it above its own line.
  const width = page.viewportSize()?.width ?? 0;
  await expect(navigation.getByRole("link").last()).toHaveCSS(
    "margin-top",
    width <= 800 ? "0px" : "26px",
  );
  await page.locator(".cloud-product").click();
  await expect(page).toHaveURL(/\/cloud$/);
  await expect(
    page.getByRole("heading", { name: "Облако — впереди" }),
  ).toBeVisible();
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

/** Login sessions must stay outside API-token CRUD: logout preserves API access, while password revocation sends the active browser back to login. */
test("logout only revokes its session and a revoked active session returns to login", async ({
  page,
  request,
  account,
}) => {
  const issued = await request.post(`${API}/api-tokens`, {
    headers: headers(account),
    data: { user_id: account.id, name: "Survives logout", scopes: ["read", "execute"], expires_in_seconds: null },
  });
  expect(issued.status()).toBe(201);
  const token = await issued.json();
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
  expect((await request.get(`${API}/auth/check`, {
    headers: { Authorization: `Bearer ${token.token}` },
  })).status()).toBe(200);
  await login(page, account);
  // Revoke the active browser through the real account API. Sessions are no
  // longer discoverable or revocable through the API-token CRUD endpoint.
  expect((await request.patch(`${API}/users/${account.id}`, {
    headers: headers(account),
    data: { password: "new sufficiently long password" },
  })).status()).toBe(200);
  await page
    .getByRole("navigation", { name: "Навигация кабинета" })
    .getByRole("link", { name: "Compute", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "С возвращением." }),
  ).toBeVisible();
});
