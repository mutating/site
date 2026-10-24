import { API, expect, login, makeAccount, password, test } from "./fixtures";
import { selectLanguage } from "./header-controls";

const png = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jWZkAAAAASUVORK5CYII=", "base64");
const avatar = { name: "avatar.png", mimeType: "image/png", buffer: png };
const trigger = (page: import("@playwright/test").Page) => page.getByRole("banner").getByRole("button", { name: /^(Меню пользователя|User menu)$/ });
const menu = (page: import("@playwright/test").Page) => page.getByRole("menu", { name: /^(Меню пользователя|User menu)$/ });

for (const mutation of ["profile", "remove avatar"] as const) {
  const orders = mutation === "profile"
    ? ["older response first", "newer response first", "older commit last", "older refresh last", "newer fails"]
    : ["older response first", "newer response first", "older commit last"];
  for (const order of orders) test(`a late ${mutation} response from a previous profile page: ${order}`, async ({ page, account }) => {
    await login(page, account);
    await trigger(page).click();
    await menu(page).getByRole("menuitem", { name: "Профиль", exact: true }).click();
    if (mutation === "remove avatar") {
      await page.getByLabel("Загрузить аватар").setInputFiles(avatar);
      await expect.poll(() => trigger(page).locator("img").evaluate((node: HTMLImageElement) => node.naturalWidth)).toBeGreaterThan(0);
    }
    const url = mutation === "profile" ? `${API}/users/${account.id}` : `${API}/users/me/avatar`;
    const oldMethod = mutation === "profile" ? "PATCH" : "DELETE";
    const newMethod = mutation === "profile" ? "PATCH" : "PUT";
    const gate = () => {
      let release!: () => void;
      let receive!: () => void;
      return {
        held: new Promise<void>(resolve => { release = resolve; }),
        requested: new Promise<void>(resolve => { receive = resolve; }),
        release: () => release(), receive: () => receive(),
      };
    };
    const older = gate();
    const newer = gate();
    let refreshIntercepted = false;
    const handle = async (route: import("@playwright/test").Route) => {
      const request = route.request();
      const oldRefresh = order === "older refresh last" && request.method() === "GET" && !refreshIntercepted;
      const oldMutation = request.method() === oldMethod && (mutation !== "profile" || request.postDataJSON().first_name === "Старое имя");
      const newMutation = request.method() === newMethod && (mutation !== "profile" || request.postDataJSON().first_name === "Новое имя");
      if (!oldRefresh && !oldMutation && !newMutation) return route.continue();
      if (oldRefresh) refreshIntercepted = true;
      if (oldMutation && order === "older refresh last") return route.continue();
      const current = oldRefresh || oldMutation ? older : newer;
      if (oldMutation && order === "older commit last") {
        current.receive();
        await current.held;
        return route.fulfill({ response: await route.fetch() });
      }
      const fails = newMutation && order === "newer fails";
      // WebKit interception needs the fixture bytes instead of a File-backed body.
      const response = fails ? null : await route.fetch(newMutation && mutation === "remove avatar" ? { postData: png } : {});
      if (response) expect(response.ok()).toBe(true);
      current.receive();
      await current.held;
      await route.fulfill(response ? { response } : { status: 503, json: { detail: "Please retry" } });
    };
    await page.route(url, handle);
    if (order === "older refresh last") await page.route(`${API}/users/me`, handle);
    const deliver = async (old: boolean) => {
      const response = page.waitForResponse(response => {
        const request = response.request();
        if (old && order === "older refresh last") return response.url() === `${API}/users/me`;
        return response.url() === url && request.method() === (old ? oldMethod : newMethod)
          && (mutation !== "profile" || request.postDataJSON().first_name === (old ? "Старое имя" : "Новое имя"));
      });
      (old ? older : newer).release();
      await (await response).finished();
      await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
    };
    try {
      if (mutation === "profile") {
        await page.getByLabel("Имя", { exact: true }).fill("Старое имя");
        await page.getByRole("button", { name: "Сохранить изменения" }).click();
      } else await page.getByRole("button", { name: "Удалить аватар" }).click();
      await older.requested;
      await page.getByRole("navigation", { name: "Навигация кабинета" }).getByRole("link", { name: "Обзор", exact: true }).click();
      await expect(page.getByRole("heading", { name: `Привет, ${account.first_name}` })).toBeVisible();
      await trigger(page).click();
      await menu(page).getByRole("menuitem", { name: "Профиль", exact: true }).click();
      if (mutation === "profile") {
        await page.getByLabel("Имя", { exact: true }).fill("Новое имя");
        await page.getByRole("button", { name: "Сохранить изменения" }).click();
      } else await page.getByLabel("Загрузить аватар").setInputFiles(avatar);
      await newer.requested;
      if (order === "older response first") {
        await deliver(true);
        await deliver(false);
      } else {
        await deliver(false);
        if (order !== "newer fails") {
          const form = mutation === "profile" ? ".profile-settings-form" : ".avatar-editor";
          await expect(page.locator(form).getByRole("status")).toBeVisible();
        }
        await deliver(true);
      }
      const current = await (await page.request.get(`${API}/auth/session`)).json();
      if (mutation === "profile") {
        const expectedName = ["older commit last", "newer fails"].includes(order) ? "Старое имя" : "Новое имя";
        expect(current.first_name).toBe(expectedName);
        await expect(trigger(page).locator(".avatar")).toHaveText(expectedName[0]);
        await page.getByRole("navigation", { name: "Навигация кабинета" }).getByRole("link", { name: "Обзор", exact: true }).click();
        await expect(page.getByRole("heading", { name: `Привет, ${expectedName}` })).toBeVisible();
      } else if (order === "older commit last") {
        expect(current.avatar_version).toBeNull();
        await expect(trigger(page).locator("img")).toHaveCount(0);
      } else {
        expect(current.avatar_version).toBeTruthy();
        await expect.poll(() => trigger(page).locator("img").evaluate((node: HTMLImageElement) => node.naturalWidth)).toBeGreaterThan(0);
      }
    } finally { older.release(); newer.release(); }
  });
}

