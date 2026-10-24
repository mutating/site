import { test, expect, login } from "./fixtures";
import { actionsPeer, binding, repositoryURL } from "./actions-peer";

const deploy = ".github/workflows/deploy.yml";
const ci = ".github/workflows/ci.yml";

/** The preparation explanation must match admission's budget, including GitHub checks before allocation. */
test("preparation help explains GitHub verification and immediate capacity failure", async ({ page, account }) => {
  await actionsPeer(page);
  await login(page, account); await page.goto(repositoryURL);
  await page.locator(".binding-advanced > summary").click();
  await expect(page.locator(".binding-advanced")).toContainText("проверку доступа GitHub");
  await expect(page.locator(".repository-binding-form")).toContainText("ожидания свободного компьютера нет");
});

/** Sparse profiles must survive saving/reloading and show which resources the workflow will receive. */
test("workflow overrides persist and automatically follow the selected workflow", async ({ page, account }) => {
  const peer = await actionsPeer(page);
  await login(page, account); await page.goto(repositoryURL);
  await expect(page.getByRole("heading", { name: "Профиль по умолчанию", exact: true })).toBeVisible();
  await page.getByLabel("CPU", { exact: true }).fill("1");
  await page.getByLabel("Workflow для переопределения", { exact: true }).selectOption(deploy);
  await page.getByRole("button", { name: "Добавить переопределение", exact: true }).click();
  const profile = page.getByRole("group", { name: deploy, exact: true });
  await expect(profile.getByLabel(`CPU · ${deploy}`, { exact: true })).toHaveValue("");
  await expect(profile.getByLabel(`CPU · ${deploy}`, { exact: true })).toHaveAttribute("placeholder", "1");
  await profile.getByLabel(`CPU · ${deploy}`, { exact: true }).fill("0.5");
  await profile.getByLabel(`RAM · ${deploy}`, { exact: true }).fill("1");
  await profile.getByLabel(`Лимит выполнения CI · минуты · ${deploy}`, { exact: true }).fill("3");
  await page.getByRole("button", { name: "Подключить", exact: true }).click();
  await expect(page.getByLabel("Workflow YAML", { exact: true })).toBeVisible();
  expect(peer.bindings[0].workflow_profiles).toEqual({ [deploy]: { cpu_millis: 500, memory_bytes: 1024 ** 3, timeout_seconds: 180 } });
  await page.getByLabel("Workflow для настройки", { exact: true }).selectOption(deploy);
  await expect(page.getByText(/Применяемый профиль:.*deploy.yml.*0.5 CPU.*1 GiB RAM/)).toBeVisible();
  await page.getByLabel("Workflow для настройки", { exact: true }).selectOption(ci);
  await expect(page.getByText(/Применяемый профиль: Профиль по умолчанию.*1 CPU.*4 GiB RAM/)).toBeVisible();
  await page.reload();
  await expect(page.getByLabel(`CPU · ${deploy}`, { exact: true })).toHaveValue("0.5");
});

