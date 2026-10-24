import { test, expect, login } from "./fixtures";
import { actionsPeer, repository } from "./actions-peer";

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
    const label = action === "connect" ? "Подключить GitHub" : action === "reconnect" ? "Повторно подключить" : "Настроить доступ GitHub";
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
