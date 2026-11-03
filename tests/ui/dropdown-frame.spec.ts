import { expect, test } from "@playwright/test";

test("hidden nested languages stop scheduled work and resume when navigation reopens", async ({ page }) => {
  await page.goto("/react.html");
  await page.evaluate(() => {
    const frame = document.createElement("iframe");
    frame.id = "language-lifecycle";
    frame.style.cssText = "position:fixed;left:20px;bottom:20px;width:300px;height:150px;z-index:100";
    frame.srcdoc = '<!doctype html><body id="lifecycle-ready"></body>';
    document.body.append(frame);
  });
  const counts = await page.frameLocator("#language-lifecycle").locator("#lifecycle-ready").evaluate(async () => {
    const modulePath = "/src/header.ts";
    const { mountLanguageSwitcher } = await import(modulePath);
    const raf = window.requestAnimationFrame.bind(window);
    let callbacks = 0;
    window.requestAnimationFrame = callback => raf(time => { callbacks++; callback(time); });
    const frames = async () => { for (let i = 0; i < 6; i++) await new Promise(raf); };
    const outer = document.createElement("details");
    outer.open = true;
    outer.innerHTML = '<summary>Navigation</summary><details class="tmc-language-switcher--mobile" open><summary>RU</summary><a href="#en" data-language-option="en">EN</a></details>';
    document.body.append(outer);
    const inner = outer.querySelector<HTMLDetailsElement>("details")!;
    const cleanup = mountLanguageSwitcher(inner, "test-language", { desktopMinWidth: 5000, closeOnSelection: false });
    try {
      await frames();
      const opened = callbacks;
      outer.open = false;
      await frames();
      const hidden = callbacks;
      await frames();
      const stillHidden = callbacks;
      const childOpen = inner.open;
      outer.open = true;
      await frames();
      const reopened = callbacks;
      cleanup();
      await frames();
      const disposed = callbacks;
      return { opened, hidden, stillHidden, childOpen, reopened, disposed };
    } finally {
      cleanup();
      window.requestAnimationFrame = raf;
    }
  });
  expect(counts.opened).toBeGreaterThan(0);
  expect(counts.childOpen).toBe(true);
  expect(counts.stillHidden).toBe(counts.hidden);
  expect(counts.reopened).toBeGreaterThan(counts.hidden);
  expect(counts.disposed).toBe(counts.reopened);
});

test("iframe focus preserves owned actions and dismisses the menu on an outside frame", async ({ page }) => {
  await page.goto("/react.html");
  await expect(page.getByRole("button", { name: "Меню примера", exact: true })).toBeVisible();
  await page.evaluate(async () => {
    const reactPath = "/node_modules/.vite/deps/react.js", domPath = "/node_modules/.vite/deps/react-dom_client.js", uiPath = "/src/react/index.tsx";
    const React = (await import(reactPath)).default;
    const { createRoot } = (await import(domPath)).default;
    const { Dropdown, SquareButton } = await import(uiPath);
    const host = document.createElement("div");
    host.style.cssText = "position:fixed;left:20px;top:120px;z-index:100";
    document.body.append(host);
    const outside = document.createElement("iframe");
    outside.id = "outside-menu-frame";
    outside.title = "Outside action";
    outside.srcdoc = '<button onclick="window.clickCount=(window.clickCount||0)+1">Outside action</button>';
    outside.style.cssText = "position:fixed;left:20px;bottom:20px;width:220px;height:50px;z-index:100";
    document.body.append(outside);
    createRoot(host).render(React.createElement(Dropdown, {
      label: "Embedded menu",
      trigger: (props: any) => React.createElement(SquareButton, props, "UI"),
    }, React.createElement("iframe", {
      id: "owned-menu-frame", title: "Embedded action",
      srcDoc: '<button onclick="window.clickCount=(window.clickCount||0)+1">Run action</button>',
      style: { width: "100%", height: "70px", border: 0 },
    })));
  });
  const trigger = page.getByRole("button", { name: "Embedded menu", exact: true });
  const menu = page.getByRole("menu", { name: "Embedded menu", exact: true });
  const frame = page.frameLocator("#owned-menu-frame");
  const outside = page.frameLocator("#outside-menu-frame");
  for (let attempt = 1; attempt <= 3; attempt++) {
    await trigger.click();
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    await expect(menu).toBeVisible();
    await frame.getByRole("button", { name: "Run action", exact: true }).click();
    expect(await frame.locator("body").evaluate(() => (window as any).clickCount || 0)).toBe(attempt);
    await expect(menu).toBeVisible();
    await outside.getByRole("button", { name: "Outside action", exact: true }).click();
    expect(await outside.locator("body").evaluate(() => (window as any).clickCount || 0)).toBe(attempt);
    await expect(menu).toBeHidden();
  }
});

