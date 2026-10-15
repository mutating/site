import { API, addComputer, expect, headers, login, test } from "./fixtures";

test("profile changes survive a network failure and only commit on successful retry", async ({
  page,
  request,
  account,
}) => {
  await login(page, account);
  await page.goto("profile");
  await page.getByLabel("Имя", { exact: true }).fill("Новое имя");
  await page.getByLabel("Фамилия", { exact: true }).fill("Новая фамилия");
  const path = `**/api/users/${account.id}`;
  await page.route(path, (route) => route.abort("failed"));
  const save = page.getByRole("button", { name: "Сохранить изменения" });
  await save.click();
  await expect(page.getByRole("alert")).toContainText(
    "Не удалось связаться с сервером",
  );
  await expect(save).toBeEnabled();
  await expect(page.getByLabel("Имя", { exact: true })).toHaveValue(
    "Новое имя",
  );
  await expect(page.getByLabel("Фамилия", { exact: true })).toHaveValue(
    "Новая фамилия",
  );
  expect(
    (
      await (
        await request.get(`${API}/users/me`, { headers: headers(account) })
      ).json()
    ).first_name,
  ).toBe(account.first_name);
  await page.unroute(path);
  // Hold a real request in flight to check duplicate-submit protection.
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route(path, async (route) => {
    await gate;
    await route.continue();
  });
  try {
    await save.click();
    await expect(
      page.getByRole("button", { name: "Подождите…" }),
    ).toBeDisabled();
  } finally {
    release();
  }
  await expect(
    page.locator(".profile-panel form").getByRole("status"),
  ).toHaveText("Изменения сохранены.");
  await expect(page.getByRole("alert")).toHaveCount(0);
  await page.reload();
  await expect(page.getByLabel("Имя", { exact: true })).toHaveValue(
    "Новое имя",
  );
  await expect(page.getByLabel("Фамилия", { exact: true })).toHaveValue(
    "Новая фамилия",
  );
});

test("organization creation preserves the draft on failure and creates once on retry", async ({
  page,
  request,
  account,
}) => {
  await login(page, account);
  await page.goto("organizations");
  await expect(page.locator(".loading")).toHaveCount(0);
  await page.getByLabel("Название организации").fill("Надёжная команда");
  const path = "**/api/companies";
  await page.route(path, (route) => route.abort("failed"));
  const create = page.getByRole("button", { name: "Создать организацию" });
  await create.click();
  await expect(page.getByRole("alert")).toContainText(
    "Не удалось связаться с сервером",
  );
  await expect(page.getByLabel("Название организации")).toHaveValue(
    "Надёжная команда",
  );
  await expect(create).toBeEnabled();
  expect(
    await (
      await request.get(`${API}/companies`, { headers: headers(account) })
    ).json(),
  ).toEqual([]);
  await page.unroute(path);
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route(path, async (route) => {
    await gate;
    await route.continue();
  });
  try {
    await create.click();
    await expect(
      page.getByRole("button", { name: "Подождите…" }),
    ).toBeDisabled();
  } finally {
    release();
  }
  await expect(
    page.getByRole("heading", { name: "Надёжная команда" }),
  ).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
  await expect(page.getByLabel("Название организации")).toHaveValue("");
  const companies = await (
    await request.get(`${API}/companies`, { headers: headers(account) })
  ).json();
  expect(companies.map((c: { name: string }) => c.name)).toEqual([
    "Надёжная команда",
  ]);
});

for (const endpoint of ["summary", "availability-history"]) {
  test(`capacity recovers from a failed ${endpoint} request without displaying false zeros`, async ({
    page,
    request,
    account,
  }) => {
    await addComputer(request, account, "Узел восстановления");
    await login(page, account);
    const path = `**/api/users/${account.id}/resources/${endpoint}?*`;
    await page.route(path, (route) => route.abort("failed"));
    await page.goto("power-cells");
    const pool = page.getByRole("region", { name: "Личные компьютеры" });
    await expect(pool.getByRole("alert")).toContainText(
      "Не удалось связаться с сервером",
    );
    await expect(pool.getByTestId("computer-count")).toHaveCount(0);
    await expect(pool.locator(".capacity-chart")).toHaveCount(0);
    await page.unroute(path);
    await pool.getByRole("button", { name: "Попробовать снова" }).click();
    await expect(pool.getByTestId("computer-count")).toHaveText("1");
    await expect(pool.locator(".metric-free")).toHaveText(["8", "8"]);
    await expect(pool.locator("[data-series]")).toHaveCount(4);
    await expect(pool.getByRole("alert")).toHaveCount(0);
  });
}

test("refresh shows newly available capacity and recovery does not discard the session", async ({
  page,
  request,
  account,
}) => {
  await login(page, account);
  await page.goto("power-cells");
  const pool = page.getByRole("region", { name: "Личные компьютеры" });
  await expect(pool.getByTestId("computer-count")).toHaveText("0");
  await addComputer(request, account, "Появился после открытия страницы");
  await page.context().setOffline(true);
  await page.getByRole("button", { name: "Обновить", exact: true }).click();
  await expect(pool.getByRole("alert")).toBeVisible();
  await page.context().setOffline(false);
  await page.getByRole("button", { name: "Обновить", exact: true }).click();
  await expect(pool.getByTestId("computer-count")).toHaveText("1");
  await expect(page.getByRole("alert")).toHaveCount(0);
  expect(
    (await page.context().cookies()).some(
      (c) => c.name === "tmc_account_session",
    ),
  ).toBe(true);
});
