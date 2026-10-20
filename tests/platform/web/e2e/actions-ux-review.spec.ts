import { test, expect, login } from "./fixtures";
import { actionsPeer, binding, repository } from "./actions-peer";

// Assertions: a saved binding resolves computer names from its own pool;
// editing another pool in the creation draft cannot change its recorded policy.
test("Actions keeps saved computer names independent of the draft pool", async ({ page, account }) => {
  const peer = await actionsPeer(page);
  peer.companies = [{ id: "team", name: "CI team", my_permissions: ["resources.manage", "resources.read", "execution.run", "execution.read"] }];
  peer.bindings = [{ ...binding, company_id: "team", computer_ids: ["team-mac"] }];
  peer.custom = async (route, url) => {
    if (url.pathname !== "/v1/computers") return false;
    await route.fulfill({ json: url.searchParams.get("company_id") === "team"
      ? [{ id: "team-mac", name: "Team Mac", runtime: "powercell-ubuntu-arm64-v1" }]
      : [{ id: "personal-mac", name: "Personal Mac", runtime: "powercell-ubuntu-arm64-v1" }] });
    return true;
  };
  await login(page, account); await page.goto("/actions");
  await page.getByRole("button", { name: "Workflow и запуски", exact: true }).click();
  const policy = page.locator(".actions-ci-policy");
  await expect(policy).toContainText("Team Mac");
  const disclosure = page.locator(".actions-ci-create summary");
  if (await disclosure.count()) await disclosure.click();
  const pool = page.getByRole("combobox", { name: "Пул компьютеров", exact: true });
  for (const value of ["team", ""]) {
    await pool.selectOption(value);
    await expect(policy).toContainText("Team Mac");
    await expect(policy).not.toContainText("team-mac");
  }
  expect(peer.calls.filter((call) => call.method === "POST" || call.method === "DELETE")).toHaveLength(0);
});

