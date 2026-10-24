import { test, expect, login } from "./fixtures";
import { actionsPeer, binding, repositoryURL } from "./actions-peer";
import type { Locator } from "@playwright/test";

/** A controlled search must keep every typed character and put that same query into the repository return link. */
test("rapid typing preserves the repository search and its return navigation", async ({ page, account }) => {
  await actionsPeer(page);
  await login(page, account); await page.goto("/actions");
  const filters = page.locator(".repository-filter-panel");
  if (!await filters.evaluate(element => (element as HTMLDetailsElement).open)) await filters.locator("summary").click();
  const search = page.getByRole("searchbox", { name: "Поиск репозитория", exact: true });
  await search.pressSequentially("sandbox");
  await expect(search).toHaveValue("sandbox");
  await expect(page).toHaveURL(/search=sandbox/);
  await expect(page.locator(".repository-card")).toHaveCount(1);
  await page.getByRole("link", { name: "Подключить", exact: true }).click();
  await page.getByRole("link", { name: "← К репозиториям", exact: true }).click();
  await expect(search).toHaveValue("sandbox");
  await page.getByLabel("Сортировка", { exact: true }).selectOption("name_desc");
  await expect(page).toHaveURL(/sort=name_desc/);
  await search.press("ControlOrMeta+A"); await search.press("Backspace");
  await expect(page).not.toHaveURL(/search=/);
  await search.pressSequentially("ci-sandbox");
  await expect(search).toHaveValue("ci-sandbox");
  await expect(page).toHaveURL(/search=ci-sandbox/);
  await page.goBack();
  await expect(search).toHaveValue("sandbox");
  await page.goForward();
  await expect(search).toHaveValue("ci-sandbox");
});

/** Editing a mobile search must not scroll its focused field off screen on each URL update. */
test("typing in mobile repository search keeps the field on screen", async ({ page, account }) => {
  await page.setViewportSize({ width: 390, height: 600 });
  await actionsPeer(page);
  await login(page, account); await page.goto("/actions");
  await page.locator(".repository-filter-panel > summary").click();
  const search = page.getByRole("searchbox", { name: "Поиск репозитория", exact: true });
  await search.evaluate(element => element.scrollIntoView({ block: "center" }));
  await search.fill("sandbox");
  await expect(page).toHaveURL(/search=sandbox/);
  await expect(search).toBeFocused();
  await expect(search).toBeInViewport({ ratio: 1 });
});

/** A second filter must merge the latest URL, rather than resurrecting a just-cleared search from an older render. */
test("clearing search and immediately changing filters keeps all choices", async ({ page, account }) => {
  await actionsPeer(page);
  await login(page, account); await page.goto("/actions?search=missing");
  const search = page.getByRole("searchbox", { name: "Поиск репозитория", exact: true });
  for (let repeat = 0; repeat < 4; repeat++) {
    await search.fill("missing");
    await expect(page.locator(".repository-card")).toHaveCount(0);
    await search.fill("");
    await page.getByLabel("Тип владельца", { exact: true }).selectOption("organization");
    await page.getByLabel("Подключение CI", { exact: true }).selectOption("all");
    await page.getByLabel("Сортировка", { exact: true }).selectOption(repeat % 2 ? "name_desc" : "name");
    await expect(page.locator(".repository-card")).toHaveCount(1);
    await expect(search).toHaveValue("");
    const query = new URL(page.url()).searchParams;
    expect(query.has("search")).toBe(false);
    expect(query.get("owner_type")).toBe("organization");
    expect(query.get("state")).toBe("all");
    expect(query.get("sort")).toBe(repeat % 2 ? "name_desc" : "name");
  }
});

/** Reloading a filtered catalog must not visually replace its chosen owner with "all owners", including after an error. */
test("selected repository owner remains visible while search reloads and fails", async ({ page, account }) => {
  const peer = await actionsPeer(page);
  await login(page, account); await page.goto("/actions?owner=mutating");
  const owner = page.getByLabel("Аккаунт или организация GitHub", { exact: true });
  await expect(owner).toHaveValue("mutating");
  let release!: () => void;
  const gate = new Promise<void>(resolve => release = resolve);
  peer.custom = async (route, url) => {
    if (!url.pathname.endsWith("/github/repositories") || !url.searchParams.has("search")) return false;
    await gate; await route.fulfill({ status: 503, json: {} }); return true;
  };
  try {
    await page.getByRole("searchbox").fill("sandbox");
    await expect(page).toHaveURL(/search=sandbox/);
    await expect(owner).toHaveValue("mutating");
    release(); await expect(page.getByRole("alert")).toBeVisible();
    await expect(owner).toHaveValue("mutating");
    peer.custom = null;
    await page.getByRole("button", { name: /Попробовать снова/ }).click();
    await expect(page.locator(".repository-card")).toHaveCount(1);
    await expect(owner).toHaveValue("mutating");
  } finally { release(); }
});

