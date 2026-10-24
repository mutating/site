import { test, expect, login, API, headers } from "./fixtures";
import { actionsPeer, binding, repositoryURL } from "./actions-peer";

const deploy = ".github/workflows/deploy.yml";

/** Successful save must reveal and focus YAML below the sticky header, including its two-line narrow layout. */
for (const width of [320, 390]) {
  test(`saved YAML heading clears the sticky header at ${width}px`, async ({ page, account }) => {
    await page.setViewportSize({ width, height: 700 });
    await actionsPeer(page); await login(page, account); await page.goto(repositoryURL);
    await page.getByRole("button", { name: "Подключить", exact: true }).click();
    const heading = page.getByRole("heading", { name: "Настройка YAML в GitHub", exact: true });
    await expect(heading).toBeFocused();
    await expect.poll(async () => {
      const header = (await page.locator(".site-header").boundingBox())!;
      return (await heading.boundingBox())!.y - header.y - header.height;
    }).toBeGreaterThanOrEqual(8);
  });
}

/** Narrow two-column native filters hide the selected state; their selected labels must remain readable. */
for (const width of [320, 390]) {
  test(`repository filters show their selected values at ${width}px`, async ({ page, account }) => {
    await page.setViewportSize({ width, height: 700 });
    const peer = await actionsPeer(page); peer.bindings = [{ ...binding }];
    await login(page, account); await page.goto("/actions");
    await page.getByText("Фильтры и сортировка", { exact: true }).click();
    await page.getByLabel("Подключение CI", { exact: true }).selectOption("pending");
    const clipped = await page.locator(".repository-filters select").evaluateAll(elements => elements.filter(element => {
      const select = element as HTMLSelectElement, style = getComputedStyle(select);
      const canvas = document.createElement("canvas"), context = canvas.getContext("2d")!;
      context.font = style.font;
      return context.measureText(select.selectedOptions[0]?.text ?? "").width + parseFloat(style.paddingLeft) + parseFloat(style.paddingRight) + 22 > select.clientWidth;
    }).map(element => element.getAttribute("aria-label")));
    expect(clipped).toEqual([]);
  });
}

/** Profile reset/clear and YAML disclosures used to expose text-sized touch targets in a long mobile form. */
test("mobile editor actions and editable controls remain comfortable to use", async ({ page, account }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const peer = await actionsPeer(page);
  peer.computers = [{ id: "cell", name: "Power Cell", runtime: "powercell-ubuntu-arm64-v1" }];
  peer.bindings = [{ ...binding, workflow_profiles: { [deploy]: { computer_ids: ["cell"] } } }];
  await login(page, account); await page.goto(repositoryURL);
  await expect(page.getByRole("group", { name: deploy, exact: true })).toBeVisible();
  const controls = page.locator(".workflow-profile .text-button, .repository-yaml summary, .repository-settings-toolbar .button");
  expect(await controls.count()).toBeGreaterThan(3);
  const small = await controls.evaluateAll(elements => elements.filter(element => element.getBoundingClientRect().height < 44).map(element => element.textContent));
  expect(small).toEqual([]);
  const fonts = await page.locator(".repository-binding-form .field input, .repository-binding-form .field select, .repository-yaml textarea").evaluateAll(elements => elements.map(element => parseFloat(getComputedStyle(element).fontSize)));
  expect(Math.min(...fonts)).toBeGreaterThanOrEqual(16);
});

/** Mobile token values and secondary controls must not retain the small text and hit areas of desktop controls. */
test("mobile token creation keeps its name and expiry readable", async ({ page, account }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await actionsPeer(page); await login(page, account); await page.goto("/api-tokens");
  await page.getByRole("button", { name: "Создать токен", exact: true }).click();
  const dialog = page.getByRole("dialog");
  expect((await dialog.getByRole("button", { name: "Закрыть диалог", exact: true }).boundingBox())!.height).toBeGreaterThanOrEqual(44);
  expect((await dialog.locator(".token-advanced summary").boundingBox())!.height).toBeGreaterThanOrEqual(44);
  const fonts = await dialog.locator(".field input, .field select").evaluateAll(elements => elements.map(element => parseFloat(getComputedStyle(element).fontSize)));
  expect(fonts.length).toBeGreaterThan(1);
  expect(Math.min(...fonts)).toBeGreaterThanOrEqual(16);
});

/** Sparse overrides for several workflows must be comparable as separate records, not a narrow joined string. */
test("mobile profile history separates workflows and stacks before and after", async ({ page, account }) => {
  await page.setViewportSize({ width: 320, height: 700 });
  const peer = await actionsPeer(page); peer.bindings = [{ ...binding }];
  peer.history.set(binding.id, [{ id: "revision", revision: 2, action: "updated", actor: null, created_at: binding.created_at, configuration: {}, changes: [{ field: "workflow_profiles", before: { [deploy]: { memory_bytes: 1024 ** 3 } }, after: { [deploy]: { memory_bytes: 2 * 1024 ** 3 }, ".github/workflows/ci.yml": { cpu_millis: 500 } } }] }]);
  await login(page, account); await page.goto(repositoryURL.replace("?", "/history?"));
  const change = page.locator(".binding-changes > div");
  await expect(change.locator("dd").last().locator(".binding-profile-diff > li")).toHaveCount(2);
  const before = (await change.locator("dd").first().boundingBox())!;
  const after = (await change.locator("dd").last().boundingBox())!;
  expect(before.width).toBeGreaterThan(200);
  expect(after.y).toBeGreaterThanOrEqual(before.y + before.height);
  await expect(change).toContainText(`${deploy}: RAM: 1 GiB`);
  await expect(change).toContainText(`${deploy}: RAM: 2 GiB`);
});

