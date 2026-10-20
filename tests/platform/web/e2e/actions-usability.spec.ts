import { test, expect, login } from "./fixtures";
import { actionsPeer, binding, repository } from "./actions-peer";

const second = { ...binding, id: "55555555-5555-4555-8555-555555555555", job_key: "integration_tests",
  repository: "mutating/very-long-repository-name-with-several-components-for-continuous-integration" };

// Assertions: long names cannot squeeze action labels into letter-wide columns;
// both languages keep every action inside 320px/mobile/tablet/desktop viewports.
test("Actions keeps binding actions readable beside long repository names", async ({ page, account }) => {
  const peer = await actionsPeer(page); peer.bindings = [{ ...binding }, second];
  await login(page, account); await page.goto("/actions");
  await expect(page.getByRole("button", { name: "Workflow и запуски", exact: true })).toHaveCount(2);
  for (const language of ["ru", "en"]) {
    if (language === "en") await page.getByRole("button", { name: "Выбрать язык", exact: true }).click();
    for (const width of [320, 390, 768, 1024, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      const actions = page.locator(".actions-repository .form-actions button");
      for (const button of await actions.all()) {
        const box = await button.boundingBox();
        const lines = await button.evaluate((node) => {
          const range = document.createRange(); range.selectNodeContents(node);
          return Array.from(range.getClientRects()).filter((rect) => rect.width > 0).length;
        });
        expect(lines).toBeLessThanOrEqual(2);
        expect(box!.width).toBeGreaterThanOrEqual(44);
        expect(box!.height).toBeLessThan(60);
        expect(box!.x).toBeGreaterThanOrEqual(0);
        expect(box!.x + box!.width).toBeLessThanOrEqual(width);
      }
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    }
  }
});

// Assertions: choosing/reopening a binding identifies its exact repository/job/ref,
// marks the selected control, references only an existing details panel, and
// puts keyboard focus below the sticky header.
test("Actions opens and focuses the selected binding details", async ({ page, account }) => {
  const peer = await actionsPeer(page); peer.bindings = [{ ...binding }, second];
  await login(page, account); await page.goto("/actions");
  const choices = page.getByRole("button", { name: "Workflow и запуски", exact: true });
  await expect(choices.first()).not.toHaveAttribute("aria-controls");
  const heading = page.locator(".actions-ci-detail").getByRole("heading", { name: "Workflow и запуски", exact: true });
  const viewport = page.viewportSize()!;
  for (const width of [viewport.width, 320]) {
    await page.setViewportSize({ ...viewport, width });
    for (const index of [0, 1, 1]) {
      await choices.nth(index).click();
      await expect(heading).toBeFocused();
      await expect(choices.nth(index)).toHaveAttribute("aria-pressed", "true");
      await expect(choices.nth(index)).toHaveAttribute("aria-controls", "ci-route-details");
      await expect(choices.nth(1 - index)).toHaveAttribute("aria-pressed", "false");
      await expect(page.locator(".actions-ci-context")).toContainText(index ? second.repository : binding.repository);
      await expect(page.locator(".actions-ci-context")).toContainText(index ? second.job_key : binding.job_key);
      await expect(page.locator(".actions-ci-context")).toContainText(binding.ref);
      expect((await heading.boundingBox())!.y).toBeGreaterThan((await page.locator(".site-header").boundingBox())!.y + (await page.locator(".site-header").boundingBox())!.height);
      await expect(page.getByRole("textbox", { name: "Workflow YAML", exact: true })).toHaveValue(new RegExp(index ? second.id : binding.id));
    }
  }
});

// Assertions: API-completed jobs display their actual GitHub conclusion rather
// than reporting failures as a generic completion; unknown details stay private.
test("Actions distinguishes every GitHub job conclusion in history", async ({ page, account }) => {
  const peer = await actionsPeer(page); peer.bindings = [{ ...binding }];
  const outcomes = [
    ["success", "Успешно"], ["failure", "Ошибка"], ["cancelled", "Отменён"],
    ["timed_out", "Истёк срок выполнения"], ["skipped", "Пропущен"], ["neutral", "Нейтральный результат"],
    ["action_required", "Требуется действие"], ["stale", "Устарел"], ["startup_failure", "Ошибка"],
    ["future-private-outcome", "Завершён"],
  ];
  peer.runs = outcomes.map(([outcome], i) => ({ id: String(i), run_id: i + 100, run_attempt: 2,
    status: outcome === "cancelled" ? "cancelled" : "completed", reason: `github_${outcome}`,
    url: `https://github.com/${repository.full_name}/actions/runs/${i + 100}/attempts/2` }));
  await login(page, account); await page.goto("/actions");
  await page.getByRole("button", { name: "Workflow и запуски", exact: true }).click();
  for (const [i, [, label]] of outcomes.entries()) {
    const row = page.locator(".actions-ci-runs p").filter({ has: page.getByRole("link", { name: `#${i + 100} / 2`, exact: true }) });
    await expect(row.locator(".actions-ci-status")).toHaveText(label);
    await expect(row).not.toContainText("future-private-outcome");
  }
  await page.getByRole("button", { name: "Выбрать язык", exact: true }).click();
  await expect(page.locator(".actions-ci-status").first()).toHaveText("Succeeded");
  await expect(page.locator(".actions-ci-status").nth(1)).toHaveText("Failed");
});

// Assertions: default refs follow the chosen repository's actual default branch;
// explicit user edits are preserved and the submitted repository/ref stay paired.
test("Actions uses each repository default branch and preserves an explicit ref", async ({ page, account }) => {
  const peer = await actionsPeer(page);
  peer.repositories = [{ ...repository, default_branch: "develop" }, { ...repository, id: 790, full_name: "mutating/other", default_branch: "release/v2" }];
  await login(page, account); await page.goto("/actions");
  const ref = page.getByRole("textbox", { name: "Git ref", exact: true });
  await expect(ref).toHaveValue("refs/heads/develop");
  await ref.fill("refs/heads/custom");
  await page.getByRole("combobox", { name: "Репозитории", exact: true }).selectOption("790");
  await expect(ref).toHaveValue("refs/heads/release/v2");
  await ref.fill("refs/heads/feature/ci");
  await page.getByRole("spinbutton", { name: "CPU", exact: true }).fill("1");
  await expect(ref).toHaveValue("refs/heads/feature/ci");
  await page.getByRole("button", { name: "Создать привязку", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "Workflow YAML", exact: true })).toBeVisible();
  expect(peer.calls.find((call) => call.method === "POST" && call.path === "github/routes")?.body).toMatchObject({ repository_id: 790, ref: "refs/heads/feature/ci" });
});

