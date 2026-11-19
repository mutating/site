import type { APIRequestContext } from "@playwright/test";
import { addComputer, API, expect, headers, makeAccount, pauseMutation, startSession, test, type Account } from "./fixtures";
import { openOrganizationMember, openOrganizationSettings, openOrganizationTokens, switchOrganizationPeople } from "./organization-controls";

async function createCompany(request: APIRequestContext, account: Account, name = "Северная лаборатория") {
  const response = await request.post(`${API}/companies`, { headers: headers(account), data: { name } });
  expect(response.status()).toBe(201);
  return (await response.json()).id as string;
}

const png = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jWZkAAAAASUVORK5CYII=", "base64");

test("organization overview has registry tabs, separate charts and direct computer links", async ({ page, request, account }) => {
  const company = await createCompany(request, account);
  const offline = await addComputer(request, account, "runner-03", company, false);
  await addComputer(request, account, "mac-studio", company, true);
  await startSession(page, account);
  await page.goto(`organizations/${company}`);
  const hero = page.locator(".organization-hero");
  await expect(hero.getByRole("heading", { name: "Северная лаборатория." })).toBeVisible();
  await expect(hero.locator(".organization-hero-avatar")).toHaveCount(0);
  await expect(hero.getByText("Создатель", { exact: true })).toHaveCount(0);
  await expect(hero.getByRole("button", { name: /Пригласить|API-токены/ })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Настройки организации", exact: true })).toHaveCount(1);

  const tabs = page.getByRole("tablist", { name: "Команда" });
  await expect(tabs).toHaveAttribute("aria-orientation", "horizontal");
  const members = tabs.getByRole("tab", { name: "Участники" });
  const invitations = tabs.getByRole("tab", { name: "Приглашения" });
  await page.evaluate(() => document.fonts.ready);
  expect(await invitations.evaluate((button) => {
    const range = document.createRange();
    range.selectNodeContents(button);
    return new Set(Array.from(range.getClientRects(), (box) => box.top)).size;
  })).toBe(1);
  await members.focus();
  await page.keyboard.press("ArrowRight");
  await expect(invitations).toBeFocused();
  await page.getByRole("region", { name: "Добавить участника" }).locator("summary").click();
  await expect(page.getByLabel("Способ добавления")).toBeVisible();
  await page.keyboard.press("Tab");
  await members.click();
  await expect(page.getByRole("region", { name: "Участники" })).toContainText("Создатель");

  const cpu = page.getByRole("region", { name: "История CPU" });
  const memory = page.getByRole("region", { name: "История памяти" });
  await expect(cpu.getByRole("img")).toBeVisible();
  await expect(memory.getByRole("img")).toBeVisible();
  const series = cpu.locator("svg[role=img] path");
  expect(await series.evaluateAll((paths) => paths.map((path) => path.getAttribute("class")))).toEqual(["free", "total"]);
  await page.route(`**/companies/${company}/resources/pool-history`, async (route) => {
    const response = await route.fetch();
    await route.fulfill({ response, json: { ...await response.json(), sampled: true } });
  });
  const online = page.locator(".organization-computer-card").filter({ hasText: "mac-studio" });
  await expect(online.locator(".organization-computer-status")).toHaveText("Онлайн");
  await page.route(`**/computers?company_id=${company}&only_available=false&limit=3`, async (route) => {
    const response = await route.fetch();
    const computers = await response.json();
    await route.fulfill({ response, json: computers.map((computer: { name: string }) =>
      computer.name === "mac-studio" ? { ...computer, session_active: false } : computer) });
  });
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect(online.locator(".organization-computer-status")).toHaveText("Офлайн");
  await expect(cpu.getByText("Выборка по времени")).toBeVisible();
  await expect(series.filter({ visible: true }).first()).toHaveAttribute("d", /^M[\d.,]+h\.01/);
  expect(await series.evaluateAll((paths) => paths.every((path) => !/[HV]/.test(path.getAttribute("d") || "")))).toBe(true);
  await cpu.locator("summary").click();
  await expect(cpu.getByRole("table")).toContainText("всего");
  await expect(cpu.getByRole("table")).toContainText("свободно");
  const radii = await page.locator(".organization-widget, .organization-computer-card").evaluateAll((widgets) =>
    widgets.map((widget) => getComputedStyle(widget).borderTopLeftRadius));
  expect(new Set(radii).size).toBe(1);
  const card = page.locator(".organization-computer-card").filter({ hasText: "runner-03" });
  await expect(card.locator(".organization-computer-status")).toHaveText("Офлайн");
  const light = await card.locator(".offline i").evaluate((dot) => getComputedStyle(dot).backgroundColor.match(/\d+/g)!.map(Number));
  expect(light[0]).toBeGreaterThan(light[1] + 65);
  expect(light[0]).toBeGreaterThan(light[2] + 65);
  expect(await card.evaluate((element) => {
    const style = getComputedStyle(element);
    return style.borderTopColor === style.borderBottomColor;
  })).toBe(true);
  await card.click();
  await expect(page).toHaveURL(new RegExp(`/compute/computers/${offline.id}\\?company_id=${company}`));
  await expect(page.getByRole("heading", { name: "runner-03." })).toBeVisible();
  await expect(page.getByRole("dialog")).toHaveCount(0);
});

test("the team tab stays under the pointer while the organization avatar loads", async ({ page, request, account, browserName }) => {
  const company = await createCompany(request, account, "Северная Объединенная лаборатория распределенных вычислений");
  const image = await request.put(`${API}/companies/${company}/images/avatar`, {
    headers: { ...headers(account), "Content-Type": "image/png" }, data: png,
  });
  expect(image.status()).toBe(200);
  await startSession(page, account);
  await page.setViewportSize({ width: 320, height: 820 });
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  await page.route(`**/v1/companies/${company}/images/avatar?v=*`, async (route) => {
    await gate;
    await route.continue();
  });
  const touch = browserName === "chromium" ? await page.context().newCDPSession(page) : null;
  try {
    await page.goto(`organizations/${company}`);
    const hero = page.locator(".organization-hero");
    const avatar = hero.locator(".organization-hero-avatar");
    const invitations = page.getByRole("tab", { name: /^Приглашения/ });
    await expect(avatar).toBeAttached();
    await expect(page.locator(".organization-computers")).toHaveAttribute("aria-busy", "false");
    await expect(page.locator(".organization-overview")).toHaveAttribute("aria-busy", "false");
    await page.evaluate(() => document.fonts.ready);
    await invitations.evaluate((button) => button.scrollIntoView({ block: "center", behavior: "instant" }));
    const heightBefore = (await hero.boundingBox())!.height;
    const box = (await invitations.boundingBox())!;
    const point = { x: box.x + box.width / 2, y: box.y + box.height / 2 };
    if (touch) await touch.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [point] });
    else {
      await page.mouse.move(point.x, point.y);
      await page.mouse.down();
    }
    release();
    await expect(avatar.locator("img")).toBeVisible();
    if (touch) await touch.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
    else await page.mouse.up();
    expect((await hero.boundingBox())!.height).toBe(heightBefore);
    await expect(invitations).toHaveAttribute("aria-selected", "true");
  } finally {
    release();
    await touch?.detach();
  }
});