test("Space activates the profile menuitem and signs out exactly once", async ({ page, account }) => {
  await login(page, account);
  await trigger(page).focus();
  await page.keyboard.press("ArrowDown");
  await expect(menu(page).getByRole("menuitem", { name: "Профиль", exact: true })).toBeFocused();
  await page.keyboard.press("Space");
  await expect(page).toHaveURL(/\/profile$/);
  await expect(page.getByLabel("Имя", { exact: true })).toHaveValue(account.first_name);
  await expect(menu(page)).toHaveCount(0);
  let signOuts = 0;
  page.on("request", request => {
    if (request.url() === `${API}/auth/session` && request.method() === "DELETE") signOuts++;
  });
  await trigger(page).focus();
  await page.keyboard.press("ArrowUp");
  await expect(menu(page).getByRole("menuitem", { name: "Выйти", exact: true })).toBeFocused();
  await page.keyboard.press("Space");
  await expect(page).toHaveURL(/\/login$/);
  expect(signOuts).toBe(1);
});

test("switching auth forms during a pending login keeps the menu and cookie on the same account", async ({ page, account, request }) => {
  const other = await makeAccount(request, "Борис");
  let release!: () => void;
  let received!: () => void;
  const held = new Promise<void>(resolve => { release = resolve; });
  const requested = new Promise<void>(resolve => { received = resolve; });
  await page.route(`${API}/auth/session`, async route => {
    if (route.request().method() !== "POST" || route.request().postDataJSON().login !== account.email) return route.continue();
    const response = await route.fetch();
    received();
    await held;
    await route.fulfill({ response });
  });
  try {
    await page.goto("login");
    await page.getByLabel("Email или ник").fill(account.email);
    await page.getByLabel("Пароль", { exact: true }).fill(password);
    await page.getByRole("button", { name: "Войти", exact: true }).click();
    await requested;
    await page.locator(".auth-switch").getByRole("link").click();
    await page.locator(".auth-switch").getByRole("link").click();
    await page.getByLabel("Email или ник").fill(other.email);
    await page.getByLabel("Пароль", { exact: true }).fill(password);
    const signedIn = page.waitForResponse(response => response.url() === `${API}/auth/session` && response.request().method() === "POST" && response.request().postDataJSON().login === other.email);
    await page.getByRole("button", { name: "Войти", exact: true }).click();
    release();
    await (await signedIn).finished();
    expect((await (await page.request.get(`${API}/auth/session`)).json()).id).toBe(other.id);
    await expect(trigger(page)).toHaveAttribute("title", other.email);
    await trigger(page).click();
    await menu(page).getByRole("menuitem", { name: "Профиль", exact: true }).click();
    await expect(page.getByLabel("Имя", { exact: true })).toHaveValue("Борис");
  } finally { release(); }
});

