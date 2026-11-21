import { openOrganizationMember, openOrganizationSettings, openOrganizationTokens } from "./organization-controls";
import { test, expect, login, startSession, API, addComputer, headers, makeAccount, pauseMutation } from "./fixtures";

test("server pagination searches all organizations and URL state survives reload and both ways back", async ({
  page,
  request,
  account,
}) => {
  for (let i = 0; i < 23; i++) {
    const response = await request.post(`${API}/companies`, {
      headers: headers(account),
      data: { name: `Team ${i}` },
    });
    expect(response.status()).toBe(201);
  }
  await request.post(`${API}/companies`, {
    headers: headers(account),
    data: { name: "Other space" },
  });
  await startSession(page, account);
  const directoryCalls: URL[] = [];
  page.on("request", (req) => {
    if (req.url().includes("/v1/organization-directory?"))
      directoryCalls.push(new URL(req.url()));
  });
  await page.goto("organizations?q=Team&page=2");
  const expected = ["Team 20", "Team 21", "Team 22"];
  await expect(page.locator(".organization-row h3")).toHaveText(expected);
  await expect(page.getByRole("searchbox")).toHaveValue("Team");
  await expect(page.locator(".directory-count")).toHaveText("23 / 24");
  expect(directoryCalls[0].searchParams.get("offset")).toBe("20");
  expect(directoryCalls[0].searchParams.get("limit")).toBe("20");
  await expect(page.getByRole("button", { name: /Далее/ })).toBeDisabled();
  await page.reload();
  await expect(page.locator(".organization-row h3")).toHaveText(expected);
  const row = page.getByRole("article", { name: "Team 20", exact: true });
  const { width } = (await row.boundingBox())!;
  await row.click({ position: { x: width - 4, y: 4 } });
  await expect(page).toHaveURL(/\/organizations\/[^/]+$/);
  await page.goBack();
  await expect(page.locator(".organization-row h3")).toHaveText(expected);
  await row.getByTestId("organization-computer-count").click();
  await expect(page).toHaveURL(/\/organizations\/[^/]+$/);
  await page
    .getByRole("link", { name: "← К организациям", exact: true })
    .click();
  await expect(page).toHaveURL(/organizations\?q=Team&page=2$/);
  await expect(page.locator(".organization-row h3")).toHaveText(expected);
  await page.getByRole("searchbox").fill("Team 22");
  await expect(page.locator(".organization-row h3")).toHaveText(["Team 22"]);
  expect(new URL(page.url()).searchParams.has("page")).toBe(false);
  await expect(page.locator(".directory-count")).toHaveText("1 / 24");
  await page.getByRole("button", { name: "Очистить поиск" }).click();
  await expect(page.locator(".organization-row")).toHaveCount(20);
  expect(new URL(page.url()).searchParams.has("q")).toBe(false);
  await expect(page.locator(".directory-count")).toHaveText("24");
  await page.getByRole("button", { name: /Далее/ }).click();
  await expect(page.locator(".organization-row")).toHaveCount(4);
  await page.getByRole("button", { name: /Назад/ }).click();
  await expect(page.locator(".organization-row")).toHaveCount(20);
});

