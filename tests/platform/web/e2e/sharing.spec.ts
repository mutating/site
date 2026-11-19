import { openUserMenu, selectLanguage } from "./header-controls";
import { openOrganizationMember, openOrganizationSettings, openOrganizationTokens, switchOrganizationPeople } from "./organization-controls";
import {
  API,
  addComputer,
  expect,
  headers,
  login,
  makeAccount,
  password,
  pauseMutation,
  startSession,
  test,
  type Account,
} from "./fixtures";
import type { APIRequestContext, Locator, Page } from "@playwright/test";
import type { CreatedInvitation, Invitation, Membership } from "../../../../platform/web/src/lib/types";

async function createCompany(page: Page, name = "Общая лаборатория") {
  await page.goto("organizations");
  await page
    .getByRole("button", { name: "Создать организацию", exact: true })
    .click();
  const nameField = page.getByLabel("Название организации");
  // Settle focus in mobile WebKit before entering the organization name.
  await nameField.click();
  await nameField.fill(name);
  await expect(nameField).toHaveValue(name);
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Создать организацию", exact: true })
    .click();
  const row = page
    .locator(".organization-row")
    .filter({ has: page.getByRole("heading", { name }) });
  await row
    .getByRole("link", { name: `Открыть организацию: ${name}`, exact: true })
    .click();
  await expect(
    page.getByRole("heading", { level: 1, name: `${name}.` }),
  ).toBeVisible();
  return new URL(page.url()).pathname.split("/").at(-1)!;
}
async function openSharing(page: Page, mode: "add" | "invite" | "link") {
  await switchOrganizationPeople(page, "Приглашения");
  const form = page.getByRole("region", { name: "Добавить участника" });
  if (
    !(await form
      .locator("details")
      .evaluate((node: HTMLDetailsElement) => node.open))
  )
    await form.locator("summary").click();
  await form.getByLabel("Способ добавления").selectOption(mode);
  return form;
}
async function signOut(page: Page) {
  await page.goto("profile");
  await (await openUserMenu(page)).getByRole("menuitem", { name: "Выйти", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "С возвращением." }),
  ).toBeVisible();
}
const roster = (page: Page) =>
  page.getByRole("region", { name: "Участники", exact: true });
const person = (page: Page, name: string) =>
  roster(page).getByRole("article", { name, exact: true });
const centerIsAccessible = (locator: Locator) => locator.evaluate((element) => {
  const { left, top, width, height } = element.getBoundingClientRect();
  return element.contains(document.elementFromPoint(left + width / 2, top + height / 2));
});

async function createCompanyWithTwoCreators(request: APIRequestContext, account: Account) {
  const peer = await makeAccount(request, "Второй создатель");
  const created = await request.post(`${API}/companies`, {
    headers: headers(account), data: { name: "Смена прав приглашения" },
  });
  expect(created.status()).toBe(201);
  const { id: company } = await created.json();
  expect((await request.post(`${API}/memberships`, {
    headers: headers(account), data: { company_id: company, user_id: peer.id, role: "creator" },
  })).status()).toBe(201);
  return { company, peer };
}

async function openOwnOrganizationRole(page: Page, name: string) {
  await switchOrganizationPeople(page, "Участники");
  const self = person(page, name);
  await openOrganizationMember(self);
  await self.getByRole("button", { name: "Изменить права" }).click();
  return { self, editor: page.getByRole("dialog", { name: "Изменить права" }) };
}

test("only the organization creator sees deletion and privileged invitation roles", async ({ page, request, account }) => {
  const administrator = await makeAccount(request, "Администратор демо");
  await startSession(page, account);
  const company = await createCompany(page);
  expect((await request.post(`${API}/memberships`, {
    headers: headers(account), data: { company_id: company, user_id: administrator.id, role: "admin" },
  })).status()).toBe(201);

  for (const [participant, creator] of [[account, true], [administrator, false]] as const) {
    await startSession(page, participant);
    await page.goto(`organizations/${company}`);
    const settings = await openOrganizationSettings(page);
    const remove = settings.getByRole("button", { name: "Удалить организацию", exact: true });
    await expect(remove).toHaveCount(creator ? 1 : 0);
    if (creator) await expect(remove).toBeEnabled();
    await settings.getByRole("button", { name: "Закрыть диалог", exact: true }).click();
    for (const method of ["invite", "link"] as const) {
      const form = await openSharing(page, method);
      const options = form.getByLabel("Роль", { exact: true }).locator("option");
      await expect(options).toHaveText(creator ? ["Участник", "Администратор", "Создатель"] : ["Участник"]);
    }
  }
});

for (const draft of ["privileged role", "role defaults", "custom permissions", "restricted administrator grant"] as const) {
  test(`an invitation with ${draft} uses current grants after the creator changes their access`, async ({ page, request, account }) => {
    const { company } = await createCompanyWithTwoCreators(request, account);
    await startSession(page, account);
    await page.goto(`organizations/${company}`);
    const addressed = draft === "custom permissions";
    const restrictedAdministrator = draft === "restricted administrator grant";
    const demoteToAdmin = draft === "privileged role" || restrictedAdministrator;
    const form = await openSharing(page, addressed ? "invite" : "link");
    const role = form.getByLabel("Роль", { exact: true });
    const custom = form.getByRole("checkbox", { name: "Настроить права отдельно" });
    const days = form.getByLabel("Срок приглашения");
    await days.selectOption("30");
    if (demoteToAdmin) await role.selectOption("admin");
    if (addressed) {
      await form.getByLabel("Email или ник").fill("new-member@example.com");
      await custom.check();
      await form.getByRole("checkbox", { name: "Управление участниками и приглашениями" }).check();
    }
    if (restrictedAdministrator) {
      await custom.check();
      const allowed = form.getByRole("group", { name: "Права", exact: true });
      for (const checkbox of await allowed.getByRole("checkbox").all()) {
        if ((await checkbox.evaluate((input: HTMLInputElement) => input.labels?.[0].textContent?.trim())) !== "Просмотр участников") {
          await checkbox.uncheck();
        }
      }
    }

    const { self, editor } = await openOwnOrganizationRole(page, account.first_name);
    if (demoteToAdmin) {
      await editor.getByLabel("Роль", { exact: true }).selectOption("admin");
    } else {
      await editor.getByLabel("Роль", { exact: true }).selectOption("user");
      await editor.getByRole("checkbox", { name: "Настроить права отдельно" }).check();
      for (const name of ["Просмотр ресурсов и истории мощностей", "Просмотр задач, результатов и артефактов",
        "Выполнение задач и работа с артефактами", "Просмотр API-токенов"]) {
        await editor.getByRole("checkbox", { name, exact: true }).uncheck();
      }
      await editor.getByRole("checkbox", { name: "Управление участниками и приглашениями" }).check();
    }
    await editor.getByRole("button", { name: "Сохранить изменения", exact: true }).click();
    await expect(editor).toHaveCount(0);
    await expect(self).toContainText(demoteToAdmin ? "Администратор" : "Участник");

    await switchOrganizationPeople(page, "Приглашения");
    await expect(role.locator("option")).toHaveText(["Участник"]);
    await expect(role).toHaveValue("user");
    await expect(days).toHaveValue("30");
    const posting = page.waitForResponse((response) =>
      response.url() === `${API}/invitations` && response.request().method() === "POST");
    await form.getByRole("button", { name: addressed ? "Пригласить" : "Создать ссылку", exact: true }).click();
    const response = await posting;
    expect(response.request().postDataJSON()).toMatchObject({
      role: "user",
      permissions: draft === "privileged role" ? null : addressed ? ["members.read", "members.manage"] : ["members.read"],
      expires_in_seconds: 30 * 86400,
      ...(addressed ? { identifier: "new-member@example.com" } : {}),
    });
    expect(response.status()).toBe(201);
    if (draft !== "privileged role") {
      await expect(custom).toBeChecked();
      if (demoteToAdmin) await expect(custom).toBeEnabled();
      else await expect(custom).toBeDisabled();
      const unavailable = form.getByRole("checkbox", { name: "Просмотр ресурсов и истории мощностей" });
      await expect(unavailable).not.toBeChecked();
      if (!demoteToAdmin) await expect(unavailable).toBeDisabled();
      if (addressed) await expect(form.getByRole("checkbox", { name: "Управление участниками и приглашениями" })).toBeChecked();
    }
  });
}