test("the avatar menu is absent before login and opens the real profile without reloading", async ({ page, account }) => {
  await page.goto("login");
  await expect(page.locator(".auth-panel")).toBeVisible();
  await expect(trigger(page)).toHaveCount(0);
  await login(page, account);
  await expect(trigger(page).locator(".avatar")).toHaveText("А");
  await expect(trigger(page)).toHaveAttribute("aria-expanded", "false");
  await expect(trigger(page)).toHaveAttribute("aria-haspopup", "menu");
  await expect(trigger(page)).toHaveClass(/tmc-square-button/);
  await page.evaluate(() => { document.body.dataset.userMenuDocument = "preserved"; });
  await trigger(page).click();
  await expect(menu(page).getByRole("menuitem")).toHaveText(["Профиль", "API-токены", "Выйти"]);
  await expect(menu(page)).toHaveAttribute("id", (await trigger(page).getAttribute("aria-controls"))!);
  await menu(page).getByRole("menuitem", { name: "Профиль", exact: true }).click();
  await expect(page).toHaveURL(/\/profile$/);
  await expect(page.getByLabel("Имя", { exact: true })).toHaveValue(account.first_name);
  await expect(page.locator("body")).toHaveAttribute("data-user-menu-document", "preserved");
  await expect(menu(page)).toHaveCount(0);
  await expect(trigger(page)).toHaveAttribute("aria-expanded", "false");
  await trigger(page).click();
  await expect(menu(page).getByRole("menuitem", { name: "Профиль" })).toHaveAttribute("aria-current", "page");
  await page.getByRole("navigation", { name: "Навигация кабинета" }).getByRole("link", { name: "Обзор" }).click();
  await expect(menu(page)).toHaveCount(0);
});

test("keyboard navigation, Escape, Tab, repeated click and outside presses dismiss the menu", async ({ page, account, browserName }) => {
  const next = browserName === "webkit" ? "Alt+Tab" : "Tab";
  await login(page, account);
  const button = trigger(page);
  await button.focus();
  await page.keyboard.press("Enter");
  const profile = menu(page).getByRole("menuitem", { name: "Профиль", exact: true });
  const tokens = menu(page).getByRole("menuitem", { name: "API-токены", exact: true });
  const logout = menu(page).getByRole("menuitem", { name: "Выйти", exact: true });
  await expect(profile).toBeFocused();
  await page.keyboard.press("ArrowUp");
  await expect(logout).toBeFocused();
  await page.keyboard.press("ArrowDown");
  await expect(profile).toBeFocused();
  await page.keyboard.press("End");
  await expect(logout).toBeFocused();
  await page.keyboard.press("Home");
  await expect(profile).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(button).toBeFocused();
  await expect(menu(page)).toHaveCount(0);
  await page.keyboard.press("Space");
  await expect(profile).toBeFocused();
  await page.keyboard.press(next);
  await expect(tokens).toBeFocused();
  await page.keyboard.press(next);
  await expect(logout).toBeFocused();
  await page.keyboard.press(next);
  await expect(menu(page)).toHaveCount(0);
  await button.focus();
  await page.keyboard.press("ArrowUp");
  await expect(logout).toBeFocused();
  await page.keyboard.press("Escape");
  await page.keyboard.press("ArrowDown");
  await expect(profile).toBeFocused();
  await page.getByRole("heading", { name: `Привет, ${account.first_name}` }).click();
  await expect(menu(page)).toHaveCount(0);
  await button.click();
  await expect(menu(page)).toBeVisible();
  await button.click();
  await expect(menu(page)).toHaveCount(0);
});