test("member search prompts use the platform text color in both themes", async ({ page, request, account }) => {
  const company = await createCompany(request, account);
  await startSession(page, account);
  for (const theme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: theme });
    for (const path of [`organizations/${company}`, `organizations/${company}/members`]) {
      await page.goto(path);
      await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
      const search = page.getByRole("searchbox", { name: "Найти участника" });
      const colors = await search.evaluate((input) => {
        const prompt = getComputedStyle(input, "::placeholder");
        return { prompt: prompt.color, opacity: prompt.opacity, label: getComputedStyle(input.parentElement!).color };
      });
      expect(colors.prompt, `${theme}: ${path}`).toBe(colors.label);
      expect(colors.opacity).toBe("1");
    }
  }
});

test("five member previews link to the filtered full roster at six and restore context", async ({ page, request, account, browserName }) => {
  const company = await createCompany(request, account);
  await addComputer(request, account, "atlas-01", company, false);
  const organizationPath = `/organizations/${company}`;
  const people = await Promise.all(["Борис", "Майя", "София", "Данил", "Лада"].map((name) => makeAccount(request, name)));
  const add = async (person: Account) => {
    const response = await request.post(`${API}/memberships`, {
      headers: headers(account), data: { company_id: company, user_id: person.id },
    });
    expect(response.status()).toBe(201);
    return response.json();
  };
  for (const person of people.slice(0, 4)) await add(person);
  await startSession(page, account);
  await page.goto("organizations?q=Северная");
  await page.getByRole("link", { name: "Открыть организацию: Северная лаборатория" }).click();
  const roster = page.getByRole("region", { name: "Участники", exact: true });
  const search = roster.getByRole("searchbox", { name: "Найти участника" });
  const link = roster.getByRole("link", { name: /^Смотреть/ });
  const rows = roster.getByRole("article");
  const memberMetric = page.locator(".organization-overview-metrics > div").filter({
    has: page.getByText("Участники", { exact: true }),
  }).locator("dd");
  const tabToLink = browserName === "webkit" ? "Alt+Tab" : "Tab";
  await expect(rows).toHaveCount(5);
  await expect(link).toHaveCount(0);

  await add(people[4]);
  const all = (await (await request.get(`${API}/memberships?company_id=${company}&limit=6`, { headers: headers(account) })).json());
  const last = all.at(-1)!;
  const term = `@${last.user.nickname.toUpperCase()}`;
  await search.fill(term);
  await expect(rows).toHaveCount(1);
  await expect(rows.first()).toContainText(`@${last.user.nickname}`);
  await expect(link).toHaveText("Смотреть результаты");
  await expect(page.getByRole("tab", { name: "Участники" })).toHaveText("Участники 6");
  await expect(memberMetric).toHaveText("6");
  await link.press("Enter");
  await expect(page).toHaveURL((url) => url.pathname === `${organizationPath}/members` && url.searchParams.get("q") === term);
  await expect(page.getByRole("heading", { name: "Участники", level: 1 })).toBeFocused();
  await expect(search).toHaveValue(term);
  await expect(rows).toHaveCount(1);
  await page.goBack();
  await expect(search).toHaveValue(term);
  await expect(link).toBeFocused();
  await expect(link).toBeInViewport();
  const nextTerm = people[4].first_name;
  await search.evaluate((element, value) => {
    // Fire a Forward navigation within the same browser task as the final keystroke.
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(element, value);
    element.dispatchEvent(new Event("input", { bubbles: true }));
    history.forward();
  }, nextTerm);
  await expect(page).toHaveURL((url) => url.pathname.endsWith("/members"));
  await expect(search).toHaveValue(term);
  await page.goBack();
  await expect(search).toHaveValue(nextTerm);
  await expect(link).toBeFocused();
  await search.fill(term);
  await search.press(tabToLink);
  await expect(link).toBeFocused();
  await expect(page).toHaveURL((url) => url.searchParams.get("q") === term);
  await link.click();

  await search.clear();
  await expect(rows).toHaveCount(6);
  const target = rows.filter({ hasText: `@${people[4].nickname}` });
  await openOrganizationMember(target);
  const edit = target.getByRole("button", { name: "Изменить права" });
  await edit.evaluate((button) => {
    if (!(button instanceof HTMLButtonElement)) throw new Error("Expected a member edit button");
    const field = document.querySelector<HTMLInputElement>(".organization-member-search input")!;
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(field, " ");
    field.dispatchEvent(new Event("input", { bubbles: true }));
    button.click();
  });
  const dialog = page.getByRole("dialog");
  await expect(page).toHaveURL((url) => url.searchParams.get("q") === " ");
  await expect(dialog).toBeVisible();
  await dialog.getByLabel("Роль", { exact: true }).selectOption("admin");
  await dialog.getByRole("button", { name: "Сохранить изменения" }).press("Enter");
  await expect(target).toContainText("Администратор");
  await expect(edit).toBeFocused();
  await page.getByRole("link", { name: "← К организации", exact: true }).press("Enter");
  await expect(search).toHaveValue(term);
  await expect(link).toBeFocused();
  await expect(link).toBeInViewport();
  await link.click();
  await search.fill(people[4].nickname);
  await expect(rows).toHaveCount(1);
  await expect(target).toBeVisible();
  await openOrganizationMember(target);
  await target.getByRole("button", { name: "Удалить участника" }).click();
  await dialog.getByRole("textbox").fill(people[4].first_name);
  await dialog.getByRole("button", { name: "Подтвердить" }).click();
  await expect(roster.getByText("Участники не найдены.")).toBeVisible();
  await expect(search).toBeFocused();
  await page.getByRole("link", { name: "← К организации", exact: true }).click();
  await expect(search).toHaveValue(term);
  await expect(search).toBeFocused();
  await expect(link).toHaveCount(0);
  await expect(page.getByRole("tab", { name: "Участники" })).toHaveText("Участники 5");

  const extra = await add(people[4]);
  await search.clear();
  await expect(link).toHaveText("Смотреть всех 6");
  let releaseFirstRoster!: () => void;
  const firstRosterGate = new Promise<void>((resolve) => { releaseFirstRoster = resolve; });
  const previewRequest = `**/v1/memberships?company_id=${company}&limit=5&offset=0`;
  await page.route(previewRequest, async (route) => {
    await firstRosterGate;
    await route.continue();
  });
  try {
    await page.reload();
    await expect(memberMetric).toHaveText("6");
    await expect(roster.getByRole("status")).toBeVisible();
    await expect(link).toHaveCount(0);
    releaseFirstRoster();
    await expect(rows).toHaveCount(5);
    await expect(link).toHaveText("Смотреть всех 6");
  } finally {
    releaseFirstRoster();
    await page.unroute(previewRequest);
  }
  expect((await request.delete(`${API}/memberships/${extra.id}`, { headers: headers(account) })).status()).toBe(204);
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  await page.route((url) => url.pathname === "/v1/memberships" && url.searchParams.get("q") === people[0].nickname, async (route) => {
    await gate;
    await route.continue();
  });
  try {
    await search.fill(people[0].nickname);
    await search.press(tabToLink);
    await expect(link).toBeFocused();
    release();
    await expect(link).toHaveCount(0);
    await expect(search).toBeFocused();
    await expect(memberMetric).toHaveText("5");
  } finally {
    release();
  }
  const toComputerList = page.waitForURL((url) => url.pathname === "/compute/computers");
  await search.evaluate((element, value) => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(element, value);
    element.dispatchEvent(new Event("input", { bubbles: true }));
    document.querySelector<HTMLAnchorElement>(".organization-computers .text-link")!.click();
  }, people[1].first_name);
  await toComputerList;
  await page.goBack();
  await expect(search).toHaveValue(people[1].first_name);
  await page.getByRole("link", { name: "← К организациям", exact: true }).click();
  await expect(page).toHaveURL((url) => url.pathname === "/organizations" && url.searchParams.get("q") === "Северная");
});

