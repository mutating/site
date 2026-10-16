import { test, expect, login, API } from "./fixtures";

test("Actions reports an unconfigured GitHub integration without exposing connection controls", async ({ page, account }) => {
  await login(page, account);
  await expect(page.getByRole("navigation").getByRole("link", { name: "Actions", exact: true })).toHaveCount(0);
  await page.goto("/actions");
  await expect(page.getByRole("heading", { name: "Подключение GitHub пока недоступно" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Подключить GitHub", exact: true })).toHaveCount(0);
});

test("Actions shows the selected private repository and disconnects the linked account", async ({ page, account }) => {
  let connected = true;
  const calls: string[] = [];
  await page.route(`${API}/github/**`, async (route) => {
    const path = new URL(route.request().url()).pathname;
    calls.push(`${route.request().method()} ${path}`);
    const headers = { "Access-Control-Allow-Origin": "http://127.0.0.1:18101", "Access-Control-Allow-Credentials": "true" };
    if (path.endsWith("/status")) {
      await route.fulfill({ json: { enabled: true, account: connected ? { id: 123, login: "github-user" } : null }, headers });
    } else if (path.endsWith("/repositories")) {
      await route.fulfill({ json: { total_count: 1, repositories: [{ id: 789, full_name: "mutating/powercell-ci-sandbox", private: true, default_branch: "main", can_administer: true }] }, headers });
    } else if (path.endsWith("/installations")) {
      await route.fulfill({ json: { total_count: 1, installations: [{ id: 456, login: "mutating", repository_selection: "selected", suspended: false }] }, headers });
    } else if (path.endsWith("/routes")) {
      await route.fulfill({ json: [], headers });
    } else if (path.endsWith("/connection")) {
      connected = false;
      await route.fulfill({ status: 204, headers });
    } else {
      throw new Error(`Unexpected GitHub UI request ${path}`);
    }
  });
  await login(page, account);
  await page.getByRole("navigation").getByRole("link", { name: "Actions", exact: true }).click();
  await expect(page.getByRole("heading", { name: "@github-user" })).toBeVisible();
  await expect(page.getByRole("link", { name: "mutating/powercell-ci-sandbox" })).toHaveAttribute("href", "https://github.com/mutating/powercell-ci-sandbox");
  await expect(page.getByText("Приватный", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Отключить GitHub", exact: true }).click();
  await expect(page.getByRole("button", { name: "Подключить GitHub", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "mutating/powercell-ci-sandbox" })).toHaveCount(0);
  expect(calls).toContain("DELETE /v1/github/connection");
});

test("Actions starts authorization and rejects a redirect to an unexpected website", async ({ page, account }) => {
  await page.route(`${API}/github/**`, async (route) => {
    const path = new URL(route.request().url()).pathname;
    const headers = { "Access-Control-Allow-Origin": "http://127.0.0.1:18101", "Access-Control-Allow-Credentials": "true" };
    if (path.endsWith("/status")) {
      await route.fulfill({ json: { enabled: true, account: null }, headers });
    } else if (path.endsWith("/authorize")) {
      await route.fulfill({ json: { url: "https://evil.test/" }, headers });
    } else {
      throw new Error(`Unexpected GitHub UI request ${path}`);
    }
  });
  await login(page, account);
  await page.goto("/actions");
  await page.getByRole("button", { name: "Подключить GitHub", exact: true }).click();
  await expect(page.getByRole("alert")).toBeVisible();
  await expect(page).toHaveURL("http://127.0.0.1:18101/actions");
});

test("Actions creates a personal CI binding with resource minimums and shows workflow instructions", async ({ page, account }) => {
  let binding: Record<string, unknown> | null = null;
  let requestBody: Record<string, unknown> | null = null;
  await page.route(`${API}/github/**`, async (route) => {
    const path = new URL(route.request().url()).pathname;
    const headers = { "Access-Control-Allow-Origin": "http://127.0.0.1:18101", "Access-Control-Allow-Credentials": "true" };
    if (path.endsWith("/status")) await route.fulfill({ json: { enabled: true, account: { id: 123, login: "github-user" } }, headers });
    else if (path.endsWith("/installations")) await route.fulfill({ json: { total_count: 1, installations: [{ id: 456, login: "mutating", repository_selection: "selected", suspended: false }] }, headers });
    else if (path.endsWith("/repositories")) await route.fulfill({ json: { total_count: 1, repositories: [{ id: 789, full_name: "mutating/powercell-ci-sandbox", private: true, default_branch: "main", can_administer: true }] }, headers });
    else if (path.endsWith("/routes") && route.request().method() === "POST") {
      requestBody = route.request().postDataJSON();
      binding = { ...requestBody, id: "11111111-1111-4111-8111-111111111111", repository: "mutating/powercell-ci-sandbox", enabled: true };
      await route.fulfill({ status: 201, json: binding, headers });
    } else if (path.endsWith("/routes")) await route.fulfill({ json: binding ? [binding] : [], headers });
    else if (path.endsWith("/workflow")) await route.fulfill({ json: { yaml: "jobs:\n  test:\n    needs: powercell_route\n" }, headers });
    else if (path.endsWith("/runs")) await route.fulfill({ json: [{ id: "run-one", run_id: 42, run_attempt: 1, status: "fallback", reason: "no_capacity", url: "https://github.com/mutating/powercell-ci-sandbox/actions/runs/42" }], headers });
    else throw new Error(`Unexpected GitHub UI request ${path}`);
  });
  await login(page, account);
  await page.goto("/actions");
  await page.getByRole("textbox", { name: "Job ID", exact: true }).fill("checks");
  await page.getByRole("spinbutton", { name: "CPU", exact: true }).fill("1");
  await page.getByRole("spinbutton", { name: "RAM · GiB", exact: true }).fill("2");
  await page.getByRole("button", { name: "Создать привязку", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "Workflow YAML", exact: true })).toHaveValue(/needs: powercell_route/);
  await expect(page.getByText("Нет подходящих свободных компьютеров", { exact: false })).toBeVisible();
  expect(requestBody).toMatchObject({ repository_id: 789, installation_id: 456, job_key: "checks", cpu_millis: 1000, memory_bytes: 2 * 1024 ** 3, company_id: null, computer_ids: [], fallback: "ubuntu-24.04-arm" });
});