for (const delivery of ["created", "delayed", "renewed"] as const) {
  test(`a ${delivery} invitation link disappears when the creator loses grant authority`, async ({ page, request, account }) => {
    const { company, peer } = await createCompanyWithTwoCreators(request, account);
    await startSession(page, account);
    await page.goto(`organizations/${company}`);
    const form = await openSharing(page, "link");
    if (delivery !== "created") await form.getByLabel("Роль", { exact: true }).selectOption("admin");
    const createButton = form.getByRole("button", { name: "Создать ссылку", exact: true });
    const link = page.getByLabel("Ссылка приглашения", { exact: true });
    let release = () => {};
    let created: Promise<CreatedInvitation>;
    if (delivery === "delayed") {
      const gate = new Promise<void>((resolve) => { release = resolve; });
      let committed!: (invitation: CreatedInvitation) => void;
      created = new Promise((resolve) => { committed = resolve; });
      await page.route(`${API}/invitations`, async (route) => {
        if (route.request().method() !== "POST") return route.continue();
        const response = await route.fetch();
        expect(response.status()).toBe(201);
        committed(await response.json());
        await gate;
        await route.fulfill({ response });
      });
    } else {
      created = page.waitForResponse((response) =>
        response.url() === `${API}/invitations` && response.request().method() === "POST")
        .then((response) => response.json());
    }
    try {
      await createButton.click();
      let invitation = await created;
      if (delivery === "renewed") {
        const row = page.locator(".invitation-row").filter({ hasText: "Администратор" });
        await row.locator(".organization-invitation-summary").click();
        const response = page.waitForResponse(`${API}/invitations/${invitation.id}/link`);
        await row.getByRole("button", { name: "Обновить ссылку" }).click();
        invitation = await (await response).json();
      }
      if (delivery !== "delayed") await expect(link).toHaveValue(new RegExp(`/invitations/${invitation.token}$`));
      const { editor } = await openOwnOrganizationRole(page, account.first_name);
      await editor.getByLabel("Роль", { exact: true }).selectOption("admin");
      await editor.getByRole("button", { name: "Сохранить изменения", exact: true }).click();
      await expect(editor).toHaveCount(0);
      expect((await request.get(`${API}/invitations/link/${invitation.token}`, { headers: headers(peer) })).status()).toBe(410);
      await switchOrganizationPeople(page, "Приглашения");
      await expect(form.getByLabel("Роль", { exact: true }).locator("option")).toHaveText(["Участник"]);
      release();
      await expect(createButton).toBeEnabled();
      await expect(link).toHaveCount(0);
    } finally {
      release();
    }
  });
}

for (const change of ["demotion", "removal"] as const) {
  test(`a denied invitation refreshes externally changed roles after ${change}`, async ({ page, request, account }) => {
    const { company, peer } = await createCompanyWithTwoCreators(request, account);
    const response = await request.get(`${API}/memberships`, { headers: headers(peer), params: { company_id: company } });
    expect(response.status()).toBe(200);
    const members: Membership[] = await response.json();
    const self = members.find((member) => member.user_id === account.id)!;
    await startSession(page, account);
    await page.goto(`organizations/${company}`);
    const form = await openSharing(page, "link");
    const role = form.getByLabel("Роль", { exact: true });
    const days = form.getByLabel("Срок приглашения");
    await role.selectOption("admin");
    await days.selectOption("30");
    const membership = `${API}/memberships/${self.id}`;
    if (change === "demotion") {
      expect((await request.patch(membership, { headers: headers(peer), data: { role: "admin" } })).status()).toBe(200);
    } else {
      expect((await request.delete(membership, { headers: headers(peer) })).status()).toBe(204);
    }

    const post = () => page.waitForResponse((entry) => entry.url() === `${API}/invitations` && entry.request().method() === "POST");
    const createLink = form.getByRole("button", { name: "Создать ссылку", exact: true });
    const denied = post();
    await createLink.click();
    expect((await denied).status()).toBe(change === "demotion" ? 403 : 404);
    if (change === "removal") {
      await expect(page).toHaveURL(/\/organizations\/?$/);
      await expect(form).toHaveCount(0);
      return;
    }
    await expect(role.locator("option")).toHaveText(["Участник"]);
    await expect(days).toHaveValue("30");
    const retry = post();
    await createLink.click();
    const accepted = await retry;
    expect(accepted.status()).toBe(201);
    expect(accepted.request().postDataJSON()).toMatchObject({ role: "user", expires_in_seconds: 30 * 86400 });
  });
}

test("an unknown invitee preserves the draft when the recheck confirms organization access", async ({ page, request, account }) => {
  const { company } = await createCompanyWithTwoCreators(request, account);
  await addComputer(request, account, "Рабочий компьютер", company, false);
  await startSession(page, account);
  await page.goto(`organizations/${company}`);
  const computers = page.getByRole("region", { name: "Компьютеры", exact: true });
  const overview = page.locator(".organization-overview");
  const computer = computers.getByRole("link", { name: /Рабочий компьютер/ });
  const accessDenied = computers.getByText("У вас нет права просматривать мощности этой организации.", { exact: true });
  await expect(computer).toBeVisible();
  const charts = ["История CPU", "История памяти"].map((name) => page.getByRole("region", { name, exact: true }));
  for (const chart of charts) {
    await chart.locator("summary").click();
    await expect(chart.getByRole("table")).toBeVisible();
  }
  const companyUrl = `${API}/companies/${company}`;
  for (const method of ["add", "invite"] as const) {
    const form = await openSharing(page, method);
    const identifier = form.getByLabel("Email или ник");
    await identifier.fill("unknown_draft_invitee");
    const role = form.getByLabel("Роль", { exact: true });
    await role.selectOption("admin");
    let release!: () => void;
    const gate = new Promise<void>((resolve) => { release = resolve; });
    await page.route(companyUrl, async (route) => {
      await gate;
      if (method === "invite") await route.fulfill({ status: 503, json: { detail: "Recheck unavailable" } });
      else await route.continue();
    });
    try {
      const recheck = page.waitForResponse(companyUrl);
      const denied = page.waitForResponse((response) => method === "add"
        ? response.url().startsWith(`${companyUrl}/users/lookup?`)
        : response.url() === `${API}/invitations` && response.request().method() === "POST");
      await form.getByRole("button", { name: method === "add" ? "Найти пользователя" : "Пригласить", exact: true }).click();
      expect((await denied).status()).toBe(404);
      await expect(computers).toHaveAttribute("aria-busy", "true");
      await expect(overview).toHaveAttribute("aria-busy", "true");
      await expect(accessDenied).toHaveCount(0);
      await expect(computer).toHaveCount(0);
      for (const chart of charts) await expect(chart).toBeHidden();
      release();
      expect((await recheck).status()).toBe(method === "invite" ? 503 : 200);
      if (method === "invite") {
        const lookup = page.getByRole("group", { name: "Загрузка организации" });
        await expect(lookup.getByRole("alert")).toBeVisible();
        await expect(computers).toHaveAttribute("aria-busy", "false");
        await expect(accessDenied).toHaveCount(0);
        await expect(computer).toHaveCount(0);
        await page.unroute(companyUrl);
        const retry = page.waitForResponse(companyUrl);
        await lookup.getByRole("button", { name: "Попробовать снова" }).click();
        expect((await retry).status()).toBe(200);
      }
      await expect(computer).toBeVisible();
      await expect(form.getByRole("alert")).toBeVisible();
      await expect(identifier).toHaveValue("unknown_draft_invitee");
      await expect(role).toHaveValue("admin");
      for (const chart of charts) await expect(chart.getByRole("table")).toBeVisible();
    } finally {
      release();
      await page.unroute(companyUrl);
    }
  }
});

