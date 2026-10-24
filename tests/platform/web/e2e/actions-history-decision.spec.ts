import { selectLanguage } from "./header-controls";
import { test, expect, login } from "./fixtures";
import { actionsPeer, binding, repositoryURL } from "./actions-peer";

// Assertions: capacity failure and cancelled preparation have accurate labels
// in RU/EN, and neither advertises another execution backend.
for (const [status, reason, ru, en] of [
  ["failed", "no_capacity", "Ошибка", "Failed"],
  ["cancelled", "bootstrap_cancelled", "Отменён", "Cancelled"],
]) {
  test(`Actions describes ${reason} without a hosted selection`, async ({ page, account }) => {
    const peer = await actionsPeer(page);
    peer.bindings = [{ ...binding }];
    peer.runs = [{ id: "run", run_id: 42, run_attempt: 1, status, reason,
      url: "https://github.com/mutating/ci-sandbox/actions/runs/42/attempts/1" }];
    await login(page, account); await page.goto(repositoryURL);
      const run = page.locator(".actions-ci-runs p");
    await expect(run.locator(".actions-ci-status")).toHaveText(ru);
    await expect(run).not.toContainText("Выбран GitHub Actions");
    await selectLanguage(page);
    await expect(run.locator(".actions-ci-status")).toHaveText(en);
    await expect(run).not.toContainText("GitHub Actions selected");
  });
}
