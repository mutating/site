import { test, expect, login } from "./fixtures";
import { actionsPeer, binding, repository, repositoryURL } from "./actions-peer";
import { selectLanguage } from "./header-controls";

/** Names and controls must stay inside the viewport at phone, tablet and desktop widths. */
test("Actions catalog, editor and change history fit narrow viewports with long names", async ({ page, account }, info) => {
  const peer = await actionsPeer(page);
  peer.repositories = [{ ...repository, full_name: `organization-${"long".repeat(20)}/repository-${"long".repeat(20)}` }];
  await login(page, account); await page.goto("/actions");
  const assertWidth = async () => expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await expect(page.locator(".repository-card")).toHaveCount(1); await assertWidth();
  await page.getByRole("link", { name: "Подключить", exact: true }).click();
  await expect(page.getByLabel("CPU", { exact: true })).toBeVisible(); await assertWidth();
  await page.getByRole("button", { name: "Подключить", exact: true }).click();
  await expect(page.getByLabel("Workflow YAML", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Настройка YAML в GitHub", exact: true })).toBeFocused(); await assertWidth();
  await page.getByRole("link", { name: "История изменений", exact: true }).click();
  await expect(page.locator(".binding-revision")).toHaveCount(1); await assertWidth();
  await page.screenshot({ path: info.outputPath("actions-history.png"), fullPage: true });
});

/** Provider conclusions keep their own meaning instead of all completed jobs being shown as successful. */
for (const [reason, ru, en] of [["github_success", "Успешно", "Succeeded"], ["github_failure", "Ошибка", "Failed"], ["github_cancelled", "Отменён", "Cancelled"], ["github_timed_out", "Истёк срок выполнения", "Execution timed out"], ["github_skipped", "Пропущен", "Skipped"], ["github_neutral", "Нейтральный результат", "Neutral result"], ["github_action_required", "Требуется действие", "Action required"], ["github_stale", "Устарел", "Stale"]]) {
  test(`Actions translates the ${reason} conclusion`, async ({ page, account }) => {
    const peer = await actionsPeer(page); peer.bindings = [{ ...binding }];
    peer.runs = [{ id: reason, run_id: 42, run_attempt: 1, status: "completed", reason, url: "https://github.com/mutating/ci-sandbox/actions/runs/42" }];
    await login(page, account); await page.goto(repositoryURL);
    await expect(page.locator(".actions-ci-status")).toHaveText(ru);
    await selectLanguage(page); await expect(page.locator(".actions-ci-status")).toHaveText(en);
  });
}

/** Browser constraints reject out-of-range policy values before a network mutation. */
test("Actions validates resource, concurrency and timeout limits before saving", async ({ page, account }) => {
  const peer = await actionsPeer(page);
  await login(page, account); await page.goto(repositoryURL);
  await page.locator(".binding-advanced > summary").click();
  for (const [name, bad] of [["cpu", "0"], ["memory", "0"], ["disk", "0"], ["parallel", "101"], ["wait", "301"], ["duration", "361"]]) {
    const input = page.locator(`input[name=${name}]`); const original = await input.inputValue();
    await input.fill(bad); await page.getByRole("button", { name: "Подключить", exact: true }).click();
    expect(peer.calls.filter(row => row.path === "github/routes" && row.method === "POST")).toHaveLength(0);
    await input.fill(original);
  }
});

/** Choosing a different branch must not show YAML generated against the saved branch's policy. */
test("Actions requires saving a changed branch before generating setup YAML", async ({ page, account }) => {
  const peer = await actionsPeer(page); peer.bindings = [{ ...binding }];
  await login(page, account); await page.goto(repositoryURL);
  await expect(page.getByLabel("Workflow YAML", { exact: true })).toBeVisible();
  await page.getByLabel("Ветка", { exact: true }).selectOption("develop");
  await expect(page.getByLabel("Workflow YAML", { exact: true })).toHaveCount(0);
  await expect(page.getByText(/Сохраните выбранную ветку/)).toBeVisible();
  await page.getByRole("button", { name: "Сохранить настройки", exact: true }).click();
  await expect(page.getByLabel("Workflow YAML", { exact: true })).toBeVisible();
  expect(peer.bindings[0].ref).toBe("refs/heads/develop");
});
