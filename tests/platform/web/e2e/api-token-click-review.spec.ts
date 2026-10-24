import { test, expect, login, API, headers } from "./fixtures";

/** An organization filter must keep its name while its token list is loading or has failed, instead of displaying all owners. */
test("organization token owner stays selected through a failed load and retry", async ({ page, request, account }) => {
  const created = await request.post(`${API}/companies`, { headers: headers(account), data: { name: "CI Lab" } });
  expect(created.status()).toBe(201);
  const company = await created.json();
  await login(page, account); await page.goto("/api-tokens");
  const owner = page.getByLabel("Владелец токенов", { exact: true });
  await expect(owner.locator(`option[value="${company.id}"]`)).toBeAttached();
  let release!: () => void;
  const gate = new Promise<void>(resolve => release = resolve);
  let fail = true, intercepted = false;
  await page.route(`${API}/api-tokens?*`, async route => {
    if (route.request().method() !== "GET" || !fail || new URL(route.request().url()).searchParams.get("company_id") !== company.id) return route.continue();
    intercepted = true; await gate;
    await route.fulfill({ status: 503, headers: { "Access-Control-Allow-Origin": "http://127.0.0.1:18101", "Access-Control-Allow-Credentials": "true" }, json: {} });
  });
  try {
    await owner.selectOption(company.id);
    await expect.poll(() => intercepted).toBe(true);
    await expect(owner).toHaveValue(company.id);
    await expect(owner.locator("option:checked")).toContainText("CI Lab");
    release(); await expect(page.getByRole("alert")).toBeVisible();
    await expect(owner).toHaveValue(company.id);
    await expect(owner.locator("option:checked")).toContainText("CI Lab");
    fail = false;
    await page.getByRole("button", { name: /Попробовать снова/ }).click();
    await expect(page.getByRole("alert")).toHaveCount(0);
    await page.getByRole("button", { name: "Создать токен", exact: true }).click();
    await expect(page.getByRole("dialog").getByRole("radio", { name: "Организация CI Lab", exact: true })).toBeChecked();
    await page.keyboard.press("Escape");
  } finally { release(); }
});
