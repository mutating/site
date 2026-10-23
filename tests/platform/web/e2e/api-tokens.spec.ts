import {
  API,
  expect,
  headers,
  login,
  makeAccount,
  test,
  type Account,
} from "./fixtures";
import type { APIRequestContext } from "@playwright/test";

async function company(
  request: APIRequestContext,
  account: Account,
  name: string,
) {
  const response = await request.post(`${API}/companies`, {
    headers: headers(account),
    data: { name },
  });
  expect(response.status()).toBe(201);
  return response.json();
}
async function token(
  request: APIRequestContext,
  account: Account,
  owner: { user_id?: string; company_id?: string },
  name: string,
) {
  const response = await request.post(`${API}/api-tokens`, {
    headers: headers(account),
    data: { ...owner, name, scopes: ["read", "execute"] },
  });
  expect(response.status()).toBe(201);
  return response.json();
}

test("registry identifies owners and issuers, filters on the server, and excludes logins", async ({
  page,
  request,
  account,
}) => {
  const lab = await company(request, account, "Лаборатория");
  const colleague = await makeAccount(request, "Дмитрий");
  const membership = await request.post(`${API}/memberships`, {
    headers: headers(account),
    data: { company_id: lab.id, user_id: colleague.id, role: "admin" },
  });
  expect(membership.status()).toBe(201);
  await token(request, account, { user_id: account.id }, "login");
  await token(request, colleague, { company_id: lab.id }, "Командный CI");
  await login(page, account);
  await page
    .getByRole("navigation", { name: "Навигация кабинета" })
    .getByRole("link", { name: "API-токены", exact: true })
    .click();
  await expect(page.locator(".token-registry-row")).toHaveCount(2);
  await expect(
    page.getByText("Личный — от вашего имени", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Организация — от имени компании", { exact: true }),
  ).toBeVisible();
  await expect(page.locator(".pagination")).toHaveCount(0);
  const corporate = page.getByRole("article", {
    name: "Командный CI",
    exact: true,
  });
  await expect(corporate.locator(".token-registry-owner")).toContainText(
    "Организация",
  );
  await expect(corporate.locator(".token-registry-owner")).toContainText(
    "Лаборатория",
  );
  await expect(corporate.locator(".token-registry-name")).toContainText(
    "Дмитрий",
  );
  await page
    .getByLabel("Владелец токенов", { exact: true })
    .selectOption("personal");
  await expect(page.locator(".token-registry-row h2")).toHaveText(["login"]);
  await page.reload();
  await expect(
    page.getByLabel("Владелец токенов", { exact: true }),
  ).toHaveValue("personal");
  await page
    .getByLabel("Владелец токенов", { exact: true })
    .selectOption(lab.id);
  await expect(page.locator(".token-registry-row h2")).toHaveText([
    "Командный CI",
  ]);
  await page.goto(`organizations/${lab.id}`);
  await page.getByRole("link", { name: "API-токены ↗", exact: true }).click();
  await expect(
    page.getByLabel("Владелец токенов", { exact: true }),
  ).toHaveValue(lab.id);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});

for (const kind of ["personal", "company"] as const) {
  test(`create ${kind} token once, copy, close, reload, execute and revoke`, async ({
    page,
    request,
    account,
  }) => {
    const lab = await company(request, account, "Лаборатория");
    await page.addInitScript(() => {
      Object.defineProperty(navigator, "clipboard", {
        configurable: true,
        value: { writeText: async () => {} },
      });
    });
    await login(page, account);
    await page.goto(
      `api-tokens?owner=${kind === "company" ? lab.id : "personal"}`,
    );
    const trigger = page.getByRole("button", {
      name: "Создать токен",
      exact: true,
    });
    await expect(trigger).toBeEnabled();
    await trigger.click();
    const dialog = page.getByRole("dialog");
    await expect(
      dialog.getByRole("radio", {
        name: kind === "company" ? /Организация/ : /Личный/,
      }),
    ).toBeChecked();
    await expect(dialog.getByLabel("Организация-владелец")).toHaveCount(0);
    await expect(dialog.locator(".token-owner-context")).toContainText(
      kind === "company"
        ? "Выпускает участник · Алиса"
        : "Выпускаете вы · Алиса",
    );
    const name = kind === "company" ? "CI лаборатории" : "Мой терминал";
    await dialog.getByLabel("Название токена", { exact: true }).fill(name);
    const days = kind === "personal" ? 90 : 7;
    await dialog.getByLabel("Срок действия").selectOption(String(days));
    if (kind === "personal") {
      await dialog.getByText("Дополнительные права", { exact: true }).click();
      await dialog
        .getByRole("checkbox", { name: "Управление ресурсами и доступом" })
        .check();
    }
    let posts = 0;
    let release!: () => void;
    let received!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const pending = new Promise<void>((resolve) => {
      received = resolve;
    });
    await page.route(`${API}/api-tokens`, async (route) => {
      if (route.request().method() !== "POST") return route.continue();
      posts++;
      const response = await route.fetch();
      expect(response.status()).toBe(201);
      received();
      await gate;
      await route.fulfill({ response });
    });
    try {
      await dialog
        .getByRole("button", {
          name: /^Создать (личный токен|токен организации)$/,
        })
        .click();
      await pending;
      await expect(
        dialog.getByRole("button", { name: "Закрыть диалог" }),
      ).toBeDisabled();
      await dialog
        .locator("form")
        .evaluate((form) => (form as HTMLFormElement).requestSubmit());
      await page.keyboard.press("Escape");
      await expect(dialog).toBeVisible();
    } finally {
      release();
    }
    await expect(
      dialog.getByRole("heading", { name: "Токен создан", exact: true }),
    ).toBeVisible();
    expect(posts).toBe(1);
    await expect(
      dialog.locator(".token-owner-summary .token-owner-badge"),
    ).toContainText(kind === "company" ? "Лаборатория" : "Личный");
    await expect(dialog.locator(".token-owner-summary")).toContainText(
      "Выпустил: Алиса",
    );
    await expect(dialog.locator(".token-issued-details time")).toBeVisible();
    const secret = await dialog
      .getByLabel("API-токен", { exact: true })
      .inputValue();
    expect(secret.startsWith("thr_")).toBe(true);
    const auth = { Authorization: `Bearer ${secret}` };
    const checked = await request.get(`${API}/auth/check`, { headers: auth });
    expect(checked.status()).toBe(200);
    expect((await checked.json()).scopes).toEqual(
      kind === "personal" ? ["execute", "manage", "read"] : ["execute", "read"],
    );
    expect(
      await page.evaluate(
        (value) =>
          JSON.stringify({
            local: { ...localStorage },
            session: { ...sessionStorage },
          }).includes(value),
        secret,
      ),
    ).toBe(false);
    await dialog.getByRole("button", { name: "Скопировать токен" }).click();
    await expect(dialog.getByRole("status")).toHaveText("Токен скопирован.");
    await dialog.getByRole("button", { name: "Готово", exact: true }).click();
    await expect(dialog).toHaveCount(0);
    await expect(trigger).toBeFocused();
    await expect(
      page.getByRole("article", { name, exact: true }),
    ).toBeVisible();
    await page.reload();
    await expect(page.getByLabel("API-токен", { exact: true })).toHaveCount(0);
    const row = page.getByRole("article", { name, exact: true });
    const expires = Date.parse(
      (await row.locator("time").getAttribute("datetime"))!,
    );
    expect(expires - Date.now()).toBeGreaterThan((days - 1) * 86400000);
    expect(expires - Date.now()).toBeLessThan((days + 1) * 86400000);
    await row
      .getByRole("button", { name: "Отозвать токен", exact: true })
      .click();
    await dialog.getByRole("textbox").fill(name);
    await dialog
      .getByRole("button", { name: "Подтвердить", exact: true })
      .click();
    await expect(row.locator(".token-status")).toHaveText("Отозван");
    expect(
      (await request.get(`${API}/auth/check`, { headers: auth })).status(),
    ).toBe(401);
  });
}

/**
 * Replacing the submitted form used to leave focus on BODY without announcing the
 * one-time result. Announce the owner and warning, then restore the trigger even
 * while the registry's background refresh is still pending.
 */
test("keyboard issuance announces the result and closing restores focus while the registry refresh is pending", async ({
  page,
  account,
}) => {
  await login(page, account);
  await page.goto("api-tokens");
  const trigger = page.getByRole("button", {
    name: "Создать токен",
    exact: true,
  });
  await trigger.click();
  let release!: () => void;
  let received!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  const pending = new Promise<void>((resolve) => {
    received = resolve;
  });
  await page.route(`${API}/api-tokens?**`, async (route) => {
    const response = await route.fetch();
    received();
    await gate;
    await route.fulfill({ response });
  });
  try {
    const dialog = page.getByRole("dialog");
    await dialog
      .getByLabel("Название токена", { exact: true })
      .fill("Фоновое обновление");
    await dialog.getByLabel("Название токена", { exact: true }).press("Enter");
    await pending;
    const title = dialog.getByRole("heading", {
      name: "Токен создан",
      exact: true,
    });
    await expect(title).toBeFocused();
    await expect(title).toHaveAccessibleDescription(
      /Личный.*Алиса.*Сохраните токен сейчас\. После закрытия секрет больше не будет показан\./,
    );
    await page.keyboard.press("Tab");
    await expect(dialog.getByLabel("API-токен", { exact: true })).toBeFocused();
    await dialog.getByRole("button", { name: "Готово", exact: true }).click();
    await expect(trigger).toBeFocused();
    await expect(dialog).toHaveCount(0);
  } finally {
    release();
  }
  await expect(
    page.getByRole("article", { name: "Фоновое обновление", exact: true }),
  ).toBeVisible();
});

/**
 * A short dialog used to retain the form's scroll offset after issuance, leaving
 * the focused result heading above its visible area. Show the result from its top.
 */
test("issuance shows the focused result after scrolling a short landscape dialog", async ({
  page,
  account,
}) => {
  await page.setViewportSize({ width: 844, height: 390 });
  await login(page, account);
  await page.goto("api-tokens");
  await page
    .getByRole("button", { name: "Создать токен", exact: true })
    .click();
  const dialog = page.getByRole("dialog");
  await dialog
    .getByLabel("Название токена", { exact: true })
    .fill("Landscape token");
  await dialog.evaluate((element) => {
    element.scrollTop = element.scrollHeight;
  });
  expect(await dialog.evaluate((element) => element.scrollTop)).toBeGreaterThan(
    0,
  );
  await dialog
    .getByRole("button", { name: "Создать личный токен", exact: true })
    .click();
  const title = dialog.getByRole("heading", {
    name: "Токен создан",
    exact: true,
  });
  await expect(title).toBeFocused();
  await expect(title).toBeInViewport({ ratio: 1 });
  await expect(dialog.locator(".token-owner-summary")).toBeInViewport({
    ratio: 1,
  });
  await dialog.getByRole("button", { name: "Готово", exact: true }).click();
  await expect(dialog).toHaveCount(0);
});

/**
 * Selecting read-only used to retain a hidden manage permission from the advanced
 * options, issuing a writable token. Read-only must clear management and prevent
 * re-enabling it until the user leaves read-only mode, for either owner type.
 */
for (const kind of ["personal", "company"] as const) {
  test(`read-only ${kind} tokens cannot retain or enable management`, async ({
    page,
    request,
    account,
  }) => {
    const lab = await company(request, account, "Лаборатория");
    await login(page, account);
    await page.goto(
      `api-tokens?owner=${kind === "company" ? lab.id : "personal"}`,
    );
    await page
      .getByRole("button", { name: "Создать токен", exact: true })
      .click();
    const dialog = page.getByRole("dialog");
    const readOnly = dialog.getByRole("radio", {
      name: "Только чтение",
      exact: true,
    });
    const manage = dialog.getByRole("checkbox", {
      name: "Управление ресурсами и доступом",
      exact: true,
    });
    const advanced = dialog.getByText("Дополнительные права", { exact: true });
    await advanced.click();
    await manage.check();
    await advanced.click();
    await readOnly.check();
    await advanced.click();
    await expect(manage).not.toBeChecked();
    await expect(manage).toBeDisabled();
    await dialog
      .getByRole("radio", { name: "Чтение и выполнение команд", exact: true })
      .check();
    await expect(manage).toBeEnabled();
    await manage.check();
    await readOnly.check();
    await expect(manage).not.toBeChecked();
    await expect(manage).toBeDisabled();
    await dialog
      .getByLabel("Название токена", { exact: true })
      .fill("Strictly read only");
    await dialog
      .getByRole("button", {
        name:
          kind === "company"
            ? "Создать токен организации"
            : "Создать личный токен",
        exact: true,
      })
      .click();
    const secret = await dialog
      .getByLabel("API-токен", { exact: true })
      .inputValue();
    const auth = { Authorization: `Bearer ${secret}` };
    const listed = await request.get(`${API}/api-tokens`, { headers: auth });
    expect(listed.status()).toBe(200);
    expect(
      (await listed.json()).find(
        (value: { name: string }) => value.name === "Strictly read only",
      ).scopes,
    ).toEqual(["read"]);
    expect(
      (await request.get(`${API}/auth/check`, { headers: auth })).status(),
    ).toBe(403);
    expect(
      (
        await request.patch(`${API}/users/${account.id}`, {
          headers: auth,
          data: { first_name: "Must not change" },
        })
      ).status(),
    ).toBe(403);
  });
}

/**
 * Valid 100-character first/last names used to overflow the issuer line, adding
 * horizontal scrolling to both creation and the one-time result on a phone.
 */
test("long valid issuer names wrap inside creation and the issued result", async ({
  page,
  request,
  account,
}) => {
  const response = await request.patch(`${API}/users/${account.id}`, {
    headers: headers(account),
    data: { first_name: "А".repeat(100), last_name: "Б".repeat(100) },
  });
  expect(response.status()).toBe(200);
  await page.setViewportSize({ width: 320, height: 640 });
  await login(page, { ...account, first_name: "А".repeat(100) });
  await page.goto("api-tokens");
  await page
    .getByRole("button", { name: "Создать токен", exact: true })
    .click();
  const dialog = page.getByRole("dialog");
  const fits = () =>
    dialog.evaluate((element) => element.scrollWidth <= element.clientWidth);
  expect(await fits()).toBe(true);
  await dialog
    .getByLabel("Название токена", { exact: true })
    .fill("Long issuer name");
  await dialog
    .getByRole("button", { name: "Создать личный токен", exact: true })
    .click();
  await expect(dialog.getByLabel("API-токен", { exact: true })).toBeVisible();
  expect(await fits()).toBe(true);
  await dialog.getByRole("button", { name: "Готово", exact: true }).click();
});

test("creation allows choosing between personal and organization ownership and handles denied clipboard", async ({
  page,
  request,
  account,
}) => {
  const lab = await company(request, account, "Лаборатория");
  await page.addInitScript(() =>
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: {
        writeText: async () => {
          throw new Error("Denied");
        },
      },
    }),
  );
  await login(page, account);
  await page.goto("api-tokens");
  await page
    .getByRole("button", { name: "Создать токен", exact: true })
    .click();
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("radio", { name: /Организация/ }).check();
  await expect(
    dialog.getByRole("radio", { name: "Организация Лаборатория", exact: true }),
  ).toBeChecked();
  await expect(dialog.getByLabel("Организация-владелец")).toHaveCount(0);
  await dialog.getByRole("radio", { name: /Личный/ }).check();
  await expect(dialog.getByLabel("Организация-владелец")).toHaveCount(0);
  await dialog
    .getByLabel("Название токена", { exact: true })
    .fill("Только чтение");
  await dialog
    .getByRole("radio", { name: "Только чтение", exact: true })
    .check();
  await dialog
    .getByRole("button", { name: /^Создать (личный токен|токен организации)$/ })
    .click();
  await expect(
    dialog.getByRole("heading", { name: "Токен создан", exact: true }),
  ).toBeVisible();
  await dialog.getByRole("button", { name: "Скопировать токен" }).click();
  await expect(dialog.getByRole("status")).toHaveText(
    "Токен выделен. Скопируйте его вручную.",
  );
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  const row = page.getByRole("article", { name: "Только чтение", exact: true });
  await expect(row.locator(".token-registry-scopes")).toHaveText("Чтение");
  await expect(row.locator(".token-registry-owner")).toContainText("Личный");
});

