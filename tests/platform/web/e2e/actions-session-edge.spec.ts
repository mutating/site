import { test, expect, login, makeAccount, API } from "./fixtures";
import { actionsPeer, binding } from "./actions-peer";

for (const operation of ["save", "toggle", "authorize", "expired", "renewed"] as const) {
  /** A real cross-tab login must isolate old replies and 401s, including a new session for the same user, while current requests still work. */
  test(`late ${operation} cannot invalidate a newer Actions session`, async ({ page, context, playwright, request, account }) => {
    const sameUser = operation === "renewed";
    const profileName = sameUser ? "Эля" : "Анна";
    const other = sameUser ? account : await makeAccount(request, "Борис");
    const peer = await actionsPeer(page);
    const oauthA = "https://github.com/login/oauth/authorize?state=old-actor-A";
    const oauthB = "https://github.com/login/oauth/authorize?state=current-actor-B";
    await page.route("https://github.com/**", route => route.fulfill({ contentType: "text/html", body: "<title>Intercepted OAuth destination</title>" }));
    const a = { ...binding, id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", user_id: account.id };
    const b = { ...binding, id: sameUser ? a.id : "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", user_id: other.id, cpu_millis: 3000 };
    peer.bindings = [a];
    let releaseProfile!: () => void, releaseMutation!: () => void, profileRequested = false, mutationRequested = false;
    const profileGate = new Promise<void>(resolve => releaseProfile = resolve);
    const mutationGate = new Promise<void>(resolve => releaseMutation = resolve);
    let actor = account.id, bMetadata = false, renewed = false;
    await page.route(`${API}/users/${account.id}`, async route => {
      if (route.request().method() !== "PATCH") { await route.continue(); return; }
      const response = await route.fetch(); expect(response.status()).toBe(200);
      profileRequested = true; await profileGate; await route.fulfill({ response });
    });
    await page.route(`${API}/users/me`, async route => {
      const response = await route.fetch(); expect(response.status()).toBe(200);
      actor = (await response.json()).id;
      if (actor === other.id && (!sameUser || renewed)) peer.bindings = [b];
      await route.fulfill({ response });
    });
    peer.custom = async (route, url) => {
      if (operation === "authorize" && url.pathname === "/v1/github/authorize" && route.request().method() === "POST") {
        if (actor === other.id) { await route.fulfill({ json: { url: oauthB } }); return true; }
        mutationRequested = true; await mutationGate; await route.fulfill({ json: { url: oauthA } }); return true;
      }
      if (operation === "authorize" && actor === other.id && url.pathname === "/v1/github/repositories") bMetadata = true;
      if (actor === other.id && route.request().method() === "GET" && url.pathname.startsWith(`/v1/github/routes/${a.id}`)) {
        await route.fulfill({ status: 404, json: { detail: "Not found" } }); return true;
      }
      if (url.pathname.endsWith("/configuration") && actor === other.id) {
        if (url.searchParams.get("binding_id") === a.id) { await route.fulfill({ status: 404, json: {} }); return true; }
        bMetadata = true;
      }
      if (url.pathname !== `/v1/github/routes/${a.id}` || route.request().method() !== "PATCH") return false;
      if (sameUser && mutationRequested) return false;
      if (operation === "expired" || sameUser) {
        // WebKit route.fetch uses the current shared cookie jar. An isolated
        // snapshot preserves A's real session so the API can reject it later.
        const oldSession = await playwright.request.newContext({ storageState: await context.storageState() });
        try {
          const identity = await oldSession.get(`${API}/users/me`);
          expect(identity.status()).toBe(200); expect((await identity.json()).id).toBe(account.id);
          mutationRequested = true; await mutationGate;
          const response = await oldSession.patch(route.request().url(), {
            headers: { Origin: "http://127.0.0.1:18101", "X-Requested-With": "ThrongDashboard" },
            data: route.request().postDataJSON(),
          });
          expect(response.status()).toBe(401);
          await route.fulfill({ response }); return true;
        } finally { await oldSession.dispose(); }
      }
      const body = route.request().postDataJSON();
      const saved = { ...a, ...(body.configuration ?? body), revision: 2 };
      peer.bindings = [saved]; mutationRequested = true; await mutationGate;
      await route.fulfill({ json: saved }); return true;
    };
    try {
      await login(page, account);
      const menu = page.getByRole("banner").getByRole("button", { name: "Меню пользователя", exact: true });
      await menu.click(); await page.getByRole("menuitem", { name: "Профиль", exact: true }).click();
      await page.getByLabel("Имя", { exact: true }).fill(profileName);
      await page.locator(".profile-settings-form").getByRole("button", { name: "Сохранить изменения", exact: true }).click();
      await expect.poll(() => profileRequested).toBe(true);
      await page.getByRole("link", { name: "Actions", exact: true }).click();
      if (operation !== "authorize") await page.getByRole("article").getByRole("link", { name: "Настроить", exact: true }).click();
      await expect(page).not.toHaveURL(/binding=/);
      const response = page.waitForResponse(row => row.request().method() === (operation === "authorize" ? "POST" : "PATCH") && row.url().endsWith(operation === "authorize" ? "/github/authorize" : a.id));
      if (operation === "authorize") {
        await page.getByRole("button", { name: "Повторно подключить", exact: true }).click();
      } else if (operation !== "toggle") {
        await page.getByLabel("CPU", { exact: true }).fill("0.75");
        await page.getByRole("button", { name: "Сохранить настройки", exact: true }).click();
      } else {
        await page.getByRole("button", { name: "Отключить подключение", exact: true }).click();
      }
      await expect.poll(() => mutationRequested).toBe(true);
      const second = await context.newPage(); await second.goto("/profile");
      const secondMenu = second.getByRole("banner").getByRole("button", { name: "Меню пользователя", exact: true });
      await expect(secondMenu).toHaveAttribute("title", account.email);
      await secondMenu.click(); await second.getByRole("menuitem", { name: "Выйти", exact: true }).click();
      await expect(second.getByLabel("Email или ник")).toBeVisible();
      await login(second, sameUser ? { ...other, first_name: profileName } : other);
      renewed = true;
      const session = await page.request.get(`${API}/users/me`);
      expect(session.status()).toBe(200); expect((await session.json()).id).toBe(other.id);
      releaseProfile();
      await expect(menu).toHaveAttribute("title", other.email);
      if (!sameUser) await expect.poll(() => bMetadata).toBe(true);
      else await expect(menu.getByText("Э", { exact: true })).toBeVisible();
      if (operation !== "authorize") await expect(page.getByLabel("CPU", { exact: true })).toHaveValue(sameUser ? "0.75" : "3");
      const currentURL = page.url();
      releaseMutation(); await response; await page.waitForLoadState("networkidle");
      await expect(page).toHaveURL(currentURL);
      await expect(menu).toHaveAttribute("title", other.email);
      if (operation === "authorize") {
        await expect(page.getByRole("alert")).toHaveCount(0);
        await page.getByRole("button", { name: "Повторно подключить", exact: true }).click();
        await expect(page).toHaveURL(oauthB);
        return;
      }
      await expect(page.getByLabel("CPU", { exact: true })).toHaveValue(sameUser ? "0.75" : "3");
      await expect(page).toHaveURL(currentURL);
      await expect(page.getByText("Настройки сохранены.", { exact: true })).toHaveCount(0);
      if (!sameUser) await expect(page.getByRole("alert")).toHaveCount(0);
      await page.getByLabel("CPU", { exact: true }).fill("1.25");
      await page.getByRole("button", { name: "Сохранить настройки", exact: true }).click();
      await expect(page.getByText("Настройки сохранены.", { exact: true })).toBeVisible();
      expect(peer.bindings.find(row => row.id === b.id)!.cpu_millis).toBe(1250);
      expect(peer.calls.filter(row => row.method === "PATCH").map(row => row.path)).toEqual([`github/routes/${a.id}`, `github/routes/${b.id}`]);
    } finally { releaseProfile(); releaseMutation(); }
  });
}
