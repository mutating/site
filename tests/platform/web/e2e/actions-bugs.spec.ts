import { test, expect, login } from "./fixtures";
import { actionsPeer, binding, repository, repositoryURL } from "./actions-peer";

/** A late create/edit/toggle response belongs to its original editor and must not navigate a different repository. */
for (const action of ["create", "edit", "toggle"] as const) {
  test(`late ${action} response cannot overwrite another repository page`, async ({ page, account }) => {
    const peer = await actionsPeer(page);
    peer.repositories.push({ ...repository, id: 790, full_name: "mutating/another", default_branch: "develop" });
    if (action !== "create") peer.bindings = [{ ...binding }];
    let release!: () => void;
    let entered!: () => void;
    const gate = new Promise<void>(resolve => release = resolve);
    const pending = new Promise<void>(resolve => entered = resolve);
    peer.custom = async (route, url) => {
      const request = route.request();
      if (request.method() !== (action === "create" ? "POST" : "PATCH") || !url.pathname.startsWith("/v1/github/routes")) return false;
      entered(); await gate;
      const body = request.postDataJSON();
      const saved = { ...binding, ...(body.configuration ?? body), revision: action === "create" ? 1 : 2 };
      peer.bindings = [saved];
      await route.fulfill({ status: action === "create" ? 201 : 200, headers: { "Access-Control-Allow-Origin": "http://127.0.0.1:18101", "Access-Control-Allow-Credentials": "true" }, json: saved });
      return true;
    };
    await login(page, account); await page.goto(repositoryURL);
    if (action === "edit") await page.getByLabel("CPU", { exact: true }).fill("1");
    const response = page.waitForResponse(row => row.request().method() === (action === "create" ? "POST" : "PATCH") && row.url().includes("/v1/github/routes"));
    await page.getByRole("button", { name: action === "create" ? "Подключить" : action === "edit" ? "Сохранить настройки" : "Отключить подключение", exact: true }).click();
    await pending;
    await page.getByRole("link", { name: "← К репозиториям", exact: true }).click();
    await page.getByRole("article", { name: "mutating/another" }).getByRole("link", { name: "Подключить", exact: true }).click();
    await expect(page.getByLabel("Ветка", { exact: true })).toHaveValue("develop");
    const currentURL = page.url();
    release(); await response;
    await page.waitForLoadState("networkidle");
    await expect(page).toHaveURL(currentURL);
    await expect(page.getByRole("heading", { name: "mutating/another.", exact: true })).toBeVisible();
    await expect(page.getByLabel("Ветка", { exact: true })).toHaveValue("develop");
    await expect(page.getByRole("button", { name: "Подключить", exact: true })).toBeEnabled();
  });
}

/** Returning to the saved branch must never expose YAML built from another branch's retained metadata. */
test("setup waits for current branch metadata and stays hidden after a failed refresh", async ({ page, account }) => {
  const peer = await actionsPeer(page); peer.bindings = [{ ...binding }];
  const mainWorkflows = structuredClone(peer.workflows);
  await login(page, account); await page.goto(repositoryURL);
  await expect(page.getByLabel("Workflow YAML", { exact: true })).toBeVisible();
  peer.workflows = [{ path: ".github/workflows/develop-only.yml", name: "Develop only", jobs: ["devjob"], parse_error: false }];
  await page.getByLabel("Ветка", { exact: true }).selectOption("develop");
  await expect(page.getByRole("button", { name: "Сохранить настройки", exact: true })).toBeEnabled();
  let release!: () => void; let entered!: () => void;
  const gate = new Promise<void>(resolve => release = resolve);
  const pending = new Promise<void>(resolve => entered = resolve);
  peer.custom = async (route, url) => {
    if (!url.pathname.endsWith("/configuration") || url.searchParams.get("branch") !== "main") return false;
    entered(); await gate;
    await route.fulfill({ status: 503, headers: { "Access-Control-Allow-Origin": "http://127.0.0.1:18101", "Access-Control-Allow-Credentials": "true" }, json: {} });
    return true;
  };
  await page.getByLabel("Ветка", { exact: true }).selectOption("main");
  await pending;
  await expect(page.getByLabel("Workflow YAML", { exact: true })).toHaveCount(0);
  release(); await expect(page.getByRole("alert")).toBeVisible();
  await expect(page.getByLabel("Workflow YAML", { exact: true })).toHaveCount(0);
  peer.custom = null; peer.workflows = mainWorkflows;
  await page.getByRole("button", { name: /Попробовать снова/ }).click();
  await expect(page.getByLabel("Workflow YAML", { exact: true })).toHaveValue(/ci.yml/);
  await expect(page.getByLabel("Задача workflow", { exact: true })).toHaveValue("test");
});

