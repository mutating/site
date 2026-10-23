import { expect, test } from "@playwright/test";

test("Space activates menu links and invokes native buttons only once", async ({ page }) => {
  await page.goto("/react.html");
  const trigger = page.getByRole("button", { name: "Меню примера", exact: true });
  await trigger.focus();
  await page.keyboard.press("ArrowDown");
  await expect(page.getByRole("menuitem", { name: "Компоненты" })).toBeFocused();
  await page.keyboard.press("Space");
  await expect(page).toHaveURL(/#components$/);
  await expect(page.getByRole("menu", { name: "Меню примера", exact: true })).toHaveCount(0);
  await trigger.focus();
  await page.keyboard.press("ArrowUp");
  await page.keyboard.press("Space");
  await expect(page.getByTestId("dropdown-count")).toHaveText("1");
});

test("standalone dropdown sizes its surface and items without a page reset", async ({ page }) => {
  await page.goto("/react.html");
  const trigger = page.getByRole("button", { name: "Меню примера", exact: true });
  await trigger.evaluate(node => {
    const root = node.closest<HTMLElement>(".tmc-dropdown")!;
    document.body.append(root);
    document.body.classList.remove("tmc-page");
    root.style.cssText = "position:fixed;top:12px;right:12px;width:max-content";
    document.getElementById("root")!.style.display = "none";
  });
  const menu = page.getByRole("menu", { name: "Меню примера", exact: true });
  await trigger.click();
  for (const width of [280, 320, 1440]) {
    await page.setViewportSize({ width, height: 800 });
    const box = (await menu.boundingBox())!;
    expect(box.width).toBe(190);
    await expect.poll(() => menu.evaluate(node => node.scrollWidth - node.clientWidth)).toBeLessThanOrEqual(0);
    for (const item of await menu.getByRole("menuitem").all()) {
      const itemBox = (await item.boundingBox())!;
      expect(itemBox.x + itemBox.width).toBeLessThanOrEqual(box.x + box.width);
    }
    await expect(menu).toBeInViewport({ ratio: 1 });
  }
});

test("standalone dropdown stays next to its trigger and inside the viewport", async ({ page }) => {
  await page.goto("/react.html");
  const trigger = page.getByRole("button", { name: "Меню примера", exact: true });
  await trigger.evaluate(node => {
    const root = node.closest<HTMLElement>(".tmc-dropdown")!;
    document.body.prepend(root);
    document.body.classList.remove("tmc-page");
    document.getElementById("root")!.style.display = "none";
  });
  const menu = page.getByRole("menu", { name: "Меню примера", exact: true });
  await trigger.click();
  for (const width of [1440, 320, 280]) {
    await page.setViewportSize({ width, height: 800 });
    const buttonBox = (await trigger.boundingBox())!;
    const rootWidth = await trigger.evaluate(node => node.parentElement!.getBoundingClientRect().width);
    expect(rootWidth).toBe(buttonBox.width);
    await expect(menu).toBeInViewport({ ratio: 1 });
    const panelBox = (await menu.boundingBox())!;
    expect(panelBox.y).toBeCloseTo(buttonBox.y + buttonBox.height + 8, 1);
    expect(panelBox.x).toBeLessThan(buttonBox.x + buttonBox.width);
    expect(panelBox.x + panelBox.width).toBeGreaterThan(buttonBox.x);
  }
  await trigger.evaluate(node => { node.parentElement!.style.cssText = "position:fixed;right:12px;top:12px"; });
  await expect(menu).toBeInViewport({ ratio: 1 });
});

test("menu keyboard commands run before an outer component stops bubbling", async ({ page }) => {
  await page.goto("/react.html");
  const trigger = page.getByRole("button", { name: "Меню примера", exact: true });
  await trigger.evaluate(node => node.closest(".demo-row")!.addEventListener("keydown", event => event.stopPropagation()));
  await trigger.focus();
  await page.keyboard.press("ArrowDown");
  await expect(page.getByRole("menuitem", { name: "Компоненты" })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("menu", { name: "Меню примера", exact: true })).toHaveCount(0);
  await expect(trigger).toBeFocused();
});

test("replacing the trigger node preserves click, ARIA state and Escape focus", async ({ page }) => {
  await page.goto("/react.html");
  const trigger = page.getByRole("button", { name: "Меню примера", exact: true });
  const menu = page.getByRole("menu", { name: "Меню примера", exact: true });
  await page.getByRole("button", { name: "Заменить кнопку меню", exact: true }).click();
  await trigger.click();
  await expect(menu).toBeVisible();
  await expect(trigger).toHaveAttribute("aria-expanded", "true");
  await page.keyboard.press("ArrowDown");
  await expect(menu.getByRole("menuitem", { name: "Компоненты" })).toBeFocused();
  // Replace the unfocused trigger without an outside pointer/focus event.
  await page.getByRole("button", { name: "Заменить кнопку меню", exact: true }).evaluate((node: HTMLButtonElement) => node.click());
  await expect(menu).toBeVisible();
  await expect(trigger).toHaveAttribute("aria-expanded", "true");
  await page.keyboard.press("Escape");
  await expect(menu).toHaveCount(0);
  await expect(trigger).toBeFocused();
  await expect(trigger).toHaveAttribute("aria-expanded", "false");
  await trigger.click();
  await expect(menu).toBeVisible();
});

test("square dropdown supports mouse, keyboard, disabled items and live children at every breakpoint", async ({ page, isMobile }) => {
  await page.goto("/react.html");
  const trigger = page.getByRole("button", { name: "Меню примера", exact: true });
  const menu = page.getByRole("menu", { name: "Меню примера", exact: true });
  for (const width of [280, 480, 481, 580, 581, 1120, 1121, 1920]) {
    await page.setViewportSize({ width, height: 900 });
    if (isMobile) await trigger.tap(); else await trigger.click();
    await expect(menu).toBeVisible();
    await expect(trigger).toHaveAttribute("aria-expanded", "true");
    await expect(menu).toHaveAttribute("id", (await trigger.getAttribute("aria-controls"))!);
    await page.setViewportSize({ width: width <= 1120 ? 1440 : 390, height: 900 });
    await expect(menu).toBeVisible();
    await trigger.press("Escape");
    await expect(trigger).toBeFocused();
    await expect(menu).toHaveCount(0);
  }
  await trigger.press("ArrowDown");
  await expect(menu.getByRole("menuitem", { name: "Компоненты" })).toBeFocused();
  await page.keyboard.press("ArrowDown");
  await expect(menu.getByRole("menuitem", { name: "Выполнить" })).toBeFocused();
  await page.keyboard.press("ArrowDown");
  await expect(menu.getByRole("menuitem", { name: "Компоненты" })).toBeFocused();
  await page.keyboard.press("End");
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("dropdown-count")).toHaveText("1");
  await expect(menu).toBeVisible();
  await page.locator("#change-dropdown-items").evaluate((node: HTMLButtonElement) => node.click());
  await expect(menu).toBeVisible();
  await expect(menu.getByRole("menuitem", { name: "Обзор", exact: true })).toBeVisible();
  await menu.getByRole("menuitem", { name: "Обзор", exact: true }).click();
  await expect(page).toHaveURL(/#overview$/);
  await expect(menu).toHaveCount(0);
});

test("dropdown cleanup under StrictMode preserves a single toggle and action handler", async ({ page }) => {
  await page.goto("/react.html");
  for (let index = 0; index < 3; index++) {
    await page.getByRole("button", { name: "Пересоздать меню", exact: true }).click();
    await expect(page.getByRole("button", { name: "Меню примера", exact: true })).toHaveCount(0);
    await page.getByRole("button", { name: "Пересоздать меню", exact: true }).click();
    await page.getByRole("button", { name: "Меню примера", exact: true }).click();
    await page.getByRole("menuitem", { name: "Выполнить" }).click();
    await expect(page.getByTestId("dropdown-count")).toHaveText(String(index + 1));
    await page.getByRole("button", { name: "Изменить пункты", exact: true }).click();
    await expect(page.getByRole("menu", { name: "Меню примера", exact: true })).toHaveCount(0);
  }
});

test("the shared controller retains native mobile-menu selection, Escape options and cleanup", async ({ page }) => {
  await page.goto("/");
  const result = await page.evaluate(async () => {
    const path = "/src/index.ts";
    const { mountMobileMenu } = await import(path);
    const details = document.createElement("details");
    details.style.display = "block";
    details.className = "tmc-mobile-menu";
    details.innerHTML = '<summary>Menu</summary><nav><a href="#probe">Link</a><details open><summary>Nested</summary></details></nav>';
    document.body.append(details);
    const destroy = mountMobileMenu(details, { closeOnEscape: false, closeOnDesktop: false });
    details.open = true;
    details.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    const keptOpen = details.open;
    details.querySelector("a")!.click();
    const selectedClosed = !details.open && !details.querySelector("details")!.open;
    destroy(); destroy();
    details.open = true;
    details.querySelector("a")!.click();
    const handlersRemoved = details.open;
    const cleanup = mountMobileMenu(details);
    details.open = true;
    details.querySelector("summary")!.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    const escaped = !details.open && document.activeElement === details.querySelector("summary");
    cleanup(); details.remove();
    return { keptOpen, selectedClosed, handlersRemoved, escaped };
  });
  expect(result).toEqual({ keptOpen: true, selectedClosed: true, handlersRemoved: true, escaped: true });
});
