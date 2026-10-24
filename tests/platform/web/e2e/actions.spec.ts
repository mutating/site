import { actionsPeer, repository } from "./actions-peer";
import { test, expect, login, API } from "./fixtures";

test("Actions reports an unconfigured GitHub integration without exposing connection controls", async ({ page, account }) => {
  await login(page, account);
  await expect(page.getByRole("navigation").getByRole("link", { name: "Actions", exact: true })).toHaveCount(0);
  await page.goto("/actions");
  await expect(page.getByRole("heading", { name: "Подключение GitHub пока недоступно" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Подключить GitHub", exact: true })).toHaveCount(0);
});

/** Private repository names open the local editor; disconnect removes cached catalog rows. */
test("Actions opens a private repository and disconnects the linked account", async ({ page, account }) => {
  const peer = await actionsPeer(page);
  await login(page, account); await page.goto("/actions");
  await expect(page.getByRole("link", { name: repository.full_name, exact: true })).toHaveAttribute("href", /actions\/repositories\/789/);
  await expect(page.locator(".repository-card")).toContainText("Приватный");
  await page.getByRole("button", { name: "Отключить GitHub", exact: true }).click();
  await expect(page.getByRole("button", { name: "Подключить GitHub", exact: true })).toBeVisible();
  await expect(page.locator(".repository-card")).toHaveCount(0);
  expect(peer.calls.some(row => row.path === "github/connection" && row.method === "DELETE")).toBe(true);
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
