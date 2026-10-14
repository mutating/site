import {
  API,
  addComputer,
  expect,
  headers,
  password,
  test,
  type Account,
} from "./fixtures";
import { GiB, historyNow, reserve, seedHistory } from "./resources";
import { resolve } from "node:path";

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
  async function screenshot(name: string) {
    await expect(
      page.locator("h1:visible, #auth-title:visible").first(),
    ).toBeVisible();
    await expect(page.locator(".loading")).toHaveCount(0);
    await page.evaluate(() => document.fonts.ready);
    await expect(page).toHaveScreenshot(`${name}.png`, { fullPage: true });
  }
  await page.goto("login");
  await screenshot("login");
  if (info.project.name.endsWith("desktop")) {
    await page.locator(".wordmark").hover();
    await expect(page.locator(".brand-word-reveal").last()).toHaveCSS(
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
    "http://127.0.0.1:18101/account/api/session",
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
  await page.goto("organizations");
  await expect(page.getByRole("heading", { name: company.name })).toBeVisible();
  await screenshot("organizations");
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
  await page.evaluate(async () => {
    await document.fonts.ready;
    await new Promise<void>((resolve) =>
      requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
    );
  });
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
