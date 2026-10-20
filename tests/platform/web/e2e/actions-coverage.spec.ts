import { test, expect, login } from "./fixtures";
import { actionsPeer, binding, installation, repository } from "./actions-peer";

// Assertions: a status outage hides connection controls; retry loads the real state.
test("Actions recovers a status failure without pretending the integration is disabled", async ({ page, account }) => {
  const peer = await actionsPeer(page);
  await login(page, account);
  peer.failures.set("GET github/status", [503]);
  await page.goto("/actions");
  await expect(page.getByRole("alert")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Подключение GitHub пока недоступно" })).toHaveCount(0);
  await page.getByRole("button", { name: /Попробовать снова/ }).click();
  await expect(page.getByRole("heading", { name: "@github-user" })).toBeVisible();
});

// Assertions: connect/reconnect and repository installation follow only the returned GitHub URL.
for (const action of ["connect", "reconnect", "install"] as const) {
  test(`Actions ${action} follows the GitHub authorization response`, async ({ page, account }) => {
    const peer = await actionsPeer(page);
    peer.connected = action !== "connect";
    await page.route("https://github.com/**", (route) => route.fulfill({ contentType: "text/html", body: "GitHub authorization peer" }));
    await login(page, account);
    await page.goto("/actions");
    const label = action === "connect" ? "Подключить GitHub" : action === "reconnect" ? "Повторно подключить" : "Выбрать репозитории";
    await page.getByRole("button", { name: label, exact: true }).click();
    await expect(page).toHaveURL("https://github.com/login/oauth/authorize?state=test-state");
    expect(peer.calls.filter((call) => call.method === "POST").map((call) => call.path)).toEqual([action === "install" ? "github/installations/authorize" : "github/authorize"]);
  });
}

// Assertions: provider errors preserve the page, release busy controls and allow another attempt.
test("Actions retries authorization after a provider failure", async ({ page, account }) => {
  const peer = await actionsPeer(page); peer.connected = false;
  peer.failures.set("POST github/authorize", [503, 503]);
  await login(page, account); await page.goto("/actions");
  const connect = page.getByRole("button", { name: "Подключить GitHub", exact: true });
  await connect.click();
  await expect(page.getByRole("alert")).toBeVisible(); await expect(connect).toBeEnabled();
  await connect.click();
  await expect.poll(() => peer.calls.filter((call) => call.path === "github/authorize").length).toBe(2);
  await expect(page).toHaveURL(/\/actions$/);
});

// Assertions: failed disconnect retains the connection and repositories; a retry clears both.
test("Actions preserves the connected account when disconnect fails", async ({ page, account }) => {
  const peer = await actionsPeer(page); peer.failures.set("DELETE github/connection", [503]);
  await login(page, account); await page.goto("/actions");
  const disconnect = page.getByRole("button", { name: "Отключить GitHub", exact: true });
  await disconnect.click();
  await expect(page.getByRole("alert")).toBeVisible();
  await expect(page.getByRole("heading", { name: "@github-user" })).toBeVisible();
  await expect(page.getByRole("link", { name: repository.full_name, exact: true })).toBeVisible();
  await disconnect.click();
  await expect(page.getByRole("heading", { name: "Подключить GitHub", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: repository.full_name, exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Создать привязку", exact: true })).toHaveCount(0);
});

// Assertions: disconnect explains its effect on CI before the action; reconnect restores discovery,
// but revoked bindings remain disabled and require a new binding and updated YAML.
test("Actions explains CI revocation before disconnect and preserves it after reconnect", async ({ page, account }) => {
  const peer = await actionsPeer(page); peer.bindings = [{ ...binding }];
  await page.route("https://github.com/login/oauth/authorize?state=test-state", async (route) => {
    peer.connected = true;
    await route.fulfill({ contentType: "text/html", body: '<a href="http://127.0.0.1:18101/actions?github=connected">Return to platform</a>' });
  });
  await login(page, account); await page.goto("/actions");
  await expect(page.locator(".actions-connection")).toContainText("Отключение GitHub отключит созданные вами CI-привязки");
  await expect(page.locator(".actions-connection")).toContainText("создайте привязки заново и обновите YAML");
  await page.getByRole("button", { name: "Отключить GitHub", exact: true }).click();
  await expect(page.getByRole("button", { name: "Подключить GitHub", exact: true })).toBeVisible();
  expect(peer.bindings[0].enabled).toBe(false);
  await page.getByRole("button", { name: "Подключить GitHub", exact: true }).click();
  await page.getByRole("link", { name: "Return to platform", exact: true }).click();
  await expect(page.getByRole("heading", { name: "@github-user", exact: true })).toBeVisible();
  await expect(page.getByText("Отключена", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Отключить привязку", exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "Workflow и запуски", exact: true }).click();
  await expect(page.locator(".actions-ci-detail").getByRole("status")).toContainText("Эта привязка отключена");
  await page.getByText("Новая привязка", { exact: true }).click();
  await expect(page.getByRole("button", { name: "Создать привязку", exact: true })).toBeVisible();
});

// Assertions: an empty filtered installation page keeps pagination usable; changing pages resets selection.
test("Actions reaches its installation after an empty filtered page", async ({ page, account }) => {
  const peer = await actionsPeer(page);
  peer.custom = async (route, url) => {
    if (url.pathname !== "/v1/github/installations") return false;
    await route.fulfill({ json: { total_count: 101, installations: url.searchParams.get("page") === "2" ? [installation] : [] } }); return true;
  };
  await login(page, account); await page.goto("/actions");
  await expect(page.getByRole("button", { name: "Назад", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "Далее", exact: true }).click();
  await expect(page.getByRole("link", { name: repository.full_name, exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Далее", exact: true })).toBeDisabled();
});

// Assertions: zero repositories on a page is not the end of the list; next and previous remain usable.
test("Actions paginates repositories even when the current page is empty", async ({ page, account }) => {
  const peer = await actionsPeer(page);
  peer.custom = async (route, url) => {
    if (!url.pathname.endsWith("/repositories")) return false;
    await route.fulfill({ json: { total_count: 101, repositories: url.searchParams.get("page") === "2" ? [repository] : [] } }); return true;
  };
  await login(page, account); await page.goto("/actions");
  await page.getByRole("button", { name: "Далее", exact: true }).click();
  await expect(page.getByRole("link", { name: repository.full_name, exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Далее", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "Назад", exact: true }).click();
  await expect(page.getByRole("link", { name: repository.full_name, exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Далее", exact: true })).toBeEnabled();
});

// Assertions: suspended installations explain how to recover, are unavailable and
// never trigger repository or create requests or misleading access-grant instructions.
test("Actions cannot configure a suspended installation", async ({ page, account }) => {
  const peer = await actionsPeer(page); peer.installations = [{ ...installation, suspended: true }];
  await login(page, account); await page.goto("/actions");
  await expect(page.getByRole("option", { name: "mutating (приостановлено)", exact: true })).toHaveJSProperty("disabled", true);
  await expect(page.getByText(/Установки приложения приостановлены в GitHub/)).toBeVisible();
  await expect(page.getByText(/Предоставьте приложению доступ/)).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Создать привязку", exact: true })).toHaveCount(0);
  expect(peer.calls.some((call) => call.path.endsWith("/repositories"))).toBe(false);
});

// Assertions: the compatibility text explains supported events and repository-scoped runner trust.
// Assertions: a repository is still visible with read access, but cannot be used to create a binding.
test("Actions shows a public read-only repository without CI creation controls", async ({ page, account }) => {
  const peer = await actionsPeer(page); peer.repositories = [{ ...repository, private: false, can_administer: false }];
  await login(page, account); await page.goto("/actions");
  await expect(page.getByRole("link", { name: repository.full_name, exact: true })).toHaveAttribute("href", "https://github.com/mutating/ci-sandbox");
  await expect(page.getByText("Публичный", { exact: true })).toBeVisible();
  await expect(page.getByText(/Пул доверяет всем авторам jobs репозитория.*метки runner не изолируют/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Создать привязку", exact: true })).toHaveCount(0);
});

// Assertions: installation and repository outages are recoverable through the adjacent retry control.
for (const endpoint of ["github/installations", "github/installations/456/repositories"]) {
  test(`Actions retries discovery at ${endpoint}`, async ({ page, account }) => {
    const peer = await actionsPeer(page); peer.failures.set(`GET ${endpoint}`, [503]);
    await login(page, account); await page.goto("/actions");
    await expect(page.getByRole("alert")).toBeVisible();
    await page.getByRole("button", { name: /Попробовать снова/ }).click();
    await expect(page.getByRole("link", { name: repository.full_name, exact: true })).toBeVisible();
    await expect(page.getByRole("alert")).toHaveCount(0);
  });
}

// Assertions: route-list failure must not claim the list is empty; retry restores stored routes.
test("Actions retries loading bindings without showing a false empty state", async ({ page, account }) => {
  const peer = await actionsPeer(page); peer.bindings = [{ ...binding }]; peer.failures.set("GET github/routes", [503]);
  await login(page, account); await page.goto("/actions");
  await expect(page.getByRole("alert")).toBeVisible();
  await expect(page.getByText("Привязок пока нет.", { exact: false })).toHaveCount(0);
  await page.getByRole("button", { name: /Попробовать снова/ }).click();
  await expect(page.getByRole("button", { name: "Workflow и запуски", exact: true })).toBeVisible();
});

// Assertions: pools require manage/run plus read for subsequent workflow/history; computers follow the pool;
// changing pools clears machine selection; resource units and every policy field survive submission.
test("Actions creates an organization binding with selected Power Cells and explicit limits", async ({ page, account }) => {
  const peer = await actionsPeer(page);
  const company = "22222222-2222-4222-8222-222222222222";
  const personalMac = "33333333-3333-4333-8333-333333333333";
  const organizationMac = "44444444-4444-4444-8444-444444444444";
  peer.companies = [
    { id: company, name: "CI team", my_permissions: ["resources.manage", "execution.run", "execution.read"] },
    { id: "manage-only", name: "Manage only", my_permissions: ["resources.manage"] },
    { id: "no-history", name: "Cannot read workflow", my_permissions: ["resources.manage", "execution.run"] },
    { id: "execute-only", name: "Execute only", my_permissions: ["execution.run"] },
  ];
  peer.custom = async (route, url) => {
    if (url.pathname !== "/v1/computers") return false;
    const organization = url.searchParams.get("company_id") === company;
    await route.fulfill({ json: [
      { id: organization ? organizationMac : personalMac, name: organization ? "Team Mac" : "Personal Mac", runtime: "powercell-ubuntu-arm64-v1" },
      { id: "not-a-powercell", name: "OCI Linux", runtime: "oci" },
    ] }); return true;
  };
  await login(page, account); await page.goto("/actions");
  const pool = page.getByRole("combobox", { name: "Пул компьютеров", exact: true });
  const computers = page.getByRole("listbox", { name: /^Конкретные компьютеры/ });
  await expect(pool.locator("option")).toHaveCount(2);
  await expect(computers.getByRole("option", { name: "Personal Mac", exact: true })).toBeVisible();
  await expect(computers.getByRole("option", { name: "OCI Linux", exact: true })).toHaveCount(0);
  await computers.selectOption(personalMac);
  await pool.selectOption(company);
  await expect(computers.getByRole("option", { name: "Team Mac", exact: true })).toBeVisible();
  await expect(computers).toHaveValues([]);
  await computers.selectOption(organizationMac);
  await page.getByRole("spinbutton", { name: "CPU", exact: true }).fill("1.5");
  await page.getByRole("spinbutton", { name: "RAM · GiB", exact: true }).fill("2.5");
  await page.getByRole("spinbutton", { name: "Диск · GiB", exact: true }).fill("4");
  await page.getByRole("spinbutton", { name: "Одновременных jobs", exact: true }).fill("3");
  await page.getByRole("spinbutton", { name: "Подготовка выделенной Power Cell · секунды", exact: true }).fill("30");
  await page.getByRole("button", { name: "Создать привязку", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "Workflow YAML", exact: true })).toBeVisible();
  expect(peer.calls.find((call) => call.path === "github/routes" && call.method === "POST")?.body).toMatchObject({
    company_id: company, computer_ids: [organizationMac], cpu_millis: 1500, memory_bytes: 2.5 * 1024 ** 3,
    disk_bytes: 4 * 1024 ** 3, max_parallel: 3, preparation_seconds: 30,
  });
  await expect(page.getByText(/1.5 CPU · 2.5 GiB RAM · CI team/)).toBeVisible();
});

// Assertions: invalid required policy values are blocked by the browser before any allocation request.
test("Actions validates workflow and resource fields before creating a binding", async ({ page, account }) => {
  const peer = await actionsPeer(page);
  await login(page, account); await page.goto("/actions");
  const create = page.getByRole("button", { name: "Создать привязку", exact: true });
  await expect(create).toBeVisible();
  for (const [name, invalid, valid] of [
    ["workflow", "../../evil.yml", ".github/workflows/ci.yml"], ["job", "two words", "checks"],
    ["cpu", "0", "1"], ["memory", "0", "2"], ["disk", "0", "4"], ["disk", "1025", "4"], ["wait", "9", "30"], ["parallel", "0", "1"],
  ]) {
    const field = page.locator(`.actions-ci-form [name="${name}"]`);
    await field.fill(invalid); await create.click();
    expect(await field.evaluate((element) => (element as HTMLInputElement).validity.valid)).toBe(false);
    expect(peer.calls.filter((call) => call.path === "github/routes" && call.method === "POST")).toHaveLength(0);
    await field.fill(valid);
  }
});

// Assertions: rejected creation preserves user inputs, creates no phantom binding and permits a corrected retry.
test("Actions retains binding inputs after the API rejects creation", async ({ page, account }) => {
  const peer = await actionsPeer(page); peer.failures.set("POST github/routes", [422]);
  await login(page, account); await page.goto("/actions");
  const job = page.getByRole("textbox", { name: "Job ID", exact: true }); await job.fill("checks");
  const create = page.getByRole("button", { name: "Создать привязку", exact: true });
  await create.click(); await expect(page.getByRole("alert")).toBeVisible();
  await expect(job).toHaveValue("checks"); await expect(create).toBeEnabled();
  await expect(page.getByRole("button", { name: "Workflow и запуски", exact: true })).toHaveCount(0);
  await create.click();
  await expect(page.getByRole("textbox", { name: "Workflow YAML", exact: true })).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
  expect(peer.bindings).toHaveLength(1);
});

// Assertions: an in-flight create cannot be submitted twice, and the single response selects the saved binding.
test("Actions prevents duplicate binding submissions while saving", async ({ page, account }) => {
  const peer = await actionsPeer(page);
  let release!: () => void; const gate = new Promise<void>((resolve) => { release = resolve; });
  peer.custom = async (route, url) => {
    if (url.pathname !== "/v1/github/routes" || route.request().method() !== "POST") return false;
    await gate; const saved = { ...binding, ...route.request().postDataJSON() }; peer.bindings.push(saved);
    await route.fulfill({ status: 201, json: saved }); return true;
  };
  await login(page, account); await page.goto("/actions");
  const create = page.getByRole("button", { name: "Создать привязку", exact: true });
  await create.click(); await expect(create).toBeDisabled();
  expect(peer.calls.filter((call) => call.path === "github/routes" && call.method === "POST")).toHaveLength(1);
  release(); await expect(page.getByRole("textbox", { name: "Workflow YAML", exact: true })).toBeVisible();
  expect(peer.bindings).toHaveLength(1);
});

// Assertions: failed disable does not change the visible state; successful retry removes only the disable control.
test("Actions disables an existing binding only after the API accepts it", async ({ page, account }) => {
  const peer = await actionsPeer(page); peer.bindings = [{ ...binding }]; peer.failures.set(`DELETE github/routes/${binding.id}`, [503]);
  await login(page, account); await page.goto("/actions");
  const disable = page.getByRole("button", { name: "Отключить привязку", exact: true });
  await disable.click(); await expect(page.getByRole("alert")).toBeVisible(); await expect(disable).toBeEnabled();
  expect(peer.bindings[0].enabled).toBe(true);
  await disable.click(); await expect(disable).toHaveCount(0);
  await expect(page.getByText(/Отключена/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Workflow и запуски", exact: true })).toBeVisible();
});

// Assertions: an acknowledged disable stays visible even if the subsequent list GET fails;
// only that binding changes, its selected details agree, and refresh recovers without another DELETE.
test("Actions preserves an acknowledged disable when list refresh fails", async ({ page, account }) => {
  const peer = await actionsPeer(page);
  peer.bindings = [{ ...binding }, { ...binding, id: "22222222-2222-4222-8222-222222222222", job_key: "checks" }];
  await login(page, account); await page.goto("/actions");
  const cards = page.locator("article").filter({ has: page.getByRole("button", { name: "Workflow и запуски", exact: true }) });
  const card = cards.filter({ hasText: " · test · " });
  const other = cards.filter({ hasText: " · checks · " });
  await card.getByRole("button", { name: "Workflow и запуски", exact: true }).click();
  const detail = page.locator(".actions-ci-detail");
  await expect(detail.getByRole("textbox", { name: "Workflow YAML", exact: true })).toBeVisible();
  peer.failures.set("GET github/routes", [503]);
  await card.getByRole("button", { name: "Отключить привязку", exact: true }).click();
  await expect(page.getByRole("alert")).toBeVisible();
  expect(peer.bindings[0].enabled).toBe(false);
  await expect(card).toContainText("Отключена");
  await expect(card.getByRole("button", { name: "Отключить привязку", exact: true })).toHaveCount(0);
  await expect(detail.getByRole("status").filter({ hasText: "Эта привязка отключена" })).toBeVisible();
  await expect(detail).not.toContainText("Примените YAML в репозитории");
  await expect(other.getByRole("button", { name: "Отключить привязку", exact: true })).toBeEnabled();
  expect(peer.bindings[1].enabled).toBe(true);
  await detail.getByRole("button", { name: "Обновить", exact: true }).click();
  await expect(page.getByRole("alert")).toHaveCount(0);
  await expect(card).toContainText("Отключена");
  await expect(other.getByRole("button", { name: "Отключить привязку", exact: true })).toBeEnabled();
  expect(peer.calls.filter((call) => call.method === "DELETE")).toHaveLength(1);
});

// Assertions: workflow/history errors are not rendered as empty history; refresh retries both requests.
// Integration instructions require upstream needs and approval/concurrency gates before routing.
test("Actions refreshes failed workflow and history requests together", async ({ page, account }) => {
  const peer = await actionsPeer(page); peer.bindings = [{ ...binding }];
  peer.failures.set(`GET github/routes/${binding.id}/workflow`, [503]);
  peer.failures.set(`GET github/routes/${binding.id}/runs`, [503]);
  await login(page, account); await page.goto("/actions");
  await page.getByRole("button", { name: "Workflow и запуски", exact: true }).click();
  await expect(page.locator(".actions-ci-detail").getByRole("alert")).toBeVisible();
  await expect(page.getByText("Запусков по этой привязке пока нет.", { exact: true })).toHaveCount(0);
  await page.locator(".actions-ci-detail").getByRole("button", { name: "Обновить", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "Workflow YAML", exact: true })).toBeVisible();
  await expect(page.getByText(/Скопируйте в него все исходные зависимости needs/)).toBeVisible();
  await expect(page.getByText(/Согласования environment.*до выбора runner.*5 минут/)).toBeVisible();
  await expect(page.getByText("Запусков по этой привязке пока нет.", { exact: true })).toBeVisible();
  await expect(page.locator(".actions-ci-detail").getByRole("alert")).toHaveCount(0);
});

// Assertions: every lifecycle state has a readable label; unknown provider reasons stay out of UI; links retain attempts.
test("Actions renders lifecycle history and safe refusal reasons", async ({ page, account }) => {
  const peer = await actionsPeer(page); peer.bindings = [{ ...binding }];
  const states = ["preparing", "ready", "selected", "running", "completed", "cancelled", "timed_out", "failed", "failed", "future-status"];
  peer.runs = states.map((status, index) => ({ id: String(index), run_id: 42 + index, run_attempt: 2, status,
    reason: index === 7 ? "no_capacity" : status === "failed" ? "private-internal-provider-detail" : null,
    url: `https://github.com/mutating/ci-sandbox/actions/runs/${42 + index}/attempts/2` }));
  await login(page, account); await page.goto("/actions");
  await page.getByRole("button", { name: "Workflow и запуски", exact: true }).click();
  const detail = page.locator(".actions-ci-detail");
  for (const label of ["Подготовка Power Cell", "Выбран Power Cell", "Выполняется", "Завершён", "Отменён", "Истёк срок выполнения", "Ошибка", "Подробности в GitHub", "Нет подходящих свободных компьютеров"])
    await expect(detail.getByText(label, { exact: false }).first()).toBeVisible();
  await expect(detail.getByText("private-internal-provider-detail", { exact: false })).toHaveCount(0);
  await expect(detail.getByRole("link", { name: "#42 / 2", exact: true })).toHaveAttribute("href", "https://github.com/mutating/ci-sandbox/actions/runs/42/attempts/2");
});

// Assertions: switching bindings replaces both workflow and history, even if a previous response arrives late.
test("Actions ignores late workflow responses after another binding is selected", async ({ page, account }) => {
  const peer = await actionsPeer(page); const second = { ...binding, id: "55555555-5555-4555-8555-555555555555", job_key: "other" };
  peer.bindings = [{ ...binding }, second];
  let release!: () => void; const gate = new Promise<void>((resolve) => { release = resolve; });
  peer.custom = async (route, url) => {
    if (url.pathname !== `/v1/github/routes/${binding.id}/workflow`) return false;
    await gate;
    await route.fulfill({ json: { yaml: "stale-first-workflow" } }).catch(() => {}); return true;
  };
  await login(page, account); await page.goto("/actions");
  await page.getByRole("button", { name: "Workflow и запуски", exact: true }).nth(0).click();
  await expect.poll(() => peer.calls.some((call) => call.path === `github/routes/${binding.id}/workflow`)).toBe(true);
  await page.getByRole("button", { name: "Workflow и запуски", exact: true }).nth(1).click();
  const workflow = page.getByRole("textbox", { name: "Workflow YAML", exact: true });
  await expect(workflow).toHaveValue(new RegExp(second.id));
  release(); await expect(workflow).toHaveValue(new RegExp(second.id));
});

// Assertions: choosing a new owner resets repository pagination; the previous
// owner's repository cannot remain in the create form or be submitted accidentally.
test("Actions resets repository pagination when the GitHub owner changes", async ({ page, account }) => {
  const peer = await actionsPeer(page);
  peer.installations = [installation, { ...installation, id: 457, login: "other-team" }];
  peer.custom = async (route, url) => {
    if (!url.pathname.endsWith("/repositories")) return false;
    const other = url.pathname.includes("/457/");
    await route.fulfill({ json: { total_count: other ? 1 : 101, repositories: [other ? { ...repository, id: 790, full_name: "other-team/new-ci" } : repository] } }); return true;
  };
  await login(page, account); await page.goto("/actions");
  await page.getByRole("button", { name: "Далее", exact: true }).click();
  await expect(page.getByRole("button", { name: "Далее", exact: true })).toBeDisabled();
  await page.getByRole("combobox", { name: "Аккаунт или организация GitHub", exact: true }).selectOption("457");
  await expect(page.getByRole("link", { name: "other-team/new-ci", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: repository.full_name, exact: true })).toHaveCount(0);
  await expect(page.getByRole("combobox", { name: "Репозитории", exact: true })).toHaveValue("790");
  const requests = peer.calls.filter((call) => call.path === "github/installations/457/repositories");
  expect(requests.length).toBeGreaterThan(0);
  expect(requests.every((call) => call.query.get("page") === "1")).toBe(true);
});

// Assertions: localization applies to create controls and recorded CI states/reasons;
// small viewports preserve controls and do not acquire horizontal page overflow.
test("Actions remains usable in English at a narrow viewport", async ({ page, account }) => {
  const peer = await actionsPeer(page); peer.bindings = [{ ...binding }];
  peer.runs = [{ id: "r", run_id: 42, run_attempt: 1, status: "failed", reason: "no_capacity", url: "https://github.com/mutating/ci-sandbox/actions/runs/42" }];
  await page.setViewportSize({ width: 390, height: 844 });
  await login(page, account); await page.goto("/actions");
  await page.getByRole("button", { name: "Выбрать язык", exact: true }).click();
  await page.locator(".actions-ci-create summary").click();
  await expect(page.getByRole("button", { name: "Create binding", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Workflow and runs", exact: true }).click();
  await expect(page.getByText("No suitable computers available", { exact: false })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

// Assertions: only allowlisted OAuth outcomes become local messages; cancellation
// preserves the existing connection and arbitrary provider/query text is never reflected.
for (const [result, message] of [
  ["cancelled", "Подключение GitHub отменено."],
  ["authorization_failed", "GitHub не завершил авторизацию. Попробуйте подключиться снова."],
  ["untrusted-provider-description", ""],
]) {
  test(`Actions displays a safe OAuth return status: ${result}`, async ({ page, account }) => {
    await actionsPeer(page);
    await login(page, account); await page.goto(`/actions?github=${result}&error_description=private-provider-description`);
    await expect(page.getByRole("heading", { name: "@github-user" })).toBeVisible();
    if (message) await expect(page.getByText(message, { exact: true })).toBeVisible();
    await expect(page.getByText("private-provider-description", { exact: false })).toHaveCount(0);
    await expect(page.getByText("untrusted-provider-description", { exact: false })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Повторно подключить", exact: true })).toBeEnabled();
  });
}