test("member search accepts fast keyboard input and complete Unicode names", async ({ page, request, account }) => {
  const company = await createCompany(request, account);
  await startSession(page, account);
  const path = `organizations/${company}`;
  await page.goto(path);
  await expect(page.getByRole("article", { name: account.first_name })).toBeVisible();
  const search = page.getByRole("searchbox", { name: "Найти участника" });
  const value = "x".repeat(120);
  const historyErrors: string[] = [];
  page.on("pageerror", (error) => historyErrors.push(error.message));
  await search.pressSequentially(value, { delay: 12 });
  await expect(search).toHaveValue(value);
  await expect(page).toHaveURL((url) => url.searchParams.get("q") === value);
  await expect(page.getByText("Участники не найдены.")).toBeVisible();
  expect(historyErrors).toEqual([]);
  await search.clear();
  await expect(page.getByRole("article", { name: account.first_name })).toBeVisible();
  const first = "🙂".repeat(99) + "Ж";
  const last = "😀" + "Я".repeat(99);
  expect((await request.patch(`${API}/users/${account.id}`, {
    headers: headers(account), data: { first_name: first, last_name: last },
  })).status()).toBe(200);
  const name = `${first} ${last}`;
  await page.goto(`${path}/members?${new URLSearchParams({ q: name })}`);
  await expect(search).toHaveValue(name);
  const row = page.getByRole("article", { name, exact: true });
  await expect(row).toBeVisible();
  await expect(row.locator(".organization-member-avatar")).toHaveText("🙂");
  await search.clear();
  await search.fill(name);
  await expect(search).toHaveValue(name);
  await expect(page).toHaveURL((url) => url.searchParams.get("q") === name);
});

