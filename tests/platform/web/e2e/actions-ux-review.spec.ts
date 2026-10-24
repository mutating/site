import { test, expect, login } from "./fixtures";
import { actionsPeer, binding, repositoryURL } from "./actions-peer";

/** Clearing/switching pools must drop foreign IDs; inspecting the new pool requires inventory read access. */
test("Actions clears selected computers explicitly and when the pool changes", async ({ page, account }) => {
  const peer = await actionsPeer(page);
  peer.companies = [{ id: "lab", name: "Lab", my_permissions: ["execution.read", "execution.run", "resources.read", "resources.manage"] }];
  peer.computers = [{ id: "personal-mac", name: "Personal Mac", runtime: "powercell-ubuntu-arm64-v1" }, { id: "lab-mac", company_id: "lab", name: "Lab Mac", runtime: "powercell-ubuntu-arm64-v1" }];
  await login(page, account); await page.goto(repositoryURL);
  await page.locator(".binding-advanced > summary").click();
  const machines = page.getByLabel("Конкретные компьютеры", { exact: true });
  await machines.selectOption(["personal-mac"]);
  await page.getByRole("button", { name: "Использовать любой компьютер", exact: true }).click();
  await expect(machines).toHaveValues([]);
  await machines.selectOption(["personal-mac"]);
  await page.getByLabel("Пул компьютеров", { exact: true }).selectOption("lab");
  await expect(machines).toHaveValues([]);
  await expect(machines.locator("option")).toHaveText(["Lab Mac"]);
  await page.getByRole("button", { name: "Подключить", exact: true }).click();
  await expect(page.getByLabel("Workflow YAML", { exact: true })).toBeVisible();
  expect(peer.bindings[0]).toMatchObject({ company_id: "lab", computer_ids: [] });
});