/** A bookmarked organizational audit ID cannot silently become a personal connection after access is revoked. */
test("unavailable explicit binding never loads another pool's history", async ({ page, account }) => {
  const peer = await actionsPeer(page); peer.bindings = [{ ...binding }];
  const missing = "22222222-2222-4222-8222-222222222222";
  await login(page, account);
  await page.goto(repositoryURL.replace("?", "/history?") + `&binding=${missing}`);
  await page.waitForLoadState("networkidle");
  expect(peer.calls.filter(row => row.path.endsWith("/revisions"))).toEqual([]);
  await expect(page.getByText("Запрошенная привязка недоступна.", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "← К репозиториям", exact: true })).toBeVisible();
  await expect(page.locator(".binding-revision")).toHaveCount(0);
});

/** Aggregate GitHub reads can legitimately take the server's 60-second discovery budget; other requests keep their normal deadline. */
for (const endpoint of ["catalog", "configuration"] as const) {
  test(`${endpoint} allows the server discovery budget without extending ordinary requests`, async ({ page, account }) => {
    await actionsPeer(page);
    await login(page, account);
    await page.addInitScript(() => {
      let budget = 0;
      (window as any).__requestBudgets = [];
      const timeout = AbortSignal.timeout.bind(AbortSignal);
      AbortSignal.timeout = milliseconds => { budget = milliseconds; return timeout(milliseconds); };
      const original = window.fetch.bind(window);
      window.fetch = (input, options) => {
        (window as any).__requestBudgets.push({ path: new URL(String(input), location.href).pathname, budget });
        return original(input, options);
      };
    });
    await page.goto(endpoint === "catalog" ? "/actions" : repositoryURL);
    const path = endpoint === "catalog" ? "/v1/github/repositories" : "/v1/github/repositories/789/configuration";
    await expect.poll(() => page.evaluate(target => (window as any).__requestBudgets.find((row: any) => row.path === target)?.budget ?? 0, path)).toBeGreaterThan(60_000);
    const ordinary = await page.evaluate(() => (window as any).__requestBudgets.filter((row: any) => row.path === "/v1/auth/session"));
    expect(ordinary.length).toBeGreaterThan(0);
    expect(ordinary.every((row: any) => row.budget === 20_000)).toBe(true);
  });
}