/** Adding a profile must move keyboard focus to the new fields instead of leaving it on a button for another workflow. */
test("adding an override focuses its new resource fields", async ({ page, account }) => {
  await actionsPeer(page); await login(page, account); await page.goto(repositoryURL);
  await page.getByLabel("Workflow для переопределения", { exact: true }).selectOption(deploy);
  const add = page.getByRole("button", { name: "Добавить переопределение", exact: true });
  await add.focus(); await add.press("Enter");
  await expect(page.getByLabel(`CPU · ${deploy}`, { exact: true })).toBeFocused();
});

/** Existing connections need a direct route to the YAML section instead of several screens of scrolling through profiles. */
test("editor provides a working keyboard accessible shortcut to YAML", async ({ page, account }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const peer = await actionsPeer(page); peer.bindings = [{ ...binding }];
  await login(page, account); await page.goto(repositoryURL);
  const shortcut = page.getByRole("link", { name: "Перейти к YAML", exact: true });
  await expect(shortcut).toBeVisible();
  await shortcut.focus(); await shortcut.press("Enter");
  const heading = page.getByRole("heading", { name: "Настройка YAML в GitHub", exact: true });
  await expect(heading).toBeFocused();
  const header = (await page.locator(".site-header").boundingBox())!;
  expect((await heading.boundingBox())!.y).toBeGreaterThanOrEqual(header.y + header.height + 8);
});

/** Runtime failures with a reason such as no_capacity must receive the same visible failure treatment as GitHub failures. */
test("failed CI is visibly marked as a failure even with a capacity reason", async ({ page, account }) => {
  const peer = await actionsPeer(page); peer.bindings = [{ ...binding }];
  peer.runs = [{ id: "run", run_id: 13, run_attempt: 1, status: "failed", reason: "no_capacity", url: "https://github.com/mutating/site/actions/runs/13" }];
  await login(page, account); await page.goto(repositoryURL);
  const status = page.locator(".actions-ci-status");
  await expect(status).toHaveText("Ошибка");
  expect(await status.evaluate(element => {
    const marker = document.createElement("span"); marker.style.color = "var(--danger)"; document.body.append(marker);
    const matches = getComputedStyle(element).color === getComputedStyle(marker).color; marker.remove(); return matches;
  })).toBe(true);
});

/** Disabled nested fieldsets multiplied opacity and made saved profiles hard to read during runs or without edit permission. */
for (const lockedBy of ["active run", "permissions"]) {
  test(`locked profiles remain readable with ${lockedBy}`, async ({ page, account }) => {
    const peer = await actionsPeer(page);
    peer.bindings = [{ ...binding, active_runs: lockedBy === "active run", workflow_profiles: { [deploy]: { cpu_millis: 500 } } }];
    if (lockedBy === "permissions") peer.repositories[0].can_administer = false;
    await login(page, account); await page.goto(repositoryURL);
    const profile = page.getByRole("group", { name: deploy, exact: true });
    await expect(profile.getByLabel(`CPU · ${deploy}`, { exact: true })).toBeDisabled();
    await expect(page.getByRole("button", { name: "Сохранить настройки", exact: true })).toBeDisabled();
    const opacity = await profile.locator("legend").evaluate(element => {
      let opacity = 1;
      for (let node: Element | null = element; node; node = node.parentElement) opacity *= Number(getComputedStyle(node).opacity);
      return opacity;
    });
    expect(opacity).toBeGreaterThanOrEqual(0.99);
  });
}

/** On a phone, six filters should not push the repository list several screens down; deep links with filters should expose their settings. */
test("mobile catalog keeps filters discoverable without crowding the default list", async ({ page, account }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const peer = await actionsPeer(page); peer.bindings = [{ ...binding }];
  await login(page, account); await page.goto("/actions");
  const search = page.getByLabel("Поиск репозитория", { exact: true });
  await expect(search).toBeHidden();
  const disclosure = page.getByText("Фильтры и сортировка", { exact: true });
  await disclosure.focus(); await disclosure.press("Enter");
  await expect(search).toBeVisible();
  await page.getByLabel("Подключение CI", { exact: true }).selectOption("pending");
  await page.reload();
  await expect(page.getByLabel("Подключение CI", { exact: true })).toBeVisible();
  await expect(page.getByLabel("Подключение CI", { exact: true })).toHaveValue("pending");
});

/** Revoking a token is a real touch action and must not keep the text-sized hit area of a desktop link. */
test("mobile token revoke has a usable touch target", async ({ page, account }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await actionsPeer(page);
  expect((await page.request.post(`${API}/api-tokens`, { headers: headers(account), data: { user_id: account.id, name: "Interface review", scopes: ["read"] } })).status()).toBe(201);
  await login(page, account); await page.goto("/api-tokens");
  const revoke = page.getByRole("button", { name: "Отозвать токен", exact: true });
  await expect(revoke).toBeVisible();
  expect((await revoke.boundingBox())!.height).toBeGreaterThanOrEqual(44);
});

/** A touch-sized dialog wider than the phone breakpoint must keep its primary action visible before the user discovers scrolling. */
test("token creation action stays visible in a short touch-sized dialog", async ({ page, account }) => {
  await page.setViewportSize({ width: 600, height: 400 });
  await actionsPeer(page); await login(page, account); await page.goto("/api-tokens");
  await page.getByRole("button", { name: "Создать токен", exact: true }).click();
  await expect(page.getByRole("dialog").getByRole("button", { name: "Создать личный токен", exact: true })).toBeInViewport({ ratio: 0.99 });
});