test("the link keeps taps and clicks and cancels a drag while filtered members arrive", async ({ page, request, account, browserName }) => {
  const company = await createCompany(request, account);
  const team = await Promise.all(Array.from({ length: 5 }, (_, index) => makeAccount(request, `Коллега ${index}`)));
  for (const person of team) {
    expect((await request.post(`${API}/memberships`, {
      headers: headers(account), data: { company_id: company, user_id: person.id },
    })).status()).toBe(201);
  }
  await startSession(page, account);
  await page.goto(`organizations/${company}`);
  const roster = page.getByRole("region", { name: "Участники", exact: true });
  const search = roster.getByRole("searchbox");
  const rows = roster.getByRole("article");
  const link = roster.getByRole("link", { name: "Смотреть результаты", exact: true });
  const term = `@${team[0].nickname}`;
  const match = (url: URL) => url.pathname === "/v1/memberships" && url.searchParams.get("limit") === "5" && url.searchParams.get("q") === term;
  const gestures = browserName === "chromium" ? ["drag", "click", "tap"] : ["drag", "click"];
  for (const gesture of gestures) {
    await search.clear();
    await expect(rows).toHaveCount(5);
    let release!: () => void;
    const gate = new Promise<void>((resolve) => { release = resolve; });
    await page.route(match, async (route) => {
      await gate;
      await route.continue();
    });
    const touch = gesture === "tap" ? await page.context().newCDPSession(page) : null;
    try {
      await search.fill(term);
      await expect(page).toHaveURL((url) => url.searchParams.get("q") === term);
      await expect(roster.getByRole("status")).toBeVisible();
      await link.evaluate((element) => element.scrollIntoView({ block: "center", behavior: "instant" }));
      const box = (await link.boundingBox())!;
      const point = { x: box.x + box.width / 2, y: box.y + box.height / 2 };
      if (touch) {
        await touch.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [point] });
      } else {
        await page.mouse.move(point.x, point.y);
        await page.mouse.down();
      }
      release();
      await expect(rows).toHaveCount(1);
      if (gesture === "drag") await page.mouse.move(1, 1);
      if (touch) await touch.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
      else await page.mouse.up();
      if (gesture === "drag") {
        await expect(page).toHaveURL((url) => url.pathname === `/organizations/${company}`);
        await expect(link).toBeVisible();
      } else {
        await expect(page).toHaveURL((url) => url.pathname.endsWith("/members") && url.searchParams.get("q") === term);
        if (gesture === "click" && browserName === "chromium") await page.goBack();
      }
    } finally {
      release();
      await page.unroute(match);
      await touch?.detach();
    }
  }
});

test("the member directory reflects permissions revoked before navigation and while searching", async ({ page, request, account }) => {
  const company = await createCompany(request, account);
  const reader = await makeAccount(request, "Участник команды");
  const response = await request.post(`${API}/memberships`, {
    headers: headers(account), data: { company_id: company, user_id: reader.id, role: "admin" },
  });
  expect(response.status()).toBe(201);
  const membership = await response.json();
  for (const index of [1, 2, 3, 4]) {
    const person = await makeAccount(request, `Участник ${index}`);
    expect((await request.post(`${API}/memberships`, {
      headers: headers(account), data: { company_id: company, user_id: person.id },
    })).status()).toBe(201);
  }
  await startSession(page, reader);
  await page.goto(`organizations/${company}`);
  const roster = page.getByRole("region", { name: "Участники", exact: true });
  const fullList = roster.getByRole("link", { name: "Смотреть всех 6" });
  await expect(fullList).toBeVisible();
  expect((await request.patch(`${API}/memberships/${membership.id}`, {
    headers: headers(account), data: { role: "user", permissions: ["members.read"] },
  })).status()).toBe(200);
  await fullList.click();
  await expect(roster.getByRole("article")).toHaveCount(6);
  const ownRow = roster.getByRole("article", { name: reader.first_name, exact: true });
  await openOrganizationMember(ownRow);
  await expect(ownRow.getByRole("button", { name: "Изменить права" })).toHaveCount(0);
  expect((await request.patch(`${API}/memberships/${membership.id}`, {
    headers: headers(account), data: { permissions: ["members.read", "members.manage"] },
  })).status()).toBe(200);
  await page.reload();
  await openOrganizationMember(ownRow);
  const edit = ownRow.getByRole("button", { name: "Изменить права" });
  await edit.click();
  const editor = page.getByRole("dialog");
  expect((await request.patch(`${API}/memberships/${membership.id}`, {
    headers: headers(account), data: { permissions: ["members.read"] },
  })).status()).toBe(200);
  await editor.getByRole("button", { name: "Сохранить изменения" }).click();
  await expect(editor).toHaveCount(0);
  await expect(edit).toHaveCount(0);
  expect((await request.patch(`${API}/memberships/${membership.id}`, {
    headers: headers(account), data: { permissions: [] },
  })).status()).toBe(200);
  await roster.getByRole("searchbox").fill(account.first_name);
  await expect(roster.getByText("У вас нет права просматривать участников этой организации.")).toBeVisible();
  await expect(roster.getByRole("searchbox")).toHaveCount(0);
  await expect(roster.getByRole("article")).toHaveCount(0);
  await expect(roster).toBeFocused();
});

