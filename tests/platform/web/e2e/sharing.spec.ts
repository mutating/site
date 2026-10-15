import {
  API,
  expect,
  headers,
  login,
  makeAccount,
  password,
  test,
} from "./fixtures";
import type { Page } from "@playwright/test";

async function createCompany(page: Page, name = "Общая лаборатория") {
  await page.goto("organizations");
  const nameField = page.getByLabel("Название организации");
  // Settle focus in mobile WebKit before entering the organization name.
  await nameField.click();
  await nameField.fill(name);
  await expect(nameField).toHaveValue(name);
  await page
    .getByRole("button", { name: "Создать организацию", exact: true })
    .click();
  const row = page
    .locator(".organization-row")
    .filter({ has: page.getByRole("heading", { name }) });
  await row.getByRole("link", { name: "Участники" }).click();
  await expect(
    page.getByRole("heading", { level: 1, name: `${name}.` }),
  ).toBeVisible();
  return new URL(page.url()).pathname.split("/").at(-1)!;
}
async function openSharing(page: Page, mode: "add" | "invite" | "link") {
  const form = page.getByRole("region", { name: "Добавить участника" });
  if (
    !(await form
      .locator("details")
      .evaluate((node: HTMLDetailsElement) => node.open))
  )
    await form.locator("summary").click();
  await form.getByLabel("Способ добавления").selectOption(mode);
  return form;
}
async function signOut(page: Page) {
  await page.goto("profile");
  await page
    .getByRole("main")
    .getByRole("button", { name: "Выйти", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "С возвращением." }),
  ).toBeVisible();
}
const roster = (page: Page) =>
  page.getByRole("region", { name: "Участники", exact: true });
const person = (page: Page, name: string) =>
  roster(page).getByRole("article", { name, exact: true });

test("find by nickname, add a member, show profiles, edit permissions, and remove safely", async ({
  page,
  request,
  account,
}) => {
  const bob = await makeAccount(request, "Боб");
  await login(page, account);
  const companyId = await createCompany(page);
  const form = await openSharing(page, "add");
  await form.getByLabel("Email или ник").fill("does_not_exist");
  await form.getByRole("button", { name: "Найти пользователя" }).click();
  await expect(form.getByRole("alert")).toContainText("Пользователь не найден");
  await form.getByLabel("Email или ник").fill(bob.nickname.toUpperCase());
  await form.getByRole("button", { name: "Найти пользователя" }).click();
  await expect(form.getByRole("status")).toContainText("Боб");
  await form
    .getByRole("button", { name: "Добавить участника", exact: true })
    .click();
  await expect(person(page, "Боб")).toContainText("Участник");
  await expect(person(page, "Боб")).toContainText(`@${bob.nickname}`);
  await expect(person(page, "Боб")).toContainText("Выполнение задач");
  await person(page, "Боб")
    .getByRole("button", { name: "Изменить права" })
    .click();
  let dialog = page.getByRole("dialog");
  await dialog.getByLabel("Настроить права отдельно").check();
  await dialog.getByLabel("Выполнение задач и работа с артефактами").uncheck();
  await dialog.getByRole("button", { name: "Сохранить изменения" }).click();
  await expect(dialog).not.toBeVisible();
  await expect(person(page, "Боб")).not.toContainText("Выполнение задач");
  const members = await (
    await request.get(`${API}/memberships?company_id=${companyId}`, {
      headers: headers(account),
    })
  ).json();
  const membership = members.find(
    (member: { user_id: string }) => member.user_id === bob.id,
  );
  expect(membership.effective_permissions).not.toContain("execution.run");
  await person(page, "Боб")
    .getByRole("button", { name: "Удалить участника" })
    .click();
  dialog = page.getByRole("dialog");
  await expect(
    dialog.getByRole("button", { name: "Подтвердить", exact: true }),
  ).toBeDisabled();
  await dialog.getByRole("textbox").fill("Боб");
  await dialog
    .getByRole("button", { name: "Подтвердить", exact: true })
    .click();
  await expect(person(page, "Боб")).toHaveCount(0);
  expect(
    (
      await request.get(`${API}/companies/${companyId}`, {
        headers: headers(bob),
      })
    ).status(),
  ).toBe(404);
});