for (const language of ["ru", "en"]) for (const theme of ["dark", "light"]) {
  test(`responsive avatar and menu stay usable ${language} ${theme}`, async ({ page, account, isMobile }) => {
    test.setTimeout(120_000);
    await page.addInitScript(theme => localStorage.setItem("tmc-theme", theme), theme);
    await login(page, account);
    if (language === "en") await selectLanguage(page, "en");
    await page.emulateMedia({ reducedMotion: "reduce" });
    for (const width of [280, 320, 360, 380, 381, 390, 480, 481, 580, 581, 800, 801, 860, 861, 1120, 1121, 1440, 1920]) {
      await page.setViewportSize({ width, height: 800 });
      const button = trigger(page);
      await expect(button).toBeInViewport({ ratio: 1 });
      const box = (await button.boundingBox())!;
      expect(box.width).toBe(width <= 480 ? 56 : width <= 580 ? 68 : 74);
      expect(box.height).toBe(width <= 580 ? 68 : 74);
      const circle = button.locator(".avatar");
      await expect(circle).toHaveCSS("border-radius", "50%");
      const circleBox = (await circle.boundingBox())!;
      expect(circleBox.width).toBe(circleBox.height);
      expect(circleBox.x + circleBox.width / 2).toBeCloseTo(box.x + box.width / 2, 1);
      expect(circleBox.y + circleBox.height / 2).toBeCloseTo(box.y + box.height / 2, 1);
      if (isMobile) await button.tap(); else await button.click();
      await expect(menu(page).getByRole("menuitem")).toHaveText(language === "ru" ? ["Профиль", "API-токены", "Выйти"] : ["Profile", "API tokens", "Sign out"]);
      await expect(menu(page)).toBeInViewport({ ratio: 1 });
      await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
      // Resizing through the desktop breakpoint must not disable or dismiss this menu.
      await page.setViewportSize({ width: width <= 1120 ? 1440 : 390, height: 800 });
      await expect(menu(page)).toBeVisible();
      await button.press("Escape");
      await expect(menu(page)).toHaveCount(0);
    }
  });
}

test("profile upload, page reload and removal immediately update the circular header avatar", async ({ page, account }) => {
  await login(page, account);
  await trigger(page).click();
  await menu(page).getByRole("menuitem", { name: "Профиль", exact: true }).click();
  const image = trigger(page).locator("img");
  await expect(image).toHaveCount(0);
  await page.getByLabel("Загрузить аватар").setInputFiles(avatar);
  await expect(image).toBeVisible();
  await expect.poll(() => image.evaluate((node: HTMLImageElement) => node.naturalWidth)).toBeGreaterThan(0);
  await expect(image).toHaveCSS("object-fit", "cover");
  await expect(image).toHaveAttribute("src", /\/users\/me\/avatar\?v=.+/);
  const src = await image.getAttribute("src");
  await page.reload();
  await expect(image).toHaveAttribute("src", src!);
  await expect(image).toBeVisible();
  await page.getByRole("button", { name: "Удалить аватар" }).click();
  await expect(image).toHaveCount(0);
  await expect(trigger(page).locator(".avatar")).toHaveText("А");
  await page.getByLabel("Имя", { exact: true }).fill("Борис");
  await page.getByRole("button", { name: "Сохранить изменения" }).click();
  await expect(trigger(page).locator(".avatar")).toHaveText("Б");
});

test("a failed avatar download uses the placeholder and recovers after a new upload", async ({ page, account }) => {
  await login(page, account);
  await trigger(page).click();
  await menu(page).getByRole("menuitem", { name: "Профиль", exact: true }).click();
  let failedReads = 0;
  await page.route(`${API}/users/me/avatar?*`, route => {
    failedReads++;
    return route.fulfill({ status: 404 });
  });
  const uploaded = page.waitForResponse(response => response.url() === `${API}/users/me/avatar` && response.request().method() === "PUT");
  await page.getByLabel("Загрузить аватар").setInputFiles(avatar);
  const version = (await (await uploaded).json()).avatar_version;
  await expect(page.locator(".avatar-editor").getByRole("status")).toBeVisible();
  await expect.poll(() => failedReads).toBeGreaterThan(0);
  await expect(trigger(page).locator("img")).toHaveCount(0);
  await expect(trigger(page).locator(".avatar")).toHaveText("А");
  await page.unroute(`${API}/users/me/avatar?*`);
  // Identical bytes keep the same avatar hash; an explicit re-upload must retry.
  await page.getByLabel("Загрузить аватар").setInputFiles(avatar);
  await expect(trigger(page).locator("img")).toBeVisible();
  expect(new URL((await trigger(page).locator("img").getAttribute("src"))!).searchParams.get("v")).toBe(version);
  await expect.poll(() => trigger(page).locator("img").evaluate((node: HTMLImageElement) => node.naturalWidth)).toBeGreaterThan(0);
});

