import { test, expect, startSession } from "./fixtures";
import { actionsPeer, binding, repositoryURL } from "./actions-peer";

for (const language of ["ru", "en"]) {
  for (const existing of [false, true]) {
    /** The access explanation used to mention only creation even when GitHub permissions blocked editing an existing connection. */
    test(`${language} administrator explanation covers ${existing ? "editing" : "creating"} a connection`, async ({ page, account }) => {
      await page.addInitScript(language => localStorage.setItem("tmc-language", language), language);
      const peer = await actionsPeer(page);
      peer.repositories[0].can_administer = false;
      if (existing) peer.bindings = [{ ...binding }];
      await startSession(page, account);
      await page.goto(repositoryURL);
      await expect(page.getByText(language === "ru"
        ? "Для настройки подключения нужны права администратора репозитория."
        : "Repository administrator access is required to configure this connection.", { exact: true })).toBeVisible();
      await expect(page.getByLabel("CPU", { exact: true })).toBeDisabled();
    });
  }
}

for (const width of [320, 768]) {
  /** Empty search offered a tiny, unadorned text reset; returning to repositories must be a recognizable touch-sized action. */
  test(`empty repository search has an accessible reset at ${width}px`, async ({ page, account }) => {
    await page.setViewportSize({ width, height: 844 });
    await actionsPeer(page);
    await startSession(page, account);
    await page.goto("/actions?search=missing&state=disabled&sort=name_desc");
    const reset = page.getByRole("button", { name: "Сбросить фильтры", exact: true });
    await expect(reset).toBeVisible();
    expect((await reset.boundingBox())!.height).toBeGreaterThanOrEqual(44);
    const visibleAffordance = await reset.evaluate(element => {
      const style = getComputedStyle(element);
      return style.textDecorationLine.includes("underline") ||
        (parseFloat(style.borderTopWidth) > 0 && style.borderTopStyle !== "none");
    });
    expect(visibleAffordance).toBe(true);
    await reset.focus(); await reset.press("Enter");
    await expect(page.locator(".repository-card")).toHaveCount(1);
    expect(new URL(page.url()).search).toBe("");
  });
}

/** Loading or failing to fetch inventory must not falsely claim saved computers were removed, in defaults or workflow overrides. */
test("unknown inventory preserves selections without diagnosing missing computers", async ({ page, account }) => {
  const peer = await actionsPeer(page);
  const workflow = ".github/workflows/deploy.yml";
  peer.bindings = [{ ...binding, computer_ids: ["cell-a"], workflow_profiles: { [workflow]: { computer_ids: ["cell-a"] } } }];
  peer.computers = [{ id: "cell-a", name: "Alpha", runtime: "powercell-ubuntu-arm64-v1" }];
  let release!: () => void;
  const gate = new Promise<void>(resolve => release = resolve);
  peer.custom = async (route, url) => {
    if (!url.pathname.endsWith("/computers")) return false;
    await gate; await route.fulfill({ status: 503, json: {} }); return true;
  };
  await startSession(page, account);
  try {
    await page.goto(repositoryURL);
    const profile = page.getByRole("group", { name: workflow, exact: true });
    await expect(profile).toBeVisible();
    await page.locator(".binding-advanced > summary").click();
    const selectors = page.locator(".repository-binding-form select[multiple]");
    await expect(selectors).toHaveCount(2);
    await expect(selectors.nth(0)).toBeDisabled();
    await expect(selectors.nth(1)).toBeDisabled();
    await expect(page.getByText(/Некоторые выбранные компьютеры больше не доступны/)).toHaveCount(0);
    await expect(selectors.nth(0).locator("option:checked")).toHaveText("cell-a · Выбор сохранён");
    await expect(selectors.nth(1).locator("option:checked")).toHaveText("cell-a · Выбор сохранён");
    release();
    await expect(page.getByRole("alert")).toBeVisible();
    await expect(page.getByText(/Некоторые выбранные компьютеры больше не доступны/)).toHaveCount(0);
    await expect(selectors.nth(0).locator("option:checked")).toHaveText("cell-a · Выбор сохранён");
    await expect(selectors.nth(1).locator("option:checked")).toHaveText("cell-a · Выбор сохранён");
    const save = page.getByRole("button", { name: "Сохранить настройки", exact: true });
    await expect(save).toBeDisabled();
    await page.getByLabel("CPU", { exact: true }).fill("0.75");
    peer.custom = null;
    await page.getByRole("button", { name: /Попробовать снова/ }).click();
    await expect(selectors.nth(0).locator("option:checked")).toHaveText("Alpha");
    await expect(selectors.nth(1).locator("option:checked")).toHaveText("Alpha");
    await expect(save).toBeEnabled(); await save.click();
    await expect(page.getByText("Настройки сохранены.", { exact: true })).toBeVisible();
    expect(peer.bindings[0]).toMatchObject({ cpu_millis: 750, computer_ids: ["cell-a"], workflow_profiles: { [workflow]: { computer_ids: ["cell-a"] } } });
  } finally { release(); }
});