test("addressed invitation appears in recipient inbox and can be accepted with a creator role", async ({
  page,
  request,
  account,
}) => {
  const bob = await makeAccount(request, "Боб");
  await login(page, account);
  const companyId = await createCompany(page);
  const form = await openSharing(page, "invite");
  await form.getByLabel("Email или ник").fill(bob.email);
  await form.getByLabel("Роль", { exact: true }).selectOption("creator");
  await form.getByRole("button", { name: "Пригласить", exact: true }).click();
  await expect(form.getByRole("status")).toHaveText("Приглашение создано.");
  const link = await form.getByLabel("Ссылка приглашения").inputValue();
  await page.goto(link);
  await expect(page.getByRole("alert")).toContainText("другой учётной записи");
  await signOut(page);
  await login(page, bob);
  await page.goto("organizations");
  const inbox = page.getByRole("region", { name: "Вас пригласили" });
  await expect(
    inbox.getByRole("heading", { name: "Общая лаборатория" }),
  ).toBeVisible();
  await inbox.getByRole("button", { name: "Принять приглашение" }).click();
  await expect(inbox.getByRole("status")).toHaveText(
    "Вы присоединились к организации.",
  );
  await page.goto(`organizations/${companyId}`);
  await person(page, "Алиса")
    .getByRole("button", { name: "Изменить права" })
    .click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Роль", { exact: true }).selectOption("admin");
  await dialog.getByRole("button", { name: "Сохранить изменения" }).click();
  await expect(person(page, "Алиса")).toContainText("Администратор");
  await person(page, "Боб")
    .getByRole("button", { name: "Изменить права" })
    .click();
  await dialog.getByLabel("Роль", { exact: true }).selectOption("user");
  await dialog.getByRole("button", { name: "Сохранить изменения" }).click();
  await expect(dialog.getByRole("alert")).toContainText(
    "хотя бы одного создателя",
  );
  await dialog.getByRole("button", { name: "Отмена", exact: true }).click();
  expect(
    (
      await (
        await request.get(`${API}/companies/${companyId}`, {
          headers: headers(account),
        })
      ).json()
    ).my_role,
  ).toBe("admin");
});

test("share link survives login-to-registration navigation and cannot be reused", async ({
  page,
  account,
}) => {
  await login(page, account);
  await createCompany(page);
  const form = await openSharing(page, "link");
  await form
    .getByRole("button", { name: "Создать ссылку", exact: true })
    .click();
  const link = await form.getByLabel("Ссылка приглашения").inputValue();
  await form.getByRole("button", { name: "Скопировать ссылку" }).click();
  await expect(form.getByRole("status").last()).toContainText(
    /скопирована|выделена/,
  );
  await signOut(page);
  await page.goto(link);
  await expect(
    page.getByRole("heading", { name: "С возвращением." }),
  ).toBeVisible();
  await page.getByRole("link", { name: "Создать аккаунт" }).click();
  await page.getByLabel(/^Имя/).fill("Новая участница");
  await page
    .getByLabel("Электронная почта", { exact: true })
    .fill(
      `new_${Date.now()}_${Math.random().toString(36).slice(2)}@example.com`,
    );
  await page.getByLabel("Пароль", { exact: true }).fill(password);
  await page
    .getByRole("button", { name: "Создать аккаунт", exact: true })
    .click();
  await expect(page).toHaveURL(link);
  await page.getByRole("button", { name: "Принять приглашение" }).click();
  await expect(page.getByRole("status")).toHaveText(
    "Вы присоединились к организации.",
  );
  await page.getByRole("link", { name: "Участники", exact: true }).click();
  await expect(person(page, "Новая участница")).toContainText("Участник");
  await expect(
    page.getByRole("region", { name: "Добавить участника" }),
  ).toHaveCount(0);
  await page.goto(link);
  await expect(page.getByRole("alert")).toContainText("использовано, отозвано");
});