test("typing is debounced for 250 ms and clearing starts an immediate unfiltered request", async ({
  page,
  request,
  account,
}) => {
  await request.post(`${API}/companies`, {
    headers: headers(account),
    data: { name: "Laboratory" },
  });
  await login(page, account);
  await page.goto("organizations");
  await expect(page.locator(".organization-row")).toHaveCount(1);
  await page.clock.install({ time: new Date("2026-10-15T00:00:00Z") });
  await page.clock.pauseAt(new Date("2026-10-15T00:00:01Z"));
  const queries: string[] = [];
  page.on("request", (req) => {
    if (req.url().includes("/v1/organization-directory?"))
      queries.push(new URL(req.url()).searchParams.get("q") || "");
  });
  const search = page.getByRole("searchbox");
  await search.fill("L");
  await page.clock.runFor(100);
  await search.fill("La");
  await page.clock.runFor(100);
  await search.fill("Lab");
  await page.clock.runFor(249);
  expect(queries).toEqual([]);
  await expect(page.locator(".organization-directory")).toHaveAttribute(
    "aria-busy",
    "true",
  );
  await page.clock.runFor(1);
  await expect.poll(() => queries).toEqual(["Lab"]);
  await expect(page.locator(".organization-row h3")).toHaveText(["Laboratory"]);
  await search.fill("not sent");
  await page.clock.runFor(100);
  await page.getByRole("button", { name: "Очистить поиск" }).click();
  await page.clock.runFor(1);
  await expect.poll(() => queries).toEqual(["Lab", ""]);
  await page.clock.runFor(300);
  expect(queries).toEqual(["Lab", ""]);
  await expect(page.locator(".organization-row h3")).toHaveText(["Laboratory"]);
});

/** Deliver a real obsolete response over an independently live transport after cancellation; the newer directory data must remain authoritative. */
test("a delayed obsolete response cannot replace newer results even when transport ignores abort", async ({
  page,
  request,
  account,
}) => {
  for (const name of ["Old laboratory", "New laboratory"])
    await request.post(`${API}/companies`, {
      headers: headers(account),
      data: { name },
    });
  await page.addInitScript(() => {
    const original = window.fetch.bind(window);
    const aborted: string[] = [];
    const delivered: { query: string; callerAborted: boolean; transportAborted: boolean; status: number; names: string[] }[] = [];
    Object.defineProperty(window, "__directoryAborted", { value: aborted });
    Object.defineProperty(window, "__directoryDelivered", { value: delivered });
    window.fetch = async (input, options) => {
      const url = new URL(input instanceof Request ? input.url : String(input), location.href);
      if (url.pathname !== "/v1/organization-directory") return original(input, options);
      const query = url.searchParams.get("q") || "";
      const signal = options?.signal ?? (input instanceof Request ? input.signal : undefined);
      signal?.addEventListener("abort", () => aborted.push(query), { once: true });
      // undefined would inherit Request.signal; override it with an independent
      // live signal so the old HTTP response really reaches the browser.
      const transport = new AbortController();
      const response = await original(input, { ...options, signal: transport.signal });
      const body = await response.clone().json();
      delivered.push({
        query, callerAborted: signal?.aborted ?? false, transportAborted: transport.signal.aborted,
        status: response.status, names: body.items.map((item: any) => item.company?.name ?? item.invitation.company_name),
      });
      return response;
    };
  });
  await login(page, account);
  await page.goto("organizations");
  await expect(page.locator(".organization-row")).toHaveCount(2);
  let release!: () => void, received!: () => void, delivered!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  const oldReceived = new Promise<void>((resolve) => {
    received = resolve;
  });
  const oldDelivered = new Promise<void>((resolve) => {
    delivered = resolve;
  });
  await page.route("**/v1/organization-directory?*", async (route) => {
    if (new URL(route.request().url()).searchParams.get("q") !== "Old")
      return route.continue();
    const response = await route.fetch();
    received();
    await gate;
    await route.fulfill({ response });
    delivered();
  });
  try {
    await page.getByRole("searchbox").fill("Old");
    await oldReceived;
    await page.getByRole("searchbox").fill("New");
    await expect
      .poll(() =>
        page.evaluate(
          () =>
            (window as unknown as { __directoryAborted: string[] })
              .__directoryAborted,
        ),
      )
      .toContain("Old");
    await expect(page.locator(".organization-row h3")).toHaveText([
      "New laboratory",
    ]);
    release();
    await oldDelivered;
    await expect.poll(() => page.evaluate(() => (window as any).__directoryDelivered.find((row: any) => row.query === "Old"))).toEqual({
      query: "Old", callerAborted: true, transportAborted: false, status: 200, names: ["Old laboratory"],
    });
    await page.waitForLoadState("networkidle");
    await expect(page.locator(".organization-row h3")).toHaveText([
      "New laboratory",
    ]);
    await expect(page.locator(".directory-count")).toHaveText("1 / 2");
    await expect(page.getByRole("alert")).toHaveCount(0);
    await expect(page.locator(".organization-directory")).toHaveAttribute(
      "aria-busy",
      "false",
    );
  } finally {
    release();
  }
});

