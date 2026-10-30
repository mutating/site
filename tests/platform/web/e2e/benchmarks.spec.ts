import { openLanguages, openUserMenu, selectLanguage } from "./header-controls";
import type { APIRequestContext, Page } from "@playwright/test";
import { randomUUID } from "node:crypto";
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
  runId?: string,
) {
  const response = await request.post(`${API}/benchmark-results`, {
    headers: headers(account),
    data: {
      computer_id: computerId,
      benchmarker_version: version,
      benchmark_name: name,
      number,
      run_id: runId,
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

function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

async function delayedResponse(page: Page, path: string) {
  const ready = deferred(),
    release = deferred(),
    delivered = deferred(),
    finished = deferred();
  await page.route(
    path,
    async (route) => {
      const pending = route.request();
      const settle = (request: typeof pending) => {
        if (request !== pending) return;
        page.off("requestfinished", settle);
        page.off("requestfailed", settle);
        delivered.resolve();
      };
      page.on("requestfinished", settle);
      page.on("requestfailed", settle);
      // Delay an actual API response, including its original body and headers.
      const response = await route.fetch();
      ready.resolve();
      try {
        await release.promise;
        await route.fulfill({ response });
      } finally {
        finished.resolve();
      }
    },
    { times: 1 },
  );
  return {
    ready: ready.promise,
    release: release.resolve,
    finished: Promise.all([delivered.promise, finished.promise]),
  };
}

async function settlePaint(page: Page) {
  await page.evaluate(
    () =>
      new Promise<void>((resolve) => {
        requestAnimationFrame(() => requestAnimationFrame(() => resolve()));
      }),
  );
}

test.describe("run dates", () => {
  test.use({ timezoneId: "Europe/Moscow" });

  test("same-version runs stay distinct and dates use the browser time zone", async ({
    page, request, account,
  }) => {
    const computer = await addComputer(request, account, "История запусков");
    const first = await measurement(request, account, computer.id, "2.0.0", "single_core", 11);
    const second = await measurement(request, account, computer.id, "2.0.0", "single_core", 22);
    const third = await measurement(request, account, computer.id, "1.0.0", "single_core", 33);
    // Exercise a day boundary without making the test depend on the wall clock.
    const dates: Record<string, string> = {
      [first.id]: "2026-10-26T20:00:00Z",
      [second.id]: "2026-10-26T21:00:00Z",
      [third.id]: "2026-10-26T22:30:00Z",
    };
    await page.route(`**/v1/computers/${computer.id}/benchmark-results?latest_only=false`, async route => {
      const response = await route.fetch();
      const rows: { id: string }[] = await response.json();
      await route.fulfill({ response, json: rows.map(row => ({ ...row, created_at: dates[row.id] })) });
    });
    await login(page, account);
    await page.goto(detailsPath(computer.id));
    await expect(history(page)).toHaveCount(0);
    await expect(latest(page).getByRole("combobox")).toHaveCount(0);
    await latest(page).getByRole("link", { name: /single_core/ }).click();
    await expect(history(page).locator("thead th")).toHaveText([
      "Дата и время ↓", "Версия тулинга", "Результат",
    ]);
    await expect(history(page).locator("tbody .benchmark-tooling-version")).toHaveText([
      "1.0.0", "2.0.0", "2.0.0",
    ]);
    await expect(history(page).locator("tbody .numeric")).toHaveText(["33", "22", "11"]);
    const times = history(page).locator("tbody time");
    await expect(times.nth(0)).toHaveAttribute("datetime", dates[third.id]);
    await expect(times.nth(0)).toContainText("27 окт.");
    await expect(times.nth(0)).toContainText("01:30:00");
    await expect(times.nth(1)).toContainText("00:00:00");
    await expect(times.nth(2)).toContainText("26 окт.");
    await expect(times.nth(2)).toContainText("23:00:00");
    await page.screenshot({ path: test.info().outputPath("benchmark-runs.png"), fullPage: true });
  });

  test("microsecond dates, whole seconds and equal timestamps have a stable order in both languages", async ({
    page, request, account,
  }) => {
    const computer = await addComputer(request, account, "Точные даты");
    const first = await measurement(request, account, computer.id, "1.0.0", "single_core", 11,
      "00000000-0000-0000-0000-000000000004");
    const second = await measurement(request, account, computer.id, "1.0.0", "single_core", 22,
      "00000000-0000-0000-0000-000000000003");
    const third = await measurement(request, account, computer.id, "1.0.0", "single_core", 33,
      "00000000-0000-0000-0000-000000000002");
    const whole = await measurement(request, account, computer.id, "1.0.0", "single_core", 44,
      "00000000-0000-0000-0000-000000000006");
    const fractional = await measurement(request, account, computer.id, "1.0.0", "single_core", 55,
      "00000000-0000-0000-0000-000000000005");
    const dates: Record<string, string> = {
      [first.id]: "2026-10-26T20:00:00.123001Z",
      [second.id]: "2026-10-26T20:00:00.123002Z",
      [third.id]: "2026-10-26T20:00:00.123002Z",
      [whole.id]: "2026-10-26T20:00:00Z",
      [fractional.id]: "2026-10-26T20:00:00.000001Z",
    };
    await page.route(`**/v1/computers/${computer.id}/benchmark-results?latest_only=false`, async route => {
      const response = await route.fetch();
      const rows: { id: string }[] = await response.json();
      await route.fulfill({ response, json: rows.reverse().map(row => ({ ...row, created_at: dates[row.id] })) });
    });
    await login(page, account);
    await page.goto(`${detailsPath(computer.id)}?benchmark=single_core`);
    await expect(history(page).locator("tbody .numeric")).toHaveText(["22", "33", "11", "55", "44"]);
    const times = history(page).locator("tbody time");
    await expect(times).toHaveCount(5);
    await expect(times.nth(0)).toHaveAttribute("datetime", dates[second.id]);
    await expect(times.nth(1)).toHaveAttribute("datetime", dates[third.id]);
    await expect(times.nth(2)).toHaveAttribute("datetime", dates[first.id]);
    await expect(times.nth(3)).toHaveAttribute("datetime", dates[fractional.id]);
    await expect(times.nth(4)).toHaveAttribute("datetime", dates[whole.id]);
    await expect(times.nth(0)).toContainText("26 окт.");
    await selectLanguage(page);
    const english = page.getByRole("region", { name: "Benchmark history · single_core" });
    await expect(english.getByRole("columnheader")).toHaveText([
      "Date and time ↓", "Tooling version", "Score",
    ]);
    await expect(english.getByRole("columnheader").first()).toHaveAttribute("aria-sort", "descending");
    await expect(english.locator("tbody .numeric")).toHaveText(["22", "33", "11", "55", "44"]);
    await expect(english.locator("time").first()).toContainText("Oct 26, 2026");
    await expect(english.locator("time").first()).toHaveAttribute("datetime", dates[second.id]);
  });
});

for (const pool of ["personal", "organization"] as const) {
  test(`${pool}: a card opens all same-version runs and refresh keeps dates and retries intact`, async ({
    page, request, account,
  }) => {
    const organization = pool === "organization" ? await company(request, account) : null;
    const computer = await addComputer(request, account, "Повторные запуски", organization?.id);
    const first = await measurement(request, account, computer.id, "0.0.2", "single_core", 11);
    const second = await measurement(request, account, computer.id, "0.0.2", "single_core", 22);
    await measurement(request, account, computer.id, "0.0.2", "multi_core", 33);
    const owner = organization ? `company_id=${organization.id}` : `user_id=${account.id}`;
    await login(page, account);
    await page.goto(`${detailsPath(computer.id)}?${owner}&page=2`);
    await expect(history(page)).toHaveCount(0);
    await expect(latest(page).getByRole("combobox")).toHaveCount(0);
    await expect(latest(page).locator("details")).toHaveCount(0);
    const card = latest(page).getByRole("link", { name: /single_core/ });
    await card.focus();
    await card.press("Enter");
    await expect(history(page)).toBeFocused();
    await expect(card).toHaveAttribute("aria-current", "true");
    await expect(history(page).locator("tbody .numeric")).toHaveText(["22", "11"]);
    await expect(history(page).locator("tbody time").nth(0)).toHaveAttribute("datetime", second.created_at);
    await expect(history(page).locator("tbody time").nth(1)).toHaveAttribute("datetime", first.created_at);

    const replay = await measurement(request, account, computer.id, "0.0.2", "single_core", 22, second.run_id);
    expect(replay).toEqual(second);
    const third = await measurement(request, account, computer.id, "0.0.2", "single_core", 44);
    await page.getByRole("button", { name: "Обновить", exact: true }).click();
    await expect(history(page).locator("tbody .numeric")).toHaveText(["44", "22", "11"]);
    await expect(history(page).locator("tbody .benchmark-tooling-version")).toHaveText([
      "0.0.2", "0.0.2", "0.0.2",
    ]);
    for (const [index, result] of [third, second, first].entries()) {
      await expect(history(page).locator("tbody time").nth(index)).toHaveAttribute("datetime", result.created_at);
    }
    await expect(card.locator("strong")).toHaveText("44");
    const query = new URL(page.url()).searchParams;
    expect(query.get("benchmark")).toBe("single_core");
    expect(query.get("page")).toBe("2");
    expect(query.get(organization ? "company_id" : "user_id")).toBe(organization?.id || account.id);
    await page.reload();
    await expect(history(page).locator("tbody .numeric")).toHaveText(["44", "22", "11"]);
    await latest(page).getByRole("link", { name: /multi_core/ }).click();
    await expect(history(page).locator("tbody .numeric")).toHaveText(["33"]);
    await page.goBack();
    await expect(history(page).locator("tbody .numeric")).toHaveText(["44", "22", "11"]);
  });

  test(`${pool}: latest suite in list and details, complete chronological run history, URLs and locale`, async ({
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
    await summary.getByRole("link", { name: "+1 ещё" }).click();
    await expect(page).toHaveURL(
      new RegExp(`/computers/${computer.id}\\?${ownerQuery}$`),
    );
    await expect(latest(page).locator(".benchmark-card")).toHaveCount(3);
    await expect(
      latest(page).getByRole("link", { name: /tiny_score/ }),
    ).toBeVisible();
    await page.goBack();
    await expect(page).toHaveURL(new RegExp(`/computers\\?${ownerQuery}$`));
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
    const expectedVersions = [versions.at(-1)!, ...[...versions].reverse()];
    await expect(history(page).locator("tbody .benchmark-tooling-version")).toHaveText(
      expectedVersions,
    );
    await expect(history(page).locator("tbody .numeric")).toHaveText([
      "1 234,56789",
      "9",
      "8",
      "7",
      "6",
      "5",
      "4",
      "3",
      "2",
      "1",
    ]);
    await expect(history(page).getByRole("heading")).toBeInViewport();
    await latest(page)
      .getByRole("link", { name: /multi_core/ })
      .click();
    await expect(history(page).locator("tbody .benchmark-tooling-version")).toHaveText([
      "1.2.0",
      "10.0.0+build.2",
    ]);
    await expect(history(page).locator("tbody .numeric")).toHaveText(["99", "0"]);
    await page.goBack();
    await expect(history(page).locator("tbody .benchmark-tooling-version")).toHaveText(
      expectedVersions,
    );
    await page.reload();
    await expect(history(page).locator("tbody .benchmark-tooling-version")).toHaveText(
      expectedVersions,
    );
    await expect(latest(page).getByRole("combobox")).toHaveCount(0);
    await expect(latest(page).locator("details")).toHaveCount(0);
    await expect(history(page).locator("tbody time")).toHaveCount(10);
    await selectLanguage(page);
    await expect(
      page.getByRole("region", { name: "Latest benchmarks", exact: true }),
    ).toContainText("1,234.56789");
    await expect(
      page
        .getByRole("region", { name: "Benchmark history · single_core" })
        .locator("tbody .numeric")
        .first(),
    ).toHaveText("1,234.56789");
    await selectLanguage(page);
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
    await expect(history(page).locator("tbody .numeric")).toHaveText("42");
    await page.getByRole("link", { name: "К списку компьютеров" }).click();
    await expect(page).toHaveURL(`http://127.0.0.1:18101/${path}`);
    await expect(page.locator("tbody tr")).toHaveCount(1);
    await expect(page.getByRole("button", { name: "Далее" })).toBeDisabled();
  });

  test(`${pool}: deletion removes stale measurements, restores the previous suite and ends empty`, async ({
    page,
    request,
    account,
  }) => {
    const organization =
      pool === "organization" ? await company(request, account) : null;
    const query = organization
      ? `company_id=${organization.id}`
      : `user_id=${account.id}`;
    const computer = await addComputer(
      request,
      account,
      "Удаление результатов",
      organization?.id,
    );
    const old = await measurement(
      request,
      account,
      computer.id,
      "1.2.0",
      "single_core",
      11,
    );
    const newest = await measurement(
      request,
      account,
      computer.id,
      "1.10.0",
      "single_core",
      22,
    );
    const sibling = await measurement(
      request,
      account,
      computer.id,
      "1.10.0",
      "multi_core",
      33,
    );
    await login(page, account);
    await page.goto(`power-cells/computers?${query}`);
    const summary = page.locator(".benchmark-summary");
    await expect(summary.locator("dd")).toHaveText(["33", "22"]);
    await page.getByRole("link", { name: computer.name, exact: true }).click();
    await latest(page)
      .getByRole("link", { name: /single_core/ })
      .click();
    await expect(history(page).locator("tbody .numeric")).toHaveText(["22", "11"]);

    expect(
      (
        await request.delete(`${API}/benchmark-results/${newest.id}`, {
          headers: headers(account),
        })
      ).status(),
    ).toBe(204);
    await page.getByRole("button", { name: "Обновить", exact: true }).click();
    await expect(latest(page).locator(".benchmark-card strong")).toHaveText([
      "33",
    ]);
    await expect(latest(page).locator(".benchmark-version")).toContainText(
      "1.10.0",
    );
    await expect(history(page).locator("tbody .benchmark-tooling-version")).toHaveText(["1.2.0"]);
    await expect(history(page).locator("tbody .numeric")).toHaveText(["11"]);
    await page.getByRole("link", { name: "К списку компьютеров" }).click();
    await expect(summary.locator("dt")).toHaveText(["multi_core"]);
    await expect(summary.locator("dd")).toHaveText(["33"]);
    await page.getByRole("link", { name: computer.name, exact: true }).click();
    await latest(page)
      .getByRole("link", { name: /multi_core/ })
      .click();
    await expect(history(page).locator("tbody .numeric")).toHaveText(["33"]);

    expect(
      (
        await request.delete(`${API}/benchmark-results/${sibling.id}`, {
          headers: headers(account),
        })
      ).status(),
    ).toBe(204);
    await page.getByRole("button", { name: "Обновить", exact: true }).click();
    await expect(latest(page).locator(".benchmark-card strong")).toHaveText([
      "11",
    ]);
    await expect(latest(page).locator(".benchmark-version")).toContainText(
      "1.2.0",
    );
    await expect(history(page)).toContainText(
      "Для этого бенчмарка результатов пока нет",
    );
    await expect(history(page).locator("tbody tr")).toHaveCount(0);
    await latest(page).getByRole("link", { name: /single_core/ }).click();
    await expect(history(page).locator("tbody .numeric")).toHaveText(["11"]);
    await page.getByRole("link", { name: "К списку компьютеров" }).click();
    await expect(summary.locator("dd")).toHaveText(["11"]);
    await expect(summary).toContainText("1.2.0");
    await page.getByRole("link", { name: computer.name, exact: true }).click();
    await latest(page)
      .getByRole("link", { name: /single_core/ })
      .click();
    await expect(history(page).locator("tbody .numeric")).toHaveText(["11"]);

    expect(
      (
        await request.delete(`${API}/benchmark-results/${old.id}`, {
          headers: headers(account),
        })
      ).status(),
    ).toBe(204);
    await page.getByRole("button", { name: "Обновить", exact: true }).click();
    await expect(
      latest(page).getByRole("heading", { name: "Результатов пока нет" }),
    ).toBeVisible();
    await expect(
      latest(page).locator(".benchmark-version, .benchmark-card"),
    ).toHaveCount(0);
    await expect(history(page)).toContainText(
      "Для этого бенчмарка результатов пока нет",
    );
    await expect(history(page).locator("tbody tr")).toHaveCount(0);
    await expect(latest(page).getByLabel("Выберите бенчмарк")).toHaveCount(0);
    await page.getByRole("link", { name: "К списку компьютеров" }).click();
    await expect(summary).toHaveText("Результатов пока нет");
    await expect(summary.locator("dd, small")).toHaveCount(0);
  });
}

test.describe("history navigation", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  async function computerWithCards(request: APIRequestContext, account: Account) {
    const computer = await addComputer(request, account, "Много бенчмарков");
    for (let index = 0; index < 12; index++) {
      await measurement(request, account, computer.id, "1.0.0", `bench_${index}`, 100 + index);
    }
    return computer;
  }

  for (const entry of ["deep link", "refresh"] as const) {
    test(`${entry}: a late latest response keeps the selected history in view`, async ({
      page, request, account,
    }) => {
      const computer = await computerWithCards(request, account);
      await login(page, account);
      if (entry === "refresh") {
        await page.goto(detailsPath(computer.id));
        await latest(page).getByRole("link", { name: /bench_0/ }).click();
        await expect(history(page).getByRole("heading")).toBeInViewport();
      }
      const held = await delayedResponse(
        page, `**/v1/computers/${computer.id}/benchmark-results?latest_only=true`,
      );
      try {
        if (entry === "refresh") {
          await page.getByRole("button", { name: "Обновить", exact: true }).click();
        } else {
          await page.goto(`${detailsPath(computer.id)}?benchmark=bench_0`);
        }
        await held.ready;
        await expect(history(page).locator("tbody .numeric")).toHaveText("100");
        await expect(history(page).getByRole("heading")).toBeInViewport();
      } finally {
        held.release();
      }
      await held.finished;
      await expect(latest(page).locator(".benchmark-card")).toHaveCount(12);
      await expect(history(page).getByRole("heading")).toBeInViewport();
      await expect(history(page)).toBeFocused();
    });
  }

  test("a late history response keeps all fitting rows in view", async ({ page, request, account }) => {
    const computer = await computerWithCards(request, account);
    await measurement(request, account, computer.id, "1.0.0", "bench_0", 200);
    await measurement(request, account, computer.id, "1.0.0", "bench_0", 300);
    await login(page, account);
    const held = await delayedResponse(
      page, `**/v1/computers/${computer.id}/benchmark-results?latest_only=false`,
    );
    try {
      await page.goto(detailsPath(computer.id));
      await latest(page).getByRole("link", { name: /bench_0/ }).click();
      await held.ready;
      await expect(history(page)).toBeFocused();
      await settlePaint(page);
      held.release();
      await held.finished;
      await expect(history(page).locator("tbody tr")).toHaveCount(3);
      await expect(history(page).locator("tbody tr").last()).toBeInViewport({ ratio: 1 });
    } finally {
      held.release();
    }
  });

  test("clicking the selected card again returns to its history without downloading it again", async ({
    page, request, account,
  }) => {
    const computer = await computerWithCards(request, account);
    await login(page, account);
    const downloads: string[] = [];
    page.on("request", request => {
      if (request.url().endsWith(`/computers/${computer.id}/benchmark-results?latest_only=false`)) {
        downloads.push(request.url());
      }
    });
    await page.goto(detailsPath(computer.id));
    const card = latest(page).getByRole("link", { name: /bench_0/ });
    await card.click();
    await expect(history(page).getByRole("heading")).toBeInViewport();
    await card.click();
    await expect(history(page).getByRole("heading")).toBeInViewport();
    await expect(history(page)).toBeFocused();
    expect(downloads).toHaveLength(1);
  });

  for (const source of ["latest", "history", "computer"] as const) {
    test(`a late ${source} response does not undo manual scrolling`, async ({
      page, request, account, browserName, isMobile,
    }) => {
      if (source === "computer") await page.setViewportSize({ width: 844, height: 390 });
      const computer = await computerWithCards(request, account);
      await login(page, account);
      const held = await delayedResponse(
        page, source === "computer"
          ? `**/v1/computers/${computer.id}`
          : `**/v1/computers/${computer.id}/benchmark-results?latest_only=${source === "latest"}`,
      );
      try {
        await page.goto(`${detailsPath(computer.id)}?benchmark=bench_0`);
        await held.ready;
        if (source !== "computer") {
          await expect(history(page).getByRole("heading")).toBeInViewport();
          await expect(history(page)).toBeFocused();
        }
        if (browserName === "webkit" && isMobile) {
          // Mobile WebKit has no wheel API; use a keyboard gesture and its scroll position.
          await page.keyboard.press(source !== "computer" ? "Home" : "PageDown");
          await page.evaluate(top => window.scrollTo(0, top), source !== "computer" ? 0 : 150);
        } else if (source === "history") {
          // A single wheel gesture can be capped before reaching the top of a long list.
          await page.keyboard.press("Home");
        } else {
          await page.mouse.move(195, source !== "computer" ? 420 : 200);
          await page.mouse.wheel(0, source !== "computer" ? -10000 : 150);
        }
        await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(source !== "computer" ? 0 : 150);
        if (source !== "computer") await expect(history(page)).toBeFocused();
        held.release();
        await held.finished;
        await expect(latest(page).locator(".benchmark-card")).toHaveCount(12);
        await settlePaint(page);
        await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(source !== "computer" ? 0 : 150);
      } finally {
        held.release();
      }
      await latest(page).getByRole("link", { name: /bench_0/ }).click();
      await expect(history(page).getByRole("heading")).toBeInViewport();
    });
  }

  for (const [source, width] of [
    ["latest", 390], ["latest", 1280], ["computer", 390], ["computer", 1280],
  ] as const) {
    test(`a late ${source} response preserves a language option's focus at ${width}px`, async ({
      page, request, account,
    }) => {
      await page.setViewportSize({ width, height: 844 });
      const computer = await computerWithCards(request, account);
      await login(page, account);
      const held = await delayedResponse(
        page, source === "latest"
          ? `**/v1/computers/${computer.id}/benchmark-results?latest_only=true`
          : `**/v1/computers/${computer.id}`,
      );
      try {
        await page.goto(`${detailsPath(computer.id)}?benchmark=bench_0`);
        await held.ready;
        if (source === "latest") {
          await expect(history(page).locator("tbody .numeric")).toHaveText("100");
          await expect(history(page)).toBeFocused();
        }
        const languages = await openLanguages(page);
        const english = languages.locator('[data-language-option="en"]');
        await english.focus();
        await expect(english).toBeFocused();
        held.release();
        await held.finished;
        await expect(latest(page).locator(".benchmark-card")).toHaveCount(12);
        await expect(history(page).locator("tbody .numeric")).toHaveText("100");
        await settlePaint(page);
        await expect(english).toBeFocused();
        await english.press("Enter");
        await expect(page.locator("html")).toHaveAttribute("lang", "en");
        await expect(languages).not.toHaveAttribute("open");
      } finally {
        held.release();
      }
    });
  }

  for (const width of [390, 1280]) {
    test(`a late computer response respects an open user menu at ${width}px`, async ({
      page, request, account,
    }) => {
      await page.setViewportSize({ width, height: 844 });
      const computer = await computerWithCards(request, account);
      await login(page, account);
      const held = await delayedResponse(page, `**/v1/computers/${computer.id}`);
      try {
        await page.goto(`${detailsPath(computer.id)}?benchmark=bench_0`);
        await held.ready;
        const menu = await openUserMenu(page);
        held.release();
        await held.finished;
        await expect(latest(page).locator(".benchmark-card")).toHaveCount(12);
        await expect(history(page).locator("tbody .numeric")).toHaveText("100");
        await settlePaint(page);
        await expect(menu).toBeVisible();
        await menu.getByRole("menuitem", { name: "Профиль", exact: true }).click();
        await expect(page).toHaveURL(/\/profile$/);
      } finally {
        held.release();
      }
    });

    test(`a late session response preserves the language control at ${width}px`, async ({
      page, request, account,
    }) => {
      await page.setViewportSize({ width, height: 844 });
      const computer = await computerWithCards(request, account);
      await login(page, account);
      const held = await delayedResponse(page, "**/v1/auth/session");
      try {
        await page.goto(`${detailsPath(computer.id)}?benchmark=bench_0`);
        await held.ready;
        const languages = await openLanguages(page, "keyboard");
        const english = languages.locator('[data-language-option="en"]');
        await english.focus();
        held.release();
        await held.finished;
        await expect(history(page).locator("tbody .numeric")).toHaveText("100");
        await expect(latest(page).locator(".benchmark-card")).toHaveCount(12);
        await settlePaint(page);
        await expect(english).toBeFocused();
        await english.press("Enter");
        await expect(page.locator("html")).toHaveAttribute("lang", "en");
      } finally {
        held.release();
      }
    });
  }

  test("skip to content keeps its hash target instead of reopening the history", async ({
    page, request, account,
  }) => {
    const computer = await computerWithCards(request, account);
    await login(page, account);
    await page.goto(detailsPath(computer.id));
    const card = latest(page).getByRole("link", { name: /bench_0/ });
    await card.click();
    await expect(history(page)).toBeFocused();
    const skip = page.getByRole("link", { name: "Перейти к содержимому", exact: true });
    await skip.focus();
    await skip.press("Enter");
    await expect(page).toHaveURL(/#main$/);
    await settlePaint(page);
    await expect(history(page)).not.toBeFocused();
    await expect(page.getByRole("heading", { level: 1 })).toBeInViewport();
    await card.click();
    await expect(page).not.toHaveURL(/#main$/);
    await expect(history(page)).toBeFocused();
    await expect(history(page).getByRole("heading")).toBeInViewport();
  });
});

test("a late history response follows the current selection and is reused on back and forward", async ({
  page,
  request,
  account,
}) => {
  const computer = await addComputer(request, account, "Быстрое переключение");
  await measurement(request, account, computer.id, "1.0.0", "single_core", 11);
  await measurement(request, account, computer.id, "1.0.0", "multi_core", 22);
  await login(page, account);
  await page.goto(detailsPath(computer.id));
  const requests: string[] = [];
  page.on("request", (request) => {
    if (
      request
        .url()
        .endsWith(
          `/computers/${computer.id}/benchmark-results?latest_only=false`,
        )
    )
      requests.push(request.url());
  });
  const held = await delayedResponse(
    page,
    `**/v1/computers/${computer.id}/benchmark-results?latest_only=false`,
  );
  try {
    await latest(page)
      .getByRole("link", { name: /single_core/ })
      .click();
    await held.ready;
    await expect(history(page).getByRole("status")).toBeVisible();
    await latest(page)
      .getByRole("link", { name: /multi_core/ })
      .click();
    await expect(history(page).getByRole("heading")).toHaveText(
      "История бенчмарка · multi_core",
    );
    await expect(history(page).getByRole("status")).toBeVisible();
    await expect(history(page).locator("tbody tr")).toHaveCount(0);
  } finally {
    held.release();
  }
  await held.finished;
  await expect(history(page).locator("tbody .numeric")).toHaveText(["22"]);
  await page.goBack();
  await expect(history(page).getByRole("heading")).toHaveText(
    "История бенчмарка · single_core",
  );
  await expect(history(page).locator("tbody .numeric")).toHaveText(["11"]);
  await page.goForward();
  await expect(history(page).locator("tbody .numeric")).toHaveText(["22"]);
  await page.goBack();
  await page.goBack();
  await expect(history(page)).toHaveCount(0);
  await expect(latest(page).locator("[aria-current]")).toHaveCount(0);
  await latest(page).getByRole("link", { name: /single_core/ }).click();
  await expect(history(page).locator("tbody .numeric")).toHaveText(["11"]);
  expect(requests).toHaveLength(1);
});

for (const endpoint of ["true", "false"]) {
  test(`a late latest_only=${endpoint} response cannot replace refreshed results`, async ({
    page,
    request,
    account,
  }) => {
    const computer = await addComputer(request, account, "Устаревший ответ");
    await measurement(
      request,
      account,
      computer.id,
      "1.0.0",
      "single_core",
      11,
    );
    await login(page, account);
    const held = await delayedResponse(
      page,
      `**/v1/computers/${computer.id}/benchmark-results?latest_only=${endpoint}`,
    );
    try {
      await page.goto(`${detailsPath(computer.id)}?benchmark=single_core`);
      await held.ready;
      await measurement(
        request,
        account,
        computer.id,
        "2.0.0",
        "single_core",
        22,
      );
      await page.getByRole("button", { name: "Обновить", exact: true }).click();
      await expect(latest(page).locator(".benchmark-version")).toContainText(
        "2.0.0",
      );
      await expect(latest(page).locator(".benchmark-card strong")).toHaveText([
        "22",
      ]);
      await expect(history(page).locator("tbody .benchmark-tooling-version")).toHaveText([
        "2.0.0",
        "1.0.0",
      ]);
      await expect(history(page).locator("tbody .numeric")).toHaveText(["22", "11"]);
    } finally {
      held.release();
    }
    await held.finished;
    await settlePaint(page);
    await expect(latest(page).locator(".benchmark-version")).toContainText(
      "2.0.0",
    );
    await expect(latest(page).locator(".benchmark-card strong")).toHaveText([
      "22",
    ]);
    await expect(history(page).locator("tbody .numeric")).toHaveText(["22", "11"]);
  });
}

test("history loads only after a card click and retries without changing the latest cards", async ({
  page, request, account,
}) => {
  const computer = await addComputer(request, account, "Архив измерений");
  await measurement(request, account, computer.id, "1.0.0", "current_bench", 11);
  await measurement(request, account, computer.id, "2.0.0", "current_bench", 22);
  await login(page, account);
  const path = `**/v1/computers/${computer.id}/benchmark-results?latest_only=false`;
  const requests: string[] = [];
  page.on("request", (request) => {
    if (request.url().endsWith(`/computers/${computer.id}/benchmark-results?latest_only=false`))
      requests.push(request.url());
  });
  await page.goto(detailsPath(computer.id));
  await expect(latest(page).locator(".benchmark-card strong")).toHaveText(["22"]);
  await expect(latest(page).getByRole("combobox")).toHaveCount(0);
  expect(requests).toHaveLength(0);
  await page.route(path, (route) => route.fulfill({ status: 503, json: { detail: "Unavailable" } }));
  await latest(page).getByRole("link", { name: /current_bench/ }).click();
  await expect(history(page).getByRole("alert")).toBeVisible();
  await expect(latest(page).locator(".benchmark-card strong")).toHaveText(["22"]);
  await page.unroute(path);
  await history(page).getByRole("button", { name: "Попробовать снова" }).click();
  await expect(history(page).locator("tbody .numeric")).toHaveText(["22", "11"]);
  expect(requests).toHaveLength(2);
});

test("late responses from a previous computer cannot populate the current computer", async ({
  page,
  request,
  account,
}) => {
  const first = await addComputer(request, account, "Первый компьютер");
  const second = await addComputer(request, account, "Текущий компьютер");
  await measurement(request, account, first.id, "1.0.0", "single_core", 111);
  await measurement(request, account, second.id, "2.0.0", "single_core", 222);
  await login(page, account);
  await page.goto(`power-cells/computers?user_id=${account.id}`);
  await expect(page.locator(".benchmark-summary dd")).toHaveCount(2);
  const held = await delayedResponse(
    page,
    `**/v1/computers/${first.id}/benchmark-results?latest_only=false`,
  );
  try {
    await page.getByRole("link", { name: first.name, exact: true }).click();
    await latest(page)
      .getByRole("link", { name: /single_core/ })
      .click();
    await held.ready;
    await page.getByRole("link", { name: "К списку компьютеров" }).click();
    await page.getByRole("link", { name: second.name, exact: true }).click();
    await latest(page)
      .getByRole("link", { name: /single_core/ })
      .click();
    await expect(history(page).locator("tbody .numeric")).toHaveText(["222"]);
  } finally {
    held.release();
  }
  await held.finished;
  await settlePaint(page);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(
    "Текущий компьютер.",
  );
  await expect(latest(page).locator(".benchmark-card strong")).toHaveText([
    "222",
  ]);
  await expect(history(page).locator("tbody .benchmark-tooling-version")).toHaveText(["2.0.0"]);
  await expect(history(page).locator("tbody .numeric")).toHaveText(["222"]);
  await expect(
    page
      .locator(".benchmark-card strong, .benchmark-history-table td")
      .filter({ hasText: /^111$/ }),
  ).toHaveCount(0);
});

test("512 benchmarks and 201 history versions remain complete and share one history download", async ({
  page,
  request,
  account,
}) => {
  test.setTimeout(90_000);
  const computer = await addComputer(
    request,
    account,
    "Большой набор",
    undefined,
    false,
  );
  const credential = await request.post(
    `${API}/computers/${computer.id}/credentials`,
    { headers: headers(account) },
  );
  expect(credential.status()).toBe(201);
  const agentHeaders = {
    Authorization: `Bearer ${(await credential.json()).token}`,
  };
  const name = (index: number) => `bench_${String(index).padStart(4, "0")}`;
  const agentURL = "http://127.0.0.1:18100/agent/v1";
  const suite = await request.put(`${agentURL}/benchmark-results`, {
    headers: agentHeaders,
    data: {
      benchmarker_version: "2.0.0",
      results: Array.from({ length: 512 }, (_, index) => ({
        benchmark_name: name(index),
        number: index,
      })),
    },
  });
  expect(suite.status()).toBe(200);
  // Older versions arrive later: latest cards use SemVer, history uses upload time.
  for (let index = 0; index < 200; index++) {
    const response = await request.put(`${agentURL}/benchmark-results`, {
      headers: agentHeaders,
      data: {
        benchmarker_version: `1.${index}.0`,
        results: [{ benchmark_name: name(511), number: index }],
      },
    });
    expect(response.status()).toBe(200);
  }
  expect(
    (
      await request.post(`${agentURL}/session`, {
        headers: { ...agentHeaders, "Idempotency-Key": randomUUID() },
        data: { clean_start: true },
      })
    ).status(),
  ).toBe(200);
  const requests: string[] = [];
  page.on("request", (request) => {
    if (
      request
        .url()
        .endsWith(
          `/computers/${computer.id}/benchmark-results?latest_only=false`,
        )
    )
      requests.push(request.url());
  });
  await login(page, account);
  await page.goto(`power-cells/computers?user_id=${account.id}`);
  await expect(page.locator(".benchmark-summary dt")).toHaveText([
    name(0),
    name(1),
  ]);
  await page.getByRole("link", { name: "+510 ещё" }).click();
  await expect(latest(page).locator(".benchmark-card")).toHaveCount(512);
  expect(requests).toHaveLength(0);
  await latest(page)
    .getByRole("link", { name: new RegExp(name(511)) })
    .click();
  const versions = [
    ...Array.from({ length: 200 }, (_, index) => `1.${199 - index}.0`),
    "2.0.0",
  ];
  const scores = [
    ...Array.from({ length: 200 }, (_, index) => String(199 - index)),
    "511",
  ];
  await expect(history(page).locator("tbody .benchmark-tooling-version")).toHaveText(versions);
  await expect(history(page).locator("tbody .numeric")).toHaveText(scores);
  await latest(page)
    .getByRole("link", { name: new RegExp(name(0)) })
    .click();
  await expect(history(page).locator("tbody .numeric")).toHaveText(["0"]);
  await latest(page).getByRole("link", { name: new RegExp(name(511)) }).click();
  await expect(history(page).locator("tbody .benchmark-tooling-version")).toHaveText(versions);
  await expect(history(page).locator("tbody .numeric")).toHaveText(scores);
  await page.goBack();
  await expect(history(page).locator("tbody .numeric")).toHaveText(["0"]);
  expect(requests).toHaveLength(1);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});

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
  await page.route(latestPath, (route) => route.fulfill({ status: 503, json: { detail: "Unavailable" } }));
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
  await page.route(historyPath, (route) => route.fulfill({ status: 503, json: { detail: "Unavailable" } }));
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
  await expect(history(page).locator("tbody .numeric")).toHaveText("42");
  await page.route(latestPath, (route) => route.fulfill({ status: 503, json: { detail: "Unavailable" } }));
  await page.getByRole("button", { name: "Обновить", exact: true }).click();
  await expect(latest(page).getByRole("alert")).toBeVisible();
  await expect(history(page).locator("tbody .numeric")).toHaveText("42");
  await page.unroute(latestPath);
  await latest(page).getByRole("button", { name: "Попробовать снова" }).click();
  await expect(
    latest(page).getByRole("link", { name: /single_core/ }),
  ).toBeVisible();
  // Another run and a newer partial suite preserve the earlier run in history.
  await measurement(request, account, computer.id, "1.0.0", "single_core", 77);
  await measurement(request, account, computer.id, "2.0.0", "new_bench", 88);
  await page.getByRole("button", { name: "Обновить", exact: true }).click();
  await expect(latest(page).locator(".benchmark-card")).toHaveCount(1);
  await expect(latest(page)).toContainText("new_bench");
  await expect(history(page).locator("tbody .numeric")).toHaveText(["77", "42"]);
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
  await expect(history(page).locator("tbody .numeric")).toHaveText("42");
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
  await expect(history(page).locator("tbody .numeric")).toHaveText("42");
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
  await expect(history(page).locator("tbody .numeric")).toHaveText("42");
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

test.describe("responsive benchmark layout", () => {
  for (const width of [320, 390, 540, 640, 768, 1180, 1440]) {
    test(`${width}px: balanced details and readable full-precision scores`, async ({
      page, request, account,
    }) => {
      await page.setViewportSize({ width, height: 1024 });
      const computer = await addComputer(request, account, "Mac mini — Евгений");
      await measurement(request, account, computer.id, "0.0.2", "single_core", 98969.99106397117);
      await measurement(request, account, computer.id, "0.0.2", "multi_core", 652398.8650576969);
      await login(page, account);
      await page.goto(detailsPath(computer.id));
      await expect(latest(page).locator(".benchmark-card")).toHaveCount(2);
      await page.evaluate(() => document.fonts.ready);
      expect(await page.locator(".benchmark-card strong").evaluateAll(scores => scores.map(element => {
        const range = document.createRange();
        range.selectNodeContents(element);
        return range.getClientRects().length;
      }))).toEqual([1, 1]);
      const rows = await page.locator(".computer-details dl > div").evaluateAll(fields => {
        const counts = new Map<number, number>();
        for (const field of fields) {
          const top = Math.round(field.getBoundingClientRect().top);
          counts.set(top, (counts.get(top) || 0) + 1);
        }
        return [...counts.values()];
      });
      expect(rows).toEqual(width <= 540 ? [1, 1, 1, 1] : width < 1360 ? [2, 2] : [4]);
      for (const language of ["ru", "en"] as const) {
        if (language === "en") await selectLanguage(page);
        for (const theme of ["dark", "light"] as const) {
          if (await page.locator("html").getAttribute("data-theme") !== theme)
            await page.locator(".tmc-theme-toggle").click();
          await page.locator('[aria-labelledby="latest-benchmarks-title"]')
            .getByRole("link", { name: /single_core/ }).press("Enter");
          const selected = page.locator('section[aria-labelledby="benchmark-history-title"]');
          await expect(selected).toBeFocused();
          const cell = selected.locator("tbody .numeric");
          await expect(cell).toHaveText(new Intl.NumberFormat(language === "ru" ? "ru-RU" : "en-US", {
            maximumSignificantDigits: 21,
          }).format(98969.99106397117));
          expect(await cell.evaluate(element => {
            const range = document.createRange();
            range.selectNodeContents(element);
            return range.getClientRects().length;
          })).toBe(1);
          expect(await selected.locator("tbody time").evaluate(element => {
            const range = document.createRange();
            range.selectNodeContents(element);
            return range.getClientRects().length;
          })).toBeLessThanOrEqual(2);
          const scroller = selected.getByRole("group");
          if (await scroller.evaluate(element => element.scrollWidth > element.clientWidth)) {
            await scroller.evaluate(element => { element.scrollLeft = 0; });
            await scroller.focus();
            await scroller.press("ArrowRight");
            await expect.poll(() => scroller.evaluate(element => element.scrollLeft)).toBeGreaterThan(0);
            await scroller.press("ArrowLeft");
            await expect.poll(() => scroller.evaluate(element => element.scrollLeft)).toBe(0);
            await scroller.evaluate(element => { element.scrollLeft = element.scrollWidth; });
          }
          expect(await cell.evaluate(element => {
            const range = document.createRange();
            range.selectNodeContents(element);
            const text = range.getBoundingClientRect();
            const container = element.closest(".table-scroll")!.getBoundingClientRect();
            return text.left >= container.left && text.right <= container.right;
          })).toBe(true);
          expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
        }
      }
    });
  }
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
  await expect(history(page).locator("tbody .benchmark-tooling-version")).toHaveText(version);
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
