import type { APIRequestContext, Page } from "@playwright/test";
import {
  API,
  addComputer,
  expect,
  headers,
  login,
  makeAccount,
  password,
  test,
  type Account,
} from "./fixtures";

async function measurement(
  request: APIRequestContext,
  account: Account,
  computerId: string,
  version: string,
  name = "single_core",
  number = 42,
) {
  const response = await request.post(`${API}/benchmark-results`, {
    headers: headers(account),
    data: {
      computer_id: computerId,
      benchmarker_version: version,
      benchmark_name: name,
      number,
    },
  });
  expect(response.status()).toBe(201);
  return response.json();
}

async function company(request: APIRequestContext, account: Account) {
  const response = await request.post(`${API}/companies`, {
    headers: headers(account),
    data: { name: "Бенч-лаборатория" },
  });
  expect(response.status()).toBe(201);
  return response.json();
}

const latest = (page: Page) =>
  page.getByRole("region", { name: "Последние бенчмарки", exact: true });
const history = (page: Page) =>
  page.getByRole("region", { name: /^История бенчмарка/ });
const detailsPath = (id: string) => `power-cells/computers/${id}`;

for (const pool of ["personal", "organization"] as const) {
  test(`${pool}: latest suite in list and details, complete SemVer history, URLs and locale`, async ({
    page,
    request,
    account,
  }) => {
    const organization =
      pool === "organization" ? await company(request, account) : null;
    const computer = await addComputer(
      request,
      account,
      "Измеряемый узел",
      organization?.id,
    );
    const ownerQuery = organization
      ? `company_id=${organization.id}`
      : `user_id=${account.id}`;
    // Insertion order, numeric version order and lexicographic order all differ.
    const versions = [
      "1.2.0",
      "10.0.0-rc.1",
      "1.0.0-beta.11",
      "10.0.0+build.1",
      "1.0.0",
      "1.10.0",
      "1.0.0-beta.2",
      "10.0.0",
      "10.0.0+build.2",
    ];
    for (const [index, version] of versions.entries()) {
      await measurement(
        request,
        account,
        computer.id,
        version,
        "single_core",
        index + 1,
      );
    }
    await measurement(
      request,
      account,
      computer.id,
      versions.at(-1)!,
      "single_core",
      1234.56789,
    );
    await measurement(
      request,
      account,
      computer.id,
      versions.at(-1)!,
      "multi_core",
      0,
    );
    await measurement(
      request,
      account,
      computer.id,
      versions.at(-1)!,
      "tiny_score",
      0.000000123456789,
    );
    await measurement(request, account, computer.id, "1.2.0", "multi_core", 99);
    await measurement(
      request,
      account,
      computer.id,
      "1.2.0",
      "legacy_only",
      777,
    );
    const empty = await addComputer(
      request,
      account,
      "Без измерений",
      organization?.id,
    );
    const other = await addComputer(
      request,
      account,
      "Другой узел",
      organization?.id,
    );
    await measurement(request, account, other.id, "99.0.0", "single_core", 888);
    let viewer = account;
    if (organization) {
      viewer = await makeAccount(request, "Наблюдатель");
      const response = await request.post(`${API}/memberships`, {
        headers: headers(account),
        data: {
          company_id: organization.id,
          user_id: viewer.id,
          role: "user",
          permissions: ["resources.read"],
        },
      });
      expect(response.status()).toBe(201);
    }
    await login(page, viewer);
    await page.goto("power-cells");
    await page
      .getByRole("region", {
        name: organization?.name || "Личные компьютеры",
        exact: true,
      })
      .getByRole("link", { name: "Список компьютеров" })
      .click();
    await expect(page).toHaveURL(new RegExp(ownerQuery));
    const row = page.getByRole("row").filter({ hasText: "Измеряемый узел" });
    const summary = row.locator(".benchmark-summary");
    await expect(summary).toContainText("10.0.0+build.2");
    await expect(summary.locator("dt")).toHaveText([
      "multi_core",
      "single_core",
    ]);
    await expect(summary.locator("dd")).toHaveText(["0", "1 234,56789"]);
    await expect(summary.getByRole("link", { name: "+1 ещё" })).toBeVisible();
    await expect(summary).not.toContainText("legacy_only");
    await expect(
      page.getByRole("row").filter({ hasText: "Без измерений" }),
    ).toContainText("Результатов пока нет");
    await expect(
      page
        .getByRole("row")
        .filter({ hasText: "Другой узел" })
        .locator(".benchmark-summary dd"),
    ).toHaveText("888");
    // Clicking the resource cell opens the computer; its name is a keyboard-accessible link too.
    await row.locator("td.numeric").first().click();
    await expect(page).toHaveURL(
      new RegExp(`/computers/${computer.id}\\?${ownerQuery}$`),
    );
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(
      "Измеряемый узел.",
    );
    await expect(
      page.getByRole("region", { name: "О компьютере" }),
    ).toContainText(computer.id);
    await expect(latest(page).locator(".benchmark-card")).toHaveCount(3);
    await expect(latest(page)).not.toContainText("legacy_only");
    await expect(latest(page)).not.toContainText("888");
    await expect(
      latest(page).getByRole("link", { name: /tiny_score/ }),
    ).toContainText("0,000000123456789");
    await expect(history(page)).toHaveCount(0);
    await latest(page)
      .getByRole("link", { name: /single_core/ })
      .click();
    await expect(page).toHaveURL(/benchmark=single_core/);
    await expect(
      latest(page).getByRole("link", { name: /single_core/ }),
    ).toHaveAttribute("aria-current", "true");
    const expectedVersions = [
      "10.0.0+build.2",
      "10.0.0+build.1",
      "10.0.0",
      "10.0.0-rc.1",
      "1.10.0",
      "1.2.0",
      "1.0.0",
      "1.0.0-beta.11",
      "1.0.0-beta.2",
    ];
    await expect(history(page).locator("tbody th")).toHaveText(
      expectedVersions,
    );
    await expect(history(page).locator("tbody td")).toHaveText([
      "1 234,56789",
      "4",
      "8",
      "2",
      "6",
      "1",
      "5",
      "3",
      "7",
    ]);
    await expect(history(page).getByRole("heading")).toBeInViewport();
    await latest(page)
      .getByRole("link", { name: /multi_core/ })
      .click();
    await expect(history(page).locator("tbody th")).toHaveText([
      "10.0.0+build.2",
      "1.2.0",
    ]);
    await expect(history(page).locator("tbody td")).toHaveText(["0", "99"]);
    await page.goBack();
    await expect(history(page).locator("tbody th")).toHaveText(
      expectedVersions,
    );
    await page.reload();
    await expect(history(page).locator("tbody th")).toHaveText(
      expectedVersions,
    );
    await page.getByRole("button", { name: "Выбрать язык" }).click();
    await expect(
      page.getByRole("region", { name: "Latest benchmarks", exact: true }),
    ).toContainText("1,234.56789");
    await expect(
      page
        .getByRole("region", { name: "Benchmark history · single_core" })
        .locator("tbody td")
        .first(),
    ).toHaveText("1,234.56789");
    await page.getByRole("button", { name: "Choose language" }).click();
    await page.getByRole("link", { name: "К списку компьютеров" }).click();
    await expect(page).toHaveURL(new RegExp(`/computers\\?${ownerQuery}$`));
    await page
      .getByRole("link", { name: "Без измерений", exact: true })
      .click();
    await expect(page).toHaveURL(new RegExp(empty.id));
    await expect(latest(page)).toContainText("Результатов пока нет");
    await expect(latest(page).getByRole("link")).toHaveCount(0);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    ).toBe(true);
  });

  test(`${pool}: computer navigation preserves the second list page`, async ({
    page,
    request,
    account,
  }) => {
    const organization =
      pool === "organization" ? await company(request, account) : null;
    const ownerQuery = organization
      ? `company_id=${organization.id}`
      : `user_id=${account.id}`;
    for (let index = 0; index < 11; index++) {
      await addComputer(request, account, `Узел ${index}`, organization?.id);
    }
    const response = await request.get(
      `${API}/computers?${ownerQuery}&only_available=true&limit=10&offset=10`,
      { headers: headers(account) },
    );
    expect(response.status()).toBe(200);
    const [computer] = await response.json();
    await measurement(request, account, computer.id, "1.0.0");
    await login(page, account);
    const path = `power-cells/computers?${ownerQuery}&page=2`;
    await page.goto(path);
    await expect(page.locator("tbody tr")).toHaveCount(1);
    const link = page.getByRole("link", { name: computer.name, exact: true });
    await link.focus();
    await page.keyboard.press("Enter");
    await latest(page)
      .getByRole("link", { name: /single_core/ })
      .click();
    await expect(history(page).locator("tbody td")).toHaveText("42");
    await page.getByRole("link", { name: "К списку компьютеров" }).click();
    await expect(page).toHaveURL(`http://127.0.0.1:18101/${path}`);
    await expect(page.locator("tbody tr")).toHaveCount(1);
    await expect(page.getByRole("button", { name: "Далее" })).toBeDisabled();
  });
}

