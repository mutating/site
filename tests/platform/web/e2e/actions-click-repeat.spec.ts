import { test, expect, login } from "./fixtures";
import { actionsPeer, binding, repositoryURL } from "./actions-peer";

/** Case-insensitive UUID links must keep the requested binding, hiding another primary's metadata until the history-jump refresh finishes. */
test("explicit binding links accept canonical UUID casing without selecting another binding", async ({ page, account }) => {
  const peer = await actionsPeer(page);
  const selectedId = "abcdef12-3456-4789-abcd-123456789abc";
  peer.bindings = [{ ...binding, id: selectedId, cpu_millis: 500 }, { ...binding }];
  let hold = false, requested = false, release!: () => void;
  const gate = new Promise<void>(resolve => release = resolve);
  peer.custom = async (route, url) => {
    if (!url.pathname.endsWith("/configuration")) return false;
    const selected = peer.bindings.find(row => row.id === url.searchParams.get("binding_id")?.toLowerCase()) ?? peer.bindings[0];
    if (hold && selected.id === selectedId) { requested = true; await gate; }
    await route.fulfill({ json: { repository: peer.repositories[0], branches: peer.branches, workflows: peer.workflows, bindings: peer.bindings, binding_id: selected.id, state: selected.enabled ? "pending" : "disabled", last_run: null } }); return true;
  };
  await login(page, account); await page.goto(`${repositoryURL}&binding=${selectedId.toUpperCase()}`);
  await expect(page.getByLabel("CPU", { exact: true })).toHaveValue("0.5");
  await page.getByRole("link", { name: "История изменений", exact: true }).click();
  await page.getByLabel("Подключение в истории", { exact: true }).selectOption(binding.id);
  await page.getByRole("link", { name: "← Настройки подключения", exact: true }).click();
  await expect(page.getByLabel("CPU", { exact: true })).toHaveValue("2");
  hold = true;
  try {
    await page.evaluate(() => history.go(-3));
    await expect.poll(() => requested).toBe(true);
    await expect(page.getByLabel("CPU", { exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Отключить подключение", exact: true })).toHaveCount(0);
    release();
    await expect(page.getByLabel("CPU", { exact: true })).toHaveValue("0.5");
    await expect(page.getByRole("button", { name: "Сохранить настройки", exact: true })).toBeEnabled();
    await page.getByRole("button", { name: "Отключить подключение", exact: true }).click();
    await expect.poll(() => peer.bindings.find(row => row.id === selectedId)!.enabled).toBe(false);
    expect(peer.bindings.find(row => row.id === binding.id)!.enabled).toBe(true);
  } finally { release(); }
});

/** Lost pool access hides its binding and returns configuration 404; a history jump must never expose another pool's mutations or YAML. */
test("returning to an unavailable binding cannot fall back to another connection", async ({ page, account }) => {
  const peer = await actionsPeer(page);
  const archivedId = "22222222-2222-4222-8222-222222222222";
  peer.companies = [{ id: "lab", name: "CI Lab", my_permissions: ["resources.read", "resources.manage", "execution.read", "execution.run"] }];
  peer.bindings = [{ ...binding, id: archivedId, company_id: "lab", enabled: false }, { ...binding }];
  await login(page, account); await page.goto(`${repositoryURL}&binding=${archivedId}`);
  await expect(page.getByRole("button", { name: "Включить подключение", exact: true })).toBeVisible();
  await page.getByRole("link", { name: "История изменений", exact: true }).click();
  await page.getByLabel("Подключение в истории", { exact: true }).selectOption(binding.id);
  await page.getByRole("link", { name: "← Настройки подключения", exact: true }).click();
  await expect(page.getByRole("button", { name: "Отключить подключение", exact: true })).toBeVisible();
  peer.bindings = peer.bindings.filter(row => row.id !== archivedId);
  peer.companies = [];
  await page.getByRole("button", { name: "Обновить", exact: true }).click();
  await expect(page.getByRole("button", { name: "Сохранить настройки", exact: true })).toBeEnabled();
  let release!: () => void, requested = false;
  const gate = new Promise<void>(resolve => release = resolve);
  peer.custom = async (route, url) => {
    if (!url.pathname.endsWith("/configuration") || url.searchParams.get("binding_id") !== archivedId) return false;
    requested = true; await gate; await route.fulfill({ status: 404, json: {} }); return true;
  };
  try {
    await page.evaluate(() => history.go(-3));
    await expect(page).toHaveURL(new RegExp(`binding=${archivedId}`));
    await expect.poll(() => requested).toBe(true);
    const disable = page.getByRole("button", { name: "Отключить подключение", exact: true });
    if (await disable.isVisible() && await disable.isEnabled()) await disable.click();
    expect(peer.calls.filter(row => row.method === "PATCH")).toHaveLength(0);
    await expect(page.getByRole("button", { name: "Отключить подключение", exact: true })).toHaveCount(0);
    await expect(page.getByLabel("Workflow YAML", { exact: true })).toHaveCount(0);
    release(); await expect(page.getByRole("alert")).toBeVisible();
    await expect(page.getByRole("button", { name: "Отключить подключение", exact: true })).toHaveCount(0);
    expect(peer.calls.filter(row => row.method === "PATCH")).toHaveLength(0);
    expect(peer.bindings[0].enabled).toBe(true);
    await page.getByRole("link", { name: "← К репозиториям", exact: true }).click();
    await page.getByRole("link", { name: "Настроить", exact: true }).click();
    await expect(page.getByRole("button", { name: "Отключить подключение", exact: true })).toBeVisible();
  } finally { release(); }
});

for (const selection of ["job", "workflow"] as const) for (const result of ["success", "failure"] as const) {
  /** Clipboard completion must affect its original YAML without changing selection or focus after a job/workflow switch. */
  test(`late clipboard ${result} cannot affect YAML after changing ${selection}`, async ({ page, account }) => {
    await page.addInitScript(() => {
      (window as any).clipboardPending = false;
      (window as any).clipboardValues = [];
      Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: (value: string) => {
        (window as any).clipboardValues.push(value);
        (window as any).clipboardPending = true;
        return new Promise<void>((resolve, reject) => { (window as any).finishClipboard = (ok: boolean) => ok ? resolve() : reject(new Error("Clipboard unavailable")); });
      } } });
    });
    const peer = await actionsPeer(page); peer.bindings = [{ ...binding }];
    await login(page, account); await page.goto(repositoryURL);
    await expect(page.getByLabel("Workflow YAML", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Скопировать YAML", exact: true }).click();
    await expect.poll(() => page.evaluate(() => (window as any).clipboardPending)).toBe(true);
    const control = page.getByLabel(selection === "job" ? "Задача workflow" : "Workflow для настройки", { exact: true });
    await control.focus();
    await control.selectOption(selection === "job" ? "checks" : ".github/workflows/deploy.yml");
    await expect(page.getByLabel("Workflow YAML", { exact: true })).toHaveValue(selection === "job" ? /checks:/ : /deploy:/);
    await page.evaluate(async ok => {
      (window as any).finishClipboard(ok);
      await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    }, result === "success");
    await expect(page.locator(".repository-yaml").getByRole("status")).toHaveText("");
    expect(await page.getByLabel("Workflow YAML", { exact: true }).evaluate(element => {
      const text = element as HTMLTextAreaElement;
      return text.selectionEnd! - text.selectionStart!;
    })).toBe(0);
    await expect(control).toBeFocused();
    await page.getByRole("button", { name: "Скопировать YAML", exact: true }).click();
    await page.evaluate(async () => {
      (window as any).finishClipboard(true);
      await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    });
    await expect(page.locator(".repository-yaml").getByRole("status")).toHaveText("YAML скопирован.");
    expect(await page.evaluate(() => (window as any).clipboardValues.at(-1))).toBe(await page.getByLabel("Workflow YAML", { exact: true }).inputValue());
  });
}