for (const intervening of ["same image", "removal", "different image"] as const) {
  test(`an avatar save supersedes an older pending download after ${intervening}`, async ({ page, account }) => {
    await login(page, account);
    await trigger(page).click();
    await menu(page).getByRole("menuitem", { name: "Профиль", exact: true }).click();
    let releaseOldReads!: () => void;
    const oldReads = new Promise<void>(resolve => { releaseOldReads = resolve; });
    let holdReads = true;
    let pendingReads = 0;
    let failedReads = 0;
    await page.route(`${API}/users/me/avatar?*`, async route => {
      if (!holdReads) return route.continue();
      pendingReads++;
      await oldReads;
      await route.fulfill({ status: 404 });
      failedReads++;
    });
    const upload = async (file = avatar) => {
      const response = page.waitForResponse(response => response.url() === `${API}/users/me/avatar` && response.request().method() === "PUT");
      await page.getByLabel("Загрузить аватар").setInputFiles(file);
      const user = await (await response).json();
      await expect(page.locator(".avatar-editor").getByRole("status")).toBeVisible();
      return user.avatar_version;
    };
    try {
      const version = await upload();
      await expect.poll(() => pendingReads).toBeGreaterThan(0);
      holdReads = false;
      if (intervening === "removal") {
        await page.getByRole("button", { name: "Удалить аватар" }).click();
        await expect(trigger(page).locator("img")).toHaveCount(0);
      } else if (intervening === "different image") {
        const other = { ...avatar, buffer: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4wC/yHwAFGQITCimxRwAAAABJRU5ErkJggg==", "base64") };
        expect(await upload(other)).not.toBe(version);
        await expect.poll(() => trigger(page).locator("img").evaluate((node: HTMLImageElement) => node.naturalWidth)).toBeGreaterThan(0);
      }
      expect(await upload()).toBe(version);
      releaseOldReads();
      await expect.poll(() => failedReads).toBe(pendingReads);
      const image = trigger(page).locator("img");
      await expect(image).toBeVisible();
      await expect.poll(() => image.evaluate((node: HTMLImageElement) => node.naturalWidth)).toBeGreaterThan(0);
    } finally {
      releaseOldReads();
    }
  });
}

test("sign out revokes the browser cookie, hides the avatar and protects routes and history", async ({ page, request, account }) => {
  await login(page, account);
  const cookie = (await page.context().cookies()).find(cookie => cookie.name === "tmc_session")!;
  await trigger(page).click();
  const response = page.waitForResponse(response => response.url() === `${API}/auth/session` && response.request().method() === "DELETE");
  await menu(page).getByRole("menuitem", { name: "Выйти", exact: true }).click();
  expect((await response).status()).toBe(204);
  await expect(page.getByRole("heading", { name: "С возвращением." })).toBeVisible();
  await expect(trigger(page)).toHaveCount(0);
  expect((await request.get(`${API}/auth/session`, { headers: { Cookie: `${cookie.name}=${cookie.value}` } })).status()).toBe(401);
  await page.goBack();
  await expect(trigger(page)).toHaveCount(0);
  await page.goto("profile");
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.locator("input[name=password]")).toHaveValue("");
});