test("a delayed last-page correction preserves a member search already being typed", async ({ page, request, account }) => {
  const company = await createCompany(request, account);
  await startSession(page, account);
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  await page.route(`**/v1/memberships?company_id=${company}&limit=20&offset=19960`, async (route) => {
    await gate;
    await route.continue();
  });
  try {
    await page.goto(`organizations/${company}/members?page=999`);
    const search = page.getByRole("searchbox", { name: "Найти участника" });
    await search.fill(account.first_name);
    release();
    await expect(page).toHaveURL((url) => url.searchParams.get("q") === account.first_name && !url.searchParams.has("page"));
    await expect(search).toHaveValue(account.first_name);
    await expect(page.getByRole("article", { name: account.first_name })).toBeVisible();
  } finally {
    release();
  }
});

test("retrying the organization lookup keeps keyboard navigation in the full roster route", async ({ page, request, account }) => {
  const company = await createCompany(request, account);
  for (let index = 0; index < 5; index++) {
    const person = await makeAccount(request, `Сотрудник ${index}`);
    expect((await request.post(`${API}/memberships`, {
      headers: headers(account), data: { company_id: company, user_id: person.id },
    })).status()).toBe(201);
  }
  await startSession(page, account);
  await page.goto(`organizations/${company}`);
  const link = page.getByRole("link", { name: "Смотреть всех 6" });
  await expect(link).toBeVisible();
  let allowRetry = false;
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  await page.route(`${API}/companies/${company}`, async (route) => {
    if (!allowRetry) return route.fulfill({ status: 503, json: {} });
    await gate;
    await route.continue();
  });
  try {
    await link.press("Enter");
    const retry = page.getByRole("button", { name: "Попробовать снова" });
    await expect(retry).toBeVisible();
    const back = page.getByRole("link", { name: "← К организации", exact: true });
    allowRetry = true;
    await retry.press("Enter");
    await expect(back).toBeFocused();
    const loading = page.getByRole("group", { name: "Загрузка организации", exact: true });
    await expect(loading).toHaveAttribute("tabindex", "0");
    await page.keyboard.press("Tab");
    await expect(loading).toBeFocused();
    release();
    await expect(page.getByRole("heading", { name: "Участники", level: 1 })).toBeVisible();
    await expect.poll(() => page.evaluate(() => Boolean(document.activeElement?.closest("main")))).toBe(true);
  } finally {
    release();
  }
});

for (const role of ["admin", "user"] as const) {
  test(`losing creator rights closes a stale ${role} role editor`, async ({ page, request, account }) => {
    const company = await createCompany(request, account);
    const coCreator = await makeAccount(request, "Другой создатель");
    const person = await makeAccount(request, "Коллега");
    for (const [user, participantRole] of [[coCreator, "creator"], [person, role]] as const) {
      expect((await request.post(`${API}/memberships`, {
        headers: headers(account), data: { company_id: company, user_id: user.id, role: participantRole },
      })).status()).toBe(201);
    }
    const ownCompany = await (await request.get(`${API}/companies/${company}`, { headers: headers(account) })).json();
    await startSession(page, account);
    await page.goto(`organizations/${company}/members`);
    const target = page.getByRole("article", { name: person.first_name, exact: true });
    await openOrganizationMember(target);
    const edit = target.getByRole("button", { name: "Изменить права" });
    await edit.click();
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("Роль", { exact: true }).selectOption("admin");
    expect((await request.patch(`${API}/memberships/${ownCompany.my_membership_id}`, {
      headers: headers(coCreator), data: { role: "admin" },
    })).status()).toBe(200);
    await dialog.getByRole("button", { name: "Сохранить изменения" }).press("Enter");
    await expect(dialog).toHaveCount(0);
    await expect(page.getByRole("searchbox", { name: "Найти участника" })).toBeFocused();
    if (role === "admin") await expect(edit).toHaveCount(0);
    else {
      await edit.click();
      await expect(dialog.getByLabel("Роль", { exact: true })).toHaveValue("user");
    }
  });
}

test("member dialogs close if another administrator already removed the member", async ({ page, request, account }) => {
  const company = await createCompany(request, account);
  await startSession(page, account);
  const people = await Promise.all(["Николай", "Татьяна"].map(async (name) => {
    const person = await makeAccount(request, name);
    const response = await request.post(`${API}/memberships`, {
      headers: headers(account), data: { company_id: company, user_id: person.id },
    });
    expect(response.status()).toBe(201);
    return { name, id: (await response.json()).id };
  }));
  await page.goto(`organizations/${company}/members`);
  const editor = page.getByRole("dialog");
  for (const [index, member] of people.entries()) {
    const row = page.getByRole("article", { name: member.name, exact: true });
    await openOrganizationMember(row);
    await row.getByRole("button", { name: index === 0 ? "Изменить права" : "Удалить участника" }).click();
    expect((await request.delete(`${API}/memberships/${member.id}`, { headers: headers(account) })).status()).toBe(204);
    if (index === 1) await editor.getByRole("textbox").fill(member.name);
    await editor.getByRole("button", { name: index === 0 ? "Сохранить изменения" : "Подтвердить" }).click();
    await expect(editor).toHaveCount(0);
    await expect(row).toHaveCount(0);
    await expect(page.getByRole("searchbox", { name: "Найти участника" })).toBeFocused();
  }
});