/** A known empty pool is valid, but a failed inventory request cannot silently become "any computer". */
test("Actions distinguishes empty inventory from an inventory failure", async ({ page, account }) => {
  const peer = await actionsPeer(page); peer.failures.set("GET computers", [503]);
  await login(page, account); await page.goto(repositoryURL);
  await page.locator(".binding-advanced > summary").click();
  await expect(page.getByRole("button", { name: "Подключить", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: /Попробовать снова/ }).click();
  await expect(page.getByRole("button", { name: "Подключить", exact: true })).toBeEnabled();
  expect(peer.calls.filter(row => row.method === "POST" && row.path === "github/routes")).toHaveLength(0);
  await expect(page.getByLabel("Конкретные компьютеры", { exact: true })).toBeDisabled();
  await expect(page.getByText(/В пуле пока нет Power Cells/)).toBeVisible();
});

/** POST errors preserve the draft and must not manufacture an active connection or hide the error on refresh. */
test("Actions preserves a rejected creation and retries without losing entered limits", async ({ page, account }) => {
  const peer = await actionsPeer(page); peer.failures.set("POST github/routes", [422]);
  await login(page, account); await page.goto(repositoryURL);
  await page.getByLabel("CPU", { exact: true }).fill("1");
  await page.getByRole("button", { name: "Подключить", exact: true }).click();
  await expect(page.getByRole("alert")).toBeVisible();
  await page.getByRole("button", { name: "Обновить", exact: true }).click();
  await expect(page.getByLabel("CPU", { exact: true })).toHaveValue("1");
  await expect(page.getByRole("alert")).toBeVisible();
  expect(peer.bindings).toHaveLength(0);
  await page.getByRole("button", { name: "Подключить", exact: true }).click();
  await expect(page.getByLabel("Workflow YAML", { exact: true })).toBeVisible();
  expect(peer.bindings).toHaveLength(1);
});

/** Synchronous duplicate submit events and slow responses cannot create duplicate repository connections. */
test("Actions ignores duplicate create events while a request is pending", async ({ page, account }) => {
  const peer = await actionsPeer(page);
  let release!: () => void; const gate = new Promise<void>(resolve => release = resolve);
  peer.custom = async (route, url) => {
    if (url.pathname !== "/v1/github/routes" || route.request().method() !== "POST") return false;
    await gate;
    const row = { ...binding, ...route.request().postDataJSON() }; peer.bindings.push(row);
    await route.fulfill({ status: 201, json: row }); return true;
  };
  await login(page, account); await page.goto(repositoryURL);
  await expect(page.getByRole("button", { name: "Подключить", exact: true })).toBeEnabled();
  await page.locator(".repository-binding-form").evaluate(form => { (form as HTMLFormElement).requestSubmit(); (form as HTMLFormElement).requestSubmit(); });
  await expect.poll(() => peer.calls.filter(row => row.method === "POST" && row.path === "github/routes").length).toBe(1);
  await expect(page.getByRole("button", { name: "Сохранение…", exact: true })).toHaveCount(0);
  release(); await expect(page.getByLabel("Workflow YAML", { exact: true })).toBeVisible();
  expect(peer.bindings).toHaveLength(1);
});

/** A saved record remains usable immediately while the confirmation GET is still pending. */
test("Actions displays a newly saved connection during metadata confirmation", async ({ page, account }) => {
  const peer = await actionsPeer(page);
  await login(page, account); await page.goto(repositoryURL);
  await expect(page.getByRole("button", { name: "Подключить", exact: true })).toBeEnabled();
  let release!: () => void; const gate = new Promise<void>(resolve => release = resolve);
  peer.custom = async (route, url) => {
    if (!url.pathname.endsWith("/configuration")) return false;
    await gate; await route.fulfill({ json: { repository: peer.repositories[0], branches: peer.branches, workflows: peer.workflows, state: "pending", bindings: peer.bindings, binding_id: binding.id, last_run: null } }); return true;
  };
  await page.getByRole("button", { name: "Подключить", exact: true }).click();
  await expect(page.getByLabel("Workflow YAML", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Настройка YAML в GitHub", exact: true })).toBeFocused();
  await expect(page.getByRole("button", { name: "Сохранить настройки", exact: true })).toBeDisabled();
  release(); await expect(page.getByRole("button", { name: "Сохранить настройки", exact: true })).toBeEnabled();
});

/** YAML and execution history have independent error/retry state; one must not hide the other. */
test("Actions loads run history independently of a failed YAML request", async ({ page, account }) => {
  const peer = await actionsPeer(page); peer.bindings = [{ ...binding }];
  peer.runs = [{ id: "run", run_id: 42, run_attempt: 2, status: "failed", reason: "no_capacity", url: "https://github.com/mutating/ci-sandbox/actions/runs/42/attempts/2" }];
  peer.failures.set(`GET github/routes/${binding.id}/workflow`, [503]);
  await login(page, account); await page.goto(repositoryURL);
  await expect(page.locator(".repository-runs").getByRole("link", { name: "#42 / 2" })).toBeVisible();
  await expect(page.locator(".repository-runs")).toContainText("Нет подходящих свободных компьютеров");
  await page.locator(".repository-yaml").getByRole("button", { name: /Попробовать снова/ }).click();
  await expect(page.getByLabel("Workflow YAML", { exact: true })).toBeVisible();
  peer.failures.set(`GET github/routes/${binding.id}/runs`, [503]);
  await page.getByRole("button", { name: "Обновить", exact: true }).click();
  await expect(page.locator(".repository-runs").getByRole("alert")).toBeVisible();
  await expect(page.getByLabel("Workflow YAML", { exact: true })).toBeVisible();
  await page.locator(".repository-runs").getByRole("button", { name: /Попробовать снова/ }).click();
  await expect(page.locator(".repository-runs").getByRole("alert")).toHaveCount(0);
});

/** Inactive bindings retain their editable policy and audit trail, but must not offer active setup YAML. */
test("Actions keeps disabled connection settings and change history accessible", async ({ page, account }) => {
  const peer = await actionsPeer(page); peer.bindings = [{ ...binding }];
  await login(page, account); await page.goto(repositoryURL);
  peer.failures.set(`PATCH github/routes/${binding.id}`, [503]);
  await page.getByRole("button", { name: "Отключить подключение", exact: true }).click();
  await expect(page.getByRole("alert")).toBeVisible();
  await expect(page.getByLabel("Workflow YAML", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Отключить подключение", exact: true }).click();
  await expect(page.getByRole("button", { name: "Включить подключение", exact: true })).toBeVisible();
  await expect(page.getByLabel("Workflow YAML", { exact: true })).toHaveCount(0);
  await page.getByRole("link", { name: "История изменений", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Подключение отключено", exact: true })).toBeVisible();
  await expect(page.locator(".binding-revision").first()).toContainText("Алиса Волкова");
});

/** Refresh must update inventory without silently discarding selected machines that have left the pool. */
test("Actions refresh marks removed selected computers and requires an explicit pool choice", async ({ page, account }) => {
  const peer = await actionsPeer(page);
  peer.computers = [{ id: "old-mac", name: "Mac для CI", runtime: "powercell-ubuntu-arm64-v1" }];
  peer.bindings = [{ ...binding, computer_ids: ["old-mac"] }];
  await login(page, account); await page.goto(repositoryURL);
  await page.locator(".binding-advanced > summary").click();
  await expect(page.getByLabel("Конкретные компьютеры", { exact: true }).locator("option")).toHaveText(["Mac для CI"]);
  peer.computers = [];
  await page.getByRole("button", { name: "Обновить", exact: true }).click();
  await expect(page.getByText(/Некоторые выбранные компьютеры больше не доступны/)).toBeVisible();
  await expect(page.getByLabel("Конкретные компьютеры", { exact: true }).locator("option")).toHaveText(["old-mac · Недоступен"]);
  await expect(page.getByRole("button", { name: "Сохранить настройки", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "Использовать любой компьютер", exact: true }).click();
  await expect(page.getByRole("button", { name: "Сохранить настройки", exact: true })).toBeEnabled();
});