/** Clearing an override restores live inheritance, while removing its card restores the entire default profile. */
test("default changes propagate to inherited fields and overrides can be reset", async ({ page, account }) => {
  const peer = await actionsPeer(page); peer.bindings = [{ ...binding, workflow_profiles: { [deploy]: { cpu_millis: 500 } } }];
  await login(page, account); await page.goto(repositoryURL);
  await page.getByLabel("CPU", { exact: true }).fill("3");
  const cpu = page.getByLabel(`CPU · ${deploy}`, { exact: true });
  await expect(cpu).toHaveValue("0.5");
  await cpu.fill(""); await expect(cpu).toHaveAttribute("placeholder", "3");
  await page.getByRole("button", { name: "Сохранить настройки", exact: true }).click();
  await expect(page.getByText("Настройки сохранены.", { exact: true })).toBeVisible();
  expect(peer.bindings[0]).toMatchObject({ cpu_millis: 3000, workflow_profiles: { [deploy]: {} } });
  await page.getByRole("group", { name: deploy, exact: true }).getByRole("button", { name: "Использовать профиль по умолчанию", exact: true }).click();
  await expect(page.getByRole("group", { name: deploy, exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "Сохранить настройки", exact: true }).click();
  await expect.poll(() => peer.bindings[0].workflow_profiles).toEqual({});
});

/** Scope or branch changes must retain an override draft without silently discarding the user's settings. */
test("profiles stay in the draft through branch refresh and workflow scope changes", async ({ page, account }) => {
  const peer = await actionsPeer(page);
  await login(page, account); await page.goto(repositoryURL);
  await page.getByLabel("Workflow для переопределения", { exact: true }).selectOption(deploy);
  await page.getByRole("button", { name: "Добавить переопределение", exact: true }).click();
  await page.getByLabel(`CPU · ${deploy}`, { exact: true }).pressSequentially("0.75");
  await page.getByLabel("Ветка", { exact: true }).selectOption("develop");
  await expect(page.getByLabel(`CPU · ${deploy}`, { exact: true })).toHaveValue("0.75");
  await page.getByRole("button", { name: "Обновить", exact: true }).click();
  await expect(page.getByLabel(`CPU · ${deploy}`, { exact: true })).toHaveValue("0.75");
  await page.getByLabel("Все workflows", { exact: true }).uncheck();
  await page.getByLabel(ci, { exact: true }).check();
  await expect(page.getByText(/Workflow отсутствует в выбранной ветке/)).toBeVisible();
  await page.getByRole("button", { name: "Подключить", exact: true }).click();
  await expect(page.getByLabel("Workflow YAML", { exact: true })).toBeVisible();
  expect(peer.bindings[0]).toMatchObject({ ref: "refs/heads/develop", workflow_paths: [ci], workflow_profiles: { [deploy]: { cpu_millis: 750 } } });
});

/** Per-workflow computer selection distinguishes inherited restrictions from an explicit full-pool selection. */
test("workflow computer overrides can choose a computer or the entire pool", async ({ page, account }) => {
  const peer = await actionsPeer(page);
  peer.computers = [{ id: "cell-a", name: "Alpha", runtime: "powercell-ubuntu-arm64-v1" }];
  peer.bindings = [{ ...binding, computer_ids: ["cell-a"], workflow_profiles: { [deploy]: {} } }];
  await login(page, account); await page.goto(repositoryURL);
  const profile = page.getByRole("group", { name: deploy, exact: true });
  await expect(profile.getByText("Компьютеры наследуются из профиля по умолчанию.")).toBeVisible();
  await profile.getByLabel("Задать компьютеры для этого workflow", { exact: true }).check();
  await profile.getByLabel(`Конкретные компьютеры · ${deploy}`, { exact: true }).selectOption("cell-a");
  await profile.getByRole("button", { name: "Использовать любой компьютер", exact: true }).click();
  await page.getByRole("button", { name: "Сохранить настройки", exact: true }).click();
  await expect(page.getByText("Настройки сохранены.", { exact: true })).toBeVisible();
  expect(peer.bindings[0]).toMatchObject({ computer_ids: ["cell-a"], workflow_profiles: { [deploy]: { computer_ids: [] } } });
  await page.getByRole("group", { name: deploy, exact: true }).getByLabel("Задать компьютеры для этого workflow", { exact: true }).uncheck();
  await page.getByRole("button", { name: "Сохранить настройки", exact: true }).click();
  await expect.poll(() => peer.bindings[0].workflow_profiles).toEqual({ [deploy]: {} });
});

/** Unavailable computers inside profiles must block saves rather than silently broadening the permitted hosts. */
test("unavailable profile computers require an explicit reset", async ({ page, account }) => {
  const peer = await actionsPeer(page); peer.bindings = [{ ...binding, workflow_profiles: { [deploy]: { computer_ids: ["missing"] } } }];
  await login(page, account); await page.goto(repositoryURL);
  await expect(page.getByRole("button", { name: "Сохранить настройки", exact: true })).toBeDisabled();
  const profile = page.getByRole("group", { name: deploy, exact: true });
  await expect(profile.getByRole("status")).toContainText("больше не доступны");
  await profile.getByLabel("Задать компьютеры для этого workflow", { exact: true }).uncheck();
  await expect(page.getByRole("button", { name: "Сохранить настройки", exact: true })).toBeEnabled();
});

/** Profile changes belong in the existing binding's audit trail with readable resource values. */
test("profile history shows previous and new workflow resources", async ({ page, account }) => {
  const peer = await actionsPeer(page); peer.bindings = [{ ...binding, workflow_profiles: { [deploy]: { memory_bytes: 1024 ** 3 } } }];
  await login(page, account); await page.goto(repositoryURL);
  await page.getByLabel(`RAM · ${deploy}`, { exact: true }).fill("2");
  await page.getByRole("button", { name: "Сохранить настройки", exact: true }).click();
  await expect(page.getByText("Настройки сохранены.", { exact: true })).toBeVisible();
  await page.getByRole("link", { name: "История изменений", exact: true }).click();
  const change = page.locator(".binding-changes > div").filter({ has: page.getByText("Профили workflows", { exact: true }) }).first();
  await expect(change).toContainText(`${deploy}: RAM: 1 GiB`);
  await expect(change).toContainText(`${deploy}: RAM: 2 GiB`);
});

/** Values accepted by the API must remain saveable when units are converted to GiB and minutes. */
test("fractional resource units survive editing without a validation trap", async ({ page, account }) => {
  const peer = await actionsPeer(page); peer.bindings = [{ ...binding, disk_bytes: 1.5 * 1024 ** 3, timeout_seconds: 185, workflow_profiles: { [deploy]: { disk_bytes: 2.5 * 1024 ** 3, timeout_seconds: 125 } } }];
  await login(page, account); await page.goto(repositoryURL);
  await page.getByRole("button", { name: "Сохранить настройки", exact: true }).click();
  await expect(page.getByText("Настройки сохранены.", { exact: true })).toBeVisible();
  expect(peer.bindings[0]).toMatchObject({ disk_bytes: 1.5 * 1024 ** 3, timeout_seconds: 185, workflow_profiles: { [deploy]: { disk_bytes: 2.5 * 1024 ** 3, timeout_seconds: 125 } } });
});
