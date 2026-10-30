import { selectLanguage } from "./header-controls";
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

async function openArchive(page: Page) {
  const archive = latest(page).locator("details");
  if (
    !(await archive.evaluate((element: HTMLDetailsElement) => element.open))
  ) {
    await archive.locator("summary").click();
  }
  return archive;
}

async function settlePaint(page: Page) {
  await page.evaluate(
    () =>
      new Promise<void>((resolve) => {
        requestAnimationFrame(() => requestAnimationFrame(() => resolve()));
      }),
  );
}

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
    const archive = await openArchive(page);
    await expect(
      archive.getByLabel("Выберите бенчмарк").locator("option"),
    ).toHaveText([
      "Выберите бенчмарк",
      "legacy_only",
      "multi_core",
      "single_core",
      "tiny_score",
    ]);
    await archive.getByLabel("Выберите бенчмарк").selectOption("legacy_only");
    await expect(history(page).locator("tbody th")).toHaveText(["1.2.0"]);
    await expect(history(page).locator("tbody td")).toHaveText(["777"]);
    await expect(latest(page).locator(".benchmark-card")).toHaveCount(3);
    await expect(
      latest(page)
        .locator(".benchmark-card")
        .filter({ hasText: "legacy_only" }),
    ).toHaveCount(0);
    await latest(page)
      .getByRole("link", { name: /single_core/ })
      .click();
    await expect(history(page).locator("tbody th")).toHaveText(
      expectedVersions,
    );
    await selectLanguage(page);
    await expect(
      page.getByRole("region", { name: "Latest benchmarks", exact: true }),
    ).toContainText("1,234.56789");
    await expect(
      page
        .getByRole("region", { name: "Benchmark history · single_core" })
        .locator("tbody td")
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
    await expect(history(page).locator("tbody td")).toHaveText("42");
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
    await expect(history(page).locator("tbody td")).toHaveText(["22", "11"]);

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
    await expect(history(page).locator("tbody th")).toHaveText(["1.2.0"]);
    await expect(history(page).locator("tbody td")).toHaveText(["11"]);
    await page.getByRole("link", { name: "К списку компьютеров" }).click();
    await expect(summary.locator("dt")).toHaveText(["multi_core"]);
    await expect(summary.locator("dd")).toHaveText(["33"]);
    await page.getByRole("link", { name: computer.name, exact: true }).click();
    await latest(page)
      .getByRole("link", { name: /multi_core/ })
      .click();
    await expect(history(page).locator("tbody td")).toHaveText(["33"]);

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
    const archive = await openArchive(page);
    await expect(
      archive.getByLabel("Выберите бенчмарк").locator("option"),
    ).toHaveText(["Выберите бенчмарк", "single_core"]);
    await archive.getByLabel("Выберите бенчмарк").selectOption("single_core");
    await expect(history(page).locator("tbody td")).toHaveText(["11"]);
    await page.getByRole("link", { name: "К списку компьютеров" }).click();
    await expect(summary.locator("dd")).toHaveText(["11"]);
    await expect(summary).toContainText("1.2.0");
    await page.getByRole("link", { name: computer.name, exact: true }).click();
    await latest(page)
      .getByRole("link", { name: /single_core/ })
      .click();
    await expect(history(page).locator("tbody td")).toHaveText(["11"]);

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
    await openArchive(page);
    await expect(latest(page).getByLabel("Выберите бенчмарк")).toHaveCount(0);
    await page.getByRole("link", { name: "К списку компьютеров" }).click();
    await expect(summary).toHaveText("Результатов пока нет");
    await expect(summary.locator("dd, small")).toHaveCount(0);
  });
}

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
  await expect(history(page).locator("tbody td")).toHaveText(["22"]);
  await page.goBack();
  await expect(history(page).getByRole("heading")).toHaveText(
    "История бенчмарка · single_core",
  );
  await expect(history(page).locator("tbody td")).toHaveText(["11"]);
  await page.goForward();
  await expect(history(page).locator("tbody td")).toHaveText(["22"]);
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
      await expect(history(page).locator("tbody th")).toHaveText([
        "2.0.0",
        "1.0.0",
      ]);
      await expect(history(page).locator("tbody td")).toHaveText(["22", "11"]);
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
    await expect(history(page).locator("tbody td")).toHaveText(["22", "11"]);
  });
}

