import { test, expect } from "@playwright/test";

for (const [adapter, path] of [["React", "/react.html?square-button-header"], ["Hugo", "/.runtime/hugo/index.html"]]) {
  test(`${adapter} square controls accept custom content and native interactions outside the header`, async ({ page }) => {
    await page.goto(path);
    const link = page.getByRole("link", { name: "Квадратная ссылка", exact: true });
    const action = page.getByRole("button", { name: "Квадратное действие", exact: true });
    const disabled = page.getByRole("button", { name: "Квадратная недоступная", exact: true });
    await expect(link.locator("svg")).toHaveCount(0);
    await expect(link).toHaveText("UI");
    await expect(action.locator("svg")).toHaveCount(1);
    expect(await link.evaluate(node => node.closest("header"))).toBeNull();
    await expect(action).toHaveAttribute("type", "button");
    if (adapter === "Hugo") {
      const text = page.getByRole("button", { name: "Экранированный текст" });
      await expect(text).toHaveText("<em>Текст</em>");
      await expect(text.locator("em")).toHaveCount(0);
      const empty = page.getByRole("link", { name: "Пустой адрес" });
      await expect(empty).toHaveAttribute("href", "");
      await expect(empty).toHaveAttribute("rel", "noopener noreferrer");
      const header = page.getByRole("link", { name: "Свободное содержимое" });
      await expect(header).toHaveText("UI");
      await expect(header.locator("svg")).toHaveCount(0);
    }
    await expect(disabled).toBeDisabled();
    await disabled.evaluate((node: HTMLButtonElement) => node.click());
    await expect(page.getByTestId("square-count")).toHaveText("0");
    await action.focus();
    await page.keyboard.press("Enter");
    await expect(page.getByTestId("square-count")).toHaveText("1");
    await page.keyboard.press("Space");
    await expect(page.getByTestId("square-count")).toHaveText("2");
    await link.focus();
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/#components$/);
  });
}

test("React square button content can change between SVG, text and an image inside the header", async ({ page }) => {
  await page.goto("/react.html?square-button-header");
  const link = page.getByRole("link", { name: "Свободное содержимое", exact: true });
  await expect(link.locator("svg")).toHaveCount(1);
  const original = await link.boundingBox();
  await page.getByRole("button", { name: "Сменить содержимое" }).click();
  await expect(link).toHaveText("UI");
  await expect(link.locator("svg")).toHaveCount(0);
  expect(await link.boundingBox()).toEqual(original);
  await page.getByRole("button", { name: "Сменить содержимое" }).click();
  await expect(link.locator("img")).toBeVisible();
  expect(await link.boundingBox()).toEqual(original);
  await page.getByRole("button", { name: "Сменить содержимое" }).click();
  await expect(link.locator("svg")).toHaveCount(1);
  expect(await link.boundingBox()).toEqual(original);
});

test("standalone square styles preserve dimensions without any header stylesheet", async ({ page }) => {
  await page.goto("/");
  await page.setContent(`<!doctype html><html data-theme="dark"><head>
    <link rel="stylesheet" href="/dist/styles/tokens.css">
    <link rel="stylesheet" href="/dist/styles/square-button.css">
    </head><body><a class="tmc-square-button" href="#target" aria-label="Go">UI</a>
    <button class="tmc-square-button" type="button" aria-label="Act">UI</button></body></html>`);
  for (const width of [280, 480, 481, 580, 581, 1120, 1121, 2560]) {
    await page.setViewportSize({ width, height: 800 });
    for (const control of [page.getByRole("link", { name: "Go" }), page.getByRole("button", { name: "Act" })]) {
      const box = (await control.boundingBox())!;
      expect(box.width).toBe(width <= 480 ? 56 : width <= 580 ? 68 : 74);
      expect(box.height).toBe(width <= 580 ? 68 : 74);
      await control.focus();
      await page.keyboard.press("Tab");
      await control.focus();
      expect(await control.evaluate(node => getComputedStyle(node).outlineWidth)).toBe("3px");
    }
  }
});
