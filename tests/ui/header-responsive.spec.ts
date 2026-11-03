import { expect, test } from "@playwright/test";

for (const [adapter, path] of [["React", "/react.html?square-button-header"], ["Hugo", "/.runtime/hugo/"]]) {
  test(`${adapter} gives adjacent content equal visible gutters in the merged header`, async ({ page }) => {
    await page.goto(path);
    await page.evaluate(() => document.fonts.ready);
    const header = page.getByRole("banner");
    for (const width of [1120, 900, 581, 580, 481, 480, 360, 280]) {
      await page.setViewportSize({ width, height: 800 });
      await expect.poll(() => header.evaluate(header => {
        const content = header.querySelector(".tmc-platform-link > :first-child")!.getBoundingClientRect();
        const controls = header.querySelector(".tmc-header-actions")!.getBoundingClientRect();
        const bounds = header.getBoundingClientRect();
        const edge = bounds.right - parseFloat(getComputedStyle(header).borderRightWidth);
        return Math.abs(content.left - controls.right - (edge - content.right));
      })).toBeLessThanOrEqual(1);
    }
  });

  test(`${adapter} keeps an open menu inside the viewport when its tall header scrolls away`, async ({ page }) => {
    await page.setViewportSize({ width: 280, height: 301 });
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.goto(path);
    await page.evaluate(() => document.fonts.ready);
    const header = page.getByRole("banner");
    await header.evaluate(header => {
      const sizes = Array.from(header.querySelectorAll<HTMLElement>("*")).map(node => [node, parseFloat(getComputedStyle(node).fontSize) * 2] as const);
      for (const [node, size] of sizes) node.style.fontSize = `${size}px`;
    });
    await expect(header).toHaveCSS("position", "relative");
    const menu = header.locator(".tmc-mobile-menu");
    await menu.locator(":scope > summary").click();
    await page.evaluate(() => window.scrollTo({ top: 220, behavior: "instant" }));
    const panel = menu.locator(":scope > nav");
    await expect(menu).toHaveAttribute("open", "");
    await expect(panel).toBeInViewport({ ratio: 1 });
    const last = panel.locator(".tmc-mobile-menu__links a").last();
    await last.focus();
    await expect(last).toBeInViewport({ ratio: 1 });
    await expect(panel).toBeInViewport({ ratio: 1 });
    await page.keyboard.press("Escape");
    await expect(menu).not.toHaveAttribute("open");
  });

  test(`${adapter} keeps adjacent controls aligned when the brand grows and the header wraps`, async ({ page }) => {
    await page.setViewportSize({ width: 1120, height: 800 });
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.goto(path);
    await page.evaluate(() => document.fonts.ready);
    const header = page.getByRole("banner");
    const link = header.locator(":scope > .tmc-platform-link");
    const alignment = () => header.evaluate(header => {
      const row = header.querySelector(".tmc-header-actions")!.getBoundingClientRect();
      const adjacent = header.querySelector(":scope > .tmc-platform-link")!.getBoundingClientRect();
      return Math.abs(adjacent.top + adjacent.height / 2 - row.top - row.height / 2);
    });
    for (const height of [96, 144, 48, 120]) {
      await header.locator(".tmc-brand").evaluate((brand: HTMLElement, height) => { brand.style.minHeight = `${height}px`; }, height);
      await expect.poll(alignment).toBeLessThanOrEqual(1);
      await expect(link).toBeInViewport({ ratio: 1 });
    }
    await header.locator(".tmc-brand").evaluate((brand: HTMLElement) => { brand.style.removeProperty("min-height"); });
    await header.evaluate(header => {
      const sizes = Array.from(header.querySelectorAll<HTMLElement>("*")).map(node => [node, parseFloat(getComputedStyle(node).fontSize) * 2] as const);
      for (const [node, size] of sizes) node.style.fontSize = `${size}px`;
    });
    for (const width of [900, 581, 580, 481, 480]) {
      await page.setViewportSize({ width, height: 800 });
      await expect.poll(alignment).toBeLessThanOrEqual(1);
    }
    await page.setViewportSize({ width: 280, height: 800 });
    await expect.poll(() => header.evaluate(header => {
      const main = header.querySelector(".tmc-header")!.getBoundingClientRect();
      return header.querySelector(":scope > .tmc-platform-link")!.getBoundingClientRect().top - main.bottom;
    })).toBeGreaterThanOrEqual(0);
    const positions = await link.evaluate(async node => {
      const values: number[] = [];
      for (let i = 0; i < 10; i++) { await new Promise(requestAnimationFrame); values.push(node.getBoundingClientRect().top); }
      return values;
    });
    expect(Math.max(...positions) - Math.min(...positions)).toBeLessThanOrEqual(1);
    await page.setViewportSize({ width: 900, height: 800 });
    await expect.poll(alignment).toBeLessThanOrEqual(1);
    await link.click();
    await expect(page).toHaveURL(/#components$/);
  });
}

for (const [adapter, path] of [["React", "/react.html?language-only"], ["Hugo", "/.runtime/hugo/language-only/"]]) {
  test(`${adapter} language without navigation never creates a menu`, async ({ page, isMobile }) => {
    await page.goto(path);
    const language = page.locator(".tmc-language-switcher--standalone");
    const summary = language.locator("summary");
    for (const width of [280, 320, 480, 481, 580, 581, 900, 1120, 1121, 1440]) {
      await page.setViewportSize({ width, height: 800 });
      await page.mouse.move(0, 799);
      await expect(page.locator(".tmc-nav, .tmc-mobile-menu")).toHaveCount(0);
      await expect(page.locator("header .tmc-language-switcher")).toHaveCount(1);
      await expect(summary).toBeInViewport({ ratio: 1 });
      await expect(summary).toHaveText("RU");
      if (isMobile) await summary.tap(); else await summary.click();
      const option = language.getByRole("link", { name: "EN", exact: true });
      await expect(option).toBeInViewport({ ratio: 1 });
      await option.focus();
      await page.keyboard.press("Escape");
      await expect(language).not.toHaveAttribute("open");
      await expect(summary).toBeFocused();
      await page.keyboard.press("Enter");
      await option.focus();
      await page.keyboard.press("Enter");
      await expect(page).toHaveURL(/#en$/);
      await expect(language).not.toHaveAttribute("open");
      await expect(summary).toBeVisible();
    }
  });
}

test("React adds and removes the navigation menu when its items change", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 800 });
  await page.goto("/react.html?language-only");
  const toggle = page.getByRole("button", { name: "Скрыть или показать навигацию" });
  for (let iteration = 0; iteration < 2; iteration++) {
    await expect(page.locator(".tmc-mobile-menu")).toHaveCount(0);
    await expect(page.locator(".tmc-language-switcher--standalone > summary")).toBeVisible();
    await toggle.click();
    await page.locator(".tmc-mobile-menu > summary").click();
    const language = page.locator(".tmc-language-switcher--mobile");
    await language.locator("summary").click();
    await expect(language.locator("a")).toBeVisible();
    // Re-render with both nested disclosures open, without an outside click closing them first.
    await toggle.evaluate((node: HTMLButtonElement) => node.click());
  }
  await page.locator(".tmc-language-switcher--standalone > summary").click();
  await expect(page.locator(".tmc-language-switcher--standalone a")).toBeVisible();
});