test("cancelling organization deletion restores focus while members refresh", async ({
  page,
  request,
  account,
}) => {
  const bob = await makeAccount(request, "Боб");
  await startSession(page, account);
  const companyId = await createCompany(page);
  const form = await openSharing(page, "add");
  await form.getByLabel("Email или ник").fill(bob.nickname);
  await form.getByRole("button", { name: "Найти пользователя" }).click();
  await expect(form.getByRole("status")).toContainText("Боб");

  let startAdding!: () => void;
  let startRefresh!: () => void;
  let releaseAdding!: () => void;
  let releaseRefresh!: () => void;
  const addingStarted = new Promise<void>((resolve) => { startAdding = resolve; });
  const refreshStarted = new Promise<void>((resolve) => { startRefresh = resolve; });
  const addingGate = new Promise<void>((resolve) => { releaseAdding = resolve; });
  const refreshGate = new Promise<void>((resolve) => { releaseRefresh = resolve; });
  await page.route(`${API}/memberships`, async (route) => {
    startAdding();
    await addingGate;
    await route.continue();
  });
  const remove = page.getByRole("button", { name: "Удалить организацию", exact: true });
  try {
    await form.getByRole("button", { name: "Добавить участника", exact: true }).click();
    await addingStarted;
    await openOrganizationSettings(page);
    await expect(remove).toBeEnabled();
    await remove.click();
    const dialog = page.getByRole("dialog", { name: "Удалить организацию" });
    await expect(dialog).toBeVisible();
    await page.route(`${API}/memberships?company_id=${companyId}&*`, async (route) => {
      startRefresh();
      await refreshGate;
      await route.continue();
    });
    releaseAdding();
    await refreshStarted;
    await dialog.getByRole("button", { name: "Отмена" }).click();
    await expect(remove).toBeFocused();
    await expect(remove).toBeEnabled();
    await remove.press("Enter");
    await expect(dialog).toBeVisible();
    await dialog.getByRole("button", { name: "Отмена" }).click();
    releaseRefresh();
    await expect(remove).toBeEnabled();
    await expect(remove).toBeFocused();
  } finally {
    releaseAdding();
    releaseRefresh();
  }
});

test("organization deletion stays reachable and invitations load on separate page deadlines", async ({
  page,
  request,
  account,
}) => {
  const created = await request.post(`${API}/companies`, {
    headers: headers(account),
    data: { name: "Команда с приглашениями" },
  });
  expect(created.status()).toBe(201);
  const { id: companyId } = await created.json();
  const invited = await request.post(`${API}/invitations`, {
    headers: headers(account),
    data: { company_id: companyId, role: "user" },
  });
  expect(invited.status()).toBe(201);
  const invitation: Invitation = await invited.json();
  const offsets: number[] = [];
  let releaseFirst!: () => void;
  let releaseLast!: () => void;
  const first = new Promise<void>((resolve) => { releaseFirst = resolve; });
  const last = new Promise<void>((resolve) => { releaseLast = resolve; });
  await page.route(`${API}/invitations?company_id=${companyId}&*`, async (route) => {
    const query = new URL(route.request().url()).searchParams;
    if (query.get("limit") === "0") return route.continue();
    const offset = Number(query.get("offset"));
    offsets.push(offset);
    const response = await route.fetch();
    const length = offset === 0 ? 200 : 1;
    await (offset === 0 ? first : last);
    await route.fulfill({
      response,
      json: Array.from({ length }, (_, index) => ({
        ...invitation,
        id: `invitation-${offset + index}`,
        email: `invited-${offset + index}@example.com`,
      })),
    });
  });
  await page.clock.install();
  await startSession(page, account);
  try {
    await page.goto(`organizations/${companyId}`);
    await expect(page.getByRole("tab", { name: "Приглашения", exact: true })).toHaveText("Приглашения 1");
    expect(offsets).toEqual([]);
    await switchOrganizationPeople(page, "Приглашения");
    await expect.poll(() => offsets).toEqual([0]);
    await openOrganizationSettings(page);
    const remove = page.getByRole("button", { name: "Удалить организацию" });
    await expect(remove).toBeEnabled({ timeout: 5_000 });
    await remove.click();
    const dialog = page.getByRole("dialog", { name: "Удалить организацию" });
    await expect(dialog).toBeVisible();
    await dialog.getByRole("button", { name: "Отмена" }).click();
    await page.getByRole("dialog", { name: "Настройки организации" }).getByRole("button", { name: "Закрыть диалог" }).click();
    await page.clock.fastForward(11_000);
    releaseFirst();
    await expect.poll(() => offsets).toEqual([0, 200]);
    await page.clock.fastForward(11_000);
    releaseLast();
    const outgoing = page.getByRole("region", { name: "Приглашения", exact: true });
    await expect(outgoing.getByRole("heading", { name: "invited-200@example.com" })).toBeVisible();
    await expect(outgoing.getByRole("alert")).toHaveCount(0);
    const trigger = page.getByRole("button", { name: "Настройки организации", exact: true });
    expect((await trigger.boundingBox())!.y).toBeLessThan((await outgoing.boundingBox())!.y);
  } finally {
    releaseFirst();
    releaseLast();
  }
});

test("member avatar API downloads retry automatically and release the image on navigation", async ({ page, request, account }) => {
  await page.addInitScript(() => {
    const revokedAvatarURLs: string[] = [];
    Object.assign(window, { revokedAvatarURLs });
    const revoke = URL.revokeObjectURL.bind(URL);
    URL.revokeObjectURL = url => { revokedAvatarURLs.push(url); revoke(url); };
  });
  const bob = await makeAccount(request, "Боб");
  const png = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jWZkAAAAASUVORK5CYII=", "base64");
  expect((await request.put(`${API}/users/me/avatar`, {
    headers: { ...headers(bob), "Content-Type": "image/png" }, data: png,
  })).status()).toBe(200);
  const company = await (await request.post(`${API}/companies`, {
    headers: headers(account), data: { name: "Аватары команды" },
  })).json();
  const member = await (await request.post(`${API}/memberships`, {
    headers: headers(account), data: { company_id: company.id, user_id: bob.id },
  })).json();
  let fail = true;
  const reads: import("@playwright/test").Request[] = [];
  await page.route(`${API}/memberships/${member.id}/avatar?*`, route => {
    reads.push(route.request());
    return fail ? route.abort("failed") : route.continue();
  });
  await login(page, account);
  await page.goto(`organizations/${company.id}`);
  await expect.poll(() => reads.length).toBeGreaterThan(0);
  await expect(person(page, "Боб").locator(".avatar")).toHaveText("Б");
  fail = false;
  const image = person(page, "Боб").locator("img");
  await expect(image).toBeVisible();
  await expect.poll(() => image.evaluate((node: HTMLImageElement) => node.naturalWidth)).toBeGreaterThan(0);
  const src = await image.getAttribute("src");
  expect(src).toMatch(/^blob:/);
  expect(reads.length).toBeGreaterThan(1);
  for (const read of reads) {
    expect(read.resourceType()).toBe("fetch");
    expect(await read.headerValue("X-Requested-With")).toBe("ThrongDashboard");
  }
  await page.getByRole("navigation", { name: "Навигация кабинета" }).getByRole("link", { name: "Обзор", exact: true }).click();
  await expect(image).toHaveCount(0);
  await expect.poll(() => page.evaluate(url =>
    (Reflect.get(window, "revokedAvatarURLs") as string[]).includes(url), src!),
  ).toBe(true);
});

