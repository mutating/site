import { test, expect, login } from "./fixtures";
import { actionsPeer, binding, repository, repositoryURL } from "./actions-peer";

/** The catalog replaces two unrelated lists; archived bindings must not duplicate projects. */
test("repository catalog shows four setup states, last CI and one row per repository", async ({ page, account }) => {
  const peer = await actionsPeer(page);
  peer.repositories = ["unconfigured", "pending", "connected", "disabled"].map((state, index) => ({ ...repository, id: 789 + index, full_name: `mutating/${state}`, state }));
  peer.bindings = [{ ...binding, id: "connected", repository_id: 791 }, ...Array.from({ length: 8 }, (_, index) => ({ ...binding, id: `archive-${index}`, repository_id: 792, enabled: false }))];
  peer.runs = [{ id: "run", route_id: "connected", run_id: 42, run_attempt: 1, status: "completed", reason: "github_success", created_at: "2026-10-08T12:00:00Z", started_at: "2026-10-08T12:00:00Z", url: "https://github.com/mutating/ci-sandbox/actions/runs/42/attempts/1" }];
  await login(page, account); await page.goto("actions");
  await expect(page.locator(".repository-card")).toHaveCount(4);
  for (const text of ["Не настроен", "Ожидает настройки workflow", "Подключён", "Отключён"]) await expect(page.locator(".repository-catalog").getByText(text, { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Привязки CI", exact: true })).toHaveCount(0);
  await expect(page.getByLabel("CPU", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("article", { name: "mutating/connected" }).getByRole("link", { name: /Успешно/ })).toHaveAttribute("href", /\/attempts\/1$/);
});

/** Search/filter/sort operate on a globally paginated catalog and survive reload/back navigation. */
test("catalog owner filters, sorting, search and page size persist in the URL", async ({ page, account }) => {
  const peer = await actionsPeer(page);
  peer.repositories = Array.from({ length: 45 }, (_, index) => ({ ...repository, id: 789 + index, full_name: `${index % 2 ? "personal" : "mutating"}/project-${String(index).padStart(2, "0")}`, owner_login: index % 2 ? "personal" : "mutating", owner_type: index % 2 ? "personal" : "organization" }));
  await login(page, account); await page.goto("actions");
  await expect(page.locator(".repository-card")).toHaveCount(20);
  await page.getByRole("button", { name: "Далее →", exact: true }).click();
  await expect(page).toHaveURL(/page=2/);
  const filters = page.locator(".repository-filter-panel");
  if (!await filters.evaluate(element => (element as HTMLDetailsElement).open)) await filters.locator("summary").click();
  await page.getByLabel("Тип владельца", { exact: true }).selectOption("personal");
  await expect(page).not.toHaveURL(/page=2/);
  await expect(page.locator(".repository-card")).toHaveCount(20);
  await page.getByLabel("Сортировка", { exact: true }).selectOption("name_desc");
  await expect(page.locator(".repository-identity strong").first()).toHaveText("personal/project-43");
  await page.getByLabel("На странице", { exact: true }).selectOption("50");
  await expect(page.locator(".repository-card")).toHaveCount(22);
  await page.reload();
  await expect(page.getByLabel("Тип владельца", { exact: true })).toHaveValue("personal");
  await page.getByLabel("Поиск репозитория", { exact: true }).fill("project-43");
  await expect(page.locator(".repository-card")).toHaveCount(1);
  await page.getByRole("article").getByRole("link", { name: "Подключить", exact: true }).click();
  await expect(page.getByRole("heading", { name: /personal\/project-43/ })).toBeVisible();
  await page.getByRole("link", { name: "← К репозиториям", exact: true }).click();
  await expect(page.getByLabel("Поиск репозитория", { exact: true })).toHaveValue("project-43");
});

/** One selected repository supplies its identity; the main Actions page no longer hosts a form. */
test("connecting from a repository opens an editor with all workflows and real branches", async ({ page, account }) => {
  const peer = await actionsPeer(page);
  await login(page, account); await page.goto("actions");
  await page.getByRole("link", { name: "Подключить", exact: true }).click();
  await expect(page).toHaveURL(/actions\/repositories\/789/);
  await expect(page.getByLabel("Все workflows", { exact: true })).toBeChecked();
  await expect(page.getByLabel("Ветка", { exact: true })).toHaveValue("main");
  await expect(page.getByRole("textbox", { name: "Git ref", exact: true })).toHaveCount(0);
  await expect(page.getByRole("textbox", { name: "Job ID", exact: true })).toHaveCount(0);
  await page.getByLabel("CPU", { exact: true }).fill("1");
  await page.getByLabel("RAM · GiB", { exact: true }).fill("1");
  await page.getByLabel("Диск · GiB", { exact: true }).fill("4");
  await page.getByRole("button", { name: "Подключить", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "Workflow YAML", exact: true })).toBeVisible();
  const call = peer.calls.find(row => row.method === "POST" && row.path === "github/routes");
  expect(call?.body).toMatchObject({ installation_id: 456, repository_id: 789, workflow_paths: [], ref: "refs/heads/main", cpu_millis: 1000, memory_bytes: 1024 ** 3, disk_bytes: 4 * 1024 ** 3, preparation_seconds: 300, max_parallel: 1 });
  await expect(page.getByText("Ожидает настройки workflow", { exact: true })).toBeVisible();
});

/** A deliberate workflow restriction must survive saving and only offer permitted setup snippets. */
test("workflow selection narrows the policy and job IDs come from actual workflow definitions", async ({ page, account }) => {
  const peer = await actionsPeer(page);
  await login(page, account); await page.goto(repositoryURL);
  await page.getByLabel("Все workflows", { exact: true }).uncheck();
  await expect(page.getByRole("button", { name: "Подключить", exact: true })).toBeDisabled();
  await page.getByLabel(".github/workflows/deploy.yml", { exact: true }).check();
  await page.getByRole("button", { name: "Подключить", exact: true }).click();
  await expect(page.getByLabel("Workflow для настройки", { exact: true })).toHaveValue(".github/workflows/deploy.yml");
  await expect(page.getByLabel("Задача workflow", { exact: true })).toHaveValue("deploy");
  await expect(page.getByLabel("Workflow YAML", { exact: true })).toHaveValue(/deploy:/);
  expect(peer.calls.find(row => row.method === "POST" && row.path === "github/routes")?.body?.workflow_paths).toEqual([".github/workflows/deploy.yml"]);
});

/** An unreadable first workflow must not hide the selector and prevent configuring other valid workflows. */
test("a workflow without runnable jobs does not block selecting a different workflow", async ({ page, account }) => {
  const peer = await actionsPeer(page); peer.bindings = [{ ...binding }];
  peer.workflows[0] = { ...peer.workflows[0], jobs: [], parse_error: true };
  await login(page, account); await page.goto(repositoryURL);
  const workflow = page.getByLabel("Workflow для настройки", { exact: true });
  await expect(workflow).toBeVisible();
  await workflow.selectOption(".github/workflows/deploy.yml");
  await expect(page.getByLabel("Задача workflow", { exact: true })).toHaveValue("deploy");
  await expect(page.getByLabel("Workflow YAML", { exact: true })).toHaveValue(/deploy:/);
  await workflow.selectOption(".github/workflows/ci.yml");
  await expect(workflow).toBeVisible();
  await expect(page.getByLabel("Задача workflow", { exact: true })).toBeDisabled();
  await expect(page.getByLabel("Workflow YAML", { exact: true })).toHaveCount(0);
  await workflow.selectOption(".github/workflows/deploy.yml");
  await expect(page.getByLabel("Workflow YAML", { exact: true })).toHaveValue(/deploy:/);
});

/** Branch metadata refresh must retain unrelated unsaved resource limits. */
test("changing branch and refreshing metadata preserves the resource draft", async ({ page, account }) => {
  const peer = await actionsPeer(page);
  await login(page, account); await page.goto(repositoryURL);
  await page.getByLabel("CPU", { exact: true }).fill("0.5");
  await page.getByLabel("RAM · GiB", { exact: true }).fill("1");
  await page.getByLabel("Ветка", { exact: true }).selectOption("develop");
  await expect(page.getByLabel("CPU", { exact: true })).toHaveValue("0.5");
  await page.getByRole("button", { name: "Обновить", exact: true }).click();
  await expect(page.getByLabel("RAM · GiB", { exact: true })).toHaveValue("1");
  await page.getByRole("button", { name: "Подключить", exact: true }).click();
  await expect(page.getByLabel("Workflow YAML", { exact: true })).toBeVisible();
  expect(peer.calls.find(row => row.method === "POST" && row.path === "github/routes")?.body).toMatchObject({ ref: "refs/heads/develop", cpu_millis: 500, memory_bytes: 1024 ** 3 });
});

/** Editing uses PATCH with an expected revision, without creating a new route ID. */
test("editing a binding preserves its ID and exposes a separate change history page", async ({ page, account }) => {
  const peer = await actionsPeer(page); peer.bindings = [{ ...binding }];
  await login(page, account); await page.goto(repositoryURL);
  await page.getByLabel("RAM · GiB", { exact: true }).fill("1");
  await page.getByRole("button", { name: "Сохранить настройки", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "Настройки сохранены." })).toBeVisible();
  expect(peer.bindings).toHaveLength(1);
  expect(peer.bindings[0]).toMatchObject({ id: binding.id, revision: 2, memory_bytes: 1024 ** 3 });
  expect(peer.calls.filter(row => row.method === "POST" && row.path === "github/routes")).toHaveLength(0);
  await page.getByRole("link", { name: "История изменений", exact: true }).click();
  await expect(page).toHaveURL(/\/history\?/);
  await expect(page.getByRole("heading", { name: "Настройки изменены", exact: true })).toBeVisible();
  await expect(page.locator(".binding-changes").first()).toContainText("1 GiB");
});

/** Disable/re-enable changes authority on the existing binding rather than making disabled copies. */
test("a disabled connection can be enabled again without generating another binding", async ({ page, account }) => {
  const peer = await actionsPeer(page); peer.bindings = [{ ...binding }];
  await login(page, account); await page.goto(repositoryURL);
  await page.getByRole("button", { name: "Отключить подключение", exact: true }).click();
  await expect(page.getByText("Отключён", { exact: true })).toBeVisible();
  await expect(page.getByLabel("Workflow YAML", { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "Включить подключение", exact: true }).click();
  await expect(page.getByLabel("Workflow YAML", { exact: true })).toBeVisible();
  expect(peer.bindings).toHaveLength(1);
  expect(peer.bindings[0]).toMatchObject({ id: binding.id, enabled: true, revision: 3 });
});

/** Running reservations protect their resource policy; stopping admission must remain available. */
test("active runs lock policy editing but leave disabling available", async ({ page, account }) => {
  const peer = await actionsPeer(page); peer.bindings = [{ ...binding, active_runs: true }];
  await login(page, account); await page.goto(repositoryURL);
  await expect(page.getByLabel("CPU", { exact: true })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Сохранить настройки", exact: true })).toBeDisabled();
  await expect(page.getByText(/Настройки можно изменить после завершения/)).toBeVisible();
  await page.getByRole("button", { name: "Отключить подключение", exact: true }).click();
  await expect(page.getByRole("button", { name: "Включить подключение", exact: true })).toBeDisabled();
});

/** A failed save must retain the user's draft and explain version/active-run conflicts. */
test("failed saves keep fields and allow a deliberate retry", async ({ page, account }) => {
  const peer = await actionsPeer(page); peer.bindings = [{ ...binding }];
  peer.failures.set(`PATCH github/routes/${binding.id}`, [409]);
  await login(page, account); await page.goto(repositoryURL);
  await page.getByLabel("CPU", { exact: true }).fill("1");
  await page.getByRole("button", { name: "Сохранить настройки", exact: true }).click();
  await expect(page.getByRole("alert")).toBeVisible();
  await expect(page.getByLabel("CPU", { exact: true })).toHaveValue("1");
  await page.getByRole("button", { name: "Сохранить настройки", exact: true }).click();
  await expect(page.getByText("Настройки сохранены.", { exact: true })).toBeVisible();
});

/** Losing GitHub administration blocks configuration and enable, but cannot block revoking an owned platform pool connection. */
for (const organization of [false, true]) {
  test(`pool owner can disable CI after losing GitHub admin (${organization ? "organization" : "personal"})`, async ({ page, account }) => {
    const peer = await actionsPeer(page); peer.repositories[0].can_administer = false;
    peer.bindings = [{ ...binding, company_id: organization ? "lab" : null }];
    if (organization) peer.companies = [{ id: "lab", name: "Лаборатория", my_permissions: ["execution.read", "execution.run", "resources.manage"] }];
    await login(page, account); await page.goto(repositoryURL);
    await expect(page.getByText("Для настройки подключения нужны права администратора репозитория.", { exact: true })).toBeVisible();
    await expect(page.getByLabel("CPU", { exact: true })).toBeDisabled();
    await page.getByRole("button", { name: "Отключить подключение", exact: true }).click();
    await expect.poll(() => peer.bindings[0].enabled).toBe(false);
    await expect(page.getByRole("button", { name: "Включить подключение", exact: true })).toHaveCount(0);
    await expect(page.getByLabel("CPU", { exact: true })).toBeDisabled();
    const updates = peer.calls.filter(row => row.method === "PATCH");
    expect(updates).toHaveLength(1);
    expect(updates[0].body).toEqual({ revision: 1, enabled: false });
  });
}

/** Sensitive runtime details remain optional; pool changes clear stale computer IDs. */
test("organization pools, explicit computers and preparation limits survive saving", async ({ page, account }) => {
  const peer = await actionsPeer(page);
  peer.companies = [{ id: "lab", name: "Лаборатория", my_permissions: ["execution.read", "execution.run", "resources.manage", "resources.read"] }];
  peer.computers = [{ id: "cell", company_id: "lab", name: "Mac лаборатории", runtime: "powercell-ubuntu-arm64-v1" }];
  await login(page, account); await page.goto(repositoryURL);
  await page.getByLabel("Пул компьютеров", { exact: true }).selectOption("lab");
  await page.locator(".binding-advanced > summary").click();
  await page.getByLabel("Конкретные компьютеры", { exact: true }).selectOption(["cell"]);
  await page.getByLabel("Таймаут подготовки · секунды", { exact: true }).fill("300");
  await page.getByLabel("Лимит выполнения CI · минуты", { exact: true }).fill("40");
  await page.getByRole("button", { name: "Подключить", exact: true }).click();
  await expect(page.getByLabel("Workflow YAML", { exact: true })).toBeVisible();
  expect(peer.calls.find(row => row.method === "POST" && row.path === "github/routes")?.body).toMatchObject({ company_id: "lab", computer_ids: ["cell"], preparation_seconds: 300, timeout_seconds: 2400 });
});

/** Copy denial must keep the generated YAML selectable instead of discarding setup instructions. */
test("YAML copy has a manual fallback and selecting jobs requests the right example", async ({ page, account }) => {
  const peer = await actionsPeer(page); peer.bindings = [{ ...binding }];
  await page.addInitScript(() => Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: async () => { throw new Error("denied"); } } }));
  await login(page, account); await page.goto(repositoryURL);
  await page.getByLabel("Workflow для настройки", { exact: true }).selectOption(".github/workflows/tests.yml");
  await page.getByLabel("Задача workflow", { exact: true }).selectOption("web");
  await expect(page.getByLabel("Workflow YAML", { exact: true })).toHaveValue(/web:/);
  await page.getByRole("button", { name: "Скопировать YAML", exact: true }).click();
  await expect(page.getByText("Выделен YAML. Скопируйте его вручную.", { exact: true })).toBeVisible();
  await expect(page.getByLabel("Workflow YAML", { exact: true })).toBeFocused();
});

/** Catalog/read errors must never masquerade as an empty repo list or an unavailable integration. */
for (const endpoint of ["status", "repositories"]) {
  test(`Actions automatically recovers after repeated network failures loading GitHub ${endpoint}`, async ({ page, account }) => {
    await actionsPeer(page);
    let attempts = 0;
    await page.route(url => url.pathname === `/v1/github/${endpoint}`, route => {
      if (++attempts <= 2) return route.abort("failed");
      return route.fallback();
    });
    await login(page, account);
    await page.goto("actions");
    await expect(page.locator(".repository-card")).toHaveCount(1);
    await expect(page.getByRole("alert")).toHaveCount(0);
    expect(attempts).toBeGreaterThanOrEqual(3);
  });
}

/** HTTP errors still offer explicit retry instead of being treated as a transport outage. */
test("catalog failures are retryable and disconnect removes repository data", async ({ page, account }) => {
  const peer = await actionsPeer(page); peer.failures.set("GET github/repositories", [503]);
  await login(page, account); await page.goto("actions");
  await expect(page.getByRole("alert")).toBeVisible();
  await expect(page.getByText("Репозитории не найдены", { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: /Попробовать снова/ }).click();
  await expect(page.locator(".repository-card")).toHaveCount(1);
  await page.getByRole("button", { name: "Отключить GitHub", exact: true }).click();
  await expect(page.getByRole("button", { name: "Подключить GitHub", exact: true })).toBeVisible();
  await expect(page.locator(".repository-card")).toHaveCount(0);
});

/** Filters that match no rows have an explicit reset, including on a narrow screen. */
test("empty filtered results can be reset and unsafe return URLs are ignored", async ({ page, account }) => {
  await actionsPeer(page);
  await login(page, account); await page.goto("actions?search=absent");
  await expect(page.getByText("Репозитории не найдены", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Сбросить фильтры", exact: true }).click();
  await expect(page.locator(".repository-card")).toHaveCount(1);
  await page.goto(repositoryURL + "&return=javascript%3Aalert(1)");
  await expect(page.getByRole("link", { name: "← К репозиториям", exact: true })).toHaveAttribute("href", "/actions");
});

/** A previous successful job remains visible, but cannot confirm a configuration revision that has never run. */
test("editing a confirmed connection waits for a run with its new policy version", async ({ page, account }) => {
  const peer = await actionsPeer(page); peer.bindings = [{ ...binding }];
  peer.runs = [{ id: "run", route_id: binding.id, route_revision: 1, run_id: 42, run_attempt: 1, status: "completed", reason: "github_success", started_at: "2026-10-08T12:00:00Z", created_at: "2026-10-08T12:00:00Z", url: "https://github.com/mutating/ci-sandbox/actions/runs/42" }];
  await login(page, account); await page.goto(repositoryURL);
  await expect(page.getByText("Подключён", { exact: true })).toBeVisible();
  await page.getByLabel("CPU", { exact: true }).fill("1");
  await page.getByRole("button", { name: "Сохранить настройки", exact: true }).click();
  await expect(page.getByText("Ожидает настройки workflow", { exact: true })).toBeVisible();
  await expect(page.locator(".repository-runs")).toContainText("Успешно");
});