test("failed sign out keeps the session and permits retry while duplicate requests are blocked", async ({ page, account }) => {
  await login(page, account);
  let calls = 0;
  let release!: () => void;
  const held = new Promise<void>(resolve => { release = resolve; });
  await page.route(`${API}/auth/session`, async route => {
    if (route.request().method() !== "DELETE") return route.continue();
    calls++;
    await held;
    await route.fulfill({ status: 503, json: { detail: "Test outage" } });
  });
  await trigger(page).click();
  await menu(page).getByRole("menuitem", { name: "Выйти", exact: true }).click();
  await expect(menu(page).getByRole("menuitem", { name: "Подождите…", exact: true })).toBeDisabled();
  await expect(page.getByRole("menuitem", { name: "Подождите…", exact: true })).toHaveCount(1);
  await expect.poll(() => calls).toBe(1);
  release();
  await expect(menu(page).getByRole("alert")).toBeVisible();
  await expect(menu(page).getByRole("menuitem", { name: "Выйти", exact: true })).toBeEnabled();
  await expect(page.getByRole("heading", { name: `Привет, ${account.first_name}` })).toBeVisible();
  await page.unroute(`${API}/auth/session`);
  await menu(page).getByRole("menuitem", { name: "Выйти", exact: true }).click();
  await expect(page).toHaveURL(/\/login$/);
  await expect(trigger(page)).toHaveCount(0);
});

test("an expired session can still be dismissed by signing out", async ({ page, account }) => {
  await login(page, account);
  expect((await page.request.delete(`${API}/auth/session`, {
    headers: { Origin: "http://127.0.0.1:18101", "X-Requested-With": "ThrongDashboard" },
  })).status()).toBe(204);
  await trigger(page).click();
  await menu(page).getByRole("menuitem", { name: "Выйти", exact: true }).click();
  await expect(page).toHaveURL(/\/login$/);
  await expect(trigger(page)).toHaveCount(0);
  await page.reload();
  await expect(trigger(page)).toHaveCount(0);
});

test("short phone, tablet and desktop viewports keep both menu actions reachable", async ({ page, account }) => {
  await login(page, account);
  for (const width of [320, 844, 1440]) for (const height of [240, 320]) {
    await page.setViewportSize({ width, height });
    await trigger(page).focus();
    await page.keyboard.press("ArrowDown");
    await expect(menu(page).getByRole("menuitem", { name: "Профиль", exact: true })).toBeInViewport({ ratio: 1 });
    await page.keyboard.press("End");
    await expect(menu(page).getByRole("menuitem", { name: "Выйти", exact: true })).toBeInViewport({ ratio: 1 });
    await expect(menu(page)).toBeInViewport({ ratio: 1 });
    await page.keyboard.press("Escape");
    await expect(trigger(page)).toBeFocused();
  }
});

test("the placeholder falls back to the account identifier when the name and nickname are empty", async ({ page, account }) => {
  await login(page, account);
  await page.goto("profile");
  await page.getByLabel("Имя", { exact: true }).fill("");
  await page.getByLabel("Ник", { exact: true }).fill("");
  await page.getByRole("button", { name: "Сохранить изменения" }).click();
  await expect(page.getByRole("status")).toHaveText("Изменения сохранены.");
  await expect(trigger(page).locator(".avatar")).toHaveText(account.email[0].toUpperCase());
});

for (const mutation of ["profile", "upload avatar", "remove avatar"] as const) {
  test(`a late ${mutation} response cannot restore the signed-out session`, async ({ page, account }) => {
    await login(page, account);
    await page.goto("profile");
    if (mutation === "remove avatar") {
      await page.getByLabel("Загрузить аватар").setInputFiles(avatar);
      await expect(trigger(page).locator("img")).toBeVisible();
    }
    const url = mutation === "profile" ? `${API}/users/${account.id}` : `${API}/users/me/avatar`;
    const method = mutation === "profile" ? "PATCH" : mutation === "upload avatar" ? "PUT" : "DELETE";
    let release!: () => void;
    let received!: () => void;
    const held = new Promise<void>(resolve => { release = resolve; });
    const requested = new Promise<void>(resolve => { received = resolve; });
    await page.route(url, async route => {
      if (route.request().method() !== method) return route.continue();
      // WebKit interception drops File-backed bodies; forward the same fixture.
      const response = await route.fetch(mutation === "upload avatar" ? { postData: png } : {});
      expect(response.ok()).toBe(true);
      received();
      await held;
      await route.fulfill({ response });
    });
    try {
      if (mutation === "profile") {
        await page.getByLabel("Имя", { exact: true }).fill("Обновлённое имя");
        await page.getByRole("button", { name: "Сохранить изменения" }).click();
      } else if (mutation === "upload avatar") await page.getByLabel("Загрузить аватар").setInputFiles(avatar);
      else await page.getByRole("button", { name: "Удалить аватар" }).click();
      await requested;
      await trigger(page).click();
      await menu(page).getByRole("menuitem", { name: "Выйти", exact: true }).click();
      await expect(page).toHaveURL(/\/login$/);
      await expect(trigger(page)).toHaveCount(0);
      await page.evaluate(() => {
        (window as any).restoredSignedOutUser = false;
        const observer = new MutationObserver(() => {
          if (document.querySelector(".user-menu, .app-shell")) (window as any).restoredSignedOutUser = true;
        });
        observer.observe(document.body, { childList: true, subtree: true });
      });
      const completed = page.waitForResponse(response => response.url() === url && response.request().method() === method);
      release();
      await (await completed).finished();
      await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
      expect(await page.evaluate(() => (window as any).restoredSignedOutUser)).toBe(false);
      await expect(page).toHaveURL(/\/login$/);
      await expect(trigger(page)).toHaveCount(0);
      expect((await page.request.get(`${API}/auth/session`)).status()).toBe(401);
    } finally { release(); }
  });
}

