import { API, expect, headers, makeAccount, startSession, test } from "./fixtures";

/**
 * A successful company refresh confirms revoked token visibility even if the
 * following registry request stalls or fails. Cached token names,
 * rows, page counts and an open revoke dialog must disappear before that request
 * completes; retrying must not resurrect them. Counts must also be invalidated
 * when every cached row is personal and the inaccessible rows are on another page.
 */
for (const loss of ["manage-only", "no-permissions", "removed", "hidden-page"] as const) {
  test(`all-owner token cache follows confirmed access loss: ${loss}`, async ({ page, request, account }) => {
    const creator = await makeAccount(request, "Владелец");
    const created = await request.post(`${API}/companies`, { headers: headers(creator), data: { name: "Закрытая команда" } });
    expect(created.status()).toBe(201);
    const company = await created.json();
    const joined = await request.post(`${API}/memberships`, { headers: headers(creator), data: {
      company_id: company.id, user_id: account.id, role: "user", permissions: ["tokens.read", "tokens.manage"],
    } });
    expect(joined.status()).toBe(201);
    const membership = await joined.json();
    for (let index = 0; index < 11; index++) {
      expect((await request.post(`${API}/api-tokens`, { headers: headers(creator), data: {
        company_id: company.id, name: `Закрытый токен ${index}`, scopes: ["read"],
      } })).status()).toBe(201);
    }
    for (let index = 0; index < (loss === "hidden-page" ? 10 : 1); index++) {
      expect((await request.post(`${API}/api-tokens`, { headers: headers(account), data: {
        user_id: account.id, name: index ? `Личный доступ ${index}` : "Личный доступ", scopes: ["read"],
      } })).status()).toBe(201);
    }
    await startSession(page, account);
    await page.goto("/api-tokens");
    await expect(page.locator(".token-registry-row")).toHaveCount(10);
    await expect(page.locator(".pagination")).toBeVisible();
    if (loss === "removed") {
      expect((await request.delete(`${API}/memberships/${membership.id}`, { headers: headers(creator) })).status()).toBe(204);
    } else {
      expect((await request.patch(`${API}/memberships/${membership.id}`, { headers: headers(creator), data: {
        permissions: loss === "manage-only" ? ["tokens.manage"] : [],
      } })).status()).toBe(200);
    }
    let companyReceived = false, listPending = false, listRequests = 0;
    let releaseCompanies!: () => void, releaseList!: () => void;
    const companiesGate = new Promise<void>(resolve => releaseCompanies = resolve);
    const listGate = new Promise<void>(resolve => releaseList = resolve);
    await page.route(`${API}/companies?*`, async route => {
      const response = await route.fetch();
      companyReceived = true;
      await companiesGate;
      await route.fulfill({ response });
    });
    await page.route(`${API}/api-tokens?*`, async route => {
      listPending = true;
      listRequests++;
      await listGate;
      await route.fulfill({ status: 503, json: {} });
    });
    try {
      await page.getByRole("button", { name: "Обновить", exact: true }).click();
      await expect.poll(() => companyReceived).toBe(true);
      await page.locator(".token-registry-row").first().getByRole("button", { name: "Отозвать токен", exact: true }).click();
      await expect(page.getByRole("dialog")).toBeVisible();
      releaseCompanies();
      await expect.poll(() => listPending).toBe(true);
      await expect(page.locator(".token-registry-row")).toHaveCount(0);
      await expect(page.getByRole("dialog")).toHaveCount(0);
      await expect(page.locator(".pagination")).toHaveCount(0);
      await expect(page.getByRole("button", { name: "Создать токен", exact: true })).toBeDisabled();
      releaseList();
      await expect(page.getByRole("alert")).toBeVisible();
      await page.getByRole("button", { name: /Попробовать снова/ }).click();
      await expect.poll(() => listRequests).toBe(2);
      await expect(page.getByRole("button", { name: "Обновить", exact: true })).toBeEnabled();
      await expect(page.getByRole("alert")).toBeVisible();
      await expect(page.locator(".token-registry-row")).toHaveCount(0);
      await expect(page.getByRole("dialog")).toHaveCount(0);
    } finally { releaseCompanies(); releaseList(); }
    await page.unrouteAll({ behavior: "wait" });
    await page.getByLabel("Владелец токенов", { exact: true }).selectOption("personal");
    await expect(page.getByRole("article", { name: "Личный доступ", exact: true })).toBeVisible();
    await expect(page.getByRole("alert")).toHaveCount(0);
  });
}

