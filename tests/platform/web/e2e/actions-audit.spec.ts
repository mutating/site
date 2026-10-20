import { test, expect, login } from "./fixtures";
import { actionsPeer, binding } from "./actions-peer";

// Assertions: Git-invalid branch components cannot produce an unusable binding;
// a nested valid branch still submits its exact ref after correcting the input.
test("Actions rejects invalid Git branch names before saving", async ({ page, account }) => {
  const peer = await actionsPeer(page);
  await login(page, account); await page.goto("/actions");
  const ref = page.getByRole("textbox", { name: "Git ref", exact: true });
  const create = page.getByRole("button", { name: "Создать привязку", exact: true });
  await expect(create).toBeEnabled();
  for (const branch of ["topic/", "topic.", ".hidden", "topic/.hidden", "topic.lock", "topic.lock/next", "topic/name.lock"]) {
    await ref.fill(`refs/heads/${branch}`); await create.click();
    expect(await ref.evaluate((node) => (node as HTMLInputElement).validity.valid)).toBe(false);
    expect(peer.calls.filter((call) => call.method === "POST" && call.path === "github/routes")).toHaveLength(0);
  }
  await ref.fill("refs/heads/topic.locked/release-v1.2"); await create.click();
  await expect(page.getByRole("textbox", { name: "Workflow YAML", exact: true })).toBeVisible();
  expect(peer.calls.find((call) => call.method === "POST" && call.path === "github/routes")?.body?.ref).toBe("refs/heads/topic.locked/release-v1.2");
});

// Assertions: read access keeps workflow/history available, while disabling an
// organization binding requires both manage and run rights, as the API does.
test("Actions offers binding management only for authorized pools", async ({ page, account }) => {
  const peer = await actionsPeer(page);
  peer.companies = [
    { id: "read", name: "Read only", my_permissions: ["execution.read"] },
    { id: "manage", name: "Without execution", my_permissions: ["resources.manage", "execution.read"] },
    { id: "full", name: "CI team", my_permissions: ["resources.manage", "execution.run", "execution.read"] },
  ];
  peer.bindings = peer.companies.map((company, index) => ({ ...binding, id: `route-${index}`, company_id: company.id, job_key: `job_${index}` }));
  await login(page, account); await page.goto("/actions");
  const cards = page.locator("article").filter({ has: page.getByRole("button", { name: "Workflow и запуски", exact: true }) });
  await expect(cards).toHaveCount(3);
  for (const index of [0, 1, 2]) {
    await expect(cards.nth(index).getByRole("button", { name: "Отключить привязку", exact: true })).toHaveCount(index === 2 ? 1 : 0);
    await cards.nth(index).getByRole("button", { name: "Workflow и запуски", exact: true }).click();
    await expect(page.getByRole("textbox", { name: "Workflow YAML", exact: true })).toHaveValue(new RegExp(`route-${index}`));
  }
  expect(peer.calls.filter((call) => call.method === "DELETE")).toHaveLength(0);
});

// Assertions: saved policy is distinguishable from the editable draft; disk,
// preparation, machines and concurrency remain inspectable after disabling;
// known computers use their names, while unavailable ones retain their identity;
// disabled details explain recovery instead of inviting use of an inactive route.
test("Actions shows the saved policy and updates selected disabled details", async ({ page, account }) => {
  const peer = await actionsPeer(page);
  peer.computers = [{ id: "mac-1", name: "Mac для CI", runtime: "powercell-ubuntu-arm64-v1" }];
  peer.bindings = [{ ...binding, disk_bytes: 4 * 1024 ** 3, preparation_seconds: 300, max_parallel: 3, computer_ids: ["mac-1", "unavailable-mac"] }];
  await login(page, account); await page.goto("/actions");
  const card = page.locator("article").filter({ has: page.getByRole("button", { name: "Workflow и запуски", exact: true }) });
  await expect(card).toContainText("Диск: 4 GiB"); await expect(card).toContainText("секунды: 300");
  await card.getByRole("button", { name: "Workflow и запуски", exact: true }).click();
  const detail = page.locator(".actions-ci-detail");
  const policy = detail.locator(".actions-ci-policy");
  for (const value of ["Личный пул", "Mac для CI", "unavailable-mac", "2 CPU · 4 GiB RAM · 4 GiB", "300", "3"]) await expect(policy).toContainText(value);
  await card.getByRole("button", { name: "Отключить привязку", exact: true }).click();
  await expect(detail.getByRole("status").filter({ hasText: "Эта привязка отключена" })).toBeVisible();
  await expect(detail).not.toContainText("Примените YAML в репозитории");
  await expect(policy).toContainText("Mac для CI");
  await expect(card.getByRole("button", { name: "Отключить привязку", exact: true })).toHaveCount(0);
  await detail.getByRole("button", { name: "Обновить", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "Workflow YAML", exact: true })).toBeVisible();
  await expect(detail).toContainText("Запусков по этой привязке пока нет.");
});