test("invitation link renewal disables the old link, revocation and decline are visible", async ({
  page,
  request,
  account,
}) => {
  const bob = await makeAccount(request, "Боб");
  await login(page, account);
  const companyId = await createCompany(page);
  const form = await openSharing(page, "invite");
  await form.getByLabel("Email или ник").fill(bob.email);
  await form.getByRole("button", { name: "Пригласить", exact: true }).click();
  const oldLink = await form.getByLabel("Ссылка приглашения").inputValue();
  const outgoing = page.getByRole("region", {
    name: "Приглашения",
    exact: true,
  });
  await outgoing.getByRole("button", { name: "Обновить ссылку" }).click();
  const newLink = await outgoing.getByLabel("Ссылка приглашения").inputValue();
  await expect(page.getByLabel("Ссылка приглашения")).toHaveCount(1);
  expect(newLink).not.toBe(oldLink);
  expect(
    (
      await request.get(
        `${API}/invitations/link/${oldLink.split("/").at(-1)}`,
        { headers: headers(bob) },
      )
    ).status(),
  ).toBe(404);
  await outgoing.getByRole("button", { name: "Отозвать", exact: true }).click();
  await expect(outgoing).toContainText("Отозвано");
  expect(
    (
      await request.get(
        `${API}/invitations/link/${newLink.split("/").at(-1)}`,
        { headers: headers(bob) },
      )
    ).status(),
  ).toBe(410);
  const again = await openSharing(page, "invite");
  await again.getByLabel("Email или ник").fill(bob.email);
  await again.getByRole("button", { name: "Пригласить", exact: true }).click();
  await signOut(page);
  await login(page, bob);
  await page.goto("organizations");
  const inbox = page.getByRole("region", { name: "Вас пригласили" });
  await inbox.getByRole("button", { name: "Отклонить", exact: true }).click();
  await expect(inbox.getByRole("status")).toHaveText("Приглашение отклонено.");
  expect(
    (
      await request.get(`${API}/companies/${companyId}`, {
        headers: headers(bob),
      })
    ).status(),
  ).toBe(404);
});

test("restricted manager sees current rights, cannot grant more, and sees capacity access limitation", async ({
  page,
  request,
  account,
}) => {
  const bob = await makeAccount(request, "Боб");
  const company = await (
    await request.post(`${API}/companies`, {
      headers: headers(account),
      data: { name: "Команда с ограничениями" },
    })
  ).json();
  expect(
    (
      await request.post(`${API}/memberships`, {
        headers: headers(account),
        data: {
          company_id: company.id,
          user_id: bob.id,
          permissions: ["members.read", "members.manage"],
        },
      })
    ).status(),
  ).toBe(201);
  await login(page, bob);
  await expect(page.getByRole("region", { name: company.name })).toContainText(
    "нет права просматривать мощности",
  );
  await page.goto(`organizations/${company.id}`);
  const form = await openSharing(page, "link");
  await expect(
    form.getByLabel("Роль", { exact: true }).locator("option"),
  ).toHaveCount(1);
  await expect(form.getByLabel("Настроить права отдельно")).toBeChecked();
  await expect(form.getByLabel("Управление API-токенами")).toBeDisabled();
  await expect(
    person(page, "Алиса").getByRole("button", { name: "Изменить права" }),
  ).toHaveCount(0);
  await form.getByRole("button", { name: "Создать ссылку" }).click();
  await expect(form.getByRole("status")).toHaveText("Приглашение создано.");
  await page.getByRole("button", { name: "Выбрать язык" }).click();
  await expect(
    page.getByRole("heading", { name: "Members", exact: true }),
  ).toBeVisible();
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("lang", "en");
});

test("member list paginates and returns to page one when the final page disappears", async ({
  page,
  request,
  account,
}) => {
  const company = await (
    await request.post(`${API}/companies`, {
      headers: headers(account),
      data: { name: "Большая команда" },
    })
  ).json();
  const ids: string[] = [];
  for (let index = 0; index < 10; index++) {
    const member = await makeAccount(request, `Участник ${index}`);
    const response = await request.post(`${API}/memberships`, {
      headers: headers(account),
      data: { company_id: company.id, user_id: member.id },
    });
    expect(response.status()).toBe(201);
    ids.push((await response.json()).id);
  }
  await login(page, account);
  await page.goto(`organizations/${company.id}`);
  await expect(roster(page).locator(".member-row")).toHaveCount(10);
  await roster(page).getByRole("button", { name: "Далее" }).click();
  await expect(roster(page).locator(".member-row")).toHaveCount(1);
  await expect(roster(page).getByRole("navigation")).toContainText(
    "Страница 2 из 2",
  );
  const last = await (
    await request.get(
      `${API}/memberships?company_id=${company.id}&limit=10&offset=10`,
      { headers: headers(account) },
    )
  ).json();
  // Removing any ordinary member reduces the total to ten; the old second page must disappear.
  const removable = last[0].role === "creator" ? ids[0] : last[0].id;
  expect(
    (
      await request.delete(`${API}/memberships/${removable}`, {
        headers: headers(account),
      })
    ).status(),
  ).toBe(204);
  await page.reload();
  await expect(page).toHaveURL(/page=1/);
  await expect(roster(page).locator(".member-row")).toHaveCount(10);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});