/** A failed inventory request must explain a disabled save without requiring the user to discover a collapsed section. */
test("inventory failures can be understood and retried with advanced settings closed", async ({ page, account }) => {
  const peer = await actionsPeer(page);
  peer.failures.set("GET computers", [503]);
  await login(page, account); await page.goto(repositoryURL);
  await expect(page.getByRole("button", { name: "Подключить", exact: true })).toBeDisabled();
  await expect(page.getByRole("alert")).toBeVisible();
  await page.getByLabel("CPU", { exact: true }).fill("0.75");
  await page.getByRole("button", { name: /Попробовать снова/ }).click();
  await expect(page.getByRole("button", { name: "Подключить", exact: true })).toBeEnabled();
  await expect(page.getByLabel("CPU", { exact: true })).toHaveValue("0.75");
  await page.getByRole("button", { name: "Подключить", exact: true }).click();
  await expect(page.getByLabel("Workflow YAML", { exact: true })).toBeVisible();
  expect(peer.bindings[0].cpu_millis).toBe(750);
});

/** Missing saved computers must explain the blocked save even while their optional selector is collapsed. */
test("unavailable saved computers are explained before opening advanced settings", async ({ page, account }) => {
  const peer = await actionsPeer(page);
  peer.bindings = [{ ...binding, computer_ids: ["removed-cell"] }];
  await login(page, account); await page.goto(repositoryURL);
  await expect(page.getByRole("button", { name: "Сохранить настройки", exact: true })).toBeDisabled();
  await expect(page.getByText(/Некоторые выбранные компьютеры больше не доступны/)).toBeVisible();
  await page.locator(".binding-advanced > summary").click();
  await page.getByRole("button", { name: "Использовать любой компьютер", exact: true }).click();
  await page.getByRole("button", { name: "Сохранить настройки", exact: true }).click();
  await expect(page.getByText("Настройки сохранены.", { exact: true })).toBeVisible();
  expect(peer.bindings[0].computer_ids).toEqual([]);
});

for (const field of ["wait", "duration"]) {
  /** Native validation must reveal and focus a collapsed invalid field below the mobile sticky header. */
  test(`saving reveals an invalid collapsed ${field} field`, async ({ page, account }) => {
    await page.setViewportSize({ width: 320, height: 568 });
    const peer = await actionsPeer(page);
    await login(page, account); await page.goto(repositoryURL);
    const advanced = page.locator(".binding-advanced");
    await advanced.locator("summary").click();
    const input = page.locator(`input[name="${field}"]`);
    await input.fill("0");
    await advanced.locator("summary").click();
    await page.getByRole("button", { name: "Подключить", exact: true }).click();
    await expect(input).toBeVisible();
    await expect(input).toBeFocused();
    const header = (await page.locator(".site-header").boundingBox())!;
    await expect.poll(async () => (await input.locator("..").boundingBox())!.y).toBeGreaterThanOrEqual(header.y + header.height + 8);
    await expect(input).toBeInViewport({ ratio: 1 });
    expect(peer.calls.filter(row => row.method === "POST" && row.path === "github/routes")).toHaveLength(0);
    await input.fill(field === "wait" ? "90" : "10");
    if (field === "wait") {
      const duration = page.locator('input[name="duration"]');
      await duration.fill("0");
      await page.getByRole("button", { name: "Подключить", exact: true }).click();
      await expect(duration).toBeFocused();
      await expect.poll(async () => (await duration.locator("..").boundingBox())!.y).toBeGreaterThanOrEqual(header.y + header.height + 8);
      await duration.fill("10");
    }
    await page.getByRole("button", { name: "Подключить", exact: true }).click();
    await expect(page.getByLabel("Workflow YAML", { exact: true })).toBeVisible();
  });
}

