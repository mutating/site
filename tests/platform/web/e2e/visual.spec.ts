import {
  API,
  addComputer,
  expect,
  headers,
  makeAccount,
  markAccountComputer,
  password,
  startSession,
  test,
  type Account,
} from "./fixtures";
import { GiB, historyNow, reserve, seedHistory, useHistoricalPersonalCapacity } from "./resources";
import { resolve } from "node:path";
import { openLanguages } from "./header-controls";
import { openOrganizationMember } from "./organization-controls";
import type { Locator, Page } from "@playwright/test";
import type { Computer, ConnectedComputerPage, Membership } from "../../../../platform/web/src/lib/types";

/** Effective visibility includes ancestor opacity and filter-based hiding. */
async function effectivelyVisible(locator: Locator) {
  return locator.evaluate((element) => {
    const transparent = (value: string) => {
      if (value === "transparent") return true;
      const match = /rgba?\(([^)]+)\)/.exec(value);
      if (!match) return false;
      const parts = match[1].split(",").map(Number);
      return parts.length === 4 && parts[3] === 0;
    };
    for (let node: Element | null = element; node; node = node.parentElement) {
      const style = getComputedStyle(node);
      if (
        style.display === "none" ||
        style.visibility === "hidden" ||
        Number(style.opacity) === 0
      )
        return false;
      const match = /opacity\(\s*([0-9.]+)(%?)\s*\)/.exec(style.filter);
      if (match && Number(match[1]) / (match[2] ? 100 : 1) === 0)
        return false;
      if (
        transparent(style.color) ||
        transparent(style.getPropertyValue("-webkit-text-fill-color"))
      )
        return false;
      if (style.clipPath !== "none") return false;
    }
    const rect = element.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  });
}

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
  await page.goto("/profile?section=api-tokens");
  await expect(page.locator(".token-registry-row")).toHaveCount(4);
  await page.evaluate(() => document.fonts.ready);
  await expect(page).toHaveScreenshot("api-tokens.png", { fullPage: true, ...exactPixels });
  await page.locator(".token-status-filter .tmc-select__face").click();
  const selectPanel = page.locator(".tmc-select__panel");
  await expect(selectPanel).toBeVisible();
  await expect(selectPanel).toHaveScreenshot("api-token-select-panel.png", {
    maxDiffPixels: 100,
  });
  await page.keyboard.press("Escape");
  await expect(selectPanel).toHaveCount(0);
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
  // Chromium's clipped CSS/image edges vary by up to 2/255 per color channel
  // between processes. Keep a zero pixel budget with only that color tolerance.
  const clippedPixels = { threshold: 0.01, maxDiffPixels: 0 };
  await expect(trigger).toBeVisible();
  await page.evaluate(() => document.fonts.ready);
  await expect(trigger).toHaveScreenshot("header-avatar-placeholder.png", clippedPixels);
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
  await expect(trigger).toHaveScreenshot("header-avatar-image.png", clippedPixels);
  await trigger.click();
  await expect(panel).toBeVisible();
  await expect(page).toHaveScreenshot("header-user-menu-image.png", { clip, ...clippedPixels });
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