test("find by nickname, add a member, show profiles, edit permissions, and remove safely", async ({
  page,
  request,
  account,
}) => {
  const bob = await makeAccount(request, "Боб");
  await login(page, account);
  const companyId = await createCompany(page);
  const form = await openSharing(page, "add");
  await form.getByLabel("Email или ник").fill("does_not_exist");
  await form.getByRole("button", { name: "Найти пользователя" }).click();
  await expect(form.getByRole("alert")).toContainText("Пользователь не найден");
  await form.getByLabel("Email или ник").fill(bob.nickname.toUpperCase());
  await form.getByRole("button", { name: "Найти пользователя" }).click();
  await expect(form.getByRole("status")).toContainText("Боб");
  await form
    .getByRole("button", { name: "Добавить участника", exact: true })
    .click();
  await switchOrganizationPeople(page, "Участники");
  const bobRow = person(page, "Боб");
  const memberSummary = bobRow.locator(".organization-member-summary");
  await expect(memberSummary).toContainText("Участник");
  await expect(memberSummary).toContainText(`@${bob.nickname}`);
  await expect(memberSummary).toHaveAttribute("aria-expanded", "false");
  await expect(bobRow.getByRole("button", { name: "Удалить участника" })).toHaveCount(0);
  await memberSummary.press("Enter");
  await expect(memberSummary).toHaveAttribute("aria-expanded", "true");
  await expect(bobRow.getByText("Выполнение задач", { exact: false })).toBeVisible();
  const creatorSummary = await openOrganizationMember(person(page, "Алиса"));
  await expect(memberSummary).toHaveAttribute("aria-expanded", "true");
  await creatorSummary.press("Enter");
  await bobRow
    .getByRole("button", { name: "Изменить права" })
    .click();
  let dialog = page.getByRole("dialog");
  await dialog.getByLabel("Настроить права отдельно").check();
  await dialog.getByLabel("Выполнение задач и работа с артефактами").uncheck();
  await dialog.getByRole("button", { name: "Сохранить изменения" }).click();
  await expect(dialog).not.toBeVisible();
  await expect(person(page, "Боб")).not.toContainText("Выполнение задач");
  const members = await (
    await request.get(`${API}/memberships?company_id=${companyId}`, {
      headers: headers(account),
    })
  ).json();
  const membership = members.find(
    (member: { user_id: string }) => member.user_id === bob.id,
  );
  expect(membership.effective_permissions).not.toContain("execution.run");
  await person(page, "Боб")
    .getByRole("button", { name: "Удалить участника" })
    .click();
  dialog = page.getByRole("dialog");
  await expect(
    dialog.getByRole("button", { name: "Подтвердить", exact: true }),
  ).toBeDisabled();
  await dialog.getByRole("textbox").fill("Боб");
  await dialog
    .getByRole("button", { name: "Подтвердить", exact: true })
    .click();
  await expect(person(page, "Боб")).toHaveCount(0);
  expect(
    (
      await request.get(`${API}/companies/${companyId}`, {
        headers: headers(bob),
      })
    ).status(),
  ).toBe(404);
});

test("addressed invitation appears in recipient inbox and can be accepted with a creator role", async ({
  page,
  request,
  account,
}) => {
  const bob = await makeAccount(request, "Боб");
  await login(page, account);
  const companyId = await createCompany(page);
  const form = await openSharing(page, "invite");
  await form.getByLabel("Email или ник").fill(bob.email);
  await form.getByLabel("Роль", { exact: true }).selectOption("creator");
  await form.getByRole("button", { name: "Пригласить", exact: true }).click();
  await expect(form.getByRole("status")).toHaveText("Приглашение создано.");
  const link = await form.getByLabel("Ссылка приглашения").inputValue();
  await page.goto(link);
  await expect(page.getByRole("alert")).toContainText("другой учётной записи");
  await signOut(page);
  await login(page, bob);
  await page.goto("organizations");
  const inbox = page.getByRole("region", {
    name: "Все организации",
    exact: true,
  });
  await expect(
    inbox.getByRole("heading", { name: "Общая лаборатория" }),
  ).toBeVisible();
  await inbox.getByRole("button", { name: "Принять приглашение" }).click();
  await expect(inbox.getByRole("status")).toHaveText(
    "Вы присоединились к организации.",
  );
  await page.goto(`organizations/${companyId}`);
  await openOrganizationMember(person(page, "Алиса"));
  await person(page, "Алиса")
    .getByRole("button", { name: "Изменить права" })
    .click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Роль", { exact: true }).selectOption("admin");
  await dialog.getByRole("button", { name: "Сохранить изменения" }).click();
  await expect(person(page, "Алиса")).toContainText("Администратор");
  await openOrganizationMember(person(page, "Боб"));
  await person(page, "Боб")
    .getByRole("button", { name: "Изменить права" })
    .click();
  await dialog.getByLabel("Роль", { exact: true }).selectOption("user");
  await dialog.getByRole("button", { name: "Сохранить изменения" }).click();
  await expect(dialog.getByRole("alert")).toContainText(
    "хотя бы одного создателя",
  );
  await dialog.getByRole("button", { name: "Отмена", exact: true }).click();
  expect(
    (
      await (
        await request.get(`${API}/companies/${companyId}`, {
          headers: headers(account),
        })
      ).json()
    ).my_role,
  ).toBe("admin");
});

test("share link survives login-to-registration navigation and cannot be reused", async ({
  page,
  account,
}) => {
  await login(page, account);
  await createCompany(page);
  const form = await openSharing(page, "link");
  await form
    .getByRole("button", { name: "Создать ссылку", exact: true })
    .click();
  const link = await form.getByLabel("Ссылка приглашения").inputValue();
  await form.getByRole("button", { name: "Скопировать ссылку" }).click();
  await expect(form.getByRole("status").last()).toContainText(
    /скопирована|выделена/,
  );
  await signOut(page);
  await page.goto(link);
  await expect(
    page.getByRole("heading", { name: "С возвращением." }),
  ).toBeVisible();
  await page.getByRole("link", { name: "Создать аккаунт" }).click();
  await page.getByLabel(/^Имя/).fill("Новая участница");
  await page
    .getByLabel("Электронная почта", { exact: true })
    .fill(
      `new_${Date.now()}_${Math.random().toString(36).slice(2)}@example.com`,
    );
  await page.getByLabel("Пароль", { exact: true }).fill(password);
  await page
    .getByRole("button", { name: "Создать аккаунт", exact: true })
    .click();
  await expect(page).toHaveURL(link);
  await page.getByRole("button", { name: "Принять приглашение" }).click();
  await expect(page.getByRole("status")).toHaveText(
    "Вы присоединились к организации.",
  );
  await page.getByRole("link", { name: "Участники", exact: true }).click();
  await expect(person(page, "Новая участница")).toContainText("Участник");
  await expect(
    page.getByRole("region", { name: "Добавить участника" }),
  ).toHaveCount(0);
  await page.goto(link);
  await expect(page.getByRole("alert")).toContainText("использовано, отозвано");
});

test("invitation link renewal disables the old link, revocation and decline are visible", async ({
  page,
  request,
  account,
  browserName,
}) => {
  const bob = await makeAccount(request, "Боб");
  await login(page, account);
  const companyId = await createCompany(page);
  const form = await openSharing(page, "invite");
  await form.getByLabel("Email или ник").fill(bob.email);
  await form.getByRole("button", { name: "Пригласить", exact: true }).click();
  const oldLink = await form.getByLabel("Ссылка приглашения").inputValue();
  const outgoing = page.getByRole("region", {
    name: "Приглашения",
    exact: true,
  });
  const invitation = outgoing.getByRole("article", { name: bob.email, exact: true });
  const summary = invitation.locator(".organization-invitation-summary");
  await page.setViewportSize({ width: 320, height: 667 });
  const method = form.locator(".tmc-select__value").first();
  await expect(method).toHaveText("Пригласить по email или нику");
  expect(await method.evaluate((value) => value.scrollWidth <= value.clientWidth + 1)).toBe(true);
  const email = form.getByLabel("Email или ник", { exact: true });
  const methodSelect = form.getByLabel("Способ добавления", { exact: true });
  const methodFace = form.locator(".tmc-select__face").first();
  const previousField = browserName === "webkit" ? "Alt+Shift+Tab" : "Shift+Tab";
  await form.getByLabel("Роль", { exact: true }).focus();
  await email.evaluate((input) => {
    window.scrollTo({ top: scrollY + input.getBoundingClientRect().top - 20, behavior: "instant" });
  });
  await page.keyboard.press(previousField);
  await expect(email).toBeFocused();
  await expect.poll(() => centerIsAccessible(email)).toBe(true);
  await methodFace.evaluate((face) => {
    window.scrollTo({ top: scrollY + face.getBoundingClientRect().top - 20, behavior: "instant" });
  });
  await page.keyboard.press(previousField);
  await expect(methodSelect).toBeFocused();
  await expect.poll(() => centerIsAccessible(methodFace)).toBe(true);
  await expect.poll(async () => {
    const [email, status] = await Promise.all([
      summary.locator("strong").boundingBox(),
      summary.locator(".organization-invitation-status").boundingBox(),
    ]);
    return !!email && !!status && status.y >= email.y + email.height;
  }).toBe(true);
  await expect(summary).toHaveAttribute("aria-expanded", "false");
  await expect(invitation.getByRole("button", { name: "Обновить ссылку" })).toHaveCount(0);
  await summary.press("Enter");
  await expect(summary).toHaveAttribute("aria-expanded", "true");
  await invitation.evaluate((row) => {
    window.scrollTo({ top: scrollY + row.getBoundingClientRect().top - 18, behavior: "instant" });
  });
  await invitation.getByRole("button", { name: "Обновить ссылку" }).press("Enter");
  const renewed = outgoing.locator(":scope > .share-link").getByLabel("Ссылка приглашения");
  await expect(renewed).toBeFocused();
  await expect.poll(() => centerIsAccessible(renewed)).toBe(true);
  const newLink = await renewed.inputValue();
  await expect(page.getByLabel("Ссылка приглашения")).toHaveCount(1);
  expect(newLink).not.toBe(oldLink);
  expect(
    (
      await request.get(
        `${API}/invitations/link/${oldLink.split("/").at(-1)}`,
        { headers: headers(bob) },
      )
    ).status(),
  ).toBe(404);
  await outgoing.getByRole("button", { name: "Отозвать", exact: true }).click();
  await expect(outgoing).toContainText("Отозвано");
  expect(
    (
      await request.get(
        `${API}/invitations/link/${newLink.split("/").at(-1)}`,
        { headers: headers(bob) },
      )
    ).status(),
  ).toBe(410);
  const again = await openSharing(page, "invite");
  await again.getByLabel("Email или ник").fill(bob.email);
  await again.getByRole("button", { name: "Пригласить", exact: true }).click();
  await signOut(page);
  await login(page, bob);
  await page.goto("organizations");
  const inbox = page.getByRole("region", {
    name: "Все организации",
    exact: true,
  });
  await inbox.getByRole("button", { name: "Отклонить", exact: true }).click();
  await expect(inbox.getByRole("status")).toHaveText("Приглашение отклонено.");
  expect(
    (
      await request.get(`${API}/companies/${companyId}`, {
        headers: headers(bob),
      })
    ).status(),
  ).toBe(404);
});