for (const input of ["mouse", "keyboard", "touch"] as const) {
  /** The full repository-to-history journey must work through native activation, including profile changes and reversible toggles. */
  test(`repository filters, profiles, YAML and history work with ${input}`, async ({ page, account, isMobile }) => {
    test.skip(input === "touch" && !isMobile, "Touch is exercised on phone projects.");
    const activate = async (control: Locator) => {
      if (input === "keyboard") await control.press("Enter");
      else if (input === "touch") await control.tap();
      else await control.click();
    };
    const type = async (control: Locator, value: string) => {
      if (input === "keyboard") { await control.press("ControlOrMeta+A"); await control.pressSequentially(value); }
      else await control.fill(value);
    };
    const check = async (control: Locator, checked: boolean) => {
      if (await control.isChecked() === checked) return;
      if (input === "keyboard") await control.press("Space");
      else if (input === "touch") await control.tap();
      else await control.click();
    };
    const peer = await actionsPeer(page);
    peer.companies = [{ id: "lab", name: "CI Lab", my_permissions: ["execution.read", "execution.run", "resources.read", "resources.manage"] }];
    peer.computers = [{ id: "lab-cell", name: "Lab Cell", company_id: "lab", runtime: "powercell-ubuntu-arm64-v1" }];
    peer.repositories.push({ ...peer.repositories[0], id: 790, full_name: "personal/docs", owner_login: "personal", owner_type: "personal" });
    await page.addInitScript(() => Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: async () => {} } }));
    await login(page, account);
    await activate(page.getByRole("link", { name: "Actions", exact: true }));
    const filters = page.locator(".repository-filter-panel");
    if (!await filters.evaluate(element => (element as HTMLDetailsElement).open)) await activate(filters.locator("summary"));
    await page.getByLabel("Тип владельца", { exact: true }).selectOption("organization");
    await type(page.locator('.repository-search input'), "sandbox");
    await expect(page.locator('.repository-search input')).toHaveValue("sandbox");
    await expect(page).toHaveURL(/search=sandbox/);
    await expect(page.locator(".repository-card")).toHaveCount(1);
    const catalogURL = page.url();
    await activate(page.getByRole("link", { name: "Подключить", exact: true }));
    await page.getByLabel("Пул компьютеров", { exact: true }).selectOption("lab");
    await type(page.getByLabel("CPU", { exact: true }), "1");
    const deploy = ".github/workflows/deploy.yml";
    await page.getByLabel("Workflow для переопределения", { exact: true }).selectOption(deploy);
    await activate(page.getByRole("button", { name: "Добавить переопределение", exact: true }));
    const profile = page.getByRole("group", { name: deploy, exact: true });
    await type(profile.getByLabel(`CPU · ${deploy}`, { exact: true }), "0.5");
    await check(profile.getByLabel("Задать компьютеры для этого workflow", { exact: true }), true);
    await profile.getByLabel(`Конкретные компьютеры · ${deploy}`, { exact: true }).selectOption("lab-cell");
    await activate(page.getByRole("button", { name: "Подключить", exact: true }));
    await expect(page.getByLabel("Workflow YAML", { exact: true })).toBeVisible();
    expect(peer.bindings[0]).toMatchObject({ company_id: "lab", cpu_millis: 1000, workflow_paths: [], workflow_profiles: { [deploy]: { cpu_millis: 500, computer_ids: ["lab-cell"] } } });
    await page.getByLabel("Workflow для настройки", { exact: true }).selectOption(deploy);
    await expect(page.getByText(/Применяемый профиль:.*deploy.yml.*0.5 CPU/)).toBeVisible();
    await activate(page.getByRole("button", { name: "Скопировать YAML", exact: true }));
    await expect(page.locator(".repository-yaml").getByRole("status")).toContainText("скопирован");
    await activate(page.getByRole("button", { name: "Отключить подключение", exact: true }));
    await expect(page.getByLabel("Workflow YAML", { exact: true })).toHaveCount(0);
    await activate(page.getByRole("button", { name: "Включить подключение", exact: true }));
    await expect(page.getByLabel("Workflow YAML", { exact: true })).toBeVisible();
    await activate(page.getByRole("group", { name: deploy, exact: true }).getByRole("button", { name: "Использовать профиль по умолчанию", exact: true }));
    await activate(page.getByRole("button", { name: "Сохранить настройки", exact: true }));
    await expect.poll(() => peer.bindings[0].workflow_profiles).toEqual({});
    await activate(page.getByRole("link", { name: "История изменений", exact: true }));
    await expect(page.locator(".binding-revision")).toHaveCount(4);
    await activate(page.getByRole("link", { name: "← Настройки подключения", exact: true }));
    await expect(page.getByLabel("Пул компьютеров", { exact: true })).toHaveValue("lab");
    await activate(page.getByRole("link", { name: "← К репозиториям", exact: true }));
    await expect(page).toHaveURL(catalogURL);
    await expect(page.locator(".repository-card")).toHaveCount(1);
    expect(peer.bindings).toHaveLength(1);
  });
}