test("responsive computer benchmarks and focused run history", async ({ page, request, account }, info) => {
  const { language, theme } = info.project.metadata;
  await page.addInitScript(({ language, theme }) => {
    localStorage.setItem("tmc-language", language);
    localStorage.setItem("tmc-theme", theme);
  }, { language, theme });
  const computer = await addComputer(request, account, "Mac mini — Евгений");
  const dates = new Map<string, string>();
  for (const [version, name, number] of [
    ["0.0.1", "single_core", 97012.458439],
    ["0.0.2", "single_core", 98001.314159],
    ["0.0.2", "single_core", 98969.99106397117],
    ["0.0.2", "multi_core", 652398.8650576969],
  ] as const) {
    const response = await request.post(`${API}/benchmark-results`, {
      headers: headers(account),
      data: { computer_id: computer.id, benchmarker_version: version, benchmark_name: name, number },
    });
    expect(response.status()).toBe(201);
    const row = await response.json();
    dates.set(row.id, new Date(Date.UTC(2026, 9, 7, 12, dates.size)).toISOString());
    // The API keeps the newest measurement by created_at. Separate the writes
    // so same-version results have a deterministic order.
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  await page.route(`**/v1/computers/${computer.id}/benchmark-results?*`, async route => {
    const response = await route.fetch();
    const rows: { id: string }[] = await response.json();
    await route.fulfill({ response, json: rows.map(row => ({ ...row, created_at: dates.get(row.id) })) });
  });
  await startSession(page, account);
  await page.goto(`compute/computers/${computer.id}?user_id=${account.id}`);
  await expect(page.locator(".benchmark-card")).toHaveCount(2);
  await page.evaluate(async () => {
    await Promise.all(Array.from(document.fonts, face => face.load()));
    await document.fonts.ready;
  });
  await expect(page).toHaveScreenshot("computer-responsive-details.png", { fullPage: true });
  await page.getByRole("link", { name: /single_core/ }).press("Enter");
  const history = page.locator('section[aria-labelledby="benchmark-history-title"]');
  await expect(history).toBeFocused();
  await expect(history.locator("tbody tr")).toHaveCount(3);
  await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
  await expect(page).toHaveScreenshot("computer-responsive-history.png");
});

async function prepareDashboard(page: Page, language: string, theme: string) {
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
      document.querySelector("dialog[open]")?.getBoundingClientRect();
      await new Promise<void>((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
      );
    });
  }
  async function screenshot(name: string) {
    await expect(
      page.locator("h1:visible, #auth-title:visible").first(),
    ).toBeVisible();
    await expect(page.locator(".loading:visible")).toHaveCount(0);
    await settleFonts();
    if (name.startsWith("computer-") || name.startsWith("organization-") || name.startsWith("compute-")) {
      // Benchmark history and the organization tab controls can scroll before capture.
      // Keep the sticky app header at the top for the full-page comparison.
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
  return { settleFonts, screenshot };
}

test("Compute overview, connected computers, pool catalog and transfer dialog", async ({ page, request }, info) => {
  const { language, theme } = info.project.metadata;
  const { screenshot } = await prepareDashboard(page, language, theme);
  const account = await makeAccount(request, "Алиса");
  expect((await request.patch(`${API}/users/${account.id}`, {
    headers: headers(account), data: { last_name: "Волкова" },
  })).status()).toBe(200);
  await startSession(page, account);
  await page.goto("compute");
  await expect(page.locator(".compute-status-summary dd")).toHaveText(["0", "0", "0"]);
  await expect(page.locator(".compute-destination").last().locator("strong")).toHaveText("1");
  await screenshot("compute-empty");
  await page.goto("compute/computers");
  await expect(page.locator(".empty-state")).toBeVisible();
  await screenshot("compute-connected-empty");

  const creation = await request.post(`${API}/companies`, {
    headers: headers(account), data: { name: "Исследовательская лаборатория" },
  });
  expect(creation.status()).toBe(201);
  const company = await creation.json();
  const personal = await addComputer(request, account, "workstation-01");
  const mini = await addComputer(request, account, "mini-node-02", undefined, true, { cpu_millis: 500, memory_bytes: 1.5 * GiB });
  const shared = await addComputer(request, account, "lab-node-01", company.id, true, { cpu_millis: 4000, memory_bytes: 12 * GiB });
  const offline = await addComputer(request, account, "portable-mac", undefined, false);
  for (const computer of [personal, shared, offline]) await markAccountComputer(request, computer.id);
  await seedHistory(request, personal.id);
  await seedHistory(request, shared.id, "sparse");
  const personalOrder = [personal.id, mini.id, offline.id];
  await page.route(`${API}/users/${account.id}/resources/pool-overview`, async (route) => {
    const response = await route.fetch();
    const snapshot = await response.json() as { computers: Computer[] };
    snapshot.computers.sort((first, second) => personalOrder.indexOf(first.id) - personalOrder.indexOf(second.id));
    await route.fulfill({ response, json: snapshot });
  });
  const connectedOrder = [personal.id, shared.id, offline.id];
  await page.route(`${API}/connected-computers?limit=10&offset=0`, async (route) => {
    const response = await route.fetch();
    const inventory: ConnectedComputerPage = await response.json();
    inventory.items.sort((first, second) => connectedOrder.indexOf(first.id) - connectedOrder.indexOf(second.id));
    await route.fulfill({ response, json: inventory });
  });

  await page.goto("compute");
  await expect(page.locator(".compute-status-summary dd")).toHaveText(["3", "2", "1"]);
  await expect(page.locator(".compute-destination").last().locator("strong")).toHaveText("2");
  await screenshot("compute");
  await page.goto("compute/pools");
  await expect(page.locator(".compute-pool-list [data-testid='organization-computer-count']")).toHaveText(["3", "1"]);
  await screenshot("compute-pools");
  await useHistoricalPersonalCapacity(page, account.id);
  await page.goto("compute/pools/personal");
  await expect(page.locator("[data-series]")).toHaveCount(4);
  await expect(page.locator(".organization-computer-identity strong")).toHaveText([personal.name, mini.name, offline.name]);
  await screenshot("compute-personal-pool");
  await page.goto(`compute/pools/${company.id}`);
  await expect(page.locator("[data-series]")).toHaveCount(4);
  await screenshot("compute-organization-pool");
  await page.goto("compute/computers");
  await expect(page.locator(".connected-computer-row")).toHaveCount(3);
  await screenshot("compute-connected-computers");
  await page.locator(".connected-computer-row").filter({ hasText: "workstation-01" }).getByRole("button").click();
  const transfer = page.getByRole("dialog");
  await expect(transfer.getByRole("combobox").locator("option")).toHaveCount(2);
  await transfer.getByRole("combobox").selectOption(company.id);
  await screenshot("compute-move-pool");
});

test("auth, every product page, populated and empty pools, and confirmation dialog", async ({
  page,
  request,
}, info) => {
  const { language, theme } = info.project.metadata;
  const { settleFonts, screenshot } = await prepareDashboard(page, language, theme);
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
  const benchmarkDates = new Map<string, string>();
  for (const [version, name, number] of [
    ["1.2.0", "single_core", 1100],
    ["1.2.0", "legacy_only", 875.25],
    ["1.9.0", "single_core", 1200.25],
    ["1.10.0", "single_core", 1240],
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
    const row = await result.json();
    benchmarkDates.set(row.id, new Date(Date.UTC(2026, 9, 7, 12, benchmarkDates.size)).toISOString());
  }
  // API receipt times vary between runs; keep displayed dates stable in pixel comparisons.
  await page.route("**/v1/computers/*/benchmark-results?*", async route => {
    const response = await route.fetch();
    const rows: { id: string; created_at: string }[] = await response.json();
    await route.fulfill({ response, json: rows.map(row => ({
      ...row, created_at: benchmarkDates.get(row.id) || row.created_at,
    })) });
  });
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
  await seedHistory(request, shared.id, "sparse");
  await page.goto("");
  await screenshot("overview");
  await page.goto(`compute/computers?user_id=${account.id}`);
  await expect(page.locator("tbody tr")).toHaveCount(2);
  await screenshot("computers");
  await page.getByRole("link", { name: "workstation-01", exact: true }).click();
  await expect(page.locator(".benchmark-card")).toHaveCount(3);
  await screenshot("computer-details");
  await page.getByRole("link", { name: /single_core/ }).click();
  await expect(page.locator(".benchmark-history-table tbody tr")).toHaveCount(
    4,
  );
  await screenshot("computer-benchmark-history");
  await page.goto(`compute/computers/${mini.id}?user_id=${account.id}`);
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
  for (const [first_name, last_name, role, alias] of [
    ["Михаил", "Ким", "admin", "kim"], ["Нина", "Орлова", "user", "orlova"], ["Майя", "Соколова", "user", "sokolova"],
    ["Данил", "Чен", "user", "chen"], ["София", "Левина", "user", "levina"],
  ]) {
    const user = await request.post(`${API}/users`, {
      data: { first_name, last_name, email: `${nickname}_${alias}@example.com`, password },
    });
    expect(user.status()).toBe(201);
    const grant = await request.post(`${API}/memberships`, { headers: headers(account), data: {
      company_id: company.id, user_id: (await user.json()).id, role,
    } });
    expect(grant.status()).toBe(201);
  }
  const invite = await request.post(`${API}/invitations`, { headers: headers(account), data: {
    company_id: company.id, identifier: "lab-guest@example.com", role: "user", expires_in_seconds: 86400,
  } });
  expect(invite.status()).toBe(201);
  // Order real memberships before slicing so the five visible rows are deterministic.
  await page.route(`**/v1/memberships?company_id=${company.id}&*`, async (route) => {
    const url = new URL(route.request().url());
    const limit = Number(url.searchParams.get("limit"));
    url.searchParams.set("limit", "200");
    const response = await route.fetch({ url: url.href });
    const members: Membership[] = await response.json();
    const order = ["creator", "admin", "user"];
    members.sort((first, second) => order.indexOf(first.role) - order.indexOf(second.role)
      || first.user.first_name.localeCompare(second.user.first_name, "ru"));
    await route.fulfill({ response, json: members.slice(0, limit) });
  });
  // Keep the week and the chart shapes stable when CI runs on a different date.
  const poolPoints = [
    [1, 2000, 4, 1500, 2.5], [3, 4000, 12, 2500, 8], [5, 4000, 12, 0, 0],
    [6, 4000, 12, 4000, 12], [8, 4000, 12, 4000, 12],
  ].map(([day, cpu, memory, freeCpu, freeMemory]) => ({
    at: new Date(Date.UTC(2026, 0, day)).toISOString(),
    cpu_total_millis: cpu,
    memory_total_bytes: memory * GiB,
    cpu_free_millis: freeCpu,
    memory_free_bytes: freeMemory * GiB,
  }));
  await page.route(`**/v1/companies/${company.id}/resources/pool-history`, (route) => route.fulfill({ json: {
    from_time: poolPoints[0].at,
    to_time: historyNow.toISOString(),
    points: poolPoints,
  } }));
  await page.goto(`organizations/${company.id}`);
  await expect(page.locator(".member-row")).toHaveCount(5);
  await expect(page.locator(".organization-history[aria-busy='false']")).toHaveCount(2);
  await expect(page.locator(".organization-computers")).toHaveAttribute("aria-busy", "false");
  await expect(page.locator(".organization-overview")).toHaveAttribute("aria-busy", "false");
  await expect(page.getByRole("tab", { name: language === "ru" ? "Приглашения" : "Invitations", exact: true })).toContainText("1");
  await screenshot("organization-members");
  await page.getByRole("tab", { name: language === "ru" ? "Приглашения" : "Invitations", exact: true }).click();
  await page.locator(".member-sharing summary").click();
  await screenshot("organization-sharing");
  await page.getByRole("tab", { name: language === "ru" ? "Участники" : "Members", exact: true }).click();
  await page.getByRole("link", { name: language === "ru" ? "Смотреть всех 6" : "View all 6" }).click();
  await expect(page.locator(".member-directory .member-row")).toHaveCount(6);
  await screenshot("organization-directory-members");
  const self = page.getByRole("article", { name: "Алиса Волкова", exact: true });
  await openOrganizationMember(self);
  await self
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
  // The dialog is centered by the browser; capture it directly so subpixel
  // viewport positioning cannot shift the whole dialog between runs.
  await settleFonts();
  const box = (await dialog.boundingBox())!;
  const viewport = page.viewportSize()!;
  expect(Math.abs(box.x + box.width / 2 - viewport.width / 2)).toBeLessThanOrEqual(1);
  expect(Math.abs(box.y + box.height / 2 - viewport.height / 2)).toBeLessThanOrEqual(1);
  // Pixel tolerance covers font rasterization variance; assert the expanded
  // permission content explicitly so hidden rows cannot slip through.
  const roleFace = dialog.locator(".tmc-select__face");
  await expect(roleFace).toBeVisible();
  expect(await effectivelyVisible(roleFace)).toBe(true);
  await expect(roleFace.locator(".tmc-select__value")).toHaveText(
    language === "ru" ? "Участник" : "Member",
  );
  const chevron = roleFace.locator(".tmc-select__chevron");
  await expect(chevron).toBeVisible();
  expect(await effectivelyVisible(chevron)).toBe(true);
  const legend = dialog.locator(".permission-grid legend");
  await expect(legend).toBeVisible();
  expect(await effectivelyVisible(legend)).toBe(true);
  await expect(legend).toHaveText(language === "ru" ? "Права" : "Permissions");
  const permissionBoxes = dialog.locator(
    ".permission-grid input[type='checkbox']",
  );
  await expect(permissionBoxes).toHaveCount(9);
  for (const box of await permissionBoxes.all()) {
    await expect(box).toBeVisible();
    expect(await effectivelyVisible(box)).toBe(true);
  }
  const checkedPermissions =
    language === "ru"
      ? [
          "Просмотр участников",
          "Просмотр ресурсов и истории мощностей",
          "Просмотр задач, результатов и артефактов",
          "Выполнение задач и работа с артефактами",
          "Просмотр API-токенов",
        ]
      : [
          "View members",
          "View resources and capacity history",
          "View tasks, results, and artifacts",
          "Run tasks and manage artifacts",
          "View API tokens",
        ];
  const uncheckedPermissions =
    language === "ru"
      ? [
          "Управление участниками и приглашениями",
          "Управление компьютерами и ресурсами",
          "Управление API-токенами",
          "Настройки организации",
        ]
      : [
          "Manage members and invitations",
          "Manage computers and resources",
          "Manage API tokens",
          "Organization settings",
        ];
  for (const name of checkedPermissions)
    await expect(
      dialog.getByRole("checkbox", { name, exact: true }),
    ).toBeChecked();
  for (const name of uncheckedPermissions)
    await expect(
      dialog.getByRole("checkbox", { name, exact: true }),
    ).not.toBeChecked();
  // Chromium can rasterize the same fractional text rows one pixel apart
  // between runs. Allow that small font-rendering variance while still
  // catching real layout or content changes.
  await expect(dialog).toHaveScreenshot("member-permissions.png", {
    maxDiffPixels: 400,
  });
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
  await page.getByRole("tab", { name: language === "ru" ? "Аккаунт" : "Account", exact: true }).click();
  await screenshot("profile-account");
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
