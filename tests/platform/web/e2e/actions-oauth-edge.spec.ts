import { test, expect, login } from "./fixtures";
import { actionsPeer, binding } from "./actions-peer";

for (const path of ["authorize", "installations/authorize"] as const) {
  /** Leaving the catalog cancels its pending external navigation; a late OAuth response must not destroy the editor's draft. */
  test(`late ${path} response cannot leave the current repository editor`, async ({ page, account }) => {
    const peer = await actionsPeer(page); peer.bindings = [{ ...binding }];
    const destination = "https://github.com/login/oauth/authorize?state=old-view";
    await page.route("https://github.com/**", route => route.fulfill({ contentType: "text/html", body: "<title>Intercepted OAuth destination</title>" }));
    let release!: () => void, requested = false;
    const gate = new Promise<void>(resolve => release = resolve);
    peer.custom = async (route, url) => {
      if (url.pathname !== `/v1/github/${path}` || route.request().method() !== "POST") return false;
      requested = true; await gate; await route.fulfill({ json: { url: destination } }); return true;
    };
    await login(page, account); await page.getByRole("link", { name: "Actions", exact: true }).click();
    const buttonName = path === "authorize" ? "Повторно подключить" : "Настроить доступ GitHub";
    await page.getByRole("button", { name: buttonName, exact: true }).click();
    try {
      await expect.poll(() => requested).toBe(true);
      await page.getByRole("article").getByRole("link", { name: "Настроить", exact: true }).click();
      await page.getByLabel("CPU", { exact: true }).fill("0.75");
      const currentURL = page.url();
      const response = page.waitForResponse(row => row.request().method() === "POST" && row.url().endsWith(`/github/${path}`));
      release(); await response; await page.waitForLoadState("networkidle");
      await expect(page).toHaveURL(currentURL);
      await expect(page.getByLabel("CPU", { exact: true })).toHaveValue("0.75");
      await page.getByRole("link", { name: "← К репозиториям", exact: true }).click();
      await page.getByRole("button", { name: buttonName, exact: true }).click();
      await expect(page).toHaveURL(destination);
    } finally { release(); }
  });
}