test("organization settings save a name and both images and use symmetric risk actions", async ({ page, request, account }) => {
  const company = await createCompany(request, account);
  await startSession(page, account);
  await page.goto(`organizations/${company}`);
  const dialog = await openOrganizationSettings(page);
  await expect(dialog.getByRole("tab", { name: "Основное" })).toHaveAttribute("aria-selected", "true");
  const input = dialog.getByLabel("Название организации");
  await expect(input).toBeEditable();
  await input.fill("Северная Объединенная лаборатория распределенных вычислений");
  await expect(dialog.getByRole("button", { name: "Сохранить", exact: true })).toBeEnabled();
  await input.press("Enter");
  await expect(dialog.getByRole("status")).toContainText("Изменения сохранены");
  await expect(input).toBeFocused();
  const background = dialog.getByLabel("Загрузить фон", { exact: true });
  await background.focus();
  await background.setInputFiles({ name: "background.png", mimeType: "image/png", buffer: png });
  await expect(dialog.locator(".organization-image-preview.background img")).toBeVisible();
  await expect(background).toBeFocused();
  await dialog.getByLabel("Загрузить аватар", { exact: true }).setInputFiles({ name: "avatar.png", mimeType: "image/png", buffer: png });
  await expect(dialog.locator(".organization-image-preview.avatar img")).toBeVisible();
  const actions = dialog.locator(".organization-danger-actions button");
  await expect(actions).toHaveCount(2);
  expect(await actions.evaluateAll((buttons) => {
    const [a, b] = buttons.map((button) => button.getBoundingClientRect());
    return Math.abs(a.width - b.width) <= 1 && a.height === b.height;
  })).toBe(true);
  await dialog.getByRole("button", { name: "Закрыть диалог" }).click();
  await expect(page.getByRole("button", { name: "Настройки организации" })).toBeFocused();
  await expect(page.locator(".organization-hero-art.is-custom img")).toHaveAttribute("src", /^blob:/);
  await expect(page.locator(".organization-hero-avatar img")).toBeVisible();
  await page.reload();
  await expect(page.getByRole("heading", { name: "Северная Объединенная лаборатория распределенных вычислений." })).toBeVisible();
  await expect(page.locator(".organization-hero-art.is-custom img")).toHaveAttribute("src", /^blob:/);
  await expect(page.locator(".organization-hero-avatar img")).toBeVisible();
  const reopened = await openOrganizationSettings(page);
  const remove = reopened.getByRole("button", { name: "Удалить фон", exact: true });
  await remove.focus();
  await remove.press("Enter");
  await expect(remove).toHaveCount(0);
  await expect(reopened.getByLabel("Загрузить фон", { exact: true })).toBeFocused();
  await reopened.getByRole("button", { name: "Закрыть диалог" }).click();
  await page.getByRole("link", { name: /К организациям/ }).click();
  await expect(page.locator(".organization-row .organization-monogram img")).toBeVisible();
});

for (const field of ["name", "background"] as const) {
  test(`organization settings recheck manage permission after denied ${field} change`, async ({ page, request, account }) => {
    const owner = await makeAccount(request);
    const company = await createCompany(request, owner);
    const joined = await request.post(`${API}/memberships`, { headers: headers(owner), data: {
      company_id: company, user_id: account.id, role: "user", permissions: ["company.manage"],
    } });
    expect(joined.status()).toBe(201);
    const membership = await joined.json();
    await startSession(page, account);
    await page.goto(`organizations/${company}`);
    const settings = await openOrganizationSettings(page);
    const name = settings.getByLabel("Название организации", { exact: true });
    const background = settings.getByLabel("Загрузить фон", { exact: true });
    await expect(name).toBeEditable();
    const updated = await request.patch(`${API}/memberships/${membership.id}`, {
      headers: headers(owner), data: { permissions: [] },
    });
    expect(updated.status()).toBe(200);
    if (field === "name") {
      await name.fill("Недоступное название");
      await name.press("Enter");
    } else {
      await background.setInputFiles({ name: "background.png", mimeType: "image/png", buffer: png });
    }
    await expect(settings.getByRole("alert")).toBeVisible();
    await expect(name).toHaveCount(0);
    await expect(background).toHaveCount(0);
    await settings.getByRole("button", { name: "Закрыть диалог" }).click();
    await openOrganizationSettings(page);
    await expect(name).toHaveCount(0);
    await expect(background).toHaveCount(0);
  });
}

test("organization token creation retains its secret when the registry shrinks to one page", async ({ page, request, account }) => {
  const company = await createCompany(request, account);
  const ids: string[] = [];
  for (let index = 0; index < 11; index++) {
    const issued = await request.post(`${API}/api-tokens`, {
      headers: headers(account), data: { company_id: company, name: `Token ${index}`, scopes: ["read"] },
    });
    expect(issued.status()).toBe(201);
    ids.push((await issued.json()).id);
  }
  await startSession(page, account);
  await page.goto(`organizations/${company}`);
  const settings = await openOrganizationTokens(page);
  await expect(settings.getByLabel("Владелец токенов")).toHaveCount(0);
  await settings.getByLabel("Состояние токенов").selectOption("active");
  await settings.getByRole("button", { name: /Далее/ }).click();
  await expect(settings.getByRole("navigation")).toContainText("Страница 2 из 2");
  await settings.getByRole("button", { name: "Создать токен", exact: true }).click();
  const create = page.getByRole("dialog", { name: "Создать токен" });
  await expect(create.getByRole("radio", { name: /Личный/ })).toHaveCount(0);
  for (const id of ids.slice(0, 2)) {
    expect((await request.patch(`${API}/access-tokens/${id}`, {
      headers: headers(account), data: { revoke: true },
    })).status()).toBe(200);
  }
  await create.getByLabel("Название токена").fill("Новый ключ команды");
  await create.getByRole("button", { name: /Создать токен организации/ }).click();
  const issued = page.getByRole("dialog", { name: /Токен создан/ });
  const secret = issued.getByLabel("API-токен", { exact: true });
  await expect(secret).toBeVisible();
  await expect.poll(async () => !(await page.locator(".organization-settings-dialog .api-tokens-page .pagination").count())).toBe(true);
  await expect(secret).not.toHaveValue("");
  await page.keyboard.press("Escape");
  await expect(settings).toBeVisible();
  await expect(settings.getByRole("article", { name: "Новый ключ команды" })).toBeVisible();
});