test("wordmark fitting follows navigation added and removed after mounting", async ({ page }) => {
  await page.setViewportSize({ width: 1121, height: 800 });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/react.html");
  await page.getByRole("button", { name: "Пересоздать хэдер", exact: true }).click();
  await expect(page.locator("header")).toHaveCount(0);
  await page.evaluate(async () => {
    const reactPath = "/node_modules/.vite/deps/react.js", domPath = "/node_modules/.vite/deps/react-dom_client.js", uiPath = "/src/react/index.tsx";
    const React = (await import(reactPath)).default;
    const { createRoot } = (await import(domPath)).default;
    const { Header, ThemeProvider } = await import(uiPath);
    document.getElementById("root")!.style.display = "none";
    const host = document.createElement("div");
    host.id = "header-fit-audit";
    document.body.prepend(host);
    const root = createRoot(host);
    const items = ["Overview settings", "Organizations", "Computers", "Source repositories"].map((label, index) => ({ id: String(index), href: `#${index}`, label }));
    const render = (filled: boolean) => root.render(React.createElement(ThemeProvider, null, React.createElement(Header, {
      brand: { href: "#home", animation: { fitWordmarkToHeader: true } },
      items: filled ? items : [],
      language: { current: "en", options: [{ code: "ru", href: "#ru" }], label: "Language", groupLabel: "Languages" },
    })));
    (window as any).renderAuditNavigation = render;
    render(false);
  });
  const header = page.locator("#header-fit-audit header");
  const stage = header.locator(".tmc-brand__wordmark-stage");
  await expect(stage).toHaveAttribute("data-can-expand", "true");
  for (let cycle = 0; cycle < 2; cycle++) {
    await page.evaluate(() => (window as any).renderAuditNavigation(true));
    await expect(header.locator(".tmc-nav a")).toHaveCount(4);
    await expect(stage).toHaveAttribute("data-can-expand", "false");
    await expect(header.locator(".tmc-header")).toHaveCSS("height", "74px");
    await page.evaluate(() => (window as any).renderAuditNavigation(false));
    await expect(stage).toHaveAttribute("data-can-expand", "true");
  }
});