test("loading summaries preserve row positions and computer links", async ({
  page,
  request,
  account,
}) => {
  const computers = [
    await addComputer(request, account, "Первый узел"),
    await addComputer(request, account, "Второй узел"),
  ].sort((a, b) => a.id.localeCompare(b.id));
  const [first, second] = computers;
  for (const name of ["single_core", "multi_core", "memory_copy"]) {
    await measurement(request, account, first.id, "1.0.0", name);
  }
  await login(page, account);
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route(
    `**/v1/computers/${first.id}/benchmark-results?latest_only=true`,
    async (route) => {
      await gate;
      await route.continue();
    },
  );
  await page.goto(`power-cells/computers?user_id=${account.id}`);
  const firstRow = page.getByRole("row").filter({ hasText: first.name });
  const link = page.getByRole("link", { name: second.name, exact: true });
  let before;
  try {
    await expect(firstRow.getByRole("status")).toBeVisible();
    before = await link.boundingBox();
  } finally {
    release();
  }
  await expect(firstRow.locator(".benchmark-summary dt")).toHaveCount(2);
  const after = await link.boundingBox();
  expect(after!.y).toBeCloseTo(before!.y, 0);
  await link.click();
  await expect(page).toHaveURL(new RegExp(`/computers/${second.id}`));
  await expect(latest(page)).toContainText("Результатов пока нет");
});