for (const unavailable of [false, true]) {
  test(`a previous settings save preserves a new token through company revalidation (${unavailable ? "with retry" : "directly"})`, async ({ page, request, account }) => {
    const name = "Северная лаборатория";
    const company = await createCompany(request, account, name);
    await startSession(page, account);
    await page.goto("organizations");
    await page.getByRole("link", { name: new RegExp(name) }).click();
    const general = await openOrganizationSettings(page);
    const uploadPath = `${API}/companies/${company}/images/background`;
    const upload = await pauseMutation(page, uploadPath, { method: "PUT" });
    await general.getByLabel("Загрузить фон", { exact: true }).setInputFiles({ name: "background.png", mimeType: "image/png", buffer: png });
    await upload.pending;

    await page.goBack();
    await page.getByRole("link", { name: new RegExp(name) }).click();
    const settings = await openOrganizationTokens(page);
    await settings.getByRole("button", { name: "Создать токен", exact: true }).click();
    const create = page.getByRole("dialog", { name: "Создать токен" });
    await create.getByLabel("Название токена").fill("Ключ после загрузки");
    await create.getByRole("button", { name: /Создать токен организации/ }).click();
    const secret = page.getByRole("dialog", { name: /Токен создан/ }).getByLabel("API-токен", { exact: true });
    await expect(secret).toBeVisible();
    const issued = await secret.inputValue();
    await expect(page.locator('.token-registry-row[aria-label="Ключ после загрузки"]')).toHaveCount(1);

    let releaseLookup!: () => void;
    const lookupGate = new Promise<void>((resolve) => { releaseLookup = resolve; });
    const lookup = `${API}/companies/${company}`;
    await page.route(lookup, async (route) => {
      await lookupGate;
      if (unavailable) await route.fulfill({ status: 503, json: {} });
      else await route.continue();
    }, { times: 1 });
    const revalidation = page.waitForResponse(lookup);
    const uploaded = page.waitForResponse((response) => response.url() === uploadPath && response.request().method() === "PUT");
    upload.release();
    try {
      expect((await uploaded).status()).toBe(200);
      await expect(secret).toBeHidden();
      await expect(page.getByRole("dialog", { name: "API-токены" })).toBeVisible();
    } finally {
      releaseLookup();
    }
    expect((await revalidation).status()).toBe(unavailable ? 503 : 200);
    if (unavailable) {
      const pending = page.getByRole("dialog", { name: "API-токены" });
      await pending.getByRole("button", { name: /Попробовать снова/ }).click();
    }
    await expect(secret).toBeVisible();
    expect(await secret.inputValue() === issued).toBe(true);
  });
}

