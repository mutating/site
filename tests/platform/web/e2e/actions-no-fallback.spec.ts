import { selectLanguage } from "./header-controls";
import { test, expect, login } from "./fixtures";
import { actionsPeer } from "./actions-peer";

// Assertions: binding creation and saved details expose only Mac execution;
// preparation is bounded, lack of capacity fails, and no obsolete policy is sent.
test("Actions has one execution policy and no hosted fallback controls", async ({ page, account }) => {
  const peer = await actionsPeer(page);
  await login(page, account); await page.goto("/actions");
  await expect(page.getByRole("textbox", { name: "Job ID", exact: true })).toBeVisible();
  await expect(page.getByRole("combobox", { name: "Fallback", exact: true })).toHaveCount(0);
  await expect(page.getByText(/Нет свободной Power Cell — запуск сразу завершится ошибкой/)).toBeVisible();
  await page.getByRole("textbox", { name: "Job ID", exact: true }).fill("checks");
  await page.getByRole("spinbutton", { name: "CPU", exact: true }).fill("3");
  await page.getByRole("spinbutton", { name: "RAM · GiB", exact: true }).fill("9");
  await page.getByRole("button", { name: "Создать привязку", exact: true }).click();
  await expect.poll(() => peer.calls.filter(c => c.path === "github/routes" && c.method === "POST").length).toBe(1);
  const sent = peer.calls.find(c => c.path === "github/routes" && c.method === "POST")!.body!;
  expect(sent).toMatchObject({ cpu_millis: 3000, memory_bytes: 9 * 1024**3 });
  expect(sent).not.toHaveProperty("fallback");
  await expect(page.locator(".actions-ci-detail")).not.toContainText(/fallback/i);
  await selectLanguage(page);
  await expect(page.getByRole("combobox", { name: "Fallback", exact: true })).toHaveCount(0);
  await expect(page.getByText(/No available Power Cell — the run fails immediately/)).toBeVisible();
});
