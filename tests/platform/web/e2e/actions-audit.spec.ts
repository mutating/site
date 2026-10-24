import { test, expect, login } from "./fixtures";
import { actionsPeer, binding, repositoryURL } from "./actions-peer";

/** Real branch names replace a free-form ref; a nested branch must reach the API unchanged. */
test("Actions selects a real nested branch without manufacturing a Git ref", async ({ page, account }) => {
  const peer = await actionsPeer(page); peer.branches = ["main", "topic.locked/release-v1.2"];
  await login(page, account); await page.goto(repositoryURL);
  await page.getByLabel("Ветка", { exact: true }).selectOption(peer.branches[1]);
  await page.getByRole("button", { name: "Подключить", exact: true }).click();
  await expect(page.getByLabel("Workflow YAML", { exact: true })).toBeVisible();
  expect(peer.bindings[0].ref).toBe("refs/heads/topic.locked/release-v1.2");
});

/** Pool read permission must not become resource-management authority on an existing connection. */
for (const permissions of [["execution.read"], ["execution.read", "resources.manage"], ["execution.read", "execution.run"]]) {
  test(`Actions keeps an organization connection read-only with ${permissions.join(",")}`, async ({ page, account }) => {
    const peer = await actionsPeer(page);
    peer.companies = [{ id: "lab", name: "Lab", my_permissions: permissions }];
    peer.bindings = [{ ...binding, company_id: "lab" }];
    await login(page, account); await page.goto(repositoryURL);
    await expect(page.getByLabel("CPU", { exact: true })).toBeDisabled();
    await expect(page.getByRole("button", { name: "Отключить подключение", exact: true })).toHaveCount(0);
    await expect(page.getByText(/Для изменения подключения нужны права/)).toBeVisible();
    await expect(page.getByLabel("Workflow YAML", { exact: true })).toBeVisible();
  });
}

/** Retained metadata keeps the draft, but stale or failed permissions cannot authorize saving. */
test("Actions blocks saving during metadata refresh and after a discovery failure", async ({ page, account }) => {
  const peer = await actionsPeer(page);
  await login(page, account); await page.goto(repositoryURL);
  await page.getByLabel("CPU", { exact: true }).fill("1");
  let release!: () => void; const gate = new Promise<void>(resolve => release = resolve);
  peer.custom = async (route, url) => {
    if (!url.pathname.endsWith("/configuration")) return false;
    await gate; await route.fulfill({ status: 503, json: {} }); return true;
  };
  await page.getByRole("button", { name: "Обновить", exact: true }).click();
  await expect(page.getByRole("button", { name: "Подключить", exact: true })).toBeDisabled();
  release(); await expect(page.getByRole("alert")).toBeVisible();
  await expect(page.getByLabel("CPU", { exact: true })).toHaveValue("1");
  await expect(page.getByRole("button", { name: "Подключить", exact: true })).toBeDisabled();
  peer.custom = null;
  await page.getByRole("button", { name: /Попробовать снова/ }).click();
  await expect(page.getByRole("button", { name: "Подключить", exact: true })).toBeEnabled();
  await expect(page.getByLabel("CPU", { exact: true })).toHaveValue("1");
});

/** An external disable must remove live setup instructions without removing recorded runs. */
test("Actions refresh detects a binding disabled in another session", async ({ page, account }) => {
  const peer = await actionsPeer(page); peer.bindings = [{ ...binding }];
  peer.runs = [{ id: "run", run_id: 42, run_attempt: 1, status: "completed", reason: "github_success", url: "https://github.com/mutating/ci-sandbox/actions/runs/42" }];
  await login(page, account); await page.goto(repositoryURL);
  await expect(page.getByLabel("Workflow YAML", { exact: true })).toBeVisible();
  peer.bindings[0].enabled = false; peer.bindings[0].revision++;
  await page.getByRole("button", { name: "Обновить", exact: true }).click();
  await expect(page.getByText("Отключён", { exact: true })).toBeVisible();
  await expect(page.getByLabel("Workflow YAML", { exact: true })).toHaveCount(0);
  await expect(page.locator(".repository-runs").getByRole("link", { name: "#42 / 1" })).toBeVisible();
});

/** Legacy exact workflow/job constraints remain intact unless the user explicitly changes scope. */
test("Actions preserves legacy workflow scope when editing resource limits", async ({ page, account }) => {
  const peer = await actionsPeer(page); peer.bindings = [{ ...binding, workflow_paths: null, job_key: "checks" }];
  await login(page, account); await page.goto(repositoryURL);
  await expect(page.getByLabel("Все workflows", { exact: true })).not.toBeChecked();
  await page.getByLabel("CPU", { exact: true }).fill("1");
  await page.getByRole("button", { name: "Сохранить настройки", exact: true }).click();
  await expect(page.getByText("Настройки сохранены.", { exact: true })).toBeVisible();
  expect(peer.bindings[0]).toMatchObject({ workflow_paths: null, workflow_path: binding.workflow_path, job_key: "checks" });
});