// Assertions: repository read access never silently hides creation; an explicit
// administrator requirement is visible even when the user has existing bindings.
test("Actions explains missing repository administration rights", async ({ page, account }) => {
  const peer = await actionsPeer(page);
  peer.repositories = [{ ...repository, can_administer: false }];
  peer.bindings = [{ ...binding }];
  await login(page, account); await page.goto("/actions");
  await expect(page.getByRole("button", { name: "Workflow и запуски", exact: true })).toBeVisible();
  await expect(page.getByText("Для создания привязки нужны права администратора репозитория.", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Создать привязку", exact: true })).toHaveCount(0);
});

// Assertions: returning users reach active bindings before disabled ones without
// traversing a long creation form; opening/closing the form retains their draft.
test("Actions prioritizes active bindings and keeps creation optional", async ({ page, account }) => {
  const peer = await actionsPeer(page);
  peer.bindings = [{ ...binding, id: "old", enabled: false, job_key: "old_job" }, { ...binding, job_key: "active_job" }];
  await login(page, account); await page.goto("/actions");
  const cards = page.locator(".actions-repository").filter({ has: page.getByRole("button", { name: "Workflow и запуски", exact: true }) });
  await expect(cards.first()).toContainText("active_job");
  await expect(page.getByRole("textbox", { name: "Job ID", exact: true })).toBeHidden();
  const disclosure = page.locator(".actions-ci-create summary");
  await disclosure.click();
  const job = page.getByRole("textbox", { name: "Job ID", exact: true });
  await job.fill("my_checks");
  await disclosure.click(); await expect(job).toBeHidden();
  await disclosure.click(); await expect(job).toHaveValue("my_checks");
});

// Assertions: status/history and refresh precede lengthy setup YAML, so a
// returning user can inspect a run without scrolling through code or instructions.
test("Actions puts recorded runs before workflow setup", async ({ page, account }) => {
  const peer = await actionsPeer(page); peer.bindings = [{ ...binding }];
  peer.runs = [{ id: "latest", run_id: 42, run_attempt: 1, status: "completed", reason: "github_success", url: "https://github.com/mutating/ci-sandbox/actions/runs/42" }];
  await login(page, account); await page.goto("/actions");
  await page.getByRole("button", { name: "Workflow и запуски", exact: true }).click();
  const detail = page.locator(".actions-ci-detail");
  const run = detail.getByRole("link", { name: "#42 / 1", exact: true });
  const yaml = detail.getByRole("textbox", { name: "Workflow YAML", exact: true });
  await expect(run).toBeVisible(); await expect(yaml).toBeVisible();
  expect((await run.boundingBox())!.y).toBeLessThan((await yaml.boundingBox())!.y);
  expect((await detail.getByRole("button", { name: "Обновить", exact: true }).boundingBox())!.y).toBeLessThan((await run.boundingBox())!.y);
});

// Assertions: an empty machine chooser does not look actionable, while creating
// a future binding remains possible; changing to a populated pool enables it.
test("Actions disables an empty specific-computer chooser", async ({ page, account }) => {
  const peer = await actionsPeer(page);
  peer.companies = [{ id: "team", name: "CI team", my_permissions: ["resources.manage", "execution.run", "execution.read"] }];
  peer.custom = async (route, url) => {
    if (url.pathname !== "/v1/computers") return false;
    await route.fulfill({ json: url.searchParams.has("company_id") ? [{ id: "mac", name: "Team Mac", runtime: "powercell-ubuntu-arm64-v1" }] : [] });
    return true;
  };
  await login(page, account); await page.goto("/actions");
  await expect(page.getByText(/В пуле пока нет Power Cells/)).toBeVisible();
  const chooser = page.getByRole("listbox", { name: /^Конкретные компьютеры/ });
  await expect(chooser).toBeDisabled();
  await expect(page.getByRole("button", { name: "Создать привязку", exact: true })).toBeEnabled();
  await page.getByRole("combobox", { name: "Пул компьютеров", exact: true }).selectOption("team");
  await expect(chooser).toBeEnabled();
  await expect(chooser.getByRole("option", { name: "Team Mac", exact: true })).toBeVisible();
});

// Assertions: the sole execution policy is explained before creation.
test("Actions explains immediate capacity failure", async ({ page, account }) => {
  await actionsPeer(page);
  await login(page, account); await page.goto("/actions");
  await expect(page.getByText(/Нет свободной Power Cell — запуск сразу завершится ошибкой/)).toBeVisible();
  await expect(page.getByRole("combobox", { name: "Fallback", exact: true })).toHaveCount(0);
});

// Assertions: a successful POST is immediately reflected in the binding list;
// the follow-up list request cannot hide it or shift the focused result panel.
test("Actions keeps a created binding visible during list refresh", async ({ page, account }) => {
  const peer = await actionsPeer(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await login(page, account); await page.goto("/actions");
  await expect(page.getByRole("button", { name: "Создать привязку", exact: true })).toBeEnabled();
  let release!: () => void; const gate = new Promise<void>((resolve) => { release = resolve; });
  peer.custom = async (route, url) => {
    if (url.pathname !== "/v1/github/routes" || route.request().method() !== "GET") return false;
    await gate; await route.fulfill({ json: peer.bindings }); return true;
  };
  await page.getByRole("button", { name: "Создать привязку", exact: true }).click();
  const heading = page.locator(".actions-ci-detail h3");
  await expect(heading).toBeFocused();
  const card = page.getByRole("button", { name: "Workflow и запуски", exact: true });
  await expect(card).toBeVisible();
  const before = (await heading.boundingBox())!.y;
  release();
  await expect(page.locator(".actions-ci-detail")).toHaveAttribute("aria-busy", "false");
  await expect(heading).toBeInViewport({ ratio: 1 });
  expect(Math.abs((await heading.boundingBox())!.y - before)).toBeLessThan(2);
});

// Assertions: disabling a binding retains its YAML as a saved record without
// inviting the user to connect that inactive workflow; active setup stays clear.
test("Actions identifies disabled workflow YAML as a saved record", async ({ page, account }) => {
  const peer = await actionsPeer(page); peer.bindings = [{ ...binding }];
  await login(page, account); await page.goto("/actions");
  await page.getByRole("button", { name: "Workflow и запуски", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Подключение workflow", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Отключить привязку", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Сохранённый workflow", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Подключение workflow", exact: true })).toHaveCount(0);
  await expect(page.getByRole("textbox", { name: "Workflow YAML", exact: true })).toHaveValue(new RegExp(binding.id));
});