test("member loading and edits recover from transport errors without discarding the draft", async ({
  page,
  request,
  account,
}) => {
  const bob = await makeAccount(request, "Боб");
  const company = await (
    await request.post(`${API}/companies`, {
      headers: headers(account),
      data: { name: "Восстановление команды" },
    })
  ).json();
  const member = await (
    await request.post(`${API}/memberships`, {
      headers: headers(account),
      data: { company_id: company.id, user_id: bob.id },
    })
  ).json();
  await login(page, account);
  const listPath = "**/v1/memberships?*";
  await page.route(listPath, (route) => route.abort("failed"));
  await page.goto(`organizations/${company.id}`);
  await expect(roster(page).getByRole("alert")).toContainText(
    "Не удалось связаться",
  );
  await page.unroute(listPath);
  await roster(page).getByRole("button", { name: "Попробовать снова" }).click();
  await expect(person(page, "Боб")).toBeVisible();
  const trigger = person(page, "Боб").getByRole("button", {
    name: "Изменить права",
  });
  await trigger.click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Роль", { exact: true }).selectOption("admin");
  const patch = `**/v1/memberships/${member.id}`;
  await page.route(patch, (route) => route.abort("failed"));
  await dialog.getByRole("button", { name: "Сохранить изменения" }).click();
  await expect(dialog.getByRole("alert")).toContainText("Не удалось связаться");
  await expect(dialog.getByLabel("Роль", { exact: true })).toHaveValue("admin");
  await page.unroute(patch);
  await dialog.getByRole("button", { name: "Сохранить изменения" }).click();
  await expect(dialog).not.toBeVisible();
  await expect(person(page, "Боб")).toContainText("Администратор");
  await trigger.click();
  await page.keyboard.press("Escape");
  await expect(trigger).toBeFocused();
});

test("invitation creation can retry and an email recipient can register later and join", async ({
  page,
  account,
}) => {
  const email = `future_${Date.now()}_${Math.random().toString(36).slice(2)}@example.com`;
  await login(page, account);
  await createCompany(page, "Будущая команда");
  const form = await openSharing(page, "invite");
  await form.getByLabel("Email или ник").fill(email);
  await form.getByLabel("Срок приглашения").selectOption("1");
  const path = "**/v1/invitations";
  await page.route(path, (route) => route.abort("failed"));
  await form.getByRole("button", { name: "Пригласить", exact: true }).click();
  await expect(form.getByRole("alert")).toContainText("Не удалось связаться");
  await expect(form.getByLabel("Email или ник")).toHaveValue(email);
  await expect(form.getByLabel("Срок приглашения")).toHaveValue("1");
  await page.unroute(path);
  await form.getByRole("button", { name: "Пригласить", exact: true }).click();
  await expect(form.getByRole("status")).toHaveText("Приглашение создано.");
  await signOut(page);
  await page.goto("register");
  await page.getByLabel(/^Имя/).fill("Будущий участник");
  await page.getByLabel("Электронная почта", { exact: true }).fill(email);
  await page.getByLabel("Пароль", { exact: true }).fill(password);
  await page
    .getByRole("button", { name: "Создать аккаунт", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Привет, Будущий участник." }),
  ).toBeVisible();
  await page.goto("organizations");
  const inbox = page.getByRole("region", { name: "Вас пригласили" });
  await expect(
    inbox.getByRole("article", { name: "Будущая команда" }),
  ).toBeVisible();
  await inbox.getByRole("button", { name: "Принять приглашение" }).click();
  await expect(inbox.getByRole("status")).toHaveText(
    "Вы присоединились к организации.",
  );
});