// Assertions: platform resource limits, reserved routing job IDs and
// malformed branch/workflow values are rejected locally with no create request.
// Valid larger resources are submitted without any backend policy.
test("Actions validates routing and platform limits before submission", async ({ page, account }) => {
  const peer = await actionsPeer(page);
  await login(page, account); await page.goto("/actions");
  const create = page.getByRole("button", { name: "Создать привязку", exact: true });
  for (const [name, invalid, valid] of [
    ["workflow", ".github/workflows/ci..yml", ".github/workflows/ci.yml"],
    ["job", "powercell_route", "test"], ["ref", "main", "refs/heads/main"],
    ["ref", "refs/heads/../main", "refs/heads/main"], ["ref", "refs/heads/feature//ci", "refs/heads/main"],
    ["cpu", "1001", "2"], ["memory", "1048577", "4"], ["disk", "1025", "10"],
  ]) {
    const input = page.locator(`.actions-ci-form [name="${name}"]`);
    await input.fill(invalid); await create.click();
    expect(await input.evaluate((node) => (node as HTMLInputElement).validity.valid)).toBe(false);
    expect(peer.calls.filter((call) => call.path === "github/routes" && call.method === "POST")).toHaveLength(0);
    await input.fill(valid);
  }
  await page.locator('[name="cpu"]').fill("3");
  await page.locator('[name="memory"]').fill("9");
  await page.locator('[name="disk"]').fill("15");
  await create.click();
  await expect(page.getByRole("textbox", { name: "Workflow YAML", exact: true })).toBeVisible();
  expect(peer.calls.find((call) => call.path === "github/routes" && call.method === "POST")?.body).toMatchObject({ cpu_millis: 3000, memory_bytes: 9 * 1024 ** 3, disk_bytes: 15 * 1024 ** 3 });
});