test("keyboard focus and Escape remain usable while sign out is pending or fails", async ({ page, account }) => {
  await login(page, account);
  let release!: () => void;
  const held = new Promise<void>(resolve => { release = resolve; });
  await page.route(`${API}/auth/session`, async route => {
    if (route.request().method() !== "DELETE") return route.continue();
    await held;
    await route.fulfill({ status: 503, json: { detail: "Test outage" } });
  });
  try {
    await trigger(page).focus();
    await page.keyboard.press("Enter");
    await page.keyboard.press("End");
    await page.keyboard.press("Enter");
    await expect(menu(page).getByRole("menuitem", { name: "Подождите…" })).toBeDisabled();
    await expect.poll(() => page.evaluate(() => !!document.activeElement?.closest(".user-menu"))).toBe(true);
    await page.keyboard.press("Escape");
    await expect(menu(page)).toHaveCount(0);
    await expect(trigger(page)).toBeFocused();
    await page.keyboard.press("ArrowDown");
    release();
    await expect(menu(page).getByRole("alert")).toBeVisible();
    await page.keyboard.press("End");
    await expect(menu(page).getByRole("menuitem", { name: "Выйти", exact: true })).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(menu(page)).toHaveCount(0);
  } finally { release(); }
});

test("Escape dismisses a menu opened by a normal pointer click", async ({ page, account }) => {
  await login(page, account);
  await trigger(page).click();
  await expect(menu(page)).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(menu(page)).toHaveCount(0);
  await expect(trigger(page)).toBeFocused();
});

test("a response from the previous login cannot overwrite the same account's new profile", async ({ page, account }) => {
  await login(page, account);
  await page.goto("profile");
  const url = `${API}/users/${account.id}`;
  let release!: () => void;
  let received!: () => void;
  let intercepted = false;
  const held = new Promise<void>(resolve => { release = resolve; });
  const requested = new Promise<void>(resolve => { received = resolve; });
  await page.route(url, async route => {
    if (route.request().method() !== "PATCH" || intercepted) return route.continue();
    intercepted = true;
    const response = await route.fetch();
    expect(response.ok()).toBe(true);
    received();
    await held;
    await route.fulfill({ response });
  });
  try {
    await page.getByLabel("Имя", { exact: true }).fill("Старое имя");
    await page.getByRole("button", { name: "Сохранить изменения" }).click();
    await requested;
    await trigger(page).click();
    await menu(page).getByRole("menuitem", { name: "Выйти", exact: true }).click();
    await expect(page).toHaveURL(/\/login$/);
    // Stay in the same document: a reload would abort the stale request.
    await page.getByLabel("Email или ник").fill(account.email);
    await page.getByLabel("Пароль", { exact: true }).fill(password);
    await page.getByRole("button", { name: "Войти", exact: true }).click();
    await expect(trigger(page).locator(".avatar")).toHaveText("С");
    await trigger(page).click();
    await menu(page).getByRole("menuitem", { name: "Профиль", exact: true }).click();
    await page.getByLabel("Имя", { exact: true }).fill("Новое имя");
    await page.getByRole("button", { name: "Сохранить изменения" }).click();
    await expect(trigger(page).locator(".avatar")).toHaveText("Н");
    const completed = page.waitForResponse(response => response.url() === url && response.request().method() === "PATCH");
    release();
    await (await completed).finished();
    await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
    await expect(trigger(page).locator(".avatar")).toHaveText("Н");
    await expect(page.getByLabel("Имя", { exact: true })).toHaveValue("Новое имя");
    expect((await (await page.request.get(`${API}/auth/session`)).json()).first_name).toBe("Новое имя");
  } finally { release(); }
});