for (const [adapter, path] of [["React", "/react.html?square-button-header"], ["Hugo", "/.runtime/hugo/"]]) {
  for (const theme of ["light", "dark"]) {
    test(`${adapter} joins the adjacent link to the header only up to 1120px: ${theme}`, async ({ page, isMobile }) => {
      test.setTimeout(90_000);
      await page.addInitScript(theme => localStorage.setItem("tmc-theme", theme), theme);
      const shell = page.getByRole("banner");
      const header = shell.locator(".tmc-header");
      const link = shell.getByRole("link", { name: "Свободное содержимое", exact: true });
      for (const width of [1440, 1121, 1120, 900, 581, 580, 481, 480, 381, 380, 320, 280, 1121]) {
        await page.setViewportSize({ width, height: 800 });
        // Start each touch viewport without the browser's focus/zoom from the previous size.
        // Continuous resize is covered on the real site and platform.
        await page.goto(path);
        const mobile = width <= 1120;
        await expect(shell).toHaveCSS("gap", mobile ? "0px" : "10px");
        await expect(shell).toHaveCSS("border-top-width", mobile ? "1px" : "0px");
        for (const part of [header, link]) {
          await expect(part).toHaveCSS("border-top-width", mobile ? "0px" : "1px");
          if (mobile) {
            await expect(part).toHaveCSS("background-color", "rgba(0, 0, 0, 0)");
            await expect(part).toHaveCSS("backdrop-filter", "none");
            await expect(part).toHaveCSS("box-shadow", "none");
          }
        }
        await expect(link).toBeInViewport({ ratio: 1 });
        const box = (await link.boundingBox())!;
        expect(box.width).toBeGreaterThanOrEqual(44);
        const band = (await shell.boundingBox())!;
        expect(box.x + box.width).toBeLessThanOrEqual(band.x + band.width);
        expect(box.y).toBeGreaterThanOrEqual(band.y);
        expect(box.y + box.height).toBeLessThanOrEqual(band.y + band.height);
        expect(await shell.evaluate(node => node.scrollWidth - node.clientWidth)).toBeLessThanOrEqual(0);
        if (mobile) {
          // Real links still populate the mobile menu, including its language switcher.
          await test.step(`${width}px navigation`, async () => {
            const summary = shell.locator(".tmc-mobile-menu > summary");
            if (isMobile) await summary.tap(); else await summary.click();
          });
          await expect(shell.locator(".tmc-mobile-menu__links a")).toHaveCount(2);
          await expect(shell.locator(".tmc-language-switcher--mobile > summary")).toBeVisible();
          await page.keyboard.press("Escape");
        }
      }
      await page.setViewportSize({ width: 390, height: 800 });
      await page.goto(path);
      if (isMobile) await link.tap(); else await link.click();
      await expect(page).toHaveURL(/#components$/);
      await page.keyboard.press("Tab");
      await link.focus();
      await expect(link).toHaveCSS("outline-width", "3px");
      await page.keyboard.press("Enter");
      await expect(page).toHaveURL(/#components$/);
    });
  }
}