test("restricted manager sees current rights, cannot grant more, and sees capacity access limitation", async ({
  page,
  request,
  account,
}) => {
  const bob = await makeAccount(request, "Боб");
  const company = await (
    await request.post(`${API}/companies`, {
      headers: headers(account),
      data: { name: "Команда с ограничениями" },
    })
  ).json();
  expect(
    (
      await request.post(`${API}/memberships`, {
        headers: headers(account),
        data: {
          company_id: company.id,
          user_id: bob.id,
          permissions: ["members.read", "members.manage"],
        },
      })
    ).status(),
  ).toBe(201);
  await login(page, bob);
  await expect(page.getByRole("region", { name: company.name })).toContainText(
    "нет права просматривать мощности",
  );
  await page.goto(`organizations/${company.id}`);
  const form = await openSharing(page, "link");
  await expect(
    form.getByLabel("Роль", { exact: true }).locator("option"),
  ).toHaveCount(1);
  await expect(form.getByLabel("Настроить права отдельно")).toBeChecked();
  await expect(form.getByLabel("Управление API-токенами")).toBeDisabled();
  await switchOrganizationPeople(page, "Участники");
  await expect(person(page, "Алиса")).toBeVisible();
  await openOrganizationMember(person(page, "Алиса"));
  await expect(
    person(page, "Алиса").getByRole("button", { name: "Изменить права" }),
  ).toHaveCount(0);
  await switchOrganizationPeople(page, "Приглашения");
  await form.getByRole("button", { name: "Создать ссылку" }).click();
  await expect(form.getByRole("status")).toHaveText("Приглашение создано.");
  await selectLanguage(page);
  await expect(
    page.getByRole("tab", { name: "Members", exact: true }),
  ).toBeVisible();
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("lang", "en");
});

test("renewed invitation link stays accessible when a long list refreshes", async ({
  page,
  request,
  account,
}) => {
  const company = await request.post(`${API}/companies`, {
    headers: headers(account), data: { name: "Много приглашений" },
  });
  expect(company.status()).toBe(201);
  const { id } = await company.json();
  for (let index = 0; index < 17; index++) {
    const invitation = await request.post(`${API}/invitations`, {
      headers: headers(account),
      data: { company_id: id, role: "user", identifier: `guest${index}@example.com` },
    });
    expect(invitation.status()).toBe(201);
  }
  await page.setViewportSize({ width: 1440, height: 900 });
  await startSession(page, account);
  await page.goto(`organizations/${id}`);
  await expect(page.locator(".organization-overview")).toHaveAttribute("aria-busy", "false");
  await switchOrganizationPeople(page, "Приглашения");
  const outgoing = page.getByRole("region", { name: "Приглашения", exact: true });
  const rows = outgoing.locator(".invitation-row");
  await expect(rows).toHaveCount(17);
  const last = rows.last();
  await last.locator(".organization-invitation-summary").press("Enter");
  await last.evaluate((row) => {
    window.scrollTo({ top: scrollY + row.getBoundingClientRect().top - 18, behavior: "instant" });
  });
  let startRefresh!: () => void;
  let releaseRefresh!: () => void;
  const refreshStarted = new Promise<void>((resolve) => { startRefresh = resolve; });
  const refreshGate = new Promise<void>((resolve) => { releaseRefresh = resolve; });
  const listPath = `**/v1/invitations?company_id=${id}&*`;
  await page.route(listPath, async (route) => {
    startRefresh();
    await refreshGate;
    await route.continue();
  });
  try {
    await last.getByRole("button", { name: "Обновить ссылку" }).press("Enter");
    await refreshStarted;
    const renewed = outgoing.getByLabel("Ссылка приглашения");
    await expect(renewed).toBeFocused();
    await expect.poll(() => centerIsAccessible(renewed)).toBe(true);
    const refreshed = page.waitForResponse((response) =>
      response.url().startsWith(`${API}/invitations?company_id=${id}&`));
    releaseRefresh();
    expect((await refreshed).ok()).toBe(true);
    await expect(rows).toHaveCount(17);
    await expect(outgoing.locator(".organization-team-list")).not.toHaveAttribute("aria-busy", "true");
    await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    await expect(renewed).toBeFocused();
    expect(await centerIsAccessible(renewed)).toBe(true);
  } finally {
    releaseRefresh();
    await page.unroute(listPath);
  }
});

test("open invitations show the role and expiry before choosing which link to revoke", async ({
  page,
  request,
  account,
}) => {
  const company = await request.post(`${API}/companies`, {
    headers: headers(account), data: { name: "Открытые приглашения" },
  });
  expect(company.status()).toBe(201);
  const { id } = await company.json();
  const userInvite = await request.post(`${API}/invitations`, {
    headers: headers(account),
    data: { company_id: id, role: "user", expires_in_seconds: 86_400 },
  });
  const adminInvite = await request.post(`${API}/invitations`, {
    headers: headers(account),
    data: { company_id: id, role: "admin", expires_in_seconds: 30 * 86_400 },
  });
  expect(userInvite.status()).toBe(201);
  expect(adminInvite.status()).toBe(201);
  await page.setViewportSize({ width: 320, height: 568 });
  await startSession(page, account);
  await page.goto(`organizations/${id}`);
  await switchOrganizationPeople(page, "Приглашения");
  const userSummary = page.getByRole("button", { name: /Ссылка без адресата Участник · Действует до/ });
  const adminSummary = page.getByRole("button", { name: /Ссылка без адресата Администратор · Действует до/ });
  await expect(userSummary).toHaveAttribute("aria-expanded", "false");
  await expect(userSummary.locator("time")).toHaveAttribute("datetime", (await userInvite.json()).expires_at);
  await expect(adminSummary).toHaveAttribute("aria-expanded", "false");
  await expect(adminSummary.locator("time")).toHaveAttribute("datetime", (await adminInvite.json()).expires_at);
  await userSummary.press("Enter");
  const userRow = page.locator(".invitation-row").filter({ has: userSummary });
  await userRow.getByRole("button", { name: "Отозвать", exact: true }).click();
  await expect(userSummary).toContainText("Отозвано");
  await expect(adminSummary).toContainText("Ожидает ответа");
  await expect(adminSummary).toHaveAttribute("aria-expanded", "false");
});