test("a late unauthorized profile response cannot expire a newer login", async ({ page, account }) => {
  await login(page, account);
  const profileLoaded = page.waitForResponse(response => response.url() === `${API}/github/status` && response.status() === 200);
  await page.goto("profile");
  await (await profileLoaded).finished();
  await expect(page.getByLabel("Имя", { exact: true })).toBeVisible();
  expect((await page.request.delete(`${API}/auth/session`, {
    headers: { Origin: "http://127.0.0.1:18101", "X-Requested-With": "ThrongDashboard" },
  })).status()).toBe(204);
  const url = `${API}/users/${account.id}`;
  let release!: () => void;
  let received!: () => void;
  const held = new Promise<void>(resolve => { release = resolve; });
  const requested = new Promise<void>(resolve => { received = resolve; });
  await page.route(url, async route => {
    if (route.request().method() !== "PATCH") return route.continue();
    const response = await route.fetch();
    expect(response.status()).toBe(401);
    received();
    await held;
    await route.fulfill({ response });
  });
  try {
    await page.getByLabel("Имя", { exact: true }).fill("Старый запрос");
    await page.getByRole("button", { name: "Сохранить изменения" }).click();
    await requested;
    await trigger(page).click();
    await menu(page).getByRole("menuitem", { name: "Выйти", exact: true }).click();
    await expect(page).toHaveURL(/\/login$/);
    await page.getByLabel("Email или ник").fill(account.email);
    await page.getByLabel("Пароль", { exact: true }).fill(password);
    await page.getByRole("button", { name: "Войти", exact: true }).click();
    await expect(trigger(page).locator(".avatar")).toHaveText("А");
    await expect(page.getByLabel("Имя", { exact: true })).toHaveValue(account.first_name);
    const completed = page.waitForResponse(response => response.url() === url && response.request().method() === "PATCH");
    release();
    await (await completed).finished();
    await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
    await expect(trigger(page)).toBeVisible();
    await expect(page).toHaveURL(/\/profile$/);
    expect((await page.request.get(`${API}/auth/session`)).status()).toBe(200);
  } finally { release(); }
});

test("a sign-in in another tab survives an older pending sign-out response", async ({ page, account, context }) => {
  await login(page, account);
  let release!: () => void;
  let received!: () => void;
  let signInStarted = false;
  const held = new Promise<void>(resolve => { release = resolve; });
  const requested = new Promise<void>(resolve => { received = resolve; });
  await context.route(`${API}/auth/session`, async route => {
    const method = route.request().method();
    if (method === "POST") signInStarted = true;
    if (method !== "DELETE") return route.continue();
    const response = await route.fetch();
    expect(response.status()).toBe(204);
    received();
    await held;
    await route.fulfill({ response });
  });
  const other = await context.newPage();
  try {
    await trigger(page).click();
    await menu(page).getByRole("menuitem", { name: "Выйти", exact: true }).click();
    await requested;
    await other.goto("login");
    await other.getByLabel("Email или ник").fill(account.email);
    await other.getByLabel("Пароль", { exact: true }).fill(password);
    const signedIn = other.waitForResponse(response => response.url() === `${API}/auth/session` && response.request().method() === "POST");
    await other.getByRole("button", { name: "Войти", exact: true }).click();
    await other.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
    // Exercise a completed concurrent login if allowed, or release the old
    // response first when the client queues cookie-changing operations.
    if (signInStarted) await (await signedIn).finished();
    const signedOut = page.waitForResponse(response => response.url() === `${API}/auth/session` && response.request().method() === "DELETE");
    release();
    await (await signedOut).finished();
    await (await signedIn).finished();
    await expect(trigger(other)).toBeVisible();
    expect((await other.request.get(`${API}/auth/session`)).status()).toBe(200);
    await other.reload();
    await expect(trigger(other)).toBeVisible();
  } finally { release(); await other.close(); }
});