/**
 * A denied create/revoke must revalidate cached token visibility. Losing only
 * manage permission still allows the list, while a removed membership must
 * dismiss the dialog and erase its organization's cached token names.
 */
for (const action of ["create", "revoke"] as const) {
  for (const loss of ["removed", "removed-offline", "read-only"] as const) {
    test(`denied token ${action} revalidates visibility after ${loss}`, async ({ page, request, account }) => {
      const creator = await makeAccount(request, "Владелец");
      const response = await request.post(`${API}/companies`, { headers: headers(creator), data: { name: "Закрытая команда" } });
      expect(response.status()).toBe(201);
      const company = await response.json();
      const joined = await request.post(`${API}/memberships`, { headers: headers(creator), data: {
        company_id: company.id, user_id: account.id, role: "user", permissions: ["tokens.read", "tokens.manage"],
      } });
      expect(joined.status()).toBe(201);
      const membership = await joined.json();
      expect((await request.post(`${API}/api-tokens`, { headers: headers(creator), data: {
        company_id: company.id, name: "Закрытый токен", scopes: ["read"],
      } })).status()).toBe(201);
      await startSession(page, account);
      await page.goto(`/api-tokens?owner=${company.id}`);
      const row = page.getByRole("article", { name: "Закрытый токен", exact: true });
      const dialog = page.getByRole("dialog");
      await expect(row).toBeVisible();
      if (action === "create") {
        await page.getByRole("button", { name: "Создать токен", exact: true }).click();
        await dialog.getByLabel("Название токена", { exact: true }).fill("Новый клиент");
      } else {
        await row.getByRole("button", { name: "Отозвать токен", exact: true }).click();
        await dialog.getByRole("textbox").fill("Закрытый токен");
      }
      if (loss === "removed" || loss === "removed-offline") {
        expect((await request.delete(`${API}/memberships/${membership.id}`, { headers: headers(creator) })).status()).toBe(204);
      } else {
        expect((await request.patch(`${API}/memberships/${membership.id}`, { headers: headers(creator), data: { permissions: ["tokens.read"] } })).status()).toBe(200);
      }
      if (loss === "removed-offline") await page.route(`${API}/companies?*`, route => route.fulfill({ status: 503, json: {} }));
      await dialog.getByRole("button", { name: action === "create" ? "Создать токен организации" : "Подтвердить", exact: true }).click();
      if (loss === "removed" || loss === "removed-offline") {
        await expect(row).toHaveCount(0);
        await expect(dialog).toHaveCount(0);
      } else {
        await expect(dialog.getByRole("alert")).toBeVisible();
        await expect(page.locator(".token-permission-note")).toBeVisible();
        await expect(row).toBeVisible();
        await page.keyboard.press("Escape");
        await expect(row.getByRole("button", { name: "Отозвать токен", exact: true })).toHaveCount(0);
      }
    });
  }
}

/** Browser Back changes the owner filter even under a modal; it must not retain an old company's revoke prompt on the personal page. */
test("owner-filter navigation discards an organization revoke prompt", async ({ page, request, account }) => {
  const creator = await makeAccount(request, "Владелец");
  const created = await request.post(`${API}/companies`, { headers: headers(creator), data: { name: "Закрытая команда" } });
  const company = await created.json();
  const joined = await request.post(`${API}/memberships`, { headers: headers(creator), data: {
    company_id: company.id, user_id: account.id, role: "user", permissions: ["tokens.read", "tokens.manage"],
  } });
  const membership = await joined.json();
  expect((await request.post(`${API}/api-tokens`, { headers: headers(creator), data: {
    company_id: company.id, name: "Закрытый токен", scopes: ["read"],
  } })).status()).toBe(201);
  await startSession(page, account);
  await page.goto("/api-tokens?owner=personal");
  await expect(page.locator(".empty-state")).toBeVisible();
  await page.getByLabel("Владелец токенов", { exact: true }).selectOption("all");
  await page.getByRole("article", { name: "Закрытый токен", exact: true }).getByRole("button", { name: "Отозвать токен", exact: true }).click();
  expect((await request.delete(`${API}/memberships/${membership.id}`, { headers: headers(creator) })).status()).toBe(204);
  await page.goBack();
  await expect(page).toHaveURL(/owner=personal/);
  await expect(page.locator(".empty-state")).toBeVisible();
  await expect(page.getByRole("dialog")).toHaveCount(0);
});