test("benchmark failures are isolated, show loading and recover with real updated results", async ({
  page,
  request,
  account,
}) => {
  const computer = await addComputer(request, account, "Устойчивый узел");
  await measurement(request, account, computer.id, "1.0.0");
  await login(page, account);
  const latestPath = `**/v1/computers/${computer.id}/benchmark-results?latest_only=true`;
  await page.route(latestPath, (route) => route.abort("failed"));
  await page.goto(`power-cells/computers?user_id=${account.id}`);
  const row = page.getByRole("row").filter({ hasText: "Устойчивый узел" });
  await expect(row.locator("td.numeric")).toHaveText(["8 / 8", "8 / 8 GiB"]);
  await expect(row.getByRole("alert")).toContainText(
    "Не удалось связаться с сервером",
  );
  await expect(row).not.toContainText("Результатов пока нет");
  await page.unroute(latestPath);
  await row.getByRole("button", { name: "Попробовать снова" }).click();
  await expect(row.locator(".benchmark-summary dd")).toHaveText("42");
  await expect(page).toHaveURL(
    new RegExp(`/computers\\?user_id=${account.id}$`),
  );
  // Hold the response to inspect loading without replacing successful API data.
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route(latestPath, async (route) => {
    await gate;
    await route.continue();
  });
  try {
    await page.getByRole("link", { name: computer.name, exact: true }).click();
    await expect(latest(page).getByRole("status")).toContainText("Загрузка");
    await expect(
      page.getByRole("region", { name: "О компьютере" }),
    ).toBeVisible();
  } finally {
    release();
  }
  await expect(
    latest(page).getByRole("link", { name: /single_core/ }),
  ).toBeVisible();
  await page.unroute(latestPath);
  const historyPath = `**/v1/computers/${computer.id}/benchmark-results?latest_only=false`;
  await page.route(historyPath, (route) => route.abort("failed"));
  await latest(page)
    .getByRole("link", { name: /single_core/ })
    .click();
  await expect(history(page).getByRole("alert")).toBeVisible();
  await expect(
    latest(page).getByRole("link", { name: /single_core/ }),
  ).toBeVisible();
  await page.unroute(historyPath);
  await history(page)
    .getByRole("button", { name: "Попробовать снова" })
    .click();
  await expect(history(page).locator("tbody td")).toHaveText("42");
  await page.route(latestPath, (route) => route.abort("failed"));
  await page.getByRole("button", { name: "Обновить", exact: true }).click();
  await expect(latest(page).getByRole("alert")).toBeVisible();
  await expect(history(page).locator("tbody td")).toHaveText("42");
  await page.unroute(latestPath);
  await latest(page).getByRole("button", { name: "Попробовать снова" }).click();
  await expect(
    latest(page).getByRole("link", { name: /single_core/ }),
  ).toBeVisible();
  // Upsert and a newer partial suite must replace values without mixing suites.
  await measurement(request, account, computer.id, "1.0.0", "single_core", 77);
  await measurement(request, account, computer.id, "2.0.0", "new_bench", 88);
  await page.getByRole("button", { name: "Обновить", exact: true }).click();
  await expect(latest(page).locator(".benchmark-card")).toHaveCount(1);
  await expect(latest(page)).toContainText("new_bench");
  await expect(history(page).locator("tbody td")).toHaveText("77");
  await expect(page.getByRole("alert")).toHaveCount(0);
});