// Assertions: pool discovery failures have a working retry; pending/failed
// metadata cannot silently submit an unrestricted pool; success enables creation.
for (const endpoint of ["companies", "computers"]) {
  test(`Actions recovers ${endpoint} discovery before allowing a binding`, async ({ page, account }) => {
    const peer = await actionsPeer(page);
    await login(page, account);
    peer.failures.set(`GET ${endpoint}`, [503]);
    await page.goto("/actions");
    await expect(page.getByRole("alert")).toBeVisible();
    const create = page.getByRole("button", { name: "Создать привязку", exact: true });
    await expect(create).toBeDisabled();
    let release!: () => void; const gate = new Promise<void>((resolve) => { release = resolve; });
    peer.custom = async (route, url) => {
      if (url.pathname !== `/v1/${endpoint}`) return false;
      await gate; await route.fulfill({ json: [] }); return true;
    };
    await page.getByRole("button", { name: /Попробовать снова/ }).click();
    await expect.poll(() => peer.calls.filter((call) => call.path === endpoint).length).toBeGreaterThan(1);
    await expect(create).toBeDisabled();
    expect(peer.calls.filter((call) => call.method === "POST" && call.path === "github/routes")).toHaveLength(0);
    release();
    await expect(create).toBeEnabled();
    await expect(page.getByRole("alert")).toHaveCount(0);
    await expect(page.getByText(/В пуле пока нет Power Cells/)).toBeVisible();
  });
}

// Assertions: any-computer is a deliberate accessible action after selecting
// specific Macs; the saved binding contains no invisible stale machine IDs.
test("Actions clears specific computers without modifier keys", async ({ page, account }) => {
  const peer = await actionsPeer(page);
  peer.computers = [{ id: "mac", name: "MacBook Pro", runtime: "powercell-ubuntu-arm64-v1" }];
  await login(page, account); await page.goto("/actions");
  const computers = page.getByRole("listbox", { name: /^Конкретные компьютеры/ });
  await computers.selectOption("mac");
  await page.getByRole("button", { name: "Использовать любой компьютер", exact: true }).click();
  await expect(computers).toHaveValues([]);
  await page.getByRole("button", { name: "Создать привязку", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "Workflow YAML", exact: true })).toBeVisible();
  expect(peer.calls.find((call) => call.method === "POST" && call.path === "github/routes")?.body?.computer_ids).toEqual([]);
});

// Assertions: workflow/history loading remains explicit, never claims empty
// history, and cannot spawn duplicate refresh requests while a response is pending.
test("Actions shows pending history while the workflow is already available", async ({ page, account }) => {
  const peer = await actionsPeer(page); peer.bindings = [{ ...binding }];
  let release!: () => void; const gate = new Promise<void>((resolve) => { release = resolve; });
  peer.custom = async (route, url) => {
    if (!url.pathname.endsWith("/runs")) return false;
    await gate; await route.fulfill({ json: [] }); return true;
  };
  await login(page, account); await page.goto("/actions");
  await page.getByRole("button", { name: "Workflow и запуски", exact: true }).click();
  const detail = page.locator(".actions-ci-detail");
  await expect(page.getByRole("textbox", { name: "Workflow YAML", exact: true })).toBeVisible();
  await expect(detail.getByRole("status")).toBeVisible();
  await expect(detail.getByRole("button", { name: "Обновить", exact: true })).toBeDisabled();
  await expect(page.getByText("Запусков по этой привязке пока нет.", { exact: true })).toHaveCount(0);
  release();
  await expect(detail.getByRole("status")).toHaveCount(0);
  await expect(page.getByText("Запусков по этой привязке пока нет.", { exact: true })).toBeVisible();
  await expect(detail.getByRole("button", { name: "Обновить", exact: true })).toBeEnabled();
});

// Assertions: submission failures are brought into view below the sticky header
// even after a long mobile form; retry preserves the entered data and opens the result.
test("Actions brings creation errors and the saved workflow into view", async ({ page, account }) => {
  const peer = await actionsPeer(page); peer.failures.set("POST github/routes", [422]);
  await page.setViewportSize({ width: 390, height: 844 });
  await login(page, account); await page.goto("/actions");
  await page.getByRole("textbox", { name: "Job ID", exact: true }).fill("checks");
  const create = page.getByRole("button", { name: "Создать привязку", exact: true });
  await create.click();
  await expect(page.locator(".actions-ci-feedback")).toBeFocused();
  const alert = page.getByRole("alert");
  await expect(alert).toBeInViewport({ ratio: 1 });
  expect((await alert.boundingBox())!.y).toBeGreaterThan(90);
  await expect(page.getByRole("textbox", { name: "Job ID", exact: true })).toHaveValue("checks");
  await create.click();
  const heading = page.locator(".actions-ci-detail h3");
  await expect(heading).toBeFocused();
  await expect(heading).toBeInViewport({ ratio: 1 });
  expect((await heading.boundingBox())!.y).toBeGreaterThan(90);
});

