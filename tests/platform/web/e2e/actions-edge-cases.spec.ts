import { test, expect, startSession } from "./fixtures";
import { actionsPeer, binding, repositoryURL } from "./actions-peer";

for (const canonical of [true, false]) {
  /** An in-flight Save and a toolbar toggle must not race revisions and silently lose the later command or the policy draft. */
  test(`saving ${canonical ? "canonical" : "implicit"} binding serializes a subsequent toolbar toggle`, async ({ page, account }) => {
    const peer = await actionsPeer(page); peer.bindings = [{ ...binding }];
    let release!: () => void, requested = false;
    const gate = new Promise<void>(resolve => release = resolve);
    peer.custom = async route => {
      if (route.request().method() !== "PATCH" || !route.request().postDataJSON().configuration) return false;
      requested = true; await gate; return false;
    };
    await startSession(page, account);
    await page.goto(repositoryURL + (canonical ? `&binding=${binding.id}&branch=main` : ""));
    await page.getByLabel("CPU", { exact: true }).fill("0.75");
    await page.getByRole("button", { name: "Сохранить настройки", exact: true }).click();
    const disable = page.getByRole("button", { name: "Отключить подключение", exact: true });
    try {
      await expect.poll(() => requested).toBe(true);
      await expect(disable).toBeDisabled();
      expect(peer.calls.filter(row => row.method === "PATCH")).toHaveLength(1);
      release();
      await expect(page.getByText("Настройки сохранены.", { exact: true })).toBeVisible();
      await expect(disable).toBeEnabled();
      await disable.click();
      await expect.poll(() => peer.bindings[0].enabled).toBe(false);
      expect(peer.bindings[0].cpu_millis).toBe(750);
      expect(peer.bindings[0].revision).toBe(3);
      expect(peer.calls.filter(row => row.method === "PATCH").map(row => row.body!.revision)).toEqual([1, 2]);
      await expect(page.getByRole("alert")).toHaveCount(0);
    } finally { release(); }
  });
}

for (const clipboardWorks of [true, false]) {
  /** Metadata retry can replace a deleted job automatically; the previous job's copied/fallback message must not describe its new YAML. */
  test(`automatic job replacement clears previous ${clipboardWorks ? "copied" : "manual-copy"} feedback`, async ({ page, account }) => {
    await page.addInitScript(ok => {
      Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: async () => { if (!ok) throw new Error("Clipboard unavailable"); } } });
    }, clipboardWorks);
    const peer = await actionsPeer(page); peer.bindings = [{ ...binding }];
    let failMetadata = false;
    peer.custom = async (route, url) => {
      if (!failMetadata || !url.pathname.endsWith("/configuration")) return false;
      await route.fulfill({ status: 503, json: {} }); return true;
    };
    await startSession(page, account);
    await page.goto(repositoryURL);
    await page.getByLabel("CPU", { exact: true }).fill("0.75");
    failMetadata = true;
    await page.getByRole("button", { name: "Сохранить настройки", exact: true }).click();
    await expect(page.getByRole("alert")).toBeVisible();
    await expect(page.getByLabel("Workflow YAML", { exact: true })).toHaveValue(/test:/);
    const feedback = page.getByText(clipboardWorks ? "YAML скопирован." : "Выделен YAML. Скопируйте его вручную.", { exact: true });
    await page.getByRole("button", { name: "Скопировать YAML", exact: true }).click();
    await expect(feedback).toBeVisible();
    peer.workflows[0].jobs = ["checks"]; failMetadata = false;
    await page.getByRole("button", { name: /^Попробовать снова/ }).click();
    await expect(page.getByLabel("Задача workflow", { exact: true })).toHaveValue("checks");
    await expect(page.getByLabel("Workflow YAML", { exact: true })).toHaveValue(/checks:/);
    await expect(feedback).toHaveCount(0);
    await page.getByRole("button", { name: "Скопировать YAML", exact: true }).click();
    await expect(feedback).toBeVisible();
  });
}