test("organization view and manage permissions are independent; loss of manage rejects an open dialog", async ({
  page,
  request,
  account,
}) => {
  const creator = await makeAccount(request, "Владелец");
  const readOnly = await company(request, creator, "Студия");
  const manageOnly = await company(request, creator, "Лаборатория");
  let manageMembership = "";
  for (const [space, permissions] of [
    [readOnly, ["tokens.read"]],
    [manageOnly, ["tokens.manage"]],
  ] as const) {
    const response = await request.post(`${API}/memberships`, {
      headers: headers(creator),
      data: {
        company_id: space.id,
        user_id: account.id,
        role: "user",
        permissions,
      },
    });
    expect(response.status()).toBe(201);
    if (space.id === manageOnly.id)
      manageMembership = (await response.json()).id;
  }
  await token(request, creator, { company_id: readOnly.id }, "Отчёты Студии");
  await login(page, account);
  await page.goto(`api-tokens?owner=${readOnly.id}`);
  await expect(
    page.getByRole("article", { name: "Отчёты Студии", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Создать токен", exact: true }),
  ).toBeDisabled();
  await expect(
    page.getByRole("button", { name: "Отозвать токен", exact: true }),
  ).toHaveCount(0);
  await expect(page.locator(".token-permission-note")).toContainText(
    "просматривать",
  );
  await page
    .getByLabel("Владелец токенов", { exact: true })
    .selectOption(manageOnly.id);
  await expect(page.locator(".token-permission-note")).toContainText(
    "нет права просмотра",
  );
  await page
    .getByRole("button", { name: "Создать токен", exact: true })
    .click();
  const dialog = page.getByRole("dialog");
  await dialog
    .getByLabel("Название токена", { exact: true })
    .fill("Отклонённый токен");
  expect(
    (
      await request.patch(`${API}/memberships/${manageMembership}`, {
        headers: headers(creator),
        data: { permissions: ["tokens.read"] },
      })
    ).status(),
  ).toBe(200);
  await dialog
    .getByRole("button", { name: /^Создать (личный токен|токен организации)$/ })
    .click();
  await expect(dialog.getByRole("alert")).toContainText("нет прав");
  await expect(dialog.getByLabel("API-токен", { exact: true })).toHaveCount(0);
  await dialog.getByRole("button", { name: "Закрыть диалог" }).click();
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "API-токенов пока нет", exact: true }),
  ).toBeVisible();
});