// Assertions: refreshing discovery keeps the existing account, draft policy,
// chosen computers and open details; each discovery endpoint is requested once.
test("Actions refresh preserves the binding draft and selected history", async ({ page, account }) => {
  const peer = await actionsPeer(page); peer.bindings = [{ ...binding }];
  peer.computers = [{ id: "mac", name: "MacBook Pro", runtime: "powercell-ubuntu-arm64-v1" }];
  await login(page, account); await page.goto("/actions");
  await page.locator(".actions-ci-create summary").click();
  await page.getByRole("textbox", { name: "Workflow", exact: true }).fill(".github/workflows/checks.yml");
  await page.getByRole("textbox", { name: "Job ID", exact: true }).fill("checks");
  await page.getByRole("textbox", { name: "Git ref", exact: true }).fill("refs/heads/develop");
  await page.getByRole("listbox", { name: /^Конкретные компьютеры/ }).selectOption("mac");
  await page.getByRole("spinbutton", { name: "CPU", exact: true }).fill("1.5");
  await page.getByRole("button", { name: "Workflow и запуски", exact: true }).click();
  const yaml = page.getByRole("textbox", { name: "Workflow YAML", exact: true });
  await expect(yaml).toBeVisible();
  const before = peer.calls.length;
  await page.locator(".actions-repositories > .actions-section-heading").getByRole("button", { name: "Обновить", exact: true }).click();
  await expect(page.locator(".actions-panels .loading")).toHaveCount(0);
  await expect(page.getByRole("textbox", { name: "Workflow", exact: true })).toHaveValue(".github/workflows/checks.yml");
  await expect(page.getByRole("textbox", { name: "Job ID", exact: true })).toHaveValue("checks");
  await expect(page.getByRole("textbox", { name: "Git ref", exact: true })).toHaveValue("refs/heads/develop");
  await expect(page.getByRole("listbox", { name: /^Конкретные компьютеры/ })).toHaveValues(["mac"]);
  await expect(page.getByRole("spinbutton", { name: "CPU", exact: true })).toHaveValue("1.5");
  await expect(yaml).toHaveValue(new RegExp(binding.id));
  expect(peer.calls.slice(before).map((call) => call.path).sort()).toEqual(["github/installations", "github/installations/456/repositories"]);
});

// Assertions: refresh failures remain visible, are not erased by a second
// implicit discovery request, and the adjacent retry recovers that endpoint.
for (const endpoint of ["github/installations", "github/installations/456/repositories"]) {
  test(`Actions refresh retains the discovery error at ${endpoint}`, async ({ page, account }) => {
    const peer = await actionsPeer(page);
    await login(page, account); await page.goto("/actions");
    await expect(page.getByRole("button", { name: "Создать привязку", exact: true })).toBeEnabled();
    const before = peer.calls.length;
    peer.failures.set(`GET ${endpoint}`, [503]);
    await page.locator(".actions-section-heading").getByRole("button", { name: "Обновить", exact: true }).click();
    await expect(page.getByRole("alert")).toBeVisible();
    await expect(page.locator(".actions-panels .loading")).toHaveCount(0);
    expect(peer.calls.slice(before).filter((call) => call.path === endpoint)).toHaveLength(1);
    await expect(page.getByRole("alert")).toBeVisible();
    await page.getByRole("button", { name: /Попробовать снова/ }).click();
    await expect(page.getByRole("alert")).toHaveCount(0);
    await expect(page.getByRole("link", { name: repository.full_name, exact: true })).toBeVisible();
    expect(peer.calls.slice(before).filter((call) => call.path === endpoint)).toHaveLength(2);
  });
}

// Assertions: a cold runner can receive a five-minute preparation budget while
// CPU/RAM stay small; larger waits are rejected locally without sending a request.
test("Actions supports a bounded five-minute cold runner preparation", async ({ page, account }) => {
  const peer = await actionsPeer(page);
  await login(page, account); await page.goto("/actions");
  const wait = page.getByRole("spinbutton", { name: "Подготовка выделенной Power Cell · секунды", exact: true });
  await page.getByRole("spinbutton", { name: "CPU", exact: true }).fill("0.5");
  await page.getByRole("spinbutton", { name: "RAM · GiB", exact: true }).fill("0.5");
  await wait.fill("301");
  await page.getByRole("button", { name: "Создать привязку", exact: true }).click();
  expect(peer.calls.filter((call) => call.path === "github/routes" && call.method === "POST")).toHaveLength(0);
  await wait.fill("300");
  await page.getByRole("button", { name: "Создать привязку", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "Workflow YAML", exact: true })).toBeVisible();
  expect(peer.calls.find((call) => call.path === "github/routes" && call.method === "POST")?.body).toMatchObject({ preparation_seconds: 300, cpu_millis: 500, memory_bytes: 512 * 1024 ** 2 });
});