/** Issued organization secrets follow refreshed access even on the personal filter; manage-only issuance remains authorized without list access. */
for (const owner of ["personal", "all"]) {
for (const keepManage of [false, true]) {
  test(`issued organization secret follows current permissions: ${owner} manage=${keepManage}`, async ({ page, request, account }) => {
    const creator = await makeAccount(request, "Владелец");
    const created = await request.post(`${API}/companies`, { headers: headers(creator), data: { name: "Закрытая команда" } });
    const company = await created.json();
    const joined = await request.post(`${API}/memberships`, { headers: headers(creator), data: {
      company_id: company.id, user_id: account.id, role: "user", permissions: ["tokens.read", "tokens.manage"],
    } });
    const membership = await joined.json();
    expect((await request.post(`${API}/api-tokens`, { headers: headers(creator), data: {
      company_id: company.id, name: "Прежний клиент", scopes: ["read"],
    } })).status()).toBe(201);
    await startSession(page, account);
    await page.goto(`/api-tokens?owner=${owner}`);
    await page.getByRole("button", { name: "Создать токен", exact: true }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByRole("radio", { name: /Организация/ }).check();
    await dialog.getByLabel("Название токена", { exact: true }).fill("Новый клиент");
    let refreshed = false;
    await page.route(`${API}/api-tokens`, async route => {
      const response = await route.fetch();
      expect(response.status()).toBe(201);
      expect((await request.patch(`${API}/memberships/${membership.id}`, { headers: headers(creator), data: { permissions: keepManage ? ["tokens.manage"] : [] } })).status()).toBe(200);
      await route.fulfill({ response, json: { ...await response.json(), token: `ORG-${"0123456789abcdef".repeat(4)}` } });
    });
    await page.route(`${API}/companies?*`, async route => {
      const response = await route.fetch();
      refreshed = true;
      await route.fulfill({ response });
    });
    await dialog.getByRole("button", { name: "Создать токен организации", exact: true }).click();
    await expect.poll(() => refreshed).toBe(true);
    if (keepManage) {
      await expect(dialog.getByRole("heading", { name: "Токен создан", exact: true })).toBeVisible();
      await expect(dialog.getByLabel("API-токен", { exact: true })).toHaveValue(/^ORG-/);
    } else await expect(dialog).toHaveCount(0);
    await page.unrouteAll({ behavior: "wait" });
  });
}
}

/**
 * A membership granted between the owners and registry reads can produce an
 * authorized token whose owner was absent from that first snapshot. A later
 * confirmed removal must still invalidate its cached metadata before a 503.
 */
test("fresh owner denial also clears tokens absent from the older company snapshot", async ({ page, request, account }) => {
  const creator = await makeAccount(request, "Владелец");
  const created = await request.post(`${API}/companies`, { headers: headers(creator), data: { name: "Новая команда" } });
  expect(created.status()).toBe(201);
  const company = await created.json();
  expect((await request.post(`${API}/api-tokens`, { headers: headers(creator), data: {
    company_id: company.id, name: "Временно доступный токен", scopes: ["read"],
  } })).status()).toBe(201);
  let membershipId = "";
  await page.route(`${API}/companies?*`, async route => {
    const response = await route.fetch();
    expect(await response.json()).toEqual([]);
    const joined = await request.post(`${API}/memberships`, { headers: headers(creator), data: {
      company_id: company.id, user_id: account.id, role: "user", permissions: ["tokens.read"],
    } });
    expect(joined.status()).toBe(201);
    membershipId = (await joined.json()).id;
    await route.fulfill({ response });
  });
  await startSession(page, account);
  await page.goto("/api-tokens");
  const row = page.getByRole("article", { name: "Временно доступный токен", exact: true });
  await expect(row).toBeVisible();
  await page.unrouteAll({ behavior: "wait" });
  expect((await request.delete(`${API}/memberships/${membershipId}`, { headers: headers(creator) })).status()).toBe(204);
  await page.route(`${API}/api-tokens?*`, route => route.fulfill({ status: 503, json: {} }));
  await page.getByRole("button", { name: "Обновить", exact: true }).click();
  await expect(page.getByRole("alert")).toBeVisible();
  await expect(row).toHaveCount(0);
  await page.unrouteAll({ behavior: "wait" });
});