test("search retries transport failures automatically, retaining the query without claiming no matches", async ({
  page,
  request,
  account,
}) => {
  await request.post(`${API}/companies`, {
    headers: headers(account),
    data: { name: "Retry lab" },
  });
  await login(page, account);
  await page.goto("organizations");
  await expect(page.locator(".organization-row")).toHaveCount(1);
  let fail = true;
  let attempts = 0;
  await page.route("**/v1/organization-directory?*", (route) => {
    ++attempts;
    return fail ? route.abort("failed") : route.continue();
  });
  await page.getByRole("searchbox").fill("retry");
  await expect.poll(() => attempts).toBeGreaterThan(0);
  await expect(page.getByRole("alert")).toHaveCount(0);
  await expect(
    page.getByRole("heading", { name: "Организации не найдены", exact: true }),
  ).toHaveCount(0);
  await expect(page.locator(".directory-count")).toHaveText("—");
  await expect(page.getByRole("searchbox")).toHaveValue("retry");
  expect(new URL(page.url()).searchParams.get("q")).toBe("retry");
  fail = false;
  await expect(page.locator(".organization-row h3")).toHaveText(["Retry lab"]);
  await expect(page.getByRole("alert")).toHaveCount(0);
  expect(attempts).toBeGreaterThan(1);
  await page.getByRole("searchbox").fill("missing");
  await expect(
    page.getByRole("heading", { name: "Организации не найдены", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".directory-count")).toHaveText("0 / 1");
});

test("deleting an organization returns to the directory and corrects an empty search result page", async ({
  page,
  request,
  account,
}) => {
  for (let i = 0; i < 21; i++) {
    const response = await request.post(`${API}/companies`, {
      headers: headers(account),
      data: { name: `Space ${i}` },
    });
    expect(response.status()).toBe(201);
  }
  await startSession(page, account);
  await page.goto("organizations?q=Space&page=2");
  await expect(page.locator(".organization-row h3")).toHaveText(["Space 20"]);
  await page.getByRole("heading", { name: "Space 20", exact: true }).click();
  await openOrganizationSettings(page);
  await page.getByRole("button", { name: "Удалить организацию" }).click();
  const dialog = page.getByRole("dialog", { name: "Удалить организацию" });
  await dialog.getByRole("textbox").fill("Space 20");
  await dialog.getByRole("button", { name: "Подтвердить", exact: true }).click();
  await expect(page).toHaveURL(/\/organizations\?q=Space$/);
  await expect(page.locator(".organization-row")).toHaveCount(20);
});

test("a late deletion refreshes the open directory without restoring an old search", async ({
  page,
  request,
  account,
}) => {
  const name = "Удаляемая лаборатория";
  const response = await request.post(`${API}/companies`, {
    headers: headers(account),
    data: { name },
  });
  expect(response.status()).toBe(201);
  const { id } = await response.json();
  const survivorName = "Другая лаборатория";
  expect((await request.post(`${API}/companies`, {
    headers: headers(account),
    data: { name: survivorName },
  })).status()).toBe(201);
  const endpoint = `${API}/companies/${id}`;
  const removal = await pauseMutation(page, endpoint);
  let releaseRefresh!: () => void;
  let refreshStarted!: () => void;
  const refreshGate = new Promise<void>((resolve) => { releaseRefresh = resolve; });
  const refreshing = new Promise<void>((resolve) => { refreshStarted = resolve; });
  await startSession(page, account);
  await page.goto(`organizations?q=${encodeURIComponent("Удаляемая")}`);
  const link = page.getByRole("link", { name: `Открыть организацию: ${name}` });
  try {
    await link.click();
    await openOrganizationSettings(page);
    await page.getByRole("button", { name: "Удалить организацию" }).click();
    const dialog = page.getByRole("dialog", { name: "Удалить организацию" });
    await expect(dialog).toBeVisible();
    await dialog.getByRole("textbox").fill(name);
    await dialog.getByRole("button", { name: "Подтвердить", exact: true }).click();
    await removal.pending;
    await page.goBack();
    const search = page.getByRole("searchbox");
    await search.fill("лаборатория");
    await expect(link).toBeVisible();
    const survivor = page.getByRole("link", { name: `Открыть организацию: ${survivorName}` });
    await expect(survivor).toBeVisible();
    await page.route("**/v1/organization-directory?*", async (route) => {
      refreshStarted();
      await refreshGate;
      await route.continue();
    });
    const deleted = page.waitForResponse((result) =>
      result.url() === endpoint && result.request().method() === "DELETE",
    );
    removal.release();
    expect((await deleted).status()).toBe(204);
    await refreshing;
    await expect(link).toHaveCount(0);
    await expect(survivor).toBeVisible();
    await expect(search).toHaveValue("лаборатория");
    releaseRefresh();
    await expect(page.getByRole("region", { name: "Все организации" })).toHaveAttribute("aria-busy", "false");
  } finally {
    removal.release();
    releaseRefresh();
  }
});

for (const reopenBeforeLeaving of [false, true]) {
  test(`a late leave preserves a fresh membership when reopened ${reopenBeforeLeaving ? "before the previous membership is removed" : "after joining again"}`, async ({
    page,
    request,
    account,
  }) => {
    const owner = await makeAccount(request);
    const name = "Повторное вступление";
    const created = await request.post(`${API}/companies`, {
      headers: headers(owner),
      data: { name },
    });
    expect(created.status()).toBe(201);
    const { id } = await created.json();
    async function join() {
      const joined = await request.post(`${API}/memberships`, {
        headers: headers(owner),
        data: { company_id: id, user_id: account.id, permissions: [] },
      });
      expect(joined.status()).toBe(201);
      return (await joined.json()).id;
    }
    const oldMembership = await join();
    const endpoint = `${API}/memberships/${oldMembership}`;
    const removal = await pauseMutation(page, endpoint, { pause: reopenBeforeLeaving ? "both" : "response" });
    await startSession(page, account);
    await page.goto("organizations");
    const companyLink = page.getByRole("link", { name: `Открыть организацию: ${name}` });
    const leave = page.getByRole("button", { name: "Выйти из организации", exact: true });
    async function reopen(membershipId: string) {
      await page.goBack();
      const reopened = page.waitForResponse(`${API}/companies/${id}`);
      await companyLink.click();
      expect((await (await reopened).json()).my_membership_id).toBe(membershipId);
      await openOrganizationSettings(page);
      await expect(leave).toBeVisible();
    }

    try {
      await companyLink.click();
      await openOrganizationSettings(page);
      await leave.click();
      const dialog = page.getByRole("dialog", { name: "Подтвердите выход" });
      await dialog.getByRole("textbox").fill(account.first_name);
      await dialog.getByRole("button", { name: "Подтвердить", exact: true }).click();
      await removal.sent;
      if (reopenBeforeLeaving) {
        await reopen(oldMembership);
        removal.commit();
      }
      await removal.pending;
      const newMembership = await join();
      expect(newMembership).not.toBe(oldMembership);
      if (!reopenBeforeLeaving) await reopen(newMembership);
      const left = page.waitForResponse((result) =>
        result.url() === endpoint && result.request().method() === "DELETE",
      );
      removal.release();
      expect((await left).status()).toBe(204);
      await page.waitForLoadState("networkidle");
      await expect(page).toHaveURL(new RegExp(`/organizations/${id}$`));
      await openOrganizationSettings(page);
      await leave.click();
      await dialog.getByRole("textbox").fill(account.first_name);
      const leftAgain = page.waitForResponse((result) =>
        result.url() === `${API}/memberships/${newMembership}` && result.request().method() === "DELETE",
      );
      await dialog.getByRole("button", { name: "Подтвердить", exact: true }).click();
      expect((await leftAgain).status()).toBe(204);
      await expect(page).toHaveURL(/\/organizations$/);
    } finally {
      removal.release();
    }
  });
}

test("a late leave returns a reopened organization to the search after access is lost", async ({
  page,
  request,
  account,
}) => {
  const owner = await makeAccount(request);
  const name = "Команда при возврате";
  const created = await request.post(`${API}/companies`, {
    headers: headers(owner),
    data: { name },
  });
  expect(created.status()).toBe(201);
  const { id } = await created.json();
  const joined = await request.post(`${API}/memberships`, {
    headers: headers(owner),
    data: { company_id: id, user_id: account.id, permissions: [] },
  });
  expect(joined.status()).toBe(201);
  const membership = await joined.json();
  const endpoint = `${API}/memberships/${membership.id}`;
  const removal = await pauseMutation(page, endpoint);
  let releaseLookup!: () => void;
  let startLookup!: () => void;
  const lookupGate = new Promise<void>((resolve) => { releaseLookup = resolve; });
  const lookupStarted = new Promise<void>((resolve) => { startLookup = resolve; });
  await startSession(page, account);
  await page.goto(`organizations?q=${encodeURIComponent("возврате")}`);
  const link = page.getByRole("link", { name: `Открыть организацию: ${name}` });
  try {
    await link.click();
    await openOrganizationSettings(page);
    await page.getByRole("button", { name: "Выйти из организации", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Подтвердите выход" });
    await dialog.getByRole("textbox").fill(account.first_name);
    await dialog.getByRole("button", { name: "Подтвердить", exact: true }).click();
    await removal.pending;
    await page.goBack();
    await page.route(`${API}/companies/${id}`, async (route) => {
      startLookup();
      await lookupGate;
      await route.continue();
    }, { times: 1 });
    await link.click();
    await lookupStarted;
    const left = page.waitForResponse((result) =>
      result.url() === endpoint && result.request().method() === "DELETE",
    );
    removal.release();
    expect((await left).status()).toBe(204);
    await expect(page.getByRole("searchbox")).toHaveValue("возврате");
    await expect(page.getByRole("heading", { name: "Организации не найдены" })).toBeVisible();
  } finally {
    releaseLookup();
    removal.release();
  }
});

test("a late leave hides stale organization actions when access cannot be checked", async ({
  page,
  request,
  account,
}) => {
  const owner = await makeAccount(request);
  const name = "Команда с проверкой доступа";
  const created = await request.post(`${API}/companies`, {
    headers: headers(owner),
    data: { name },
  });
  expect(created.status()).toBe(201);
  const { id } = await created.json();
  const joined = await request.post(`${API}/memberships`, {
    headers: headers(owner),
    data: { company_id: id, user_id: account.id, role: "creator" },
  });
  expect(joined.status()).toBe(201);
  const { id: membershipId } = await joined.json();
  const endpoint = `${API}/memberships/${membershipId}`;
  const removal = await pauseMutation(page, endpoint);
  await startSession(page, account);
  await page.goto(`organizations?q=${encodeURIComponent("проверкой доступа")}`);
  const link = page.getByRole("link", { name: `Открыть организацию: ${name}` });
  const leave = page.getByRole("button", { name: "Выйти из организации", exact: true });
  const remove = page.getByRole("button", { name: "Удалить организацию", exact: true });
  try {
    await link.click();
    await openOrganizationSettings(page);
    await leave.click();
    const dialog = page.getByRole("dialog", { name: "Подтвердите выход" });
    await dialog.getByRole("textbox").fill(account.first_name);
    await dialog.getByRole("button", { name: "Подтвердить", exact: true }).click();
    await removal.pending;
    await page.goBack();
    await link.click();
    await openOrganizationSettings(page);
    await expect(remove).toBeEnabled();
    await page.route(`${API}/companies/${id}`, async (route) => {
      const response = await route.fetch();
      await route.fulfill({ response, status: 503, json: { detail: "Unavailable" } });
    }, { times: 1 });
    removal.release();
    await expect(page.getByRole("alert")).toContainText("Не удалось связаться");
    await expect(leave).toHaveCount(0);
    await expect(remove).toHaveCount(0);
    await page.getByRole("button", { name: "Попробовать снова" }).click();
    await expect(page.getByRole("searchbox")).toHaveValue("проверкой доступа");
    await expect(link).toHaveCount(0);
  } finally {
    removal.release();
  }
});

for (const destination of ["rename", "rename with a new draft", "role change", "resource permissions"]) {
  test(`a late organization update refreshes the reopened view after ${destination}`, async ({
    page,
    request,
    account,
  }) => {
    const oldName = "Прежнее имя команды";
    const freshName = "Новое имя команды";
    const renaming = destination.startsWith("rename");
    const draftName = "Черновик переименования";
    const created = await request.post(`${API}/companies`, {
      headers: headers(account), data: { name: oldName },
    });
    expect(created.status()).toBe(201);
    const { id } = await created.json();
    const companyPath = `organizations/${id}`;
    let endpoint = `${API}/companies/${id}`;
    if (!renaming) {
      const coCreator = await makeAccount(request, "Боб");
      expect((await request.post(`${API}/memberships`, {
        headers: headers(account),
        data: { company_id: id, user_id: coCreator.id, role: "creator" },
      })).status()).toBe(201);
      const current = await request.get(endpoint, { headers: headers(account) });
      expect(current.status()).toBe(200);
      endpoint = `${API}/memberships/${(await current.json()).my_membership_id}`;
    }
    const computer = "Компьютер для проверки прав";
    if (destination === "resource permissions") await addComputer(request, account, computer, id, false);
    const update = await pauseMutation(page, endpoint, { method: "PATCH" });
    await startSession(page, account);
    await page.goto("organizations");
    const link = page.getByRole("link", { name: `Открыть организацию: ${oldName}` });
    const remove = page.getByRole("button", { name: "Удалить организацию", exact: true });
    try {
      await link.click();
      if (renaming) {
        await openOrganizationSettings(page);
        await page.getByLabel("Название организации").fill(freshName);
      } else {
        const self = page.getByRole("article", { name: account.first_name, exact: true });
        await openOrganizationMember(self);
        await self.getByRole("button", { name: "Изменить права" }).click();
        const editor = page.getByRole("dialog", { name: "Изменить права" });
        await editor.getByLabel("Роль", { exact: true }).selectOption("admin");
        if (destination === "resource permissions") {
          await editor.getByLabel("Настроить права отдельно").check();
          await editor.getByLabel("Просмотр ресурсов и истории мощностей").uncheck();
        }
      }
      const save = renaming
        ? page.getByRole("dialog", { name: "Настройки организации", exact: true })
        : page.getByRole("dialog", { name: "Изменить права" });
      await save.getByRole("button", { name: renaming ? "Сохранить" : "Сохранить изменения", exact: true }).click();
      await update.pending;
      await page.goBack();
      await link.click();
      await openOrganizationSettings(page);
      await expect(remove).toBeEnabled();
      const input = page.getByLabel("Название организации");
      if (destination === "rename with a new draft") await input.fill(draftName);
      if (destination === "resource permissions") {
        await page.keyboard.press("Escape");
        await page.getByRole("link", { name: /Список компьютеров/ }).click();
        await expect(page.getByRole("link", { name: computer, exact: true })).toBeVisible();
      }

      const saved = page.waitForResponse((result) =>
        result.url() === endpoint && result.request().method() === "PATCH",
      );
      update.release();
      expect((await saved).status()).toBe(200);
      if (destination === "resource permissions") {
        await expect(page.getByRole("alert")).toContainText("У вас нет прав");
        await expect(page.getByRole("link", { name: computer, exact: true })).toHaveCount(0);
        await page.getByRole("link", { name: /К организации/ }).click();
        await expect(page.getByRole("link", { name: /Список компьютеров/ })).toHaveCount(0);
      } else {
        await expect(page).toHaveURL(new RegExp(`/${companyPath}$`));
        if (renaming) {
          await expect(page.locator(".organization-hero h1")).toHaveAttribute("aria-label", `${freshName}.`);
          await expect(input).toHaveValue(destination === "rename" ? freshName : draftName);
          await remove.click();
          const dialog = page.getByRole("dialog", { name: "Удалить организацию" });
          await dialog.getByRole("textbox").fill(oldName);
          await expect(dialog.getByRole("button", { name: "Подтвердить", exact: true })).toBeDisabled();
          await dialog.getByRole("textbox").fill(freshName);
          await expect(dialog.getByRole("button", { name: "Подтвердить", exact: true })).toBeEnabled();
        } else {
          await expect(remove).toHaveCount(0);
          await page.keyboard.press("Escape");
          await expect(page.getByRole("article", { name: account.first_name, exact: true })).toContainText("Администратор");
        }
      }
    } finally {
      update.release();
    }
  });
}

for (const view of ["inventory", "computer details", "API tokens"]) {
  test(`a late leave clears inaccessible organization resources from ${view}`, async ({
    page,
    request,
    account,
  }) => {
    const owner = await makeAccount(request);
    const name = "Команда для проверки инвентаря";
    const created = await request.post(`${API}/companies`, {
      headers: headers(owner),
      data: { name },
    });
    expect(created.status()).toBe(201);
    const { id } = await created.json();
    const joined = await request.post(`${API}/memberships`, {
      headers: headers(owner),
      data: { company_id: id, user_id: account.id },
    });
    expect(joined.status()).toBe(201);
    const { id: membershipId } = await joined.json();
    const nameToHide = view === "API tokens" ? "Токен команды" : "Рабочая станция команды";
    if (view === "API tokens") {
      expect((await request.post(`${API}/api-tokens`, {
        headers: headers(owner),
        data: { company_id: id, name: nameToHide, scopes: ["read"] },
      })).status()).toBe(201);
    } else {
      await addComputer(request, owner, nameToHide, id, false);
    }
    const endpoint = `${API}/memberships/${membershipId}`;
    const removal = await pauseMutation(page, endpoint);
    const query = "проверки инвентаря";
    await startSession(page, account);
    await page.goto(`organizations?q=${encodeURIComponent(query)}`);
    const link = page.getByRole("link", { name: `Открыть организацию: ${name}` });
    try {
      await link.click();
      await openOrganizationSettings(page);
      await page.getByRole("button", { name: "Выйти из организации", exact: true }).click();
      const dialog = page.getByRole("dialog", { name: "Подтвердите выход" });
      await dialog.getByRole("textbox").fill(account.first_name);
      await dialog.getByRole("button", { name: "Подтвердить", exact: true }).click();
      await removal.pending;
      await page.goBack();
      await link.click();
      if (view === "API tokens") {
        await openOrganizationTokens(page);
        await expect(page.getByRole("article", { name: nameToHide })).toBeVisible();
      } else {
        await page.getByRole("link", { name: /Список компьютеров/ }).click();
        const computer = page.getByRole("link", { name: nameToHide, exact: true });
        await expect(computer).toBeVisible();
        if (view === "computer details") {
          await computer.click();
          await expect(page.getByRole("heading", { name: `${nameToHide}.`, exact: true })).toBeVisible();
        }
      }
      const inventoryUrl = page.url();
      const left = page.waitForResponse((result) =>
        result.url() === endpoint && result.request().method() === "DELETE",
      );
      removal.release();
      expect((await left).status()).toBe(204);
      if (view === "API tokens") {
        await expect(page.getByRole("searchbox")).toHaveValue(query);
        await expect(page.getByRole("dialog", { name: "Настройки организации" })).toHaveCount(0);
        await expect(page.getByText(nameToHide, { exact: true })).toHaveCount(0);
      } else {
        await expect(page.getByRole("alert")).toContainText("не найден");
        await expect(page).toHaveURL(inventoryUrl);
        await expect(page.getByText(nameToHide, { exact: true })).toHaveCount(0);
        if (view === "computer details") await page.getByRole("link", { name: /К списку компьютеров/ }).click();
        await page.getByRole("link", { name: /К организациям/ }).click();
        await expect(page.getByRole("searchbox")).toHaveValue(query);
        await expect(page.getByRole("heading", { name: "Организации не найдены" })).toBeVisible();
      }
    } finally {
      removal.release();
    }
  });
}

for (const destination of [{ label: "Compute", path: "/compute/pools" }, { label: "Обзор", path: "/" }]) {
  test(`a late leave refreshes the organization pool without leaving ${destination.label}`, async ({
    page,
    request,
    account,
  }) => {
    const owner = await makeAccount(request);
    const response = await request.post(`${API}/companies`, {
      headers: headers(owner),
      data: { name: "Команда для выхода" },
    });
    expect(response.status()).toBe(201);
    const company = await response.json();
    const joined = await request.post(`${API}/memberships`, {
      headers: headers(owner),
      data: { company_id: company.id, user_id: account.id },
    });
    expect(joined.status()).toBe(201);
    await addComputer(request, owner, "Компьютер для сводки", company.id);
    const membership = await joined.json();
    const endpoint = `${API}/memberships/${membership.id}`;
    const removal = await pauseMutation(page, endpoint);
    await startSession(page, account);
    await page.goto("organizations");
    const nav = page.getByRole("navigation", { name: "Навигация кабинета" });
    try {
      await page.getByRole("link", { name: /Открыть организацию: Команда для выхода/ }).click();
      await openOrganizationSettings(page);
      await page.getByRole("button", { name: "Выйти из организации", exact: true }).click();
      const dialog = page.getByRole("dialog", { name: "Подтвердите выход" });
      await dialog.getByRole("textbox").fill(account.first_name);
      await dialog.getByRole("button", { name: "Подтвердить", exact: true }).click();
      await removal.pending;
      await page.goBack();
      await nav.getByRole("link", { name: destination.label, exact: true }).click();
      const catalog = destination.path === "/compute/pools";
      if (catalog) await page.getByRole("link", { name: "Вычислительные пулы", exact: true }).click();
      const pool = page.getByRole(catalog ? "article" : "region", { name: company.name, exact: true });
      await expect(pool.getByTestId(catalog ? "organization-computer-count" : "computer-count")).toHaveText("1");
      const companies = "**/v1/companies?*";
      if (catalog) {
        await page.route(companies, (route) => route.fulfill({ status: 503, json: { detail: "Temporary outage" } }));
      }
      const left = page.waitForResponse((result) =>
        result.url() === endpoint && result.request().method() === "DELETE",
      );
      removal.release();
      expect((await left).status()).toBe(204);
      if (catalog) await expect(page.getByRole("alert")).toBeVisible();
      await expect(pool).toHaveCount(0);
      if (catalog) {
        await expect(page.getByRole("article", { name: "Личный пул" })).toBeVisible();
        await page.unroute(companies);
        await page.getByRole("alert").getByRole("button").click();
        await expect(page.getByRole("alert")).toHaveCount(0);
        await expect(pool).toHaveCount(0);
      }
      expect(new URL(page.url()).pathname).toBe(destination.path);
      await nav.getByRole("link", { name: "Организации", exact: true }).click();
      await expect(page.getByRole("heading", { name: "Ваша команда начинается здесь" })).toBeVisible();
    } finally {
      removal.release();
    }
  });
}