test("tabbing to a long invitation keeps the whole row in view on mobile", async ({
  page,
  request,
  account,
  browserName,
}) => {
  const company = await request.post(`${API}/companies`, {
    headers: headers(account), data: { name: "Приглашения с длинным адресом" },
  });
  expect(company.status()).toBe(201);
  const { id } = await company.json();
  const address = `${"w".repeat(64)}@${"x".repeat(60)}.example.com`;
  for (const identifier of [address, "colleague@example.com"]) {
    const invite = await request.post(`${API}/invitations`, {
      headers: headers(account), data: { company_id: id, role: "user", identifier },
    });
    expect(invite.status()).toBe(201);
  }
  await page.setViewportSize({ width: 320, height: 568 });
  await startSession(page, account);
  await page.goto(`organizations/${id}`);
  await page.getByRole("tab", { name: "Приглашения", exact: true }).press("Enter");
  const first = page.getByRole("article", { name: "colleague@example.com", exact: true }).getByRole("button");
  const long = page.getByRole("article", { name: address, exact: true }).getByRole("button");
  await first.focus();
  await long.evaluate((button) => {
    window.scrollTo({ top: scrollY + button.getBoundingClientRect().top - innerHeight + 30, behavior: "instant" });
  });
  await page.keyboard.press(browserName === "webkit" ? "Alt+Tab" : "Tab");
  await expect(long).toBeFocused();
  await expect.poll(() => long.evaluate((button) => {
    const row = button.getBoundingClientRect();
    const header = document.querySelector(".site-header")!.getBoundingClientRect();
    return Math.max(header.bottom - row.top, row.bottom - innerHeight);
  })).toBeLessThanOrEqual(1);
});

test("team tab stays under the pointer as the resource overview loads", async ({ page, request, account }) => {
  const company = await request.post(`${API}/companies`, {
    headers: headers(account), data: { name: "Команда с ресурсами" },
  });
  expect(company.status()).toBe(201);
  const { id } = await company.json();
  await page.setViewportSize({ width: 1440, height: 900 });
  await startSession(page, account);
  let showResources!: () => void;
  const resourcesGate = new Promise<void>((resolve) => { showResources = resolve; });
  await page.route(`**/v1/companies/${id}/resources/pool-summary`, async (route) => {
    await resourcesGate;
    await route.continue();
  });
  try {
    await page.goto(`organizations/${id}`);
    const overview = page.locator(".organization-overview");
    await expect(overview).toHaveAttribute("aria-busy", "true");
    const tab = page.getByRole("tab", { name: "Приглашения", exact: true });
    await expect.poll(() => centerIsAccessible(tab)).toBe(true);
    const { x, y, width, height } = (await tab.boundingBox())!;
    await page.mouse.move(x + width / 2, y + height / 2);
    await page.mouse.down();
    showResources();
    await expect(overview).toHaveAttribute("aria-busy", "false");
    await expect(overview.locator(".organization-metric-detail")).toHaveCount(3);
    await page.mouse.up();
    await expect(tab).toHaveAttribute("aria-selected", "true");
  } finally {
    showResources();
  }
});

for (const { count, directory } of [{ count: 0, directory: false }, { count: 3, directory: false }, { count: 3, directory: true }]) {
  test(`the team ${directory ? "link" : "tab"} keeps the pointer while ${count} computers load`, async ({ page, request, account, browserName }) => {
    const company = await (await request.post(`${API}/companies`, {
      headers: headers(account), data: { name: "Рабочая команда" },
    })).json();
    for (let index = 0; index < count; index++) await addComputer(request, account, `Mac ${index + 1}`, company.id, false);
    if (directory) {
      for (let index = 0; index < 5; index++) {
        const person = await makeAccount(request, `Коллега ${index}`);
        expect((await request.post(`${API}/memberships`, {
          headers: headers(account), data: { company_id: company.id, user_id: person.id },
        })).status()).toBe(201);
      }
    }
    await startSession(page, account);
    await page.setViewportSize({ width: 390, height: 750 });
    let showComputers!: () => void;
    const loadComputers = new Promise<void>((resolve) => { showComputers = resolve; });
    await page.route(`**/v1/computers?company_id=${company.id}&only_available=false&limit=3`, async (route) => {
      await loadComputers;
      await route.continue();
    });
    try {
      await page.goto(`organizations/${company.id}`);
      const computers = page.locator(".organization-computers");
      await expect(computers).toHaveAttribute("aria-busy", "true");
      await expect(page.locator(".organization-overview")).toHaveAttribute("aria-busy", "false");
      const control = directory ? page.getByRole("link", { name: "Смотреть всех 6" })
        : page.getByRole("tab", { name: "Приглашения", exact: true });
      await expect(control).toBeVisible();
      await control.evaluate((element) => element.scrollIntoView({ block: "center", behavior: "instant" }));
      await expect.poll(() => centerIsAccessible(control)).toBe(true);
      const box = (await control.boundingBox())!;
      const point = { x: box.x + box.width / 2, y: box.y + box.height / 2 };
      const touch = browserName === "chromium" && count > 0 ? await page.context().newCDPSession(page) : null;
      if (touch) {
        await touch.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [point] });
      } else {
        await page.mouse.move(point.x, point.y);
        await page.mouse.down();
      }
      showComputers();
      await expect(computers).toHaveAttribute("aria-busy", "false");
      await expect(computers.locator(".organization-computer-card")).toHaveCount(count);
      if (touch) await touch.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
      else await page.mouse.up();
      if (directory) await expect(page).toHaveURL(/\/members$/);
      else await expect(control).toHaveAttribute("aria-selected", "true");
    } finally {
      showComputers();
    }
  });
}