// Assertions: retained discovery data preserves the draft during refresh but
// cannot authorize creation while permission checks are pending or have failed;
// retry restores creation without losing the entered job ID.
for (const endpoint of ["github/installations", "github/installations/456/repositories"]) {
  test(`Actions waits for current discovery permissions at ${endpoint}`, async ({ page, account }) => {
    const peer = await actionsPeer(page);
    await login(page, account); await page.goto("/actions");
    const create = page.getByRole("button", { name: "Создать привязку", exact: true });
    await expect(create).toBeEnabled();
    await page.getByRole("textbox", { name: "Job ID", exact: true }).fill("integration_checks");
    let release!: () => void; const gate = new Promise<void>((resolve) => { release = resolve; });
    peer.custom = async (route, url) => {
      if (url.pathname !== `/v1/${endpoint}`) return false;
      await gate; await route.fulfill({ status: 503, json: { detail: "Temporary permission check failure" } }); return true;
    };
    await page.getByRole("button", { name: "Обновить", exact: true }).click();
    await expect(create).toBeDisabled();
    release(); await expect(page.getByRole("alert")).toBeVisible();
    await expect(create).toBeDisabled();
    expect(peer.calls.filter((call) => call.method === "POST" && call.path === "github/routes")).toHaveLength(0);
    peer.custom = null;
    await page.getByRole("button", { name: /Попробовать снова/ }).click();
    await expect(create).toBeEnabled();
    await expect(page.getByRole("textbox", { name: "Job ID", exact: true })).toHaveValue("integration_checks");
    await create.click();
    await expect(page.getByRole("textbox", { name: "Workflow YAML", exact: true })).toBeVisible();
    expect(peer.calls.find((call) => call.method === "POST" && call.path === "github/routes")?.body?.job_key).toBe("integration_checks");
  });
}

// Assertions: refreshing a binding also detects another session's disable;
// pending metadata keeps refresh disabled even when workflow/history are ready,
// then replaces active instructions without losing the recorded runs or YAML.
test("Actions refresh detects a binding disabled in another session", async ({ page, account }) => {
  const peer = await actionsPeer(page); peer.bindings = [{ ...binding }];
  peer.runs = [{ id: "run-1", run_id: 1234, run_attempt: 1, status: "completed", reason: "github_success", url: "https://github.com/mutating/ci-sandbox/actions/runs/1234" }];
  await login(page, account); await page.goto("/actions");
  await page.getByRole("button", { name: "Workflow и запуски", exact: true }).click();
  const detail = page.locator(".actions-ci-detail");
  const refresh = detail.getByRole("button", { name: "Обновить", exact: true });
  await expect(refresh).toBeEnabled();
  peer.bindings[0].enabled = false;
  let release!: () => void; const gate = new Promise<void>((resolve) => { release = resolve; });
  peer.custom = async (route, url) => {
    if (url.pathname !== "/v1/github/routes") return false;
    await gate; await route.fulfill({ json: peer.bindings }); return true;
  };
  await refresh.click(); await expect(refresh).toBeDisabled();
  await expect(detail).toHaveAttribute("aria-busy", "true");
  await expect(page.getByRole("textbox", { name: "Workflow YAML", exact: true })).toBeVisible();
  release();
  await expect(detail.getByRole("status").filter({ hasText: "Эта привязка отключена" })).toBeVisible();
  await expect(refresh).toBeEnabled();
  await expect(detail).not.toContainText("Примените YAML в репозитории");
  await expect(detail.getByRole("link", { name: "#1234 / 1", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Отключить привязку", exact: true })).toHaveCount(0);
});
