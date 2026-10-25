import {
  API,
  addComputer,
  expect,
  headers,
  makeAccount,
  password,
  startSession,
  test,
  type Account,
} from "./fixtures";
import { GiB, historyNow, reserve, seedHistory } from "./resources";
import { resolve } from "node:path";
import { openLanguages } from "./header-controls";

/** Owner and issuer context, permission choices and a full-length safe secret must stay legible in every theme and viewport. */
test("owner-aware API token registry and issuance dialog", async ({ page, request, account }, info) => {
  const { language, theme } = info.project.metadata;
  const exactPixels = { threshold: 0.01, maxDiffPixels: 0 };
  await page.addInitScript(({ language, theme }) => {
    localStorage.setItem("tmc-language", language);
    localStorage.setItem("tmc-theme", theme);
  }, { language, theme });
  await page.clock.setFixedTime(historyNow);
  const issuer = await makeAccount(request, "Дмитрий");
  const studioOwner = await makeAccount(request, "Ирина");
  for (const [actor, last_name] of [[account, "Волкова"], [issuer, "Орлов"], [studioOwner, "Миронова"]] as const) {
    expect((await request.patch(`${API}/users/${actor.id}`, { headers: headers(actor), data: { last_name } })).status()).toBe(200);
  }
  const response = await request.post(`${API}/companies`, { headers: headers(account), data: { name: "Лаборатория" } });
  expect(response.status()).toBe(201);
  const lab = await response.json();
  const studioResponse = await request.post(`${API}/companies`, { headers: headers(studioOwner), data: { name: "Студия" } });
  expect(studioResponse.status()).toBe(201);
  const studio = await studioResponse.json();
  expect((await request.post(`${API}/memberships`, { headers: headers(studioOwner), data: { company_id: studio.id, user_id: account.id, role: "user", permissions: ["tokens.read"] } })).status()).toBe(201);
  expect((await request.post(`${API}/memberships`, { headers: headers(account), data: { company_id: lab.id, user_id: issuer.id, role: "admin" } })).status()).toBe(201);
  const records = [
    [account, { user_id: account.id }, "Рабочий Mac", ["read", "execute"]],
    [account, { company_id: lab.id }, "CI лаборатории", ["read", "execute"]],
    [issuer, { company_id: lab.id }, "Отчёты команды", ["read"]],
    [studioOwner, { company_id: studio.id }, "Аналитика Студии", ["read"]],
  ] as const;
  for (const [actor, owner, name, scopes] of records) expect((await request.post(`${API}/api-tokens`, { headers: headers(actor), data: { ...owner, name, scopes } })).status()).toBe(201);
  await page.route(`${API}/api-tokens**`, async route => {
    const response = await route.fetch();
    const body = await response.json();
    const fixedDates = { created_at: "2026-10-07T12:00:00Z", expires_at: "2026-11-06T12:00:00Z" };
    if (Array.isArray(body)) {
      const recordNames = records.map(value => value[2]) as readonly string[];
      body.sort((a, b) => recordNames.indexOf(a.name) - recordNames.indexOf(b.name));
      return route.fulfill({ response, json: body.map(value => ({ ...value, ...fixedDates })) });
    }
    expect(body.issuer.last_name).toBe("Волкова");
    return route.fulfill({ response, json: { ...body, ...fixedDates, token: `ORG-${"0123456789abcdef".repeat(4)}` } });
  });
  expect((await page.request.post(`${API}/auth/session`, {
    headers: { Origin: "http://127.0.0.1:18101", "X-Requested-With": "ThrongDashboard" },
    data: { login: account.email, password },
  })).status()).toBe(200);
  await page.goto("api-tokens");
  await expect(page.locator(".token-registry-row")).toHaveCount(4);
  await page.evaluate(() => document.fonts.ready);
  await expect(page).toHaveScreenshot("api-tokens.png", { fullPage: true, ...exactPixels });
  await page.getByRole("button", { name: language === "ru" ? "Создать токен" : "Create token", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toHaveScreenshot("api-token-personal-create.png", exactPixels);
  await dialog.getByRole("radio", { name: language === "ru" ? /Организация/ : /Organization/ }).check();
  await dialog.getByLabel(language === "ru" ? "Название токена" : "Token name", { exact: true }).fill("Ночные сборки");
  await expect(dialog).toHaveScreenshot("api-token-create.png", exactPixels);
  await expect(page).toHaveScreenshot("api-token-overlay.png", exactPixels);
  await dialog.getByRole("button", { name: language === "ru" ? "Создать токен организации" : "Create organization token", exact: true }).click();
  await expect(dialog.getByRole("heading", { name: language === "ru" ? "Токен создан" : "Token created", exact: true })).toBeVisible();
  await expect(dialog.locator(".token-owner-summary")).toContainText("Алиса Волкова");
  await expect(dialog).toHaveScreenshot("api-token-issued.png", exactPixels);
});

test("user menu with the placeholder and a real uploaded avatar", async ({ page, account }, info) => {
  const { language, theme } = info.project.metadata;
  await page.addInitScript(({ language, theme }) => {
    localStorage.setItem("tmc-language", language);
    localStorage.setItem("tmc-theme", theme);
  }, { language, theme });
  expect((await page.request.post(`${API}/auth/session`, {
    headers: { Origin: "http://127.0.0.1:18101", "X-Requested-With": "ThrongDashboard" },
    data: { login: account.email, password },
  })).status()).toBe(200);
  await page.goto("/");
  const trigger = page.locator(".user-menu .tmc-square-button");
  const exactPixels = { threshold: 0, maxDiffPixels: 0 };
  // Chromium's clipped raster edges vary by up to 2/255 per color channel
  // between processes. Keep a zero pixel budget with only that color tolerance.
  const imagePixels = { threshold: 0.01, maxDiffPixels: 0 };
  await expect(trigger).toBeVisible();
  await page.evaluate(() => document.fonts.ready);
  await expect(trigger).toHaveScreenshot("header-avatar-placeholder.png", exactPixels);
  await trigger.click();
  const panel = page.getByRole("menu");
  await expect(panel).toBeVisible();
  const buttonBox = (await trigger.boundingBox())!;
  const panelBox = (await panel.boundingBox())!;
  const x = Math.max(0, Math.min(buttonBox.x, panelBox.x) - 20);
  const y = Math.max(0, buttonBox.y - 8);
  const clip = { x, y, width: Math.min(page.viewportSize()!.width, buttonBox.x + buttonBox.width + 20) - x, height: panelBox.y + panelBox.height + 20 - y };
  await expect(page).toHaveScreenshot("header-user-menu-placeholder.png", { clip, ...exactPixels });
  expect((await page.request.put(`${API}/users/me/avatar`, {
    headers: { Origin: "http://127.0.0.1:18101", "X-Requested-With": "ThrongDashboard", "Content-Type": "image/png" },
    data: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jWZkAAAAASUVORK5CYII=", "base64"),
  })).status()).toBe(200);
  await page.reload();
  await expect(trigger.locator("img")).toBeVisible();
  await expect.poll(() => trigger.locator("img").evaluate((image: HTMLImageElement) => image.naturalWidth)).toBeGreaterThan(0);
  await page.evaluate(() => document.fonts.ready);
  await expect(trigger).toHaveScreenshot("header-avatar-image.png", imagePixels);
  await trigger.click();
  await expect(panel).toBeVisible();
  await expect(page).toHaveScreenshot("header-user-menu-image.png", { clip, ...imagePixels });
});

test("the shared language dropdown in both themes and layouts", async ({ page }, info) => {
  const { language, theme } = info.project.metadata;
  await page.addInitScript(({ language, theme }) => {
    localStorage.setItem("tmc-language", language);
    localStorage.setItem("tmc-theme", theme);
  }, { language, theme });
  await page.goto("login");
  await expect(page.locator(".auth-panel")).toBeVisible();
  await page.evaluate(() => document.fonts.ready);
  const dropdown = await openLanguages(page);
  const summary = (await dropdown.locator("summary").boundingBox())!;
  const list = (await dropdown.locator(".tmc-language-switcher__list").boundingBox())!;
  await expect(page).toHaveScreenshot("header-language-open.png", { clip: {
    x: Math.min(summary.x, list.x) - 4,
    y: summary.y - 4,
    width: Math.max(summary.width, list.width) + 8,
    height: list.y + list.height - summary.y + 8,
  } });
});

test("auth, every product page, populated and empty pools, and confirmation dialog", async ({
  page,
  request,
}, info) => {
  const { language, theme } = info.project.metadata;
  await page.addInitScript(
    ({ language, theme }) => {
      localStorage.setItem("tmc-language", language);
      localStorage.setItem("tmc-theme", theme);
    },
    { language, theme },
  );
  await page.clock.setFixedTime(historyNow);
  async function settleFonts() {
    await page.evaluate(async () => {
      await Promise.all(Array.from(document.fonts, (face) => face.load()));
      await document.fonts.ready;
      await new Promise<void>((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
      );
    });
  }
  async function screenshot(name: string) {
    await expect(
      page.locator("h1:visible, #auth-title:visible").first(),
    ).toBeVisible();
    await expect(page.locator(".loading")).toHaveCount(0);
    await settleFonts();
    if (name.startsWith("computer-")) {
      // BenchmarkHistory scrolls and focuses in an animation frame after mount.
      // Settle that frame before resetting the full-page capture to the top.
      await page.evaluate(async () => {
        if (document.activeElement instanceof HTMLElement)
          document.activeElement.blur();
        window.scrollTo({ top: 0, left: 0, behavior: "instant" });
        await new Promise<void>((resolve) =>
          requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
        );
      });
      await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(0);
    }
    await expect(page).toHaveScreenshot(`${name}.png`, { fullPage: true });
  }
  await page.goto("login");
  await screenshot("login");
  await expect(page.getByRole("banner")).toHaveScreenshot("header-compact.png");
  if (info.project.name.endsWith("desktop")) {
    for (const width of [900, 320]) {
      await page.setViewportSize({ width, height: 1000 });
      await settleFonts();
      await expect(page.getByRole("banner")).toHaveScreenshot(`header-${width}.png`);
    }
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.locator(".tmc-brand__wordmark").hover();
    await expect(page.locator(".tmc-brand__word-reveal").last()).toHaveCSS(
      "opacity",
      "1",
    );
    await expect(page.locator(".site-header")).toHaveScreenshot(
      "brand-expanded.png",
    );
  }
  await page.goto("register");
  await screenshot("register");
  const nickname = `alice_${info.project.name.replaceAll("-", "_")}`;
  const email = `${nickname}@example.com`;
  const created = await request.post(`${API}/users`, {
    data: {
      email,
      password,
      nickname,
      first_name: "Алиса",
      last_name: "Волкова",
    },
  });
  expect(created.status()).toBe(201);
  const auth = await request.post(`${API}/auth/login`, {
    data: { login: email, password },
  });
  expect(auth.status()).toBe(200);
  const account: Account = {
    ...(await created.json()),
    token: (await auth.json()).access_token,
  };
  const response = await page.request.post(
    "http://127.0.0.1:18100/v1/auth/session",
    {
      headers: {
        Origin: "http://127.0.0.1:18101",
        "X-Requested-With": "ThrongDashboard",
      },
      data: { login: email, password },
    },
  );
  expect(response.status()).toBe(200);
  await page.goto("");
  await screenshot("overview-empty");
  await page.goto("power-cells");
  await screenshot("power-cells-empty");
  const first = await addComputer(request, account, "fixture-node-01");
  const second = await addComputer(request, account, "fixture-node-02");
  // The API orders by UUID. Give the first returned node the same visible data
  // every run, using real API writes rather than reordering the browser DOM.
  const [personal, mini] = [first, second].sort((a, b) =>
    a.id.localeCompare(b.id),
  );
  for (const [id, data] of [
    [personal.id, { name: "workstation-01" }],
    [
      mini.id,
      { name: "mini-node-02", cpu_millis: 500, memory_bytes: 1.5 * GiB },
    ],
  ] as const) {
    expect(
      (
        await request.patch(`${API}/computers/${id}`, {
          headers: headers(account),
          data,
        })
      ).status(),
    ).toBe(200);
  }
  for (const [version, name, number] of [
    ["1.2.0", "single_core", 1100],
    ["1.2.0", "legacy_only", 875.25],
    ["1.9.0", "single_core", 1200.25],
    ["1.10.0", "single_core", 1250.5],
    ["1.10.0", "multi_core", 9350.75],
    ["1.10.0", "memory_copy", 0],
  ] as const) {
    const result = await request.post(`${API}/benchmark-results`, {
      headers: headers(account),
      data: {
        computer_id: personal.id,
        benchmarker_version: version,
        benchmark_name: name,
        number,
      },
    });
    expect(result.status()).toBe(201);
  }
  await reserve(request, account, 1250, 2 * GiB);
  const companyResponse = await request.post(`${API}/companies`, {
    headers: headers(account),
    data: { name: "Исследовательская лаборатория" },
  });
  expect(companyResponse.status()).toBe(201);
  const company = await companyResponse.json();
  const shared = await addComputer(
    request,
    account,
    "lab-node-01",
    company.id,
    true,
    { cpu_millis: 4000, memory_bytes: 12 * GiB },
  );
  await seedHistory(request, personal.id);
  await seedHistory(request, shared.id);
  await page.goto("");
  await screenshot("overview");
  await page.goto("power-cells");
  await expect(page.locator("[data-series]")).toHaveCount(8);
  await screenshot("power-cells");
  await page.goto(`power-cells/computers?user_id=${account.id}`);
  await expect(page.locator("tbody tr")).toHaveCount(2);
  await screenshot("computers");
  await page.getByRole("link", { name: "workstation-01", exact: true }).click();
  await expect(page.locator(".benchmark-card")).toHaveCount(3);
  await screenshot("computer-details");
  await page.getByRole("link", { name: /single_core/ }).click();
  await expect(page.locator(".benchmark-history-table tbody tr")).toHaveCount(
    3,
  );
  await screenshot("computer-benchmark-history");
  await page.locator(".benchmark-archive summary").click();
  await page
    .getByLabel(language === "ru" ? "Выберите бенчмарк" : "Choose a benchmark")
    .selectOption("legacy_only");
  await expect(page.locator(".benchmark-history-table tbody th")).toHaveText([
    "1.2.0",
  ]);
  await screenshot("computer-benchmark-archive");
  await page.goto(`power-cells/computers/${mini.id}?user_id=${account.id}`);
  await screenshot("computer-benchmarks-empty");
  await page.goto("organizations");
  await expect(page.getByRole("heading", { name: company.name })).toBeVisible();
  await screenshot("organizations");
  await page
    .getByRole("button", {
      name: language === "ru" ? "Создать организацию" : "Create organization",
      exact: true,
    })
    .click();
  const createDialog = page.getByRole("dialog");
  await expect(createDialog).toBeVisible();
  await settleFonts();
  await expect(createDialog).toHaveScreenshot("organization-create-dialog.png");
  await page.keyboard.press("Escape");
  await expect(createDialog).not.toBeVisible();
  await page.goto(`organizations/${company.id}`);
  await expect(page.locator(".member-row")).toHaveCount(1);
  await screenshot("organization-members");
  await page.locator(".member-sharing summary").click();
  await screenshot("organization-sharing");
  await page
    .getByRole("button", {
      name: language === "ru" ? "Изменить права" : "Edit permissions",
      exact: true,
    })
    .click();
  const dialog = page.getByRole("dialog");
  await dialog
    .getByLabel(language === "ru" ? "Роль" : "Role", { exact: true })
    .selectOption("user");
  await dialog
    .getByLabel(
      language === "ru" ? "Настроить права отдельно" : "Customize permissions",
    )
    .check();
  // The newly expanded dialog introduces text at additional font sizes/weights.
  // Let font loading and the following layout/paint settle before pixel capture.
  await settleFonts();
  await expect(page).toHaveScreenshot("member-permissions.png");
  await page.keyboard.press("Escape");
  const invited = await request.post(`${API}/invitations`, {
    headers: headers(account),
    data: {
      company_id: company.id,
      permissions: ["members.read", "resources.read"],
    },
  });
  expect(invited.status()).toBe(201);
  await page.goto(`invitations/${(await invited.json()).token}`);
  await expect(page.locator(".invitation-preview")).toBeVisible();
  await expect(page.locator(".loading")).toHaveCount(0);
  await page.evaluate(() => document.fonts.ready);
  // The wall-clock expiry is covered functionally; mask only its changing date for pixel comparison.
  await expect(page).toHaveScreenshot("invitation-preview.png", {
    fullPage: true,
    stylePath: resolve("../../tests/platform/web/e2e/visual-stable.css"),
  });
  await page.goto("profile");
  await screenshot("profile");
  await page
    .getByText(
      language === "ru" ? "Управление аккаунтом" : "Account management",
      {
        exact: true,
      },
    )
    .click();
  await page
    .getByRole("button", {
      name: language === "ru" ? "Удалить аккаунт" : "Delete account",
      exact: true,
    })
    .click();
  await expect(page.getByRole("dialog")).toBeVisible();
  // Capture the viewport: full-page capture of the fixed dialog differs by browser scroll policy.
  await expect(page).toHaveScreenshot("confirmation.png");
  await page.keyboard.press("Escape");
  await page.goto("cloud");
  await screenshot("cloud");
});

/** Repository status, separate settings and immutable revision history must fit every supported layout. */
test("Actions repository catalog, configuration and history", async ({ page, account }, info) => {
  const { actionsPeer, binding, repository } = await import("./actions-peer");
  const { language, theme } = info.project.metadata;
  await page.addInitScript(({ language, theme }) => {
    localStorage.setItem("tmc-language", language); localStorage.setItem("tmc-theme", theme);
  }, { language, theme });
  await page.clock.setFixedTime(historyNow);
  const peer = await actionsPeer(page);
  peer.repositories = [repository, ...["unconfigured", "connected", "disabled"].map((state, index) => ({ ...repository, id: 790 + index, full_name: ["pomponchik/site", "mutating/platform", "mutating/archived-ci"][index], owner_login: index === 0 ? "pomponchik" : "mutating", owner_type: index === 0 ? "personal" : "organization", state }))];
  peer.bindings = [{ ...binding, workflow_profiles: { ".github/workflows/deploy.yml": { cpu_millis: 500, memory_bytes: 1024 ** 3, preparation_seconds: 120, timeout_seconds: 180 } } }, { ...binding, id: "connected", repository_id: 791 }, { ...binding, id: "disabled", repository_id: 792, enabled: false }];
  peer.runs = [{ route_id: binding.id, id: "run", run_id: 42, run_attempt: 1, status: "completed", reason: "github_success", created_at: "2026-10-08T12:00:00Z", url: "https://github.com/mutating/ci-sandbox/actions/runs/42" }, { route_id: "connected", id: "connected-run", run_id: 43, run_attempt: 1, status: "completed", reason: "github_success", started_at: "2026-10-08T12:00:00Z", created_at: "2026-10-08T12:00:00Z", url: "https://github.com/mutating/platform/actions/runs/43" }];
  await startSession(page, account);
  const screenshot = async (name: string) => {
    await expect(page.locator(".loading")).toHaveCount(0); await page.evaluate(() => document.fonts.ready);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await expect(page).toHaveScreenshot(name, { fullPage: true, threshold: 0.01, maxDiffPixels: 0 });
  };
  await page.goto("actions"); await expect(page.locator(".repository-card")).toHaveCount(4);
  await screenshot("actions-catalog.png");
  await page.getByRole("article", { name: repository.full_name }).getByRole("link", { name: language === "ru" ? "Настроить" : "Configure", exact: true }).click();
  await expect(page.getByLabel("Workflow YAML", { exact: true })).toBeVisible();
  await screenshot("actions-repository.png");
  await page.getByLabel("CPU", { exact: true }).fill("1");
  await page.getByRole("button", { name: language === "ru" ? "Сохранить настройки" : "Save settings", exact: true }).click();
  await expect(page.getByText(language === "ru" ? "Настройки сохранены." : "Settings saved.", { exact: true })).toBeVisible();
  await page.getByRole("link", { name: language === "ru" ? "История изменений" : "Change history", exact: true }).click();
  await expect(page.locator(".binding-revision")).toHaveCount(2);
  await screenshot("actions-history.png");
});