test("full member list paginates, recovers keyboard focus on retry and corrects a removed last page", async ({
  page,
  request,
  account,
  browserName,
}) => {
  const company = await (
    await request.post(`${API}/companies`, {
      headers: headers(account),
      data: { name: "Большая команда" },
    })
  ).json();
  const ids: string[] = [];
  for (let index = 0; index < 20; index++) {
    const member = await makeAccount(request, `Участник ${index} ${"Ж".repeat(80)}`);
    const response = await request.post(`${API}/memberships`, {
      headers: headers(account),
      data: { company_id: company.id, user_id: member.id },
    });
    expect(response.status()).toBe(201);
    ids.push((await response.json()).id);
  }
  await startSession(page, account);
  await page.setViewportSize({ width: 320, height: 568 });
  await page.goto(`organizations/${company.id}`);
  const visibleMember = roster(page).locator(".member-row");
  await expect(visibleMember).toHaveCount(5);
  await roster(page).getByRole("link", { name: "Смотреть всех 21" }).click();
  await expect(visibleMember).toHaveCount(20);
  const summaries = visibleMember.locator(".organization-member-summary");
  const longMemberIndex = (await summaries.nth(18).innerText()).includes("ЖЖЖ") ? 18 : 17;
  const longMember = summaries.nth(longMemberIndex);
  expect((await longMember.boundingBox())!.height).toBeGreaterThan(120);
  await summaries.nth(longMemberIndex + 1).focus();
  await longMember.evaluate((button) => {
    window.scrollTo({ top: scrollY + button.getBoundingClientRect().top - 20, behavior: "instant" });
  });
  await page.keyboard.press(browserName === "webkit" ? "Alt+Shift+Tab" : "Shift+Tab");
  await expect(longMember).toBeFocused();
  await expect.poll(() => centerIsAccessible(longMember)).toBe(true);
  const lastPage = `**/v1/memberships?company_id=${company.id}&limit=20&offset=20`;
  const firstPage = `**/v1/memberships?company_id=${company.id}&limit=20&offset=0`;
  const search = roster(page).getByRole("searchbox");
  const results = roster(page).getByRole("group", { name: "Результаты поиска участников", exact: true });
  async function backFrom(origin: Locator) {
    let releaseFirstPage!: () => void;
    const firstPageGate = new Promise<void>((resolve) => { releaseFirstPage = resolve; });
    await page.route(firstPage, async (route) => {
      await firstPageGate;
      await route.continue();
    });
    try {
      await origin.focus();
      await page.goBack();
      await expect(search).toBeFocused();
      await expect(results).toHaveAttribute("tabindex", "0");
      await page.keyboard.press("Tab");
      await expect(results).toBeFocused();
      releaseFirstPage();
      await expect(visibleMember).toHaveCount(20);
    } finally {
      releaseFirstPage();
      await page.unroute(firstPage);
    }
    await page.goForward();
  }

  await page.route(lastPage, (route) => route.fulfill({ status: 503, json: {} }));
  const pageField = roster(page).getByRole("spinbutton");
  expect((await pageField.boundingBox())!.width).toBeGreaterThanOrEqual(60);
  await pageField.fill("2");
  await pageField.press("Enter");
  const retry = roster(page).getByRole("button", { name: "Попробовать снова" });
  await expect(retry).toBeVisible();
  await expect.poll(() => centerIsAccessible(retry)).toBe(true);
  await backFrom(retry);
  await expect(retry).toBeVisible();
  await page.unroute(lastPage);
  await retry.press("Enter");
  await expect(visibleMember).toHaveCount(1);
  await expect(search).toBeFocused();
  await expect(search).toHaveCSS("outline-style", "none");
  await expect(visibleMember).toBeInViewport();
  await expect.poll(async () => {
    const [member, header] = await Promise.all([
      visibleMember.boundingBox(),
      page.locator(".site-header").boundingBox(),
    ]);
    return !!member && !!header && member.y >= header.y + header.height;
  }).toBe(true);
  await expect(roster(page).getByText("21 из 21")).toBeVisible();
  await expect(roster(page).getByRole("spinbutton")).toHaveValue("2");
  for (const origin of [pageField, visibleMember.first().locator(".organization-member-summary")]) {
    await backFrom(origin);
    await expect(visibleMember).toHaveCount(1);
  }
  await openOrganizationMember(visibleMember.first());
  await visibleMember.getByRole("button", { name: "Изменить права" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.goBack();
  await expect(visibleMember).toHaveCount(20);
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await page.goForward();
  await expect(visibleMember).toHaveCount(1);
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await roster(page).getByRole("button", { name: "Назад" }).click();
  await expect(visibleMember).toHaveCount(20);
  let resume!: () => void;
  const responseGate = new Promise<void>((resolve) => { resume = resolve; });
  await page.route(lastPage, async (route) => {
    await responseGate;
    await route.fulfill({ status: 503, json: {} });
  });
  try {
    await roster(page).getByRole("button", { name: "Далее" }).press("Enter");
    await expect(search).toBeFocused();
    await expect(results).toHaveAttribute("tabindex", "0");
    await page.keyboard.press("Tab");
    await expect(results).toBeFocused();
    const menu = page.getByRole("button", { name: "Меню пользователя" });
    await menu.press("ArrowDown");
    const profile = page.getByRole("menuitem", { name: "Профиль", exact: true });
    await expect(profile).toBeFocused();
    resume();
    await expect(retry).toBeVisible();
    await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    await expect(profile).toBeFocused();
    await page.keyboard.press("Escape");
  } finally {
    resume();
    await page.unroute(lastPage);
  }
  await retry.evaluate((button) => button.scrollIntoView({ block: "center" }));
  await retry.click();
  await expect(visibleMember).toHaveCount(1);
  await page.goto(`organizations/${company.id}?page=3`);
  await expect(page).toHaveURL((url) => url.pathname.endsWith("/members") && url.searchParams.get("page") === "2");
  await expect(roster(page).getByRole("spinbutton")).toHaveValue("2");
  const last = await (
    await request.get(
      `${API}/memberships?company_id=${company.id}&limit=20&offset=20`,
      { headers: headers(account) },
    )
  ).json();
  // Removing any ordinary member reduces the total to twenty.
  const removable = last[0].role === "creator" ? ids[0] : last[0].id;
  expect(
    (
      await request.delete(`${API}/memberships/${removable}`, {
        headers: headers(account),
      })
    ).status(),
  ).toBe(204);
  await page.reload();
  await expect(page).toHaveURL((url) => url.pathname.endsWith("/members") && !url.searchParams.has("page"));
  await expect(roster(page).locator(".member-row")).toHaveCount(20);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});

test("a member added from Invitations refreshes an already open full list", async ({ page, request, account }) => {
  const invitee = await makeAccount(request, "Новый участник");
  await startSession(page, account);
  const companyId = await createCompany(page);
  for (let index = 0; index < 5; index++) {
    const person = await makeAccount(request, `Участник ${index}`);
    expect((await request.post(`${API}/memberships`, {
      headers: headers(account), data: { company_id: companyId, user_id: person.id },
    })).status()).toBe(201);
  }
  await page.reload();
  const form = await openSharing(page, "add");
  await form.getByLabel("Email или ник").fill(invitee.nickname);
  await form.getByRole("button", { name: "Найти пользователя" }).click();
  await expect(form.getByRole("status")).toContainText(invitee.first_name);
  const addition = await pauseMutation(page, `${API}/memberships`, { method: "POST" });
  try {
    await form.getByRole("button", { name: "Добавить участника", exact: true }).click();
    await addition.pending;
    await switchOrganizationPeople(page, "Участники");
    await roster(page).getByRole("link", { name: "Смотреть всех 6" }).click();
    await expect(roster(page).getByRole("article")).toHaveCount(6);
    addition.release();
    await expect(roster(page).getByRole("article")).toHaveCount(7);
    await expect(person(page, invitee.first_name)).toBeVisible();
  } finally {
    addition.release();
  }
});

test("overlapping additions and removals keep the last valid roster page", async ({ page, request, account }) => {
  const invitee = await makeAccount(request, "Добавленный участник");
  await startSession(page, account);
  const companyId = await createCompany(page, "Параллельные изменения");
  for (let index = 0; index < 20; index++) {
    const person = await makeAccount(request, `Участник ${index}`);
    expect((await request.post(`${API}/memberships`, {
      headers: headers(account), data: { company_id: companyId, user_id: person.id },
    })).status()).toBe(201);
  }
  await page.reload();
  const form = await openSharing(page, "add");
  await form.getByLabel("Email или ник").fill(invitee.nickname);
  await form.getByRole("button", { name: "Найти пользователя" }).click();
  await expect(form.getByRole("status")).toContainText(invitee.first_name);
  const addition = await pauseMutation(page, `${API}/memberships`, { method: "POST", pause: "both" });
  try {
    await form.getByRole("button", { name: "Добавить участника", exact: true }).click();
    await addition.sent;
    await switchOrganizationPeople(page, "Участники");
    await roster(page).getByRole("link", { name: "Смотреть всех 21" }).click();
    await expect(roster(page).getByText("1–20 из 21")).toBeVisible();
    addition.commit();
    await addition.pending;
    await roster(page).getByRole("button", { name: "Далее" }).click();
    await expect(roster(page).getByText("21–22 из 22")).toBeVisible();
    const endpoint = `${API}/memberships?company_id=${companyId}&limit=20&offset=20`;
    const members: Membership[] = await (await request.get(endpoint, { headers: headers(account) })).json();
    const member = members.find(({ role }) => role === "user")!;
    const target = person(page, member.user.first_name);
    await openOrganizationMember(target);
    const removal = await pauseMutation(page, `${API}/memberships/${member.id}`, { pause: "response" });
    try {
      await target.getByRole("button", { name: "Удалить участника" }).click();
      const dialog = page.getByRole("dialog");
      await dialog.getByRole("textbox").fill(member.user.first_name);
      await dialog.getByRole("button", { name: "Подтвердить" }).click();
      await removal.pending;
      addition.release();
      await expect(target).toHaveCount(0);
      await expect(roster(page).getByText("21 из 21")).toBeVisible();
      const latest = page.waitForResponse((response) => response.url() === endpoint && response.ok());
      removal.release();
      await latest;
      await expect(dialog).toHaveCount(0);
      await expect(page).toHaveURL(/\/members\?page=2$/);
      await expect(roster(page).getByText("21 из 21")).toBeVisible();
    } finally {
      removal.release();
    }
  } finally {
    addition.release();
  }
});

for (const registry of ["invitations", "tokens"] as const) {
  test(`a late member removal refreshes revoked ${registry} in the reopened organization`, async ({ page, request, account }) => {
    await startSession(page, account);
    const companyId = await createCompany(page, "Права делегата");
    const delegate = await makeAccount(request, "Делегат команды");
    const grant = await request.post(`${API}/memberships`, {
      headers: headers(account), data: { company_id: companyId, user_id: delegate.id, role: "admin" },
    });
    expect(grant.status()).toBe(201);
    const memberId = (await grant.json()).id;
    for (let index = 0; index < 4; index++) {
      const person = await makeAccount(request, `Участник ${index}`);
      expect((await request.post(`${API}/memberships`, {
        headers: headers(account), data: { company_id: companyId, user_id: person.id },
      })).status()).toBe(201);
    }
    const invitedEmail = `pending_${delegate.nickname}@example.com`;
    const grantData = registry === "tokens"
      ? { company_id: companyId, name: "Ключ делегата", scopes: ["read"] }
      : { company_id: companyId, identifier: invitedEmail, role: "user", expires_in_seconds: 86400 };
    expect((await request.post(`${API}/${registry === "tokens" ? "api-tokens" : "invitations"}`, {
      headers: headers(delegate), data: grantData,
    })).status()).toBe(201);
    await page.reload();
    await roster(page).getByRole("link", { name: "Смотреть всех 6" }).click();
    const target = person(page, delegate.first_name);
    await openOrganizationMember(target);
    await target.getByRole("button", { name: "Удалить участника" }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByRole("textbox").fill(delegate.first_name);
    const removal = await pauseMutation(page, `${API}/memberships/${memberId}`);
    try {
      await dialog.getByRole("button", { name: "Подтвердить" }).click();
      await removal.pending;
      await page.goBack();
      if (registry === "tokens") {
        const settings = await openOrganizationTokens(page);
        const token = settings.locator(".token-registry-row").filter({ hasText: "Ключ делегата" });
        await expect(token).toContainText("Активен");
        await token.getByRole("button", { name: "Отозвать токен", exact: true }).focus();
        expect((await request.delete(`${API}/memberships/${memberId}`, { headers: headers(account) })).status()).toBe(204);
        const alreadyRemoved = page.waitForResponse((response) => response.url() === `${API}/memberships/${memberId}` && response.request().method() === "DELETE");
        removal.release();
        expect((await alreadyRemoved).status()).toBe(404);
        await expect(token).toContainText("Отозван");
        await expect(token).toBeFocused();
      } else {
        await switchOrganizationPeople(page, "Приглашения");
        const invitation = page.getByRole("article", { name: invitedEmail, exact: true });
        await expect(invitation).toContainText("Ожидает ответа");
        const summary = invitation.locator(".organization-invitation-summary");
        await summary.press("Enter");
        await invitation.getByRole("button", { name: "Отозвать", exact: true }).focus();
        removal.release();
        await expect(invitation).toContainText("Отозвано");
        await expect(summary).toBeFocused();
      }
    } finally {
      removal.release();
    }
  });
}

test("returning from the full roster keeps a completed invitation revocation", async ({ page, request, account }) => {
  await startSession(page, account);
  const companyId = await createCompany(page, "Приглашения команды");
  for (let index = 0; index < 5; index++) {
    const person = await makeAccount(request, `Коллега ${index}`);
    expect((await request.post(`${API}/memberships`, {
      headers: headers(account), data: { company_id: companyId, user_id: person.id },
    })).status()).toBe(201);
  }
  const email = `invite_${account.nickname}@example.com`;
  const response = await request.post(`${API}/invitations`, {
    headers: headers(account), data: { company_id: companyId, identifier: email, role: "user", expires_in_seconds: 86400 },
  });
  expect(response.status()).toBe(201);
  const created = await response.json();
  await page.reload();
  await switchOrganizationPeople(page, "Приглашения");
  const invitation = page.getByRole("article", { name: email, exact: true });
  await invitation.getByRole("button", { name: email }).click();
  const revocation = await pauseMutation(page, `${API}/invitations/${created.id}`);
  try {
    await invitation.getByRole("button", { name: "Отозвать", exact: true }).click();
    await revocation.pending;
    await switchOrganizationPeople(page, "Участники");
    await roster(page).getByRole("link", { name: "Смотреть всех 6" }).click();
    await page.getByRole("link", { name: "← К организации", exact: true }).click();
    await switchOrganizationPeople(page, "Приглашения");
    await expect(invitation).toContainText("Ожидает ответа");
    revocation.release();
    await expect(invitation).toContainText("Отозвано");
  } finally {
    revocation.release();
  }
});

test("member loading and edits recover from transport errors without discarding the draft", async ({
  page,
  request,
  account,
}) => {
  const bob = await makeAccount(request, "Боб");
  const company = await (
    await request.post(`${API}/companies`, {
      headers: headers(account),
      data: { name: "Восстановление команды" },
    })
  ).json();
  const member = await (
    await request.post(`${API}/memberships`, {
      headers: headers(account),
      data: { company_id: company.id, user_id: bob.id },
    })
  ).json();
  await login(page, account);
  const listPath = "**/v1/memberships?*";
  let attempts = 0;
  await page.route(listPath, (route) => {
    ++attempts;
    return route.abort("failed");
  });
  await page.goto(`organizations/${company.id}`);
  await expect.poll(() => attempts).toBeGreaterThan(0);
  await expect(roster(page).getByRole("alert")).toHaveCount(0);
  await page.unroute(listPath);
  await expect(person(page, "Боб")).toBeVisible();
  await openOrganizationMember(person(page, "Боб"));
  const trigger = person(page, "Боб").getByRole("button", {
    name: "Изменить права",
  });
  await trigger.click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Роль", { exact: true }).selectOption("admin");
  const patch = `**/v1/memberships/${member.id}`;
  await page.route(patch, (route) => route.abort("failed"));
  await dialog.getByRole("button", { name: "Сохранить изменения" }).click();
  await expect(dialog.getByRole("alert")).toContainText("Не удалось связаться");
  await expect(dialog.getByLabel("Роль", { exact: true })).toHaveValue("admin");
  await page.unroute(patch);
  await dialog.getByRole("button", { name: "Сохранить изменения" }).click();
  await expect(dialog).not.toBeVisible();
  await expect(person(page, "Боб")).toContainText("Администратор");
  await trigger.click();
  await page.keyboard.press("Escape");
  await expect(trigger).toBeFocused();
});

test("invitation creation can retry and an email recipient can register later and join", async ({
  page,
  account,
}) => {
  const email = `future_${Date.now()}_${Math.random().toString(36).slice(2)}@example.com`;
  await login(page, account);
  await createCompany(page, "Будущая команда");
  const form = await openSharing(page, "invite");
  await form.getByLabel("Email или ник").fill(email);
  await form.getByLabel("Срок приглашения").selectOption("1");
  const path = "**/v1/invitations";
  await page.route(path, (route) => route.abort("failed"));
  await form.getByRole("button", { name: "Пригласить", exact: true }).click();
  await expect(form.getByRole("alert")).toContainText("Не удалось связаться");
  await expect(form.getByLabel("Email или ник")).toHaveValue(email);
  await expect(form.getByLabel("Срок приглашения")).toHaveValue("1");
  await page.unroute(path);
  await form.getByRole("button", { name: "Пригласить", exact: true }).click();
  await expect(form.getByRole("status")).toHaveText("Приглашение создано.");
  await signOut(page);
  await page.goto("register");
  await page.getByLabel(/^Имя/).fill("Будущий участник");
  await page.getByLabel("Электронная почта", { exact: true }).fill(email);
  await page.getByLabel("Пароль", { exact: true }).fill(password);
  await page
    .getByRole("button", { name: "Создать аккаунт", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Привет, Будущий участник." }),
  ).toBeVisible();
  await page.goto("organizations");
  const inbox = page.getByRole("region", {
    name: "Все организации",
    exact: true,
  });
  await expect(
    inbox.getByRole("article", { name: "Будущая команда" }),
  ).toBeVisible();
  await inbox.getByRole("button", { name: "Принять приглашение" }).click();
  await expect(inbox.getByRole("status")).toHaveText(
    "Вы присоединились к организации.",
  );
});
