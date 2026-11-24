import type { APIRequestContext, APIResponse, Locator, Page } from "@playwright/test";
import { addComputer, API, expect, headers, login, makeAccount, markAccountComputer, password, pauseMutation, startSession, test, type Account } from "./fixtures";
import { openUserMenu } from "./header-controls";
import { openOrganizationMember, openOrganizationSettings, openOrganizationTokens, switchOrganizationPeople } from "./organization-controls";
import { GiB, reserve } from "./resources";

async function createCompany(request: APIRequestContext, owner: Account, name: string) {
  const response = await request.post(`${API}/companies`, { headers: headers(owner), data: { name } });
  expect(response.status()).toBe(201);
  return await response.json() as { id: string; name: string };
}

async function addMember(request: APIRequestContext, owner: Account, companyId: string, account: Account, role: "admin" | "user", permissions?: string[]) {
  const response = await request.post(`${API}/memberships`, {
    headers: headers(owner),
    data: { company_id: companyId, user_id: account.id, role, ...(permissions ? { permissions } : {}) },
  });
  expect(response.status()).toBe(201);
  return (await response.json() as { id: string }).id;
}

function temporaryReadFailure(page: Page, endpoint: string, unavailable = () => true) {
  const origin = new URL(page.url()).origin;
  return page.route(endpoint, (route) => route.request().method() === "GET" && unavailable()
    ? route.fulfill({
      status: 503,
      headers: { "access-control-allow-origin": origin, "access-control-allow-credentials": "true" },
      json: { detail: "Temporary outage" },
    })
    : route.continue());
}

async function holdPoolRead(page: Page, endpoint: string) {
  let send!: () => void;
  let deliver!: () => void;
  let report!: (response: APIResponse) => void;
  const request = new Promise<void>((resolve) => { send = resolve; });
  const response = new Promise<void>((resolve) => { deliver = resolve; });
  const captured = new Promise<APIResponse>((resolve) => { report = resolve; });
  await page.route(endpoint, async (route) => {
    await request;
    const result = await route.fetch();
    report(result);
    await response;
    await route.fulfill({ response: result });
  }, { times: 1 });
  return { send, deliver, captured };
}

async function reloadAndGoBackOnLoad(page: Page) {
  await page.addInitScript(() => {
    if (sessionStorage.getItem("compute-back-on-load") !== "1") return;
    sessionStorage.removeItem("compute-back-on-load");
    window.addEventListener("load", () => window.history.back(), { once: true });
  });
  await page.evaluate(() => sessionStorage.setItem("compute-back-on-load", "1"));
  await page.reload({ waitUntil: "commit" });
}

async function signInOnCurrentForm(page: Page, account: Account) {
  await page.getByLabel("Email или ник").fill(account.email);
  await page.getByLabel("Пароль", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Войти", exact: true }).click();
}

async function signInAfterSwitchingForms(page: Page, account: Account) {
  await page.getByRole("link", { name: "Создать аккаунт ↗", exact: true }).click();
  await expect(page).toHaveURL(/\/register$/);
  await page.getByRole("link", { name: "Войти ↗", exact: true }).click();
  await expect(page).toHaveURL(/\/login$/);
  await signInOnCurrentForm(page, account);
}

test("Compute uses account status totals and organization rows display actual pools, roles, images and counts", async ({ page, request, account }) => {
  const owner = await makeAccount(request);
  const lab = await createCompany(request, account, "Лаборатория");
  const admin = await createCompany(request, owner, "Общий стенд");
  const restricted = await createCompany(request, owner, "Ограниченный пул");
  await addMember(request, owner, admin.id, account, "admin");
  await addMember(request, owner, restricted.id, account, "user", ["members.read"]);
  const png = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jWZkAAAAASUVORK5CYII=", "base64");
  expect((await request.put(`${API}/companies/${lab.id}/images/avatar`, {
    headers: { ...headers(account), "Content-Type": "image/png" }, data: png,
  })).status()).toBe(200);
  const online = await addComputer(request, account, "Mac без имени");
  expect((await request.patch(`${API}/computers/${online.id}`, {
    headers: headers(account), data: { name: null },
  })).status()).toBe(200);
  const offline = await addComputer(request, account, "Тестовый Mac", lab.id, false);
  const foreign = await addComputer(request, owner, "Mac другого аккаунта", admin.id);
  await addComputer(request, account, "API-агент");
  for (const computer of [online, offline, foreign]) await markAccountComputer(request, computer.id);
  await startSession(page, account);
  await page.goto("compute");
  await expect(page.locator(".compute-status-summary dd")).toHaveText(["2", "1", "1"]);
  const computersLink = page.getByRole("link", { name: "Подключённые компьютеры", exact: true });
  const poolsLink = page.getByRole("link", { name: "Вычислительные пулы", exact: true });
  await expect(computersLink.locator("strong")).toHaveText("2");
  await expect(poolsLink.locator("strong")).toHaveText("4");
  await computersLink.click();
  const on = page.getByRole("region", { name: "Онлайн", exact: true });
  const off = page.getByRole("region", { name: "Не в сети", exact: true });
  await expect(on.getByRole("listitem")).toHaveCount(1);
  await expect(on.locator(".status-badge")).toHaveText("Онлайн");
  await expect(on).toContainText(online.id.slice(0, 8));
  await expect(on).toContainText("linux · amd64 · oci");
  await expect(on.getByRole("link", { name: "Личный пул" })).toHaveAttribute("href", "/compute/pools/personal");
  await expect(off.getByRole("listitem")).toHaveCount(1);
  await expect(off.locator(".status-badge")).toHaveText("Не в сети");
  await expect(off).toContainText("Тестовый Mac");
  await expect(off.getByRole("link", { name: lab.name })).toHaveAttribute("href", `/organizations/${lab.id}`);
  await expect(page.getByText("Mac другого аккаунта")).toHaveCount(0);
  await expect(page.getByText("API-агент")).toHaveCount(0);
  await page.getByRole("link", { name: "← К Compute" }).click();
  await poolsLink.click();

  const poolList = page.getByRole("region", { name: "Вычислительные пулы" });
  await expect(poolList.getByRole("article")).toHaveCount(4);
  const personal = poolList.getByRole("article", { name: "Личный пул" });
  await expect(personal.locator(".organization-monogram")).toHaveText("ЛП");
  await expect(personal.locator(".organization-role")).toHaveText("Личный");
  await expect(personal).toContainText("Личное рабочее пространство");
  await expect(personal.getByTestId("organization-computer-count")).toHaveText("2");
  await expect(personal.locator(".organization-members-metric")).toHaveCount(0);
  for (const [company, role, members, computers] of [
    [lab, "Создатель", "1", "1"],
    [admin, "Администратор", "2", "1"],
    [restricted, "Участник", "2", "0"],
  ] as const) {
    const row = poolList.getByRole("article", { name: company.name, exact: true });
    await expect(row.locator(".organization-role")).toHaveText(role);
    await expect(row.locator(".organization-members-metric")).toHaveText(members);
    await expect(row.getByTestId("organization-computer-count")).toHaveText(computers);
    await expect(row).toContainText("Общее рабочее пространство");
  }
  await expect(poolList.getByRole("article", { name: lab.name }).locator(".organization-monogram img")).toBeVisible();
  await expect(poolList.getByRole("article", { name: admin.name }).locator(".organization-monogram")).toHaveText("ОС");
  await poolList.getByRole("link", { name: `Открыть пул ${restricted.name}` }).click();
  await expect(page.getByText("У вас нет права просматривать мощности этой организации.")).toBeVisible();
  await page.getByRole("link", { name: "← К списку пулов" }).click();
  await personal.getByRole("link").click();
  await expect(page.getByRole("region", { name: "Личный пул", exact: true }).getByTestId("computer-count")).toHaveText("2");
});

test("pool names in the connected list open the organization or the personal workspace", async ({ page, request, account }) => {
  const company = await createCompany(request, account, "Команда сборки");
  const personal = await addComputer(request, account, "Личный Mac");
  const offline = await addComputer(request, account, "Резервный Mac", undefined, false);
  const shared = await addComputer(request, account, "Командный Mac", company.id);
  for (const computer of [personal, offline, shared]) await markAccountComputer(request, computer.id);
  await startSession(page, account);
  await page.goto("compute/computers");
  await expect(page.locator(`[data-computer-id="${shared.id}"] .status-badge`)).toHaveText("Онлайн");
  await expect(page.locator(`[data-computer-id="${offline.id}"] .status-badge`)).toHaveText("Не в сети");
  await page.getByRole("link", { name: company.name, exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/organizations/${company.id}$`));
  await expect(page.getByRole("heading", { name: `${company.name}.`, exact: true })).toBeVisible();
  await expect(page.getByRole("region", { name: "Команда", exact: true })).toBeVisible();

  await page.goBack();
  const row = page.getByRole("listitem").filter({ hasText: personal.name });
  await row.getByRole("link", { name: "Личный пул", exact: true }).click();
  await expect(page).toHaveURL(/\/compute\/pools\/personal$/);
  await expect(page.getByRole("heading", { name: "Личный пул.", exact: true })).toBeVisible();
  const overview = page.getByRole("region", { name: "Сводка личного пула" });
  await expect(overview.locator("dd:not(.organization-metric-detail)")).toHaveText(["2", "8", "8", "1"]);
  const inventory = page.getByRole("region", { name: "Компьютеры", exact: true });
  await expect(inventory.locator(".organization-computer-card")).toHaveCount(2);
  await expect(inventory.getByRole("link", { name: /Резервный Mac/ })).toBeVisible();
  await expect(inventory).not.toContainText(shared.name);
  await expect(page.getByRole("region", { name: "Команда", exact: true })).toHaveCount(0);
  await inventory.getByRole("link", { name: "Список компьютеров" }).click();
  await expect(page).toHaveURL(new RegExp(`/compute/computers\\?user_id=${account.id}&only_available=false$`));
  await expect(page.getByRole("row").filter({ hasText: offline.name })).toBeVisible();
  await page.getByRole("link", { name: /К личному пулу/ }).click();
  await expect(page.getByRole("heading", { name: "Личный пул.", exact: true })).toBeVisible();
});

for (const [destination, travel] of [
  ["roster", "direct"], ["roster", "back link"], ["organization inventory", "direct"],
  ["organization inventory", "back link"], ["personal inventory", "direct"], ["personal inventory", "back link"],
  ["organization inventory", "next page"],
] as const) test(`browser history restores the connected pool link through ${destination} and ${travel}`, async ({ page, request, account }) => {
  const company = destination === "personal inventory" ? null : await createCompany(request, account, "Команда с историей");
  if (destination === "roster" && company) {
    for (let index = 0; index < 5; index++) {
      await addMember(request, account, company.id, await makeAccount(request, `Коллега ${index}`), "user");
    }
  }
  const computer = await addComputer(request, account, "Mac из списка", company?.id);
  await markAccountComputer(request, computer.id);
  if (travel === "next page") {
    for (let index = 0; index < 10; index++) await addComputer(request, account, `Mac ${index}`, company?.id, false);
  }
  await startSession(page, account);
  await page.goto("compute/computers");
  await page.getByRole("link", { name: "Перейти к содержимому", exact: true }).press("Enter");
  await expect(page).toHaveURL(/\/compute\/computers#main$/);
  const origin = page.getByRole("link", { name: company?.name ?? "Личный пул", exact: true });
  await origin.press("Enter");
  if (destination === "roster" && company) {
    await page.locator(`a[href^="/organizations/${company.id}/members"]`).click();
    await expect(page).toHaveURL(/\/members$/);
  } else {
    await page.getByRole("link", { name: "Список компьютеров", exact: true }).click();
    await expect(page).toHaveURL(/only_available=false/);
  }
  if (travel === "back link") {
    await page.getByRole("link", { name: company ? "← К организации" : "← К личному пулу", exact: true }).click();
    await expect(page.getByRole("heading", { name: `${company?.name ?? "Личный пул"}.`, exact: true })).toBeVisible();
  } else if (travel === "next page") {
    await page.getByRole("navigation", { name: "Страница" }).getByRole("button", { name: "Далее →", exact: true }).click();
    await expect(page).toHaveURL(/page=2$/);
  }
  await page.evaluate((delta) => history.go(delta), travel === "direct" ? -2 : -3);
  await expect(page).toHaveURL(/\/compute\/computers#main$/);
  await expect(origin).toBeFocused();
});

for (const [personal, path] of [
  [false, "pool card"], [true, "pool card"], [false, "inventory link"], [true, "inventory link"],
  [false, "inventory row"], [true, "details back link"], [false, "benchmark card"],
] as const) test(`browser history restores the connected ${personal ? "personal" : "organization"} pool link through ${path}`, async ({ page, request, account }) => {
  const company = personal ? null : await createCompany(request, account, "Команда с карточкой компьютера");
  const computer = await addComputer(request, account, "Mac с подробностями", company?.id);
  await markAccountComputer(request, computer.id);
  if (path === "benchmark card") {
    expect((await request.post(`${API}/benchmark-results`, {
      headers: headers(account),
      data: { computer_id: computer.id, benchmarker_version: "1.0.0", benchmark_name: "single_core", number: 42 },
    })).status()).toBe(201);
  }
  await startSession(page, account);
  await page.goto("compute/computers");
  await page.getByRole("link", { name: "Перейти к содержимому", exact: true }).press("Enter");
  await expect(page).toHaveURL(/\/compute\/computers#main$/);
  const origin = page.getByRole("link", { name: company?.name ?? "Личный пул", exact: true });
  await origin.press("Enter");
  let entries = 2;
  if (path === "pool card" || path === "benchmark card") {
    await page.locator(".organization-computer-card").filter({ hasText: computer.name }).click();
  } else {
    entries++;
    await page.getByRole("link", { name: "Список компьютеров", exact: true }).click();
    const row = page.getByRole("row").filter({ hasText: computer.name });
    if (path === "inventory row") await row.getByRole("cell").nth(1).click();
    else await row.getByRole("link", { name: computer.name, exact: true }).click();
  }
  await expect(page.getByRole("heading", { name: `${computer.name}.`, exact: true })).toBeVisible();
  if (path === "details back link") {
    entries++;
    await page.getByRole("link", { name: "← К списку компьютеров", exact: true }).click();
    await expect(page).toHaveURL(/\/compute\/computers\?/);
  } else if (path === "benchmark card") {
    entries++;
    await page.getByRole("link", { name: /single_core/ }).click();
    await expect(page).toHaveURL(/benchmark=single_core/);
  }
  await page.evaluate((steps) => history.go(-steps), entries);
  await expect(page).toHaveURL(/\/compute\/computers#main$/);
  await expect(origin).toBeFocused();
});

for (const [signInAgain, anchored] of [[false, true], [true, false], [true, true]] as const) {
  test(`a benchmark opened from the personal pool keeps its destination after ${signInAgain ? "signing in again" : "reload"}${anchored ? " with the content anchor" : ""}`, async ({ page, request, account, context }) => {
    const computer = await addComputer(request, account, "Mac с бенчмарком");
    await markAccountComputer(request, computer.id);
    expect((await request.post(`${API}/benchmark-results`, {
      headers: headers(account),
      data: { computer_id: computer.id, benchmarker_version: "1.0.0", benchmark_name: "single_core", number: 42 },
    })).status()).toBe(201);
    await startSession(page, account);
    await page.setViewportSize({ width: 1000, height: 460 });
    await page.goto("compute/computers");
    const origin = page.getByRole("link", { name: "Личный пул", exact: true });
    await origin.press("Enter");
    await page.locator(".organization-computer-card").filter({ hasText: computer.name }).click();
    const title = page.getByRole("heading", { name: `${computer.name}.`, exact: true });
    const historyPanel = page.getByRole("region", { name: /^История бенчмарка/ });
    await page.getByRole("link", { name: /single_core/ }).click();
    await expect(historyPanel).toBeFocused();
    if (anchored) {
      await page.getByRole("link", { name: "Перейти к содержимому", exact: true }).press("Enter");
      await expect(page).toHaveURL(/benchmark=single_core#main$/);
      await expect(title).toBeInViewport();
    }
    const destination = page.url();
    if (signInAgain) {
      const other = await context.newPage();
      try {
        await other.goto("compute");
        const menu = await openUserMenu(other);
        await menu.getByRole("menuitem", { name: "Выйти", exact: true }).click();
        await expect(page).toHaveURL(/\/login$/);
        await page.bringToFront();
        await signInOnCurrentForm(page, account);
        await expect(page).toHaveURL(destination);
      } finally {
        await other.close();
      }
    } else await page.reload();
    await expect(page).toHaveURL(destination);
    await expect(historyPanel).toBeVisible();
    if (anchored) {
      await expect(title).toBeInViewport();
      await expect(historyPanel).not.toBeFocused();
    } else await expect(historyPanel).toBeFocused();
    await page.evaluate((steps) => history.go(-steps), anchored ? 4 : 3);
    await expect(page).toHaveURL(/\/compute\/computers$/);
    await expect(origin).toBeFocused();
  });
}

for (const scoped of [false, true]) for (const lastPageDisappears of [false, true]) {
  test(`the content anchor remains focused on the ${scoped ? "organization inventory" : "connected list"} when a refresh ${lastPageDisappears ? "removes the last page" : "leaves the last page"}`, async ({ page, request, account, browserName }) => {
    const company = scoped ? await createCompany(request, account, "Команда с двумя страницами компьютеров") : null;
    const total = lastPageDisappears ? 11 : 12;
    const computers = [];
    for (let index = 1; index <= total; index++) {
      const computer = await addComputer(request, account, `Mac ${index}`, company?.id, false);
      computers.push(computer);
      if (!scoped || index === 1) await markAccountComputer(request, computer.id);
    }
    await startSession(page, account);
    await page.setViewportSize({ width: 1000, height: 500 });
    await page.goto(scoped ? "compute/computers" : "compute/computers?page=2");
    const pager = page.getByRole("navigation", { name: "Страница", exact: true });
    const rows = page.locator(scoped ? ".computer-row" : ".connected-computer-row");
    if (company) {
      await page.getByRole("link", { name: company.name, exact: true }).click();
      await page.getByRole("link", { name: "Список компьютеров", exact: true }).click();
      await pager.getByRole("button", { name: "Далее →", exact: true }).click();
    }
    await expect(rows).toHaveCount(total - 10);
    const main = page.getByRole("main");
    const back = main.getByRole("link", { name: scoped ? "← К организации" : "← К Compute", exact: true });
    await page.getByRole("link", { name: "Перейти к содержимому", exact: true }).press("Enter");
    await expect(page).toHaveURL(/page=2#main$/);
    await expect(main).toBeFocused();
    expect((await request.delete(`${API}/computers/${computers[0].id}`, { headers: headers(account) })).status()).toBe(204);
    const loaded = page.waitForResponse((response) =>
      response.url().startsWith(`${API}/${scoped ? "computers" : "connected-computers"}?`) && response.status() === 200);
    await page.evaluate(() => window.dispatchEvent(new Event("online")));
    await (await loaded).finished();
    await expect(rows).toHaveCount(lastPageDisappears ? 10 : 1);
    const expected = lastPageDisappears ? (scoped ? /page=1#main$/ : /\/compute\/computers#main$/) : /page=2#main$/;
    await expect(page).toHaveURL(expected);
    await expect(main).toBeFocused();
    await page.keyboard.press(browserName === "webkit" ? "Alt+Tab" : "Tab");
    await expect(back).toBeFocused();
  });
}

for (const lastPageDisappears of [false, true]) test(`the content anchor remains focused on the roster when the last page ${lastPageDisappears ? "is removed" : "still exists"}`, async ({ page, request, account, browserName }) => {
  const company = await createCompany(request, account, "Команда с двумя страницами участников");
  const membershipIds: string[] = [];
  for (let index = 0; index < (lastPageDisappears ? 20 : 21); index++) {
    const member = await makeAccount(request, `Участник ${index}`);
    membershipIds.push(await addMember(request, account, company.id, member, "user"));
  }
  const computer = await addComputer(request, account, "Mac команды", company.id);
  await markAccountComputer(request, computer.id);
  await startSession(page, account);
  await page.setViewportSize({ width: 1000, height: 500 });
  await page.goto("compute/computers");
  await page.getByRole("link", { name: company.name, exact: true }).click();
  await page.locator(`a[href^="/organizations/${company.id}/members"]`).click();
  const pager = page.getByRole("navigation", { name: "Страница", exact: true });
  await pager.getByRole("button", { name: "Далее →", exact: true }).click();
  const rows = page.getByRole("region", { name: "Участники", exact: true }).getByRole("article");
  await expect(rows).toHaveCount(lastPageDisappears ? 1 : 2);
  const main = page.getByRole("main");
  await page.getByRole("link", { name: "Перейти к содержимому", exact: true }).press("Enter");
  await expect(page).toHaveURL(/members\?page=2#main$/);
  await expect(main).toBeFocused();
  expect((await request.delete(`${API}/memberships/${membershipIds[0]}`, { headers: headers(account) })).status()).toBe(204);
  const loaded = page.waitForResponse((response) => response.url().startsWith(`${API}/memberships?company_id=${company.id}`) &&
    response.status() === 200);
  await page.evaluate(() => window.dispatchEvent(new Event("online")));
  await (await loaded).finished();
  await expect(rows).toHaveCount(lastPageDisappears ? 20 : 1);
  await expect(page).toHaveURL(lastPageDisappears ? /members#main$/ : /members\?page=2#main$/);
  await expect(main).toBeFocused();
  await page.keyboard.press(browserName === "webkit" ? "Alt+Tab" : "Tab");
  await expect(main.getByRole("link", { name: "← К организации", exact: true })).toBeFocused();
});

test("an explicit legacy availability filter opens the personal inventory", async ({ page, request, account }) => {
  const company = await createCompany(request, account, "Командный пул");
  const personal = await addComputer(request, account, "Личный Mac", undefined, false);
  const shared = await addComputer(request, account, "Командный Mac", company.id);
  await markAccountComputer(request, shared.id);
  await startSession(page, account);
  await page.goto("compute/computers?only_available=false");
  await expect(page.getByRole("row").filter({ hasText: personal.name })).toBeVisible();
  await expect(page.getByRole("row").filter({ hasText: shared.name })).toHaveCount(0);
  await page.goto("compute/computers");
  await expect(page.getByRole("listitem").filter({ hasText: shared.name })).toBeVisible();
  await expect(page.getByRole("listitem").filter({ hasText: personal.name })).toHaveCount(0);
});

for (const [scope, destination, linkName] of [
  ["organization", "/organizations", "← К организациям"],
  ["personal", "/compute/pools", "← К списку пулов"],
  ["personal", "/compute", "← К Compute"],
] as const) for (const jump of [false, true]) {
  test(`the ${scope} pool's back link to ${destination} preserves the computer for ${jump ? "a history jump" : "two Back actions"}`, async ({ page, request, account }) => {
    const company = scope === "organization" ? await createCompany(request, account, "Команда для возврата") : null;
    const poolName = company?.name ?? "Личный пул";
    for (let index = 1; index <= 15; index++) {
      const computer = await addComputer(request, account, `Mac ${index}`, company?.id, false);
      await markAccountComputer(request, computer.id);
    }
    await startSession(page, account);
    await page.setViewportSize({ width: 950, height: 460 });
    await page.goto("compute/computers?page=2");
    const rows = page.locator(".connected-computer-row");
    await expect(rows).toHaveCount(5);
    const computerId = await rows.last().getAttribute("data-computer-id");
    const origin = page.locator(`[data-computer-id="${computerId}"]`).getByRole("link", { name: poolName, exact: true });
    await origin.press("Enter");
    await expect(page.getByRole("heading", { name: `${poolName}.`, exact: true })).toBeVisible();
    await page.getByRole("link", { name: linkName, exact: true }).press("Enter");
    await expect(page).toHaveURL(new RegExp(`${destination}$`));
    if (jump) await page.evaluate(() => history.go(-2));
    else {
      await page.goBack();
      await expect(page.getByRole("heading", { name: `${poolName}.`, exact: true })).toBeVisible();
      await page.goBack();
    }
    await expect(page).toHaveURL(/\/compute\/computers\?page=2$/);
    await expect(rows).toHaveCount(5);
    await expect(origin).toBeFocused({ timeout: 5000 });
    await expect(origin).toBeInViewport();
  });
}

for (const action of ["unchanged", "filtered", "paginated"] as const) {
  test(`the ${action} organization directory preserves the computer on a history jump`, async ({ page, request, account }) => {
    const company = await createCompany(request, account, "Лаборатория доставки");
    for (let index = 1; index <= (action === "paginated" ? 20 : 1); index++) {
      await createCompany(request, account, `Другая команда ${index}`);
    }
    const computer = await addComputer(request, account, "Клавиатурный Mac", company.id);
    await markAccountComputer(request, computer.id);
    await startSession(page, account);
    await page.goto("compute/computers");
    await page.getByRole("link", { name: "Перейти к содержимому", exact: true }).press("Enter");
    await expect(page).toHaveURL(/\/compute\/computers#main$/);
    const origin = page.getByRole("link", { name: company.name, exact: true });
    await origin.press("Enter");
    await expect(page.getByRole("heading", { name: `${company.name}.`, exact: true })).toBeVisible();
    await page.getByRole("link", { name: "← К организациям", exact: true }).press("Enter");
    const directory = page.getByRole("region", { name: "Все организации", exact: true });
    const entries = directory.getByRole("article");
    await expect(entries).toHaveCount(action === "paginated" ? 20 : 2);
    if (action === "filtered") {
      await directory.getByRole("searchbox").fill("Лаборатория");
      await expect(page).toHaveURL(/\/organizations\?q=/);
      await expect(entries).toHaveCount(1);
      await expect(entries.first()).toHaveAccessibleName(company.name);
    } else if (action === "paginated") {
      await directory.getByRole("button", { name: "Далее →", exact: true }).click();
      await expect(page).toHaveURL(/\/organizations\?page=2$/);
      await expect(entries).toHaveCount(1);
    }
    await page.evaluate((steps) => history.go(steps), action === "paginated" ? -3 : -2);
    await expect(page).toHaveURL(/\/compute\/computers#main$/);
    await expect(origin).toBeFocused({ timeout: 5000 });
    await expect(origin).toBeInViewport();
  });
}

for (const scope of ["personal", "organization"] as const) {
  test(`Back restores the originating computer after opening the ${scope} pool from page two`, async ({ page, request, account }) => {
    const personal = scope === "personal";
    const company = await createCompany(request, account, "Команда со второй страницы");
    for (let i = 1; i <= 15; i++) {
      const computer = await addComputer(request, account, `Mac ${i}`, personal ? undefined : company.id, false);
      await markAccountComputer(request, computer.id);
    }
    await startSession(page, account);
    await page.setViewportSize({ width: 1000, height: 500 });
    await page.goto("compute/computers?page=2");
    const rows = page.locator(".connected-computer-row");
    await expect(rows).toHaveCount(5);
    const originId = await rows.last().getAttribute("data-computer-id");
    const origin = page.locator(`[data-computer-id="${originId}"]`).getByRole("link", { name: personal ? "Личный пул" : company.name, exact: true });
    await origin.scrollIntoViewIfNeeded();
    await origin.focus();
    const pool = personal ? /\/compute\/pools\/personal$/ : new RegExp(`/organizations/${company.id}$`);
    await origin.press("Enter");
    await expect(page).toHaveURL(pool);
    for (const visit of ["initial", "forward", "reload"]) {
      if (visit === "reload") await reloadAndGoBackOnLoad(page);
      else await page.goBack();
      await expect(page).toHaveURL(/\/compute\/computers\?page=2$/);
      await expect(origin).toBeFocused();
      await expect(origin).toBeInViewport();
      if (visit !== "reload") {
        await page.goForward();
        await expect(page).toHaveURL(pool);
      }
    }
    await page.getByRole("link", { name: "← К Compute", exact: true }).click();
    await page.goBack();
    await expect(page).toHaveURL(/\/compute\/computers\?page=2$/);
    await expect(rows).toHaveCount(5);
    await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
    await expect(origin).not.toBeFocused();
    await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(0);
  });

  for (const travel of ["ordinary Back", "Back to the anchored list", "a browser history jump"] as const) test(`${travel} restores the ${scope} pool link after skipping to content`, async ({ page, request, account, browserName }) => {
    const company = scope === "organization" ? await createCompany(request, account, "Команда с клавиатурной ссылкой") : null;
    const computer = await addComputer(request, account, "Клавиатурный Mac", company?.id);
    await markAccountComputer(request, computer.id);
    await startSession(page, account);
    await page.goto("compute/computers");
    const origin = page.getByRole("link", { name: company?.name ?? "Личный пул", exact: true });
    const skip = page.getByRole("link", { name: "Перейти к содержимому", exact: true });
    const anchored = travel !== "ordinary Back";
    const source = anchored ? /\/compute\/computers#main$/ : /\/compute\/computers$/;
    if (anchored) {
      await skip.press("Enter");
      await expect(page).toHaveURL(source);
    }
    await origin.press("Enter");
    const main = page.getByRole("main");
    await expect(main.getByRole("heading", { level: 1 })).toHaveText(`${company?.name ?? "Личный пул"}.`);
    await skip.press("Enter");
    await expect(page).toHaveURL(/#main$/);
    await expect(main).toBeFocused();
    await page.keyboard.press(browserName === "webkit" ? "Alt+Tab" : "Tab");
    await expect(main.locator(".back-link").first()).toBeFocused();
    await skip.press("Enter");
    await expect(main).toBeFocused();
    if (travel === "a browser history jump") await page.evaluate(() => history.go(-2));
    else {
      await page.goBack();
      await expect(page).not.toHaveURL(/#main$/);
      await page.goBack();
    }
    await expect(page).toHaveURL(source);
    await expect(origin).toBeFocused();
  });

  for (const signedOut of [false, true]) test(`early Back from the ${scope} pool restores the link while the app bundle is downloading${signedOut ? " after signing out in another tab" : ""}`, async ({ page, request, account }) => {
    const poolName = scope === "personal" ? "Личный пул" : "Команда при медленной загрузке";
    const companyId = scope === "personal" ? undefined : (await createCompany(request, account, poolName)).id;
    for (let index = 1; index <= 13; index++) {
      const computer = await addComputer(request, account, `Mac ${index}`, companyId, false);
      await markAccountComputer(request, computer.id);
    }
    await startSession(page, account);
    await page.setViewportSize({ width: 950, height: 470 });
    await page.goto("compute/computers?page=2");
    const row = page.locator(".connected-computer-row").last();
    const originId = await row.getAttribute("data-computer-id");
    const origin = page.locator(`[data-computer-id="${originId}"]`).getByRole("link", { name: poolName, exact: true });
    await origin.press("Enter");
    await expect(page.getByRole("heading", { name: `${poolName}.`, exact: true })).toBeVisible();

    let releaseBundle!: () => void;
    const held = new Promise<void>((resolve) => { releaseBundle = resolve; });
    const bundle = "**/assets/index-*.js";
    const started = page.waitForRequest(bundle);
    await page.route(bundle, async (route) => {
      await held;
      await route.continue();
    }, { times: 1 });
    const other = signedOut ? await page.context().newPage() : null;
    try {
      await page.reload({ waitUntil: "commit" });
      await started;
      await page.locator("#root").waitFor({ state: "attached" });
      if (other) {
        await other.goto("compute");
        const menu = await openUserMenu(other);
        const endedSession = other.waitForResponse((response) => response.url() === `${API}/auth/session` &&
          response.request().method() === "DELETE" && response.status() === 204);
        await menu.getByRole("menuitem", { name: "Выйти", exact: true }).click();
        await endedSession;
        await page.bringToFront();
      }
      await page.evaluate(() => window.history.back());
      await expect(page).toHaveURL(/\/compute\/computers\?page=2$/);
    } finally {
      releaseBundle();
      await other?.close();
    }
    if (signedOut) {
      await expect(page).toHaveURL(/\/login$/);
      await signInOnCurrentForm(page, account);
      await expect(page).toHaveURL(/\/compute\/computers\?page=2$/);
    }
    await expect(origin).toBeFocused();
    await expect(origin).toBeInViewport();
  });

  for (const progress of ["pending", "canceled", "finished"] as const) {
    test(`rapid signing in again in another tab restores only a pending Back from the ${scope} pool (${progress})`, async ({ page, request, account }) => {
      const poolName = scope === "personal" ? "Личный пул" : "Команда при смене сессии";
      const companyId = scope === "personal" ? undefined : (await createCompany(request, account, poolName)).id;
      for (let index = 1; index <= 13; index++) {
        const computer = await addComputer(request, account, `Mac ${index}`, companyId, false);
        await markAccountComputer(request, computer.id);
      }
      await startSession(page, account);
      const other = await page.context().newPage();
      let releaseList = () => {};
      let releaseSession = () => {};
      try {
        await other.goto("compute");
        await page.bringToFront();
        await page.setViewportSize({ width: 950, height: 470 });
        await page.goto("compute/computers?page=2");
        const title = page.getByRole("heading", { name: "Подключённые компьютеры.", exact: true });
        const rows = page.locator(".connected-computer-row");
        await expect(rows).toHaveCount(3);
        const computerId = await rows.last().getAttribute("data-computer-id");
        const origin = page.locator(`[data-computer-id="${computerId}"]`).getByRole("link", { name: poolName, exact: true });
        await origin.press("Enter");
        await expect(page.getByRole("heading", { name: `${poolName}.`, exact: true })).toBeVisible();

        const list = `${API}/connected-computers?limit=10&offset=10`;
        const listRead = progress === "finished" ? null : page.waitForRequest(list);
        if (listRead) {
          const held = new Promise<void>((resolve) => { releaseList = resolve; });
          await page.route(list, async (route) => {
            await held;
            await route.continue().catch(() => {});
          }, { times: 1 });
        }
        await page.goBack();
        await listRead;
        if (progress === "canceled") {
          await page.keyboard.press("Tab");
          releaseList();
          await expect(rows).toHaveCount(3);
          await expect(origin).not.toBeFocused();
        } else if (progress === "finished") {
          await expect(origin).toBeFocused();
          await page.getByRole("link", { name: "← К Compute", exact: true }).focus();
        }

        const menu = await openUserMenu(other);
        const endpoint = `${API}/auth/session`;
        const held = new Promise<void>((resolve) => { releaseSession = resolve; });
        const waiting = page.waitForRequest((pending) => pending.url() === endpoint && pending.method() === "GET");
        await page.route(endpoint, async (route) => {
          await held;
          await route.continue().catch(() => {});
        }, { times: 1 });
        const signedOut = other.waitForResponse((response) => response.url() === endpoint &&
          response.request().method() === "DELETE" && response.status() === 204);
        await menu.getByRole("menuitem", { name: "Выйти", exact: true }).click();
        await signedOut;
        await waiting;
        await expect(title).toHaveCount(0);
        const verified = page.waitForResponse((response) => response.url() === endpoint &&
          response.request().method() === "GET" && response.status() === 200);
        await signInOnCurrentForm(other, account);
        await verified;
        releaseList();
        await expect(page).toHaveURL(/\/compute\/computers\?page=2$/);
        await expect(rows).toHaveCount(3);
        if (progress === "pending") {
          await expect(origin).toBeFocused();
          await expect(origin).toBeInViewport();
        } else {
          await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
          await expect(origin).not.toBeFocused();
        }
      } finally {
        releaseList();
        releaseSession();
        await other.close();
      }
    });
  }

  test(`Back from the ${scope} pool uses the account confirmed by the outstanding session check`, async ({ page, request, account }) => {
    const alternate = await makeAccount(request);
    const company = scope === "personal" ? null : await createCompany(request, account, "Пул для двух аккаунтов");
    const poolName = company?.name ?? "Личный пул";
    if (company) await addMember(request, account, company.id, alternate, "user");
    const computers: { id: string }[] = [];
    for (let index = 1; index <= 13; index++) {
      const computer = await addComputer(request, account, `Mac ${index}`, company?.id, false);
      computers.push(computer);
      await markAccountComputer(request, computer.id);
    }
    await startSession(page, account);
    const other = await page.context().newPage();
    let releaseFirst = () => {};
    let releaseSecond = () => {};
    const session = `${API}/auth/session`;
    const sessionConfirm = () => page.waitForResponse((response) => response.url() === session &&
      response.request().method() === "GET" && response.status() === 200);
    const signOut = async () => {
      const menu = await openUserMenu(other);
      await menu.getByRole("menuitem", { name: "Выйти", exact: true }).click();
      await expect(other).toHaveURL(/\/login$/);
    };
    try {
      await other.goto("compute");
      await page.bringToFront();
      await page.goto("compute/computers?page=2");
      const rows = page.locator(".connected-computer-row");
      await expect(rows).toHaveCount(3);
      const id = await rows.first().getAttribute("data-computer-id");
      const origin = page.locator(`[data-computer-id="${id}"]`).getByRole("link", { name: poolName, exact: true });
      await origin.press("Enter");
      const poolTitle = page.getByRole("heading", { name: `${poolName}.`, exact: true });
      await expect(poolTitle).toBeVisible();

      const firstCheck = new Promise<void>((resolve) => { releaseFirst = resolve; });
      const refresh = page.waitForRequest(session);
      await page.route(session, async (route) => {
        await firstCheck;
        await route.continue().catch(() => {});
      }, { times: 1 });
      await signOut();
      await refresh;
      const alternateConfirmed = sessionConfirm();
      await signInOnCurrentForm(other, alternate);
      expect((await (await alternateConfirmed).json()).id).toBe(alternate.id);
      await expect(poolTitle).toBeVisible();
      releaseFirst();
      expect((await request.delete(`${API}/computers/${computers[0].id}`, { headers: headers(account) })).status()).toBe(204);
      expect(await (await request.get(`${API}/connected-computers/${id}/position`, { headers: headers(account) })).json()).toBe(9);

      const secondCheck = new Promise<void>((resolve) => { releaseSecond = resolve; });
      let confirmRequest!: () => void;
      const checking = new Promise<void>((resolve) => { confirmRequest = resolve; });
      let requests = 0;
      await page.route(session, async (route) => {
        if (++requests === 2) confirmRequest();
        await secondCheck;
        await route.continue().catch(() => {});
      });
      await signOut();
      const returnedAccount = sessionConfirm();
      await signInOnCurrentForm(other, account);
      await checking;
      await expect(poolTitle).toHaveCount(0);
      await page.bringToFront();
      await page.evaluate(() => window.history.back());
      await expect(page).toHaveURL(/\/compute\/computers\?page=2$/);
      releaseSecond();
      expect((await (await returnedAccount).json()).id).toBe(account.id);
      await expect(page).toHaveURL(/\/compute\/computers$/);
      await expect(origin).toBeFocused();
    } finally {
      releaseFirst();
      releaseSecond();
      await other.close();
    }
  });

  for (const lookupStatus of ["with a successful lookup", "after a temporary position error"] as const) {
    test(`Back follows the originating computer to a different page of the ${scope} list ${lookupStatus}`, async ({ page, request, account }) => {
      const poolName = scope === "personal" ? "Личный пул" : "Команда со смещённой страницы";
      const companyId = scope === "personal" ? undefined : (await createCompany(request, account, poolName)).id;
      const computers: { id: string }[] = [];
      for (let i = 1; i <= 13; i++) {
        const computer = await addComputer(request, account, `Mac ${i}`, companyId, false);
        computers.push(computer);
        await markAccountComputer(request, computer.id);
      }
      await startSession(page, account);
      if (lookupStatus === "after a temporary position error") await page.clock.install();
      await page.setViewportSize({ width: 1000, height: 500 });
      await page.goto("compute/computers?page=2");
      const rows = page.locator(".connected-computer-row");
      await expect(rows).toHaveCount(3);
      const originId = await rows.first().getAttribute("data-computer-id");
      const origin = page.locator(`[data-computer-id="${originId}"]`).getByRole("link", { name: poolName, exact: true });
      await origin.press("Enter");
      await expect(page.getByRole("heading", { name: `${poolName}.`, exact: true })).toBeVisible();
      expect((await request.delete(`${API}/computers/${computers[0].id}`, { headers: headers(account) })).status()).toBe(204);
      const endpoint = `${API}/connected-computers/${originId}/position`;
      const error = lookupStatus === "after a temporary position error"
        ? page.waitForResponse((response) => response.url() === endpoint && response.status() === 503) : null;
      if (error) {
        await page.route(endpoint, (route) => route.fulfill({
          status: 503,
          headers: { "access-control-allow-origin": new URL(page.url()).origin, "access-control-allow-credentials": "true" },
          json: { detail: "Temporary outage" },
        }), { times: 1 });
      }
      await page.goBack();
      if (error) {
        await error;
        await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
        await page.clock.fastForward(5_001);
      }
      await expect(page).toHaveURL(/\/compute\/computers$/);
      await expect(origin).toBeFocused();
      await expect(origin).toBeInViewport();
      await page.goForward();
      await expect(page.getByRole("heading", { name: `${poolName}.`, exact: true })).toBeVisible();
      await page.goBack();
      await expect(page).toHaveURL(/\/compute\/computers$/);
      await expect(origin).toBeFocused();
    });
  }

  for (const reauthenticatedAt of ["the pool", "the computer list", "both auth screens"] as const) {
    test(`Back restores the ${scope} pool link after signing in again from ${reauthenticatedAt}`, async ({ page, context, request, account }) => {
      const poolName = scope === "personal" ? "Личный пул" : "Команда после повторного входа";
      const companyId = scope === "personal" ? undefined : (await createCompany(request, account, poolName)).id;
      for (let i = 1; i <= 14; i++) {
        const computer = await addComputer(request, account, `Mac ${i}`, companyId, false);
        await markAccountComputer(request, computer.id);
      }
      await startSession(page, account);
      await page.setViewportSize({ width: 900, height: 400 });
      await page.goto("compute/computers?page=2");
      const rows = page.locator(".connected-computer-row");
      await expect(rows).toHaveCount(4);
      const originId = await rows.last().getAttribute("data-computer-id");
      const origin = page.locator(`[data-computer-id="${originId}"]`).getByRole("link", { name: poolName, exact: true });
      await origin.press("Enter");
      const poolTitle = page.getByRole("heading", { name: `${poolName}.`, exact: true });
      const sourcePage = /\/compute\/computers\?page=2$/;
      await expect(poolTitle).toBeVisible();
      if (reauthenticatedAt === "the computer list") {
        await page.goBack();
        await expect(page).toHaveURL(sourcePage);
        await expect(origin).toBeFocused();
      }

      const secondTab = await context.newPage();
      try {
        await secondTab.goto("compute");
        const menu = await openUserMenu(secondTab);
        await menu.getByRole("menuitem", { name: "Выйти", exact: true }).click();
        await expect(page).toHaveURL(/\/login$/);
        if (reauthenticatedAt === "both auth screens") {
          await signInAfterSwitchingForms(page, account);
        } else {
          await login(secondTab, account);
          if (reauthenticatedAt === "the computer list") {
            await expect(page).toHaveURL(sourcePage);
            await page.goForward();
          }
        }
        await expect(poolTitle).toBeVisible();
      } finally {
        await secondTab.close();
      }

      await page.goBack();
      await expect(page).toHaveURL(sourcePage);
      await expect(origin).toBeFocused();
      await expect(origin).toBeInViewport();
    });
  }

  for (const interruption of [
    "401 from the connected list", "401 from position lookup", "tab sign-out during the connected list lookup", "tab sign-out during position lookup",
  ] as const) {
    test(`sign-in resumes the pending Back from the ${scope} pool after ${interruption}`, async ({
      page, request, account,
    }) => {
      const poolName = scope === "personal" ? "Личный пул" : "Команда после истечения сессии";
      const companyId = scope === "personal" ? undefined : (await createCompany(request, account, poolName)).id;
      const computers: { id: string }[] = [];
      for (let index = 1; index <= 13; index++) {
        const computer = await addComputer(request, account, `Mac ${index}`, companyId, false);
        computers.push(computer);
        await markAccountComputer(request, computer.id);
      }
      await startSession(page, account);
      await page.setViewportSize({ width: 1000, height: 470 });
      await page.goto("compute/computers?page=2");
      const rows = page.locator(".connected-computer-row");
      const movedToFirstPage = interruption.includes("position");
      const row = movedToFirstPage ? rows.first() : rows.last();
      const id = await row.getAttribute("data-computer-id");
      const origin = page.locator(`[data-computer-id="${id}"]`).getByRole("link", { name: poolName, exact: true });
      await origin.press("Enter");
      await expect(page.getByRole("heading", { name: `${poolName}.`, exact: true })).toBeVisible();

      if (movedToFirstPage) {
        expect((await request.delete(`${API}/computers/${computers[0].id}`, { headers: headers(account) })).status()).toBe(204);
      }
      const read = movedToFirstPage ? `${API}/connected-computers/${id}/position` : `${API}/connected-computers?limit=10&offset=10`;
      if (interruption.startsWith("tab sign-out")) {
        const other = await page.context().newPage();
        let releaseRead!: () => void;
        const held = new Promise<void>((resolve) => { releaseRead = resolve; });
        let confirmRead!: () => void;
        const requested = new Promise<void>((resolve) => { confirmRead = resolve; });
        try {
          await other.goto("compute");
          await page.bringToFront();
          await page.route(read, async (route) => {
            const response = await route.fetch();
            expect(response.status()).toBe(200);
            confirmRead();
            await held;
            await route.fulfill({ response }).catch(() => {});
          }, { times: 1 });
          await page.goBack();
          await requested;
          const menu = await openUserMenu(other);
          await menu.getByRole("menuitem", { name: "Выйти", exact: true }).click();
          await expect(page).toHaveURL(/\/login$/);
        } finally {
          releaseRead();
          await other.close();
        }
      } else {
        const browserOrigin = new URL(page.url()).origin;
        await page.route(read, async (route) => {
          expect((await page.request.delete(`${API}/auth/session`, {
            headers: { Origin: browserOrigin, "X-Requested-With": "ThrongDashboard" },
          })).status()).toBe(204);
          // The back-navigation request was created before the session was cleared.
          const expired = await page.request.get(read, { headers: { Origin: browserOrigin } });
          expect(expired.status()).toBe(401);
          await route.fulfill({ response: expired });
        }, { times: 1 });
        await page.goBack();
        await expect(page).toHaveURL(/\/login$/);
      }
      await signInOnCurrentForm(page, account);
      await expect(page).toHaveURL(movedToFirstPage ? /\/compute\/computers$/ : /\/compute\/computers\?page=2$/);
      await expect(origin).toBeFocused();
      await expect(origin).toBeInViewport();
    });
  }

  test(`Forward preserves the ${scope} pool origin when its list page shrinks in the background`, async ({ page, request, account }) => {
    const poolName = scope === "personal" ? "Личный пул" : "Пул после фонового обновления";
    const companyId = scope === "organization" ? (await createCompany(request, account, poolName)).id : undefined;
    const computers: { id: string }[] = [];
    for (let index = 1; index <= 11; index++) {
      const computer = await addComputer(request, account, `Mac ${index}`, companyId, false);
      computers.push(computer);
      await markAccountComputer(request, computer.id);
    }
    await startSession(page, account);
    await page.setViewportSize({ width: 1000, height: 500 });
    await page.goto("compute/computers?page=2");
    const title = page.getByRole("heading", { name: `${poolName}.`, exact: true });
    const origin = page.locator(`[data-computer-id="${computers.at(-1)!.id}"]`).getByRole("link", { name: poolName, exact: true });
    await origin.press("Enter");
    await expect(title).toBeVisible();
    await page.goBack();
    await expect(origin).toBeFocused();
    expect((await request.delete(`${API}/computers/${computers[0].id}`, { headers: headers(account) })).status()).toBe(204);
    await page.evaluate(() => window.dispatchEvent(new Event("online")));
    await expect(page).toHaveURL(/\/compute\/computers$/);
    await expect(origin).toBeVisible();
    await page.goForward();
    await expect(title).toBeVisible();
    await page.goBack();
    await expect(page).toHaveURL(/\/compute\/computers$/);
    await expect(origin).toBeFocused();
    await expect(origin).toBeInViewport();
  });

  test(`Back still restores the ${scope} pool link after the last page shrinks`, async ({ page, request, account }) => {
    const personal = scope === "personal";
    const company = await createCompany(request, account, "Команда после удаления компьютера");
    const computers: { id: string }[] = [];
    for (let i = 1; i <= 11; i++) {
      const computer = await addComputer(request, account, `Mac ${i}`, personal ? undefined : company.id, false);
      computers.push(computer);
      await markAccountComputer(request, computer.id);
    }
    await startSession(page, account);
    await page.goto("compute/computers?page=2");
    const rows = page.locator(".connected-computer-row");
    await expect(rows).toHaveCount(1);
    const originId = await rows.first().getAttribute("data-computer-id");
    const origin = page.locator(`[data-computer-id="${originId}"]`).getByRole("link", { name: personal ? "Личный пул" : company.name, exact: true });
    await origin.click();
    const removed = computers.find((entry) => entry.id !== originId)!;
    expect((await request.delete(`${API}/computers/${removed.id}`, { headers: headers(account) })).status()).toBe(204);
    await page.goBack();
    await expect(page).toHaveURL(/\/compute\/computers$/);
    await expect(origin).toBeFocused();
    await expect(origin).toBeInViewport();
    await page.goForward();
    await page.reload();
    await page.goBack();
    await expect(page).toHaveURL(/\/compute\/computers$/);
    await expect(origin).toBeFocused();
  });
}

for (const entry of ["personal pool catalog", "organization pool catalog", "organization directory"] as const) {
  test(`Back resumes the ${entry} after switching mandatory sign-in forms`, async ({ page, context, request, account }) => {
    const company = await createCompany(request, account, "Команда в каталоге");
    const directory = entry === "organization directory";
    const origin = directory ? "/organizations" : "/compute/pools";
    const pool = directory ? `/organizations/${company.id}`
      : entry === "personal pool catalog" ? "/compute/pools/personal" : `/compute/pools/${company.id}`;
    await startSession(page, account);
    await page.goto(origin.slice(1));
    await page.locator(`main a[href="${pool}"]`).click();
    await expect(page).toHaveURL(new RegExp(`${pool}$`));

    const secondTab = await context.newPage();
    try {
      await secondTab.goto("compute");
      const menu = await openUserMenu(secondTab);
      await menu.getByRole("menuitem", { name: "Выйти", exact: true }).click();
      await expect(page).toHaveURL(/\/login$/);
      await signInAfterSwitchingForms(page, account);
      await expect(page).toHaveURL(new RegExp(`${pool}$`));
    } finally {
      await secondTab.close();
    }

    await page.goBack();
    await expect(page).toHaveURL(new RegExp(`${origin}$`));
    await expect(page.locator(`main a[href="${pool}"]`)).toBeVisible();
  });
}

test("registration opened from voluntary sign-in keeps the login entry", async ({ page }) => {
  await page.goto("login");
  await page.getByRole("link", { name: "Создать аккаунт ↗", exact: true }).click();
  await expect(page).toHaveURL(/\/register$/);
  await page.goBack();
  await expect(page).toHaveURL(/\/login$/);
});

test("Back restores the computer after leaving its organization and reloading the redirect", async ({ page, request, account }) => {
  const owner = await makeAccount(request);
  const company = await createCompany(request, owner, "Команда перед выходом");
  await addMember(request, owner, company.id, account, "user", ["resources.manage", "resources.read"]);
  const computers: { id: string }[] = [];
  for (let i = 1; i <= 11; i++) {
    const computer = await addComputer(request, account, `Mac ${i}`, company.id, false);
    computers.push(computer);
    await markAccountComputer(request, computer.id);
  }
  await startSession(page, account);
  await page.goto("compute/computers?page=2");
  const rows = page.locator(".connected-computer-row");
  await expect(rows).toHaveCount(1);
  const originId = await rows.first().getAttribute("data-computer-id");
  await rows.first().getByRole("link", { name: company.name, exact: true }).click();
  const removed = computers.find((computer) => computer.id !== originId)!;
  expect((await request.delete(`${API}/computers/${removed.id}`, { headers: headers(owner) })).status()).toBe(204);
  const settings = await openOrganizationSettings(page);
  await settings.getByRole("button", { name: "Выйти из организации", exact: true }).click();
  const leave = page.getByRole("dialog", { name: "Подтвердите выход", exact: true });
  await leave.getByRole("textbox").fill(account.first_name);
  await leave.getByRole("button", { name: "Подтвердить", exact: true }).click();
  await expect(page).toHaveURL(/\/organizations$/);
  const origin = page.locator(`[data-computer-id="${originId}"]`);
  await page.goBack();
  await expect(page).toHaveURL(/\/compute\/computers$/);
  await expect(origin).toBeFocused();
  await page.goForward();
  await expect(page).toHaveURL(/\/organizations$/);
  await reloadAndGoBackOnLoad(page);
  await expect(page).toHaveURL(/\/compute\/computers$/);
  await expect(origin).toBeFocused();
  await expect(origin).toBeInViewport();
});

for (const scope of ["personal", "organization"] as const) test(`the ${scope} pool displays one snapshot when its last computer moves during navigation`, async ({ page, request, account }) => {
  const company = await createCompany(request, account, "Новый пул");
  const personal = scope === "personal";
  const computer = await addComputer(request, account, "Mac в переносе", personal ? undefined : company.id);
  await markAccountComputer(request, computer.id);
  await startSession(page, account);
  await page.goto("compute/computers");
  let finishMove!: () => void;
  const moved = new Promise<void>((resolve) => { finishMove = resolve; });
  const owner = personal ? `user_id=${account.id}` : `company_id=${company.id}`;
  await page.route(`${API}/computers?${owner}&only_available=false&limit=3`, async (route) => {
    await moved;
    await route.continue();
  });
  const pool = personal ? `users/${account.id}` : `companies/${company.id}`;
  await page.route(`${API}/${pool}/resources/pool-overview`, async (route) => {
    try {
      const response = await route.fetch();
      const transfer = await request.patch(`${API}/connected-computers/${computer.id}/pool`, {
        headers: headers(account), data: { company_id: personal ? company.id : null, expected_company_id: personal ? null : company.id },
      });
      expect(transfer.status()).toBe(200);
      await route.fulfill({ response });
    } finally {
      finishMove();
    }
  }, { times: 1 });
  await page.getByRole("link", { name: personal ? "Личный пул" : company.name, exact: true }).click();
  const overview = page.locator(".organization-overview");
  const inventory = page.getByRole("region", { name: "Компьютеры", exact: true });
  const metrics = overview.locator("dd:not(.organization-metric-detail)");
  const inventoryCount = inventory.locator(".organization-widget-count");
  await expect(metrics).toHaveText(["1", "8", "8", "1"]);
  await expect(inventoryCount).toHaveText("1");
  await expect(inventory.getByRole("link", { name: /Mac в переносе/ })).toBeVisible();

  if (personal) await page.getByRole("button", { name: "Обновить" }).click();
  else await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect(metrics).toHaveText(["0", "0", "0", personal ? "0" : "1"]);
  await expect(inventoryCount).toHaveText("0");
  await expect(inventory.getByRole("link", { name: /Mac в переносе/ })).toHaveCount(0);
});

test("the personal history and overview agree after a transfer during navigation", async ({ page, request, account }) => {
  const company = await createCompany(request, account, "Пул назначения");
  const computer = await addComputer(request, account, "Mac перед переносом");
  await markAccountComputer(request, computer.id);
  await startSession(page, account);
  await page.goto("compute/computers");
  const path = `${API}/users/${account.id}/resources`;
  let releaseOverview!: () => void;
  const transferred = new Promise<void>((resolve) => { releaseOverview = resolve; });
  await page.route(`${path}/pool-overview`, async (route) => {
    await transferred;
    await route.continue();
  }, { times: 1 });
  await page.route((url) => url.origin + url.pathname === `${path}/availability-history`, async (route) => {
    try {
      const oldHistory = await route.fetch();
      expect(oldHistory.status()).toBe(200);
      expect((await oldHistory.json()).points.at(-1).cpu_millis).toBe(8000);
      expect((await request.patch(`${API}/connected-computers/${computer.id}/pool`, {
        headers: headers(account), data: { company_id: company.id, expected_company_id: null },
      })).status()).toBe(200);
      await route.fulfill({ response: oldHistory });
    } finally {
      releaseOverview();
    }
  }, { times: 1 });

  await page.getByRole("link", { name: "Личный пул", exact: true }).click();
  const overview = page.locator(".organization-overview");
  const plot = page.getByRole("slider", { name: "Мощности и загрузка" });
  await expect(overview.locator("dd:not(.organization-metric-detail)")).toHaveText(["0", "0", "0", "0"]);
  await expect(page.getByText("В личном пуле пока нет компьютеров.")).toBeVisible();
  await expect(plot).toHaveAttribute("aria-valuetext", /CPU · мощность 0 ядер/);
});

for (const [scope, gesture] of [["personal", "click"], ["personal", "tap"], ["organization", "tap"]] as const) {
  test(`a ${scope} chart ${gesture} survives the inventory arriving during the gesture`, async ({
    page, request, account, context, browserName, hasTouch,
  }) => {
    test.skip(gesture === "tap" && (browserName !== "chromium" || !hasTouch), "Use native Chromium touch events");
    const company = scope === "organization" ? await createCompany(request, account, "Организация с графиком") : null;
    const poolName = company?.name ?? "Личный пул";
    const pool = company ? `companies/${company.id}` : `users/${account.id}`;
    for (let i = 1; i <= 3; i++) {
      const computer = await addComputer(request, account, `Mac ${i}`, company?.id);
      await markAccountComputer(request, computer.id);
    }
    await startSession(page, account);
    await page.setViewportSize({ width: 414, height: 896 });
    await page.goto("compute/computers");
    let releaseOverview!: () => void;
    const released = new Promise<void>((resolve) => { releaseOverview = resolve; });
    await page.route(`${API}/${pool}/resources/pool-overview`, async (route) => {
      await released;
      await route.continue();
    }, { times: 1 });
    const inventory = page.getByRole("region", { name: "Компьютеры", exact: true });
    const organizationHistory = page.getByRole("region", { name: "История CPU", exact: true });
    const control = company ? organizationHistory.locator(".organization-resource-chart")
      : gesture === "click" ? page.getByRole("button", { name: "CPU · мощность", exact: true })
      : page.getByRole("slider", { name: "Мощности и загрузка" });
    const touch = gesture === "tap" ? await context.newCDPSession(page) : null;
    try {
      await page.getByRole("link", { name: poolName, exact: true }).first().click();
      await expect(control).toBeVisible();
      await control.scrollIntoViewIfNeeded();
      const box = (await control.boundingBox())!;
      const x = box.x + box.width / 2, y = box.y + box.height / 2;
      if (touch) {
        await touch.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x, y }] });
      } else {
        await page.mouse.move(x, y);
        await page.mouse.down();
      }
      releaseOverview();
      await expect(inventory.locator(".organization-computer-card")).toHaveCount(3);
      if (touch) {
        await touch.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
        if (company) await expect(control).toBeVisible();
        else await expect(control.locator(".chart-tooltip")).toBeVisible();
      } else {
        await page.mouse.up();
        await expect(control).toHaveAttribute("aria-pressed", "false");
      }
      const destination = company ? new RegExp(`/organizations/${company.id}$`) : /\/compute\/pools\/personal$/;
      await expect(page).toHaveURL(destination);
    } finally {
      releaseOverview();
      await touch?.detach();
    }
  });
}

for (const scope of ["personal", "organization"] as const) {
  for (const removed of ["preceding", "pressed"] as const) {
    test(`a tap on a ${scope} card stays on the selected computer when the ${removed} card is removed`, async ({
      page, context, request, account, browserName, hasTouch,
    }) => {
      test.skip(browserName !== "chromium" || !hasTouch, "Use native Chromium touch events");
      const company = scope === "organization" ? await createCompany(request, account, "Команда с компьютерами") : null;
      const poolName = company?.name ?? "Личный пул";
      for (let index = 1; index <= 3; index++) {
        const computer = await addComputer(request, account, `Mac ${index}`, company?.id);
        await markAccountComputer(request, computer.id);
      }
      await startSession(page, account);
      await page.setViewportSize({ width: 414, height: 896 });
      await page.goto("compute/computers");
      await page.getByRole("link", { name: poolName, exact: true }).first().click();
      const poolUrl = page.url();
      const cards = page.locator(".organization-computer-card");
      await expect(cards).toHaveCount(3);
      const target = cards.nth(1);
      const destination = (await target.getAttribute("href"))!;
      const removedLink = cards.nth(removed === "pressed" ? 1 : 0);
      const removedId = new URL((await removedLink.getAttribute("href"))!, poolUrl).pathname.split("/").at(-1);
      await target.evaluate((element) => element.scrollIntoView({ block: "center" }));
      const box = (await target.boundingBox())!;
      const point = { x: box.x + box.width / 2, y: box.y + box.height / 2 };
      const touch = await context.newCDPSession(page);
      try {
        await touch.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [point] });
        expect((await request.delete(`${API}/computers/${removedId}`, { headers: headers(account) })).status()).toBe(204);
        await page.evaluate(() => window.dispatchEvent(new Event("online")));
        await expect(page.locator(".organization-overview dd").first()).toHaveText("2");
        await touch.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
        if (removed === "pressed") {
          await expect(cards).toHaveCount(2);
          await expect(page).toHaveURL(poolUrl);
        } else {
          await expect(page).toHaveURL(new URL(destination, poolUrl).href);
        }
      } finally {
        await touch.detach();
      }
    });
  }
}

test("renaming an organization during a tap preserves the original pool target", async ({
  page, request, account, browserName, hasTouch,
}) => {
  test.skip(browserName !== "chromium" || !hasTouch, "Use native Chromium touch events");
  const company = await createCompany(request, account, "Инфраструктура и системы распределённых вычислений");
  const computer = await addComputer(request, account, "Командный Mac", company.id, false);
  await markAccountComputer(request, computer.id);
  await startSession(page, account);
  await page.setViewportSize({ width: 414, height: 896 });
  await page.goto("compute/computers");
  const link = page.getByRole("link", { name: company.name, exact: true });
  await link.evaluate((element) => element.scrollIntoView({ block: "center" }));
  const box = (await link.boundingBox())!;
  const touch = await page.context().newCDPSession(page);
  try {
    await touch.send("Input.dispatchTouchEvent", {
      type: "touchStart", touchPoints: [{ x: box.x + box.width - 6, y: box.y + box.height / 2 }],
    });
    const update = await request.patch(`${API}/companies/${company.id}`, {
      headers: headers(account), data: { name: "ИТ" },
    });
    expect(update.status()).toBe(200);
    const refreshed = page.waitForResponse((response) =>
      response.url() === `${API}/connected-computers?limit=10&offset=0` && response.status() === 200);
    await page.evaluate(() => window.dispatchEvent(new Event("online")));
    await (await refreshed).finished();
    await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
    await touch.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
    await expect(page).toHaveURL(new RegExp(`/organizations/${company.id}$`));
    await expect(page.getByRole("heading", { name: "ИТ.", exact: true })).toBeVisible();
  } finally {
    await touch.detach();
  }
});

for (const mutation of ["remove a preceding row", "remove the pressed row", "move the pressed computer"] as const) {
  test(`the connected pool link keeps its target when another client can ${mutation}`, async ({
    page, request, account, context, browserName, hasTouch,
  }) => {
    test.skip(browserName !== "chromium" || !hasTouch, "Use native Chromium touch events");
    const company = await createCompany(request, account, "Команда на нажатой ссылке");
    const computers: { id: string }[] = [];
    for (let index = 1; index <= 3; index++) {
      const computer = await addComputer(request, account, `Mac ${index}`, index === 2 ? company.id : undefined, false);
      computers.push(computer);
      await markAccountComputer(request, computer.id);
    }
    await startSession(page, account);
    await page.setViewportSize({ width: 414, height: 896 });
    await page.goto("compute/computers");
    const target = page.getByRole("link", { name: company.name, exact: true });
    await target.evaluate((element) => element.scrollIntoView({ block: "center" }));
    const box = (await target.boundingBox())!;
    const touch = await context.newCDPSession(page);
    try {
      await touch.send("Input.dispatchTouchEvent", {
        type: "touchStart", touchPoints: [{ x: box.x + box.width / 2, y: box.y + box.height / 2 }],
      });
      if (mutation === "move the pressed computer") {
        expect((await request.patch(`${API}/connected-computers/${computers[1].id}/pool`, {
          headers: headers(account), data: { company_id: null, expected_company_id: company.id },
        })).status()).toBe(200);
      } else {
        const removed = computers[mutation === "remove a preceding row" ? 0 : 1];
        expect((await request.delete(`${API}/computers/${removed.id}`, { headers: headers(account) })).status()).toBe(204);
      }
      const refreshed = page.waitForResponse((response) =>
        response.url() === `${API}/connected-computers?limit=10&offset=0` && response.status() === 200);
      await page.evaluate(() => window.dispatchEvent(new Event("online")));
      await (await refreshed).finished();
      if (mutation !== "remove a preceding row") await expect(target).toBeHidden();
      await touch.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
      if (mutation === "remove a preceding row") await expect(page).toHaveURL(new RegExp(`/organizations/${company.id}$`));
      else await expect(page).toHaveURL(/\/compute\/computers$/);
    } finally {
      await touch.detach();
    }
  });
}

for (const scope of ["personal", "organization"] as const) {
  for (const lookup of [
    "on successful lookup", "after retrying a temporary lookup error", "after distinct retries for lookup and overview",
    "when the overview recovers during the retry tap",
  ] as const) {
    test(`a tap opens a computer displaced from the ${scope} preview by a new computer ${lookup}`, async ({
      page, request, account, context, browserName, hasTouch,
    }) => {
      test.skip(browserName !== "chromium" || !hasTouch, "Use native Chromium touch events");
      const organization = scope === "organization";
      const company = await createCompany(request, account, "Команда с новым компьютером");
      const previous = organization ? null : company.id;
      const destination = organization ? company.id : null;
      const computers: { id: string }[] = [];
      for (let index = 1; index <= 4; index++) {
        const computer = await addComputer(request, account, `Mac ${index}`, previous ?? undefined, false);
        computers.push(computer);
        await markAccountComputer(request, computer.id);
      }
      computers.sort((first, second) => first.id.localeCompare(second.id));
      const move = async (id: string) => {
        expect((await request.patch(`${API}/connected-computers/${id}/pool`, {
          headers: headers(account), data: { company_id: destination, expected_company_id: previous },
        })).status()).toBe(200);
      };
      for (const computer of computers.slice(1)) await move(computer.id);
      await startSession(page, account);
      await page.setViewportSize({ width: 414, height: 896 });
      await page.goto("compute/computers");
      await page.getByRole("link", { name: organization ? company.name : "Личный пул", exact: true }).first().click();
      const cards = page.locator(".organization-computer-card");
      await expect(cards).toHaveCount(3);
      const target = cards.last();
      const href = (await target.getAttribute("href"))!;
      expect(href).toContain(computers[3].id);
      await target.evaluate((element) => element.scrollIntoView({ block: "center" }));
      const box = (await target.boundingBox())!;
      const touch = await context.newCDPSession(page);
      try {
        await touch.send("Input.dispatchTouchEvent", {
          type: "touchStart", touchPoints: [{ x: box.x + box.width / 2, y: box.y + box.height / 2 }],
        });
        await move(computers[0].id);
        await page.evaluate(() => window.dispatchEvent(new Event("online")));
        await expect(page.locator(".organization-overview dd").first()).toHaveText("4");
        const lookupRequest = (url: URL) => url.origin + url.pathname === `${API}/computers` &&
          url.searchParams.get("computer_id") === computers[3].id;
        if (lookup !== "on successful lookup") {
          const origin = new URL(page.url()).origin;
          await page.route(lookupRequest, (route) => route.fulfill({
            status: 503,
            headers: { "access-control-allow-origin": origin, "access-control-allow-credentials": "true" },
            json: { detail: "Temporary outage" },
          }));
        }
        await touch.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
        if (lookup !== "on successful lookup") {
          const inventory = page.getByRole("region", { name: "Компьютеры", exact: true });
          const retry = inventory.getByRole("button", { name: "Повторить открытие компьютера ↻", exact: true });
          await expect(retry).toBeVisible();
          const pool = organization ? `companies/${company.id}` : `users/${account.id}`;
          const endpoint = `${API}/${pool}/resources/pool-overview`;
          if (lookup === "after distinct retries for lookup and overview" || lookup === "when the overview recovers during the retry tap") {
            await temporaryReadFailure(page, endpoint);
            await page.evaluate(() => window.dispatchEvent(new Event("online")));
            await expect(inventory.getByRole("button", { name: "Попробовать снова ↻", exact: true })).toBeVisible();
          }
          await expect(retry).toHaveCount(1);
          await page.unroute(lookupRequest);
          if (lookup === "when the overview recovers during the retry tap") {
            await retry.evaluate((element) => element.scrollIntoView({ block: "center" }));
            const retryBox = (await retry.boundingBox())!;
            await touch.send("Input.dispatchTouchEvent", {
              type: "touchStart", touchPoints: [{ x: retryBox.x + retryBox.width / 2, y: retryBox.y + retryBox.height / 2 }],
            });
            await page.unroute(endpoint);
            const recovered = page.waitForResponse((response) => response.url() === endpoint && response.status() === 200);
            await page.evaluate(() => window.dispatchEvent(new Event("online")));
            await (await recovered).finished();
            await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
            await touch.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
          } else await retry.press("Enter");
        }
        await expect(page).toHaveURL(new URL(href, page.url()).href);
      } finally {
        await touch.detach();
      }
    });
  }

  for (const selected of ["card", "full inventory link"] as const) {
    test(`a temporary overview error preserves a tap already on the ${scope} ${selected}`, async ({
      page, request, account, browserName, hasTouch,
    }) => {
      test.skip(browserName !== "chromium" || !hasTouch, "Use native Chromium touch events");
      const company = scope === "organization" ? await createCompany(request, account, "Пул с ошибкой сводки") : null;
      for (let index = 1; index <= 3; index++) {
        const computer = await addComputer(request, account, `Mac ${index}`, company?.id);
        await markAccountComputer(request, computer.id);
      }
      await startSession(page, account);
      await page.setViewportSize({ width: 414, height: 896 });
      await page.goto("compute/computers");
      await page.getByRole("link", { name: company?.name ?? "Личный пул", exact: true }).first().click();
      const cards = page.locator(".organization-computer-card");
      await expect(cards).toHaveCount(3);
      const control = selected === "card" ? cards.nth(1) : page.getByRole("link", { name: "Список компьютеров", exact: true });
      const href = (await control.getAttribute("href"))!;
      await control.evaluate((element) => element.scrollIntoView({ block: "center" }));
      const box = (await control.boundingBox())!;
      const touch = await page.context().newCDPSession(page);
      try {
        await touch.send("Input.dispatchTouchEvent", {
          type: "touchStart", touchPoints: [{ x: box.x + box.width / 2, y: box.y + box.height / 2 }],
        });
        const pool = company ? `companies/${company.id}` : `users/${account.id}`;
        const endpoint = `${API}/${pool}/resources/pool-overview`;
        await temporaryReadFailure(page, endpoint);
        const failed = page.waitForResponse((response) => response.url() === endpoint && response.status() === 503);
        await page.evaluate(() => window.dispatchEvent(new Event("online")));
        await failed;
        await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
        await touch.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
        await expect(page).toHaveURL(new URL(href, page.url()).href);
      } finally {
        await touch.detach();
      }
    });
  }

  test(`a temporary history error preserves a tap already on the ${scope} chart`, async ({
    page, request, account, context, browserName, hasTouch,
  }) => {
    test.skip(browserName !== "chromium" || !hasTouch, "Use native Chromium touch events");
    const company = scope === "organization" ? await createCompany(request, account, "Пул с ошибкой графика") : null;
    const computer = await addComputer(request, account, "Mac с историей", company?.id);
    await markAccountComputer(request, computer.id);
    await startSession(page, account);
    await page.setViewportSize({ width: 414, height: 896 });
    await page.goto("compute/computers");
    await page.getByRole("link", { name: company?.name ?? "Личный пул", exact: true }).click();
    const widget = company ? page.locator(".organization-history").last() : page.locator(".personal-pool-history");
    const target = company ? widget.locator(".organization-resource-chart") : widget.getByRole("button", { name: "CPU · мощность", exact: true });
    await target.evaluate((element) => element.scrollIntoView({ block: "center" }));
    const endpoint = company ? `${API}/companies/${company.id}/resources/pool-history`
      : `${API}/users/${account.id}/resources/availability-history`;
    const failed = page.waitForResponse((response) => response.url() === endpoint && response.status() === 503);
    const box = (await target.boundingBox())!;
    const touch = await context.newCDPSession(page);
    try {
      await touch.send("Input.dispatchTouchEvent", {
        type: "touchStart", touchPoints: [{ x: box.x + box.width / 2, y: box.y + box.height / 2 }],
      });
      await temporaryReadFailure(page, endpoint);
      await page.evaluate(() => window.dispatchEvent(new Event("online")));
      await failed;
      await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
      await touch.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
      if (!company) await expect(target).toHaveAttribute("aria-pressed", "false");
      await expect(widget.getByRole("button", { name: /Попробовать снова/ })).toBeVisible();
    } finally {
      await touch.detach();
    }
  });
}

for (const revoked of ["resource permission", "membership"] as const) {
  for (const order of ["an earlier summary error", "a later report outage"] as const) {
    test(`the catalog organization chart keeps inventory unavailable after ${revoked} is revoked with ${order}`, async ({
      page, request, account,
    }) => {
      const owner = await makeAccount(request);
      const company = await createCompany(request, owner, "Пул с отозванным правом");
      const membership = await addMember(request, owner, company.id, account, "user", ["resources.read", "members.read"]);
      await addComputer(request, owner, "Рабочий Mac", company.id);
      await startSession(page, account);
      await page.goto("compute/pools");
      await page.locator(`main a[href="/compute/pools/${company.id}"]`).click();
      const card = page.getByRole("region", { name: company.name, exact: true });
      await expect(card.getByTestId("computer-count")).toHaveText("1");
      const inventory = card.getByRole("link", { name: /Список компьютеров/ });
      await inventory.focus();
      await temporaryReadFailure(page, `${API}/companies/${company.id}`);
      if (revoked === "membership") {
        expect((await request.delete(`${API}/memberships/${membership}`, { headers: headers(owner) })).status()).toBe(204);
      } else {
        expect((await request.patch(`${API}/memberships/${membership}`, {
          headers: headers(owner), data: { permissions: ["members.read"] },
        })).status()).toBe(200);
      }
      const reports = `${API}/companies/${company.id}/resources`;
      const summary = `${reports}/summary?only_available=true`;
      const history = `${reports}/availability-history?*`;
      const reload = () => page.evaluate(() => window.dispatchEvent(new Event("online")));
      if (order === "an earlier summary error") {
        let releaseHistory!: () => void;
        const held = new Promise<void>((resolve) => { releaseHistory = resolve; });
        await temporaryReadFailure(page, summary);
        await page.route(history, async (route) => {
          await held;
          await route.continue();
        }, { times: 1 });
        try {
          const failed = page.waitForResponse((response) => response.url() === summary && response.status() === 503);
          await reload();
          await failed;
          await expect(card.getByRole("button", { name: /Попробовать снова/ })).toBeVisible();
        } finally {
          releaseHistory();
        }
        await expect(inventory).toHaveCount(0);
      } else {
        await reload();
        await expect(inventory).toHaveCount(0);
        await temporaryReadFailure(page, summary);
        await temporaryReadFailure(page, history);
        const failed = page.waitForResponse((response) => response.url() === summary && response.status() === 503);
        await reload();
        await failed;
        await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
        await expect(inventory).toHaveCount(0);
        await page.unroute(history);
      }
      await expect(page.getByRole("link", { name: /К списку пулов/ })).toBeFocused();
      await expect(card.getByTestId("computer-count")).toHaveCount(0);
      await page.unroute(summary);
      if (revoked === "membership") {
        await addMember(request, owner, company.id, account, "user", ["resources.read", "members.read"]);
      } else {
        expect((await request.patch(`${API}/memberships/${membership}`, {
          headers: headers(owner), data: { permissions: ["resources.read", "members.read"] },
        })).status()).toBe(200);
      }
      await card.getByRole("button", { name: /Попробовать снова/ }).press("Enter");
      await expect(inventory).toBeFocused();
    });
  }
}

test("a temporary connected-list error preserves a tap already on its pool link", async ({
  page, request, account, browserName, hasTouch,
}) => {
  test.skip(browserName !== "chromium" || !hasTouch, "Use native Chromium touch events");
  const company = await createCompany(request, account, "Команда при ошибке списка");
  const computer = await addComputer(request, account, "Mac с выбранным пулом", company.id);
  await markAccountComputer(request, computer.id);
  await startSession(page, account);
  await page.setViewportSize({ width: 414, height: 896 });
  await page.goto("compute/computers");
  const link = page.getByRole("link", { name: company.name, exact: true });
  await link.evaluate((element) => element.scrollIntoView({ block: "center" }));
  const box = (await link.boundingBox())!;
  const endpoint = `${API}/connected-computers?limit=10&offset=0`;
  const touch = await page.context().newCDPSession(page);
  try {
    await touch.send("Input.dispatchTouchEvent", {
      type: "touchStart", touchPoints: [{ x: box.x + box.width / 2, y: box.y + box.height / 2 }],
    });
    await temporaryReadFailure(page, endpoint);
    const failed = page.waitForResponse((response) => response.url() === endpoint && response.status() === 503);
    await page.evaluate(() => window.dispatchEvent(new Event("online")));
    await failed;
    await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
    await touch.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
    await expect(page).toHaveURL(new RegExp(`/organizations/${company.id}$`));
  } finally {
    await touch.detach();
  }
});

for (const outcome of ["successful transfer", "temporary confirmation error"] as const) {
  test(`feedback from an earlier ${outcome} preserves the current pool tap`, async ({
    page, request, account, browserName, hasTouch,
  }) => {
    test.skip(browserName !== "chromium" || !hasTouch, "Use native Chromium touch events");
    const destination = await createCompany(request, account, "Место переноса");
    const targetCompany = await createCompany(request, account, "Пул для перехода");
    const moving = await addComputer(request, account, "Mac в переносе");
    const other = await addComputer(request, account, "Mac для перехода", targetCompany.id);
    for (const computer of [moving, other]) await markAccountComputer(request, computer.id);
    await startSession(page, account);
    await page.setViewportSize({ width: 414, height: 896 });
    await page.goto("compute/computers");
    const endpoint = `${API}/connected-computers/${moving.id}/pool`;
    const reconcile = `${endpoint}/reconcile`;
    const origin = new URL(page.url()).origin;
    const fail = (route: import("@playwright/test").Route) => route.fulfill({
      status: 503,
      headers: { "access-control-allow-origin": origin, "access-control-allow-credentials": "true" },
      json: { detail: "Temporary outage" },
    });
    await page.route(endpoint, async (route) => {
      if (route.request().method() !== "PATCH") return route.continue();
      expect((await route.fetch()).status()).toBe(200);
      await route.abort("failed");
    });
    let confirming = false;
    let releaseConfirmation!: () => void;
    const held = new Promise<void>((resolve) => { releaseConfirmation = resolve; });
    await page.route(reconcile, async (route) => {
      if (!confirming) return fail(route);
      await held;
      if (outcome === "temporary confirmation error") return fail(route);
      return route.continue();
    });
    try {
      await page.getByRole("button", { name: "Сменить пул — Mac в переносе", exact: true }).click();
      const dialog = page.getByRole("dialog");
      await dialog.getByLabel("Новый пул").selectOption(destination.id);
      await dialog.getByRole("button", { name: "Сменить пул", exact: true }).click();
      await expect(dialog.getByRole("alert").getByRole("button")).toBeVisible();
      const initialCheck = page.waitForResponse((response) => response.url() === reconcile && response.status() === 503);
      await page.reload();
      await initialCheck;
      const confirm = page.getByRole("button", { name: "Проверить перенос — Mac в переносе", exact: true });
      await expect(confirm).toBeEnabled();
      confirming = true;
      await confirm.click();
      await expect(confirm).toBeDisabled();

      const link = page.getByRole("link", { name: targetCompany.name, exact: true });
      await link.evaluate((element) => element.scrollIntoView({ block: "center" }));
      const box = (await link.boundingBox())!;
      const touch = await page.context().newCDPSession(page);
      try {
        await touch.send("Input.dispatchTouchEvent", {
          type: "touchStart", touchPoints: [{ x: box.x + box.width / 2, y: box.y + box.height / 2 }],
        });
        const completed = page.waitForResponse((response) => response.url() === reconcile &&
          response.status() === (outcome === "successful transfer" ? 200 : 503));
        releaseConfirmation();
        await completed;
        if (outcome === "successful transfer") await expect(confirm).toHaveCount(0);
        else await expect(confirm).toBeEnabled();
        await touch.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
        await expect(page).toHaveURL(new RegExp(`/organizations/${targetCompany.id}$`));
      } finally {
        await touch.detach();
      }
    } finally {
      releaseConfirmation();
    }
  });
}

test("deleting the focused computer during a press restores focus to the list heading", async ({ page, request, account }) => {
  const company = await createCompany(request, account, "Команда удалённого компьютера");
  const remaining = await addComputer(request, account, "Mac остаётся");
  const computer = await addComputer(request, account, "Mac удаляется", company.id);
  await markAccountComputer(request, remaining.id);
  await markAccountComputer(request, computer.id);
  await startSession(page, account);
  await page.goto("compute/computers");
  const link = page.getByRole("link", { name: company.name, exact: true });
  await link.focus();
  const box = (await link.boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  expect((await request.delete(`${API}/computers/${computer.id}`, { headers: headers(account) })).status()).toBe(204);
  await page.evaluate(() => window.dispatchEvent(new Event("online")));
  await expect(link).toBeHidden();
  await page.mouse.up();
  await expect(page).toHaveURL(/\/compute\/computers$/);
  await expect(page.getByRole("heading", { name: "Подключённые компьютеры." })).toBeFocused();
});

test("a background organization details error preserves a press on the history chart", async ({
  page, request, account, browserName,
}) => {
  test.skip(browserName !== "webkit", "Exercise WebKit scroll anchoring");
  const company = await createCompany(request, account, "Команда с временной ошибкой");
  const computer = await addComputer(request, account, "Mac с историей", company.id);
  await markAccountComputer(request, computer.id);
  await startSession(page, account);
  await page.setViewportSize({ width: 414, height: 896 });
  await page.goto("compute/computers");
  await page.getByRole("link", { name: company.name, exact: true }).click();
  const widget = page.getByRole("region", { name: "История CPU", exact: true });
  const toggle = widget.locator(".organization-resource-chart");
  await toggle.evaluate((element) => element.scrollIntoView({ block: "center" }));
  const box = (await toggle.boundingBox())!;
  const endpoint = `${API}/companies/${company.id}`;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await temporaryReadFailure(page, endpoint);
  await page.evaluate(() => window.dispatchEvent(new Event("online")));
  await expect(page.getByRole("button", { name: /Попробовать снова/ })).toBeVisible();
  await page.mouse.up();
});

test("the full inventory keeps the directory return link after membership loss and a later 503", async ({
  page, request, account,
}) => {
  const owner = await makeAccount(request);
  const company = await createCompany(request, owner, "Команда с инвентарём");
  const member = await addMember(request, owner, company.id, account, "user", ["resources.read", "resources.manage"]);
  const computer = await addComputer(request, account, "Командный Mac", company.id);
  await markAccountComputer(request, computer.id);
  await startSession(page, account);
  await page.goto("compute/computers");
  await page.getByRole("link", { name: company.name, exact: true }).click();
  await page.getByRole("link", { name: "Список компьютеров", exact: true }).click();
  const organizationBack = page.getByRole("link", { name: "← К организации", exact: true });
  await expect(organizationBack).toHaveAttribute("href", `/organizations/${company.id}`);
  const row = page.getByRole("row").filter({ hasText: computer.name });
  await expect(row).toBeVisible();
  expect((await request.delete(`${API}/memberships/${member}`, { headers: headers(owner) })).status()).toBe(204);
  await page.evaluate(() => window.dispatchEvent(new Event("online")));
  const directoryBack = page.getByRole("link", { name: "← К организациям", exact: true });
  await expect(directoryBack).toHaveAttribute("href", "/organizations");
  await expect(row).toHaveCount(0);

  const endpoint = `${API}/computers?company_id=${company.id}&only_available=false&limit=10&offset=0`;
  await temporaryReadFailure(page, endpoint);
  const outage = page.waitForResponse((response) => response.url() === endpoint && response.status() === 503);
  await page.evaluate(() => window.dispatchEvent(new Event("online")));
  await outage;
  await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
  await expect(directoryBack).toHaveAttribute("href", "/organizations");
  await expect(organizationBack).toHaveCount(0);

  await page.unroute(endpoint);
  await addMember(request, owner, company.id, account, "user", ["members.read"]);
  const forbidden = page.waitForResponse((response) => response.url() === endpoint && response.status() === 403);
  await page.evaluate(() => window.dispatchEvent(new Event("online")));
  await forbidden;
  await expect(organizationBack).toHaveAttribute("href", `/organizations/${company.id}`);
  await expect(row).toHaveCount(0);
});

for (const timing of ["during an outage", "after rejoining", "after losing resource read"] as const) {
  test(`the inventory applies a confirmed exit only to the membership it verified ${timing}`, async ({
    page, request, account, context,
  }) => {
    const rejoined = timing === "after rejoining";
    const owner = await makeAccount(request);
    const company = await createCompany(request, owner, "Команда в двух вкладках");
    const membership = await addMember(request, owner, company.id, account, "user", ["resources.read", "resources.manage"]);
    const computer = await addComputer(request, account, "Командный Mac", company.id);
    await markAccountComputer(request, computer.id);
    await startSession(page, account);
    await page.goto("compute/computers");
    await page.getByRole("link", { name: company.name, exact: true }).click();
    await page.getByRole("link", { name: "Список компьютеров", exact: true }).click();
    const row = page.getByRole("row").filter({ hasText: computer.name });
    await expect(row).toBeVisible();
    const path = `${API}/computers?company_id=${company.id}&only_available=false&limit=10&offset=0`;
    if (timing === "after losing resource read") {
      expect((await request.patch(`${API}/memberships/${membership}`, {
        headers: headers(owner), data: { permissions: ["members.read"] },
      })).status()).toBe(200);
      const forbidden = page.waitForResponse((response) => response.url() === path && response.status() === 403);
      await page.evaluate(() => window.dispatchEvent(new Event("online")));
      expect((await forbidden).headers()["x-organization-membership-id"]).toBe(membership);
      await expect(row).toHaveCount(0);
    }
    const other = await context.newPage();
    let releaseExit!: () => void;
    const held = new Promise<void>((resolve) => { releaseExit = resolve; });
    let markRemoved!: () => void;
    const removed = new Promise<void>((resolve) => { markRemoved = resolve; });
    try {
      await other.goto(`organizations/${company.id}`);
      const settings = await openOrganizationSettings(other);
      await settings.getByRole("button", { name: "Выйти из организации", exact: true }).click();
      const dialog = other.getByRole("dialog", { name: "Подтвердите выход", exact: true });
      await dialog.getByRole("textbox").fill(account.first_name);
      if (rejoined) {
        await other.route(`${API}/memberships/${membership}`, async (route) => {
          if (route.request().method() !== "DELETE") return route.continue();
          const result = await route.fetch();
          expect(result.status()).toBe(204);
          markRemoved();
          await held;
          await route.fulfill({ response: result });
        });
      } else {
        await temporaryReadFailure(page, path);
      }
      const waitForOutage = () => page.waitForResponse((response) => response.url() === path && response.status() === 503);
      const immediateOutage = rejoined ? null : waitForOutage();
      await dialog.getByRole("button", { name: "Подтвердить", exact: true }).click();
      if (rejoined) {
        await removed;
        const current = await addMember(request, owner, company.id, account, "user", ["resources.read", "resources.manage"]);
        const verified = page.waitForResponse((response) => response.url() === path &&
          response.status() === 200 && response.headers()["x-organization-membership-id"] === current);
        await page.evaluate(() => window.dispatchEvent(new Event("online")));
        await (await verified).finished();
        await expect(row).toBeVisible();
        await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
        await temporaryReadFailure(page, path);
      }
      const outage = immediateOutage ?? waitForOutage();
      releaseExit();
      await expect(other).toHaveURL(/\/organizations$/);
      await (await outage).finished();
      await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
      if (rejoined) {
        await expect(row).toBeVisible();
        await expect(page.getByRole("link", { name: "← К организации", exact: true })).toHaveAttribute("href", `/organizations/${company.id}`);
      } else {
        await expect(row).toHaveCount(0);
        await expect(page.getByRole("link", { name: "← К организациям", exact: true })).toHaveAttribute("href", "/organizations");
      }
    } finally {
      releaseExit();
      await other.close();
    }
  });
}

test("renaming the opened organization preserves a tap on its full inventory link", async ({
  page, request, account, browserName, hasTouch,
}) => {
  test.skip(browserName !== "chromium" || !hasTouch, "Use native Chromium touch events");
  const company = await createCompany(request, account, "Пул");
  for (let index = 1; index <= 3; index++) {
    const computer = await addComputer(request, account, `Mac ${index}`, company.id);
    await markAccountComputer(request, computer.id);
  }
  await startSession(page, account);
  await page.setViewportSize({ width: 414, height: 896 });
  await page.goto("compute/computers");
  await page.getByRole("link", { name: company.name, exact: true }).first().click();
  await expect(page.locator(".organization-computer-card")).toHaveCount(3);
  const inventory = page.getByRole("link", { name: "Список компьютеров", exact: true });
  const href = (await inventory.getAttribute("href"))!;
  await inventory.evaluate((element) => element.scrollIntoView({ block: "center" }));
  const box = (await inventory.boundingBox())!;
  const touch = await page.context().newCDPSession(page);
  try {
    await touch.send("Input.dispatchTouchEvent", {
      type: "touchStart", touchPoints: [{ x: box.x + box.width / 2, y: box.y + box.height / 2 }],
    });
    const name = "Инфраструктура и системы распределённых вычислений";
    expect((await request.patch(`${API}/companies/${company.id}`, {
      headers: headers(account), data: { name },
    })).status()).toBe(200);
    await page.evaluate(() => window.dispatchEvent(new Event("online")));
    await expect(page.getByRole("heading", { name: `${name}.`, exact: true })).toBeVisible();
    await touch.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
    await expect(page).toHaveURL(new URL(href, page.url()).href);
  } finally {
    await touch.detach();
  }
});

test("another tab's confirmed rename preserves a computer card press while metadata reloads", async ({
  page, request, account, context, browserName, hasTouch,
}) => {
  test.skip(browserName !== "chromium" || !hasTouch, "Use native Chromium touch events");
  const company = await createCompany(request, account, "Сборочная команда");
  for (let index = 1; index <= 3; index++) {
    const computer = await addComputer(request, account, `Mac ${index}`, company.id);
    await markAccountComputer(request, computer.id);
  }
  await startSession(page, account);
  await page.setViewportSize({ width: 414, height: 896 });
  await page.goto("compute/computers");
  await page.getByRole("link", { name: company.name, exact: true }).first().click();
  const cards = page.locator(".organization-computer-card");
  await expect(cards).toHaveCount(3);
  const editor = await context.newPage();
  const endpoint = `${API}/companies/${company.id}`;
  const rename = await pauseMutation(editor, endpoint, { method: "PATCH", pause: "response" });
  let releaseMetadata!: () => void;
  const held = new Promise<void>((resolve) => { releaseMetadata = resolve; });
  try {
    await editor.goto(`organizations/${company.id}`);
    const settings = await openOrganizationSettings(editor);
    const name = settings.getByLabel("Название организации", { exact: true });
    await name.fill("Сборочная команда с новым названием");
    await name.press("Enter");
    await rename.pending;
    await page.bringToFront();
    const card = cards.nth(1);
    const href = (await card.getAttribute("href"))!;
    await card.evaluate((element) => element.scrollIntoView({ block: "center" }));
    const box = (await card.boundingBox())!;
    const touch = await context.newCDPSession(page);
    try {
      await touch.send("Input.dispatchTouchEvent", {
        type: "touchStart", touchPoints: [{ x: box.x + box.width / 2, y: box.y + box.height / 2 }],
      });
      await page.route(endpoint, async (route) => {
        await held;
        await route.continue();
      });
      const reloading = page.waitForRequest((pending) => pending.url() === endpoint && pending.method() === "GET");
      rename.release();
      await reloading;
      await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
      await expect(cards).toHaveCount(3);
      await touch.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
      releaseMetadata();
      await expect(page).toHaveURL(new URL(href, page.url()).href);
    } finally {
      await touch.detach();
    }
  } finally {
    rename.release();
    releaseMetadata();
    await editor.close();
  }
});

test("renaming the organization during a press on its settings button still opens the dialog", async ({
  page, request, account, browserName, hasTouch,
}) => {
  test.skip(browserName !== "chromium" || !hasTouch, "Use native Chromium touch events");
  const company = await createCompany(request, account, "Пул");
  const computer = await addComputer(request, account, "Командный Mac", company.id);
  await markAccountComputer(request, computer.id);
  await startSession(page, account);
  await page.setViewportSize({ width: 414, height: 896 });
  await page.goto("compute/computers");
  await page.getByRole("link", { name: company.name, exact: true }).click();
  const settings = page.getByRole("button", { name: "Настройки организации", exact: true });
  await settings.evaluate((element) => element.scrollIntoView({ block: "center" }));
  const box = (await settings.boundingBox())!;
  const touch = await page.context().newCDPSession(page);
  try {
    await touch.send("Input.dispatchTouchEvent", {
      type: "touchStart", touchPoints: [{ x: box.x + box.width / 2, y: box.y + box.height / 2 }],
    });
    const name = "Инфраструктура и системы распределённых вычислений";
    const endpoint = `${API}/companies/${company.id}`;
    expect((await request.patch(endpoint, { headers: headers(account), data: { name } })).status()).toBe(200);
    const loaded = page.waitForResponse((response) => response.url() === endpoint && response.status() === 200);
    await page.evaluate(() => window.dispatchEvent(new Event("online")));
    await (await loaded).finished();
    await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
    await touch.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
    await expect(page.getByRole("dialog")).toBeVisible();
    await expect(page.locator(".organization-hero h1")).toHaveAttribute("aria-label", `${name}.`);
  } finally {
    await touch.detach();
  }
});

for (const { personal, outage, leavePrevious, canRead = true } of [
  { personal: true, outage: false, leavePrevious: false },
  { personal: false, outage: false, leavePrevious: false },
  { personal: true, outage: true, leavePrevious: false },
  { personal: false, outage: true, leavePrevious: false },
  { personal: false, outage: true, leavePrevious: true },
  { personal: true, outage: true, leavePrevious: false, canRead: false },
]) {
  const description = canRead
    ? `tracks exit from its ${leavePrevious ? "former" : "current"} pool after transfer from ${personal ? "personal" : "organization"}${outage ? " during a detail outage" : ""}`
    : "hides after a transfer to a pool without read permission during a detail outage";
  test(`a computer detail ${description}`, async ({
    page, request, account, context,
  }) => {
    const owner = await makeAccount(request);
    const destination = await createCompany(request, owner, "Текущий пул компьютера");
    const membership = await addMember(request, owner, destination.id, account, "user",
      canRead ? ["resources.read", "resources.manage"] : ["resources.manage"]);
    const source = personal ? null : await createCompany(request, owner, "Исходный пул компьютера");
    if (source) await addMember(request, owner, source.id, account, "user", ["resources.read", "resources.manage"]);
    const computer = await addComputer(request, account, "Mac после переноса", source?.id);
    await markAccountComputer(request, computer.id);
    await startSession(page, account);
    await page.goto("compute/computers");
    await page.getByRole("link", { name: source?.name ?? "Личный пул", exact: true }).click();
    await page.locator("a.organization-computer-card").click();
    const title = page.getByRole("heading", { name: computer.name });
    await expect(title).toBeVisible();
    const uri = `${API}/computers/${computer.id}`;
    const secondTab = await context.newPage();
    try {
      await secondTab.goto("compute/computers");
      const row = secondTab.getByRole("listitem").filter({ hasText: computer.name });
      await row.getByRole("button", { name: /Сменить пул/ }).click();
      const mover = secondTab.getByRole("dialog", { name: "Сменить пул" });
      await mover.getByLabel("Новый пул").selectOption(destination.id);
      if (outage) await temporaryReadFailure(page, uri);
      const refreshed = page.waitForResponse((response) => response.url() === uri &&
        (outage ? response.status() === 503 :
          response.status() === 200 && response.headers()["x-organization-membership-id"] === membership));
      await mover.getByRole("button", { name: "Сменить пул", exact: true }).click();
      await expect(row.getByRole("link", { name: destination.name, exact: true })).toBeVisible();
      await (await refreshed).finished();
      if (!canRead) {
        expect((await request.get(uri, { headers: headers(account) })).status()).toBe(403);
        await expect(title).toHaveCount(0);
        return;
      }
      await expect(title).toBeVisible();
      if (!outage) await temporaryReadFailure(page, uri);
      await secondTab.goto(`organizations/${(leavePrevious ? source! : destination).id}`);
      const settings = await openOrganizationSettings(secondTab);
      await settings.getByRole("button", { name: "Выйти из организации", exact: true }).click();
      const dialog = secondTab.getByRole("dialog", { name: "Подтвердите выход", exact: true });
      await dialog.getByRole("textbox").fill(account.first_name);
      await dialog.getByRole("button", { name: "Подтвердить", exact: true }).click();
      await expect(secondTab).toHaveURL(/\/organizations$/);
      const actual = await request.get(uri, { headers: headers(account) });
      expect(actual.status()).toBe(leavePrevious ? 200 : 404);
      if (leavePrevious) {
        expect(actual.headers()["x-organization-membership-id"]).toBe(membership);
        await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
        await expect(title).toBeVisible();
      } else await expect(title).toHaveCount(0);
    } finally {
      await secondTab.close();
    }
  });
}

for (const scenario of ["membership lost", "membership renewed", "an older transfer receipt"] as const) {
  test(`computer details follow current access when confirmation arrives after ${scenario}`, async ({
    page, request, account, context,
  }) => {
    const owner = await makeAccount(request);
    const company = await createCompany(request, owner, "Пул с обновлённым доступом");
    const permissions = ["resources.manage", "resources.read"];
    const previous = await addMember(request, owner, company.id, account, "user", permissions);
    const computer = await addComputer(request, account, "Mac с задержанным переносом");
    await markAccountComputer(request, computer.id);
    await startSession(page, account);
    await page.goto("compute/computers");
    await page.getByRole("link", { name: "Личный пул", exact: true }).click();
    await page.locator("a.organization-computer-card").click();
    const title = page.getByRole("heading", { name: computer.name });
    await expect(title).toBeVisible();
    const detail = `${API}/computers/${computer.id}`;
    const mutation = `${API}/connected-computers/${computer.id}/pool`;
    const keepsAccess = scenario !== "membership lost";
    const originalReceipt = scenario === "an older transfer receipt";
    const origin = new URL(page.url()).origin;
    const refreshDetails = async (membership: string) => {
      const pending = page.waitForResponse((response) => response.url() === detail && response.status() === 200 &&
        response.headers()["x-organization-membership-id"] === membership);
      await page.evaluate(() => window.dispatchEvent(new Event("online")));
      await (await pending).finished();
      await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
    };
    const mover = await context.newPage();
    const leaving = await context.newPage();
    const leave = await pauseMutation(leaving, `${API}/memberships/${previous}`, { pause: "response" });
    let releaseTransfer!: () => void;
    let committedTransfer!: () => void;
    const held = new Promise<void>((resolve) => { releaseTransfer = resolve; });
    const committed = new Promise<void>((resolve) => { committedTransfer = resolve; });
    try {
      await mover.route(mutation, async (route) => {
        if (route.request().method() !== "PATCH") return route.continue();
        const response = await route.fetch();
        expect(response.status()).toBe(200);
        committedTransfer();
        await held;
        if (originalReceipt) await route.fulfill({ response });
        else await route.fulfill({
          status: 503,
          headers: { "access-control-allow-origin": origin, "access-control-allow-credentials": "true" },
          json: { detail: "Lost transfer response" },
        });
      });
      await mover.goto("compute/computers");
      await mover.getByRole("button", { name: /Сменить пул/ }).click();
      const transferDialog = mover.getByRole("dialog", { name: "Сменить пул" });
      await transferDialog.getByLabel("Новый пул").selectOption(company.id);
      await transferDialog.getByRole("button", { name: "Сменить пул", exact: true }).click();
      await committed;
      if (keepsAccess) await refreshDetails(previous);

      await leaving.goto(`organizations/${company.id}`);
      const settings = await openOrganizationSettings(leaving);
      await settings.getByRole("button", { name: "Выйти из организации", exact: true }).click();
      const leaveDialog = leaving.getByRole("dialog", { name: "Подтвердите выход", exact: true });
      await leaveDialog.getByRole("textbox").fill(account.first_name);
      await leaveDialog.getByRole("button", { name: "Подтвердить", exact: true }).click();
      await leave.pending;
      const current = keepsAccess ? await addMember(request, owner, company.id, account, "user", permissions) : undefined;
      if (originalReceipt) await refreshDetails(current!);
      await temporaryReadFailure(page, detail);
      const refreshed = page.waitForResponse((response) => response.url() === detail && response.status() === 503);
      const checked = mover.waitForResponse((response) =>
        response.url() === (originalReceipt ? mutation : `${mutation}/reconcile`) && response.status() === 200);
      releaseTransfer();
      const receipt = await checked;
      if (!originalReceipt) expect(await receipt.json()).toBe(true);
      await (await refreshed).finished();
      const actual = await request.get(detail, { headers: headers(account) });
      expect(actual.status()).toBe(keepsAccess ? 200 : 404);
      if (keepsAccess) {
        expect(actual.headers()["x-organization-membership-id"]).toBe(current);
        const refreshedAfterExit = page.waitForResponse((response) => response.url() === detail && response.status() === 503);
        const exited = leaving.waitForResponse((response) => response.url() === `${API}/memberships/${previous}` &&
          response.request().method() === "DELETE" && response.status() === 204);
        leave.release();
        await exited;
        await (await refreshedAfterExit).finished();
        await expect(title).toBeVisible();
      } else {
        await expect(title).toHaveCount(0);
        expect(receipt.headers()["x-organization-id"]).toBeUndefined();
        expect(receipt.headers()["x-computer-read-allowed"]).toBe("false");
      }
    } finally {
      releaseTransfer();
      leave.release();
      await mover.close();
      await leaving.close();
    }
  });
}

for (const { created, revoke } of [
  { created: false, revoke: true },
  { created: true, revoke: true },
  { created: false, revoke: false },
]) {
  test(`a late organization rename checks an open ${created ? "created" : "renewed"} invitation link (${revoke ? "revoked" : "still valid"})`, async ({
    page, request, account,
  }) => {
    const owner = await makeAccount(request);
    const invitee = await makeAccount(request);
    const company = await createCompany(request, owner, "Пул с приглашениями");
    const permissions = ["company.manage", "members.read", "resources.read", "resources.manage"];
    const membership = await addMember(request, owner, company.id, account, "user", [...permissions, "members.manage"]);
    const computer = await addComputer(request, account, "Mac в пуле с приглашениями", company.id, false);
    await markAccountComputer(request, computer.id);
    let invitationId: string | undefined;
    if (!created) {
      const invitation = await request.post(`${API}/invitations`, {
        headers: headers(account),
        data: { company_id: company.id, identifier: invitee.email, role: "user", permissions: [] },
      });
      expect(invitation.status()).toBe(201);
      invitationId = (await invitation.json()).id;
    }
    await startSession(page, account);
    await page.goto("compute/computers");
    const pool = page.getByRole("link", { name: company.name, exact: true });
    await pool.click();
    const settings = await openOrganizationSettings(page);
    const endpoint = `${API}/companies/${company.id}`;
    const rename = await pauseMutation(page, endpoint, { method: "PATCH" });
    try {
      const name = settings.getByLabel("Название организации", { exact: true });
      await name.fill("Пул после переименования");
      await name.press("Enter");
      await rename.pending;
      await page.goBack();
      await expect(page).toHaveURL(/\/compute\/computers$/);
      await pool.click();
      await switchOrganizationPeople(page, "Приглашения");
      const receipt = page.waitForResponse((response) => response.request().method() === "POST" &&
        response.url() === (created ? `${API}/invitations` : `${API}/invitations/${invitationId}/link`));
      if (created) {
        const sharing = page.getByRole("region", { name: "Добавить участника", exact: true });
        await sharing.locator("summary").click();
        await sharing.getByLabel("Способ добавления").selectOption("link");
        await sharing.getByRole("button", { name: "Создать ссылку", exact: true }).click();
      } else {
        const row = page.getByRole("article", { name: invitee.email, exact: true });
        await row.locator(".organization-invitation-summary").click();
        await row.getByRole("button", { name: "Обновить ссылку", exact: true }).click();
      }
      const token = (await (await receipt).json()).token;
      expect(typeof token).toBe("string");
      const link = page.getByLabel("Ссылка приглашения", { exact: true });
      await expect(link).toBeVisible();
      if (revoke) {
        expect((await request.patch(`${API}/memberships/${membership}`, {
          headers: headers(owner), data: { permissions },
        })).status()).toBe(200);
      }
      const checked = await request.get(`${API}/invitations/link/${token}`, { headers: headers(invitee) });
      expect(checked.status()).toBe(revoke ? 410 : 200);
      await temporaryReadFailure(page, endpoint);
      const meta = page.waitForResponse((response) => response.url() === endpoint && response.status() === 503);
      const invitations = page.waitForResponse((response) =>
        response.url().startsWith(`${API}/invitations?company_id=${company.id}&include_counts=false`) &&
        response.status() === (revoke ? 403 : 200));
      rename.release();
      await (await meta).finished();
      await (await invitations).finished();
      if (revoke) await expect(link).toHaveCount(0);
      else await expect(link).toBeVisible();
    } finally {
      rename.release();
    }
  });
}

for (const { change, outage } of [
  { change: "renew same", outage: false },
  { change: "renew different", outage: false },
  { change: "renew same", outage: true },
  { change: "renew different", outage: true },
  { change: "revoke", outage: true },
]) {
  test(`an open invitation link follows a confirmed change in a second tab (${change}${outage ? " during a registry outage" : ""})`, async ({
    page, request, account, context,
  }) => {
    const company = await createCompany(request, account, "Пул со ссылками");
    const invitee = await makeAccount(request);
    const sameInvitation = change !== "renew different";
    const secondInvitee = sameInvitation ? invitee : await makeAccount(request);
    const recipients = sameInvitation ? [invitee] : [invitee, secondInvitee];
    const invitations = new Map<string, string>();
    for (const recipient of recipients) {
      const created = await request.post(`${API}/invitations`, {
        headers: headers(account),
        data: { company_id: company.id, identifier: recipient.email, role: "user" },
      });
      expect(created.status()).toBe(201);
      invitations.set(recipient.id, (await created.json()).id);
    }
    const computer = await addComputer(request, account, "Mac со ссылками", company.id);
    await markAccountComputer(request, computer.id);
    await startSession(page, account);
    const openInvitations = async (tab: Page) => {
      await tab.goto("compute/computers");
      await tab.getByRole("link", { name: company.name, exact: true }).click();
      await switchOrganizationPeople(tab, "Приглашения");
    };
    const renew = async (tab: Page, recipient: Account) => {
      const row = tab.getByRole("article", { name: recipient.email, exact: true });
      await row.locator(".organization-invitation-summary").click();
      const response = tab.waitForResponse((result) =>
        result.url() === `${API}/invitations/${invitations.get(recipient.id)}/link` && result.status() === 200);
      await row.getByRole("button", { name: "Обновить ссылку", exact: true }).click();
      return (await (await response).json()).token as string;
    };
    await openInvitations(page);
    const activeLink = page.getByLabel("Ссылка приглашения", { exact: true });
    const previous = await renew(page, invitee);
    await expect(activeLink).toBeVisible();
    const other = await context.newPage();
    try {
      await openInvitations(other);
      const registry = `${API}/invitations?company_id=${company.id}&include_counts=false`;
      if (outage) await temporaryReadFailure(page, `${registry}&*`);
      const checked = page.waitForResponse((response) =>
        response.url().startsWith(registry) && response.status() === (outage ? 503 : 200));
      if (change === "revoke") {
        const row = other.getByRole("article", { name: invitee.email, exact: true });
        await row.locator(".organization-invitation-summary").click();
        const revoked = other.waitForResponse((response) => response.url() === `${API}/invitations/${invitations.get(invitee.id)}` &&
          response.request().method() === "DELETE" && response.status() === 204);
        await row.getByRole("button", { name: "Отозвать", exact: true }).click();
        await revoked;
      } else {
        const current = await renew(other, secondInvitee);
        await expect(other.getByLabel("Ссылка приглашения", { exact: true })).toBeVisible();
        expect((await request.get(`${API}/invitations/link/${current}`, {
          headers: headers(secondInvitee),
        })).status()).toBe(200);
      }
      await (await checked).finished();
      expect((await request.get(`${API}/invitations/link/${previous}`, {
        headers: headers(invitee),
      })).status()).toBe(change === "revoke" ? 410 : sameInvitation ? 404 : 200);
      if (sameInvitation) await expect(activeLink).toHaveCount(0);
      else await expect(activeLink).toHaveValue(new RegExp(`${previous}$`));
    } finally {
      await other.close();
    }
  });
}

test("a new invitation link can be shared despite a registry outage after renaming", async ({ page, request, account, context }) => {
  const company = await createCompany(request, account, "Пул для нового приглашения");
  const computer = await addComputer(request, account, "Mac для нового приглашения", company.id);
  await markAccountComputer(request, computer.id);
  const invitee = await makeAccount(request);
  await startSession(page, account);
  await page.goto("compute/computers");
  await page.getByRole("link", { name: company.name, exact: true }).click();
  await switchOrganizationPeople(page, "Приглашения");
  const sharing = page.getByRole("region", { name: "Добавить участника", exact: true });
  await sharing.locator("summary").click();
  await sharing.getByLabel("Способ добавления").selectOption("link");
  const other = await context.newPage();
  try {
    await other.goto(`organizations/${company.id}`);
    const settings = await openOrganizationSettings(other);
    const registry = `${API}/invitations?company_id=${company.id}&include_counts=false`;
    await temporaryReadFailure(page, `${registry}&*`);
    const unavailable = page.waitForResponse((response) => response.url().startsWith(registry) && response.status() === 503);
    const name = settings.getByLabel("Название организации", { exact: true });
    await name.fill("Переименованный пул для приглашения");
    await name.press("Enter");
    await (await unavailable).finished();
    await expect(page.getByRole("heading", { name: "Переименованный пул для приглашения.", exact: true })).toBeVisible();
    const created = page.waitForResponse((response) => response.url() === `${API}/invitations` && response.status() === 201);
    await sharing.getByRole("button", { name: "Создать ссылку", exact: true }).click();
    const token = (await (await created).json()).token;
    expect((await request.get(`${API}/invitations/link/${token}`, { headers: headers(invitee) })).status()).toBe(200);
    await expect(sharing.getByLabel("Ссылка приглашения", { exact: true })).toBeVisible();
  } finally {
    await other.close();
  }
});

test("a late appearance save preserves a pressed computer card through canonical revalidation", async ({
  page, request, account, browserName, hasTouch,
}) => {
  test.skip(browserName !== "chromium" || !hasTouch, "Use native Chromium touch events");
  const company = await createCompany(request, account, "Команда с новым фоном");
  for (let index = 1; index <= 3; index++) {
    const computer = await addComputer(request, account, `Mac ${index}`, company.id);
    await markAccountComputer(request, computer.id);
  }
  await startSession(page, account);
  await page.setViewportSize({ width: 414, height: 896 });
  await page.goto("compute/computers");
  const link = page.getByRole("link", { name: company.name, exact: true }).first();
  await link.click();
  const settings = await openOrganizationSettings(page);
  const endpoint = `${API}/companies/${company.id}`;
  const upload = await pauseMutation(page, `${endpoint}/images/background`, { method: "PUT", pause: "response" });
  const png = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jWZkAAAAASUVORK5CYII=", "base64");
  let releaseMetadata!: () => void;
  const held = new Promise<void>((resolve) => { releaseMetadata = resolve; });
  try {
    await settings.getByLabel("Загрузить фон", { exact: true }).setInputFiles({ name: "background.png", mimeType: "image/png", buffer: png });
    await upload.pending;
    await page.goBack();
    await expect(page).toHaveURL(/\/compute\/computers$/);
    await link.click();
    const cards = page.locator(".organization-computer-card");
    await expect(cards).toHaveCount(3);
    const card = cards.nth(1);
    const href = (await card.getAttribute("href"))!;
    await card.evaluate((element) => element.scrollIntoView({ block: "center" }));
    const box = (await card.boundingBox())!;
    const touch = await page.context().newCDPSession(page);
    try {
      await touch.send("Input.dispatchTouchEvent", {
        type: "touchStart", touchPoints: [{ x: box.x + box.width / 2, y: box.y + box.height / 2 }],
      });
      await page.route(endpoint, async (route) => {
        await held;
        await route.continue().catch(() => {});
      });
      const reloading = page.waitForRequest((pending) => pending.url() === endpoint && pending.method() === "GET");
      upload.release();
      await reloading;
      await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
      await expect(cards).toHaveCount(3);
      await touch.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
      releaseMetadata();
      await expect(page).toHaveURL(new URL(href, page.url()).href);
    } finally {
      await touch.detach();
    }
  } finally {
    upload.release();
    releaseMetadata();
  }
});

for (const deletion of ["former pool", "current pool", "former pool after moving to personal"] as const) {
  test(`a computer detail uses its verified owner when deleting the ${deletion}`, async ({
    page, request, account, context,
  }) => {
    const source = await createCompany(request, account, "Исходная организация карточки");
    const personal = deletion === "former pool after moving to personal";
    const destination = personal ? null : await createCompany(request, account, "Текущая организация карточки");
    const computer = await addComputer(request, account, "Mac после смены владельца", source.id);
    await markAccountComputer(request, computer.id);
    await startSession(page, account);
    await page.goto("compute/computers");
    await page.getByRole("link", { name: source.name, exact: true }).click();
    await page.locator("a.organization-computer-card").click();
    const title = page.getByRole("heading", { name: computer.name });
    await expect(title).toBeVisible();
    expect((await request.patch(`${API}/connected-computers/${computer.id}/pool`, {
      headers: headers(account), data: { company_id: destination?.id ?? null, expected_company_id: source.id },
    })).status()).toBe(200);
    const uri = `${API}/computers/${computer.id}`;
    const verified = page.waitForResponse((response) => response.url() === uri && response.status() === 200 &&
      response.headers()["x-organization-id"] === destination?.id);
    await page.evaluate(() => window.dispatchEvent(new Event("online")));
    await (await verified).finished();
    await expect(title).toBeVisible();
    await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
    await temporaryReadFailure(page, uri);

    const deletingCurrent = deletion === "current pool";
    if (deletingCurrent) expect((await request.delete(uri, { headers: headers(account) })).status()).toBe(204);
    const company = deletingCurrent ? destination! : source;
    const other = await context.newPage();
    try {
      await other.goto(`organizations/${company.id}`);
      const settings = await openOrganizationSettings(other);
      await settings.getByRole("button", { name: "Удалить организацию", exact: true }).click();
      const dialog = other.getByRole("dialog", { name: "Удалить организацию", exact: true });
      await dialog.getByRole("textbox").fill(company.name);
      await dialog.getByRole("button", { name: "Подтвердить", exact: true }).click();
      await expect(other).toHaveURL(/\/organizations$/);
      expect((await request.get(uri, { headers: headers(account) })).status()).toBe(deletingCurrent ? 404 : 200);
      const failed = page.waitForResponse((response) => response.url() === uri && response.status() === 503);
      await page.evaluate(() => window.dispatchEvent(new Event("online")));
      await failed;
      await expect(page.getByRole("button", { name: /Попробовать снова/ })).toBeVisible();
      if (deletingCurrent) await expect(title).toHaveCount(0);
      else await expect(title).toBeVisible();
    } finally {
      await other.close();
    }
  });
}

for (const confirmation of ["a new transfer", "a fresh preview"] as const) test(`an organization keeps its verified pool after an older exit despite a metadata outage using ${confirmation}`, async ({
  page, request, account, context,
}) => {
  const owner = await makeAccount(request);
  const company = await createCompany(request, owner, "Организация после повторного вступления");
  const permissions = ["members.read", "resources.read", "resources.manage"];
  const previous = await addMember(request, owner, company.id, account, "user", permissions);
  const present = await addComputer(request, account, "Mac команды", company.id);
  const moving = await addComputer(request, account, "Mac нового членства");
  for (const device of [present, moving]) await markAccountComputer(request, device.id);
  await startSession(page, account);
  await page.goto("compute/computers");
  await page.getByRole("link", { name: company.name, exact: true }).click();
  const cards = page.locator("a.organization-computer-card");
  await expect(cards.filter({ hasText: present.name })).toBeVisible();
  const other = await context.newPage();
  const mover = await context.newPage();
  const exitEndpoint = `${API}/memberships/${previous}`;
  const exit = await pauseMutation(other, exitEndpoint, { pause: "response" });
  const metadata = `${API}/companies/${company.id}`;
  try {
    await other.goto(`organizations/${company.id}`);
    const settings = await openOrganizationSettings(other);
    await settings.getByRole("button", { name: "Выйти из организации", exact: true }).click();
    const confirm = other.getByRole("dialog", { name: "Подтвердите выход", exact: true });
    await confirm.getByRole("textbox").fill(account.first_name);
    await confirm.getByRole("button", { name: "Подтвердить", exact: true }).click();
    await exit.pending;
    const current = await addMember(request, owner, company.id, account, "user", permissions);
    expect(current).not.toBe(previous);
    await temporaryReadFailure(page, metadata);
    if (confirmation === "a new transfer") {
      await mover.goto("compute/computers");
      await mover.getByRole("button", { name: `Сменить пул — ${moving.name}`, exact: true }).click();
      const dialog = mover.getByRole("dialog", { name: "Сменить пул" });
      await dialog.getByLabel("Новый пул").selectOption(company.id);
      const receipt = mover.waitForResponse((response) => response.url() === `${API}/connected-computers/${moving.id}/pool` &&
        response.status() === 200);
      await dialog.getByRole("button", { name: "Сменить пул", exact: true }).click();
      expect((await receipt).headers()["x-organization-membership-id"]).toBe(current);
      await expect(cards.filter({ hasText: moving.name })).toBeVisible();
    } else {
      const renamed = "Mac после повторного вступления";
      const edited = await request.patch(`${API}/computers/${present.id}`, {
        headers: headers(account), data: { name: renamed },
      });
      expect(edited.status()).toBe(200);
      const overview = `${API}/companies/${company.id}/resources/pool-overview`;
      const latest = page.waitForResponse((response) => response.url() === overview && response.status() === 200);
      await page.evaluate(() => window.dispatchEvent(new Event("online")));
      expect((await latest).headers()["x-organization-membership-id"]).toBe(current);
      await expect(cards.filter({ hasText: renamed })).toBeVisible();
    }
    const recheck = page.waitForResponse((response) => response.url() === metadata && response.status() === 503);
    const confirmedExit = other.waitForResponse((response) =>
      response.url() === exitEndpoint && response.request().method() === "DELETE" && response.status() === 204);
    exit.release();
    await confirmedExit;
    await (await recheck).finished();
    const actual = await request.get(metadata, { headers: headers(account) });
    expect(actual.status()).toBe(200);
    expect((await actual.json()).my_membership_id).toBe(current);
    await expect(cards).toHaveCount(confirmation === "a new transfer" ? 2 : 1);
    await expect(page).toHaveURL(new RegExp(`/organizations/${company.id}$`));
  } finally {
    exit.release();
    await other.close();
    await mover.close();
  }
});

for (const view of ["team list", "catalog chart"] as const) {
  test(`the organization ${view} stays available after a delayed exit from an older membership`, async ({
    page, request, account, context,
  }) => {
    const owner = await makeAccount(request, "Владелец");
    const company = await createCompany(request, owner, "Пул с актуальным членством");
    const roster = view === "team list";
    const permissions = roster ? ["members.read", "resources.manage"] : ["members.read", "resources.manage", "resources.read"];
    const previous = await addMember(request, owner, company.id, account, "user", permissions);
    const computer = await addComputer(request, account, "Первый Mac команды", company.id);
    await markAccountComputer(request, computer.id);
    await startSession(page, account);
    if (roster) {
      await page.goto("compute/computers");
      await page.getByRole("link", { name: company.name, exact: true }).click();
      await expect(page.getByRole("article", { name: owner.first_name, exact: true })).toBeVisible();
    } else {
      await page.goto("compute/pools");
      await page.getByRole("link", { name: `Открыть пул ${company.name}`, exact: true }).click();
      await expect(page.getByTestId("computer-count")).toHaveText("1");
    }
    const metadata = `${API}/companies/${company.id}`;
    const data = roster ? `${API}/memberships?company_id=${company.id}`
      : `${API}/companies/${company.id}/resources/summary?only_available=true`;
    const other = await context.newPage();
    const previousExit = `${API}/memberships/${previous}`;
    const exit = await pauseMutation(other, previousExit, { pause: "response" });
    try {
      await other.goto(`organizations/${company.id}`);
      const settings = await openOrganizationSettings(other);
      await settings.getByRole("button", { name: "Выйти из организации", exact: true }).click();
      const confirm = other.getByRole("dialog", { name: "Подтвердите выход", exact: true });
      await confirm.getByRole("textbox").fill(account.first_name);
      await confirm.getByRole("button", { name: "Подтвердить", exact: true }).click();
      await exit.pending;
      const current = await addMember(request, owner, company.id, account, "user", permissions);
      await temporaryReadFailure(page, metadata);
      const colleague = roster ? await makeAccount(request, "Новый сотрудник") : null;
      if (colleague) await addMember(request, owner, company.id, colleague, "user", ["members.read"]);
      else await addComputer(request, account, "Второй Mac команды", company.id);
      const verified = page.waitForResponse((response) =>
        response.url().startsWith(data) && response.status() === 200 &&
        response.headers()["x-organization-membership-id"] === current);
      await page.evaluate(() => window.dispatchEvent(new Event("online")));
      await (await verified).finished();
      const granted = colleague ? page.getByRole("article", { name: colleague.first_name, exact: true }) : page.getByTestId("computer-count");
      if (colleague) await expect(granted).toBeVisible();
      else await expect(granted).toHaveText("2");
      const rechecked = page.waitForResponse((response) => response.url() === metadata && response.status() === 503);
      const departed = other.waitForResponse((response) => response.url() === previousExit && response.status() === 204);
      exit.release();
      await departed;
      await (await rechecked).finished();
      const actual = await request.get(data, { headers: headers(account) });
      expect(actual.status()).toBe(200);
      expect(actual.headers()["x-organization-membership-id"]).toBe(current);
      if (colleague) {
        await expect(granted).toBeVisible();
        await expect(page.getByText("У вас нет права просматривать мощности этой организации.")).toBeVisible();
      } else {
        await expect(granted).toHaveText("2");
        await expect(page.getByRole("link", { name: "Список компьютеров", exact: true })).toBeVisible();
      }
    } finally {
      exit.release();
      await other.close();
    }
  });
}

for (const { catalog, granted, rosterFirst = false } of [
  { catalog: false, granted: true },
  { catalog: false, granted: true, rosterFirst: true },
  { catalog: true, granted: true },
  { catalog: true, granted: false },
]) test(`a verified transfer updates resource reading during a metadata outage in the ${catalog ? "catalog chart" : "organization"} (read=${granted}${rosterFirst ? ", after a newer roster" : ""})`, async ({ page, request, account, context }) => {
  const owner = await makeAccount(request);
  const company = await createCompany(request, owner, "Пул с новым разрешением");
  const managerPermissions = ["resources.manage", ...(rosterFirst ? ["members.read"] : [])];
  const readPermissions = [...managerPermissions, "resources.read"];
  const membership = await addMember(request, owner, company.id, account, "user", granted ? managerPermissions : readPermissions);
  const organizationComputer = await addComputer(request, account, "Mac с изначально закрытым доступом", company.id);
  const computer = await addComputer(request, account, "Mac для нового доступа");
  for (const device of [organizationComputer, computer]) await markAccountComputer(request, device.id);
  await startSession(page, account);
  await page.goto(catalog ? "compute/pools" : "compute/computers");
  await page.getByRole("link", { name: catalog ? `Открыть пул ${company.name}` : company.name, exact: true }).click();
  const denied = page.getByText("У вас нет права просматривать мощности этой организации.");
  const inventory = page.getByRole("link", { name: "Список компьютеров", exact: true });
  if (granted) await expect(denied).toBeVisible();
  else await expect(inventory).toBeVisible();
  const endpoint = `${API}/companies/${company.id}`;
  await temporaryReadFailure(page, endpoint);
  if (!granted) {
    await temporaryReadFailure(page, `${endpoint}/resources/summary?only_available=true`);
    await temporaryReadFailure(page, `${endpoint}/resources/availability-history?*`);
  }
  expect((await request.patch(`${API}/memberships/${membership}`, {
    headers: headers(owner), data: { permissions: granted ? readPermissions : managerPermissions },
  })).status()).toBe(200);
  const mover = await context.newPage();
  const mutation = `${API}/connected-computers/${computer.id}/pool`;
  const delayed = rosterFirst ? await pauseMutation(mover, mutation, { method: "PATCH", pause: "response" }) : null;
  try {
    await mover.goto("compute/computers");
    await mover.getByRole("button", { name: `Сменить пул — ${computer.name}`, exact: true }).click();
    const dialog = mover.getByRole("dialog", { name: "Сменить пул" });
    await dialog.getByLabel("Новый пул").selectOption(company.id);
    const delivered = mover.waitForResponse((response) => response.url() === mutation && response.status() === 200);
    await dialog.getByRole("button", { name: "Сменить пул", exact: true }).click();
    let rosterRevision: number | undefined;
    if (delayed) {
      await delayed.pending;
      const colleague = await makeAccount(request, "Участник после переноса");
      await addMember(request, owner, company.id, colleague, "user", ["members.read"]);
      const roster = page.waitForResponse((response) =>
        response.url().startsWith(`${API}/memberships?company_id=${company.id}`) && response.status() === 200);
      await page.evaluate(() => window.dispatchEvent(new Event("online")));
      rosterRevision = Number((await roster).headers()["x-computer-pool-check-revision"]);
      await expect(page.getByRole("article", { name: colleague.first_name, exact: true })).toBeVisible();
      delayed.release();
    }
    const receipt = await delivered;
    expect(receipt.headers()["x-computer-read-allowed"]).toBe(granted ? "true" : "false");
    if (rosterRevision !== undefined) {
      expect(rosterRevision).toBeGreaterThan(Number(receipt.headers()["x-computer-pool-check-revision"]));
    }
    const actual = await request.get(`${endpoint}/resources/summary?only_available=true`, { headers: headers(account) });
    expect(actual.status()).toBe(granted ? 200 : 403);
    if (granted) {
      await expect(inventory).toBeVisible();
      if (catalog) await expect(page.getByTestId("computer-count")).toHaveText("2");
      else await expect(page.locator("a.organization-computer-card").filter({ hasText: computer.name })).toBeVisible();
      await expect(denied).toHaveCount(0);
    } else {
      await expect(inventory).toHaveCount(0);
      await expect(denied).toBeVisible();
    }
  } finally {
    delayed?.release();
    await mover.close();
  }
});

for (const late of [false, true]) test(`a newer resource grant survives an inventory outage (${late ? "delayed transfer receipt" : "ordered transfer receipt"})`, async ({ page, request, account, context }) => {
  const owner = await makeAccount(request);
  const company = await createCompany(request, owner, "Организация с доступным графиком");
  const membership = await addMember(request, owner, company.id, account, "user", ["resources.manage"]);
  const existing = await addComputer(request, account, "Mac для графика", company.id);
  const computer = await addComputer(request, account, "Mac для переноса");
  for (const device of [existing, computer]) await markAccountComputer(request, device.id);
  await startSession(page, account);
  await page.goto("compute/computers");
  await page.getByRole("link", { name: company.name, exact: true }).click();
  const denied = page.getByText("У вас нет права просматривать мощности этой организации.", { exact: true });
  const inventory = page.getByRole("link", { name: "Список компьютеров", exact: true });
  const graph = page.getByRole("region", { name: "История CPU", exact: true }).locator("figure");
  await expect(denied).toBeVisible();
  const mover = await context.newPage();
  const mutation = `${API}/connected-computers/${computer.id}/pool`;
  const delayed = await pauseMutation(mover, mutation, { method: "PATCH", pause: "response" });
  const metadata = `${API}/companies/${company.id}`;
  const overview = `${metadata}/resources/pool-overview`;
  const history = `${metadata}/resources/pool-history`;
  try {
    await mover.goto("compute/computers");
    await mover.getByRole("button", { name: `Сменить пул — ${computer.name}`, exact: true }).click();
    const dialog = mover.getByRole("dialog", { name: "Сменить пул" });
    await dialog.getByLabel("Новый пул").selectOption(company.id);
    const transferred = mover.waitForResponse((response) => response.url() === mutation && response.status() === 200);
    await dialog.getByRole("button", { name: "Сменить пул", exact: true }).click();
    await delayed.pending;
    if (!late) {
      delayed.release();
      await (await transferred).finished();
    }
    await temporaryReadFailure(page, overview);
    expect((await request.patch(`${API}/memberships/${membership}`, {
      headers: headers(owner), data: { permissions: ["resources.manage", "resources.read"] },
    })).status()).toBe(200);
    const checked = page.waitForResponse(async (response) => response.url() === metadata && response.status() === 200 &&
      (await response.json()).my_permissions.includes("resources.read"));
    const graphed = page.waitForResponse((response) => response.url() === history && response.status() === 200);
    const inventoryFailed = page.waitForResponse((response) => response.url() === overview && response.status() === 503);
    await page.evaluate(() => window.dispatchEvent(new Event("online")));
    const revision = Number((await checked).headers()["x-computer-pool-check-revision"]);
    await (await inventoryFailed).finished();
    await (await graphed).finished();
    await expect(inventory).toBeVisible();
    await expect(graph).toBeVisible();
    if (late) {
      await inventory.focus();
      const rechecked = page.waitForResponse((response) => response.url() === metadata && response.status() === 200);
      const retried = page.waitForResponse((response) => response.url() === overview && response.status() === 503);
      delayed.release();
      expect((await (await rechecked).json()).my_permissions).toContain("resources.read");
      await (await retried).finished();
    }
    const receipt = await transferred;
    expect(receipt.headers()["x-computer-read-allowed"]).toBe("false");
    expect(revision).toBeGreaterThan(Number(receipt.headers()["x-computer-pool-check-revision"]));
    expect((await request.get(overview, { headers: headers(account) })).status()).toBe(200);
    expect((await request.get(history, { headers: headers(account) })).status()).toBe(200);
    await expect(page.locator(".organization-computers").getByRole("button", { name: "Попробовать снова ↻", exact: true })).toBeVisible();
    await expect(inventory).toBeVisible();
    await expect(graph).toBeVisible();
    await expect(denied).toHaveCount(0);
    if (late) await expect(inventory).toBeFocused();
  } finally {
    delayed.release();
    await mover.close();
  }
});

for (const outage of [false, true]) test(`restored organization access recovers the chart ${outage ? "while the inventory is unavailable" : "with a healthy inventory"}`, async ({ page, request, account }) => {
  const owner = await makeAccount(request);
  const company = await createCompany(request, owner, "Организация с восстановленным доступом");
  const originalPermissions = ["resources.manage", "resources.read"];
  const membership = await addMember(request, owner, company.id, account, "user", originalPermissions);
  const computer = await addComputer(request, account, "Mac после восстановления доступа", company.id);
  await markAccountComputer(request, computer.id);
  await startSession(page, account);
  await page.goto("compute/computers");
  await page.getByRole("link", { name: company.name, exact: true }).click();
  const inventory = page.getByRole("link", { name: "Список компьютеров", exact: true });
  const computers = page.getByRole("region", { name: "Компьютеры", exact: true });
  const graph = page.getByRole("region", { name: "История CPU", exact: true }).locator("figure");
  const denied = page.getByText("У вас нет права просматривать мощности этой организации.", { exact: true });
  await expect(graph).toBeVisible();
  await inventory.focus();
  const metadata = `${API}/companies/${company.id}`;
  const overview = `${metadata}/resources/pool-overview`;
  const history = `${metadata}/resources/pool-history`;
  const update = (permissions: string[]) => request.patch(`${API}/memberships/${membership}`, {
    headers: headers(owner), data: { permissions },
  });
  await temporaryReadFailure(page, metadata);
  expect((await update(["resources.manage"])).status()).toBe(200);
  const revoked = page.waitForResponse((response) => [overview, history].includes(response.url()) && response.status() === 403);
  await page.evaluate(() => window.dispatchEvent(new Event("online")));
  const forbidden = await revoked;
  await expect(denied).toBeVisible();
  await expect(inventory).toHaveCount(0);
  await expect(graph).toHaveCount(0);
  await expect(page.getByRole("link", { name: "← К организациям", exact: true })).toBeFocused();

  expect((await update(originalPermissions)).status()).toBe(200);
  if (outage) await temporaryReadFailure(page, overview);
  await page.unroute(metadata);
  const checked = page.waitForResponse((response) => response.url() === metadata && response.status() === 200);
  const listed = page.waitForResponse((response) => response.url() === overview && response.status() === (outage ? 503 : 200));
  await page.evaluate(() => window.dispatchEvent(new Event("online")));
  const granted = await checked;
  expect((await granted.json()).my_permissions).toContain("resources.read");
  await (await listed).finished();
  await expect(inventory).toBeVisible();
  await expect(graph).toBeVisible();
  await expect(denied).toHaveCount(0);
  expect(forbidden.headers()["x-organization-membership-id"]).toBe(membership);
  expect(Number(granted.headers()["x-computer-pool-check-revision"]))
    .toBeGreaterThan(Number(forbidden.headers()["x-computer-pool-check-revision"]));
  if (outage) {
    const retry = computers.getByRole("button", { name: "Попробовать снова ↻", exact: true });
    await expect(retry).toBeVisible();
    await page.unroute(overview);
    await retry.click();
  }
  await expect(computers.getByRole("link", { name: new RegExp(computer.name) })).toBeVisible();
});

test("a newer organization history preserves reading when the inventory and metadata are unavailable", async ({ page, request, account, context }) => {
  const owner = await makeAccount(request);
  const company = await createCompany(request, owner, "Организация с актуальной историей");
  const originalPermissions = ["resources.manage", "resources.read"];
  const membership = await addMember(request, owner, company.id, account, "user", originalPermissions);
  const existing = await addComputer(request, account, "Mac команды", company.id);
  const moving = await addComputer(request, account, "Mac после переноса");
  for (const computer of [existing, moving]) await markAccountComputer(request, computer.id);
  await startSession(page, account);
  await page.goto("compute/computers");
  await page.getByRole("link", { name: company.name, exact: true }).click();
  const total = page.getByRole("region", { name: "История CPU", exact: true }).locator("figcaption span").first().locator("strong");
  const inventory = page.getByRole("link", { name: "Список компьютеров", exact: true });
  await expect(total).toHaveText("8 ядер");
  const metadata = `${API}/companies/${company.id}`;
  const overview = `${metadata}/resources/pool-overview`;
  const history = `${metadata}/resources/pool-history`;
  const mutation = `${API}/connected-computers/${moving.id}/pool`;
  const update = (permissions: string[]) => request.patch(`${API}/memberships/${membership}`, {
    headers: headers(owner), data: { permissions },
  });
  const mover = await context.newPage();
  const held = await pauseMutation(mover, mutation, { method: "PATCH", pause: "response" });
  try {
    await mover.goto("compute/computers");
    await mover.getByRole("button", { name: `Сменить пул — ${moving.name}`, exact: true }).click();
    const dialog = mover.getByRole("dialog", { name: "Сменить пул" });
    await dialog.getByLabel("Новый пул").selectOption(company.id);
    await temporaryReadFailure(page, metadata);
    await temporaryReadFailure(page, overview);
    expect((await update(["resources.manage"])).status()).toBe(200);
    const delivered = mover.waitForResponse((response) => response.url() === mutation && response.status() === 200);
    await dialog.getByRole("button", { name: "Сменить пул", exact: true }).click();
    await held.pending;
    expect((await update(originalPermissions)).status()).toBe(200);
    const refreshed = page.waitForResponse((response) => response.url() === history && response.status() === 200);
    const unavailable = page.waitForResponse((response) => response.url() === overview && response.status() === 503);
    await page.evaluate(() => window.dispatchEvent(new Event("online")));
    const read = await refreshed;
    await (await unavailable).finished();
    await expect(total).toHaveText("16 ядер");
    await expect(inventory).toBeVisible();
    const checked = page.waitForResponse((response) => response.url() === metadata && response.status() === 503);
    held.release();
    const receipt = await delivered;
    expect(receipt.headers()["x-computer-read-allowed"]).toBe("false");
    await (await checked).finished();
    await expect(total).toHaveText("16 ядер");
    await expect(inventory).toBeVisible();
    expect(read.headers()["x-organization-membership-id"]).toBe(membership);
    expect(Number(read.headers()["x-computer-pool-check-revision"]))
      .toBeGreaterThan(Number(receipt.headers()["x-computer-pool-check-revision"]));
  } finally {
    held.release();
    await mover.close();
  }
});

for (const [endpoint, restored] of [["history", true], ["overview", true], ["history", false]] as const) {
  test(`a delayed organization ${endpoint} denial ${restored ? "does not replace the restored data" : "still hides data after a current revocation"}`, async ({ page, request, account }) => {
    const owner = await makeAccount(request);
    const company = await createCompany(request, owner, "Команда с актуальными данными");
    const fullPermissions = ["resources.read", "resources.manage"];
    const membership = await addMember(request, owner, company.id, account, "user", fullPermissions);
    const computer = await addComputer(request, account, "Mac команды", company.id);
    await markAccountComputer(request, computer.id);
    await startSession(page, account);
    await page.goto("compute/computers");
    await page.getByRole("link", { name: company.name, exact: true }).click();
    const computers = page.getByRole("region", { name: "Компьютеры", exact: true });
    const chart = page.getByRole("region", { name: "История CPU", exact: true });
    const inventory = computers.getByRole("link", { name: "Список компьютеров", exact: true });
    const card = computers.getByRole("link", { name: new RegExp(computer.name) });
    const total = chart.locator("figcaption span").first().locator("strong");
    const denied = page.getByText("У вас нет права просматривать мощности этой организации.", { exact: true });
    const metadata = `${API}/companies/${company.id}`;
    const history = `${metadata}/resources/pool-history`;
    const overview = `${metadata}/resources/pool-overview`;
    const delay = endpoint === "history" ? history : overview;
    const outage = endpoint === "history" ? overview : history;
    const delayedPanel = endpoint === "history" ? chart : computers;
    const retryPanel = endpoint === "history" ? computers : chart;
    const retry = retryPanel.getByRole("button", { name: "Попробовать снова ↻", exact: true });
    const update = (permissions: string[]) => request.patch(`${API}/memberships/${membership}`, {
      headers: headers(owner), data: { permissions },
    });
    await expect(total).toHaveText("8 ядер");
    await expect(card).toBeVisible();
    await temporaryReadFailure(page, metadata);
    await temporaryReadFailure(page, outage);
    let release!: () => void;
    let report!: (revision: number) => void;
    const held = new Promise<void>((resolve) => { release = resolve; });
    const captured = new Promise<number>((resolve) => { report = resolve; });
    await page.route(delay, async (route) => {
      const reply = await route.fetch();
      expect(reply.status()).toBe(403);
      expect(reply.headers()["x-organization-membership-id"]).toBe(membership);
      report(Number(reply.headers()["x-computer-pool-check-revision"]));
      await held;
      await route.fulfill({ response: reply });
    }, { times: 1 });
    try {
      expect((await update(["resources.manage"])).status()).toBe(200);
      await page.evaluate(() => window.dispatchEvent(new Event("online")));
      const olderRevision = await captured;
      await expect(retry).toBeVisible();
      if (restored) {
        expect((await update(fullPermissions)).status()).toBe(200);
        await page.unroute(outage);
        const received = page.waitForResponse((response) => response.url() === outage && response.status() === 200);
        await retry.click();
        const proof = await received;
        const revision = endpoint === "history" ? Number((await proof.json()).check_revision)
          : Number(proof.headers()["x-computer-pool-check-revision"]);
        expect(proof.headers()["x-organization-membership-id"]).toBe(membership);
        expect(revision).toBeGreaterThan(olderRevision);
        if (endpoint === "history") await expect(card).toBeVisible();
        else await expect(total).toHaveText("8 ядер");
      }
      const delivered = page.waitForResponse((response) => response.url() === delay && response.status() === 403);
      const refreshed = restored ? page.waitForResponse((response) => response.url() === delay && response.status() === 200) : null;
      release();
      await (await delivered).finished();
      if (refreshed) {
        await (await refreshed).finished();
        await expect(delayedPanel).toHaveAttribute("aria-busy", "false");
        await expect(delayedPanel.getByRole("alert")).toHaveCount(0);
        await expect(denied).toHaveCount(0);
        await expect(total).toHaveText("8 ядер");
        await expect(inventory).toBeVisible();
        await expect(card).toBeVisible();
      } else {
        await expect(denied).toBeVisible();
        await expect(inventory).toHaveCount(0);
        await expect(total).toHaveCount(0);
      }
    } finally {
      release();
    }
  });
}

for (const [source, rejoined] of [
  ["history", true], ["metadata", true], ["roster", true], ["history", false], ["metadata", false], ["roster", false], ["legacy metadata", true],
] as const) {
  test(`a delayed missing-membership response from ${source} ${rejoined ? "preserves the verified renewed organization" : "removes a genuinely unavailable organization"}`, async ({ page, request, account }) => {
    const owner = await makeAccount(request, "Владелец команды");
    const company = await createCompany(request, owner, "Команда с восстановленным участием");
    const permissions = ["resources.read", "resources.manage", ...(source === "roster" ? ["members.read"] : [])];
    const join = () => addMember(request, owner, company.id, account, "user", permissions);
    const original = await join();
    const computer = await addComputer(request, account, "Mac команды", company.id);
    await markAccountComputer(request, computer.id);
    await startSession(page, account);
    const legacy = source === "legacy metadata";
    await page.goto(legacy ? `compute/pools/${company.id}` : "compute/computers");
    if (!legacy) await page.getByRole("link", { name: company.name, exact: true }).click();
    const title = page.getByRole("heading", { name: legacy ? company.name : `${company.name}.`, exact: true });
    const list = page.getByRole("link", { name: /^Список компьютеров/ });
    const region = legacy ? page.getByRole("region", { name: company.name, exact: true })
      : page.getByRole("region", { name: "Компьютеры", exact: true });
    const data = legacy ? region.getByTestId("computer-count") : region.getByRole("link", { name: new RegExp(computer.name) });
    const graph = page.getByRole("region", { name: "История CPU", exact: true }).locator("figure");
    const roster = page.getByRole("article", { name: owner.first_name, exact: true });
    const retry = region.getByRole("button", { name: "Попробовать снова ↻", exact: true });
    const metadata = `${API}/companies/${company.id}`;
    const overview = `${metadata}/resources/${legacy ? "summary?only_available=true" : "pool-overview"}`;
    const history = `${metadata}/resources/${legacy ? "availability-history?*" : "pool-history"}`;
    const team = `${API}/memberships?company_id=${company.id}&limit=5&offset=0`;
    const delay = source === "history" ? history : source === "roster" ? team : metadata;
    const outage = delay === metadata ? history : metadata;
    let overviewAvailable = false;
    let otherReadAvailable = false;
    await expect(title).toBeVisible();
    await expect(data).toBeVisible();
    if (!legacy) await expect(graph).toBeVisible();
    if (source === "roster") {
      await expect(roster).toBeVisible();
      await temporaryReadFailure(page, history);
    }
    await temporaryReadFailure(page, overview, () => !overviewAvailable);
    await temporaryReadFailure(page, outage, () => !otherReadAvailable);
    let release!: () => void;
    let report!: (revision: number) => void;
    const held = new Promise<void>((resolve) => { release = resolve; });
    const captured = new Promise<number>((resolve) => { report = resolve; });
    await page.route(delay, async (route) => {
      const reply = await route.fetch();
      expect(reply.status()).toBe(404);
      expect(reply.headers()["x-organization-membership-id"]).toBeUndefined();
      expect(reply.headers()["x-organization-id"]).toBeUndefined();
      report(Number(reply.headers()["x-computer-pool-check-revision"]));
      await held;
      await route.fulfill({ response: reply });
    }, { times: 1 });
    try {
      expect((await request.delete(`${API}/memberships/${original}`, { headers: headers(owner) })).status()).toBe(204);
      await page.evaluate(() => window.dispatchEvent(new Event("online")));
      const missingRevision = await captured;
      await expect(retry).toBeVisible();
      if (rejoined) {
        const renewed = await join();
        expect(renewed).not.toBe(original);
        overviewAvailable = true;
        if (legacy) otherReadAvailable = true;
        const current = page.waitForResponse((response) => response.url() === overview && response.status() === 200);
        await retry.click();
        const proof = await current;
        expect(proof.headers()["x-organization-membership-id"]).toBe(renewed);
        const revision = legacy ? Number(proof.headers()["x-computer-pool-check-revision"]) : Number((await proof.json()).check_revision);
        expect(revision).toBeGreaterThan(missingRevision);
        await expect(retry).toHaveCount(0);
        await expect(data).toBeVisible();
        if (source === "metadata") otherReadAvailable = true;
      }
      const delivered = page.waitForResponse((response) => response.url() === delay && response.status() === 404);
      const updated = rejoined ? page.waitForResponse((response) => response.url() === delay && response.status() === 200) : null;
      release();
      await (await delivered).finished();
      if (updated) {
        await (await updated).finished();
        await expect(title).toBeVisible();
        await expect(data).toBeVisible();
        await expect(list).toBeVisible();
        if (!legacy) await expect(graph).toBeVisible();
        if (source === "roster") await expect(roster).toBeVisible();
      } else {
        await expect(title).toHaveCount(0);
        await expect(list).toHaveCount(0);
        if (delay !== metadata) {
          otherReadAvailable = true;
          await page.getByRole("button", { name: "Попробовать снова ↻", exact: true }).click();
        }
        await expect(page).toHaveURL(/\/organizations$/);
      }
    } finally {
      release();
    }
  });
}

for (const renewed of [false, true]) test(`a delayed roster denial respects ${renewed ? "verified renewed membership" : "a current restriction"}`, async ({ page, request, account }) => {
  const owner = await makeAccount(request, "Владелец команды");
  const company = await createCompany(request, owner, "Команда с проверенным составом");
  const resourcePermissions = ["resources.read", "resources.manage"];
  const join = () => addMember(request, owner, company.id, account, "user", [...resourcePermissions, "members.read"]);
  const original = await join();
  const computer = await addComputer(request, account, "Mac команды", company.id);
  await markAccountComputer(request, computer.id);
  await startSession(page, account);
  await page.goto("compute/computers");
  await page.getByRole("link", { name: company.name, exact: true }).click();
  const title = page.getByRole("heading", { name: `${company.name}.`, exact: true });
  const computers = page.getByRole("region", { name: "Компьютеры", exact: true });
  const card = computers.getByRole("link", { name: new RegExp(computer.name) });
  const row = page.getByRole("article", { name: owner.first_name, exact: true });
  const retry = computers.getByRole("button", { name: "Попробовать снова ↻", exact: true });
  const metadata = `${API}/companies/${company.id}`;
  const overview = `${metadata}/resources/pool-overview`;
  const team = `${API}/memberships?company_id=${company.id}&limit=5&offset=0`;
  await expect(card).toBeVisible();
  await expect(row).toBeVisible();
  let overviewAvailable = false;
  await temporaryReadFailure(page, metadata);
  await temporaryReadFailure(page, overview, () => !overviewAvailable);
  let release!: () => void;
  let report!: (revision: number) => void;
  const held = new Promise<void>((resolve) => { release = resolve; });
  const captured = new Promise<number>((resolve) => { report = resolve; });
  await page.route(team, async (route) => {
    const response = await route.fetch();
    expect(response.status()).toBe(403);
    expect(response.headers()["x-organization-membership-id"]).toBe(original);
    report(Number(response.headers()["x-computer-pool-check-revision"]));
    await held;
    await route.fulfill({ response });
  }, { times: 1 });
  try {
    expect((await request.patch(`${API}/memberships/${original}`, {
      headers: headers(owner), data: { permissions: resourcePermissions },
    })).status()).toBe(200);
    await page.evaluate(() => window.dispatchEvent(new Event("online")));
    const earlier = await captured;
    await expect(retry).toBeVisible();
    let current = original;
    if (renewed) {
      expect((await request.delete(`${API}/memberships/${original}`, { headers: headers(owner) })).status()).toBe(204);
      current = await join();
      expect(current).not.toBe(original);
    } else await createCompany(request, owner, "Ещё одна команда владельца");
    overviewAvailable = true;
    const checked = page.waitForResponse((response) => response.url() === overview && response.status() === 200);
    await retry.click();
    const proof = await checked;
    expect(proof.headers()["x-organization-membership-id"]).toBe(current);
    expect(Number((await proof.json()).check_revision)).toBeGreaterThan(earlier);
    await expect(card).toBeVisible();
    const delivered = page.waitForResponse((response) => response.url() === team && response.status() === 403);
    const refreshed = renewed ? page.waitForResponse((response) => response.url() === team && response.status() === 200 &&
      response.headers()["x-organization-membership-id"] === current) : null;
    release();
    await (await delivered).finished();
    if (refreshed) {
      await (await refreshed).finished();
      await expect(row).toBeVisible();
    } else await expect(row).toHaveCount(0);
    await expect(card).toBeVisible();
    await expect(title).toBeVisible();
  } finally {
    release();
  }
});

for (const section of ["roster", "invitations"] as const) for (const restored of [false, true]) {
  test(`a delayed ${section} denial ${restored ? "leaves the restored permission on the same membership" : "survives an unrelated resource check"}`, async ({ page, request, account }) => {
    const owner = await makeAccount(request, "Владелец команды");
    const company = await createCompany(request, owner, "Команда с проверенным разрешением");
    const resources = ["resources.read", "resources.manage"];
    const invitations = section === "invitations";
    const contact = "pending-member@example.com";
    const permission = invitations ? "members.manage" : "members.read";
    const membership = await addMember(request, owner, company.id, account, invitations ? "admin" : "user",
      invitations ? undefined : [...resources, "members.read"]);
    const setRead = async (read: boolean) => {
      const data = invitations ? (read ? { role: "admin" } : { role: "user", permissions: [...resources, "members.read"] })
        : { permissions: read ? [...resources, "members.read"] : resources };
      const result = await request.patch(`${API}/memberships/${membership}`, { headers: headers(owner), data });
      expect(result.status()).toBe(200);
      expect((await result.json()).id).toBe(membership);
    };
    if (invitations) expect((await request.post(`${API}/invitations`, {
      headers: headers(owner), data: { company_id: company.id, identifier: contact, role: "user" },
    })).status()).toBe(201);
    const computer = await addComputer(request, account, "Mac команды", company.id);
    await markAccountComputer(request, computer.id);
    await startSession(page, account);
    await page.goto("compute/computers");
    await page.getByRole("link", { name: company.name, exact: true }).click();
    if (invitations) await switchOrganizationPeople(page, "Приглашения");
    const row = page.getByRole("article", { name: invitations ? contact : owner.first_name, exact: true });
    const computers = page.getByRole("region", { name: "Компьютеры", exact: true });
    const metadata = `${API}/companies/${company.id}`;
    const overview = `${metadata}/resources/pool-overview`;
    const endpoint = invitations ? `${API}/invitations?company_id=${company.id}&include_counts=false&limit=200&offset=0`
      : `${API}/memberships?company_id=${company.id}&limit=5&offset=0`;
    await expect(row).toBeVisible();
    let metadataAvailable = false;
    let overviewAvailable = false;
    await temporaryReadFailure(page, metadata, () => !metadataAvailable);
    await temporaryReadFailure(page, overview, () => !overviewAvailable);
    const previous = await holdPoolRead(page, endpoint);
    try {
      await setRead(false);
      await page.evaluate(() => window.dispatchEvent(new Event("online")));
      previous.send();
      const denied = await previous.captured;
      expect(denied.status()).toBe(403);
      expect(denied.headers()["x-organization-membership-id"]).toBe(membership);
      const earlier = Number(denied.headers()["x-computer-pool-check-revision"]);
      const panel = restored ? page.getByRole("group", { name: "Загрузка организации", exact: true }) : computers;
      const retry = panel.getByRole("button", { name: "Попробовать снова ↻", exact: true });
      await expect(retry).toBeVisible();
      if (restored) {
        await setRead(true);
        metadataAvailable = true;
      } else {
        await createCompany(request, owner, "Другая команда владельца");
        overviewAvailable = true;
      }
      const checked = page.waitForResponse((response) => response.url() === (restored ? metadata : overview) && response.status() === 200);
      await retry.click();
      const proof = await checked;
      const body = await proof.json();
      if (restored) expect(body).toMatchObject({ my_membership_id: membership, my_permissions: expect.arrayContaining([permission]) });
      else expect(proof.headers()["x-organization-membership-id"]).toBe(membership);
      expect(Number(restored ? proof.headers()["x-computer-pool-check-revision"] : body.check_revision)).toBeGreaterThan(earlier);
      await expect(retry).toHaveCount(0);
      await expect(row).toBeVisible();
      const delivered = page.waitForResponse((response) => response.url() === endpoint && response.status() === 403);
      const refreshed = restored ? page.waitForResponse((response) => response.url() === endpoint && response.status() === 200) : null;
      previous.deliver();
      await (await delivered).finished();
      if (refreshed) {
        await (await refreshed).finished();
        await expect(row).toBeVisible();
      } else await expect(row).toHaveCount(0);
      await expect(page.getByRole("heading", { name: `${company.name}.`, exact: true })).toBeVisible();
    } finally {
      previous.send();
      previous.deliver();
    }
  });
}

for (const registry of [false, true]) for (const mode of ["healthy", "unavailable", "restricted"] as const) {
  test(`a confirmed permission restores ${registry ? "invitation actions" : "roster search"} when its section is ${mode}`, async ({ page, request, account }) => {
    const owner = await makeAccount(request, "Владелец команды");
    const company = await createCompany(request, owner, "Команда с частичным восстановлением");
    const resources = ["resources.read", "resources.manage"];
    const permission = registry ? "members.manage" : "members.read";
    const membership = await addMember(request, owner, company.id, account, registry ? "admin" : "user",
      registry ? undefined : [...resources, "members.read"]);
    const setRead = async (read: boolean) => {
      const data = registry ? (read ? { role: "admin" } : { role: "user", permissions: [...resources, "members.read"] })
        : { permissions: read ? [...resources, "members.read"] : resources };
      const result = await request.patch(`${API}/memberships/${membership}`, { headers: headers(owner), data });
      expect(result.status()).toBe(200);
      expect((await result.json()).id).toBe(membership);
    };
    const contact = "pending-after-restoration@example.com";
    if (registry) expect((await request.post(`${API}/invitations`, {
      headers: headers(owner), data: { company_id: company.id, identifier: contact, role: "user" },
    })).status()).toBe(201);
    const computer = await addComputer(request, account, "Mac команды", company.id);
    await markAccountComputer(request, computer.id);
    await startSession(page, account);
    await page.goto("compute/computers");
    await page.getByRole("link", { name: company.name, exact: true }).click();
    if (registry) await switchOrganizationPeople(page, "Приглашения");
    const control = registry ? page.getByRole("region", { name: "Добавить участника", exact: true })
      : page.getByRole("searchbox", { name: "Найти участника", exact: true });
    const row = page.getByRole("article", { name: registry ? contact : owner.first_name, exact: true });
    const host = page.getByRole("region", { name: registry ? "Приглашения" : "Участники", exact: true });
    const metadata = `${API}/companies/${company.id}`;
    const endpoint = registry ? `${API}/invitations?company_id=${company.id}&include_counts=false&limit=200&offset=0`
      : `${API}/memberships?company_id=${company.id}&limit=5&offset=0`;
    const retry = page.getByRole("group", { name: "Загрузка организации", exact: true })
      .getByRole("button", { name: "Попробовать снова ↻", exact: true });
    await expect(row).toBeVisible();
    await expect(control).toBeVisible();
    let metadataAvailable = false;
    let sectionAvailable = true;
    await temporaryReadFailure(page, metadata, () => !metadataAvailable);
    await temporaryReadFailure(page, endpoint, () => !sectionAvailable);
    await setRead(false);
    const denied = page.waitForResponse((response) => response.url() === endpoint && response.status() === 403);
    await page.evaluate(() => window.dispatchEvent(new Event("online")));
    const earlier = await denied;
    expect(earlier.headers()["x-organization-membership-id"]).toBe(membership);
    await expect(control).toHaveCount(0);
    await expect(row).toHaveCount(0);
    await expect(retry).toBeVisible();

    if (mode === "restricted") await createCompany(request, owner, "Другая команда владельца");
    else await setRead(true);
    metadataAvailable = true;
    sectionAvailable = mode !== "unavailable";
    const checked = page.waitForResponse((response) => response.url() === metadata && response.status() === 200);
    const reloaded = mode === "restricted" ? null : page.waitForResponse((response) => response.url() === endpoint &&
      response.status() === (mode === "unavailable" ? 503 : 200));
    await retry.click();
    const proof = await checked;
    expect(Number(proof.headers()["x-computer-pool-check-revision"]))
      .toBeGreaterThan(Number(earlier.headers()["x-computer-pool-check-revision"]));
    const body = await proof.json();
    expect(body.my_membership_id).toBe(membership);
    expect(body.my_permissions.includes(permission)).toBe(mode !== "restricted");
    if (reloaded) {
      await (await reloaded).finished();
      await expect(control).toBeVisible();
      if (mode === "healthy") await expect(row).toBeVisible();
      else {
        await expect(row).toHaveCount(0);
        await expect(host.getByRole("alert")).toContainText("Не удалось связаться с сервером.");
      }
    } else {
      await expect(control).toHaveCount(0);
      await expect(row).toHaveCount(0);
    }
    await expect(page.getByRole("link", { name: new RegExp(computer.name) })).toBeVisible();
  });
}

for (const registry of [false, true]) for (const restored of [false, true]) {
  test(`older metadata ${restored ? "preserves a newer read of" : "applies a current restriction to"} the ${registry ? "invitation registry" : "roster"}`, async ({ page, request, account }) => {
    const owner = await makeAccount(request, "Владелец команды");
    const company = await createCompany(request, owner, "Команда с обновлённым разрешением");
    const resources = ["resources.read", "resources.manage"];
    const membership = await addMember(request, owner, company.id, account, registry ? "admin" : "user",
      registry ? undefined : [...resources, "members.read"]);
    const permission = registry ? "members.manage" : "members.read";
    const setRead = async (read: boolean) => {
      const data = registry ? (read ? { role: "admin" } : { role: "user", permissions: [...resources, "members.read"] })
        : { permissions: read ? [...resources, "members.read"] : resources };
      expect((await request.patch(`${API}/memberships/${membership}`, { headers: headers(owner), data })).status()).toBe(200);
    };
    const invite = async (identifier: string) => {
      expect((await request.post(`${API}/invitations`, {
        headers: headers(owner), data: { company_id: company.id, identifier, role: "user" },
      })).status()).toBe(201);
    };
    const contact = "old-invitation@example.com";
    const newContact = "new-invitation@example.com";
    const colleague = await makeAccount(request, "Новый коллега");
    if (registry) await invite(contact);
    const computer = await addComputer(request, account, "Mac команды", company.id);
    await markAccountComputer(request, computer.id);
    await startSession(page, account);
    await page.goto("compute/computers");
    await page.getByRole("link", { name: company.name, exact: true }).click();
    if (registry) await switchOrganizationPeople(page, "Приглашения");
    const previousRow = page.getByRole("article", { name: registry ? contact : owner.first_name, exact: true });
    const newRow = page.getByRole("article", { name: registry ? newContact : colleague.first_name, exact: true });
    const control = registry ? page.getByRole("region", { name: "Добавить участника", exact: true })
      : page.getByRole("searchbox", { name: "Найти участника", exact: true });
    const metadata = `${API}/companies/${company.id}`;
    const endpoint = registry ? `${API}/invitations?company_id=${company.id}&include_counts=false&limit=200&offset=0`
      : `${API}/memberships?company_id=${company.id}&limit=5&offset=0`;
    await expect(control).toBeVisible();
    await expect(previousRow).toBeVisible();
    const detail = await holdPoolRead(page, metadata);
    let release!: () => void;
    const held = new Promise<void>((resolve) => { release = resolve; });
    await page.route(endpoint, async (route) => {
      await held;
      await route.continue();
    });
    try {
      await setRead(false);
      await page.evaluate(() => window.dispatchEvent(new Event("online")));
      detail.send();
      const old = await detail.captured;
      expect(old.status()).toBe(200);
      const data = await old.json();
      expect(data.my_membership_id).toBe(membership);
      expect(data.my_permissions).not.toContain(permission);
      const earlier = Number(old.headers()["x-computer-pool-check-revision"]);
      if (restored) {
        await setRead(true);
        if (registry) await invite(newContact);
        else await addMember(request, owner, company.id, colleague, "user");
        const checked = page.waitForResponse((response) => response.url() === endpoint && response.status() === 200);
        release();
        const proof = await checked;
        expect(proof.headers()["x-organization-membership-id"]).toBe(membership);
        expect(Number(proof.headers()["x-computer-pool-check-revision"])).toBeGreaterThan(earlier);
        await expect(newRow).toBeVisible();
      }
      const delivered = page.waitForResponse((response) => response.url() === metadata && response.status() === 200 &&
        Number(response.headers()["x-computer-pool-check-revision"]) === earlier);
      const refreshed = restored ? page.waitForResponse((response) => response.url() === metadata && response.status() === 200 &&
        Number(response.headers()["x-computer-pool-check-revision"]) > earlier) : null;
      detail.deliver();
      await (await delivered).finished();
      if (refreshed) {
        await (await refreshed).finished();
        await expect(control).toBeVisible();
        await expect(newRow).toBeVisible();
      } else {
        await expect(control).toHaveCount(0);
        await expect(previousRow).toHaveCount(0);
      }
      await expect(page.getByRole("link", { name: new RegExp(computer.name) })).toBeVisible();
    } finally {
      detail.send();
      detail.deliver();
      release();
    }
  });
}

for (const renewed of [false, true]) test(`a delayed successful organization read ${renewed ? "keeps the verified renewed membership" : "applies a promotion for the same membership"}`, async ({ page, request, account }) => {
  const owner = await makeAccount(request);
  const company = await createCompany(request, owner, "Команда с проверенной ролью");
  const join = () => addMember(request, owner, company.id, account, "user", ["resources.read", "resources.manage", "members.read"]);
  const original = await join();
  const computer = await addComputer(request, account, "Mac команды", company.id);
  await markAccountComputer(request, computer.id);
  await startSession(page, account);
  await page.goto("compute/computers");
  await page.getByRole("link", { name: company.name, exact: true }).click();
  const title = page.getByRole("heading", { name: `${company.name}.`, exact: true });
  const computers = page.getByRole("region", { name: "Компьютеры", exact: true });
  const card = computers.getByRole("link", { name: new RegExp(computer.name) });
  const invitations = page.getByRole("tab", { name: "Приглашения", exact: true });
  const retry = computers.getByRole("button", { name: "Попробовать снова ↻", exact: true });
  const metadata = `${API}/companies/${company.id}`;
  const overview = `${metadata}/resources/pool-overview`;
  let overviewAvailable = false;
  await expect(card).toBeVisible();
  await expect(invitations).toHaveCount(0);
  await temporaryReadFailure(page, metadata);
  await temporaryReadFailure(page, overview, () => !overviewAvailable);
  let release!: () => void;
  let report!: (revision: number) => void;
  const held = new Promise<void>((resolve) => { release = resolve; });
  const captured = new Promise<number>((resolve) => { report = resolve; });
  await page.route(metadata, async (route) => {
    const response = await route.fetch();
    expect(response.status()).toBe(200);
    expect(await response.json()).toMatchObject({ my_membership_id: original, my_role: "admin" });
    report(Number(response.headers()["x-computer-pool-check-revision"]));
    await held;
    await route.fulfill({ response });
  }, { times: 1 });
  try {
    expect((await request.patch(`${API}/memberships/${original}`, {
      headers: headers(owner), data: { role: "admin" },
    })).status()).toBe(200);
    await page.evaluate(() => window.dispatchEvent(new Event("online")));
    const olderRevision = await captured;
    await expect(retry).toBeVisible();
    let membership = original;
    if (renewed) {
      expect((await request.delete(`${API}/memberships/${original}`, { headers: headers(owner) })).status()).toBe(204);
      membership = await join();
      expect(membership).not.toBe(original);
    } else {
      await createCompany(request, owner, "Другая команда владельца");
    }
    const actual = await request.get(metadata, { headers: headers(account) });
    expect(actual.status()).toBe(200);
    expect(await actual.json()).toMatchObject({ my_membership_id: membership, my_role: renewed ? "user" : "admin" });
    overviewAvailable = true;
    const refreshed = page.waitForResponse((response) => response.url() === overview && response.status() === 200);
    await retry.click();
    const proof = await refreshed;
    expect(proof.headers()["x-organization-membership-id"]).toBe(membership);
    expect(Number((await proof.json()).check_revision)).toBeGreaterThan(olderRevision);
    await expect(card).toBeVisible();
    await expect(invitations).toHaveCount(0);
    const delivered = page.waitForResponse((response) => response.url() === metadata && response.status() === 200);
    const checked = renewed ? page.waitForResponse((response) => response.url() === metadata && response.status() === 503) : null;
    release();
    await (await delivered).finished();
    if (checked) {
      await (await checked).finished();
      await expect(invitations).toHaveCount(0);
    } else {
      await expect(invitations).toBeVisible();
    }
    await expect(card).toBeVisible();
    await expect(title).toBeVisible();
  } finally {
    release();
  }
});

for (const [source, renewed] of [["roster", false], ["roster", true], ["overview", false], ["overview", true]] as const) {
  test(`a delayed successful ${source} ${renewed ? "cannot replace data after renewed membership" : "updates the current membership"}`, async ({ page, request, account }) => {
    const owner = await makeAccount(request, "Владелец команды");
    const company = await createCompany(request, owner, "Команда с согласованным снимком");
    const join = () => addMember(request, owner, company.id, account, "user", ["members.read", "resources.read", "resources.manage"]);
    const original = await join();
    const teammate = await makeAccount(request, "Временный коллега");
    const computer = await addComputer(request, account, "Mac команды", company.id);
    await markAccountComputer(request, computer.id);
    await startSession(page, account);
    await page.goto("compute/computers");
    await page.getByRole("link", { name: company.name, exact: true }).click();
    const computers = page.getByRole("region", { name: "Компьютеры", exact: true });
    const chart = page.getByRole("region", { name: "История CPU", exact: true });
    const metadata = `${API}/companies/${company.id}`;
    const overview = `${metadata}/resources/pool-overview`;
    const history = `${metadata}/resources/pool-history`;
    const team = `${API}/memberships?company_id=${company.id}&limit=5&offset=0`;
    const readsRoster = source === "roster";
    const delayed = readsRoster ? team : overview;
    const proofEndpoint = readsRoster ? overview : history;
    const retry = (readsRoster ? computers : chart).getByRole("button", { name: "Попробовать снова ↻", exact: true });
    const originalCard = computers.getByRole("link", { name: new RegExp(computer.name) });
    const previousName = "Временное имя Mac";
    const previousData = readsRoster ? page.getByRole("article", { name: teammate.first_name, exact: true })
      : computers.getByRole("link", { name: new RegExp(previousName) });
    const rename = (name: string) => request.patch(`${API}/computers/${computer.id}`, { headers: headers(account), data: { name } });
    await expect(originalCard).toBeVisible();
    await expect(chart.locator("figure")).toBeVisible();
    await expect(page.getByRole("article", { name: owner.first_name, exact: true })).toBeVisible();
    let temporaryMember: string | undefined;
    if (readsRoster) temporaryMember = await addMember(request, owner, company.id, teammate, "user", ["members.read"]);
    else expect((await rename(previousName)).status()).toBe(200);

    await temporaryReadFailure(page, metadata);
    let proofAvailable = false;
    await temporaryReadFailure(page, proofEndpoint, () => !proofAvailable);
    let release!: () => void;
    let report!: (revision: number) => void;
    const held = new Promise<void>((resolve) => { release = resolve; });
    const captured = new Promise<number>((resolve) => { report = resolve; });
    await page.route(delayed, async (route) => {
      const response = await route.fetch();
      expect(response.status()).toBe(200);
      expect(response.headers()["x-organization-membership-id"]).toBe(original);
      const body = await response.json();
      if (readsRoster) expect(body.some((member: { user_id: string }) => member.user_id === teammate.id)).toBe(true);
      else expect(body.computers.map((item: { name: string }) => item.name)).toContain(previousName);
      report(Number(readsRoster ? response.headers()["x-computer-pool-check-revision"] : body.check_revision));
      await held;
      await route.fulfill({ response });
    }, { times: 1 });
    try {
      await page.evaluate(() => window.dispatchEvent(new Event("online")));
      const earlierRevision = await captured;
      await expect(retry).toBeVisible();
      let current = original;
      if (renewed) {
        expect((await request.delete(`${API}/memberships/${original}`, { headers: headers(owner) })).status()).toBe(204);
        if (temporaryMember) expect((await request.delete(`${API}/memberships/${temporaryMember}`, { headers: headers(owner) })).status()).toBe(204);
        current = await join();
        expect(current).not.toBe(original);
        if (!readsRoster) expect((await rename(computer.name)).status()).toBe(200);
      } else {
        await createCompany(request, owner, "Другая команда владельца");
      }
      proofAvailable = true;
      const checked = page.waitForResponse((response) => response.url() === proofEndpoint && response.status() === 200);
      await retry.click();
      const proof = await checked;
      const revision = readsRoster ? Number((await proof.json()).check_revision) : Number(proof.headers()["x-computer-pool-check-revision"]);
      expect(proof.headers()["x-organization-membership-id"]).toBe(current);
      expect(revision).toBeGreaterThan(earlierRevision);
      await expect(retry).toHaveCount(0);
      await expect(previousData).toHaveCount(0);
      const delivered = page.waitForResponse((response) => response.url() === delayed && response.status() === 200 &&
        response.headers()["x-organization-membership-id"] === original);
      const refreshed = renewed ? page.waitForResponse((response) => response.url() === delayed && response.status() === 200 &&
        response.headers()["x-organization-membership-id"] === current) : null;
      release();
      await (await delivered).finished();
      if (refreshed) {
        await (await refreshed).finished();
        await expect(previousData).toHaveCount(0);
        await expect(originalCard).toBeVisible();
      } else await expect(previousData).toBeVisible();
    } finally {
      release();
    }
  });
}

for (const [opened, status, renewed] of [
  [false, 404, false], [false, 404, true], [true, 404, false], [true, 404, true],
  [false, 200, false], [false, 200, true], [true, 200, false], [true, 200, true],
  [true, 403, false], [true, 403, true],
] as const) {
  test(`a delayed invitation ${opened ? "registry" : "count"} ${status} respects ${renewed ? "renewed membership" : "the current membership check"}`, async ({ page, request, account }) => {
    const owner = await makeAccount(request, "Владелец команды");
    const company = await createCompany(request, owner, "Команда с приглашениями");
    const contact = "future-member@example.com";
    const invite = async (identifier: string) => {
      const response = await request.post(`${API}/invitations`, {
        headers: headers(owner), data: { company_id: company.id, identifier, role: "user" },
      });
      expect(response.status()).toBe(201);
      return (await response.json() as { id: string }).id;
    };
    await invite(contact);
    const join = () => addMember(request, owner, company.id, account, "admin");
    const original = await join();
    const computer = await addComputer(request, account, "Mac для команды", company.id);
    await markAccountComputer(request, computer.id);
    await startSession(page, account);
    await page.goto("compute/computers");
    await page.getByRole("link", { name: company.name, exact: true }).click();
    const title = page.getByRole("heading", { name: `${company.name}.`, exact: true });
    const computers = page.getByRole("region", { name: "Компьютеры", exact: true });
    const card = computers.getByRole("link", { name: new RegExp(computer.name) });
    const row = page.getByRole("article", { name: contact, exact: true });
    const candidate = "invitation-before-rejoining@example.com";
    const candidateRow = page.getByRole("article", { name: candidate, exact: true });
    const tab = page.getByRole("tab", { name: "Приглашения", exact: true });
    const retry = computers.getByRole("button", { name: "Попробовать снова ↻", exact: true });
    const metadata = `${API}/companies/${company.id}`;
    const overview = `${metadata}/resources/pool-overview`;
    const registry = `${API}/invitations?company_id=${company.id}&${opened ? "include_counts=false&limit=200&offset=0" : "limit=0"}`;
    await expect(card).toBeVisible();
    await expect(tab).toHaveText(/Приглашения.*1/);
    if (opened) {
      await switchOrganizationPeople(page, "Приглашения");
      await expect(row).toBeVisible();
    }
    const candidateId = status === 200 ? await invite(candidate) : null;
    let overviewAvailable = false;
    let metadataAvailable = false;
    await temporaryReadFailure(page, overview, () => !overviewAvailable);
    await temporaryReadFailure(page, metadata, () => !metadataAvailable);
    await temporaryReadFailure(page, `${metadata}/resources/pool-history`);
    await temporaryReadFailure(page, `${API}/memberships?company_id=${company.id}&limit=5&offset=0`);
    let release!: () => void;
    let report!: (revision: number) => void;
    const held = new Promise<void>((resolve) => { release = resolve; });
    const captured = new Promise<number>((resolve) => { report = resolve; });
    await page.route(registry, async (route) => {
      const reply = await route.fetch();
      expect(reply.status()).toBe(status);
      expect(reply.headers()["x-organization-membership-id"]).toBe(status === 404 ? undefined : original);
      report(Number(reply.headers()["x-computer-pool-check-revision"]));
      await held;
      await route.fulfill({ response: reply });
    }, { times: 1 });
    try {
      if (status === 404) expect((await request.delete(`${API}/memberships/${original}`, { headers: headers(owner) })).status()).toBe(204);
      if (status === 403) expect((await request.patch(`${API}/memberships/${original}`, {
        headers: headers(owner), data: { role: "user", permissions: ["members.read", "resources.read", "resources.manage"] },
      })).status()).toBe(200);
      await page.evaluate(() => window.dispatchEvent(new Event("online")));
      const earlierRevision = await captured;
      await expect(retry).toBeVisible();
      let current = original;
      if (renewed || status !== 404) {
        if (renewed) {
          if (status !== 404) expect((await request.delete(`${API}/memberships/${original}`, { headers: headers(owner) })).status()).toBe(204);
          current = await join();
          expect(current).not.toBe(original);
          if (candidateId && opened) {
            expect((await request.delete(`${API}/invitations/${candidateId}`, { headers: headers(owner) })).status()).toBe(204);
          } else if (candidateId) await invite("invitation-after-rejoining@example.com");
        } else await createCompany(request, owner, "Ещё одна команда владельца");
        overviewAvailable = true;
        const checked = page.waitForResponse((response) => response.url() === overview && response.status() === 200);
        await retry.click();
        const proof = await checked;
        expect(proof.headers()["x-organization-membership-id"]).toBe(current);
        expect(Number((await proof.json()).check_revision)).toBeGreaterThan(earlierRevision);
        await expect(card).toBeVisible();
      }
      const delivered = page.waitForResponse((response) => response.url() === registry && response.status() === status &&
        response.headers()["x-organization-membership-id"] === (status === 404 ? undefined : original));
      const refreshed = renewed ? page.waitForResponse((response) => response.url() === registry && response.status() === 200 &&
        response.headers()["x-organization-membership-id"] === current) : null;
      release();
      await (await delivered).finished();
      if (refreshed) await (await refreshed).finished();
      if (renewed || status !== 404) {
        await expect(title).toBeVisible();
        await expect(card).toBeVisible();
        if (status === 200) {
          if (opened) await expect(candidateRow.locator(".organization-invitation-status"))
            .toHaveClass(new RegExp(renewed ? "\\brevoked\\b" : "\\bpending\\b"));
          else await expect(tab).toHaveText(new RegExp(`Приглашения.*${renewed ? 3 : 2}`));
        } else if (opened) {
          if (status === 403 && !renewed) await expect(row).toHaveCount(0);
          else await expect(row).toBeVisible();
        } else await expect(tab).toHaveText(/Приглашения.*1/);
      } else {
        await expect(title).toHaveCount(0);
        await expect(card).toHaveCount(0);
        metadataAvailable = true;
        await page.evaluate(() => window.dispatchEvent(new Event("online")));
        await expect(page).toHaveURL(/\/organizations$/);
      }
    } finally {
      release();
    }
  });
}

test("the invitation registry restarts pagination when its membership changes between pages", async ({ page, request, account }) => {
  const owner = await makeAccount(request);
  const company = await createCompany(request, owner, "Команда с двумя страницами приглашений");
  const join = () => addMember(request, owner, company.id, account, "admin");
  const original = await join();
  const invite = async (identifier: string) => {
    const response = await request.post(`${API}/invitations`, {
      headers: headers(owner), data: { company_id: company.id, identifier, role: "user" },
    });
    expect(response.status()).toBe(201);
    return (await response.json() as { id: string }).id;
  };
  for (let index = 0; index < 200; index++) await invite(`invite-${index}@example.com`);
  const contact = "updated-between-pages@example.com";
  const invitation = await invite(contact);
  const computer = await addComputer(request, account, "Mac для команды", company.id);
  await markAccountComputer(request, computer.id);
  await startSession(page, account);
  await page.goto("compute/computers");
  await page.getByRole("link", { name: company.name, exact: true }).click();
  await expect(page.getByRole("tab", { name: "Приглашения", exact: true })).toHaveText(/201/);
  const registry = `${API}/invitations?company_id=${company.id}&include_counts=false&limit=200`;
  const firstPage = `${registry}&offset=0`;
  const secondPage = `${registry}&offset=200`;
  let release!: () => void;
  let report!: () => void;
  const held = new Promise<void>((resolve) => { release = resolve; });
  const pending = new Promise<void>((resolve) => { report = resolve; });
  await page.route(secondPage, async (route) => {
    report();
    await held;
    await route.continue();
  }, { times: 1 });
  try {
    const firstRead = page.waitForResponse((response) => response.url() === firstPage && response.status() === 200);
    await switchOrganizationPeople(page, "Приглашения");
    const first = await firstRead;
    expect(first.headers()["x-organization-membership-id"]).toBe(original);
    expect((await first.json() as Array<{ id: string; status: string }>).find((row) => row.id === invitation)?.status).toBe("pending");
    await pending;
    expect((await request.delete(`${API}/memberships/${original}`, { headers: headers(owner) })).status()).toBe(204);
    const current = await join();
    expect(current).not.toBe(original);
    expect((await request.delete(`${API}/invitations/${invitation}`, { headers: headers(owner) })).status()).toBe(204);
    const secondRead = page.waitForResponse((response) => response.url() === secondPage && response.status() === 200);
    const restarted = page.waitForResponse((response) => response.url() === firstPage && response.status() === 200 &&
      response.headers()["x-organization-membership-id"] === current);
    release();
    expect((await secondRead).headers()["x-organization-membership-id"]).toBe(current);
    await (await restarted).finished();
    await expect(page.getByRole("article", { name: contact, exact: true }).locator(".organization-invitation-status")).toHaveClass(/\brevoked\b/);
    await expect(page.getByRole("article", { name: "invite-0@example.com", exact: true })).toBeVisible();
  } finally {
    release();
  }
});

for (const [path, status, renewed] of [
  ["summary", 200, false], ["summary", 200, true], ["summary", 404, true], ["history", 404, true], ["history", 404, false],
  ["summary", 403, false], ["summary", 403, true], ["history", 403, false], ["history", 403, true],
] as const) test(`the legacy chart applies a delayed ${path} ${status} only for current membership${renewed ? status === 403 ? " after restoring reading" : " after rejoining" : ""}`, async ({ page, request, account }) => {
  const owner = await makeAccount(request);
  const company = await createCompany(request, owner, "Команда в каталоге пулов");
  const permissions = ["resources.read", "resources.manage"];
  const join = () => addMember(request, owner, company.id, account, "user", permissions);
  const original = await join();
  const setRead = async (read: boolean) => {
    expect((await request.patch(`${API}/memberships/${original}`, {
      headers: headers(owner), data: { permissions: read ? permissions : ["resources.manage"] },
    })).status()).toBe(200);
  };
  await addComputer(request, account, "Mac команды", company.id);
  await startSession(page, account);
  await page.goto("compute/pools");
  await page.getByRole("link", { name: `Открыть пул ${company.name}`, exact: true }).click();
  const title = page.getByRole("heading", { name: company.name, exact: true });
  const count = page.getByTestId("computer-count");
  const inventory = page.getByRole("link", { name: /^Список компьютеров/ });
  const metadataRetry = page.locator(".compute-pools > .error-box").getByRole("button", { name: "Попробовать снова ↻", exact: true });
  await expect(count).toHaveText("1");
  const extra = status === 200 ? await addComputer(request, account, "Временный Mac", company.id) : null;
  const metadata = `${API}/companies/${company.id}`;
  const summary = `${metadata}/resources/summary?only_available=true`;
  const history = `${metadata}/resources/availability-history?*`;
  const target = path === "summary" ? summary : history;
  const peer = path === "summary" ? history : summary;
  let metadataAvailable = false;
  await temporaryReadFailure(page, metadata, () => !metadataAvailable);
  let releasePeer!: () => void;
  const heldPeer = new Promise<void>((resolve) => { releasePeer = resolve; });
  if (status !== 200) {
    if (renewed) await page.route(peer, async (route) => {
      await heldPeer;
      await route.continue();
    });
    else await temporaryReadFailure(page, peer);
  }
  let release!: () => void;
  let report!: (proof: { url: string; revision: number }) => void;
  const held = new Promise<void>((resolve) => { release = resolve; });
  const captured = new Promise<{ url: string; revision: number }>((resolve) => { report = resolve; });
  await page.route(target, async (route) => {
    const reply = await route.fetch();
    expect(reply.status()).toBe(status);
    expect(reply.headers()["x-organization-membership-id"]).toBe(status === 404 ? undefined : original);
    if (status === 200) expect((await reply.json()).computer_count).toBe(2);
    report({ url: route.request().url(), revision: Number(reply.headers()["x-computer-pool-check-revision"]) });
    await held;
    await route.fulfill({ response: reply });
  }, { times: 1 });
  try {
    if (status === 404) expect((await request.delete(`${API}/memberships/${original}`, { headers: headers(owner) })).status()).toBe(204);
    if (status === 403) await setRead(false);
    await page.evaluate(() => window.dispatchEvent(new Event("online")));
    const older = await captured;
    await expect(metadataRetry).toBeVisible();
    if (renewed || status === 200) {
      let member = original;
      if (renewed) {
        if (status === 403) await setRead(true);
        else {
          if (status === 200) expect((await request.delete(`${API}/memberships/${original}`, { headers: headers(owner) })).status()).toBe(204);
          if (extra) expect((await request.delete(`${API}/computers/${extra.id}`, { headers: headers(owner) })).status()).toBe(204);
          member = await join();
          expect(member).not.toBe(original);
        }
      } else await createCompany(request, owner, "Другая команда владельца");
      metadataAvailable = true;
      releasePeer();
      const current = page.waitForResponse((response) => response.url() === metadata && response.status() === 200);
      await metadataRetry.click();
      const proof = await current;
      expect((await proof.json()).my_membership_id).toBe(member);
      expect(Number(proof.headers()["x-computer-pool-check-revision"])).toBeGreaterThan(older.revision);
      await expect(metadataRetry).toHaveCount(0);
    }
    const delivered = page.waitForResponse((response) => response.url() === older.url && response.status() === status);
    const prefix = path === "history" ? `${metadata}/resources/availability-history?` : summary;
    const refreshed = renewed ? page.waitForResponse((response) => response.url().startsWith(prefix) && response.status() === 200 &&
      Number(response.headers()["x-computer-pool-check-revision"]) > older.revision) : null;
    release();
    await (await delivered).finished();
    if (refreshed) await (await refreshed).finished();
    await expect(title).toBeVisible();
    if (renewed || status === 200) {
      await expect(count).toHaveText(renewed ? "1" : "2");
      await expect(inventory).toBeVisible();
    } else {
      await expect(inventory).toHaveCount(0);
      await expect(count).toHaveCount(0);
    }
  } finally {
    release();
    releasePeer();
  }
});

for (const restored of [false, true]) test(`legacy metadata ${restored ? "keeps inventory focus after a newer read confirmation" : "moves focus after a current revocation"}`, async ({ page, request, account }) => {
  const owner = await makeAccount(request);
  const company = await createCompany(request, owner, "Команда с проверенным чтением");
  const membership = await addMember(request, owner, company.id, account, "user", ["resources.read", "members.read"]);
  await addComputer(request, owner, "Mac команды", company.id);
  await startSession(page, account);
  await page.goto("compute/pools");
  await page.getByRole("link", { name: `Открыть пул ${company.name}`, exact: true }).click();
  const inventory = page.getByRole("link", { name: /^Список компьютеров/ });
  const card = page.getByRole("region", { name: company.name, exact: true });
  const metadata = `${API}/companies/${company.id}`;
  const summary = `${metadata}/resources/summary?only_available=true`;
  const history = `${metadata}/resources/availability-history?`;
  const setRead = async (read: boolean) => {
    expect((await request.patch(`${API}/memberships/${membership}`, {
      headers: headers(owner), data: { permissions: read ? ["resources.read", "members.read"] : ["members.read"] },
    })).status()).toBe(200);
  };
  await expect(card.getByTestId("computer-count")).toHaveText("1");
  await inventory.focus();
  const detail = await holdPoolRead(page, metadata);
  let release!: () => void;
  const held = new Promise<void>((resolve) => { release = resolve; });
  for (const endpoint of [summary, `${history}*`]) {
    if (restored) await page.route(endpoint, async (route) => {
      await held;
      await route.continue();
    });
    else await temporaryReadFailure(page, endpoint);
  }
  try {
    await setRead(false);
    await page.evaluate(() => window.dispatchEvent(new Event("online")));
    detail.send();
    const old = await detail.captured;
    expect(old.status()).toBe(200);
    const body = await old.json();
    expect(body.my_membership_id).toBe(membership);
    expect(body.my_permissions).not.toContain("resources.read");
    const earlier = Number(old.headers()["x-computer-pool-check-revision"]);
    if (restored) {
      await setRead(true);
      const currentSummary = page.waitForResponse((response) => response.url() === summary && response.status() === 200);
      const currentHistory = page.waitForResponse((response) => response.url().startsWith(history) && response.status() === 200);
      release();
      for (const proof of await Promise.all([currentSummary, currentHistory])) {
        expect(proof.headers()["x-organization-membership-id"]).toBe(membership);
        expect(Number(proof.headers()["x-computer-pool-check-revision"])).toBeGreaterThan(earlier);
        await proof.finished();
      }
      await expect(card).not.toHaveAttribute("aria-busy", "true");
    } else await expect(card.getByRole("button", { name: "Попробовать снова ↻", exact: true })).toBeVisible();
    await expect(inventory).toBeFocused();
    const delivered = page.waitForResponse((response) => response.url() === metadata && response.status() === 200);
    detail.deliver();
    await (await delivered).finished();
    await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
    if (restored) {
      await expect(inventory).toBeFocused();
      await expect(card.getByTestId("computer-count")).toHaveText("1");
    } else {
      await expect(inventory).toHaveCount(0);
      await expect(page.getByRole("link", { name: "← К списку пулов", exact: true })).toBeFocused();
    }
  } finally {
    detail.send();
    detail.deliver();
    release();
  }
});

for (const restored of [false, true]) test(`legacy history ${restored ? "retries a summary denied before restoring access" : "keeps a current summary denial"}`, async ({ page, request, account }) => {
  const owner = await makeAccount(request);
  const company = await createCompany(request, owner, "Команда с независимыми отчётами");
  const membership = await addMember(request, owner, company.id, account, "user", ["resources.read", "members.read"]);
  await addComputer(request, owner, "Mac команды", company.id);
  await startSession(page, account);
  await page.goto("compute/pools");
  await page.getByRole("link", { name: `Открыть пул ${company.name}`, exact: true }).click();
  const inventory = page.getByRole("link", { name: /^Список компьютеров/ });
  const card = page.getByRole("region", { name: company.name, exact: true });
  const count = card.getByTestId("computer-count");
  const metadata = `${API}/companies/${company.id}`;
  const summary = `${metadata}/resources/summary?only_available=true`;
  const history = `${metadata}/resources/availability-history?`;
  const setRead = async (read: boolean) => {
    expect((await request.patch(`${API}/memberships/${membership}`, {
      headers: headers(owner), data: { permissions: read ? ["resources.read", "members.read"] : ["members.read"] },
    })).status()).toBe(200);
  };
  await expect(inventory).toBeVisible();
  await expect(count).toHaveText("1");
  await temporaryReadFailure(page, metadata);
  let release!: () => void;
  const held = new Promise<void>((resolve) => { release = resolve; });
  await page.route(`${history}*`, async (route) => {
    await held;
    await route.continue();
  }, { times: 1 });
  try {
    await setRead(false);
    const denied = page.waitForResponse((response) => response.url() === summary && response.status() === 403);
    await page.evaluate(() => window.dispatchEvent(new Event("online")));
    const denial = await denied;
    await expect(inventory).toHaveCount(0);
    await expect(count).toHaveCount(0);
    await expect(card.getByRole("alert")).toContainText("У вас нет прав на это действие.");

    if (restored) await setRead(true);
    const graphed = page.waitForResponse((response) => response.url().startsWith(history) && response.status() === (restored ? 200 : 403));
    const retried = restored ? page.waitForResponse((response) => response.url() === summary && response.status() === 200) : null;
    release();
    const proof = await graphed;
    expect(proof.headers()["x-organization-membership-id"]).toBe(membership);
    if (retried) {
      expect(Number(proof.headers()["x-computer-pool-check-revision"]))
        .toBeGreaterThan(Number(denial.headers()["x-computer-pool-check-revision"]));
      await (await retried).finished();
      await expect(inventory).toBeVisible();
      await expect(count).toHaveText("1");
      await expect(card.getByRole("alert")).toHaveCount(0);
    } else {
      await expect(inventory).toHaveCount(0);
      await expect(count).toHaveCount(0);
    }
  } finally {
    release();
  }
});

for (const confirmation of ["all reports", "only metadata", "only the summary"] as const) test(`the legacy chart recovers its inventory link with ${confirmation}`, async ({ page, request, account }) => {
  const owner = await makeAccount(request);
  const company = await createCompany(request, owner, "Команда с восстановленным чтением");
  const membership = await addMember(request, owner, company.id, account, "user", ["resources.read", "members.read"]);
  await addComputer(request, owner, "Mac команды", company.id);
  await startSession(page, account);
  await page.goto("compute/pools");
  await page.getByRole("link", { name: `Открыть пул ${company.name}`, exact: true }).click();
  const inventory = page.getByRole("link", { name: /^Список компьютеров/ });
  const card = page.getByRole("region", { name: company.name, exact: true });
  const count = card.getByTestId("computer-count");
  const metadata = `${API}/companies/${company.id}`;
  const summary = `${metadata}/resources/summary?only_available=true`;
  const history = `${metadata}/resources/availability-history?`;
  const setRead = async (read: boolean) => {
    expect((await request.patch(`${API}/memberships/${membership}`, {
      headers: headers(owner), data: { permissions: read ? ["resources.read", "members.read"] : ["members.read"] },
    })).status()).toBe(200);
  };
  await expect(count).toHaveText("1");
  let metadataAvailable = false;
  await temporaryReadFailure(page, metadata, () => !metadataAvailable);
  await setRead(false);
  const denied = page.waitForResponse((response) => response.url() === summary && response.status() === 403);
  await page.evaluate(() => window.dispatchEvent(new Event("online")));
  const earlier = Number((await denied).headers()["x-computer-pool-check-revision"]);
  await expect(inventory).toHaveCount(0);
  await expect(count).toHaveCount(0);

  await setRead(true);
  metadataAvailable = confirmation !== "only the summary";
  if (confirmation === "only metadata") await temporaryReadFailure(page, summary);
  if (confirmation !== "all reports") await temporaryReadFailure(page, `${history}*`);
  const verifiedPath = confirmation === "only the summary" ? summary : metadata;
  const verified = page.waitForResponse((response) => response.url() === verifiedPath && response.status() === 200);
  await page.getByRole("button", { name: "Обновить", exact: true }).click();
  const proof = await verified;
  expect(Number(proof.headers()["x-computer-pool-check-revision"])).toBeGreaterThan(earlier);
  await expect(inventory).toBeVisible();
  if (confirmation === "all reports") await expect(count).toHaveText("1");
  else await expect(card.getByRole("button", { name: "Попробовать снова ↻", exact: true })).toBeVisible();
});

test("the legacy chart keeps a newer reading denial when an older summary succeeds", async ({ page, request, account }) => {
  const owner = await makeAccount(request);
  const company = await createCompany(request, owner, "Команда с отозванным чтением");
  const membership = await addMember(request, owner, company.id, account, "user", ["resources.read", "members.read"]);
  await addComputer(request, owner, "Mac команды", company.id);
  await startSession(page, account);
  await page.goto("compute/pools");
  await page.getByRole("link", { name: `Открыть пул ${company.name}`, exact: true }).click();
  const inventory = page.getByRole("link", { name: /^Список компьютеров/ });
  const metadata = `${API}/companies/${company.id}`;
  const summary = `${metadata}/resources/summary?only_available=true`;
  const history = `${metadata}/resources/availability-history?`;
  await expect(page.getByTestId("computer-count")).toHaveText("1");
  await temporaryReadFailure(page, metadata);
  let releaseSummary!: () => void;
  let releaseHistory!: () => void;
  let report!: (revision: number) => void;
  const heldSummary = new Promise<void>((resolve) => { releaseSummary = resolve; });
  const heldHistory = new Promise<void>((resolve) => { releaseHistory = resolve; });
  const captured = new Promise<number>((resolve) => { report = resolve; });
  await page.route(`${history}*`, async (route) => {
    await heldHistory;
    await route.continue();
  });
  await page.route(summary, async (route) => {
    const reply = await route.fetch();
    expect(reply.status()).toBe(200);
    report(Number(reply.headers()["x-computer-pool-check-revision"]));
    await heldSummary;
    await route.fulfill({ response: reply });
  }, { times: 1 });
  try {
    await page.evaluate(() => window.dispatchEvent(new Event("online")));
    const earlier = await captured;
    expect((await request.patch(`${API}/memberships/${membership}`, {
      headers: headers(owner), data: { permissions: ["members.read"] },
    })).status()).toBe(200);
    const denied = page.waitForResponse((response) => response.url().startsWith(history) && response.status() === 403);
    releaseHistory();
    expect(Number((await denied).headers()["x-computer-pool-check-revision"])).toBeGreaterThan(earlier);
    await expect(inventory).toHaveCount(0);
    const delivered = page.waitForResponse((response) => response.url() === summary && response.status() === 200);
    releaseSummary();
    await (await delivered).finished();
    await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
    await expect(inventory).toHaveCount(0);
    await expect(page.getByTestId("computer-count")).toHaveCount(0);
  } finally {
    releaseHistory();
    releaseSummary();
  }
});

for (const newestFirst of [false, true]) test(`the legacy chart keeps the latest denial when reports arrive ${newestFirst ? "newest first" : "in order"}`, async ({ page, request, account }) => {
  const owner = await makeAccount(request);
  const company = await createCompany(request, owner, "Команда с обновлениями чтения");
  const membership = await addMember(request, owner, company.id, account, "user", ["resources.read", "members.read"]);
  await addComputer(request, owner, "Mac команды", company.id);
  await startSession(page, account);
  await page.goto("compute/pools");
  await page.getByRole("link", { name: `Открыть пул ${company.name}`, exact: true }).click();
  const inventory = page.getByRole("link", { name: /^Список компьютеров/ });
  const metadata = `${API}/companies/${company.id}`;
  const summary = `${metadata}/resources/summary?only_available=true`;
  const history = `${metadata}/resources/availability-history?`;
  const setRead = async (read: boolean) => {
    expect((await request.patch(`${API}/memberships/${membership}`, {
      headers: headers(owner), data: { permissions: read ? ["resources.read", "members.read"] : ["members.read"] },
    })).status()).toBe(200);
  };
  await expect(page.getByTestId("computer-count")).toHaveText("1");
  await page.route(metadata, (route) => route.continue());
  const [earlier, intermediate, latest] = await Promise.all([
    holdPoolRead(page, summary), holdPoolRead(page, metadata), holdPoolRead(page, `${history}*`),
  ]);
  try {
    await setRead(false);
    await page.evaluate(() => window.dispatchEvent(new Event("online")));
    earlier.send();
    const earlierDenial = await earlier.captured;
    expect(earlierDenial.status()).toBe(403);
    expect(earlierDenial.headers()["x-organization-membership-id"]).toBe(membership);
    await setRead(true);
    intermediate.send();
    const grant = await intermediate.captured;
    expect(grant.status()).toBe(200);
    expect(await grant.json()).toMatchObject({ my_membership_id: membership, my_permissions: expect.arrayContaining(["resources.read"]) });
    await setRead(false);
    latest.send();
    const latestDenial = await latest.captured;
    expect(latestDenial.status()).toBe(403);
    expect(latestDenial.headers()["x-organization-membership-id"]).toBe(membership);
    const revision = "x-computer-pool-check-revision";
    expect(Number(grant.headers()[revision])).toBeGreaterThan(Number(earlierDenial.headers()[revision]));
    expect(Number(latestDenial.headers()[revision])).toBeGreaterThan(Number(grant.headers()[revision]));
    for (const read of newestFirst ? [latest, earlier] : [earlier, latest]) {
      const delivered = page.waitForResponse((response) => response.status() === 403 &&
        (read === earlier ? response.url() === summary : response.url().startsWith(history)));
      read.deliver();
      await (await delivered).finished();
      await expect(inventory).toHaveCount(0);
    }
    const checked = page.waitForResponse((response) => response.url() === metadata && response.status() === 200);
    intermediate.deliver();
    await (await checked).finished();
    await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
    await expect(inventory).toHaveCount(0);
    await expect(page.getByTestId("computer-count")).toHaveCount(0);
  } finally {
    for (const read of [earlier, intermediate, latest]) {
      read.send();
      read.deliver();
    }
  }
});

for (const recovered of [false, true]) test(`leaving uses the membership verified by the pool ${recovered ? "when metadata recovers during confirmation" : "when metadata is unavailable"}`, async ({ page, request, account }) => {
  const owner = await makeAccount(request);
  const company = await createCompany(request, owner, "Пул с восстановленным участием");
  const permissions = ["resources.read", "resources.manage", "members.read"];
  const previous = await addMember(request, owner, company.id, account, "user", permissions);
  const computer = await addComputer(request, account, "Mac до повторного вступления", company.id);
  await markAccountComputer(request, computer.id);
  await startSession(page, account);
  await page.goto("compute/computers");
  await page.getByRole("link", { name: company.name, exact: true }).click();
  const cards = page.locator("a.organization-computer-card");
  await expect(cards.filter({ hasText: computer.name })).toBeVisible();
  const endpoint = `${API}/companies/${company.id}`;
  await temporaryReadFailure(page, endpoint);
  expect((await request.delete(`${API}/memberships/${previous}`, { headers: headers(owner) })).status()).toBe(204);
  const current = await addMember(request, owner, company.id, account, "user", permissions);
  const renamed = "Mac после повторного вступления перед выходом";
  expect((await request.patch(`${API}/computers/${computer.id}`, {
    headers: headers(account), data: { name: renamed },
  })).status()).toBe(200);
  const verified = page.waitForResponse((response) => response.url() === `${endpoint}/resources/pool-overview` &&
    response.status() === 200 && response.headers()["x-organization-membership-id"] === current);
  await page.evaluate(() => window.dispatchEvent(new Event("online")));
  await (await verified).finished();
  await expect(cards.filter({ hasText: renamed })).toBeVisible();
  const leaveButton = (settings: Locator) => settings.getByRole("button", { name: "Выйти из организации", exact: true });
  const leave = leaveButton(await openOrganizationSettings(page));
  await leave.click();
  const confirm = page.getByRole("dialog", { name: "Подтвердите выход", exact: true });
  const phrase = confirm.getByRole("textbox");
  await phrase.fill(account.first_name);
  if (recovered) {
    await page.unroute(endpoint);
    const checked = page.waitForResponse((response) => response.url() === endpoint && response.status() === 200);
    await page.evaluate(() => window.dispatchEvent(new Event("online")));
    const reply = await checked;
    expect((await reply.json()).my_membership_id).toBe(current);
    await expect(page.locator(".organization-settings-dialog")).toHaveCount(0);
    await expect(phrase).toHaveValue(account.first_name);
    await expect(phrase).toBeFocused();
  }
  await confirm.getByRole("button", { name: "Отмена", exact: true }).click();
  await expect(confirm).toHaveCount(0);
  await expect(recovered ? page.getByRole("button", { name: "Настройки организации", exact: true }) : leave).toBeFocused();
  const afterCancel = await request.get(endpoint, { headers: headers(account) });
  expect(afterCancel.status()).toBe(200);
  expect((await afterCancel.json()).my_membership_id).toBe(current);
  await leaveButton(await openOrganizationSettings(page)).click();
  await phrase.fill(account.first_name);
  const left = page.waitForResponse((response) => response.url().startsWith(`${API}/memberships/`) &&
    response.request().method() === "DELETE");
  await confirm.getByRole("button", { name: "Подтвердить", exact: true }).click();
  const result = await left;
  expect(result.url()).toBe(`${API}/memberships/${current}`);
  expect(result.status()).toBe(204);
  await expect(page).toHaveURL(/\/organizations$/);
  expect((await request.get(endpoint, { headers: headers(account) })).status()).toBe(404);
});

for (const fragment of [false, true]) test(`the roster search preserves leave confirmation${fragment ? " after skipping to content" : ""} and a history navigation dismisses it`, async ({ page, request, account }) => {
  const owner = await makeAccount(request);
  const company = await createCompany(request, owner, "Пул с поиском участников");
  const membership = await addMember(request, owner, company.id, account, "user", ["resources.read", "resources.manage", "members.read"]);
  for (let index = 0; index < 4; index++) {
    const colleague = await makeAccount(request, `Участник ${index}`);
    await addMember(request, owner, company.id, colleague, "user");
  }
  const computer = await addComputer(request, account, "Mac из команды", company.id);
  await markAccountComputer(request, computer.id);
  await startSession(page, account);
  await page.goto("compute/computers");
  await page.getByRole("link", { name: company.name, exact: true }).click();
  if (fragment) await page.getByRole("link", { name: "Перейти к содержимому", exact: true }).press("Enter");
  const anchor = fragment ? "#main" : "";
  const overview = new RegExp(`/organizations/${company.id}${anchor}$`);
  const members = new RegExp(`/organizations/${company.id}/members$`);
  await expect(page).toHaveURL(overview);
  await page.locator(`a[href^="/organizations/${company.id}/members"]`).click();
  await expect(page).toHaveURL(members);
  await page.goBack();
  await expect(page).toHaveURL(overview);
  const search = page.getByRole("searchbox", { name: "Найти участника", exact: true });
  await expect(search).toBeVisible();
  await page.clock.install();
  const now = await page.evaluate(() => Date.now());
  await page.clock.pauseAt(now + 1_000);
  await search.fill("Ivan");
  await page.getByRole("button", { name: "Настройки организации", exact: true }).press("Enter");
  const settings = page.getByRole("dialog", { name: "Настройки организации", exact: true });
  await settings.getByRole("button", { name: "Выйти из организации", exact: true }).press("Enter");
  const confirm = page.getByRole("dialog", { name: "Подтвердите выход", exact: true });
  const phrase = confirm.getByRole("textbox");
  await phrase.fill(account.first_name);
  await expect(page).toHaveURL(overview);
  await page.clock.runFor(300);
  const searchedOverview = new RegExp(`/organizations/${company.id}\\?q=Ivan${anchor}$`);
  await expect(page).toHaveURL(searchedOverview);
  await expect(phrase).toHaveValue(account.first_name);
  await expect(phrase).toBeFocused();
  await page.clock.resume();

  await page.goForward();
  await expect(page).toHaveURL(members);
  await expect(confirm).toHaveCount(0);
  await page.goBack();
  await expect(page).toHaveURL(searchedOverview);
  await expect(page.getByRole("heading", { name: `${company.name}.`, exact: true })).toBeVisible();
  await expect(confirm).toHaveCount(0);
  const current = await request.get(`${API}/companies/${company.id}`, { headers: headers(account) });
  expect(current.status()).toBe(200);
  expect((await current.json()).my_membership_id).toBe(membership);
});

for (const [fragment, travel] of [
  [false, "another organization"], [true, "another organization"], [true, "a new visit"], [true, "the original history entry"],
] as const) test(`a pending roster search stays in its organization when navigating to ${travel}${fragment ? " after skipping to content" : ""}`, async ({ page, request, account }) => {
  const first = await createCompany(request, account, "Команда с начатым поиском");
  const second = travel === "another organization" ? await createCompany(request, account, "Другая команда") : first;
  for (const company of new Set([first, second])) {
    const computer = await addComputer(request, account, `Mac ${company.name}`, company.id);
    await markAccountComputer(request, computer.id);
  }
  await startSession(page, account);
  await page.goto("compute/computers");
  const enter = (name: string) => page.getByRole("link", { name, exact: true }).press("Enter");
  const skip = async () => {
    await enter("Перейти к содержимому");
    await expect(page.getByRole("main")).toBeFocused();
  };
  const search = page.getByRole("searchbox", { name: "Найти участника", exact: true });
  const suffix = fragment ? "#main" : "";
  await enter(first.name);
  await expect(search).toHaveValue("");
  if (fragment) await skip();
  const original = new RegExp(`/organizations/${first.id}${suffix}$`);
  await expect(page).toHaveURL(original);
  await page.clock.install();
  await page.clock.pauseAt(await page.evaluate(() => Date.now() + 1_000));
  await search.fill("Ivan");
  await expect(search).toHaveValue("Ivan");
  await expect(page).toHaveURL(original);
  if (fragment) {
    await page.goBack();
    await expect(page).toHaveURL(new RegExp(`/organizations/${first.id}$`));
  }
  await page.goBack();
  await expect(page).toHaveURL(/\/compute\/computers$/);
  if (travel === "the original history entry") {
    await page.goForward();
    await expect(page).toHaveURL(new RegExp(`/organizations/${first.id}$`));
    await page.goForward();
    await expect(page).toHaveURL(original);
    await expect(search).toHaveValue("Ivan");
    await page.clock.runFor(300);
    await expect(page).toHaveURL(new RegExp(`/organizations/${first.id}\\?q=Ivan${suffix}$`));
    return;
  }
  await enter(second.name);
  await expect(search).toHaveValue("");
  if (fragment) await skip();
  const destination = new RegExp(`/organizations/${second.id}${suffix}$`);
  await expect(page).toHaveURL(destination);
  await expect(search).toHaveValue("");
  await page.clock.runFor(300);
  await expect(page).toHaveURL(destination);
  await expect(search).toHaveValue("");
});

test("a computer detail ignores an older confirmed exit after verifying the renewed membership", async ({
  page, request, account, context,
}) => {
  const owner = await makeAccount(request);
  const company = await createCompany(request, owner, "Команда на карточке");
  const membership = await addMember(request, owner, company.id, account, "user", ["resources.read", "resources.manage"]);
  const computer = await addComputer(request, account, "Командный Mac", company.id);
  await markAccountComputer(request, computer.id);
  await startSession(page, account);
  await page.goto("compute/computers");
  await page.getByRole("link", { name: company.name, exact: true }).click();
  await page.locator("a.organization-computer-card").click();
  const title = page.getByRole("heading", { name: computer.name });
  await expect(title).toBeVisible();
  const uri = `${API}/computers/${computer.id}`;
  const secondTab = await context.newPage();
  let release!: () => void;
  const held = new Promise<void>((resolve) => { release = resolve; });
  let confirmCommit!: () => void;
  const committed = new Promise<void>((resolve) => { confirmCommit = resolve; });
  try {
    await secondTab.goto(`organizations/${company.id}`);
    const settings = await openOrganizationSettings(secondTab);
    await settings.getByRole("button", { name: "Выйти из организации", exact: true }).click();
    const dialog = secondTab.getByRole("dialog", { name: "Подтвердите выход", exact: true });
    await dialog.getByRole("textbox").fill(account.first_name);
    await secondTab.route(`${API}/memberships/${membership}`, async (route) => {
      if (route.request().method() !== "DELETE") return route.continue();
      const result = await route.fetch();
      expect(result.status()).toBe(204);
      confirmCommit();
      await held;
      await route.fulfill({ response: result });
    });
    await dialog.getByRole("button", { name: "Подтвердить", exact: true }).click();
    await committed;
    const current = await addMember(request, owner, company.id, account, "user", ["resources.read", "resources.manage"]);
    const checked = page.waitForResponse((response) => response.url() === uri && response.status() === 200 &&
      response.headers()["x-organization-membership-id"] === current);
    await page.evaluate(() => window.dispatchEvent(new Event("online")));
    await (await checked).finished();
    await expect(title).toBeVisible();
    await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
    await temporaryReadFailure(page, uri);
    const outage = page.waitForResponse((response) => response.url() === uri && response.status() === 503);
    release();
    await expect(secondTab).toHaveURL(/\/organizations$/);
    await (await outage).finished();
    await expect(page.getByRole("button", { name: /Попробовать снова/ })).toBeVisible();
    await expect(title).toBeVisible();
  } finally {
    release();
    await secondTab.close();
  }
});

test("an authoritative displaced-card lookup denial hides the organization inventory immediately", async ({
  page, request, account, browserName, hasTouch,
}) => {
  test.skip(browserName !== "chromium" || !hasTouch, "Use native Chromium touch events");
  const owner = await makeAccount(request);
  const company = await createCompany(request, owner, "Команда с отзывом доступа");
  const membership = await addMember(request, owner, company.id, account, "user", ["resources.read", "resources.manage"]);
  const computers: { id: string }[] = [];
  for (let index = 1; index <= 4; index++) {
    const computer = await addComputer(request, account, `Mac ${index}`, undefined, false);
    await markAccountComputer(request, computer.id);
    computers.push(computer);
  }
  computers.sort((first, second) => first.id.localeCompare(second.id));
  const move = async (id: string) => {
    expect((await request.patch(`${API}/connected-computers/${id}/pool`, {
      headers: headers(account), data: { company_id: company.id, expected_company_id: null },
    })).status()).toBe(200);
  };
  for (const computer of computers.slice(1)) await move(computer.id);
  await startSession(page, account);
  await page.setViewportSize({ width: 414, height: 896 });
  await page.goto("compute/computers");
  await page.getByRole("link", { name: company.name, exact: true }).first().click();
  const cards = page.locator(".organization-computer-card");
  await expect(cards).toHaveCount(3);
  const target = cards.last();
  await expect(target).toHaveAttribute("href", new RegExp(computers[3].id));
  await target.evaluate((element) => element.scrollIntoView({ block: "center" }));
  const box = (await target.boundingBox())!;
  const touch = await page.context().newCDPSession(page);
  try {
    await touch.send("Input.dispatchTouchEvent", {
      type: "touchStart", touchPoints: [{ x: box.x + box.width / 2, y: box.y + box.height / 2 }],
    });
    await move(computers[0].id);
    await page.evaluate(() => window.dispatchEvent(new Event("online")));
    await expect(page.locator(".organization-overview dd").first()).toHaveText("4");
    expect((await request.patch(`${API}/memberships/${membership}`, {
      headers: headers(owner), data: { permissions: ["resources.manage"] },
    })).status()).toBe(200);
    const denied = page.waitForResponse((response) =>
      response.url().startsWith(`${API}/computers?`) && response.url().includes(`computer_id=${computers[3].id}`) && response.status() === 403);
    await touch.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
    await denied;
    await expect(cards).toHaveCount(0);
    await expect(page.getByRole("link", { name: "Список компьютеров", exact: true })).toHaveCount(0);
    await expect(page).toHaveURL(new RegExp(`/organizations/${company.id}$`));
  } finally {
    await touch.detach();
  }
});

for (const [revokedAccess, detailsUnavailable] of [["resource reading", true], ["resource reading", false], ["membership", false], ["membership", true]] as const) {
  test(`an organization opened from a computer pool clears access after external revocation of ${revokedAccess}${detailsUnavailable ? " during a details outage" : ""}`, async ({ page, request, account }) => {
    const owner = await makeAccount(request, "Создатель команды");
    const company = await createCompany(request, owner, "Команда с ограниченным доступом");
    const canReadMembers = revokedAccess === "membership" && detailsUnavailable;
    const id = await addMember(request, owner, company.id, account, "user", [
      "resources.read", "resources.manage", ...(canReadMembers ? ["members.read"] : []),
    ]);
    const computer = await addComputer(request, account, "Рабочий Mac", company.id);
    await markAccountComputer(request, computer.id);
    await startSession(page, account);
    await page.clock.install();
    await page.goto("compute/computers");
    await page.getByRole("link", { name: company.name, exact: true }).click();
    const title = page.getByRole("heading", { name: `${company.name}.`, exact: true });
    const inventory = page.getByRole("link", { name: "Список компьютеров" });
    const history = page.getByRole("region", { name: "История CPU" });
    const team = page.getByRole("region", { name: "Команда", exact: true });
    await expect(title).toBeVisible();
    await expect(history.locator("figure")).toBeVisible();
    if (canReadMembers) await expect(team).toContainText(owner.first_name);
    await inventory.focus();
    await expect(inventory).toBeFocused();

    if (revokedAccess === "resource reading") {
      const revoked = await request.patch(`${API}/memberships/${id}`, {
        headers: headers(owner), data: { permissions: ["resources.manage"] },
      });
      expect(revoked.status()).toBe(200);
    } else {
      const removed = await request.delete(`${API}/memberships/${id}`, { headers: headers(owner) });
      expect(removed.status()).toBe(204);
    }
    const details = `${API}/companies/${company.id}`;
    if (detailsUnavailable) {
      await page.route(details, (route) => route.fulfill({ status: 503, json: { detail: "Temporary outage" } }));
    }
    await page.clock.fastForward(61_000);
    await expect(inventory).toHaveCount(0);
    await expect(history).toHaveCount(0);
    if (revokedAccess === "resource reading") {
      await expect(page.getByRole("link", { name: /К организациям/ })).toBeFocused();
      await expect(title).toBeVisible();
      if (detailsUnavailable) {
        expect((await request.patch(`${API}/memberships/${id}`, {
          headers: headers(owner), data: { permissions: ["resources.read", "resources.manage"] },
        })).status()).toBe(200);
        await page.clock.fastForward(61_000);
        await expect(inventory).toBeVisible();
        await expect(history.locator("figure")).toBeVisible();
      } else {
        await expect(page.getByText("У вас нет права просматривать мощности этой организации.")).toBeVisible();
      }
    } else {
      await expect(title).toHaveCount(0);
      await expect(page.getByRole("button", { name: /Настройки организации/ })).toHaveCount(0);
      await expect(team).toHaveCount(0);
      if (detailsUnavailable) {
        await page.unroute(details);
        await page.clock.fastForward(61_000);
      }
      await expect(page).toHaveURL(/\/organizations$/);
      await page.goBack();
      await expect(page).toHaveURL(/\/compute\/computers$/);
      const row = page.locator(`[data-computer-id="${computer.id}"]`);
      await expect(row).toContainText("Пул недоступен");
      await expect(row).toBeFocused();
    }
  });
}

for (const scope of ["personal", "organization"] as const) {
  test(`the ${scope} pool restores focus when a focused computer moves out`, async ({ page, request, account }) => {
    const personal = scope === "personal";
    const company = await createCompany(request, account, "Команда для переноса");
    const companyId = personal ? undefined : company.id;
    const staying = await addComputer(request, account, "Mac остаётся", companyId);
    const moving = await addComputer(request, account, "Mac перемещается", companyId);
    await markAccountComputer(request, moving.id);
    await startSession(page, account);
    await page.clock.install();
    await page.goto("compute/computers");
    await page.getByRole("link", { name: personal ? "Личный пул" : company.name, exact: true }).click();
    const inventory = page.getByRole("region", { name: "Компьютеры", exact: true });
    const card = inventory.getByRole("link", { name: new RegExp(moving.name) });
    await card.focus();
    await expect(card).toBeFocused();
    expect((await request.patch(`${API}/connected-computers/${moving.id}/pool`, {
      headers: headers(account), data: { company_id: personal ? company.id : null, expected_company_id: companyId ?? null },
    })).status()).toBe(200);
    await page.clock.fastForward(61_000);
    await expect(card).toHaveCount(0);
    await expect(inventory.getByRole("link", { name: new RegExp(staying.name) })).toBeVisible();
    await expect(inventory.getByRole("link", { name: "Список компьютеров", exact: true })).toBeFocused();
  });

  test(`the ${scope} pool preserves the retry focus until recovery`, async ({ page, request, account }) => {
    const personal = scope === "personal";
    const company = await createCompany(request, account, "Команда с обновлением");
    const computer = await addComputer(request, account, "Рабочий Mac", personal ? undefined : company.id);
    await markAccountComputer(request, computer.id);
    await startSession(page, account);
    await page.clock.install();
    await page.goto("compute/computers");
    const pool = personal ? `users/${account.id}` : `companies/${company.id}`;
    const endpoint = `${API}/${pool}/resources/pool-overview`;
    await page.route(endpoint, async (route) => {
      const response = await route.fetch();
      await route.fulfill({ response, status: 503, json: { detail: "Temporary outage" } });
    });
    await page.getByRole("link", { name: personal ? "Личный пул" : company.name, exact: true }).click();
    const retry = page.getByRole("button", { name: /Попробовать снова/ });
    await retry.focus();
    await expect(retry).toBeFocused();
    const failed = page.waitForResponse((response) => response.url() === endpoint && response.status() === 503);
    await page.clock.fastForward(61_000);
    await failed;
    await expect(retry).toBeFocused();

    await page.unroute(endpoint);
    await page.clock.fastForward(61_000);
    const inventory = page.getByRole("region", { name: "Компьютеры", exact: true });
    await expect(inventory.getByRole("link", { name: /Рабочий Mac/ })).toBeVisible();
    await expect(inventory.getByRole("link", { name: "Список компьютеров", exact: true })).toBeFocused();
  });

  test(`the ${scope} history restores retry focus after a temporary outage`, async ({ page, request, account }) => {
    const personal = scope === "personal";
    const company = await createCompany(request, account, "Команда с историей");
    const computer = await addComputer(request, account, "Рабочий Mac", personal ? undefined : company.id);
    await markAccountComputer(request, computer.id);
    await startSession(page, account);
    await page.goto("compute/computers");
    await page.getByRole("link", { name: personal ? "Личный пул" : company.name, exact: true }).click();
    const endpoint = personal ? `${API}/users/${account.id}/resources/availability-history` : `${API}/companies/${company.id}/resources/pool-history`;
    const history = personal ? page.locator(".personal-pool-history") : page.getByRole("region", { name: "История CPU", exact: true });
    await expect(history.locator(personal ? ".chart-panel" : "figure")).toBeVisible();
    await page.route(endpoint, async (route) => {
      const response = await route.fetch();
      await route.fulfill({ response, status: 503, json: { detail: "Temporary outage" } });
    });
    await page.evaluate(() => window.dispatchEvent(new Event("focus")));
    const retry = history.getByRole("button", { name: /Попробовать снова/ });
    await retry.focus();
    await expect(retry).toBeFocused();
    await page.unroute(endpoint);
    await page.evaluate(() => window.dispatchEvent(new Event("focus")));
    await expect(retry).toHaveCount(0);
    await expect(history).toBeFocused();
  });
}

for (const revokedAccess of ["member reading", "membership"] as const) {
  test(`the member directory opened from a computer pool clears data after external revocation of ${revokedAccess}`, async ({ page, request, account }) => {
    const owner = await makeAccount(request, "Создатель команды");
    const company = await createCompany(request, owner, "Команда с каталогом");
    const id = await addMember(request, owner, company.id, account, "user", ["resources.read", "resources.manage", "members.read"]);
    for (let i = 1; i <= 4; i++) {
      const colleague = await makeAccount(request, `Участник ${i}`);
      await addMember(request, owner, company.id, colleague, "user");
    }
    const computer = await addComputer(request, account, "Командный Mac", company.id);
    await markAccountComputer(request, computer.id);
    await startSession(page, account);
    await page.clock.install();
    await page.goto("compute/computers");
    await page.getByRole("link", { name: company.name, exact: true }).click();
    await page.getByRole("link", { name: "Смотреть всех 6" }).click();
    const directoryURL = new RegExp(`/organizations/${company.id}/members$`);
    await expect(page).toHaveURL(directoryURL);
    const member = page.getByRole("article", { name: owner.first_name, exact: true });
    await expect(member).toBeVisible();

    if (revokedAccess === "member reading") {
      expect((await request.patch(`${API}/memberships/${id}`, {
        headers: headers(owner), data: { permissions: ["resources.read", "resources.manage"] },
      })).status()).toBe(200);
    } else {
      expect((await request.delete(`${API}/memberships/${id}`, { headers: headers(owner) })).status()).toBe(204);
    }
    await page.clock.fastForward(61_000);
    await expect(member).toHaveCount(0);
    if (revokedAccess === "member reading") {
      await expect(page).toHaveURL(directoryURL);
      await expect(page.getByText("У вас нет права просматривать участников этой организации.")).toBeVisible();
    } else {
      await expect(page).toHaveURL(/\/organizations$/);
    }
  });
}

for (const view of ["team preview", "member directory", "outgoing invitation list"] as const) {
  test(`the organization ${view} refreshes from the live collection and closes when it returns a membership denial`, async ({ page, request, account }) => {
    const owner = await makeAccount(request, "Создатель команды");
    const company = await createCompany(request, owner, "Команда для проверки доступа");
    const permissions = ["resources.manage", view === "outgoing invitation list" ? "members.manage" : "members.read"];
    const membershipId = await addMember(request, owner, company.id, account, "user", permissions);
    const computer = await addComputer(request, account, "Рабочий Mac", company.id);
    await markAccountComputer(request, computer.id);
    const invitee = `${owner.nickname}@example.net`;
    const colleagues: { person: Account; membershipId: string }[] = [];
    if (view === "outgoing invitation list") {
      expect((await request.post(`${API}/invitations`, {
        headers: headers(owner), data: { company_id: company.id, identifier: invitee, role: "user" },
      })).status()).toBe(201);
    } else if (view === "member directory") {
      for (let i = 1; i <= 4; i++) {
        const person = await makeAccount(request, `Коллега ${i}`);
        colleagues.push({ person, membershipId: await addMember(request, owner, company.id, person, "user") });
      }
    }
    await startSession(page, account);
    await page.clock.install();
    await page.goto("compute/computers");
    await page.getByRole("link", { name: company.name, exact: true }).click();
    await expect(page.getByText("У вас нет права просматривать мощности этой организации.")).toBeVisible();

    if (view === "outgoing invitation list") {
      await page.getByRole("tab", { name: /Приглашения/ }).click();
      await expect(page.getByRole("article", { name: invitee, exact: true })).toBeVisible();
    } else if (view === "member directory") {
      await page.getByRole("link", { name: "Смотреть всех 6" }).click();
      const removed = colleagues[0];
      const row = page.getByRole("article", { name: removed.person.first_name, exact: true });
      await expect(row).toBeVisible();
      expect((await request.delete(`${API}/memberships/${removed.membershipId}`, { headers: headers(owner) })).status()).toBe(204);
      await page.clock.fastForward(61_000);
      await expect(row).toHaveCount(0);
      await expect(page.getByText("Участники 5", { exact: true })).toBeVisible();
    } else {
      await expect(page.getByRole("article", { name: owner.first_name, exact: true })).toBeVisible();
      const person = await makeAccount(request, "Новый участник");
      await addMember(request, owner, company.id, person, "user");
      await page.clock.fastForward(61_000);
      await expect(page.getByRole("article", { name: person.first_name, exact: true })).toBeVisible();
    }
    const details = `${API}/companies/${company.id}`;
    await page.route(details, (route) => route.fulfill({ status: 503, json: { detail: "Temporary outage" } }));
    expect((await request.delete(`${API}/memberships/${membershipId}`, { headers: headers(owner) })).status()).toBe(204);
    await page.clock.fastForward(61_000);
    await expect(page.locator(".organization-detail")).toHaveCount(0);
    await page.unroute(details);
    await page.clock.fastForward(61_000);
    await expect(page).toHaveURL(/\/organizations$/);
  });
}

for (const action of ["tokens", "invitation", "member editor"] as const) {
  test(`the organization ${action} updates and clears protected data on an external access change`, async ({ page, request, account }) => {
    const owner = await makeAccount(request);
    const company = await createCompany(request, owner, "Команда с реестром");
    const permissions = action === "tokens" ? ["resources.manage", "tokens.read"] : ["resources.manage", "members.read", "members.manage"];
    if (action === "member editor") permissions.push("resources.read");
    const membership = await addMember(request, owner, company.id, account, "user", permissions);
    const computer = await addComputer(request, account, "Рабочий Mac", company.id);
    await markAccountComputer(request, computer.id);
    const colleague = await makeAccount(request, "Коллега для проверки");
    if (action === "member editor") await addMember(request, owner, company.id, colleague, "user");
    const invitee = `${owner.nickname}@example.net`;
    if (action === "invitation") {
      expect((await request.post(`${API}/invitations`, {
        headers: headers(owner), data: { company_id: company.id, identifier: invitee, role: "user", permissions: [] },
      })).status()).toBe(201);
    }
    await startSession(page, account);
    await page.clock.install();
    await page.goto("compute/computers");
    await page.getByRole("link", { name: company.name, exact: true }).click();
    const details = `${API}/companies/${company.id}`;
    const metadataOutage = () => page.route(details, (route) => route.fulfill({ status: 503, json: { detail: "Temporary outage" } }));

    if (action === "tokens") {
      const created = await request.post(`${API}/api-tokens`, {
        headers: headers(owner), data: { company_id: company.id, name: "Токен команды", scopes: ["read"] },
      });
      expect(created.status()).toBe(201);
      const { id } = await created.json();
      const settings = await openOrganizationTokens(page);
      const row = settings.locator(".token-registry-row").filter({ hasText: "Токен команды" });
      await expect(row.getByText("Активен", { exact: true })).toBeVisible();
      expect((await request.patch(`${API}/access-tokens/${id}`, { headers: headers(owner), data: { revoke: true } })).status()).toBe(200);
      await page.clock.fastForward(61_000);
      await expect(row.getByText("Отозван", { exact: true })).toBeVisible();
      await metadataOutage();
      expect((await request.delete(`${API}/memberships/${membership}`, { headers: headers(owner) })).status()).toBe(204);
      await page.clock.fastForward(61_000);
      await expect(settings).toHaveCount(0);
    } else if (action === "invitation") {
      await switchOrganizationPeople(page, "Приглашения");
      const article = page.getByRole("article", { name: invitee, exact: true });
      await article.getByRole("button", { name: new RegExp(invitee) }).click();
      await article.getByRole("button", { name: "Обновить ссылку", exact: true }).click();
      const copy = page.getByRole("button", { name: "Скопировать ссылку", exact: true });
      await expect(copy).toBeVisible();
      await metadataOutage();
      expect((await request.patch(`${API}/memberships/${membership}`, {
        headers: headers(owner), data: { permissions: ["resources.manage", "members.read"] },
      })).status()).toBe(200);
      await page.clock.fastForward(61_000);
      await expect(article).toHaveCount(0);
      await expect(copy).toHaveCount(0);
    } else {
      const row = page.getByRole("article", { name: colleague.first_name, exact: true });
      await openOrganizationMember(row);
      await row.getByRole("button", { name: "Изменить права", exact: true }).click();
      const editor = page.getByRole("dialog", { name: "Изменить права", exact: true });
      await expect(editor).toBeVisible();
      await metadataOutage();
      expect((await request.patch(`${API}/memberships/${membership}`, {
        headers: headers(owner), data: { permissions: ["resources.manage", "resources.read", "members.manage"] },
      })).status()).toBe(200);
      await page.clock.fastForward(61_000);
      await expect(row).toHaveCount(0);
      await expect(editor).toHaveCount(0);
      await expect(page.getByRole("link", { name: "Список компьютеров", exact: true })).toBeVisible();
      await expect(page.locator(".organization-computer-card")).toContainText(computer.name);
      await expect(page.getByRole("region", { name: "История CPU" }).locator("figure")).toBeVisible();
      let release!: () => void;
      let started!: () => void;
      const hold = new Promise<void>((resolve) => { release = resolve; });
      const pending = new Promise<void>((resolve) => { started = resolve; });
      await page.route(`${API}/memberships?company_id=${company.id}&*`, async (route) => {
        started();
        await hold;
        await route.continue();
      });
      try {
        await page.clock.fastForward(61_000);
        await pending;
        await expect(row).toHaveCount(0);
        await expect(editor).toHaveCount(0);
      } finally { release(); }
    }
  });
}

for (const readTokens of [false, true]) {
  test(`manually refreshing an organization token registry rechecks revoked management with reading ${readTokens ? "allowed" : "denied"}`, async ({ page, request, account }) => {
    const owner = await makeAccount(request);
    const company = await createCompany(request, owner, "Команда с правом выпуска токенов");
    const permissions = ["resources.manage", ...(readTokens ? ["tokens.read"] : [])];
    const membership = await addMember(request, owner, company.id, account, "user", [...permissions, "tokens.manage"]);
    const computer = await addComputer(request, account, "Рабочий Mac", company.id);
    await markAccountComputer(request, computer.id);
    await startSession(page, account);
    await page.goto("compute/computers");
    await page.getByRole("link", { name: company.name, exact: true }).click();
    const settings = await openOrganizationTokens(page);
    const create = settings.getByRole("button", { name: "Создать токен", exact: true });
    await expect(create).toBeEnabled();
    expect((await request.patch(`${API}/memberships/${membership}`, {
      headers: headers(owner), data: { permissions },
    })).status()).toBe(200);
    await settings.getByRole("button", { name: "Обновить", exact: true }).click();
    if (readTokens) {
      await expect(settings.locator(".token-permission-note")).toBeVisible();
      await expect(create).toBeDisabled();
    } else {
      await expect(settings.getByRole("tab", { name: "API-токены", exact: true })).toHaveCount(0);
    }
  });
}

test("the organization token registry follows its own read access during a details outage", async ({ page, request, account }) => {
  const owner = await makeAccount(request);
  const company = await createCompany(request, owner, "Команда с токеном для чтения");
  const membership = await addMember(request, owner, company.id, account, "user", ["resources.manage", "members.read", "tokens.read"]);
  const computer = await addComputer(request, account, "Рабочий Mac", company.id);
  await markAccountComputer(request, computer.id);
  expect((await request.post(`${API}/api-tokens`, {
    headers: headers(owner), data: { company_id: company.id, name: "Токен для чтения", scopes: ["read"] },
  })).status()).toBe(201);
  const created = await request.post(`${API}/api-tokens`, {
    headers: headers(owner), data: { company_id: company.id, name: "Отозванный токен", scopes: ["read"] },
  });
  expect(created.status()).toBe(201);
  const revokedId = (await created.json()).id;
  expect((await request.patch(`${API}/access-tokens/${revokedId}`, {
    headers: headers(owner), data: { revoke: true },
  })).status()).toBe(200);
  await startSession(page, account);
  await page.clock.install();
  await page.goto("compute/computers");
  await page.getByRole("link", { name: company.name, exact: true }).click();
  const settings = await openOrganizationTokens(page);
  const row = settings.getByRole("article", { name: "Токен для чтения", exact: true });
  await expect(row).toBeVisible();
  await temporaryReadFailure(page, `${API}/companies/${company.id}`);
  const setPermissions = async (permissions: string[]) => {
    expect((await request.patch(`${API}/memberships/${membership}`, {
      headers: headers(owner), data: { permissions: ["resources.manage", ...permissions] },
    })).status()).toBe(200);
  };
  try {
    await setPermissions(["tokens.read"]);
    await page.clock.fastForward(61_000);
    await expect(settings.getByRole("alert")).toBeVisible();
    await expect(row).toBeVisible();
    const revoked = settings.getByRole("article", { name: "Отозванный токен", exact: true });
    await expect(revoked).toBeVisible();
    await settings.getByLabel("Состояние токенов").selectOption("active");
    await expect(revoked).toHaveCount(0);
    await expect(row).toBeVisible();
    await setPermissions([]);
    await page.clock.fastForward(61_000);
    await expect(row).toHaveCount(0);
    await setPermissions(["tokens.read"]);
    await page.clock.fastForward(61_000);
    await expect(row).toBeVisible();
  } finally {
    await page.unrouteAll({ behavior: "wait" });
  }
});

for (const target of ["details", "token registry"] as const) {
  test(`the organization ${target} keeps the retry focused after another failed reconnect`, async ({ page, request, account }) => {
    const company = await createCompany(request, account, "Команда после повторного сбоя");
    const computer = await addComputer(request, account, "Рабочий Mac", company.id);
    await markAccountComputer(request, computer.id);
    await startSession(page, account);
    await page.goto("compute/computers");
    const details = target === "details";
    const endpoint = details ? `${API}/companies/${company.id}` : `${API}/api-tokens?*`;
    if (details) await temporaryReadFailure(page, endpoint);
    await page.getByRole("link", { name: company.name, exact: true }).click();
    if (!details) {
      await temporaryReadFailure(page, endpoint);
      await openOrganizationTokens(page);
    }
    const retry = page.getByRole("button", { name: /Попробовать снова/ });
    const collectionResponse = () => page.waitForResponse((response) => {
      return details ? response.url() === endpoint : response.url().startsWith(`${API}/api-tokens?`);
    });
    try {
      await retry.focus();
      await page.context().setOffline(true);
      const attempted = collectionResponse();
      await page.context().setOffline(false);
      expect((await attempted).status()).toBe(503);
      await expect(retry).toBeFocused();
    } finally { await page.unrouteAll({ behavior: "wait" }); }
    const succeeded = collectionResponse();
    await page.keyboard.press("Enter");
    expect((await succeeded).status()).toBe(200);
    await expect(retry).toHaveCount(0);
    const fallback = details ? page.getByRole("link", { name: /К организациям/ }) : page.getByRole("group", { name: "Доступные токены" });
    await expect(fallback).toBeFocused();
  });
}

for (const collection of ["members", "invitations"] as const) test(`organization resources, history and ${collection} reload when the browser reconnects after a timeout`, async ({ page, request, account }) => {
  const company = await createCompany(request, account, "Команда после сбоя сети");
  const computer = await addComputer(request, account, "Mac вне сети", company.id);
  await markAccountComputer(request, computer.id);
  await startSession(page, account);
  await page.clock.install();
  await page.goto("compute/computers");
  await page.getByRole("link", { name: company.name, exact: true }).click();
  const rowName = collection === "members" ? "Коллега после сбоя" : `${account.nickname}@example.net`;
  if (collection === "invitations") await switchOrganizationPeople(page, "Приглашения");
  const records = page.getByRole("region", { name: collection === "members" ? "Участники" : "Приглашения", exact: true });
  const history = page.getByRole("region", { name: "История CPU", exact: true });
  const computers = page.getByRole("region", { name: "Компьютеры", exact: true });
  await expect(history.locator("figure")).toBeVisible();
  await expect(computers.getByRole("link", { name: /Mac вне сети/ })).toBeVisible();
  await page.context().setOffline(true);
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await page.clock.fastForward(21_000);
  await expect(history.getByRole("alert")).toBeVisible();
  await expect(computers.getByRole("alert")).toBeVisible();
  await expect(records.getByRole("alert")).toBeVisible();
  if (collection === "members") {
    const colleague = await makeAccount(request, rowName);
    await addMember(request, account, company.id, colleague, "user");
  } else {
    expect((await request.post(`${API}/invitations`, {
      headers: headers(account), data: { company_id: company.id, identifier: rowName, role: "user" },
    })).status()).toBe(201);
  }
  if (collection === "invitations") {
    const endpoint = `${API}/invitations?company_id=${company.id}&include_counts=false&*`;
    const retry = records.getByRole("button", { name: /Попробовать снова/ });
    await retry.focus();
    await page.route(endpoint, async (route) => {
      const response = await route.fetch();
      await route.fulfill({ response, status: 503, json: { detail: "Temporary outage" } });
    });
    try {
      const failed = page.waitForResponse((response) => response.url().startsWith(`${API}/invitations?company_id=${company.id}&include_counts=false`) && response.status() === 503);
      await page.context().setOffline(false);
      await failed;
      await expect(retry).toBeFocused();
    } finally { await page.unrouteAll({ behavior: "wait" }); }
    await page.keyboard.press("Enter");
    await expect(page.getByRole("tab", { name: /Приглашения/ })).toBeFocused();
  } else {
    await page.context().setOffline(false);
  }
  await expect(history.getByRole("alert")).toHaveCount(0);
  await expect(computers.getByRole("alert")).toHaveCount(0);
  await expect(computers.getByRole("link", { name: /Mac вне сети/ })).toBeVisible();
  await expect(records.getByRole("article", { name: rowName, exact: true })).toBeVisible();
});

for (const delayed of ["overview", "history"] as const) test(`the first organization ${delayed} reconciles an older response after a move`, async ({ page, request, account }) => {
  const company = await createCompany(request, account, "Команда в момент переноса");
  const computer = await addComputer(request, account, "Командный Mac", company.id);
  await markAccountComputer(request, computer.id);
  await startSession(page, account);
  await page.clock.install();
  await page.goto("compute/computers");
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  await page.route(`${API}/companies/${company.id}/resources/pool-${delayed}`, async (route) => {
    await gate;
    await route.continue();
  }, { times: 1 });
  try {
    await page.getByRole("link", { name: company.name, exact: true }).click();
    const total = page.getByRole("region", { name: "История CPU", exact: true }).locator("figcaption span").first().locator("strong");
    if (delayed === "overview") await expect(total).toHaveText("8 ядер");
    else await expect(page.locator(".organization-computer-card")).toContainText(computer.name);
    expect((await request.patch(`${API}/connected-computers/${computer.id}/pool`, {
      headers: headers(account), data: { company_id: null, expected_company_id: company.id },
    })).status()).toBe(200);
    release();
    await expect(page.locator(".organization-overview dd").first()).toHaveText("0");
    await expect(total).toHaveText("0 ядер");
    await expect(page.locator(".organization-computer-card")).toHaveCount(0);
    if (delayed === "history") {
      expect((await request.patch(`${API}/connected-computers/${computer.id}/pool`, {
        headers: headers(account), data: { company_id: company.id, expected_company_id: null },
      })).status()).toBe(200);
      await page.clock.fastForward(61_000);
      await expect(page.locator(".organization-overview dd").first()).toHaveText("1");
      await expect(total).toHaveText("8 ядер");
    }
  } finally { release(); }
});

test("the organization reconciles both widgets when a computer moves again during reconciliation", async ({ page, request, account }) => {
  const company = await createCompany(request, account, "Команда с повторным переносом");
  const computer = await addComputer(request, account, "Командный Mac", company.id);
  await markAccountComputer(request, computer.id);
  await startSession(page, account);
  await page.goto("compute/computers");
  let releaseOverview!: () => void;
  let releaseCorrection!: () => void;
  let startCorrection!: () => void;
  const firstOverview = new Promise<void>((resolve) => { releaseOverview = resolve; });
  const correction = new Promise<void>((resolve) => { releaseCorrection = resolve; });
  const correcting = new Promise<void>((resolve) => { startCorrection = resolve; });
  const path = `${API}/companies/${company.id}/resources`;
  await page.route(`${path}/pool-overview`, async (route) => {
    await firstOverview;
    await route.continue();
  }, { times: 1 });
  let historyReads = 0;
  await page.route((url) => [`${path}/pool-history`, `${path}/pool-snapshot`].includes(url.href), async (route) => {
    if (route.request().url().endsWith("/pool-history") && ++historyReads === 1) return route.continue();
    startCorrection();
    await correction;
    await route.continue();
  });
  const move = async (destination: string | null, current: string | null) => {
    expect((await request.patch(`${API}/connected-computers/${computer.id}/pool`, {
      headers: headers(account), data: { company_id: destination, expected_company_id: current },
    })).status()).toBe(200);
  };
  try {
    await page.getByRole("link", { name: company.name, exact: true }).click();
    const total = page.getByRole("region", { name: "История CPU", exact: true }).locator("figcaption span").first().locator("strong");
    const count = page.locator(".organization-overview dd").first();
    await expect(total).toHaveText("8 ядер");
    await move(null, company.id);
    releaseOverview();
    await expect(count).toHaveText("0");
    await correcting;
    await move(company.id, null);
    releaseCorrection();
    await expect(count).toHaveText("1");
    await expect(total).toHaveText("8 ядер");
    await expect(page.locator(".organization-computer-card")).toContainText(computer.name);
  } finally {
    releaseOverview();
    releaseCorrection();
    await page.unrouteAll({ behavior: "wait" });
  }
});

test("the organization retries a failed reconciliation without waiting for the pool poll", async ({ page, request, account }) => {
  const company = await createCompany(request, account, "Команда с повтором снимка");
  const computer = await addComputer(request, account, "Командный Mac", company.id);
  await markAccountComputer(request, computer.id);
  await startSession(page, account);
  await page.clock.install();
  await page.goto("compute/computers");
  const path = `${API}/companies/${company.id}/resources`;
  const endpoint = `${path}/pool-snapshot`;
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  await page.route(`${path}/pool-overview`, async (route) => {
    await gate;
    await route.continue();
  }, { times: 1 });
  await temporaryReadFailure(page, endpoint);
  try {
    await page.getByRole("link", { name: company.name, exact: true }).click();
    const chart = page.getByRole("region", { name: "История CPU", exact: true });
    const total = chart.locator("figcaption span").first().locator("strong");
    await expect(total).toHaveText("8 ядер");
    expect((await request.patch(`${API}/connected-computers/${computer.id}/pool`, {
      headers: headers(account), data: { company_id: null, expected_company_id: company.id },
    })).status()).toBe(200);
    release();
    await expect(chart.getByRole("alert")).toBeVisible();
    await page.unroute(endpoint);
    await page.clock.fastForward(6_000);
    await expect(total).toHaveText("0 ядер");
    await expect(chart.getByRole("alert")).toHaveCount(0);
  } finally {
    release();
    await page.unrouteAll({ behavior: "wait" });
  }
});

test("organization charts refresh when the pool loses its last computer", async ({ page, request, account }) => {
  const company = await createCompany(request, account, "Команда перед переносом");
  const computer = await addComputer(request, account, "Командный Mac", company.id);
  await markAccountComputer(request, computer.id);
  await startSession(page, account);
  await page.clock.install();
  await page.goto("compute/computers");
  await page.getByRole("link", { name: company.name, exact: true }).click();
  const history = page.getByRole("region", { name: "История CPU", exact: true });
  const total = history.locator("figcaption span").first().locator("strong");
  await expect(total).toHaveText("8 ядер");
  expect((await request.patch(`${API}/connected-computers/${computer.id}/pool`, {
    headers: headers(account), data: { company_id: null, expected_company_id: company.id },
  })).status()).toBe(200);
  await page.clock.fastForward(61_000);
  await expect(page.locator(".organization-overview dd").first()).toHaveText("0");
  await expect(total).toHaveText("0 ядер");
  await expect(history.locator("figure")).toBeVisible();
});

for (const view of ["overview", "computers"] as const) {
  test(`Compute ${view} refreshes external agent state without reloading the page`, async ({ page, request, account }) => {
    const computer = await addComputer(request, account, "Мой Mac");
    await markAccountComputer(request, computer.id);
    await startSession(page, account);
    await page.clock.install();
    await page.goto(view === "overview" ? "compute" : "compute/computers");
    const totals = page.locator(".compute-status-summary dd");
    const online = page.getByRole("region", { name: "Онлайн", exact: true });
    const offline = page.getByRole("region", { name: "Не в сети", exact: true });
    const move = page.getByRole("button", { name: `Сменить пул — ${computer.name}`, exact: true });
    if (view === "overview") await expect(totals).toHaveText(["1", "1", "0"]);
    else {
      await expect(online).toContainText(computer.name);
      await move.focus();
    }
    const stopped = await request.post(`${API}/computers/${computer.id}/credentials`, { headers: headers(account) });
    expect(stopped.status()).toBe(201);
    await page.clock.fastForward(61_000);
    if (view === "overview") await expect(totals).toHaveText(["1", "0", "1"]);
    else {
      await expect(offline).toContainText(computer.name);
      await expect(online).toHaveCount(0);
      await expect(move).toBeFocused();
    }
  });
}

test("a sign-out and new sign-in in another tab replace the old Compute session", async ({ page, request, account }) => {
  const other = await makeAccount(request, "Борис");
  const firstComputer = await addComputer(request, account, "Mac Алисы");
  const secondComputer = await addComputer(request, other, "Mac Бориса");
  await markAccountComputer(request, firstComputer.id);
  await markAccountComputer(request, secondComputer.id);
  await startSession(page, account);
  await page.goto("compute/computers");
  await expect(page.getByText(firstComputer.name)).toBeVisible();
  const second = await page.context().newPage();
  try {
    await second.goto("compute");
    const menu = await openUserMenu(second);
    await menu.getByRole("menuitem", { name: "Выйти", exact: true }).click();
    await expect(page.getByRole("heading", { name: "С возвращением." })).toBeVisible();
    await expect(page.getByText(firstComputer.name)).toHaveCount(0);
    await login(second, other);
    await expect(page.getByText(secondComputer.name)).toBeVisible();
    await expect(page.getByText(firstComputer.name)).toHaveCount(0);
  } finally {
    await second.close();
  }
});

test("another sign-in to the same account preserves an open Compute move", async ({ page, request, account }) => {
  const target = await createCompany(request, account, "Пул назначения");
  const computer = await addComputer(request, account, "Мой Mac", undefined, false);
  await markAccountComputer(request, computer.id);
  const other = await page.context().newPage();
  await page.goto("login");
  await other.goto("login");
  test.skip(!await other.evaluate(() => Boolean(navigator.locks)), "Browser Web Locks support is required for this scenario");
  const endpoint = `${API}/auth/session`;
  const firstLogin = await pauseMutation(page, endpoint, { method: "POST", pause: "response" });
  const secondLogin = await pauseMutation(other, endpoint, { method: "POST" });
  try {
    for (const tab of [page, other]) {
      await tab.getByLabel("Email или ник").fill(account.email);
      await tab.getByLabel("Пароль", { exact: true }).fill(password);
    }
    await page.getByRole("button", { name: "Войти", exact: true }).click();
    await firstLogin.pending;
    await other.getByRole("button", { name: "Войти", exact: true }).click();
    await expect(other.locator("form button.primary")).toBeDisabled();
    firstLogin.release();
    await secondLogin.pending;
    await expect(page.getByRole("heading", { name: `Привет, ${account.first_name}` })).toBeVisible();
    await page.goto("compute/computers");
    await page.getByRole("button", { name: `Сменить пул — ${computer.name}`, exact: true }).click();
    const dialog = page.getByRole("dialog");
    const selector = dialog.getByLabel("Новый пул");
    await selector.selectOption(target.id);
    const verification = page.waitForResponse((response) =>
      response.url() === endpoint && response.request().method() === "GET" && response.ok()
    );
    secondLogin.release();
    expect((await (await verification).json()).id).toBe(account.id);
    await expect(dialog).toBeVisible();
    await expect(selector).toHaveValue(target.id);
  } finally {
    firstLogin.release();
    secondLogin.release();
    await other.close();
  }
});

for (const access of ["removed", "read only"] as const) {
  test(`refresh keeps focus in a connected row when pool access becomes ${access}`, async ({ page, request, account }) => {
    const owner = await makeAccount(request);
    const pool = await createCompany(request, owner, "Общий пул");
    const member = await request.post(`${API}/memberships`, {
      headers: headers(owner), data: { company_id: pool.id, user_id: account.id, role: "admin" },
    });
    expect(member.status()).toBe(201);
    const endpoint = `${API}/memberships/${(await member.json()).id}`;
    const computer = await addComputer(request, account, "Мой Mac", pool.id);
    await markAccountComputer(request, computer.id);
    await startSession(page, account);
    await page.clock.install();
    await page.goto("compute/computers");
    const row = page.getByRole("region", { name: "Онлайн", exact: true }).getByRole("listitem").filter({ hasText: computer.name });
    const link = row.getByRole("link", { name: pool.name });
    const move = row.getByRole("button", { name: /Сменить пул/ });
    await expect(link).toBeVisible();
    if (access === "removed") {
      await link.focus();
      expect((await request.delete(endpoint, { headers: headers(owner) })).status()).toBe(204);
    } else {
      await move.focus();
      expect((await request.patch(endpoint, { headers: headers(owner), data: { permissions: ["resources.read"] } })).status()).toBe(200);
    }
    await page.clock.fastForward(61_000);
    await expect(move).toBeDisabled();
    await expect(row).toBeFocused();
    if (access === "removed") await expect(link).toHaveCount(0);
    else await expect(link).toBeVisible();
  });
}

for (const control of ["pool selector", "submit"] as const) {
  test(`an open pool dialog explains revoked access and keeps focus after disabling the ${control}`, async ({ page, request, account }) => {
    const owner = await makeAccount(request);
    const pool = await createCompany(request, owner, "Общий пул");
    const member = await request.post(`${API}/memberships`, {
      headers: headers(owner), data: { company_id: pool.id, user_id: account.id, role: "admin" },
    });
    expect(member.status()).toBe(201);
    const endpoint = `${API}/memberships/${(await member.json()).id}`;
    const computer = await addComputer(request, account, "Мой Mac", pool.id);
    await markAccountComputer(request, computer.id);
    await startSession(page, account);
    await page.clock.install();
    await page.goto("compute/computers");
    await page.getByRole("button", { name: `Сменить пул — ${computer.name}`, exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Сменить пул" });
    const selector = dialog.getByLabel("Новый пул");
    const submit = dialog.getByRole("button", { name: "Сменить пул", exact: true });
    if (control === "submit") await selector.selectOption("personal");
    const focused = control === "submit" ? submit : selector;
    await focused.focus();
    await expect(focused).toBeFocused();

    const update = await request.patch(endpoint, { headers: headers(owner), data: { permissions: ["resources.read"] } });
    expect(update.status()).toBe(200);
    await page.clock.fastForward(61_000);
    await expect(dialog.getByRole("alert")).toHaveText("У вас нет прав на это действие.");
    await expect(selector).toBeDisabled();
    await expect(submit).toBeDisabled();
    await expect(dialog.getByRole("button", { name: "Закрыть диалог", exact: true })).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);
  });
}

test("connected computers paginate without losing online and offline rows, and recover a vanished final page", async ({ page, request, account }) => {
  const pool = await createCompany(request, account, "Пул для переноса");
  const workers = [];
  for (let i = 0; i < 12; i++) {
    const computer = await addComputer(request, account, `Подключённый узел ${i + 1}`, undefined, i % 2 === 0);
    await markAccountComputer(request, computer.id);
    workers.push(computer);
  }
  await startSession(page, account);
  await page.setViewportSize({ width: 390, height: 460 });
  await page.goto("compute/computers");
  const rows = page.locator(".connected-computer-row");
  const pager = page.getByRole("navigation", { name: "Страница" });
  await expect(rows).toHaveCount(10);
  await expect(pager).toContainText("1–10 из 12");
  await expect(pager.getByRole("button", { name: "Назад" })).toBeDisabled();
  await pager.getByRole("button", { name: "Далее" }).focus();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/page=2$/);
  await expect(pager).toContainText("11–12 из 12");
  await expect(pager.getByRole("button", { name: "Далее" })).toBeDisabled();
  await expect(rows).toHaveCount(2);
  const heading = page.getByRole("heading", { name: /Подключённые компьютеры/ });
  await expect(heading).toBeFocused();
  await expect(heading).toBeInViewport();
  await expect(page.getByRole("region", { name: "Онлайн", exact: true })).toContainText("Подключённый узел 11");
  await expect(page.getByRole("region", { name: "Не в сети", exact: true })).toContainText("Подключённый узел 12");
  const worker = rows.filter({ hasText: workers[10].name });
  await worker.getByRole("button", { name: /Сменить пул/ }).click();
  await page.getByRole("dialog").getByLabel("Новый пул").selectOption(pool.id);
  await page.goBack();
  await expect(pager).toContainText("1–10 из 12");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await page.goForward();
  await expect(worker.getByRole("link", { name: "Личный пул" })).toBeVisible();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await page.reload();
  await expect(rows).toHaveCount(2);
  for (const worker of workers.slice(10)) {
    expect((await request.delete(`${API}/computers/${worker.id}`, { headers: headers(account) })).status()).toBe(204);
  }
  await page.reload();
  await expect(page).toHaveURL(/compute\/computers$/);
  await expect(rows).toHaveCount(10);
  await expect(pager).toContainText("1–10 из 10");
});

test("a background refresh moves focus from a disabled pagination control to the current page", async ({ page, request, account }) => {
  const workers = [];
  for (let index = 1; index <= 11; index++) {
    const computer = await addComputer(request, account, `Mac ${index}`, undefined, false);
    await markAccountComputer(request, computer.id);
    workers.push(computer);
  }
  await startSession(page, account);
  await page.clock.install();
  await page.goto("compute/computers");
  const pagination = page.getByRole("navigation", { name: "Страница" });
  const next = pagination.getByRole("button", { name: "Далее", exact: true });
  await next.focus();
  await expect(next).toBeFocused();
  const deleted = await request.delete(`${API}/computers/${workers[10].id}`, { headers: headers(account) });
  expect(deleted.status()).toBe(204);
  await page.clock.fastForward(61_000);
  await expect(pagination).toContainText("1–10 из 10");
  await expect(next).toBeDisabled();
  await expect(pagination.getByRole("button", { name: "Страница 1", exact: true })).toBeFocused();
});

test("a scoped pool list keeps keyboard focus when an external move disables its next page", async ({ page, request, account }) => {
  const pool = await createCompany(request, account, "Пул с двумя страницами");
  const moved = await addComputer(request, account, "Переносимый узел", pool.id, false);
  await markAccountComputer(request, moved.id);
  for (let index = 1; index <= 10; index++) {
    await addComputer(request, account, `Mac ${index}`, pool.id, false);
  }
  await startSession(page, account);
  await page.clock.install();
  await page.goto(`compute/computers?company_id=${pool.id}&only_available=false`);
  const pager = page.getByRole("navigation", { name: "Страница" });
  const next = pager.getByRole("button", { name: /Далее/ });
  await expect(pager).toContainText("Страница 1 из 2");
  await next.focus();
  await expect(next).toBeFocused();
  const transfer = await request.patch(`${API}/connected-computers/${moved.id}/pool`, {
    headers: headers(account), data: { company_id: null, expected_company_id: pool.id },
  });
  expect(transfer.status()).toBe(200);
  await page.clock.fastForward(61_000);
  await expect(pager).toContainText("Страница 1 из 1");
  await expect(next).toBeDisabled();
  await expect(pager).toBeFocused();
});

for (const scenario of ["another page", "empty final page", "external move during a list outage"] as const) {
  test(`an open transfer follows its computer across list pages in the ${scenario} scenario`, async ({ page, request, account }) => {
    const pool = await createCompany(request, account, "Пул для переноса");
    const workers = [];
    const initialCount = scenario === "empty final page" ? 11 : 12;
    for (let index = 1; index <= initialCount; index++) {
      const computer = await addComputer(request, account, `Mac ${index}`, undefined, false);
      await markAccountComputer(request, computer.id);
      workers.push(computer);
    }
    await startSession(page, account);
    await page.clock.install();
    await page.goto("compute/computers?page=2");
    const rowSelector = `[data-computer-id="${workers[10].id}"]`;
    const row = page.locator(rowSelector);
    await row.getByRole("button", { name: /Сменить пул/ }).click();
    const dialog = page.getByRole("dialog");
    const destination = dialog.getByLabel("Новый пул");
    await destination.selectOption(pool.id);
    const departed = await request.delete(`${API}/computers/${workers[0].id}`, { headers: headers(account) });
    expect(departed.status()).toBe(204);
    await page.clock.fastForward(61_000);
    await expect(row).toHaveCount(0);
    await expect(page).toHaveURL(/page=2$/);
    if (initialCount === 12) {
      await expect(page.getByRole("navigation", { name: "Страница" })).toContainText("11–11 из 11");
    }
    await expect(dialog.getByRole("alert")).toHaveCount(0);
    const submit = dialog.getByRole("button", { name: "Сменить пул", exact: true });
    await expect(submit).toBeEnabled();
    await expect(destination).toHaveValue(pool.id);

    if (scenario === "external move during a list outage") {
      const listPage = "**/v1/connected-computers?limit=10&offset=10";
      await page.route(listPage, (route) => route.fulfill({ status: 503, json: { detail: "Temporary outage" } }));
      const otherTab = await page.context().newPage();
      try {
        await otherTab.goto("compute/computers");
        await otherTab.locator(rowSelector).getByRole("button", { name: /Сменить пул/ }).click();
        const otherDialog = otherTab.getByRole("dialog");
        await otherDialog.getByLabel("Новый пул").selectOption(pool.id);
        await otherDialog.getByRole("button", { name: "Сменить пул", exact: true }).click();
        await expect(otherDialog).toHaveCount(0);
        await expect(dialog.getByRole("alert")).toHaveText("Пул уже изменился. Откройте смену пула ещё раз.");
        await expect(dialog.locator("strong")).toHaveText(pool.name);
        await expect(submit).toBeDisabled();
      } finally {
        await otherTab.close();
        await page.unroute(listPage);
      }
      return;
    }

    await submit.click();
    await expect(dialog).toHaveCount(0);
    await expect(page.getByRole("status").filter({ hasText: "Вычислительный пул изменён." })).toBeVisible();
    if (initialCount === 11) await expect(page).toHaveURL(/compute\/computers$/);
    else await page.getByRole("button", { name: "Страница 1", exact: true }).click();
    await expect(row.getByRole("link", { name: pool.name })).toBeVisible();
  });
}

for (const view of ["connected computers", "pool catalog", "pool detail", "overview", "organization directory", "organization detail"] as const) {
  test(`pending transfer refreshes the ${view} reopened before it commits`, async ({ page, request, account }) => {
    const pool = await createCompany(request, account, "Пул сборки");
    const computer = await addComputer(request, account, "Переносимый Mac");
    await markAccountComputer(request, computer.id);
    await startSession(page, account);
    await page.goto("compute");
    const computersLink = page.getByRole("link", { name: "Подключённые компьютеры", exact: true });
    const poolsLink = page.getByRole("link", { name: "Вычислительные пулы", exact: true });
    await computersLink.click();
    const row = page.getByRole("listitem").filter({ hasText: computer.name });
    await row.getByRole("button", { name: /Сменить пул/ }).click();
    const dialog = page.getByRole("dialog", { name: "Сменить пул" });
    await dialog.getByLabel("Новый пул").selectOption(pool.id);
    const endpoint = `${API}/connected-computers/${computer.id}/pool`;
    const transfer = await pauseMutation(page, endpoint, { method: "PATCH" });
    const committed = page.waitForResponse((response) => response.url() === endpoint && response.ok());
    const poolCount = page.getByRole("article", { name: pool.name, exact: true }).getByTestId("organization-computer-count");
    const personalCount = page.getByRole("article", { name: "Личный пул", exact: true }).getByTestId("organization-computer-count");
    const detailCount = page.getByRole("region", { name: pool.name, exact: true }).getByTestId("computer-count");
    const organizationCount = page.getByRole("region", { name: "Компьютеры", exact: true }).locator(".organization-widget-count");
    try {
      await dialog.getByRole("button", { name: "Сменить пул", exact: true }).click();
      await transfer.pending;
      await page.goBack();
      await expect(dialog).toHaveCount(0);
      if (view === "connected computers") {
        await computersLink.click();
        await expect(row.getByRole("link", { name: "Личный пул" })).toBeVisible();
      } else if (view === "pool catalog" || view === "pool detail") {
        await poolsLink.click();
        await expect(personalCount).toHaveText("1");
        await expect(poolCount).toHaveText("0");
        if (view === "pool detail") {
          await page.getByRole("link", { name: `Открыть пул ${pool.name}` }).click();
          await expect(detailCount).toHaveText("0");
        }
      } else if (view === "overview") {
        await page.getByRole("link", { name: "Обзор", exact: true }).click();
        await expect(detailCount).toHaveText("0");
      } else {
        await page.getByRole("link", { name: "Организации", exact: true }).click();
        await expect(poolCount).toHaveText("0");
        if (view === "organization detail") {
          await page.getByRole("link", { name: `Открыть организацию: ${pool.name}` }).click();
          await expect(organizationCount).toHaveText("0");
        }
      }
    } finally {
      transfer.release();
    }
    await committed;
    if (view === "connected computers") {
      await expect(row.getByRole("link", { name: pool.name })).toBeVisible();
      await expect(row.getByRole("link", { name: "Личный пул" })).toHaveCount(0);
    } else if (view === "pool catalog") {
      await expect(personalCount).toHaveText("0");
      await expect(poolCount).toHaveText("1");
    } else if (view === "organization directory") {
      await expect(poolCount).toHaveText("1");
    } else if (view === "organization detail") {
      await expect(organizationCount).toHaveText("1");
      await expect(page.getByRole("link", { name: new RegExp(computer.name) })).toBeVisible();
    } else {
      await expect(detailCount).toHaveText("1");
    }
  });
}

test("pool picker displays allowed destinations, cancellation, a stale pool and the persisted move", async ({ page, request, account }) => {
  const owner = await makeAccount(request);
  const first = await createCompany(request, account, "Пул сборки");
  const other = await createCompany(request, account, "Пул проверки");
  const denied = await createCompany(request, owner, "Только просмотр");
  await addMember(request, owner, denied.id, account, "user");
  const computer = await addComputer(request, account, "Мой рабочий Mac");
  await markAccountComputer(request, computer.id);
  await startSession(page, account);
  await page.goto("compute/computers");
  const row = page.getByRole("listitem").filter({ hasText: "Мой рабочий Mac" });
  const move = row.getByRole("button", { name: "Сменить пул — Мой рабочий Mac", exact: true });
  const dialog = page.getByRole("dialog", { name: "Сменить пул" });
  const select = dialog.getByLabel("Новый пул");
  const confirm = dialog.getByRole("button", { name: "Сменить пул", exact: true });
  await move.click();
  await expect(dialog).toContainText("Личный пул");
  await expect(confirm).toBeDisabled();
  await expect(select.locator("option")).toHaveText(["Выберите пул", first.name, other.name]);
  await select.selectOption(first.id);
  await dialog.getByRole("button", { name: "Отмена", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  await expect(move).toBeFocused();
  await expect(row.getByRole("link", { name: "Личный пул" })).toBeVisible();
  await move.click();
  await select.selectOption(first.id);
  const transferRequest = `${API}/connected-computers/${computer.id}/pool`;
  const change = await pauseMutation(page, transferRequest, { method: "PATCH" });
  const inventory = "**/v1/connected-computers?*";
  await page.route(inventory, (route) => route.fulfill({ status: 503, json: { detail: "Temporary outage" } }));
  const refreshed = page.waitForResponse((response) =>
    response.url().startsWith(`${API}/connected-computers?`) && response.ok(),
  );
  try {
    await confirm.click();
    await change.pending;
    await expect(select).toBeDisabled();
    await expect(dialog.getByRole("button", { name: "Отмена" })).toBeDisabled();
  } finally {
    change.release();
  }
  await expect(dialog).toHaveCount(0);
  await page.unroute(transferRequest);
  await expect(page.getByRole("status")).toContainText("Вычислительный пул изменён.");
  await expect(page.getByRole("alert")).toBeVisible();
  await expect(row).toContainText("Уточняем пул");
  await expect(row.getByRole("link")).toHaveCount(0);
  await expect(move).toBeDisabled();
  await expect(page.getByRole("heading", { name: /Подключённые компьютеры/ })).toBeFocused();
  await page.unroute(inventory);
  await page.getByRole("alert").getByRole("button").click();
  await refreshed;
  await expect(row.getByRole("link", { name: first.name })).toBeVisible();
  await page.reload();
  await expect(row.getByRole("link", { name: first.name })).toBeVisible();

  await move.click();
  await expect(select.locator("option")).toHaveText(["Выберите пул", "Личный пул", other.name]);
  const external = await request.patch(`${API}/connected-computers/${computer.id}/pool`, {
    headers: headers(account),
    data: { company_id: other.id, expected_company_id: first.id },
  });
  expect(external.status()).toBe(200);
  await select.selectOption("personal");
  await page.route(inventory, (route) => route.fulfill({ status: 503, json: { detail: "Temporary outage" } }));
  await confirm.click();
  await expect(dialog.getByRole("alert").filter({ hasText: "Пул уже изменился" })).toBeVisible();
  await expect(dialog.getByRole("alert").getByRole("button")).toBeVisible();
  await expect(confirm).toBeDisabled();
  await dialog.getByRole("button", { name: "Закрыть диалог" }).click();
  await expect(row).toContainText("Уточняем пул");
  await expect(row.getByRole("link")).toHaveCount(0);
  await expect(move).toBeDisabled();
  await page.unroute(inventory);
  await page.getByRole("alert").getByRole("button").click();
  await expect(row.getByRole("link", { name: other.name })).toBeVisible();
  await move.click();
  await select.selectOption("personal");
  await confirm.click();
  await expect(row.getByRole("link", { name: "Личный пул" })).toBeVisible();
  await page.getByRole("link", { name: "← К Compute" }).click();
  await page.getByRole("link", { name: "Вычислительные пулы" }).click();
  await expect(page.getByRole("article", { name: "Личный пул" }).getByTestId("organization-computer-count")).toHaveText("1");
  await expect(page.getByRole("article", { name: first.name }).getByTestId("organization-computer-count")).toHaveText("0");
  await expect(page.getByRole("article", { name: other.name }).getByTestId("organization-computer-count")).toHaveText("0");
});

test("connected lists and the pool picker distinguish the personal pool and organizations with identical names", async ({ page, request, account }) => {
  const from = await createCompany(request, account, "Исходный пул");
  const namedPersonal = await createCompany(request, account, "Личный пул");
  const spacedName = await createCompany(request, account, "Личный  пул");
  const first = await createCompany(request, account, "Общий пул");
  const second = await createCompany(request, account, "Общий пул");
  const withSuffix = await createCompany(request, account, "Личный пул (Организация)");
  const computer = await addComputer(request, account, "Мой Mac", from.id, false);
  await markAccountComputer(request, computer.id);
  for (const pool of [null, namedPersonal, spacedName, withSuffix, first, second]) {
    const member = await addComputer(request, account, `Mac ${pool?.id ?? "в личном пуле"}`, pool?.id, false);
    await markAccountComputer(request, member.id);
  }
  const organizationLabel = "Личный пул (Организация)";
  await startSession(page, account);
  await page.goto("compute/computers");
  await expect(page.getByRole("link", { name: "Личный пул", exact: true })).toHaveAttribute("href", "/compute/pools/personal");
  for (const pool of [namedPersonal, spacedName, withSuffix, first, second]) {
    const label = pool === first || pool === second ? pool.name : organizationLabel;
    const link = page.getByRole("link", { name: `${label} · ${pool.id}`, exact: true });
    await expect(link).toHaveAttribute("href", `/organizations/${pool.id}`);
  }
  const chosen = page.getByRole("link", { name: `${first.name} · ${first.id}`, exact: true });
  await chosen.press("Enter");
  await expect(page).toHaveURL(new RegExp(`/organizations/${first.id}$`));
  await page.goBack();
  await expect(chosen).toBeFocused();
  await page.getByRole("button", { name: `Сменить пул — ${computer.name}`, exact: true }).click();
  const dialog = page.getByRole("dialog");
  const select = dialog.getByLabel("Новый пул");
  await expect(select.locator('option[value="personal"]')).toHaveText("Личный пул");
  for (const company of [namedPersonal, spacedName, withSuffix]) {
    await expect(select.locator(`option[value="${company.id}"]`)).toHaveText(`${organizationLabel} · ${company.id}`);
  }
  for (const company of [first, second]) {
    await expect(select.locator(`option[value="${company.id}"]`)).toHaveText(`${company.name} · ${company.id}`);
  }
  await select.selectOption({ label: `${organizationLabel} · ${namedPersonal.id}` });
  await dialog.getByRole("button", { name: "Сменить пул", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  const moved = page.locator(`[data-computer-id="${computer.id}"]`);
  await expect(moved.getByRole("link", { name: `${organizationLabel} · ${namedPersonal.id}`, exact: true }))
    .toHaveAttribute("href", `/organizations/${namedPersonal.id}`);
  await page.goto("compute/pools");
  const personal = page.getByRole("link", { name: /открыть пул личный пул$/i });
  await expect(personal).toHaveAttribute("href", "/compute/pools/personal");
  for (const company of [namedPersonal, spacedName, withSuffix]) {
    const link = page.getByRole("link", { name: new RegExp(`${company.id}$`) });
    await expect(link).toHaveAttribute("href", `/compute/pools/${company.id}`);
    await expect(link).toHaveAccessibleName(new RegExp(`Организация.*${company.id}$`));
  }
});

test("a definitive rejection unlocks the connected list reopened while the transfer was pending", async ({ page, request, account }) => {
  const target = await createCompany(request, account, "Пул назначения");
  const computer = await addComputer(request, account, "Рабочий Mac");
  await markAccountComputer(request, computer.id);
  await startSession(page, account);
  await page.goto("compute");
  const computers = page.getByRole("link", { name: "Подключённые компьютеры", exact: true });
  await computers.click();
  const move = page.getByRole("button", { name: `Сменить пул — ${computer.name}`, exact: true });
  await move.click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Новый пул").selectOption(target.id);
  const endpoint = `${API}/connected-computers/${computer.id}/pool`;
  let rejectTransfer!: () => void;
  const alongsideNavigation = new Promise<void>((resolve) => { rejectTransfer = resolve; });
  await page.route(endpoint, async (route) => {
    await alongsideNavigation;
    await route.fulfill({ status: 409, json: { detail: "Computer has reserved resources" } });
  });
  const submitted = page.waitForRequest((req) => req.url() === endpoint && req.method() === "PATCH");
  try {
    await dialog.getByRole("button", { name: "Сменить пул", exact: true }).click();
    await submitted;
    await page.goBack();
    await computers.click();
    await expect(move).toBeDisabled();
  } finally {
    rejectTransfer();
  }
  await expect(move).toBeEnabled();
  await expect(page.getByRole("link", { name: "Личный пул" })).toBeVisible();
});

test("connected computer errors recover and unavailable counts differ from zero", async ({ page, account }) => {
  const path = "**/v1/connected-computers?*";
  await page.route(path, (route) => route.fulfill({ status: 403, json: { detail: "Organization permission required" } }));
  await page.goto("compute/computers");
  await page.getByLabel("Email или ник").fill(account.email);
  await page.getByLabel("Пароль", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Войти", exact: true }).click();
  await expect(page).toHaveURL(/compute\/computers$/);
  await expect(page.getByRole("alert")).toBeVisible();
  await page.unroute(path);
  await page.getByRole("alert").getByRole("button").click();
  await expect(page.getByText("Пока нет подключённых компьютеров", { exact: true })).toBeVisible();
  await expect(page.locator(".connected-pagination")).toHaveCount(0);
  const companyPath = "**/v1/companies?*";
  const personalPath = "**/v1/computers?user_id=*&limit=1";
  await page.route(companyPath, (route) => route.fulfill({ status: 503, json: { detail: "Temporary outage" } }));
  await page.getByRole("link", { name: "← К Compute" }).click();
  await expect(page.locator(".compute-status-summary dd")).toHaveText(["0", "0", "0"]);
  const poolEntry = page.getByRole("link", { name: "Вычислительные пулы" });
  await expect(poolEntry.locator("strong")).toHaveText("—");
  await page.unroute(companyPath);
  await page.getByRole("alert").getByRole("button").click();
  await expect(poolEntry.locator("strong")).toHaveText("1");
  await page.route(personalPath, (route) => route.fulfill({ status: 503, json: { detail: "Temporary outage" } }));
  await poolEntry.click();
  const pools = page.getByRole("region", { name: "Вычислительные пулы" });
  await expect(pools.getByRole("article")).toHaveCount(1);
  await expect(pools.getByTestId("organization-computer-count")).toHaveText("—");
  await page.unroute(personalPath);
  await page.getByRole("alert").getByRole("button").click();
  await expect(pools.getByTestId("organization-computer-count")).toHaveText("0");
});

test("connection rows retain their device identity after pool access changes", async ({ page, request, account }) => {
  const owner = await makeAccount(request);
  const shared = await createCompany(request, owner, "Организация с длинным названием для распределённых вычислений");
  const joined = await request.post(`${API}/memberships`, {
    headers: headers(owner),
    data: { company_id: shared.id, user_id: account.id, role: "admin" },
  });
  expect(joined.status()).toBe(201);
  const membership = await joined.json();
  const inCompany = await addComputer(request, account, "Рабочая станция с длинным именем", shared.id, false);
  const inPersonal = await addComputer(request, account, "Домашний Mac", undefined, false);
  for (const device of [inCompany, inPersonal]) await markAccountComputer(request, device.id);
  expect((await request.patch(`${API}/memberships/${membership.id}`, {
    headers: headers(owner), data: { permissions: ["resources.read"] },
  })).status()).toBe(200);
  await startSession(page, account);
  await page.setViewportSize({ width: 320, height: 740 });
  await page.goto("compute/computers");
  const row = page.locator(".connected-computer-row").filter({ hasText: inCompany.name });
  const transfer = row.getByRole("button", { name: /Сменить пул/ });
  await expect(row.getByRole("link", { name: shared.name })).toBeVisible();
  await expect(transfer).toBeDisabled();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(320);
  const unavailableMove = await request.patch(`${API}/connected-computers/${inCompany.id}/pool`, {
    headers: headers(account), data: { company_id: null, expected_company_id: shared.id },
  });
  expect(unavailableMove.status()).toBe(403);
  expect((await request.patch(`${API}/memberships/${membership.id}`, {
    headers: headers(owner), data: { permissions: null },
  })).status()).toBe(200);
  await page.reload();
  await transfer.click();
  const dialog = page.getByRole("dialog");
  const confirm = dialog.getByRole("button", { name: "Сменить пул", exact: true });
  await dialog.getByLabel("Новый пул").selectOption("personal");
  await expect(confirm).toBeEnabled();
  expect((await request.delete(`${API}/memberships/${membership.id}`, { headers: headers(owner) })).status()).toBe(204);
  await confirm.click();
  await expect(dialog.getByRole("alert")).toBeVisible();
  await expect(dialog).toContainText("Пул недоступен");
  await expect(confirm).toBeDisabled();
  await expect(dialog.getByLabel("Новый пул")).toBeDisabled();
  await dialog.getByRole("button", { name: "Закрыть диалог" }).click();
  await expect(row).toContainText("Пул недоступен");
  await expect(row.getByRole("link")).toHaveCount(0);
  await expect(transfer).toBeDisabled();
  await page.getByRole("button", { name: "Сменить пул — Домашний Mac", exact: true }).click();
  await expect(dialog).toContainText("Доступных пулов для переноса пока нет.");
  await expect(dialog.getByRole("button", { name: "Сменить пул", exact: true })).toBeDisabled();
});

test("pool picker displays a reservation conflict without moving the computer", async ({ page, request, account }) => {
  const pool = await createCompany(request, account, "Рабочий пул");
  const computer = await addComputer(request, account, "Занятый Mac");
  await markAccountComputer(request, computer.id);
  await reserve(request, account, 1000, GiB);
  await startSession(page, account);
  await page.goto("compute/computers");
  await page.getByRole("button", { name: "Сменить пул — Занятый Mac", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Новый пул").selectOption(pool.id);
  await dialog.getByRole("button", { name: "Сменить пул", exact: true }).click();
  await expect(dialog.getByRole("alert")).toContainText("Ресурсы компьютера зарезервированы.");
  await expect(dialog.getByLabel("Новый пул")).toHaveValue(pool.id);
  await dialog.getByRole("button", { name: "Отмена" }).click();
  await expect(page.getByRole("listitem").filter({ hasText: "Занятый Mac" }).getByRole("link", { name: "Личный пул" })).toBeVisible();
});

test("pool picker accepts another destination after the selected pool becomes unavailable", async ({ page, request, account }) => {
  const owner = await makeAccount(request);
  const removed = await createCompany(request, owner, "Временный пул");
  const available = await createCompany(request, account, "Доступный пул");
  const grant = await request.post(`${API}/memberships`, {
    headers: headers(owner), data: { company_id: removed.id, user_id: account.id, role: "admin" },
  });
  expect(grant.status()).toBe(201);
  const membership = await grant.json();
  const computer = await addComputer(request, account, "Мой Mac");
  await markAccountComputer(request, computer.id);
  await startSession(page, account);
  await page.goto("compute/computers");
  await page.getByRole("button", { name: "Сменить пул — Мой Mac" }).click();

  const dialog = page.getByRole("dialog");
  const select = dialog.getByLabel("Новый пул");
  const confirm = dialog.getByRole("button", { name: "Сменить пул", exact: true });
  await select.selectOption(removed.id);
  expect((await request.delete(`${API}/memberships/${membership.id}`, { headers: headers(owner) })).status()).toBe(204);
  const inventory = "**/v1/connected-computers?*";
  await page.route(inventory, (route) => route.fulfill({ status: 503, json: { detail: "Temporary outage" } }));
  await confirm.click();
  await expect(select.locator("option")).toHaveText(["Выберите пул", available.name]);
  await expect(select).toBeDisabled();
  await expect(confirm).toBeDisabled();
  const retry = dialog.getByRole("alert").getByRole("button");
  await expect(retry).toBeVisible();
  await page.unroute(inventory);
  await retry.click();
  await expect(select).toBeEnabled();
  await select.selectOption(available.id);
  await confirm.click();
  await expect(dialog).toHaveCount(0);
  await expect(page.getByRole("listitem").filter({ hasText: computer.name }).getByRole("link", { name: available.name })).toBeVisible();
});

test("a restored destination can be selected again after a denied move", async ({ page, request, account }) => {
  const owner = await makeAccount(request);
  const target = await createCompany(request, owner, "Пул сборки");
  const membershipData = { company_id: target.id, user_id: account.id, role: "admin" };
  const grant = await request.post(`${API}/memberships`, { headers: headers(owner), data: membershipData });
  expect(grant.status()).toBe(201);
  const membership = await grant.json();
  const computer = await addComputer(request, account, "Мой Mac");
  await markAccountComputer(request, computer.id);
  await startSession(page, account);
  await page.clock.install();
  await page.goto("compute/computers");
  await page.getByRole("button", { name: "Сменить пул — Мой Mac" }).click();
  const dialog = page.getByRole("dialog");
  const select = dialog.getByLabel("Новый пул");
  const submit = dialog.getByRole("button", { name: "Сменить пул", exact: true });
  await select.selectOption(target.id);
  expect((await request.delete(`${API}/memberships/${membership.id}`, { headers: headers(owner) })).status()).toBe(204);
  await submit.click();
  await expect(dialog.getByRole("alert")).toBeVisible();
  await expect(select.locator("option")).toHaveText(["Выберите пул"]);
  expect((await request.post(`${API}/memberships`, { headers: headers(owner), data: membershipData })).status()).toBe(201);
  await page.clock.fastForward(61_000);
  await expect(select.locator("option")).toHaveText(["Выберите пул", target.name]);
  await expect(select).toHaveValue("");
  await expect(submit).toBeDisabled();
  await select.selectOption(target.id);
  await submit.click();
  await expect(dialog).toHaveCount(0);
  await expect(page.getByRole("listitem").filter({ hasText: computer.name }).getByRole("link", { name: target.name })).toBeVisible();
});

test("a delayed leave receipt preserves the verified pool after rejoining its organization", async ({ page, request, account }) => {
  const owner = await makeAccount(request);
  const pool = await createCompany(request, owner, "Повторно доступный пул");
  const join = async () => {
    const member = await request.post(`${API}/memberships`, {
      headers: headers(owner), data: { company_id: pool.id, user_id: account.id, role: "admin" },
    });
    expect(member.status()).toBe(201);
    return (await member.json()).id as string;
  };
  const oldMembership = await join();
  const computer = await addComputer(request, account, "Мой Mac", pool.id);
  await markAccountComputer(request, computer.id);
  const leaveEndpoint = `${API}/memberships/${oldMembership}`;
  const leave = await pauseMutation(page, leaveEndpoint, { pause: "response" });
  try {
    await startSession(page, account);
    await page.goto("organizations");
    await page.getByRole("link", { name: `Открыть организацию: ${pool.name}` }).click();
    await openOrganizationSettings(page);
    await page.getByRole("button", { name: "Выйти из организации", exact: true }).click();
    const confirmation = page.getByRole("dialog", { name: "Подтвердите выход" });
    await confirmation.getByRole("textbox").fill(account.first_name);
    await confirmation.getByRole("button", { name: "Подтвердить", exact: true }).click();
    await leave.pending;
    expect(await join()).not.toBe(oldMembership);
    await page.goBack();
    await page.getByRole("navigation", { name: "Навигация кабинета" }).getByRole("link", { name: "Compute", exact: true }).click();
    await page.getByRole("link", { name: "Подключённые компьютеры", exact: true }).click();
    const row = page.getByRole("listitem").filter({ hasText: computer.name });
    await expect(row.getByRole("link", { name: pool.name })).toBeVisible();
    await page.route("**/v1/connected-computers?*", (route) => route.fulfill({ status: 503, json: { detail: "Temporary outage" } }));
    leave.release();
    await expect(page.getByRole("alert")).toBeVisible();
    await expect(row.getByRole("link", { name: pool.name })).toBeVisible();
    await expect(row.getByRole("button", { name: /Сменить пул/ })).toBeEnabled();
  } finally {
    leave.release();
  }
});

test("a delayed transfer receipt does not reveal the pool after access is revoked", async ({ page, request, account }) => {
  const owner = await makeAccount(request);
  const target = await createCompany(request, owner, "Покидаемый пул");
  const joined = await request.post(`${API}/memberships`, {
    headers: headers(owner), data: { company_id: target.id, user_id: account.id, role: "admin" },
  });
  expect(joined.status()).toBe(201);
  const member = await joined.json();
  const computer = await addComputer(request, account, "Мой Mac");
  await markAccountComputer(request, computer.id);
  const leaveEndpoint = `${API}/memberships/${member.id}`;
  const leave = await pauseMutation(page, leaveEndpoint);
  const transfer = await pauseMutation(page, `${API}/connected-computers/${computer.id}/pool`, { method: "PATCH", pause: "response" });

  try {
    await startSession(page, account);
    await page.goto("organizations");
    await page.getByRole("link", { name: `Открыть организацию: ${target.name}` }).click();
    await openOrganizationSettings(page);
    await page.getByRole("button", { name: "Выйти из организации", exact: true }).click();
    const confirmation = page.getByRole("dialog", { name: "Подтвердите выход" });
    await confirmation.getByRole("textbox").fill(account.first_name);
    await confirmation.getByRole("button", { name: "Подтвердить", exact: true }).click();
    await leave.pending;
    await page.goBack();
    await page.getByRole("navigation", { name: "Навигация кабинета" }).getByRole("link", { name: "Compute", exact: true }).click();
    await page.getByRole("link", { name: "Подключённые компьютеры", exact: true }).click();
    const row = page.locator(".connected-computer-row").filter({ hasText: computer.name });
    await row.getByRole("button", { name: /Сменить пул/ }).click();
    const dialog = page.getByRole("dialog", { name: "Сменить пул" });
    await dialog.getByLabel("Новый пул").selectOption(target.id);
    await dialog.getByRole("button", { name: "Сменить пул", exact: true }).click();
    await transfer.pending;
    const left = page.waitForResponse((response) => response.url() === leaveEndpoint && response.request().method() === "DELETE");
    leave.release();
    expect((await left).status()).toBe(204);
    await expect(row).toContainText("Пул недоступен");
    await page.route("**/v1/connected-computers?*", (route) => route.fulfill({ status: 503, json: { detail: "Temporary outage" } }));
    transfer.release();
    await expect(dialog).toHaveCount(0);
    await expect(page.getByRole("alert")).toBeVisible();
    await expect(row).toContainText("Пул недоступен");
    await expect(row.getByRole("link")).toHaveCount(0);
  } finally {
    leave.release();
    transfer.release();
  }
});

test("a lost response after a committed transfer is confirmed before rechecking the inventory", async ({ page, request, account }) => {
  const target = await createCompany(request, account, "Пул сборки");
  const computer = await addComputer(request, account, "Мой Mac");
  await markAccountComputer(request, computer.id);
  await startSession(page, account);
  await page.goto("compute/computers");
  const move = page.getByRole("button", { name: "Сменить пул — Мой Mac" });
  await move.click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Новый пул").selectOption(target.id);
  const inventory = "**/v1/connected-computers?*";
  await page.route(inventory, (route) => route.fulfill({ status: 503, json: { detail: "Temporary outage" } }));
  await page.route(`${API}/connected-computers/${computer.id}/pool`, async (route) => {
    expect((await route.fetch()).status()).toBe(200);
    await route.abort("failed");
  });
  await dialog.getByRole("button", { name: "Сменить пул", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  await expect(page.getByRole("status")).toContainText("Вычислительный пул изменён.");
  const row = page.locator(".connected-computer-row").filter({ hasText: computer.name });
  await expect(row).toContainText("Уточняем пул");
  await expect(move).toBeDisabled();
  await expect(row.getByRole("link")).toHaveCount(0);
  await page.unroute(inventory);
  await page.getByRole("alert").getByRole("button").click();
  await expect(row.getByRole("link", { name: target.name })).toBeVisible();
  await expect(move).toBeEnabled();
});

test("an interrupted transfer resolved before the original request reaches the API cannot take effect later", async ({ page, request, account }) => {
  const target = await createCompany(request, account, "Пул сборки");
  const computer = await addComputer(request, account, "Мой Mac");
  await markAccountComputer(request, computer.id);
  await startSession(page, account);
  await page.goto("compute/computers");
  const move = page.getByRole("button", { name: "Сменить пул — Мой Mac" });
  await move.click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Новый пул").selectOption(target.id);
  const endpoint = `${API}/connected-computers/${computer.id}/pool`;
  let key: string | undefined;
  let originalIntent: unknown;
  await page.route(endpoint, (route) => {
    key = route.request().headers()["idempotency-key"];
    originalIntent = route.request().postDataJSON();
    return route.abort("failed");
  });
  await dialog.getByRole("button", { name: "Сменить пул", exact: true }).click();
  await expect(dialog.getByRole("alert")).toContainText("Перенос не выполнен");
  expect(key).toBeTruthy();
  const late = await request.patch(endpoint, {
    headers: { ...headers(account), "Idempotency-Key": key! },
    data: originalIntent as Record<string, unknown>,
  });
  expect(late.status()).toBe(409);
  expect((await late.json()).detail).toBe("Computer pool change was cancelled");
  await page.keyboard.press("Escape");
  await expect(page.getByRole("link", { name: "Личный пул" })).toBeVisible();
  await expect(move).toBeEnabled();
});

test("failed transfer reconciliation remains retryable after reloading the page", async ({ page, request, account }) => {
  const target = await createCompany(request, account, "Пул сборки");
  const computer = await addComputer(request, account, "Мой Mac");
  await markAccountComputer(request, computer.id);
  await startSession(page, account);
  await page.goto("compute/computers");
  const move = page.getByRole("button", { name: "Сменить пул — Мой Mac" });
  await move.click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Новый пул").selectOption(target.id);
  const endpoint = `${API}/connected-computers/${computer.id}/pool`;
  await page.route(`${endpoint}/reconcile`, (route) => route.fulfill({ status: 503, json: { detail: "Temporary outage" } }));
  await page.route(endpoint, async (route) => {
    expect((await route.fetch()).status()).toBe(200);
    await route.abort("failed");
  });
  await dialog.getByRole("button", { name: "Сменить пул", exact: true }).click();
  const retryInDialog = dialog.getByRole("alert").getByRole("button");
  await expect(retryInDialog).toBeVisible();
  let finishModalCheck!: () => void;
  const holdModalCheck = new Promise<void>((resolve) => { finishModalCheck = resolve; });
  await page.route(`${endpoint}/reconcile`, async (route) => {
    await holdModalCheck;
    await route.fulfill({ status: 503, json: { detail: "Temporary outage" } });
  }, { times: 1 });
  try {
    await retryInDialog.focus();
    await page.keyboard.press("Enter");
    await expect(dialog.getByRole("heading")).toBeFocused();
  } finally {
    finishModalCheck();
  }
  await expect(retryInDialog).toBeFocused();
  const automaticCheck = page.waitForResponse((response) => response.url() === `${endpoint}/reconcile` && response.status() === 503);
  await page.reload();
  await automaticCheck;
  const row = page.locator(".connected-computer-row").filter({ hasText: computer.name });
  await expect(row).toContainText("Уточняем пул");
  await expect(row.getByRole("link")).toHaveCount(0);
  const retry = row.getByRole("button", { name: "Проверить перенос — Мой Mac" });
  const pageNumber = page.getByRole("navigation", { name: "Страница" }).getByRole("button", { name: "Страница 1" });
  let respondWithError!: () => void;
  const waitForFocus = new Promise<void>((resolve) => { respondWithError = resolve; });
  await page.route(`${endpoint}/reconcile`, async (route) => {
    await waitForFocus;
    await route.fulfill({ status: 503, json: { detail: "Temporary outage" } });
  });
  try {
    await retry.click();
    await expect(retry).toBeDisabled();
    await pageNumber.focus();
  } finally {
    respondWithError();
  }
  await expect(retry).toBeEnabled();
  await expect(pageNumber).toBeFocused();
  await page.unroute(`${endpoint}/reconcile`);
  await retry.click();
  await expect(row.getByRole("link", { name: target.name })).toBeVisible();
  await expect(move).toBeEnabled();
});

test("a second tab with a copied transfer waits for the original request", async ({ page, request, account }) => {
  const target = await createCompany(request, account, "Пул сборки");
  const computer = await addComputer(request, account, "Мой Mac");
  await markAccountComputer(request, computer.id);
  await startSession(page, account);
  await page.goto("compute/computers");
  test.skip(!await page.evaluate(() => Boolean(navigator.locks)), "Browser Web Locks support is required for this scenario");
  await page.getByRole("button", { name: "Сменить пул — Мой Mac" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Новый пул").selectOption(target.id);
  const endpoint = `${API}/connected-computers/${computer.id}/pool`;
  const transfer = await pauseMutation(page, endpoint, { method: "PATCH" });
  const sent = page.waitForRequest((req) => req.url() === endpoint && req.method() === "PATCH");
  const duplicate = await page.context().newPage();
  try {
    await dialog.getByRole("button", { name: "Сменить пул", exact: true }).click();
    const key = (await sent).headers()["idempotency-key"];
    await transfer.pending;
    const saved = await page.evaluate(() => sessionStorage.getItem("tmc-computer-pool-moves"));
    expect(saved).toContain(key);
    await duplicate.addInitScript((pending) => {
      if (pending) sessionStorage.setItem("tmc-computer-pool-moves", pending);
    }, saved);
    await duplicate.goto("compute/computers");
    await expect(duplicate.getByRole("button", { name: "Проверить перенос — Мой Mac" })).toBeVisible();
    await expect.poll(() => duplicate.evaluate(async (key) =>
      (await navigator.locks.query()).pending?.some(({ name }) => name === `tmc-pool-move:${key}`), key,
    )).toBe(true);
  } finally {
    transfer.release();
  }
  await expect(dialog).toHaveCount(0);
  await expect(page.getByRole("link", { name: target.name })).toBeVisible();
  await expect(duplicate.getByRole("link", { name: target.name })).toBeVisible();
  await duplicate.close();
});

test("an already open second tab shows the verified pool after a transfer", async ({ page, request, account }) => {
  const target = await createCompany(request, account, "Пул сборки");
  const computer = await addComputer(request, account, "Мой Mac");
  await markAccountComputer(request, computer.id);
  await startSession(page, account);
  await page.goto("compute/computers");
  const second = await page.context().newPage();
  try {
    await second.goto("compute/computers");
    const row = second.getByRole("listitem").filter({ hasText: computer.name });
    const oldPool = row.getByRole("link", { name: "Личный пул" });
    await expect(oldPool).toBeVisible();
    await oldPool.focus();
    await expect(oldPool).toBeFocused();
    const inventory = "**/v1/connected-computers?*";
    await second.route(inventory, (route) => route.fulfill({ status: 503, json: { detail: "Temporary outage" } }));
    await page.bringToFront();
    await page.getByRole("button", { name: "Сменить пул — Мой Mac" }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("Новый пул").selectOption(target.id);
    await dialog.getByRole("button", { name: "Сменить пул", exact: true }).click();
    await expect(dialog).toHaveCount(0);
    await second.bringToFront();
    await expect(row).toContainText("Уточняем пул");
    await expect(oldPool).toHaveCount(0);
    await expect(row).toBeFocused();
    await second.unroute(inventory);
    await second.getByRole("alert").getByRole("button").click();
    await expect(row.getByRole("link", { name: target.name })).toBeVisible();
  } finally {
    await second.close();
  }
});

for (const origin of ["list", "dialog"] as const) {
  test(`a transfer reconciled in a copied tab clears the previous error in the ${origin}`, async ({ page, request, account }) => {
    const target = await createCompany(request, account, "Пул сборки");
    const computer = await addComputer(request, account, "Мой Mac");
    await markAccountComputer(request, computer.id);
    await startSession(page, account);
    await page.goto("compute/computers");
    await page.getByRole("button", { name: "Сменить пул — Мой Mac" }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("Новый пул").selectOption(target.id);
    const endpoint = `${API}/connected-computers/${computer.id}/pool`;
    await page.route(`${endpoint}/reconcile`, (route) => route.fulfill({ status: 503, json: { detail: "Temporary outage" } }));
    await page.route(endpoint, async (route) => {
      expect((await route.fetch()).status()).toBe(200);
      await route.abort("failed");
    });
    await dialog.getByRole("button", { name: "Сменить пул", exact: true }).click();
    await expect(dialog.getByRole("alert").getByRole("button")).toBeVisible();
    const retry = page.getByRole("button", { name: "Проверить перенос — Мой Mac" });
    if (origin === "list") {
      await page.keyboard.press("Escape");
      await expect(retry).toBeVisible();
      await retry.click();
      await expect(page.getByRole("alert")).toBeVisible();
    } else {
      const verify = dialog.getByRole("alert").getByRole("button");
      await verify.click();
      await expect(verify).toBeFocused();
    }
    const saved = await page.evaluate(() => sessionStorage.getItem("tmc-computer-pool-moves"));
    const second = await page.context().newPage();
    try {
      await second.addInitScript((pending) => {
        if (pending) sessionStorage.setItem("tmc-computer-pool-moves", pending);
      }, saved);
      await second.goto("compute/computers");
      await expect(second.getByRole("link", { name: target.name })).toBeVisible();
      if (origin === "list") {
        await expect(page.getByRole("link", { name: target.name })).toBeVisible();
        await expect(retry).toHaveCount(0);
        await expect(page.getByRole("alert")).toHaveCount(0);
      } else {
        await expect(dialog.locator("strong")).toHaveText(target.name);
        await expect(dialog.getByRole("alert")).toHaveText("Пул уже изменился. Откройте смену пула ещё раз.");
        await expect(dialog.getByRole("button", { name: "Закрыть диалог", exact: true })).toBeFocused();
      }
    } finally {
      await second.close();
    }
  });
}

test("leaving an organization hides its pool in another tab even while revalidation fails", async ({ page, request, account }) => {
  const owner = await makeAccount(request);
  const pool = await createCompany(request, owner, "Покидаемый пул");
  await addMember(request, owner, pool.id, account, "admin");
  const computer = await addComputer(request, account, "Мой Mac", pool.id);
  await markAccountComputer(request, computer.id);
  await startSession(page, account);
  const second = await page.context().newPage();
  try {
    await second.goto("compute/computers");
    const row = second.getByRole("listitem").filter({ hasText: computer.name });
    await expect(row.getByRole("link", { name: pool.name })).toBeVisible();
    await second.route("**/v1/connected-computers?*", (route) => route.fulfill({ status: 503, json: { detail: "Temporary outage" } }));
    await page.goto("organizations");
    await page.getByRole("link", { name: `Открыть организацию: ${pool.name}` }).click();
    await openOrganizationSettings(page);
    await page.getByRole("button", { name: "Выйти из организации", exact: true }).click();
    const confirmation = page.getByRole("dialog", { name: "Подтвердите выход" });
    await confirmation.getByRole("textbox").fill(account.first_name);
    await confirmation.getByRole("button", { name: "Подтвердить", exact: true }).click();
    await expect(second.getByRole("alert")).toBeVisible();
    await expect(row).toContainText("Пул недоступен");
    await expect(row.getByRole("link")).toHaveCount(0);
    await expect(row.getByRole("button", { name: /Сменить пул/ })).toBeDisabled();
  } finally {
    await second.close();
  }
});

test("renaming an organization updates the pool name in another open tab", async ({ page, request, account }) => {
  const pool = await createCompany(request, account, "Старое название пула");
  const computer = await addComputer(request, account, "Мой Mac", pool.id);
  await markAccountComputer(request, computer.id);
  await startSession(page, account);
  const second = await page.context().newPage();
  try {
    await second.goto("compute/computers");
    await expect(second.getByRole("link", { name: pool.name })).toBeVisible();
    await page.goto("organizations");
    await page.getByRole("link", { name: `Открыть организацию: ${pool.name}` }).click();
    const settings = await openOrganizationSettings(page);
    const name = settings.getByLabel("Название организации", { exact: true });
    const rename = page.waitForResponse((response) =>
      response.request().method() === "PATCH" && response.url() === `${API}/companies/${pool.id}` && response.ok(),
    );
    await name.fill("Новое название пула");
    await name.press("Enter");
    await rename;
    await expect(second.getByRole("link", { name: "Новое название пула" })).toBeVisible();
    await expect(second.getByRole("link", { name: pool.name })).toHaveCount(0);
  } finally {
    await second.close();
  }
});

test("a pool created in another tab appears in the open Compute catalog", async ({ page, account }) => {
  const name = "Новый вычислительный пул";
  await startSession(page, account);
  await page.goto("compute/pools");
  const catalog = page.getByRole("region", { name: "Вычислительные пулы" });
  await expect(catalog.getByRole("article")).toHaveCount(1);
  const otherTab = await page.context().newPage();
  try {
    await otherTab.goto("organizations");
    await otherTab.getByRole("button", { name: "Создать организацию", exact: true }).click();
    const dialog = otherTab.getByRole("dialog", { name: "Создать организацию" });
    await dialog.getByLabel("Название организации").fill(name);
    await dialog.getByRole("button", { name: "Создать организацию", exact: true }).click();
    await expect(dialog).toHaveCount(0);
    await expect(catalog.getByRole("link", { name: `Открыть пул ${name}` })).toBeVisible();
  } finally {
    await otherTab.close();
  }
});

for (const openInvitation of ["link", "organization directory"] as const) {
  test(`Compute counts an invitation accepted from the ${openInvitation} in another tab`, async ({ page, request, account }) => {
    const owner = await makeAccount(request);
    const pool = await createCompany(request, owner, "Пул по приглашению");
    const invite = await request.post(`${API}/invitations`, {
      headers: headers(owner),
      data: { company_id: pool.id, ...(openInvitation === "organization directory" ? { identifier: account.email } : {}) },
    });
    expect(invite.status()).toBe(201);
    const invitation = await invite.json();
    await startSession(page, account);
    await page.goto("compute");
    const pools = page.getByRole("link", { name: "Вычислительные пулы", exact: true });
    await expect(pools.locator("strong")).toHaveText("1");
    const otherTab = await page.context().newPage();
    try {
      await otherTab.goto(openInvitation === "link" ? `invitations/${invitation.token}` : "organizations");
      const accepted = otherTab.waitForResponse((response) =>
        response.request().method() === "POST" && response.url().endsWith("/accept") && response.ok(),
      );
      await otherTab.getByRole("button", { name: "Принять приглашение", exact: true }).click();
      await accepted;
      await expect(pools.locator("strong")).toHaveText("2");
    } finally {
      await otherTab.close();
    }
  });
}

test("a pool detail updates its membership after invitation and external removal", async ({ page, request, account }) => {
  const owner = await makeAccount(request);
  const pool = await createCompany(request, owner, "Пул по приглашению");
  const created = await request.post(`${API}/invitations`, {
    headers: headers(owner), data: { company_id: pool.id },
  });
  expect(created.status()).toBe(201);
  const invitation = await created.json();
  await startSession(page, account);
  await page.clock.install();
  await page.goto(`compute/pools/${pool.id}`);
  const card = page.getByRole("region", { name: pool.name, exact: true });
  const notFound = page.getByText("Страница не найдена", { exact: true });
  await expect(notFound).toBeVisible();
  const invitationPage = await page.context().newPage();
  try {
    await invitationPage.goto(`invitations/${invitation.token}`);
    await invitationPage.getByRole("button", { name: "Принять приглашение", exact: true }).click();
    await expect(card).toBeVisible();
  } finally {
    await invitationPage.close();
  }
  await page.bringToFront();
  const members = await request.get(`${API}/memberships?company_id=${pool.id}`, { headers: headers(owner) });
  expect(members.ok()).toBe(true);
  const membership = (await members.json()).find((row: { user_id: string }) => row.user_id === account.id);
  expect(membership).toBeTruthy();
  const removed = await request.delete(`${API}/memberships/${membership.id}`, { headers: headers(owner) });
  expect(removed.status()).toBe(204);
  await page.clock.fastForward(61_000);
  await expect(notFound).toBeVisible();
  await expect(card).toHaveCount(0);
});

for (const scope of ["personal", "organization"] as const) {
  for (const preview of [false, true]) test(`a confirmed transfer removes the moved computer from its former ${scope} ${preview ? "preview before refresh" : "inventory during a read outage"}`, async ({
    page, request, account, context,
  }) => {
    const source = scope === "organization" ? await createCompany(request, account, "Пул до переноса") : null;
    const owner = await makeAccount(request);
    const destination = await createCompany(request, owner, "Пул с правом переноса");
    await addMember(request, owner, destination.id, account, "user", ["resources.manage"]);
    const moving = await addComputer(request, account, "Mac для переноса", source?.id);
    const staying = await addComputer(request, account, "Mac остаётся в пуле", source?.id);
    await markAccountComputer(request, moving.id);
    await startSession(page, account);
    await page.goto("compute/computers");
    await page.getByRole("link", { name: source?.name ?? "Личный пул", exact: true }).click();
    if (!preview) await page.getByRole("link", { name: /Список компьютеров/ }).click();
    const computerLink = (name: string) => preview ? page.locator("a.organization-computer-card").filter({ hasText: name })
      : page.getByRole("link", { name, exact: true });
    const oldLink = computerLink(moving.name);
    const otherLink = computerLink(staying.name);
    await expect(oldLink).toBeVisible();
    await expect(otherLink).toBeVisible();
    const filter = source ? `company_id=${source.id}` : `user_id=${account.id}`;
    const pool = source ? `companies/${source.id}` : `users/${account.id}`;
    const list = preview ? `${API}/${pool}/resources/pool-overview` : `${API}/computers?${filter}&only_available=false&limit=10&offset=0`;
    let releaseRead = () => {};
    if (preview) {
      const held = new Promise<void>((resolve) => { releaseRead = resolve; });
      await page.route(list, async (route) => {
        await held;
        await route.continue().catch(() => {});
      });
    } else await temporaryReadFailure(page, list);
    const mover = await context.newPage();
    try {
      await mover.goto("compute/computers");
      await mover.getByRole("button", { name: `Сменить пул — ${moving.name}`, exact: true }).click();
      const dialog = mover.getByRole("dialog", { name: "Сменить пул" });
      await dialog.getByLabel("Новый пул").selectOption(destination.id);
      const updated = preview ? page.waitForRequest(list) :
        page.waitForResponse((response) => response.url() === list && response.status() === 503);
      await dialog.getByRole("button", { name: "Сменить пул", exact: true }).click();
      await expect(mover.getByRole("link", { name: destination.name, exact: true })).toBeVisible();
      await updated;
      const actual = await request.get(list, { headers: headers(account) });
      expect(actual.status()).toBe(200);
      const snapshot = await actual.json();
      expect((preview ? snapshot.computers : snapshot).map((computer: { id: string }) => computer.id)).toEqual([staying.id]);
      expect((await request.get(`${API}/computers/${moving.id}`, { headers: headers(account) })).status()).toBe(403);
      await expect(otherLink).toBeVisible();
      await expect(oldLink).toHaveCount(0);
    } finally {
      releaseRead();
      await mover.close();
    }
  });

  for (const { connected, ordered } of [
    { connected: false, ordered: false },
    { connected: true, ordered: false },
    { connected: true, ordered: true },
  ]) test(`a returned computer keeps its ${scope} ${connected ? "pool link" : "inventory row"} with ${ordered ? "ordered" : "delayed"} transfer responses`, async ({ page, request, account, context }) => {
    const source = scope === "organization" ? await createCompany(request, account, "Исходный пул с инвентарём") : null;
    const target = await createCompany(request, account, "Временный пул переноса");
    const computer = await addComputer(request, account, "Возвращаемый Mac", source?.id);
    await markAccountComputer(request, computer.id);
    await startSession(page, account);
    const sourceName = source?.name ?? "Личный пул";
    await page.goto("compute/computers");
    if (!connected) {
      await page.getByRole("link", { name: sourceName, exact: true }).click();
      await page.getByRole("link", { name: /Список компьютеров/ }).click();
    }
    const connectedRow = page.getByRole("listitem").filter({ hasText: computer.name });
    const link = connected ? connectedRow.getByRole("link", { name: sourceName, exact: true })
      : page.getByRole("link", { name: computer.name, exact: true });
    await expect(link).toBeVisible();
    const owner = source ? `company_id=${source.id}` : `user_id=${account.id}`;
    const list = connected ? `${API}/connected-computers?limit=10&offset=0`
      : `${API}/computers?${owner}&only_available=false&limit=10&offset=0`;
    await temporaryReadFailure(page, list);
    const first = await context.newPage();
    const second = await context.newPage();
    const endpoint = `${API}/connected-computers/${computer.id}/pool`;
    const transfer = await pauseMutation(first, endpoint, { method: "PATCH", pause: "response" });
    const move = async (tab: Page, destination: string) => {
      await tab.goto("compute/computers");
      await tab.getByRole("button", { name: `Сменить пул — ${computer.name}`, exact: true }).click();
      const dialog = tab.getByRole("dialog", { name: "Сменить пул" });
      await dialog.getByLabel("Новый пул").selectOption(destination);
      await dialog.getByRole("button", { name: "Сменить пул", exact: true }).click();
    };
    try {
      await move(first, target.id);
      await transfer.pending;
      const rechecked = () => page.waitForResponse((response) => response.url() === list && response.status() === 503);
      if (ordered) {
        const afterMove = rechecked();
        transfer.release();
        await (await afterMove).finished();
        await expect(connectedRow).toContainText("Уточняем пул");
        await expect(link).toHaveCount(0);
      }
      const afterReturn = rechecked();
      await move(second, source?.id ?? "personal");
      await expect(second.getByRole("link", { name: sourceName, exact: true })).toBeVisible();
      await (await afterReturn).finished();
      await expect(link).toBeVisible();
      if (!ordered) {
        const afterOldResponse = rechecked();
        transfer.release();
        await (await afterOldResponse).finished();
      }
      const actual = await request.get(list, { headers: headers(account) });
      expect(actual.status()).toBe(200);
      const inventory = await actual.json();
      expect((connected ? inventory.items : inventory).map((entry: { id: string }) => entry.id)).toContain(computer.id);
      await expect(link).toBeVisible();
      if (connected) {
        await expect(connectedRow.getByRole("button", { name: /Сменить пул/ })).toBeDisabled();
        await link.press("Enter");
        await expect(page).toHaveURL(source ? new RegExp(`/organizations/${source.id}$`) : /\/compute\/pools\/personal$/);
      }
    } finally {
      transfer.release();
      await first.close();
      await second.close();
    }
  });

  for (const outcome of ["receipt", "rejection", "cancellation"] as const) test(`the ${scope} pool keeps moving enabled after a ${outcome} older than a verified connected row`, async ({ page, request, account, context }) => {
    const source = scope === "organization" ? await createCompany(request, account, "Исходная команда компьютера") : null;
    const rejected = outcome === "rejection";
    const cancelled = outcome === "cancellation";
    const owner = rejected ? await makeAccount(request) : account;
    const target = await createCompany(request, owner, "Пул обновлённой строки");
    const membership = rejected ? await addMember(request, owner, target.id, account, "user", ["resources.read", "resources.manage"]) : null;
    const setPermissions = async (permissions: string[]) => {
      expect((await request.patch(`${API}/memberships/${membership}`, {
        headers: headers(owner), data: { permissions },
      })).status()).toBe(200);
    };
    const computer = await addComputer(request, account, "Mac со свежей строкой", source?.id);
    await markAccountComputer(request, computer.id);
    await startSession(page, account);
    await page.goto("compute/computers");
    const row = page.getByRole("listitem").filter({ hasText: computer.name });
    const sourceName = source?.name ?? "Личный пул";
    await expect(row.getByRole("link", { name: sourceName, exact: true })).toBeVisible();
    const mutation = `${API}/connected-computers/${computer.id}/pool`;
    const list = `${API}/connected-computers?limit=10&offset=0`;
    const other = await context.newPage();
    const notifying = cancelled ? `${mutation}/reconcile` : mutation;
    const delayed = await pauseMutation(other, notifying, { method: cancelled ? "POST" : "PATCH", pause: "response" });
    const move = (tab: Page) => tab.getByRole("button", { name: `Сменить пул — ${computer.name}`, exact: true });
    try {
      await other.goto("compute/computers");
      await move(other).click();
      const first = other.getByRole("dialog", { name: "Сменить пул" });
      await first.getByLabel("Новый пул").selectOption(target.id);
      if (rejected) await setPermissions(["resources.read"]);
      if (cancelled) {
        const origin = new URL(page.url()).origin;
        await other.route(mutation, (route) => route.request().method() === "PATCH" ? route.fulfill({
          status: 503, headers: { "access-control-allow-origin": origin, "access-control-allow-credentials": "true" },
          json: { detail: "The first request did not reach the API" },
        }) : route.continue());
      }
      await first.getByRole("button", { name: "Сменить пул", exact: true }).click();
      await delayed.pending;
      if (rejected) await setPermissions(["resources.read", "resources.manage"]);
      const read = page.waitForResponse((response) => response.url() === list && response.status() === 200);
      await page.evaluate(() => window.dispatchEvent(new Event("online")));
      const fresh = await read;
      const freshLink = row.getByRole("link", { name: outcome === "receipt" ? target.name : sourceName, exact: true });
      await expect(freshLink).toBeVisible();
      await temporaryReadFailure(page, list);
      const staleRead = page.waitForResponse((response) => response.url() === list && response.status() === 503);
      const olderReceipt = other.waitForResponse((response) =>
        response.url() === notifying && response.status() === (rejected ? 403 : 200));
      delayed.release();
      const receipt = await olderReceipt;
      if (cancelled) expect(await receipt.json()).toBe(false);
      expect(Number(fresh.headers()["x-computer-pool-check-revision"]))
        .toBeGreaterThanOrEqual(Number(receipt.headers()["x-computer-pool-check-revision"]));
      await (await staleRead).finished();
      await expect(freshLink).toBeVisible();
      await expect(move(page)).toBeEnabled();
      await move(page).click();
      const another = page.getByRole("dialog", { name: "Сменить пул" });
      await another.getByLabel("Новый пул").selectOption(outcome === "receipt" ? source?.id ?? "personal" : target.id);
      const saved = page.waitForResponse((response) => response.url() === mutation && response.request().method() === "PATCH");
      await another.getByRole("button", { name: "Сменить пул", exact: true }).click();
      expect((await saved).status()).toBe(200);
    } finally {
      delayed.release();
      await other.close();
    }
  });

  test(`the ${scope} pool shows loading for a confirmed arrival in an empty preview`, async ({ page, request, account, context }) => {
    const company = await createCompany(request, account, "Команда для проверки прибытия");
    const personal = scope === "personal";
    const computer = await addComputer(request, account, "Прибывающий Mac", personal ? company.id : undefined);
    await markAccountComputer(request, computer.id);
    await startSession(page, account);
    await page.goto(personal ? "compute/pools/personal" : `organizations/${company.id}`);
    const preview = page.getByRole("region", { name: "Компьютеры", exact: true });
    const empty = preview.getByText(/пока нет компьютеров/);
    await expect(empty).toBeVisible();
    const overview = `${API}/${personal ? `users/${account.id}` : `companies/${company.id}`}/resources/pool-overview`;
    let release!: () => void;
    const held = new Promise<void>((resolve) => { release = resolve; });
    const other = await context.newPage();
    try {
      await page.route(overview, async (route) => {
        await held;
        await route.continue().catch(() => {});
      });
      const routine = page.waitForRequest(overview);
      await page.evaluate(() => window.dispatchEvent(new Event("online")));
      await routine;
      await expect(empty).toBeVisible();
      await other.goto("compute/computers");
      const row = other.getByRole("listitem").filter({ hasText: computer.name });
      await row.getByRole("button", { name: /Сменить пул/ }).click();
      const dialog = other.getByRole("dialog", { name: "Сменить пул" });
      await dialog.getByLabel("Новый пул").selectOption(personal ? "personal" : company.id);
      const reloading = page.waitForRequest(overview);
      await dialog.getByRole("button", { name: "Сменить пул", exact: true }).click();
      await expect(row.getByRole("link", { name: personal ? "Личный пул" : company.name, exact: true })).toBeVisible();
      await reloading;
      const confirmed = await request.get(overview, { headers: headers(account) });
      expect(confirmed.status()).toBe(200);
      expect((await confirmed.json()).computers[0].id).toBe(computer.id);
      await expect(preview.getByText("Загрузка…", { exact: true })).toBeVisible();
      await expect(empty).toHaveCount(0);
      release();
      await expect(preview.locator("a.organization-computer-card").filter({ hasText: computer.name })).toBeVisible();
    } finally {
      release();
      await other.close();
    }
  });

  test(`the ${scope} pool shows loading when every preview computer leaves before the inventory refresh`, async ({ page, request, account, context }) => {
    const origin = scope === "organization" ? await createCompany(request, account, "Пул с четырьмя компьютерами") : null;
    const destination = await createCompany(request, account, "Пул назначения трёх компьютеров");
    for (let index = 1; index <= 4; index++) {
      const computer = await addComputer(request, account, `Mac номер ${index}`, origin?.id);
      await markAccountComputer(request, computer.id);
    }
    await startSession(page, account);
    await page.goto("compute/computers");
    await page.getByRole("link", { name: origin?.name ?? "Личный пул", exact: true }).first().click();
    const preview = page.getByRole("region", { name: "Компьютеры", exact: true });
    const cards = preview.locator("a.organization-computer-card");
    await expect(cards).toHaveCount(3);
    const names = await cards.locator(".organization-computer-identity strong").allTextContents();
    const scopePath = origin ? `companies/${origin.id}` : `users/${account.id}`;
    const overview = `${API}/${scopePath}/resources/pool-overview`;
    let release!: () => void;
    const held = new Promise<void>((resolve) => { release = resolve; });
    await page.route(overview, async (route) => {
      await held;
      await route.continue().catch(() => {});
    });
    const mover = await context.newPage();
    try {
      await mover.goto("compute/computers");
      for (const name of names) {
        const row = mover.getByRole("listitem").filter({ hasText: name });
        await row.getByRole("button", { name: /Сменить пул/ }).click();
        const dialog = mover.getByRole("dialog", { name: "Сменить пул" });
        await dialog.getByLabel("Новый пул").selectOption(destination.id);
        await dialog.getByRole("button", { name: "Сменить пул", exact: true }).click();
        await expect(row.getByRole("link", { name: destination.name, exact: true })).toBeVisible();
      }
      await expect(cards).toHaveCount(0);
      const actual = await request.get(overview, { headers: headers(account) });
      expect(actual.status()).toBe(200);
      const remaining = (await actual.json()).computers;
      expect(remaining).toHaveLength(1);
      await expect(preview.getByText("Загрузка…", { exact: true })).toBeVisible();
      release();
      await expect(cards).toHaveCount(1);
      await expect(cards.first()).toContainText(remaining[0].name);
    } finally {
      release();
      await mover.close();
    }
  });

  test(`confirmed reconciliation refreshes the actual ${scope} pool after a second transfer`, async ({ page, request, account, context }) => {
    const source = await createCompany(request, account, "Исходная команда");
    const expected = await createCompany(request, account, "Первый пул");
    const actual = scope === "organization" ? await createCompany(request, account, "Итоговая команда") : null;
    const present = await addComputer(request, account, "Mac в итоговом пуле", actual?.id);
    const moving = await addComputer(request, account, "Mac с потерянным ответом переноса", source.id);
    for (const computer of [present, moving]) await markAccountComputer(request, computer.id);
    await startSession(page, account);
    await page.goto("compute/computers");
    const actualName = actual?.name ?? "Личный пул";
    await page.getByRole("link", { name: actualName, exact: true }).click();
    const cards = page.locator("a.organization-computer-card");
    await expect(cards.filter({ hasText: present.name })).toBeVisible();
    const currentCard = cards.filter({ hasText: moving.name });
    await expect(currentCard).toHaveCount(0);
    const mover = await context.newPage();
    const mutation = `${API}/connected-computers/${moving.id}/pool`;
    const origin = new URL(page.url()).origin;
    try {
      await mover.route(mutation, async (route) => {
        if (route.request().method() !== "PATCH") return route.continue();
        expect((await route.fetch()).status()).toBe(200);
        const second = await request.patch(mutation, {
          headers: headers(account),
          data: { company_id: actual?.id ?? null, expected_company_id: expected.id },
        });
        expect(second.status()).toBe(200);
        await route.fulfill({
          status: 503,
          headers: { "access-control-allow-origin": origin, "access-control-allow-credentials": "true" },
          json: { detail: "Lost first transfer response" },
        });
      });
      await mover.goto("compute/computers");
      const row = mover.getByRole("listitem").filter({ hasText: moving.name });
      await row.getByRole("button", { name: /Сменить пул/ }).click();
      const dialog = mover.getByRole("dialog", { name: "Сменить пул" });
      await dialog.getByLabel("Новый пул").selectOption(expected.id);
      const checked = mover.waitForResponse((response) => response.url() === `${mutation}/reconcile` && response.status() === 200);
      await dialog.getByRole("button", { name: "Сменить пул", exact: true }).click();
      const receipt = await checked;
      expect(await receipt.json()).toBe(true);
      expect(receipt.headers()["x-organization-id"]).toBe(actual?.id);
      await expect(row.getByRole("link", { name: actualName, exact: true })).toBeVisible();
      await expect(currentCard).toBeVisible();
    } finally {
      await mover.close();
    }
  });
}

test("the scoped inventory obeys a newer denial from a replacement organization membership", async ({ page, request, account, context }) => {
  const owner = await makeAccount(request);
  const company = await createCompany(request, owner, "Пул с новым членством");
  const previous = await addMember(request, owner, company.id, account, "user", ["resources.manage", "resources.read"]);
  const currentComputer = await addComputer(request, account, "Mac в защищённом инвентаре", company.id);
  const moving = await addComputer(request, account, "Mac для разрешённого переноса");
  for (const computer of [currentComputer, moving]) await markAccountComputer(request, computer.id);
  await startSession(page, account);
  await page.goto("compute/computers");
  await page.getByRole("link", { name: company.name, exact: true }).click();
  await page.getByRole("link", { name: /Список компьютеров/ }).click();
  const row = page.getByRole("link", { name: currentComputer.name, exact: true });
  await expect(row).toBeVisible();
  const list = `${API}/computers?company_id=${company.id}&only_available=false&limit=10&offset=0`;
  await temporaryReadFailure(page, list);
  expect((await request.delete(`${API}/memberships/${previous}`, { headers: headers(owner) })).status()).toBe(204);
  const current = await addMember(request, owner, company.id, account, "user", ["resources.manage"]);
  const mover = await context.newPage();
  try {
    await mover.goto("compute/computers");
    await mover.getByRole("button", { name: `Сменить пул — ${moving.name}`, exact: true }).click();
    const dialog = mover.getByRole("dialog", { name: "Сменить пул" });
    await dialog.getByLabel("Новый пул").selectOption(company.id);
    const read = page.waitForResponse((response) => response.url() === list && response.status() === 503);
    await dialog.getByRole("button", { name: "Сменить пул", exact: true }).click();
    await (await read).finished();
    const actual = await request.get(list, { headers: headers(account) });
    expect(actual.status()).toBe(403);
    expect(actual.headers()["x-organization-membership-id"]).toBe(current);
    await expect(row).toHaveCount(0);
  } finally {
    await mover.close();
  }
});

test("a move refreshes its affected chart without disturbing an unrelated computer list", async ({ page, request, account }) => {
  const observed = await createCompany(request, account, "Отдельный пул");
  const destination = await createCompany(request, account, "Пул назначения");
  const stays = await addComputer(request, account, "Стенд в отдельном пуле", observed.id);
  const historyComputer = await addComputer(request, account, "Стенд с историей", destination.id, false);
  const moved = await addComputer(request, account, "Мой Mac", undefined, false);
  await markAccountComputer(request, moved.id);
  const sample = await request.post("http://127.0.0.1:18100/__test__/history", {
    data: { computer_id: historyComputer.id, pattern: "full" },
  });
  expect(sample.ok()).toBe(true);
  await startSession(page, account);
  await page.goto(`compute/computers?company_id=${observed.id}`);
  const link = page.getByRole("link", { name: stays.name, exact: true });
  await link.focus();
  let inventoryReads = 0;
  page.on("request", (incoming) => {
    if (incoming.url().startsWith(`${API}/computers?company_id=${observed.id}&`)) inventoryReads++;
  });
  const chart = await page.context().newPage();
  const other = await page.context().newPage();
  try {
    await chart.clock.install({ time: new Date("2026-01-08T00:00:00Z") });
    await chart.goto(`compute/pools/${destination.id}`);
    const series = chart.getByRole("button", { name: "CPU · мощность", exact: true });
    await series.focus();
    await series.press("Enter");
    await expect(series).toHaveAttribute("aria-pressed", "false");
    const summary = `${API}/companies/${destination.id}/resources/summary?only_available=true`;
    await chart.route(summary, (route) => route.fulfill({ status: 503, json: { detail: "Temporary outage" } }));
    await other.goto("compute/computers");
    await other.getByRole("button", { name: `Сменить пул — ${moved.name}`, exact: true }).click();
    const dialog = other.getByRole("dialog");
    await dialog.getByLabel("Новый пул").selectOption(destination.id);
    await dialog.getByRole("button", { name: "Сменить пул", exact: true }).click();
    await expect(chart.getByRole("alert")).toBeVisible();
    await expect(series).toBeFocused();
    await expect(series).toHaveAttribute("aria-pressed", "false");
    expect(inventoryReads).toBe(0);
    await page.bringToFront();
    await expect(link).toBeFocused();
    await chart.unroute(summary);
    await chart.getByRole("alert").getByRole("button").click();
    await expect(chart.getByRole("alert")).toHaveCount(0);
    await expect(series).toHaveAttribute("aria-pressed", "false");
  } finally {
    await other.close();
    await chart.close();
  }
});

test("a pool detail refreshes its count after a move from another client", async ({ page, request, account }) => {
  const pool = await createCompany(request, account, "Пул назначения");
  const computer = await addComputer(request, account, "Мой Mac");
  await markAccountComputer(request, computer.id);
  await startSession(page, account);
  await page.clock.install();
  await page.goto(`compute/pools/${pool.id}`);
  const count = page.getByRole("region", { name: pool.name }).getByTestId("computer-count");
  await expect(count).toHaveText("0");
  const moved = await request.patch(`${API}/connected-computers/${computer.id}/pool`, {
    headers: headers(account), data: { company_id: pool.id, expected_company_id: null },
  });
  expect(moved.status()).toBe(200);
  await page.clock.fastForward(61_000);
  await expect(count).toHaveText("1");
});

test("an external membership removal keeps keyboard focus in the remaining pool catalog", async ({ page, request, account }) => {
  const owner = await makeAccount(request);
  const pool = await createCompany(request, owner, "Временный пул");
  const added = await request.post(`${API}/memberships`, {
    headers: headers(owner), data: { company_id: pool.id, user_id: account.id, role: "user" },
  });
  expect(added.status()).toBe(201);
  const { id } = await added.json();
  await startSession(page, account);
  await page.clock.install();
  await page.goto("compute/pools");
  const link = page.getByRole("link", { name: `Открыть пул ${pool.name}` });
  await link.focus();
  await expect(link).toBeFocused();
  const removed = await request.delete(`${API}/memberships/${id}`, { headers: headers(owner) });
  expect(removed.status()).toBe(204);
  await page.clock.fastForward(61_000);
  await expect(link).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Открыть пул Личный пул" })).toBeFocused();
});

test("a focused pool control moves to Back when another client revokes resource reading", async ({ page, request, account }) => {
  const owner = await makeAccount(request);
  const pool = await createCompany(request, owner, "Пул с доступом на чтение");
  const membership = await request.post(`${API}/memberships`, {
    headers: headers(owner),
    data: { company_id: pool.id, user_id: account.id, role: "user", permissions: ["members.read", "resources.read"] },
  });
  expect(membership.status()).toBe(201);
  const { id } = await membership.json();
  await startSession(page, account);
  await page.clock.install();
  await page.goto(`compute/pools/${pool.id}`);
  const card = page.getByRole("region", { name: pool.name });
  const inventory = card.getByRole("link", { name: "Список компьютеров" });
  await inventory.focus();
  await expect(inventory).toBeFocused();
  const revoked = await request.patch(`${API}/memberships/${id}`, {
    headers: headers(owner), data: { permissions: ["members.read"] },
  });
  expect(revoked.status()).toBe(200);
  await page.clock.fastForward(61_000);
  await expect(inventory).toHaveCount(0);
  await expect(page.getByRole("link", { name: /К списку пулов/ })).toBeFocused();
  const members = card.getByRole("link", { name: /Участники/ });
  await members.focus();
  const refreshed = page.waitForResponse(`${API}/companies/${pool.id}`);
  await page.clock.fastForward(61_000);
  await refreshed;
  await expect(members).toBeFocused();
});

for (const entry of ["pool", "organization"]) {
  test(`an external pool move refreshes open inventory from the ${entry} and removes details the viewer can no longer see`, async ({ page, request, account }) => {
    const source = await createCompany(request, account, "Исходный пул");
    const viewer = await makeAccount(request);
    await addMember(request, account, source.id, viewer, "user", ["resources.read"]);
    const computer = await addComputer(request, account, "Переносимый Mac", source.id);
    const remaining = await addComputer(request, account, "Mac в исходном пуле", source.id);
    await markAccountComputer(request, computer.id);
    await startSession(page, viewer);
    await page.clock.install();
    await page.goto(entry === "pool" ? `compute/pools/${source.id}` : `organizations/${source.id}`);
    await page.getByRole("link", { name: "Список компьютеров" }).click();
    const movingLink = page.getByRole("link", { name: computer.name, exact: true });
    await expect(movingLink).toBeVisible();
    const href = await movingLink.getAttribute("href");
    expect(href).toBeTruthy();
    const detail = await page.context().newPage();
    try {
      await detail.clock.install();
      await detail.goto(href!);
      const title = detail.getByRole("heading", { name: `${computer.name}.`, exact: true });
      await expect(title).toBeVisible();
      await detail.getByRole("button", { name: "Обновить", exact: true }).focus();
      const moved = await request.patch(`${API}/connected-computers/${computer.id}/pool`, {
        headers: headers(account), data: { company_id: null, expected_company_id: source.id },
      });
      expect(moved.status()).toBe(200);
      await page.bringToFront();
      await page.clock.fastForward(61_000);
      await expect(movingLink).toHaveCount(0);
      await expect(page.getByRole("link", { name: remaining.name, exact: true })).toBeVisible();
      await detail.bringToFront();
      await detail.clock.fastForward(61_000);
      await expect(detail.getByRole("alert")).toContainText("Страница не найдена");
      await expect(title).toHaveCount(0);
      await expect(detail.getByRole("heading", { name: "О компьютере" })).toHaveCount(0);
      await expect(detail.getByRole("link", { name: /К списку компьютеров/ })).toBeFocused();
    } finally {
      await detail.close();
    }
  });
}

for (const surface of ["pool metrics", "pool metadata", "connected computers"]) {
  test(`${surface} keeps a focused retry button during automatic refresh while the error persists`, async ({ page, request, account }) => {
    const pool = await createCompany(request, account, "Пул с ошибкой");
    const company = `${API}/companies/${pool.id}`;
    const failedRead = surface === "pool metrics" ? `${company}/resources/summary?only_available=true`
      : surface === "pool metadata" ? company
      : `${API}/connected-computers?limit=10&offset=0`;
    await startSession(page, account);
    await page.clock.install();
    let failures = 0;
    await page.route(failedRead, (route) => {
      failures++;
      return route.fulfill({ status: 503, json: { detail: "Temporary outage" } });
    });
    await page.goto(surface === "connected computers" ? "compute/computers" : `compute/pools/${pool.id}`);
    const alert = page.getByRole("alert");
    const retry = alert.getByRole("button", { name: /Попробовать снова/ });
    await retry.focus();
    const completed = page.waitForResponse((response) => response.url() === failedRead && response.status() === 503);
    await page.clock.fastForward(61_000);
    await completed;
    await expect.poll(() => failures).toBeGreaterThan(1);
    await expect(retry).toBeFocused();
    await page.unroute(failedRead);
    await page.clock.fastForward(61_000);
    await expect(alert).toHaveCount(0);
    const fallback = surface === "pool metrics" ? page.getByRole("link", { name: "Список компьютеров" })
      : surface === "pool metadata" ? page.getByRole("link", { name: /К списку пулов/ })
      : page.getByRole("heading", { name: "Подключённые компьютеры.", level: 1 });
    await expect(fallback).toBeFocused();
  });
}

test("a brief pool metadata outage preserves the chart selection and focus", async ({ page, request, account }) => {
  const pool = await createCompany(request, account, "Пул для графика");
  const computer = await addComputer(request, account, "Стенд", pool.id, false);
  const sample = await request.post("http://127.0.0.1:18100/__test__/history", {
    data: { computer_id: computer.id, pattern: "full" },
  });
  expect(sample.ok()).toBe(true);
  await startSession(page, account);
  await page.clock.install({ time: new Date("2026-01-08T00:00:00Z") });
  await page.goto(`compute/pools/${pool.id}`);
  const series = page.getByRole("button", { name: "CPU · мощность", exact: true });
  await expect(series).toHaveAttribute("aria-pressed", "true");
  await series.focus();
  await series.press("Enter");
  await expect(series).toHaveAttribute("aria-pressed", "false");

  const details = `${API}/companies/${pool.id}`;
  await page.route(details, (route) => route.fulfill({ status: 503, json: { detail: "Temporary outage" } }));
  await page.clock.fastForward(61_000);
  await expect(page.getByRole("alert")).toBeVisible();
  await expect(series).toHaveAttribute("aria-pressed", "false");
  await expect(series).toBeFocused();
  await page.unroute(details);
  await page.clock.fastForward(61_000);
  await expect(page.getByRole("alert")).toHaveCount(0);
  await expect(series).toHaveAttribute("aria-pressed", "false");
  await expect(series).toBeFocused();
});

test("a legacy unscoped computer list keeps the personal inventory and page URL", async ({ page, request, account }) => {
  const computer = await addComputer(request, account, "Компьютер личного пула");
  await startSession(page, account);
  await page.goto("power-cells/computers?page=1#inventory");
  await expect(page).toHaveURL(new RegExp(`/compute/computers\\?page=1&user_id=${account.id}#inventory$`));
  await expect(page.getByRole("link", { name: computer.name })).toBeVisible();
});

for (const prefix of ["compute", "power-cells"] as const) {
  test(`${prefix} inventory opens its organization when an old address uses uppercase UUIDs`, async ({ page, request, account }) => {
    const company = await createCompany(request, account, "Организация по сохранённому адресу");
    const computer = await addComputer(request, account, "Mac из сохранённого списка", company.id);
    await startSession(page, account);
    await page.goto(`${prefix}/computers?company_id=${company.id.toUpperCase()}&only_available=false`);
    await expect(page.getByRole("link", { name: computer.name, exact: true })).toBeVisible();
    await page.getByRole("link", { name: "← К организации", exact: true }).click();
    await expect(page.getByRole("heading", { name: `${company.name}.`, exact: true })).toBeVisible();
    await page.goto(`organizations/${company.id.toUpperCase()}`);
    await expect(page.getByRole("heading", { name: `${company.name}.`, exact: true })).toBeVisible();
  });
}

test("computers awaiting confirmation explain why the pool cannot be changed", async ({ page, request, account }) => {
  const computer = await addComputer(request, account, "Ожидающий Mac", undefined, false);
  await markAccountComputer(request, computer.id);
  await startSession(page, account);
  await page.clock.install();
  await page.route("**/v1/connected-computers?*", async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    body.items[0].transfer_pending = true;
    body.items[0].can_move = false;
    await route.fulfill({ response, json: body });
  });
  await page.goto("compute/computers");
  const row = page.getByRole("listitem").filter({ hasText: computer.name });
  await expect(row.getByText("Подтвердите подключение в приложении на компьютере, чтобы продолжить.")).toBeVisible();
  await expect(row.getByRole("button", { name: /Сменить пул/ })).toBeDisabled();
  await page.unroute("**/v1/connected-computers?*");
  await page.clock.fastForward(61_000);
  await expect(row.getByRole("button", { name: /Сменить пул/ })).toBeEnabled();
});

test("keyboard focus in the computer and pool lists clears the sticky header", async ({ page, request, account, browserName }) => {
  const company = await createCompany(request, account, "Лаборатория");
  const computer = await addComputer(request, account, "Мой Mac");
  await markAccountComputer(request, computer.id);
  await startSession(page, account);
  await page.setViewportSize({ width: 390, height: 568 });
  const header = page.getByRole("banner");

  const reveals = async (earlier: Locator, later: Locator) => {
    await expect(header).toHaveCSS("position", "sticky");
    await later.evaluate((button) => button.focus({ preventScroll: true }));
    const gap = () => earlier.evaluate((node) =>
      node.getBoundingClientRect().top - document.querySelector(".site-header")!.getBoundingClientRect().bottom,
    );
    await earlier.evaluate((node) => {
      // Give the fixture room to put the target underneath the header.
      document.body.style.paddingBottom = `${window.innerHeight}px`;
      window.scrollBy(0, node.getBoundingClientRect().top - 20);
    });
    await expect.poll(gap).toBeLessThan(0);
    await page.keyboard.press(browserName === "webkit" ? "Alt+Shift+Tab" : "Shift+Tab");
    await expect(earlier).toBeFocused();
    await expect.poll(gap).toBeGreaterThanOrEqual(0);
  };

  await page.goto("compute/pools");
  await reveals(page.getByRole("link", { name: "← К Compute" }), page.getByRole("link", { name: "Открыть пул Личный пул" }));
  await reveals(
    page.getByRole("link", { name: "Открыть пул Личный пул" }),
    page.getByRole("link", { name: `Открыть пул ${company.name}` }),
  );
  await page.goto("compute/computers");
  await reveals(page.getByRole("link", { name: "← К Compute" }), page.getByRole("link", { name: "Личный пул" }));
  await reveals(
    page.getByRole("link", { name: "Личный пул" }),
    page.getByRole("button", { name: "Сменить пул — Мой Mac" }),
  );
});