/** A successful save must reveal its own YAML, then leave another binding's message and keyboard focus untouched after a history jump. */
test("saved feedback and YAML focus remain scoped to the saved binding", async ({ page, account }) => {
  const peer = await actionsPeer(page);
  const otherId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
  peer.companies = [{ id: "lab", name: "CI Lab", my_permissions: ["resources.read", "resources.manage", "execution.read", "execution.run"] }];
  peer.bindings = [{ ...binding }, { ...binding, id: otherId, company_id: "lab" }];
  await startSession(page, account);
  await page.goto(`${repositoryURL}&binding=${binding.id}`);
  await page.getByRole("link", { name: "История изменений", exact: true }).click();
  await page.getByLabel("Подключение в истории", { exact: true }).selectOption(otherId);
  await page.getByRole("link", { name: "← Настройки подключения", exact: true }).click();
  await page.getByLabel("CPU", { exact: true }).fill("0.75");
  await page.getByRole("button", { name: "Сохранить настройки", exact: true }).click();
  const feedback = page.getByText("Настройки сохранены.", { exact: true });
  const heading = page.getByRole("heading", { name: "Настройка YAML в GitHub", exact: true });
  await expect(feedback).toBeVisible();
  await expect(heading).toBeFocused();
  await page.evaluate(() => history.go(-3));
  await expect(page).toHaveURL(new RegExp(`binding=${binding.id}`));
  await expect(page.getByLabel("Пул компьютеров", { exact: true })).toHaveValue("");
  await expect(page.getByLabel("CPU", { exact: true })).toHaveValue("2");
  await expect(page.getByLabel("Workflow YAML", { exact: true })).toBeVisible();
  await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
  await expect.soft(heading).not.toBeFocused();
  await expect(feedback).toHaveCount(0);
});

for (const lateError of [false, true]) {
  /** A toggle error belongs to its original selection, whether it arrives before or after a history jump to another pool's binding. */
  test(`toggle failure ${lateError ? "arriving after" : "shown before"} a binding switch cannot contaminate the current editor`, async ({ page, account }) => {
    const peer = await actionsPeer(page);
    const otherId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
    peer.companies = [{ id: "lab", name: "CI Lab", my_permissions: ["resources.read", "resources.manage", "execution.read", "execution.run"] }];
    peer.bindings = [{ ...binding }, { ...binding, id: otherId, company_id: "lab", enabled: false }];
    let release!: () => void, requested = false;
    const gate = new Promise<void>(resolve => release = resolve);
    peer.custom = async (route, url) => {
      if (route.request().method() !== "PATCH" || !url.pathname.endsWith(otherId)) return false;
      requested = true; await gate; await route.fulfill({ status: 409, json: { detail: "Binding changed; reload before saving" } }); return true;
    };
    await startSession(page, account);
    await page.goto(`${repositoryURL}&binding=${binding.id}`);
    await page.getByRole("link", { name: "История изменений", exact: true }).click();
    await page.getByLabel("Подключение в истории", { exact: true }).selectOption(otherId);
    await page.getByRole("link", { name: "← Настройки подключения", exact: true }).click();
    await page.getByRole("button", { name: "Включить подключение", exact: true }).click();
    try {
      await expect.poll(() => requested).toBe(true);
      const response = page.waitForResponse(row => row.request().method() === "PATCH" && row.url().endsWith(otherId));
      if (!lateError) {
        release(); await response;
        await expect(page.getByRole("alert")).toBeVisible();
      }
      await page.evaluate(() => history.go(-3));
      await expect(page).toHaveURL(new RegExp(`binding=${binding.id}`));
      await expect(page.getByLabel("Пул компьютеров", { exact: true })).toHaveValue("");
      if (lateError) { release(); await response; }
      const save = page.getByRole("button", { name: "Сохранить настройки", exact: true });
      await expect(save).toBeEnabled();
      await expect(page.getByRole("alert")).toHaveCount(0);
      await page.getByLabel("CPU", { exact: true }).fill("0.75");
      await save.click();
      await expect(page.getByText("Настройки сохранены.", { exact: true })).toBeVisible();
      await expect(page.getByRole("alert")).toHaveCount(0);
      expect(peer.bindings.find(row => row.id === binding.id)!.cpu_millis).toBe(750);
      expect(peer.bindings.find(row => row.id === otherId)!.enabled).toBe(false);
    } finally { release(); }
  });
}