/** Reinstalling the App changes its immutable ID; reconnect through POST while the old binding stays in history. */
test("reinstalled App opens a new connection rather than patching the old installation", async ({ page, account }) => {
  const peer = await actionsPeer(page);
  const old = { ...binding, enabled: false, revision: 2, ref: "refs/heads/develop" };
  peer.bindings = [old]; peer.repositories[0].installation_id = 457;
  const freshId = "22222222-2222-4222-8222-222222222222";
  peer.custom = async (route, url) => {
    if (url.pathname !== "/v1/github/routes" || route.request().method() !== "POST") return false;
    const saved = { ...binding, ...route.request().postDataJSON(), id: freshId };
    peer.bindings.push(saved);
    await route.fulfill({ status: 201, headers: { "Access-Control-Allow-Origin": "http://127.0.0.1:18101", "Access-Control-Allow-Credentials": "true" }, json: saved });
    return true;
  };
  await login(page, account); await page.goto("/actions");
  await page.getByRole("article", { name: repository.full_name }).getByRole("link", { name: "Настроить", exact: true }).click();
  await expect(page.getByLabel("Ветка", { exact: true })).toHaveValue("main");
  await expect(page.getByRole("link", { name: "История изменений", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Включить подключение", exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "Подключить", exact: true }).click();
  await expect(page.getByLabel("Workflow YAML", { exact: true })).toBeVisible();
  const creates = peer.calls.filter(row => row.method === "POST" && row.path === "github/routes");
  expect(creates).toHaveLength(1);
  expect(creates[0].body).toMatchObject({ installation_id: 457, ref: "refs/heads/main" });
  expect(peer.calls.filter(row => row.method === "PATCH")).toEqual([]);
  expect(peer.bindings.find(row => row.id === old.id)).toEqual(old);
  await page.getByRole("link", { name: "История изменений", exact: true }).click();
  await expect(page.getByLabel("Подключение в истории", { exact: true }).locator("option")).toHaveCount(2);
});

/** Historical installation identity cannot be edited or re-enabled; an old enabled connection can still be disabled. */
for (const enabled of [false, true]) {
  test(`old installation is read only and ${enabled ? "can be disabled" : "cannot be enabled"}`, async ({ page, account }) => {
    const peer = await actionsPeer(page); peer.repositories[0].installation_id = 457;
    peer.bindings = [{ ...binding, enabled }];
    await login(page, account);
    await page.goto(repositoryURL.replace("installation=456", `installation=457&binding=${binding.id}`));
    await expect(page.getByText("Это подключение относится к прежней установке GitHub App. Откройте репозиторий из каталога, чтобы подключить текущую установку.", { exact: true })).toBeVisible();
    await expect(page.getByLabel("CPU", { exact: true })).toBeDisabled();
    await expect(page.getByRole("button", { name: "Сохранить настройки", exact: true })).toBeDisabled();
    await expect(page.getByRole("button", { name: "Включить подключение", exact: true })).toHaveCount(0);
    if (enabled) {
      await page.getByRole("button", { name: "Отключить подключение", exact: true }).click();
      await expect.poll(() => peer.bindings[0].enabled).toBe(false);
      expect(peer.calls.filter(row => row.method === "PATCH")[0].body).toMatchObject({ enabled: false });
    }
  });
}

/** A public mutation response must update known connection state without erasing expanded active-run facts when metadata refresh fails. */
for (const action of ["create", "edit", "noop", "disable", "enable"] as const) {
  test(`successful ${action} retains truthful metadata during a discovery outage`, async ({ page, account }) => {
    const peer = await actionsPeer(page);
    if (action !== "create") {
      peer.bindings = [{ ...binding, enabled: action !== "enable", active_runs: action === "disable" }];
      peer.runs = [{ id: "run", run_id: 1, run_attempt: 1, route_id: binding.id, route_revision: 1, started_at: "2026-10-08T12:00:00Z", created_at: "2026-10-08T12:00:00Z", status: "completed", reason: null, url: "https://github.com/mutating/ci-sandbox/actions/runs/1" }];
    }
    let saved = false;
    const headers = { "Access-Control-Allow-Origin": "http://127.0.0.1:18101", "Access-Control-Allow-Credentials": "true" };
    peer.custom = async (route, url) => {
      if (saved && url.pathname.endsWith("/configuration")) { await route.fulfill({ status: 503, headers, json: {} }); return true; }
      if (!["POST", "PATCH"].includes(route.request().method()) || !url.pathname.startsWith("/v1/github/routes")) return false;
      const body = route.request().postDataJSON();
      const { active_runs, ...publicResponse } = { ...binding, ...peer.bindings[0], ...(body.configuration ?? body), revision: action === "create" || action === "noop" ? 1 : 2 };
      peer.bindings = [{ ...publicResponse, active_runs: active_runs ?? false }]; saved = true;
      await route.fulfill({ status: action === "create" ? 201 : 200, headers, json: publicResponse });
      return true;
    };
    await login(page, account); await page.goto(repositoryURL);
    const badge = page.locator(".repository-settings-toolbar .repository-state");
    await expect(badge).toHaveText(action === "create" ? "Не настроен" : action === "enable" ? "Отключён" : "Подключён");
    if (action === "edit") await page.getByLabel("CPU", { exact: true }).fill("1");
    await page.getByRole("button", { name: action === "create" ? "Подключить" : action === "disable" ? "Отключить подключение" : action === "enable" ? "Включить подключение" : "Сохранить настройки", exact: true }).click();
    await expect(page.getByRole("alert")).toBeVisible();
    if (action === "disable") await expect(page.getByRole("button", { name: "Включить подключение", exact: true })).toBeDisabled();
    await expect(badge).toHaveText(action === "disable" ? "Отключён" : action === "noop" ? "Подключён" : "Ожидает настройки workflow");
    if (action === "disable") await expect(page.getByLabel("Workflow YAML", { exact: true })).toHaveCount(0);
    peer.custom = null;
    await page.getByRole("button", { name: /Попробовать снова/ }).click();
    await expect(page.getByRole("alert")).toHaveCount(0);
    await expect(badge).toHaveText(action === "disable" ? "Отключён" : action === "noop" ? "Подключён" : "Ожидает настройки workflow");
  });
}

/** A browser-history jump between two bindings of one repository must invalidate the older binding's pending callback. */
test("late archive edit cannot replace the binding reached through browser history", async ({ page, account }) => {
  const peer = await actionsPeer(page);
  const archive = { ...binding, id: "22222222-2222-4222-8222-222222222222", enabled: false, cpu_millis: 1000, ref: "refs/heads/develop" };
  peer.bindings = [{ ...binding }, archive];
  await login(page, account); await page.goto(repositoryURL);
  await page.getByRole("link", { name: "История изменений", exact: true }).click();
  await page.getByLabel("Подключение в истории", { exact: true }).selectOption(archive.id);
  await page.getByRole("link", { name: "← Настройки подключения", exact: true }).click();
  await expect(page.getByLabel("CPU", { exact: true })).toHaveValue("1");
  await page.getByLabel("CPU", { exact: true }).fill("0.5");
  let release!: () => void; let entered!: () => void;
  const gate = new Promise<void>(resolve => release = resolve);
  const pending = new Promise<void>(resolve => entered = resolve);
  peer.custom = async (route, url) => {
    if (route.request().method() !== "PATCH" || !url.pathname.endsWith(archive.id)) return false;
    entered(); await gate;
    const saved = { ...archive, ...route.request().postDataJSON().configuration, revision: 2 };
    peer.bindings[1] = saved;
    await route.fulfill({ headers: { "Access-Control-Allow-Origin": "http://127.0.0.1:18101", "Access-Control-Allow-Credentials": "true" }, json: saved });
    return true;
  };
  const response = page.waitForResponse(row => row.request().method() === "PATCH" && row.url().endsWith(archive.id));
  await page.getByRole("button", { name: "Сохранить настройки", exact: true }).click();
  await pending;
  await page.evaluate(() => history.go(-3));
  await expect(page.getByLabel("CPU", { exact: true })).toHaveValue("2");
  const currentURL = page.url();
  release(); await response; await page.waitForLoadState("networkidle");
  await expect(page).toHaveURL(currentURL);
  await expect(page.getByLabel("CPU", { exact: true })).toHaveValue("2");
  await expect(page.getByLabel("Ветка", { exact: true })).toHaveValue("main");
});

/** The real Git branch named null must not collide with an absent branch query and retain the default branch's workflows. */
test("literal null branch reloads its workflows and configures its real job", async ({ page, account }) => {
  const peer = await actionsPeer(page); peer.branches.push("null");
  await login(page, account); await page.goto(repositoryURL);
  await expect(page.getByLabel("Workflow для переопределения", { exact: true })).toHaveValue(".github/workflows/ci.yml");
  peer.workflows = [{ path: ".github/workflows/null-only.yml", name: "Null only", jobs: ["nulljob"], parse_error: false }];
  await page.getByLabel("Ветка", { exact: true }).selectOption("null");
  await expect(page.getByLabel("Workflow для переопределения", { exact: true })).toHaveValue(".github/workflows/null-only.yml");
  expect(peer.calls.some(row => row.path.endsWith("/configuration") && row.query.get("branch") === "null")).toBe(true);
  await page.getByRole("button", { name: "Подключить", exact: true }).click();
  await expect(page.getByLabel("Workflow YAML", { exact: true })).toHaveValue(/nulljob/);
  expect(peer.calls.find(row => row.method === "POST" && row.path === "github/routes")?.body?.ref).toBe("refs/heads/null");
});