test("the full benchmark list loads on demand, retries and keeps historical-only names reachable", async ({
  page,
  request,
  account,
}) => {
  const computer = await addComputer(request, account, "Архив измерений");
  await measurement(request, account, computer.id, "1.0.0", "legacy_only", 11);
  await measurement(
    request,
    account,
    computer.id,
    "2.0.0",
    "current_bench",
    22,
  );
  await login(page, account);
  const path = `**/v1/computers/${computer.id}/benchmark-results?latest_only=false`;
  const requests: string[] = [];
  page.on("request", (request) => {
    if (
      request
        .url()
        .includes(
          `/computers/${computer.id}/benchmark-results?latest_only=false`,
        )
    )
      requests.push(request.url());
  });
  await page.goto(detailsPath(computer.id));
  await expect(latest(page).locator(".benchmark-card strong")).toHaveText([
    "22",
  ]);
  expect(requests).toHaveLength(0);
  await page.route(path, (route) => route.fulfill({ status: 503, json: { detail: "Unavailable" } }));
  const archive = await openArchive(page);
  await expect(archive.getByRole("alert")).toBeVisible();
  await expect(archive.getByLabel("Выберите бенчмарк")).toHaveCount(0);
  await expect(latest(page).locator(".benchmark-card strong")).toHaveText([
    "22",
  ]);
  await page.unroute(path);
  await archive.getByRole("button", { name: "Попробовать снова" }).click();
  const select = archive.getByLabel("Выберите бенчмарк");
  await expect(select.locator("option")).toHaveText([
    "Выберите бенчмарк",
    "current_bench",
    "legacy_only",
  ]);
  await select.selectOption("legacy_only");
  await expect(history(page).locator("tbody td")).toHaveText(["11"]);
  await latest(page)
    .getByRole("link", { name: /current_bench/ })
    .click();
  await expect(history(page).locator("tbody td")).toHaveText(["22"]);
  await archive.locator("summary").click();
  await openArchive(page);
  await select.selectOption("legacy_only");
  await expect(history(page).locator("tbody td")).toHaveText(["11"]);
  expect(requests).toHaveLength(2); // The failed request and its retry, no per-selection downloads.
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
    await expect(history(page).locator("tbody td")).toHaveText(["222"]);
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
  await expect(history(page).locator("tbody th")).toHaveText(["2.0.0"]);
  await expect(history(page).locator("tbody td")).toHaveText(["222"]);
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
  // Older versions are uploaded later so upload time cannot substitute for SemVer.
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
    "2.0.0",
    ...Array.from({ length: 200 }, (_, index) => `1.${199 - index}.0`),
  ];
  const scores = [
    "511",
    ...Array.from({ length: 200 }, (_, index) => String(199 - index)),
  ];
  await expect(history(page).locator("tbody th")).toHaveText(versions);
  await expect(history(page).locator("tbody td")).toHaveText(scores);
  await latest(page)
    .getByRole("link", { name: new RegExp(name(0)) })
    .click();
  await expect(history(page).locator("tbody td")).toHaveText(["0"]);
  const archive = await openArchive(page);
  await expect(
    archive.getByLabel("Выберите бенчмарк").locator("option"),
  ).toHaveCount(513);
  await archive.getByLabel("Выберите бенчмарк").selectOption(name(511));
  await expect(history(page).locator("tbody th")).toHaveText(versions);
  await expect(history(page).locator("tbody td")).toHaveText(scores);
  await page.goBack();
  await expect(history(page).locator("tbody td")).toHaveText(["0"]);
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
  await expect(history(page).locator("tbody td")).toHaveText("42");
  await page.route(latestPath, (route) => route.fulfill({ status: 503, json: { detail: "Unavailable" } }));
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