for (const initiallyEnabled of [true, false]) for (const jumpBranch of [false, true]) {
  /** Toggle and policy editing must not race; a browser branch jump must still refresh the completed command without changing that URL. */
  test(`pending ${initiallyEnabled ? "disable" : "enable"} ${jumpBranch ? "refreshes the branch reached through browser history" : "serializes policy editing"}`, async ({ page, account }) => {
    const peer = await actionsPeer(page); peer.bindings = [{ ...binding, enabled: initiallyEnabled }];
    let release!: () => void, requested = false;
    const gate = new Promise<void>(resolve => release = resolve);
    peer.custom = async route => {
      if (route.request().method() !== "PATCH") return false;
      requested = true; await gate; return false;
    };
    await startSession(page, account);
    await page.goto(repositoryURL + (jumpBranch ? "&branch=develop" : ""));
    if (jumpBranch) {
      await expect(page.getByLabel("Ветка", { exact: true })).toHaveValue("develop");
      await page.getByRole("link", { name: "← К репозиториям", exact: true }).click();
      await page.getByRole("link", { name: "Настроить", exact: true }).click();
    }
    const branch = page.getByLabel("Ветка", { exact: true });
    await expect(branch).toHaveValue("main");
    await expect(page.getByRole("button", { name: "Сохранить настройки", exact: true })).toBeEnabled();
    await page.getByRole("button", { name: initiallyEnabled ? "Отключить подключение" : "Включить подключение", exact: true }).click();
    try {
      await expect.poll(() => requested).toBe(true);
      if (jumpBranch) {
        await page.evaluate(() => history.go(-2));
        await expect(branch).toHaveValue("develop");
        await expect(page.getByRole("button", { name: "Обновить", exact: true })).toBeEnabled();
      } else {
        await expect(branch).toBeDisabled();
        await expect(page.getByLabel("CPU", { exact: true })).toBeDisabled();
        await expect(page.locator(".repository-binding-form .button.primary")).toBeDisabled();
      }
      release();
      await expect(page.locator(".repository-settings-toolbar .repository-state")).toHaveText(initiallyEnabled ? "Отключён" : "Ожидает настройки workflow");
      await expect(page.getByRole("button", { name: initiallyEnabled ? "Включить подключение" : "Отключить подключение", exact: true })).toBeEnabled();
      await expect(branch).toHaveValue(jumpBranch ? "develop" : "main");
      await expect(page.getByRole("button", { name: "Сохранить настройки", exact: true })).toBeEnabled();
      expect(peer.bindings[0].revision).toBe(2);
      expect(peer.bindings[0].enabled).toBe(!initiallyEnabled);
      expect(peer.calls.filter(row => row.method === "PATCH")).toHaveLength(1);
    } finally { release(); }
  });
}