test("multiple organizations have an explicit selector and the dialog preserves the chosen owner", async ({
  page,
  request,
  account,
}) => {
  await page.setViewportSize({ width: 320, height: 640 });
  await company(request, account, "Лаборатория");
  const studio = await company(request, account, "Студия");
  await login(page, account);
  await page.goto("api-tokens");
  const trigger = page.getByRole("button", {
    name: "Создать токен",
    exact: true,
  });
  await trigger.click();
  const dialog = page.getByRole("dialog");
  await expect(
    dialog.getByRole("radio", { name: "Личный Алиса", exact: true }),
  ).toBeChecked();
  await dialog.getByRole("radio", { name: /Организация/ }).check();
  await dialog.getByLabel("Организация-владелец").selectOption(studio.id);
  await expect(
    dialog.getByRole("radio", { name: "Организация Студия", exact: true }),
  ).toBeChecked();
  await expect(dialog.locator(".token-owner-context")).toContainText(
    "Выпускает участник · Алиса",
  );
  await dialog.getByRole("radio", { name: /Личный/ }).check();
  await expect(dialog.getByLabel("Организация-владелец")).toHaveCount(0);
  await dialog.getByRole("radio", { name: /Организация/ }).check();
  await expect(dialog.getByLabel("Организация-владелец")).toHaveValue(
    studio.id,
  );
  const create = dialog.getByRole("button", {
    name: "Создать токен организации",
    exact: true,
  });
  const actionBounds = (await create.boundingBox())!;
  const dialogBounds = (await dialog.boundingBox())!;
  expect(actionBounds.y).toBeGreaterThanOrEqual(dialogBounds.y);
  expect(actionBounds.y + actionBounds.height).toBeLessThanOrEqual(
    dialogBounds.y + dialogBounds.height,
  );
  expect(actionBounds.y + actionBounds.height).toBeLessThanOrEqual(640);
  await dialog
    .getByLabel("Название токена", { exact: true })
    .fill("Сборки Студии");
  await dialog
    .getByRole("button", { name: "Создать токен организации", exact: true })
    .click();
  await expect(dialog.getByLabel("API-токен", { exact: true })).toBeVisible();
  await expect(
    dialog.locator(".token-owner-summary .token-owner-badge strong"),
  ).toHaveText("Студия");
  await dialog
    .getByRole("button", { name: "Закрыть диалог", exact: true })
    .click();
  await expect(trigger).toBeFocused();
  await page
    .getByLabel("Владелец токенов", { exact: true })
    .selectOption(studio.id);
  await expect(page.locator(".token-registry-row h2")).toHaveText([
    "Сборки Студии",
  ]);
  await trigger.click();
  await expect(dialog.getByLabel("API-токен", { exact: true })).toHaveCount(0);
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(trigger).toBeFocused();
});

test("pagination remains scoped to the selected owner and resets after a filter change", async ({
  page,
  request,
  account,
}) => {
  for (let i = 0; i < 11; i++)
    await token(request, account, { user_id: account.id }, `Токен ${i}`);
  const lab = await company(request, account, "Лаборатория");
  await token(request, account, { company_id: lab.id }, "Командный токен");
  await login(page, account);
  await page.goto("api-tokens?owner=personal");
  await expect(page.locator(".token-registry-row")).toHaveCount(10);
  await page.getByRole("button", { name: "Далее →", exact: true }).click();
  await expect(page.locator(".token-registry-row")).toHaveCount(1);
  await expect(page).toHaveURL(/page=2/);
  await page
    .getByLabel("Владелец токенов", { exact: true })
    .selectOption(lab.id);
  await expect(page.locator(".token-registry-row h2")).toHaveText([
    "Командный токен",
  ]);
  await expect(page).not.toHaveURL(/page=/);
});