test("organization token registry refreshes permissions after denied creation", async ({ page, request, account }) => {
  const owner = await makeAccount(request);
  const company = await createCompany(request, owner);
  const granted = await request.post(`${API}/memberships`, { headers: headers(owner), data: {
    company_id: company, user_id: account.id, role: "user", permissions: ["tokens.read", "tokens.manage"],
  } });
  expect(granted.status()).toBe(201);
  const member = await granted.json();
  const created = await request.post(`${API}/api-tokens`, { headers: headers(owner), data: {
    company_id: company, name: "Ключ для чтения", scopes: ["read"],
  } });
  expect(created.status()).toBe(201);

  await startSession(page, account);
  await page.goto(`organizations/${company}`);
  const settings = await openOrganizationTokens(page);
  const start = settings.getByRole("button", { name: "Создать токен", exact: true });
  await start.click();
  const create = page.getByRole("dialog", { name: "Создать токен" });
  await create.getByLabel("Название токена").fill("Не выданный ключ");
  const updated = await request.patch(`${API}/memberships/${member.id}`, {
    headers: headers(owner), data: { permissions: ["tokens.read"] },
  });
  expect(updated.status()).toBe(200);
  await create.getByRole("button", { name: /Создать токен организации/ }).click();
  await expect(create.getByRole("alert")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(settings.locator(".token-permission-note")).toBeVisible();
  await expect(start).toBeDisabled();
  const token = settings.getByRole("article", { name: "Ключ для чтения" });
  await expect(token).toBeVisible();
  await expect(token.getByRole("button", { name: "Отозвать токен" })).toHaveCount(0);
});

test("closing an issued organization token keeps focus in the refreshing registry", async ({ page, request, account }) => {
  const company = await createCompany(request, account);
  await startSession(page, account);
  await page.goto(`organizations/${company}`);
  const settings = await openOrganizationTokens(page);
  const trigger = settings.getByRole("button", { name: "Создать токен", exact: true });
  await trigger.click();
  const create = page.getByRole("dialog", { name: "Создать токен" });
  await create.getByLabel("Название токена").fill("Ключ перед обновлением");

  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  await page.route(`${API}/api-tokens?*`, async (route) => {
    await gate;
    await route.continue();
  }, { times: 1 });
  try {
    await create.getByRole("button", { name: /Создать токен организации/ }).click();
    await expect(page.getByRole("dialog", { name: /Токен создан/ })).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(trigger).toBeDisabled();
    await expect(settings.locator(".token-registry-toolbar")).toBeFocused();
  } finally {
    release();
  }
  await expect(settings.getByRole("article", { name: "Ключ перед обновлением" })).toBeVisible();
});

test("revoking an organization token keeps focus in the refreshing registry", async ({ page, request, account }) => {
  const company = await createCompany(request, account);
  const name = "Ключ для отзыва";
  const issued = await request.post(`${API}/api-tokens`, {
    headers: headers(account), data: { company_id: company, name, scopes: ["read"] },
  });
  expect(issued.status()).toBe(201);
  await startSession(page, account);
  await page.goto(`organizations/${company}`);
  const settings = await openOrganizationTokens(page);
  const token = settings.getByRole("article", { name });
  const trigger = token.getByRole("button", { name: "Отозвать токен" });
  await trigger.click();
  const dialog = page.getByRole("dialog", { name: "Отозвать токен" });
  await dialog.getByRole("textbox").fill(name);

  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  await page.route(`${API}/api-tokens?*`, async (route) => {
    await gate;
    await route.continue();
  }, { times: 1 });
  try {
    await dialog.getByRole("button", { name: "Подтвердить", exact: true }).click();
    await expect(dialog).toBeHidden();
    await expect(trigger).toBeDisabled();
    await expect(trigger).toBeFocused();
  } finally {
    release();
  }
  await expect(token.locator(".token-status")).toHaveText("Отозван");
  await expect(token).toBeFocused();
});

test("refreshing organization tokens keeps focus and hides the tab after all token rights are removed", async ({ page, request, account }) => {
  const owner = await makeAccount(request);
  const company = await createCompany(request, owner);
  const granted = await request.post(`${API}/memberships`, { headers: headers(owner), data: {
    company_id: company, user_id: account.id, role: "user", permissions: ["tokens.read", "company.manage"],
  } });
  expect(granted.status()).toBe(201);
  const member = await granted.json();
  await startSession(page, account);
  await page.goto(`organizations/${company}`);
  const settings = await openOrganizationTokens(page);
  const refresh = settings.getByRole("button", { name: "Обновить", exact: true });
  await expect(refresh).toBeEnabled();
  const reading = page.waitForResponse(`${API}/api-tokens?limit=10&offset=0&company_id=${company}`);
  await refresh.press("Enter");
  expect((await reading).status()).toBe(200);
  await expect(refresh).toBeEnabled();
  await expect(refresh).toBeFocused();

  const updated = await request.patch(`${API}/memberships/${member.id}`, {
    headers: headers(owner), data: { permissions: ["company.manage"] },
  });
  expect(updated.status()).toBe(200);
  await refresh.press("Enter");
  await expect(settings.getByRole("tab", { name: "API-токены" })).toHaveCount(0);
  await expect(settings.getByRole("tab", { name: "Основное" })).toBeFocused();
  await settings.getByRole("button", { name: "Закрыть диалог" }).click();
  await openOrganizationSettings(page);
  await expect(settings.getByRole("tab", { name: "API-токены" })).toHaveCount(0);
});

test("organization deletion keeps its member conflict visible after a previous rename settles", async ({ page, request, account }) => {
  const name = "Северная лаборатория";
  const company = await createCompany(request, account, name);
  const member = await makeAccount(request);
  const joined = await request.post(`${API}/memberships`, { headers: headers(account), data: {
    company_id: company, user_id: member.id, role: "user",
  } });
  expect(joined.status()).toBe(201);
  const endpoint = `${API}/companies/${company}`;
  await startSession(page, account);
  await page.goto("organizations");
  const row = page.getByRole("link", { name: new RegExp(name) });
  await row.click();
  let settings = await openOrganizationSettings(page);
  const rename = await pauseMutation(page, endpoint, { method: "PATCH" });
  const field = settings.getByLabel("Название организации", { exact: true });
  await field.fill("Новое название команды");
  await field.press("Enter");
  await rename.pending;

  await page.goBack();
  await row.click();
  settings = await openOrganizationSettings(page);
  await settings.getByRole("button", { name: "Удалить организацию", exact: true }).click();
  const confirmation = page.getByRole("dialog", { name: "Удалить организацию" });
  const typedName = confirmation.getByRole("textbox");
  await typedName.fill(name);
  const deletion = await pauseMutation(page, endpoint);
  try {
    await typedName.press("Enter");
    await deletion.pending;
    const lookup = page.waitForResponse((response) => response.url() === endpoint && response.request().method() === "GET");
    rename.release();
    expect((await lookup).status()).toBe(200);
    await expect(confirmation).toBeVisible();
    const response = page.waitForResponse((result) => result.url() === endpoint && result.request().method() === "DELETE");
    deletion.release();
    expect((await response).status()).toBe(409);
    await expect(confirmation.getByRole("alert")).toContainText(/участник/i);
    await expect(typedName).toBeFocused();
  } finally {
    rename.release();
    deletion.release();
  }
});
