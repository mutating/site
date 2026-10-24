import { test, expect, login } from "./fixtures";
import { actionsPeer, binding, repositoryURL } from "./actions-peer";

/** History navigation must carry the actual binding, even when the editor chose it implicitly from the catalog. */
test("editor history keeps the selected active binding instead of a newer archive", async ({ page, account }) => {
  const peer = await actionsPeer(page);
  peer.bindings = [{ ...binding, created_at: "2026-10-01T12:00:00Z" }, { ...binding, id: "archive", enabled: false }];
  peer.history.set(binding.id, [{ id: "active-revision", revision: 1, action: "created", created_at: binding.created_at, actor: null, configuration: {}, changes: [{ field: "cpu_millis", before: null, after: 500 }] }]);
  await login(page, account); await page.goto(repositoryURL);
  await page.getByRole("link", { name: "История изменений", exact: true }).click();
  await expect(page.getByLabel("Подключение в истории", { exact: true })).toHaveValue(binding.id);
  await expect(page.locator(".binding-changes")).toContainText("0.5 CPU");
});

/** Returning from an implicitly chosen archive must open that archive's settings, not the active binding. */
test("history return link preserves an implicitly selected archived binding", async ({ page, account }) => {
  const peer = await actionsPeer(page);
  peer.bindings = [{ ...binding, created_at: "2026-10-01T12:00:00Z" }, { ...binding, id: "archive", enabled: false }];
  await login(page, account); await page.goto(repositoryURL.replace("?", "/history?"));
  await expect(page.getByLabel("Подключение в истории", { exact: true })).toHaveValue("archive");
  await page.getByRole("link", { name: "← Настройки подключения", exact: true }).click();
  await expect(page).toHaveURL(/binding=archive/);
  await expect(page.getByText("Отключён", { exact: true })).toBeVisible();
});

/** Archived duplicate records belong in a history selector and must remain accessible after OAuth disconnect. */
test("Actions history selects archived bindings without requiring a live GitHub connection", async ({ page, account }) => {
  const peer = await actionsPeer(page); peer.connected = false;
  peer.bindings = [{ ...binding, id: "archive", enabled: false, created_at: "2026-10-01T12:00:00Z" }, { ...binding }];
  peer.history.set("archive", [{ id: "old", revision: 1, action: "imported", created_at: binding.created_at, actor: null, configuration: {}, changes: [{ field: "workflow_path", before: null, after: ".github/workflows/legacy.yml" }] }]);
  await login(page, account); await page.goto(repositoryURL.replace("?", "/history?"));
  await page.getByLabel("Подключение в истории", { exact: true }).selectOption("archive");
  await expect(page.getByRole("heading", { name: "Прежние настройки", exact: true })).toBeVisible();
  await expect(page.locator(".binding-changes")).toContainText(".github/workflows/legacy.yml");
  expect(peer.calls.some(row => row.path.endsWith("/configuration"))).toBe(false);
});

/** Revision history is independently paginated; out-of-range URLs must recover and filters remain in the return link. */
test("Actions history paginates revisions and clamps stale page numbers", async ({ page, account }) => {
  const peer = await actionsPeer(page); peer.bindings = [{ ...binding }];
  peer.history.set(binding.id, Array.from({ length: 21 }, (_, index) => ({ id: `revision-${index}`, revision: 21 - index, action: "updated", created_at: binding.created_at, actor: null, configuration: {}, changes: [{ field: "max_parallel", before: 1, after: index + 2 }] })));
  await login(page, account); await page.goto(repositoryURL.replace("?", "/history?") + "&page=1001&return=%2Factions%3Fowner_type%3Dpersonal");
  await expect(page).toHaveURL(/page=2/);
  await expect(page.locator(".binding-revision")).toHaveCount(1);
  await expect(page.locator(".binding-revision")).toContainText("Версия 1");
  await page.getByRole("button", { name: "← Назад", exact: true }).click();
  await expect(page.locator(".binding-revision")).toHaveCount(20);
  await expect(page.getByRole("link", { name: "← Настройки подключения", exact: true })).toHaveAttribute("href", /return=%2Factions%3Fowner_type%3Dpersonal/);
});

/** Request failures must not be rendered as an empty audit log, either for routes or revisions. */
for (const endpoint of ["github/routes", `github/routes/${binding.id}/revisions`]) {
  test(`Actions history retries ${endpoint} without a false empty state`, async ({ page, account }) => {
    const peer = await actionsPeer(page); peer.bindings = [{ ...binding }]; peer.failures.set(`GET ${endpoint}`, [503]);
    await login(page, account); await page.goto(repositoryURL.replace("?", "/history?"));
    await expect(page.getByRole("alert")).toBeVisible();
    await expect(page.getByText("История изменений пока пуста.", { exact: true })).toHaveCount(0);
    await page.getByRole("button", { name: /Попробовать снова/ }).click();
    await expect(page.getByText("История изменений пока пуста.", { exact: true })).toBeVisible();
  });
}

/** More than 100 runs must advance offset with the endpoint's own page limit instead of looping or getting 422. */
test("Actions loads more than one hundred executions with compatible API pagination", async ({ page, account }) => {
  const peer = await actionsPeer(page); peer.bindings = [{ ...binding }];
  peer.runs = Array.from({ length: 101 }, (_, index) => ({ id: `run-${index}`, run_id: index, run_attempt: 1, status: "completed", reason: "github_success", url: `https://github.com/mutating/ci-sandbox/actions/runs/${index}` }));
  await login(page, account); await page.goto(repositoryURL);
  await expect(page.locator(".actions-ci-runs > p")).toHaveCount(101);
  const requests = peer.calls.filter(row => row.path.endsWith("/runs"));
  expect(requests.map(row => [row.query.get("limit"), row.query.get("offset")])).toEqual([["100", "0"], ["100", "100"]]);
});

/** Archive settings must not inherit the active connection's status or execution history. */
test("archived settings keep their own state and runs alongside an active connection", async ({ page, account }) => {
  const peer = await actionsPeer(page);
  peer.bindings = [{ ...binding }, { ...binding, id: "archive", enabled: false }];
  peer.runs = [{ id: "old", route_id: "archive", run_id: 2, run_attempt: 1, status: "failed", reason: "no_capacity", url: "https://github.com/mutating/ci-sandbox/actions/runs/2" }, { id: "active", route_id: binding.id, run_id: 3, run_attempt: 1, status: "completed", reason: "github_success", started_at: "2026-10-08T12:00:00Z", url: "https://github.com/mutating/ci-sandbox/actions/runs/3" }];
  await login(page, account); await page.goto(repositoryURL + "&binding=archive");
  await expect(page.getByText("Отключён", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: /Последний.*Ошибка/ })).toBeVisible();
  await expect(page.locator(".actions-ci-runs > p")).toHaveCount(1);
  await expect(page.locator(".actions-ci-runs")).toContainText("#2 / 1");
  await expect(page.locator(".actions-ci-runs")).not.toContainText("#3 / 1");
});