for (const selected of ["ABCDEFAB-CDEF-4ABC-8ABC-ABCDEFABCDEF", "aBcDeFaB-cDeF-4aBc-8aBc-AbCdEfAbCdEf"]) {
  /** UUIDs accepted by the API and editor must also open the same audit history regardless of letter casing. */
  test(`history accepts a case-insensitive binding UUID: ${selected}`, async ({ page, account }) => {
    const peer = await actionsPeer(page);
    const id = selected.toLowerCase();
    peer.bindings = [{ ...binding, id }];
    peer.history.set(id, [{ id: "revision", revision: 1, action: "created", created_at: binding.created_at, actor: null, configuration: {}, changes: [{ field: "cpu_millis", before: null, after: 500 }] }]);
    await startSession(page, account);
    await page.goto(repositoryURL.replace("?", "/history?") + `&binding=${selected}`);
    await expect(page.locator(".binding-revision")).toHaveCount(1);
    await expect(page.getByLabel("Подключение в истории", { exact: true })).toHaveValue(id);
    await expect(page.locator(".binding-changes")).toContainText("0.5 CPU");
    expect(peer.calls.filter(row => row.path.endsWith("/revisions")).map(row => row.path)).toEqual([`github/routes/${id}/revisions`]);
    await page.getByRole("link", { name: "← Настройки подключения", exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`binding=${id}`));
    await expect(page.getByLabel("CPU", { exact: true })).toHaveValue("2");
  });
}

for (const implicit of [false, true]) {
  /** Switching primary bindings must not retain another binding's form, actions, state or CI result while discovery fails. */
  test(`history return keeps ${implicit ? "implicit" : "explicit"} binding metadata scoped to its selection`, async ({ page, account }) => {
    const peer = await actionsPeer(page);
    const aId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", bId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
    const run = (id: string, run_id: number, success: boolean) => ({ id, route_id: id, run_id, run_attempt: 1, status: "completed", reason: success ? "github_success" : "github_failure", created_at: binding.created_at, url: `https://github.com/mutating/ci-sandbox/actions/runs/${run_id}` });
    const a = { ...binding, id: aId, cpu_millis: 500, enabled: implicit, state: implicit ? "connected" : "disabled", last_run: run(aId, 101, true) };
    const b = { ...binding, id: bId, cpu_millis: 2000, enabled: !implicit, state: implicit ? "disabled" : "pending", last_run: run(bId, 202, false) };
    peer.bindings = [a, b]; peer.runs = [a.last_run, b.last_run];
    let hold = false, requested = false, fail = true, release!: () => void;
    const gate = new Promise<void>(resolve => release = resolve);
    peer.custom = async (route, url) => {
      if (!url.pathname.endsWith("/configuration")) return false;
      const selected = url.searchParams.get("binding_id") === bId ? b : a;
      if (hold && selected.id === aId) {
        requested = true; await gate;
        if (fail) { await route.fulfill({ status: 503, json: {} }); return true; }
      }
      await route.fulfill({ json: { repository: peer.repositories[0], branches: peer.branches, workflows: peer.workflows, bindings: peer.bindings, binding_id: selected.id, state: selected.state, last_run: selected.last_run } }); return true;
    };
    await startSession(page, account);
    await page.goto(repositoryURL + (implicit ? "" : `&binding=${aId}`));
    await expect(page.getByLabel("CPU", { exact: true })).toHaveValue("0.5");
    await page.getByRole("link", { name: "История изменений", exact: true }).click();
    await page.getByLabel("Подключение в истории", { exact: true }).selectOption(bId);
    await page.getByRole("link", { name: "← Настройки подключения", exact: true }).click();
    await expect(page.getByLabel("CPU", { exact: true })).toHaveValue("2");
    hold = true;
    const checkNoForeignData = async () => {
      await expect(page.locator(".repository-settings-page > .field-help > a.repository-run-result[href$='/202']")).toHaveCount(0);
      await expect(page.getByLabel("CPU", { exact: true }).filter({ visible: true })).toHaveCount(0);
      await expect(page.locator(".repository-settings-toolbar .repository-state")).toHaveCount(0);
      await expect(page.getByRole("button", { name: /^(Включить|Отключить) подключение$/ })).toHaveCount(0);
    };
    try {
      await page.evaluate(() => history.go(-3)); await expect.poll(() => requested).toBe(true);
      await checkNoForeignData();
      release(); await expect(page.getByRole("alert")).toBeVisible();
      await checkNoForeignData();
      expect(peer.calls.some(row => row.method === "PATCH")).toBe(false);
      fail = false; await page.getByRole("button", { name: /Попробовать снова/ }).click();
      await expect(page.getByLabel("CPU", { exact: true })).toHaveValue("0.5");
      await expect(page.locator(".repository-settings-toolbar .repository-state")).toHaveText(implicit ? "Подключён" : "Отключён");
      await expect(page.locator(".repository-settings-page > .field-help > a.repository-run-result")).toHaveAttribute("href", /\/101$/);
    } finally { release(); }
  });
}

