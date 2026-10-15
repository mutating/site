import {
  test,
  expect,
  login,
  API,
  headers,
  makeAccount,
  addComputer,
  type Account,
} from "./fixtures";
import type { APIRequestContext } from "@playwright/test";

async function space(
  request: APIRequestContext,
  owner: Account,
  name: string,
  computers = 0,
) {
  const response = await request.post(`${API}/companies`, {
    headers: headers(owner),
    data: { name },
  });
  expect(response.status()).toBe(201);
  const company = await response.json();
  const nodes = [];
  for (let i = 0; i < computers; i++)
    nodes.push(
      await addComputer(request, owner, `${name}-${i}`, company.id, false),
    );
  return { ...company, nodes };
}
async function invitation(
  request: APIRequestContext,
  owner: Account,
  recipient: Account,
  companyId: string,
  role = "user",
) {
  const response = await request.post(`${API}/invitations`, {
    headers: headers(owner),
    data: { company_id: companyId, identifier: recipient.email, role },
  });
  expect(response.status()).toBe(201);
  return response.json();
}
async function member(
  request: APIRequestContext,
  owner: Account,
  recipient: Account,
  companyId: string,
  role = "user",
) {
  const response = await request.post(`${API}/memberships`, {
    headers: headers(owner),
    data: { company_id: companyId, user_id: recipient.id, role },
  });
  expect(response.status()).toBe(201);
  return response.json();
}