test("deep links require login and preserve computer, pool and selected benchmark", async ({
  page,
  request,
  account,
}) => {
  const computer = await addComputer(request, account, "Закрытая история");
  await measurement(request, account, computer.id, "1.0.0");
  const path = `${detailsPath(computer.id)}?user_id=${account.id}&page=2&benchmark=single_core`;
  await page.goto(path);
  await expect(page).toHaveURL(/\/login$/);
  await page.getByLabel("Email или ник").fill(account.email);
  await page.getByLabel("Пароль", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Войти", exact: true }).click();
  await expect(page).toHaveURL(`http://127.0.0.1:18101/${path}`);
  await expect(history(page).locator("tbody td")).toHaveText("42");
});

test("unknown, unrelated and deleted computers expose no benchmark data", async ({
  page,
  request,
  account,
}) => {
  const other = await makeAccount(request, "Другой владелец");
  const foreign = await addComputer(request, other, "Секретный компьютер");
  await measurement(request, other, foreign.id, "1.0.0", "secret_bench", 999);
  await login(page, account);
  for (const id of [foreign.id, "00000000-0000-0000-0000-000000000000"]) {
    await page.goto(`${detailsPath(id)}?benchmark=secret_bench`);
    await expect(page.getByRole("alert")).toContainText("Страница не найдена");
    await expect(latest(page)).toHaveCount(0);
    await expect(history(page)).toHaveCount(0);
    await expect(page.getByRole("main")).not.toContainText("999");
  }
  const own = await addComputer(
    request,
    account,
    "Удалённый компьютер",
    undefined,
    false,
  );
  await measurement(request, account, own.id, "1.0.0");
  await page.goto(`${detailsPath(own.id)}?benchmark=single_core`);
  await expect(history(page).locator("tbody td")).toHaveText("42");
  expect(
    (
      await request.delete(`${API}/computers/${own.id}`, {
        headers: headers(account),
      })
    ).status(),
  ).toBe(204);
  await page.reload();
  await expect(page.getByRole("alert")).toContainText("Страница не найдена");
  await expect(history(page)).toHaveCount(0);
});

test("organization permission revocation removes access to details and history", async ({
  page,
  request,
  account,
}) => {
  const organization = await company(request, account);
  const computer = await addComputer(
    request,
    account,
    "Общий узел",
    organization.id,
  );
  await measurement(request, account, computer.id, "1.0.0");
  const viewer = await makeAccount(request, "Читатель");
  const membership = await request.post(`${API}/memberships`, {
    headers: headers(account),
    data: {
      company_id: organization.id,
      user_id: viewer.id,
      role: "user",
      permissions: ["resources.read"],
    },
  });
  expect(membership.status()).toBe(201);
  const { id } = await membership.json();
  await login(page, viewer);
  await page.goto(
    `${detailsPath(computer.id)}?company_id=${organization.id}&benchmark=single_core`,
  );
  await expect(history(page).locator("tbody td")).toHaveText("42");
  expect(
    (
      await request.patch(`${API}/memberships/${id}`, {
        headers: headers(account),
        data: { permissions: [] },
      })
    ).status(),
  ).toBe(200);
  await page.getByRole("button", { name: "Обновить", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText(
    "У вас нет прав на это действие",
  );
  await expect(latest(page)).toHaveCount(0);
  await expect(history(page)).toHaveCount(0);
  await page.goto(`power-cells/computers?company_id=${organization.id}`);
  await expect(page.getByRole("alert")).toContainText(
    "У вас нет прав на это действие",
  );
  await expect(page.locator(".computer-row")).toHaveCount(0);
});

test("missing historical benchmark and long names fit mobile without stale selection", async ({
  page,
  request,
  account,
}) => {
  const computer = await addComputer(request, account, "Длинные имена");
  const name = "benchmark_" + "x".repeat(190);
  const version = "1.0.0+" + "build".repeat(30);
  await measurement(request, account, computer.id, version, name, 1e25);
  await login(page, account);
  await page.goto(`${detailsPath(computer.id)}?benchmark=missing_benchmark`);
  await expect(history(page)).toContainText(
    "Для этого бенчмарка результатов пока нет",
  );
  await latest(page)
    .getByRole("link", { name: new RegExp(name) })
    .click();
  await expect(history(page).locator("tbody th")).toHaveText(version);
  await expect(history(page).locator("tbody tr")).toHaveCount(1);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.getByRole("link", { name: "К списку компьютеров" }).click();
  await expect(page.locator(".benchmark-summary dt")).toHaveText(name);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
});