for (const moveFocus of [false, true]) {
  /** A late save must preserve the user's next keyboard action while an ordinary save still reveals and focuses YAML. */
  test(`late save ${moveFocus ? "preserves the chosen history link" : "focuses YAML when no new action was chosen"}`, async ({ page, account }) => {
    const peer = await actionsPeer(page); peer.bindings = [{ ...binding }];
    let release!: () => void, requested = false;
    const gate = new Promise<void>(resolve => release = resolve);
    peer.custom = async route => {
      if (route.request().method() !== "PATCH") return false;
      requested = true; await gate; return false;
    };
    await startSession(page, account);
    await page.goto(repositoryURL);
    await page.getByLabel("CPU", { exact: true }).fill("0.5");
    await page.getByRole("button", { name: "Сохранить настройки", exact: true }).click();
    try {
      await expect.poll(() => requested).toBe(true);
      const history = page.getByRole("link", { name: "История изменений", exact: true });
      if (moveFocus) { await history.focus(); await expect(history).toBeFocused(); }
      release();
      await expect(page.getByText("Настройки сохранены.", { exact: true })).toBeVisible();
      const heading = page.getByRole("heading", { name: "Настройка YAML в GitHub", exact: true });
      await expect(heading).toBeVisible();
      await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
      if (moveFocus) {
        await expect(history).toBeFocused();
        await page.keyboard.press("Enter");
        await expect(page.locator(".binding-revision")).toHaveCount(2);
      } else {
        await expect(heading).toBeFocused();
        const header = (await page.locator(".site-header").boundingBox())!;
        await expect.poll(async () => (await heading.boundingBox())!.y - header.y - header.height).toBeGreaterThanOrEqual(8);
      }
    } finally { release(); }
  });
}

for (const recover of ["retry", "save"] as const) {
  /** A resolved toggle failure must disappear after refreshing stale metadata or successfully saving the same canonical selection. */
  test(`toggle failure clears after successful ${recover} of the same binding`, async ({ page, account }) => {
    const peer = await actionsPeer(page); peer.bindings = [{ ...binding, enabled: false }];
    let fail = true;
    peer.custom = async (route, url) => {
      if (!fail || route.request().method() !== "PATCH" || !url.pathname.endsWith(binding.id)) return false;
      if (recover === "retry") peer.bindings = [{ ...peer.bindings[0], revision: 2, cpu_millis: 750 }];
      await route.fulfill({ status: recover === "retry" ? 409 : 503, json: {} }); return true;
    };
    await startSession(page, account);
    const url = `${repositoryURL}&binding=${binding.id}&branch=main`;
    await page.goto(url);
    await page.getByRole("button", { name: "Включить подключение", exact: true }).click();
    await expect(page.getByRole("alert")).toBeVisible();
    fail = false;
    if (recover === "retry") {
      await page.getByRole("button", { name: /^Попробовать снова/ }).click();
      await expect(page.getByLabel("CPU", { exact: true })).toHaveValue("0.75");
    } else {
      await page.getByLabel("CPU", { exact: true }).fill("0.5");
      await page.getByRole("button", { name: "Сохранить настройки", exact: true }).click();
      await expect(page.getByText("Настройки сохранены.", { exact: true })).toBeVisible();
      expect(peer.bindings[0].cpu_millis).toBe(500);
    }
    await expect(page).toHaveURL(new RegExp(url.replace("?", "\\?")));
    await expect(page.getByRole("alert")).toHaveCount(0);
    expect(peer.bindings[0].revision).toBe(2);
  });
}