test("one directory prioritizes pending, authored and member organizations and sorts each group by all computers", async ({
  page,
  request,
  account,
}) => {
  const owner = await makeAccount(request, "Владелец");
  const pendingAlpha = await space(request, owner, "Приглашение Alpha", 2);
  await invitation(request, owner, account, pendingAlpha.id);
  const ownZero = await space(request, account, "Своя без компьютеров");
  const memberSix = await space(request, owner, "Участник шесть", 6);
  await member(request, owner, account, memberSix.id);
  const pendingZulu = await space(request, owner, "Приглашение Zulu", 2);
  await invitation(request, owner, account, pendingZulu.id);
  const ownFour = await space(request, account, "Своя четыре", 4);
  expect(
    (
      await request.patch(`${API}/computers/${ownFour.nodes[0].id}`, {
        headers: headers(account),
        data: { enabled: false },
      })
    ).status(),
  ).toBe(200);
  const invitedCreator = await space(
    request,
    owner,
    "Приглашённый создатель",
    2,
  );
  await member(request, owner, account, invitedCreator.id, "creator");
  const pendingZero = await space(
    request,
    owner,
    "Приглашение без компьютеров",
  );
  await invitation(request, owner, account, pendingZero.id);
  const ownOne = await space(request, account, "Своя одна", 1);
  const memberZero = await space(
    request,
    owner,
    "Администратор без компьютеров",
  );
  await member(request, owner, account, memberZero.id, "admin");
  await addComputer(request, account, "Личный компьютер", undefined, false);
  await login(page, account);
  await page.goto("organizations");
  await expect(page.locator(".organization-directory")).toHaveCount(1);
  await expect(page.locator(".organization-list")).toHaveCount(1);
  await expect(page.locator(".organization-row h3")).toHaveText([
    pendingAlpha.name,
    pendingZulu.name,
    pendingZero.name,
    ownFour.name,
    ownOne.name,
    ownZero.name,
    memberSix.name,
    invitedCreator.name,
    memberZero.name,
  ]);
  await expect(
    page.locator('[data-testid="organization-computer-count"]'),
  ).toHaveText(["2", "2", "0", "4", "1", "0", "6", "2", "0"]);
  await expect(page.locator(".organization-row")).toHaveCount(9);
  await expect(
    page.getByRole("heading", { name: "Созданные вами", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("heading", { name: "Ваши организации", exact: true }),
  ).toHaveCount(0);
  await expect(page.locator(".create-organization")).toHaveCount(0);
  await expect(page.getByLabel("Название организации")).toHaveCount(0);
});

test("temporary search matches names locally, preserves ordering and clears without API requests", async ({
  page,
  request,
  account,
}) => {
  const owner = await makeAccount(request);
  const mine = await space(request, account, "LAB Research", 1);
  const pending = await space(request, owner, "Lab Collective");
  await invitation(request, owner, account, pending.id);
  await space(request, account, "Квантовая лаборатория");
  await login(page, account);
  await page.goto("organizations");
  await expect(page.locator(".organization-row")).toHaveCount(3);
  const calls: string[] = [];
  page.on("request", (req) => {
    if (/\/v1\/(companies|invitations)(?:\?|$)/.test(req.url()))
      calls.push(req.url());
  });
  const search = page.getByRole("searchbox", { name: "Найти организацию" });
  await search.fill(" lAb ");
  await expect(page.locator(".organization-row h3")).toHaveText([
    pending.name,
    mine.name,
  ]);
  await search.fill("ЛАБОРАТ");
  await expect(page.locator(".organization-row h3")).toHaveText([
    "Квантовая лаборатория",
  ]);
  await search.fill(".*");
  await expect(
    page.getByRole("heading", { name: "Организации не найдены", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".organization-row")).toHaveCount(0);
  await page.getByRole("button", { name: "Очистить поиск" }).click();
  await expect(search).toHaveValue("");
  await expect(page.locator(".organization-row")).toHaveCount(3);
  expect(calls).toEqual([]);
});

test("create button opens an accessible dialog; whitespace, cancellation, Escape and focus restoration work", async ({
  page,
  request,
  account,
}) => {
  await login(page, account);
  await page.goto("organizations");
  const trigger = page.getByRole("button", {
    name: "Создать организацию",
    exact: true,
  });
  await expect(trigger).toHaveCSS("font-size", "12px");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.getByLabel("Название организации")).toHaveCount(0);
  await trigger.click();
  let dialog = page.getByRole("dialog", {
    name: "Создать организацию",
    exact: true,
  });
  await expect(dialog).toBeVisible();
  const input = dialog.getByLabel("Название организации");
  await expect(input).toBeFocused();
  await expect(input).toHaveAttribute("maxlength", "200");
  const submit = dialog.getByRole("button", {
    name: "Создать организацию",
    exact: true,
  });
  await expect(submit).toBeDisabled();
  await input.fill("   ");
  await expect(submit).toBeDisabled();
  await input.fill("Черновик");
  await expect(submit).toBeEnabled();
  await dialog.getByRole("button", { name: "Отмена", exact: true }).click();
  await expect(dialog).not.toBeVisible();
  await expect(trigger).toBeFocused();
  await page.keyboard.press("Enter");
  dialog = page.getByRole("dialog", {
    name: "Создать организацию",
    exact: true,
  });
  await expect(dialog.getByLabel("Название организации")).toHaveValue("");
  await page.keyboard.press("Escape");
  await expect(dialog).not.toBeVisible();
  await expect(trigger).toBeFocused();
  await expect(
    page.getByRole("heading", { name: "Ваша команда начинается здесь" }),
  ).toBeVisible();
  expect(
    await (
      await request.get(`${API}/companies`, { headers: headers(account) })
    ).json(),
  ).toEqual([]);
});

test("creation and directory controls switch to English and Enter submits the dialog", async ({
  page,
  account,
}) => {
  await login(page, account);
  await page.goto("organizations");
  await page.getByRole("button", { name: "Выбрать язык" }).click();
  await expect(
    page.getByRole("searchbox", { name: "Find an organization" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "All organizations", exact: false }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Create organization", exact: true })
    .click();
  const dialog = page.getByRole("dialog", {
    name: "Create organization",
    exact: true,
  });
  await dialog.getByLabel("Organization name").fill("English Team");
  await page.keyboard.press("Enter");
  await expect(dialog).not.toBeVisible();
  await expect(
    page.getByRole("heading", { name: "English Team", exact: true }),
  ).toBeVisible();
});

test("creation inserts once and resorts immediately while the background list response is delayed", async ({
  page,
  request,
  account,
}) => {
  const owner = await makeAccount(request);
  const mine = await space(request, account, "Существующая", 1);
  const joined = await space(request, owner, "Чужая", 2);
  await member(request, owner, account, joined.id);
  const pending = await space(request, owner, "Приглашение");
  await invitation(request, owner, account, pending.id);
  await login(page, account);
  await page.goto("organizations");
  await expect(page.locator(".organization-row")).toHaveCount(3);
  let navigations = 0;
  page.on("framenavigated", (frame) => {
    if (frame === page.mainFrame()) navigations++;
  });
  await page.getByRole("searchbox").fill(mine.name);
  await page
    .getByRole("button", { name: "Создать организацию", exact: true })
    .click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Название организации").fill("  Новая <Team>  ");
  let releasePost!: () => void, releaseRefresh!: () => void;
  const postGate = new Promise<void>((resolve) => {
    releasePost = resolve;
  });
  const refreshGate = new Promise<void>((resolve) => {
    releaseRefresh = resolve;
  });
  let posts = 0;
  await page.route("**/v1/companies", async (route) => {
    posts++;
    await postGate;
    await route.continue();
  });
  await page.route("**/v1/companies?*", async (route) => {
    await refreshGate;
    await route.continue();
  });
  try {
    await dialog
      .getByRole("button", { name: "Создать организацию", exact: true })
      .click();
    await expect.poll(() => posts).toBe(1);
    await expect(dialog.getByLabel("Название организации")).toBeDisabled();
    await expect(
      dialog.getByRole("button", { name: "Отмена", exact: true }),
    ).toBeDisabled();
    await page.keyboard.press("Escape");
    await expect(dialog).toBeVisible();
    await dialog.locator("form").evaluate((form) => {
      form.dispatchEvent(
        new Event("submit", { bubbles: true, cancelable: true }),
      );
      form.dispatchEvent(
        new Event("submit", { bubbles: true, cancelable: true }),
      );
    });
    releasePost();
    await expect(dialog).not.toBeVisible();
    await expect(page.getByRole("searchbox")).toHaveValue("");
    await expect(page.locator(".organization-row h3")).toHaveText([
      pending.name,
      mine.name,
      "Новая <Team>",
      joined.name,
    ]);
    await expect(page.locator(".organization-directory")).toHaveAttribute(
      "aria-busy",
      "true",
    );
    await expect(page.getByRole("status")).toContainText("Организация создана");
    expect(posts).toBe(1);
    expect(navigations).toBe(0);
  } finally {
    releasePost();
    releaseRefresh();
  }
  await expect(page.locator(".organization-directory")).toHaveAttribute(
    "aria-busy",
    "false",
  );
  await expect(page.locator(".organization-row h3")).toHaveText([
    pending.name,
    mine.name,
    "Новая <Team>",
    joined.name,
  ]);
  const saved = await (
    await request.get(`${API}/companies`, { headers: headers(account) })
  ).json();
  expect(
    saved.filter((row: { name: string }) => row.name === "Новая <Team>"),
  ).toHaveLength(1);
});

test("acceptance moves an invited creator into the membership group and updates counts without navigation", async ({
  page,
  request,
  account,
}) => {
  const owner = await makeAccount(request);
  const mine = await space(request, account, "Созданная мной");
  const joined = await space(request, owner, "Участник", 1);
  await member(request, owner, account, joined.id);
  const invited = await space(request, owner, "Приглашённый создатель", 3);
  await invitation(request, owner, account, invited.id, "creator");
  await login(page, account);
  await page.goto("organizations");
  await expect(page.locator(".organization-row h3")).toHaveText([
    invited.name,
    mine.name,
    joined.name,
  ]);
  await page
    .getByRole("article", { name: invited.name, exact: true })
    .getByRole("button", { name: "Принять приглашение" })
    .click();
  await expect(page.locator(".organization-row h3")).toHaveText([
    mine.name,
    invited.name,
    joined.name,
  ]);
  const row = page.getByRole("article", { name: invited.name, exact: true });
  await expect(row).toHaveAttribute("data-group", "2");
  await expect(row.locator(".organization-role")).toHaveText("Создатель");
  await expect(row.locator(".organization-members-metric")).toHaveAttribute(
    "aria-label",
    "Участники: 2",
  );
  await expect(page.locator(".invitation-row")).toHaveCount(0);
  await expect(page.getByRole("status")).toHaveText(
    "Вы присоединились к организации.",
  );
  await expect(page).toHaveURL(/\/organizations$/);
});

test("declining a pending invitation removes only that row reactively", async ({
  page,
  request,
  account,
}) => {
  const owner = await makeAccount(request);
  const mine = await space(request, account, "Моя организация");
  const pending = await space(request, owner, "Не принятая");
  await invitation(request, owner, account, pending.id);
  await login(page, account);
  await page.goto("organizations");
  await page
    .getByRole("article", { name: pending.name, exact: true })
    .getByRole("button", { name: "Отклонить", exact: true })
    .click();
  await expect(page.locator(".organization-row h3")).toHaveText([mine.name]);
  await expect(page.getByRole("status")).toHaveText("Приглашение отклонено.");
});

test("failed directory load shows a retry without fabricating an empty list", async ({
  page,
  request,
  account,
}) => {
  await space(request, account, "Команда после повтора");
  await login(page, account);
  await page.route("**/v1/invitations?*", (route) => route.abort("failed"));
  await page.goto("organizations");
  await expect(page.getByRole("alert")).toContainText(
    "Не удалось связаться с сервером",
  );
  await expect(page.locator(".directory-count")).toHaveText("—");
  await expect(
    page.getByRole("heading", { name: "Ваша команда начинается здесь" }),
  ).toHaveCount(0);
  await page.unroute("**/v1/invitations?*");
  await page.getByRole("button", { name: "Попробовать снова" }).click();
  await expect(page.locator(".organization-row h3")).toHaveText([
    "Команда после повтора",
  ]);
  await expect(page.getByRole("alert")).toHaveCount(0);
});
